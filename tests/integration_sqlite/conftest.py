"""
Fixtures de integracao para o EventProcessorService V2.

Estrategia: SQLite async (aiosqlite) — zero infraestrutura externa.

PROBLEMA DO PG_UUID:
    O ORM de producao (orm_models.py) usa PG_UUID(as_uuid=True),
    que e um tipo Postgres-especifico. SQLite nao reconhece.

    Solucao: TestEventRecord com String(36) para UUIDs.
    O ORM de producao nao e tocado.

NOTA pytest-asyncio 1.x:
    - loop_scope NAO deve ser passado nos decoradores quando
      asyncio_default_fixture_loop_scope ja esta no pyproject.toml.
      Passar os dois causa skip silencioso em alguns cenarios.
    - Fixtures async que nao fazem teardown usam yield mesmo assim
      para compatibilidade com pytest-asyncio 1.x.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from finanalytics_ai.domain.events.models import DomainEvent, EventStatus
from finanalytics_ai.infrastructure.event_processor.idempotency import InMemoryIdempotencyStore

# ── Base SQLite-compativel (sem PG_UUID) ──────────────────────────────────────


class TestBase(DeclarativeBase):
    """Base ORM exclusiva para testes — usa tipos portateis."""

    pass


class TestEventRecord(TestBase):
    """
    Espelho do EventRecord de producao com tipos SQLite-compativeis.
    UUID como String(36) em vez de PG_UUID.
    """

    __tablename__ = "event_records"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    payload_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ── Fixtures ──────────────────────────────────────────────────────────────────
# IMPORTANTE: sem loop_scope nos decoradores.
# O escopo e controlado globalmente por asyncio_default_fixture_loop_scope
# no pyproject.toml. Duplicar aqui causa conflito no pytest-asyncio 1.x.


@pytest_asyncio.fixture
async def async_engine():
    """Engine SQLite em memoria — schema limpo por teste."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(TestBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(async_engine):
    """async_sessionmaker pronta para uso nos testes."""
    factory = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    yield factory


@pytest_asyncio.fixture
async def sql_repository(session_factory):
    """TestSqlRepository com schema SQLite-compativel."""
    yield TestSqlRepository(session_factory)


@pytest.fixture
def idempotency_store():
    """InMemoryIdempotencyStore — instancia limpa por teste."""
    return InMemoryIdempotencyStore()


# ── Repositorio de teste ───────────────────────────────────────────────────────


class TestSqlRepository:
    """
    EventRepository usando TestEventRecord (SQLite-compativel).

    Implementa o contrato de SqlEventRepository mas persiste em
    TestEventRecord (String UUID) em vez de EventRecord (PG_UUID).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def upsert(self, event: DomainEvent) -> None:
        async with self._sf() as session, session.begin():
            record = TestEventRecord(
                event_id=str(event.event_id),
                event_type=str(event.payload.event_type),
                source=event.payload.source,
                correlation_id=(
                    str(event.payload.correlation_id) if event.payload.correlation_id else None
                ),
                status=str(event.status),
                payload_data=event.payload.data,
                error_message=event.error_message,
                retry_count=event.retry_count,
                metadata_=event.metadata,
                created_at=event.created_at,
                processed_at=event.processed_at,
            )
            await session.merge(record)

    async def find_by_id(self, event_id: uuid.UUID) -> DomainEvent | None:
        from sqlalchemy import select

        async with self._sf() as session:
            result = await session.execute(
                select(TestEventRecord).where(TestEventRecord.event_id == str(event_id))
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def find_by_status(self, status: EventStatus, *, limit: int = 100) -> list[DomainEvent]:
        from sqlalchemy import select

        async with self._sf() as session:
            result = await session.execute(
                select(TestEventRecord).where(TestEventRecord.status == str(status)).limit(limit)
            )
            return [self._to_domain(r) for r in result.scalars().all()]

    def _to_domain(self, row: TestEventRecord) -> DomainEvent:
        from finanalytics_ai.domain.events.models import EventPayload
        from finanalytics_ai.domain.events.value_objects import EventType

        payload = EventPayload(
            event_type=EventType(row.event_type),
            data=row.payload_data or {},
            source=row.source,
        )
        return DomainEvent(
            event_id=uuid.UUID(row.event_id),
            payload=payload,
            status=EventStatus(row.status),
            created_at=row.created_at,
            processed_at=row.processed_at,
            error_message=row.error_message,
            retry_count=row.retry_count,
            metadata=row.metadata_ or {},
        )
