"""
Implementacao concreta do EventRepository usando SQLAlchemy 2.x async.

Decisao: upsert via merge() do SQLAlchemy em vez de INSERT ON CONFLICT.
Motivo: portabilidade entre PostgreSQL e SQLite (para testes).
Em producao com PostgreSQL, INSERT ... ON CONFLICT seria mais eficiente,
mas a diferenca eh negligivel para o volume esperado.

Se performance se tornar gargalo: trocar para execute(
    insert(EventRecord).values(...).on_conflict_do_update(...)
) com dialect-specific clause.

session_factory: callable que retorna AsyncSession. Padrao do projeto
(veja infrastructure/database.py existente).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finanalytics_ai.domain.events.exceptions import DatabaseError
from finanalytics_ai.infrastructure.event_processor.mapper import (
    domain_to_record,
    record_to_domain,
)
from finanalytics_ai.infrastructure.event_processor.orm_models import EventRecord

if TYPE_CHECKING:
    import uuid

from finanalytics_ai.domain.events.models import DomainEvent, EventStatus

logger = structlog.get_logger(__name__)

# Type alias para legibilidade
SessionFactory = Callable[[], AsyncSession]


class SqlEventRepository:
    """
    Implementacao do EventRepository com SQLAlchemy async.

    session_factory: fabrica de sessoes (normalmente async_sessionmaker).
    Recebemos a factory, nao a sessao, para garantir que cada operacao
    use sua propria sessao (evita estado compartilhado em contextos async).
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def upsert(self, event: DomainEvent) -> None:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    record = domain_to_record(event)
                    await session.merge(record)
        except Exception as exc:
            logger.error(
                "repository.upsert_failed",
                event_id=str(event.event_id),
                error=str(exc),
            )
            raise DatabaseError(
                f"Falha ao persistir evento {event.event_id}: {exc}",
                event_id=event.event_id,
                original=exc,
            ) from exc

    async def find_by_id(self, event_id: uuid.UUID) -> DomainEvent | None:
        try:
            async with self._session_factory() as session:
                result = await session.get(EventRecord, event_id)
                if result is None:
                    return None
                return record_to_domain(result)
        except Exception as exc:
            raise DatabaseError(
                f"Falha ao buscar evento {event_id}: {exc}",
                event_id=event_id,
                original=exc,
            ) from exc

    async def find_by_status(
        self,
        status: EventStatus,
        *,
        limit: int = 100,
    ) -> list[DomainEvent]:
        try:
            async with self._session_factory() as session:
                stmt = (
                    select(EventRecord)
                    .where(EventRecord.status == status.value)
                    .order_by(EventRecord.created_at)
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return [record_to_domain(r) for r in result.scalars().all()]
        except Exception as exc:
            raise DatabaseError(
                f"Falha ao buscar eventos com status {status}: {exc}",
                original=exc,
            ) from exc
