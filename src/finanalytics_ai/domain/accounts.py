"""
Domínio de contas de negociação.

Regras centrais:
- Uma conta pertence a um broker e tem um tipo (REAL | SIMULATOR).
- Apenas uma conta pode estar ativa por vez.
- Conta de simulador nunca envia ordens reais à DLL.
- Idempotência no cadastro: mesma (broker_id, account_id, type) não duplica.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
import uuid

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AccountType(str, Enum):
    REAL = "real"
    SIMULATOR = "simulator"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


# ---------------------------------------------------------------------------
# Exceções de domínio
# ---------------------------------------------------------------------------


class AccountDomainError(Exception):
    """Base para erros de domínio de contas."""


class DuplicateAccountError(AccountDomainError):
    """Tentativa de cadastrar conta já existente."""

    def __init__(self, broker_id: str, account_id: str, account_type: AccountType) -> None:
        super().__init__(
            f"Conta já cadastrada: broker={broker_id} account={account_id} type={account_type}"
        )
        self.broker_id = broker_id
        self.account_id = account_id
        self.account_type = account_type


class AccountNotFoundError(AccountDomainError):
    """Conta não encontrada pelo identificador informado."""

    def __init__(self, account_uuid: str) -> None:
        super().__init__(f"Conta não encontrada: {account_uuid}")
        self.account_uuid = account_uuid


class NoActiveAccountError(AccountDomainError):
    """Operação requer conta ativa, mas nenhuma foi definida."""


# ---------------------------------------------------------------------------
# Entidade
# ---------------------------------------------------------------------------


@dataclass
class TradingAccount:
    """
    Entidade central de conta.

    `broker_id` + `account_id` + `account_type` formam a chave de negócio.
    `uuid` é o identificador interno do sistema.
    """

    uuid: str
    broker_id: str  # Ex: "227" (XP), "386" (Clear)
    account_id: str  # Número da conta na corretora
    account_type: AccountType
    label: str  # Nome amigável definido pelo usuário
    status: AccountStatus
    routing_password: str | None  # Senha de roteamento (nunca exposta em logs)
    created_at: datetime
    updated_at: datetime

    # Metadados opcionais vindos da DLL após sync
    broker_name: str | None = None
    sub_account_id: str | None = None

    @classmethod
    def create(
        cls,
        broker_id: str,
        account_id: str,
        account_type: AccountType,
        label: str,
        routing_password: str | None = None,
        sub_account_id: str | None = None,
    ) -> TradingAccount:
        now = datetime.now(UTC)
        return cls(
            uuid=str(uuid.uuid4()),
            broker_id=broker_id,
            account_id=account_id,
            account_type=account_type,
            label=label,
            status=AccountStatus.INACTIVE,
            routing_password=routing_password,
            created_at=now,
            updated_at=now,
            sub_account_id=sub_account_id,
        )

    def activate(self) -> None:
        self.status = AccountStatus.ACTIVE
        self.updated_at = datetime.now(UTC)

    def deactivate(self) -> None:
        self.status = AccountStatus.INACTIVE
        self.updated_at = datetime.now(UTC)

    @property
    def is_real(self) -> bool:
        return self.account_type == AccountType.REAL

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE

    def as_dll_identifier(self) -> dict[str, str]:
        """Retorna os campos necessários para montar TConnectorAccountIdentifier."""
        return {
            "broker_id": self.broker_id,
            "account_id": self.account_id,
            "sub_account_id": self.sub_account_id or "",
        }

    def __repr__(self) -> str:
        return (
            f"TradingAccount(uuid={self.uuid!r}, broker={self.broker_id}, "
            f"account={self.account_id}, type={self.account_type}, status={self.status})"
        )


# ---------------------------------------------------------------------------
# Repository protocol (porta — implementado na infra)
# ---------------------------------------------------------------------------


class AccountRepository(Protocol):
    async def save(self, account: TradingAccount) -> None:
        """Persiste nova conta. Levanta DuplicateAccountError se já existe."""
        ...

    async def get_by_uuid(self, account_uuid: str) -> TradingAccount:
        """Levanta AccountNotFoundError se não encontrar."""
        ...

    async def get_by_broker_account(
        self,
        broker_id: str,
        account_id: str,
        account_type: AccountType,
    ) -> TradingAccount | None: ...

    async def list_all(self) -> Sequence[TradingAccount]: ...

    async def list_by_type(self, account_type: AccountType) -> Sequence[TradingAccount]: ...

    async def get_active(self) -> TradingAccount | None:
        """Retorna a conta atualmente ativa, se houver."""
        ...

    async def set_active(self, account_uuid: str) -> TradingAccount:
        """
        Define a conta ativa.
        - Desativa todas as outras.
        - Levanta AccountNotFoundError se uuid não existir.
        """
        ...

    async def update(self, account: TradingAccount) -> None: ...

    async def delete(self, account_uuid: str) -> None: ...
