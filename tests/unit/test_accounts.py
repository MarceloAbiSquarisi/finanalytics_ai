"""
Testes unitários do módulo de contas.

Estratégia: fake in-memory do AccountRepository.
- Sem banco, sem rede, sem DLL.
- Cobertura de: criação, duplicata, ativação, deleção de ativa, listagem por tipo.
- Os testes de integração (com banco real) ficam em tests/integration/.
"""
from __future__ import annotations

import pytest
from typing import Dict, Optional, Sequence
from datetime import datetime, timezone

from finanalytics_ai.domain.accounts import (
    AccountDomainError,
    AccountNotFoundError,
    AccountStatus,
    AccountType,
    DuplicateAccountError,
    NoActiveAccountError,
    TradingAccount,
)
from finanalytics_ai.application.use_cases import (
    CreateAccount,
    CreateAccountCmd,
    DeleteAccount,
    GetActiveAccount,
    ListAccounts,
    SetActiveAccount,
    UpdateAccount,
    UpdateAccountCmd,
)


# ---------------------------------------------------------------------------
# Fake repository (in-memory)
# ---------------------------------------------------------------------------

class FakeAccountRepository:
    def __init__(self) -> None:
        self._store: Dict[str, TradingAccount] = {}

    async def save(self, account: TradingAccount) -> None:
        key = (account.broker_id, account.account_id, account.account_type)
        for a in self._store.values():
            if (a.broker_id, a.account_id, a.account_type) == key:
                raise DuplicateAccountError(*key)
        self._store[account.uuid] = account

    async def get_by_uuid(self, account_uuid: str) -> TradingAccount:
        if account_uuid not in self._store:
            raise AccountNotFoundError(account_uuid)
        return self._store[account_uuid]

    async def get_by_broker_account(
        self, broker_id: str, account_id: str, account_type: AccountType
    ) -> Optional[TradingAccount]:
        for a in self._store.values():
            if (
                a.broker_id == broker_id
                and a.account_id == account_id
                and a.account_type == account_type
            ):
                return a
        return None

    async def list_all(self) -> Sequence[TradingAccount]:
        return list(self._store.values())

    async def list_by_type(self, account_type: AccountType) -> Sequence[TradingAccount]:
        return [a for a in self._store.values() if a.account_type == account_type]

    async def get_active(self) -> Optional[TradingAccount]:
        for a in self._store.values():
            if a.is_active:
                return a
        return None

    async def set_active(self, account_uuid: str) -> TradingAccount:
        if account_uuid not in self._store:
            raise AccountNotFoundError(account_uuid)
        for a in self._store.values():
            a.deactivate()
        self._store[account_uuid].activate()
        return self._store[account_uuid]

    async def update(self, account: TradingAccount) -> None:
        if account.uuid not in self._store:
            raise AccountNotFoundError(account.uuid)
        self._store[account.uuid] = account

    async def delete(self, account_uuid: str) -> None:
        if account_uuid not in self._store:
            raise AccountNotFoundError(account_uuid)
        del self._store[account_uuid]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo() -> FakeAccountRepository:
    return FakeAccountRepository()


def _real_cmd(**kwargs) -> CreateAccountCmd:
    defaults = dict(
        broker_id="227",
        account_id="12345",
        account_type=AccountType.REAL,
        label="XP Real",
        routing_password="secret",
    )
    defaults.update(kwargs)
    return CreateAccountCmd(**defaults)


def _sim_cmd(**kwargs) -> CreateAccountCmd:
    defaults = dict(
        broker_id="227",
        account_id="12345",
        account_type=AccountType.SIMULATOR,
        label="XP Simulador",
    )
    defaults.update(kwargs)
    return CreateAccountCmd(**defaults)


# ---------------------------------------------------------------------------
# Testes: CreateAccount
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_real_account(repo):
    uc = CreateAccount(repo)
    account = await uc.execute(_real_cmd())

    assert account.uuid
    assert account.broker_id == "227"
    assert account.account_type == AccountType.REAL
    assert account.status == AccountStatus.INACTIVE
    assert account.routing_password == "secret"


@pytest.mark.asyncio
async def test_create_simulator_and_real_same_account_id_allowed(repo):
    """Real e simulador com mesmo account_id são contas distintas."""
    uc = CreateAccount(repo)
    real = await uc.execute(_real_cmd())
    sim = await uc.execute(_sim_cmd())

    assert real.uuid != sim.uuid
    assert len(repo._store) == 2


@pytest.mark.asyncio
async def test_create_duplicate_raises(repo):
    uc = CreateAccount(repo)
    await uc.execute(_real_cmd())

    with pytest.raises(DuplicateAccountError) as exc_info:
        await uc.execute(_real_cmd())

    assert exc_info.value.account_id == "12345"


# ---------------------------------------------------------------------------
# Testes: ListAccounts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_all_empty(repo):
    uc = ListAccounts(repo)
    result = await uc.execute()
    assert result == []


@pytest.mark.asyncio
async def test_list_by_type_filters_correctly(repo):
    create = CreateAccount(repo)
    await create.execute(_real_cmd())
    await create.execute(_sim_cmd())

    list_uc = ListAccounts(repo)
    reals = await list_uc.execute(AccountType.REAL)
    sims = await list_uc.execute(AccountType.SIMULATOR)

    assert len(reals) == 1
    assert all(a.account_type == AccountType.REAL for a in reals)
    assert len(sims) == 1


# ---------------------------------------------------------------------------
# Testes: SetActiveAccount / GetActiveAccount
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_active_switches_correctly(repo):
    create = CreateAccount(repo)
    acc1 = await create.execute(_real_cmd(account_id="111", label="Conta 1"))
    acc2 = await create.execute(_real_cmd(account_id="222", label="Conta 2"))

    set_active = SetActiveAccount(repo)
    await set_active.execute(acc1.uuid)
    assert repo._store[acc1.uuid].is_active
    assert not repo._store[acc2.uuid].is_active

    # Troca para acc2 — acc1 deve ser desativada
    await set_active.execute(acc2.uuid)
    assert not repo._store[acc1.uuid].is_active
    assert repo._store[acc2.uuid].is_active


@pytest.mark.asyncio
async def test_get_active_raises_when_none(repo):
    uc = GetActiveAccount(repo)
    with pytest.raises(NoActiveAccountError):
        await uc.execute()


@pytest.mark.asyncio
async def test_get_active_returns_active_account(repo):
    create = CreateAccount(repo)
    acc = await create.execute(_real_cmd())
    await SetActiveAccount(repo).execute(acc.uuid)

    active = await GetActiveAccount(repo).execute()
    assert active.uuid == acc.uuid


@pytest.mark.asyncio
async def test_set_active_invalid_uuid_raises(repo):
    with pytest.raises(AccountNotFoundError):
        await SetActiveAccount(repo).execute("non-existent-uuid")


# ---------------------------------------------------------------------------
# Testes: UpdateAccount
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_label(repo):
    acc = await CreateAccount(repo).execute(_real_cmd())
    updated = await UpdateAccount(repo).execute(
        UpdateAccountCmd(account_uuid=acc.uuid, label="Novo Label")
    )
    assert updated.label == "Novo Label"


@pytest.mark.asyncio
async def test_update_routing_password(repo):
    acc = await CreateAccount(repo).execute(_real_cmd(routing_password=None))
    assert acc.routing_password is None

    updated = await UpdateAccount(repo).execute(
        UpdateAccountCmd(account_uuid=acc.uuid, routing_password="nova_senha")
    )
    assert updated.routing_password == "nova_senha"


# ---------------------------------------------------------------------------
# Testes: DeleteAccount
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_inactive_account(repo):
    acc = await CreateAccount(repo).execute(_real_cmd())
    await DeleteAccount(repo).execute(acc.uuid)
    assert acc.uuid not in repo._store


@pytest.mark.asyncio
async def test_delete_active_account_raises(repo):
    acc = await CreateAccount(repo).execute(_real_cmd())
    await SetActiveAccount(repo).execute(acc.uuid)

    with pytest.raises(AccountDomainError, match="ativa"):
        await DeleteAccount(repo).execute(acc.uuid)


@pytest.mark.asyncio
async def test_delete_nonexistent_raises(repo):
    with pytest.raises(AccountNotFoundError):
        await DeleteAccount(repo).execute("ghost-uuid")


# ---------------------------------------------------------------------------
# Testes: propriedades de domínio
# ---------------------------------------------------------------------------

def test_account_is_real_property():
    acc = TradingAccount.create(
        broker_id="227",
        account_id="99",
        account_type=AccountType.REAL,
        label="test",
    )
    assert acc.is_real
    assert not acc.is_active


def test_account_dll_identifier():
    acc = TradingAccount.create(
        broker_id="386",
        account_id="77",
        account_type=AccountType.REAL,
        label="Clear",
        sub_account_id="01",
    )
    ident = acc.as_dll_identifier()
    assert ident["broker_id"] == "386"
    assert ident["sub_account_id"] == "01"
