# finanalytics_fix_sprint2.ps1
# Corrige dois problemas nos testes de integracao V2:
# 1. asyncio_default_fixture_loop_scope ausente → pytest-asyncio 1.x skipa testes em classes
# 2. PG_UUID no ORM → falha no SQLite (tipo Postgres-especifico)
#
# Executar: powershell -ExecutionPolicy Bypass -File "D:\Downloads\finanalytics_fix_sprint2.ps1"

$ErrorActionPreference = "Stop"
$PROJECT = "D:\Projetos\finanalytics_ai_fresh"

function Write-Step($msg) { Write-Host "`n[FIX-S2] $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }

function Write-File($path, $content) {
    $dir = Split-Path $path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $content = $content.Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)
    Write-OK "Escrito: $path"
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Step "1/2 — Adicionando asyncio_default_fixture_loop_scope ao pyproject.toml"
# pytest-asyncio 1.x exige esta configuracao para executar testes async em classes.
# Sem ela, os testes sao SKIPPED silenciosamente (nao FAILED).
# ─────────────────────────────────────────────────────────────────────────────

$pyprojectPath = "$PROJECT\pyproject.toml"
$content = Get-Content $pyprojectPath -Raw

if ($content -notmatch "asyncio_default_fixture_loop_scope") {
    $old = 'asyncio_mode = "auto"'
    $new = @'
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
'@
    $content = $content.Replace($old, $new)
    $content = $content.Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText($pyprojectPath, $content, [System.Text.Encoding]::UTF8)
    Write-OK "asyncio_default_fixture_loop_scope = 'function' adicionado"
} else {
    Write-OK "asyncio_default_fixture_loop_scope ja existe"
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Step "2/2 — Reescrevendo conftest com Base SQLite-compativel"
# PG_UUID (PostgreSQL-especifico) falha no SQLite.
# Solucao: definir um Base alternativo com String(36) para UUIDs nos testes.
# O ORM de producao continua usando PG_UUID — nao tocamos nele.
# ─────────────────────────────────────────────────────────────────────────────

Write-File "$PROJECT\tests\integration\event_processor_v2\conftest.py" @'
"""
Fixtures de integracao para o EventProcessorService V2.

Estrategia: SQLite async (aiosqlite) — zero infraestrutura externa.

PROBLEMA DO PG_UUID:
    O ORM de producao (orm_models.py) usa PG_UUID(as_uuid=True),
    que e um tipo Postgres-especifico. SQLite nao reconhece.
    
    Solucao adotada: definir um Base proprio para testes com String(36)
    para colunas UUID. Isso espelha o schema de producao sem acoplamento
    ao dialeto Postgres.

    Alternativa rejeitada: mockar PG_UUID via conftest-level monkeypatch.
    Mais fragil — quebra se o ORM mudar internamente.

Por que nao usar pytest-postgresql?
    Requer Postgres instalado no CI. Mais lento (processo separado por teste).
    Para logica de dominio, SQLite e suficiente e muito mais rapido.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from finanalytics_ai.infrastructure.event_processor.idempotency import InMemoryIdempotencyStore


# ── Base SQLite-compativel (sem PG_UUID) ──────────────────────────────────────

class TestBase(DeclarativeBase):
    """Base ORM exclusiva para testes — usa tipos portateis."""
    pass


class TestEventRecord(TestBase):
    """
    Espelho do EventRecord de producao com tipos SQLite-compativeis.
    UUID armazenado como String(36) em vez de PG_UUID.
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
        default=lambda: datetime.now(timezone.utc),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(loop_scope="function")
async def async_engine():
    """Engine SQLite em memoria — schema limpo por teste."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        # SQLite nao suporta multiple connections para :memory: por padrao
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(TestBase.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(async_engine):
    """async_sessionmaker pronta para uso nos testes."""
    return async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture(loop_scope="function")
async def sql_repository(session_factory):
    """
    SqlEventRepository substituido por TestSqlRepository.

    Nao usamos SqlEventRepository diretamente porque ele referencia
    EventRecord (com PG_UUID). Usamos uma versao de teste com TestEventRecord.
    """
    return TestSqlRepository(session_factory)


@pytest.fixture
def idempotency_store():
    """InMemoryIdempotencyStore — instancia limpa por teste."""
    return InMemoryIdempotencyStore()


# ── Repositorio de teste ───────────────────────────────────────────────────────

from finanalytics_ai.domain.events.models import DomainEvent, EventStatus


class TestSqlRepository:
    """
    EventRepository usando TestEventRecord (SQLite-compativel).

    Implementa o mesmo contrato de SqlEventRepository
    mas persiste em TestEventRecord (String UUID) em vez de EventRecord (PG_UUID).
    """

    def __init__(self, session_factory) -> None:  # type: ignore[type-arg]
        self._sf = session_factory

    async def upsert(self, event: DomainEvent) -> None:
        async with self._sf() as session:
            async with session.begin():
                record = TestEventRecord(
                    event_id=str(event.event_id),
                    event_type=str(event.payload.event_type),
                    source=event.payload.source,
                    correlation_id=(
                        str(event.payload.correlation_id)
                        if event.payload.correlation_id else None
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
                select(TestEventRecord).where(
                    TestEventRecord.event_id == str(event_id)
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def find_by_status(
        self, status: EventStatus, *, limit: int = 100
    ) -> list[DomainEvent]:
        from sqlalchemy import select
        async with self._sf() as session:
            result = await session.execute(
                select(TestEventRecord)
                .where(TestEventRecord.status == str(status))
                .limit(limit)
            )
            rows = result.scalars().all()
            return [self._to_domain(r) for r in rows]

    def _to_domain(self, row: TestEventRecord) -> DomainEvent:
        from finanalytics_ai.domain.events.models import EventPayload
        from finanalytics_ai.domain.events.value_objects import EventType
        payload = EventPayload(
            event_type=EventType(row.event_type),
            data=row.payload_data or {},
            source=row.source,
        )
        event = DomainEvent(
            event_id=uuid.UUID(row.event_id),
            payload=payload,
            status=EventStatus(row.status),
            created_at=row.created_at,
            processed_at=row.processed_at,
            error_message=row.error_message,
            retry_count=row.retry_count,
            metadata=row.metadata_ or {},
        )
        return event
'@

Write-Host "`n[FIX-S2] Concluido!" -ForegroundColor Green
Write-Host @"

Rode agora:
  uv run pytest tests/integration/event_processor_v2/ -v

Deve mostrar 6 testes rodando (nao mais 'skipped').
"@
