"""
Implementação concreta de EventStore usando SQLAlchemy + PostgreSQL.

Implementa o Port EventStore do domínio.
Design decision: Repository Pattern — o domínio nunca importa SQLAlchemy.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import Column, DateTime, Integer, String, Text, select, update

from finanalytics_ai.domain.entities.event import EventStatus, EventType, MarketEvent
from finanalytics_ai.infrastructure.database.connection import Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


class EventModel(Base):
    """Modelo ORM para eventos de mercado."""

    __tablename__ = "market_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), unique=True, nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    ticker = Column(String(10), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    source = Column(String(100), default="unknown")
    status = Column(String(20), default=EventStatus.PENDING, index=True)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, default="")
    occurred_at = Column(DateTime, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SQLEventStore:
    """Implementação de EventStore usando PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, event: MarketEvent) -> None:
        model = EventModel(
            event_id=event.event_id,
            event_type=event.event_type.value,
            ticker=event.ticker,
            payload=json.dumps(event.payload),
            source=event.source,
            status=event.status.value,
            occurred_at=event.occurred_at,
        )
        self._session.add(model)
        await self._session.flush()
        logger.debug("event.saved", event_id=event.event_id)

    async def find_by_id(self, event_id: str) -> MarketEvent | None:
        stmt = select(EventModel).where(EventModel.event_id == event_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return self._to_domain(model)

    async def exists(self, event_id: str) -> bool:
        stmt = select(EventModel.id).where(EventModel.event_id == event_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def update_status(self, event_id: str, status: EventStatus, error: str = "") -> None:
        values: dict[str, object] = {"status": status.value}
        if error:
            values["error_message"] = error
        if status == EventStatus.PROCESSED:
            values["processed_at"] = datetime.utcnow()
        stmt = update(EventModel).where(EventModel.event_id == event_id).values(**values)
        await self._session.execute(stmt)

    async def find_pending(self, limit: int = 100) -> list[MarketEvent]:
        stmt = (
            select(EventModel)
            .where(EventModel.status == EventStatus.PENDING.value)
            .limit(limit)
            .order_by(EventModel.occurred_at)
        )
        result = await self._session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars()]

    def _to_domain(self, model: EventModel) -> MarketEvent:
        return MarketEvent(
            event_id=str(model.event_id),
            event_type=EventType(model.event_type),
            ticker=str(model.ticker),
            payload=json.loads(str(model.payload)),
            source=str(model.source),
            status=EventStatus(model.status),
            retry_count=int(model.retry_count),
            error_message=str(model.error_message),
            occurred_at=model.occurred_at,
            processed_at=model.processed_at,
        )
