"""
Repositório de contas de negociação — SQLAlchemy async + PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Text, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column
import structlog

from finanalytics_ai.domain.accounts import (
    AccountNotFoundError,
    AccountStatus,
    AccountType,
    DuplicateAccountError,
    TradingAccount,
)
from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class TradingAccountModel(Base):
    __tablename__ = "trading_accounts"
    __table_args__ = (
        UniqueConstraint("broker_id", "account_id", "account_type", name="uq_trading_account"),
    )

    uuid: Mapped[str] = mapped_column(String(36), primary_key=True)
    broker_id: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(50), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="inactive", index=True)
    routing_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_account_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)


class SQLAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, account: TradingAccount) -> None:
        existing = await self._get_by_broker_account_model(
            account.broker_id, account.account_id, account.account_type
        )
        if existing:
            raise DuplicateAccountError(account.broker_id, account.account_id, account.account_type)
        self._session.add(self._to_model(account))
        await self._session.flush()
        logger.info("account.created", account_uuid=account.uuid, account_type=account.account_type)

    async def get_by_uuid(self, account_uuid: str) -> TradingAccount:
        model = await self._session.get(TradingAccountModel, account_uuid)
        if not model:
            raise AccountNotFoundError(account_uuid)
        return self._to_domain(model)

    async def get_by_broker_account(
        self,
        broker_id: str,
        account_id: str,
        account_type: AccountType,
    ) -> TradingAccount | None:
        model = await self._get_by_broker_account_model(broker_id, account_id, account_type)
        return self._to_domain(model) if model else None

    async def list_all(self) -> Sequence[TradingAccount]:
        stmt = select(TradingAccountModel).order_by(TradingAccountModel.created_at)
        result = await self._session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars()]

    async def list_by_type(self, account_type: AccountType) -> Sequence[TradingAccount]:
        stmt = (
            select(TradingAccountModel)
            .where(TradingAccountModel.account_type == account_type.value)
            .order_by(TradingAccountModel.created_at)
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars()]

    async def get_active(self) -> TradingAccount | None:
        stmt = (
            select(TradingAccountModel)
            .where(TradingAccountModel.status == AccountStatus.ACTIVE.value)
            .limit(1)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def set_active(self, account_uuid: str) -> TradingAccount:
        model = await self._session.get(TradingAccountModel, account_uuid)
        if not model:
            raise AccountNotFoundError(account_uuid)

        await self._session.execute(
            update(TradingAccountModel).values(
                status=AccountStatus.INACTIVE.value,
                updated_at=datetime.utcnow(),
            )
        )
        model.status = AccountStatus.ACTIVE.value
        model.updated_at = datetime.utcnow()
        await self._session.flush()

        logger.info("account.activated", account_uuid=account_uuid)
        return self._to_domain(model)

    async def update(self, account: TradingAccount) -> None:
        model = await self._session.get(TradingAccountModel, account.uuid)
        if not model:
            raise AccountNotFoundError(account.uuid)
        model.label = account.label
        model.routing_password = account.routing_password
        model.broker_name = account.broker_name
        model.sub_account_id = account.sub_account_id
        model.updated_at = datetime.utcnow()
        await self._session.flush()

    async def delete(self, account_uuid: str) -> None:
        model = await self._session.get(TradingAccountModel, account_uuid)
        if not model:
            raise AccountNotFoundError(account_uuid)
        await self._session.delete(model)
        await self._session.flush()

    async def _get_by_broker_account_model(
        self,
        broker_id: str,
        account_id: str,
        account_type: AccountType,
    ) -> TradingAccountModel | None:
        stmt = (
            select(TradingAccountModel)
            .where(TradingAccountModel.broker_id == broker_id)
            .where(TradingAccountModel.account_id == account_id)
            .where(TradingAccountModel.account_type == account_type.value)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    def _to_model(self, a: TradingAccount) -> TradingAccountModel:
        return TradingAccountModel(
            uuid=a.uuid,
            broker_id=a.broker_id,
            account_id=a.account_id,
            account_type=a.account_type.value,
            label=a.label,
            status=a.status.value,
            routing_password=a.routing_password,
            broker_name=a.broker_name,
            sub_account_id=a.sub_account_id,
            created_at=a.created_at.replace(tzinfo=None) if a.created_at.tzinfo else a.created_at,
            updated_at=a.updated_at.replace(tzinfo=None) if a.updated_at.tzinfo else a.updated_at,
        )

    def _to_domain(self, m: TradingAccountModel) -> TradingAccount:
        return TradingAccount(
            uuid=m.uuid,
            broker_id=m.broker_id,
            account_id=m.account_id,
            account_type=AccountType(m.account_type),
            label=m.label,
            status=AccountStatus(m.status),
            routing_password=m.routing_password,
            broker_name=m.broker_name,
            sub_account_id=m.sub_account_id,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )
