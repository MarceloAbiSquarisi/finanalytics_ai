"""
AccountService — facade que agrega os use cases e gerencia a sessão SQLAlchemy.

Segue o mesmo padrão dos outros services do projeto:
  - Instanciado no lifespan do app.py
  - Exposto via get_account_service() (módulo-level singleton)
  - Cada método abre/fecha sessão via session_factory
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
import structlog

from finanalytics_ai.application.use_cases import (
    CreateAccount,
    CreateAccountCmd,
    DeleteAccount,
    GetAccount,
    GetActiveAccount,
    ListAccounts,
    SetActiveAccount,
    UpdateAccount,
    UpdateAccountCmd,
)
from finanalytics_ai.domain.accounts import AccountType, TradingAccount
from finanalytics_ai.infrastructure.database.repositories.sql_account_repo import (
    SQLAccountRepository,
)

logger = structlog.get_logger(__name__)


class AccountService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    def _repo(self, session: AsyncSession) -> SQLAccountRepository:
        return SQLAccountRepository(session)

    async def create(self, data: dict) -> TradingAccount:
        async with self._factory() as session, session.begin():
            cmd = CreateAccountCmd(
                broker_id=data["broker_id"],
                account_id=data["account_id"],
                account_type=AccountType(data["account_type"]),
                label=data["label"],
                routing_password=data.get("routing_password"),
                sub_account_id=data.get("sub_account_id"),
            )
            return await CreateAccount(self._repo(session)).execute(cmd)

    async def list(self, account_type: str | None = None) -> Sequence[TradingAccount]:
        async with self._factory() as session:
            at = AccountType(account_type) if account_type else None
            return await ListAccounts(self._repo(session)).execute(at)

    async def get(self, account_uuid: str) -> TradingAccount:
        async with self._factory() as session:
            return await GetAccount(self._repo(session)).execute(account_uuid)

    async def get_active(self) -> TradingAccount:
        async with self._factory() as session:
            return await GetActiveAccount(self._repo(session)).execute()

    async def set_active(self, account_uuid: str) -> TradingAccount:
        async with self._factory() as session, session.begin():
            return await SetActiveAccount(self._repo(session)).execute(account_uuid)

    async def update(self, account_uuid: str, data: dict) -> TradingAccount:
        async with self._factory() as session, session.begin():
            cmd = UpdateAccountCmd(
                account_uuid=account_uuid,
                label=data.get("label"),
                routing_password=data.get("routing_password"),
            )
            return await UpdateAccount(self._repo(session)).execute(cmd)

    async def delete(self, account_uuid: str) -> None:
        async with self._factory() as session, session.begin():
            await DeleteAccount(self._repo(session)).execute(account_uuid)
