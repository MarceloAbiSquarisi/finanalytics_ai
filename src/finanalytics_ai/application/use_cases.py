"""
Casos de uso do módulo de contas.

Cada use case recebe suas dependências via __init__ (DI manual).
Não conhece HTTP, SQL ou DLL — só o protocolo AccountRepository.

Decisão de design: use cases são classes, não funções.
Motivo: facilita mock parcial em testes e permite estado efêmero
(ex: cache de conta ativa dentro de uma request).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging

from finanalytics_ai.domain.accounts import (
    AccountDomainError,
    AccountRepository,
    AccountType,
    DuplicateAccountError,
    NoActiveAccountError,
    TradingAccount,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTOs de entrada (evita acoplar o use case ao schema HTTP)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateAccountCmd:
    broker_id: str
    account_id: str
    account_type: AccountType
    label: str
    routing_password: str | None = None
    sub_account_id: str | None = None


@dataclass(frozen=True)
class UpdateAccountCmd:
    account_uuid: str
    label: str | None = None
    routing_password: str | None = None
    broker_name: str | None = None


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


class CreateAccount:
    """
    Cadastra uma nova conta.

    Idempotência: se a mesma (broker_id, account_id, type) já existe,
    levanta DuplicateAccountError — o chamador decide se ignora ou exibe erro.
    Não faz upsert silencioso para evitar overwrite acidental de senha de roteamento.
    """

    def __init__(self, repo: AccountRepository) -> None:
        self._repo = repo

    async def execute(self, cmd: CreateAccountCmd) -> TradingAccount:
        existing = await self._repo.get_by_broker_account(
            broker_id=cmd.broker_id,
            account_id=cmd.account_id,
            account_type=cmd.account_type,
        )
        if existing:
            raise DuplicateAccountError(cmd.broker_id, cmd.account_id, cmd.account_type)

        account = TradingAccount.create(
            broker_id=cmd.broker_id,
            account_id=cmd.account_id,
            account_type=cmd.account_type,
            label=cmd.label,
            routing_password=cmd.routing_password,
            sub_account_id=cmd.sub_account_id,
        )
        await self._repo.save(account)

        logger.info(
            "account.created",
            extra={
                "account_uuid": account.uuid,
                "broker_id": account.broker_id,
                "account_type": account.account_type,
            },
        )
        return account


class ListAccounts:
    def __init__(self, repo: AccountRepository) -> None:
        self._repo = repo

    async def execute(self, account_type: AccountType | None = None) -> Sequence[TradingAccount]:
        if account_type:
            return await self._repo.list_by_type(account_type)
        return await self._repo.list_all()


class GetAccount:
    def __init__(self, repo: AccountRepository) -> None:
        self._repo = repo

    async def execute(self, account_uuid: str) -> TradingAccount:
        return await self._repo.get_by_uuid(account_uuid)


class SetActiveAccount:
    """
    Define qual conta está ativa para operações (manual e automático).

    Apenas uma conta ativa por vez — o repositório garante atomicidade
    ao desativar todas antes de ativar a escolhida.

    Trade-off: não permitimos conta ativa por tipo (uma real + uma simulador).
    Motivo: simplifica o fluxo de ordens automáticas — um único ponto de
    verdade sobre "para onde vai a ordem". Se o produto crescer e precisar
    de paralelo real+sim, adicionar `set_active_by_type`.
    """

    def __init__(self, repo: AccountRepository) -> None:
        self._repo = repo

    async def execute(self, account_uuid: str) -> TradingAccount:
        account = await self._repo.set_active(account_uuid)
        logger.info(
            "account.activated",
            extra={
                "account_uuid": account.uuid,
                "account_type": account.account_type,
                "broker_id": account.broker_id,
            },
        )
        return account


class GetActiveAccount:
    """
    Retorna a conta ativa. Levanta NoActiveAccountError se nenhuma está ativa.
    Usado pelo módulo de ordens antes de qualquer envio.
    """

    def __init__(self, repo: AccountRepository) -> None:
        self._repo = repo

    async def execute(self) -> TradingAccount:
        account = await self._repo.get_active()
        if not account:
            raise NoActiveAccountError("Nenhuma conta ativa. Selecione uma conta antes de operar.")
        return account


class UpdateAccount:
    def __init__(self, repo: AccountRepository) -> None:
        self._repo = repo

    async def execute(self, cmd: UpdateAccountCmd) -> TradingAccount:
        account = await self._repo.get_by_uuid(cmd.account_uuid)

        if cmd.label is not None:
            account.label = cmd.label
        if cmd.routing_password is not None:
            account.routing_password = cmd.routing_password
        if cmd.broker_name is not None:
            account.broker_name = cmd.broker_name

        await self._repo.update(account)
        logger.info("account.updated", extra={"account_uuid": account.uuid})
        return account


class DeleteAccount:
    def __init__(self, repo: AccountRepository) -> None:
        self._repo = repo

    async def execute(self, account_uuid: str) -> None:
        account = await self._repo.get_by_uuid(account_uuid)
        if account.is_active:
            raise AccountDomainError(
                "Não é possível remover a conta ativa. Ative outra conta antes."
            )
        await self._repo.delete(account_uuid)
        logger.info("account.deleted", extra={"account_uuid": account_uuid})
