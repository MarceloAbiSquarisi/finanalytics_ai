"""
Repositório de alertas — SQLAlchemy async + PostgreSQL.

Segue o mesmo padrão do PortfolioRepository:
  - ORM model separado da entidade de domínio
  - Métodos async retornam entidades de domínio, nunca models
  - find_active_by_ticker é o hot path — chamado a cada PRICE_UPDATE do Kafka
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import DateTime, Numeric, String, Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from finanalytics_ai.domain.entities.alert import Alert, AlertStatus, AlertType
from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class AlertModel(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(30), nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AlertStatus.ACTIVE, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SQLAlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, alert: Alert) -> None:
        existing = await self._session.get(AlertModel, alert.alert_id)
        if existing:
            existing.status = alert.status.value
            existing.triggered_at = alert.triggered_at
        else:
            self._session.add(
                AlertModel(
                    alert_id=alert.alert_id,
                    user_id=alert.user_id,
                    ticker=alert.ticker.upper(),
                    alert_type=alert.alert_type.value,
                    threshold=alert.threshold,
                    reference_price=alert.reference_price,
                    status=alert.status.value,
                    note=alert.note,
                    created_at=alert.created_at,
                    expires_at=alert.expires_at,
                )
            )
        await self._session.flush()
        logger.debug("alert.saved", alert_id=alert.alert_id, status=alert.status)

    async def find_active_by_ticker(self, ticker: str) -> list[Alert]:
        """Hot path — chamado a cada tick de preço para o ticker."""
        stmt = (
            select(AlertModel)
            .where(AlertModel.ticker == ticker.upper())
            .where(AlertModel.status == AlertStatus.ACTIVE.value)
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars()]

    async def find_by_user(self, user_id: str) -> list[Alert]:
        stmt = select(AlertModel).where(AlertModel.user_id == user_id).order_by(AlertModel.created_at.desc())
        result = await self._session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars()]

    async def find_by_id(self, alert_id: str) -> Alert | None:
        model = await self._session.get(AlertModel, alert_id)
        return self._to_domain(model) if model else None

    async def mark_triggered(self, alert_id: str) -> None:
        stmt = (
            update(AlertModel)
            .where(AlertModel.alert_id == alert_id)
            .values(status=AlertStatus.TRIGGERED.value, triggered_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)

    async def cancel(self, alert_id: str, user_id: str) -> bool:
        stmt = (
            update(AlertModel)
            .where(AlertModel.alert_id == alert_id)
            .where(AlertModel.user_id == user_id)
            .where(AlertModel.status == AlertStatus.ACTIVE.value)
            .values(status=AlertStatus.CANCELLED.value)
        )
        result = await self._session.execute(stmt)
        return result.rowcount > 0  # type: ignore[attr-defined]

    def _to_domain(self, m: AlertModel) -> Alert:
        return Alert(
            alert_id=str(m.alert_id),
            user_id=str(m.user_id),
            ticker=str(m.ticker),
            alert_type=AlertType(m.alert_type),
            threshold=Decimal(str(m.threshold)),
            reference_price=Decimal(str(m.reference_price)),
            status=AlertStatus(m.status),
            note=str(m.note),
            created_at=m.created_at,
            triggered_at=m.triggered_at,
            expires_at=m.expires_at,
        )
