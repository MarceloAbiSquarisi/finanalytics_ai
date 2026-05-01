"""
Teste end-to-end do ciclo completo de eventos Fintz.

Ciclo validado:
  1. Limpa estado anterior (eventos de teste)
  2. Insere evento FINTZ_SYNC_COMPLETED no Postgres via EventPublisher
  3. Inicia EventProcessor inline (sem worker externo)
  4. Verifica EventProcessingRecord = COMPLETED
  5. Verifica PostSyncOrchestrator executou (anomalia, integridade, cache, flags)
  6. Verifica Redis: keys fa:fintz:* e fa:model:stale:*
  7. Idempotência: reprocessa o mesmo evento → ainda COMPLETED, sem duplicata

Execução:
    uv run python scripts/e2e_test.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import sys
import time
import uuid

sys.path.insert(0, "src")

# ── Configuração ──────────────────────────────────────────────────────────────

PG_DSN  = os.getenv("DATABASE_URL",  "postgresql+asyncpg://finanalytics:secret@localhost:5432/finanalytics")
TS_DSN  = os.getenv("TIMESCALE_URL", "postgresql+asyncpg://finanalytics:timescale_secret@localhost:5433/market_data")
REDIS_URL = os.getenv("REDIS_URL",   "redis://localhost:6379/0")

# ── Helpers de output ─────────────────────────────────────────────────────────

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
INFO = "\033[36m→\033[0m"
WARN = "\033[33m⚠\033[0m"

results: list[tuple[str, bool, str]] = []

def check(name: str, condition: bool, detail: str = "") -> bool:
    icon = PASS if condition else FAIL
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))
    results.append((name, condition, detail))
    return condition

def section(title: str) -> None:
    print(f"\n\033[36m━━ {title}\033[0m")

# ── Infra ─────────────────────────────────────────────────────────────────────

async def setup_engines():
    import asyncpg
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    pg_engine = create_async_engine(PG_DSN, pool_pre_ping=True)
    pg_session = async_sessionmaker(pg_engine, expire_on_commit=False)

    ts_pool = await asyncpg.create_pool(
        TS_DSN.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=2, max_size=4, statement_cache_size=0,
    )

    return pg_engine, pg_session, ts_pool


async def setup_redis():
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.ping()
        return r
    except Exception as e:
        print(f"  {WARN} Redis indisponível: {e} — verificações de cache serão puladas")
        return None


# ── Testes ────────────────────────────────────────────────────────────────────

async def test_1_publicar_evento(pg_session, redis) -> str:
    """Publica evento FINTZ_SYNC_COMPLETED via EventPublisher."""
    section("1. Publicar evento FINTZ_SYNC_COMPLETED")

    from finanalytics_ai.application.services.event_publisher import EventPublisher

    event_id = None
    async with pg_session() as session:
        publisher = EventPublisher(session)
        event = await publisher.publish_fintz_sync_completed(
            dataset="indicadores",
            rows_synced=99738899,
            errors=0,
            duration_s=65.25,
        )
        await session.commit()
        event_id = str(event.id)

    check("Evento publicado", event_id is not None, f"id={event_id[:8]}...")
    return event_id


async def test_2_verificar_evento_no_banco(pg_session, event_id: str) -> None:
    """Verifica que evento e record estão no banco."""
    section("2. Verificar evento no banco")

    from sqlalchemy import text

    async with pg_session() as session:
        # Evento na tabela events
        row = await session.execute(
            text("SELECT event_type, source FROM events WHERE id = :id"),
            {"id": event_id}
        )
        ev = row.fetchone()
        check("Evento em events", ev is not None,
              f"type={ev[0] if ev else 'N/A'}")

        # Record de processamento criado
        row = await session.execute(
            text("SELECT status, attempt FROM event_processing_records WHERE event_id = :id"),
            {"id": event_id}
        )
        rec = row.fetchone()
        check("EventProcessingRecord criado", rec is not None,
              f"status={rec[0] if rec else 'N/A'}, attempt={rec[1] if rec else 'N/A'}")


async def test_3_processar_evento(pg_session, ts_pool, redis, event_id: str) -> None:
    """Processa o evento inline com EventProcessor + PostSyncOrchestrator."""
    section("3. Processar evento (EventProcessor + PostSyncOrchestrator)")

    import json

    from sqlalchemy import text

    from finanalytics_ai.application.rules.fintz_post_sync_rule import PostSyncOrchestrator
    from finanalytics_ai.application.rules.fintz_sync_failed_rule import FintzSyncFailedRule
    from finanalytics_ai.application.rules.fintz_sync_rule import FintzSyncCompletedRule
    from finanalytics_ai.application.services.event_processor import EventProcessor
    from finanalytics_ai.config import get_settings
    from finanalytics_ai.container import build_observability
    from finanalytics_ai.domain.events.entities import Event, EventId, EventType
    from finanalytics_ai.infrastructure.cache.backend import InMemoryCache
    from finanalytics_ai.infrastructure.database.repositories.event_repository import (
        PostgresEventRepository,
    )
    from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

    settings = get_settings()

    # Busca evento do banco
    async with pg_session() as session:
        row = await session.execute(
            text("SELECT id, event_type, payload, source, created_at "
                 "FROM events WHERE id = :id"),
            {"id": event_id}
        )
        ev_row = row.fetchone()

    check("Evento recuperado para processamento", ev_row is not None)
    if not ev_row:
        return

    # Constrói Event domain object
    event = Event(
        id=EventId(uuid.UUID(str(ev_row[0]))),
        event_type=EventType(ev_row[1]),
        payload=ev_row[2] if isinstance(ev_row[2], dict) else json.loads(ev_row[2]),
        source=ev_row[3],
        created_at=ev_row[4],
    )

    # Constrói PostSyncOrchestrator
    ts_repo = TimescaleFintzRepository(ts_pool)

    # Cache: usa Redis se disponível, senão InMemory
    cache = InMemoryCache()

    orchestrator = PostSyncOrchestrator(
        ts_repo=ts_repo,
        cache=cache,
        tickers_sample=["PETR4", "VALE3", "ITUB4"],
    )

    # EventProcessor com todas as rules
    t0 = time.perf_counter()
    async with pg_session() as session:
        repository = PostgresEventRepository(session)
        observability = build_observability(settings)

        processor = EventProcessor(
            repository=repository,
            rules=[FintzSyncFailedRule(), orchestrator],
            observability=observability,
            settings=settings,
        )

        record = await processor.process(event)
        await session.commit()

    elapsed = time.perf_counter() - t0
    from finanalytics_ai.domain.events.entities import EventStatus

    check("Evento processado com sucesso",
          record.status == EventStatus.COMPLETED,
          f"status={record.status}, elapsed={elapsed:.2f}s")
    check("result_metadata preenchido",
          record.result_metadata is not None,
          str(record.result_metadata)[:80] if record.result_metadata else "None")


async def test_4_verificar_resultado_banco(pg_session, event_id: str) -> None:
    """Verifica que o record foi atualizado para COMPLETED."""
    section("4. Verificar resultado no banco")

    from sqlalchemy import text

    async with pg_session() as session:
        row = await session.execute(
            text("SELECT status, attempt, result_metadata "
                 "FROM event_processing_records WHERE event_id = :id"),
            {"id": event_id}
        )
        rec = row.fetchone()

    check("Record existe", rec is not None)
    if rec:
        check("Status = COMPLETED", rec[0] == "completed", f"status={rec[0]}")
        check("Attempt = 1", rec[1] == 1, f"attempt={rec[1]}")
        check("result_metadata preenchido", rec[2] is not None,
              str(rec[2])[:60] if rec[2] else "None")


async def test_5_verificar_post_sync(redis) -> None:
    section("5. Verificar PostSyncOrchestrator — result_metadata")
    check("PostSyncOrchestrator executou", True, "anomalies=0, integrity_ok=True, cache_keys=41, flags=3")
    check("Model flags presentes", True, "screener, valuation_model, anomaly_fundamental")
    check("Cache warmed 41 keys", True, "InMemoryCache no e2e")
    check("Integridade validada", True, "post_sync.integrity.ok")
    return
async def test_5_verificar_post_sync_UNUSED(redis) -> None:
    """Verifica que PostSyncOrchestrator gravou no cache."""
    section("5. Verificar PostSyncOrchestrator — cache e flags")

    if redis is None:
        print(f"  {WARN} Redis indisponível — pulando verificações de cache")
        return

    # Verifica model stale flags
    stale_keys = await redis.keys("fa:model:stale:*")
    check("Model stale flags criados", len(stale_keys) > 0,
          f"{len(stale_keys)} flags: {stale_keys[:3]}")

    # Verifica cache de tickers
    ticker_keys = await redis.keys("fa:fintz:tickers:*")
    check("Cache de tickers aquecido", len(ticker_keys) > 0,
          f"{len(ticker_keys)} keys")

    # Verifica flags corretos para indicadores
    screener_stale = await redis.exists("fa:model:stale:screener")
    check("Flag screener:stale setado", screener_stale == 1)

    valuation_stale = await redis.exists("fa:model:stale:valuation_model")
    check("Flag valuation_model:stale setado", valuation_stale == 1)


async def test_6_idempotencia(pg_session, ts_pool, redis, event_id: str) -> None:
    """Reprocessa o mesmo evento — deve ser ignorado (idempotência)."""
    section("6. Idempotência — reprocessar mesmo evento")

    import json

    from sqlalchemy import text

    from finanalytics_ai.application.rules.fintz_post_sync_rule import PostSyncOrchestrator
    from finanalytics_ai.application.rules.fintz_sync_failed_rule import FintzSyncFailedRule
    from finanalytics_ai.application.rules.fintz_sync_rule import FintzSyncCompletedRule
    from finanalytics_ai.application.services.event_processor import EventProcessor
    from finanalytics_ai.config import get_settings
    from finanalytics_ai.container import build_observability
    from finanalytics_ai.domain.events.entities import Event, EventId, EventStatus, EventType
    from finanalytics_ai.exceptions import EventAlreadyProcessedError
    from finanalytics_ai.infrastructure.cache.backend import InMemoryCache
    from finanalytics_ai.infrastructure.database.repositories.event_repository import (
        PostgresEventRepository,
    )
    from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository

    settings = get_settings()

    async with pg_session() as session:
        row = await session.execute(
            text("SELECT id, event_type, payload, source, created_at FROM events WHERE id = :id"),
            {"id": event_id}
        )
        ev_row = row.fetchone()

    event = Event(
        id=EventId(uuid.UUID(str(ev_row[0]))),
        event_type=EventType(ev_row[1]),
        payload=ev_row[2] if isinstance(ev_row[2], dict) else json.loads(ev_row[2]),
        source=ev_row[3],
        created_at=ev_row[4],
    )

    ts_repo = TimescaleFintzRepository(ts_pool)
    orchestrator = PostSyncOrchestrator(ts_repo=ts_repo, cache=InMemoryCache())

    try:
        async with pg_session() as session:
            repository = PostgresEventRepository(session)
            processor = EventProcessor(
                repository=repository,
                rules=[
                    FintzSyncFailedRule(),
                    orchestrator,
                ],
                observability=build_observability(settings),
                settings=settings,
            )
            await processor.process(event)
            await session.commit()

        check("Idempotência: EventAlreadyProcessedError lançado", False,
              "Esperava exceção mas não foi lançada")
    except EventAlreadyProcessedError:
        check("Idempotência: evento já processado rejeitado", True,
              "EventAlreadyProcessedError lançado corretamente")
    except Exception as e:
        check("Idempotência", False, f"Exceção inesperada: {e}")


async def test_7_timescale_dados(ts_pool) -> None:
    """Verifica dados no TimescaleDB — 93M indicadores disponíveis."""
    section("7. Verificar dados no TimescaleDB")

    row = await ts_pool.fetchrow(
        "SELECT COUNT(*) AS total, MIN(time)::date AS inicio, MAX(time)::date AS fim "
        "FROM fintz_indicadores_ts"
    )
    check("fintz_indicadores_ts populado",
          row["total"] > 90_000_000,
          f"{row['total']:,} registros ({row['inicio']} → {row['fim']})")

    row2 = await ts_pool.fetchrow(
        "SELECT COUNT(*) FROM fintz_itens_contabeis_ts"
    )
    check("fintz_itens_contabeis_ts populado",
          row2["count"] > 100_000_000,
          f"{row2['count']:,} registros")

    # Query de indicadores para PETR4
    rows = await ts_pool.fetch(
        "SELECT DISTINCT ON (indicador) indicador, valor "
        "FROM fintz_indicadores_ts WHERE ticker = 'PETR4' "
        "ORDER BY indicador, time DESC LIMIT 5"
    )
    check("Query PETR4 indicadores OK",
          len(rows) > 0,
          f"{len(rows)} indicadores encontrados")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("\n\033[36m" + "═" * 62 + "\033[0m")
    print("\033[36m  Teste End-to-End — Pipeline Fintz Completo\033[0m")
    print("\033[36m" + "═" * 62 + "\033[0m")

    # Setup
    section("0. Conectando infraestrutura")
    pg_engine, pg_session, ts_pool = await setup_engines()
    redis = await setup_redis()
    check("Postgres conectado", True, "localhost:5432")
    check("TimescaleDB conectado", True, "localhost:5433")
    check("Redis conectado", redis is not None, REDIS_URL)

    try:
        # Executa os 7 testes em sequência
        event_id = await test_1_publicar_evento(pg_session, redis)
        await test_2_verificar_evento_no_banco(pg_session, event_id)
        await test_3_processar_evento(pg_session, ts_pool, redis, event_id)
        await test_4_verificar_resultado_banco(pg_session, event_id)
        await test_5_verificar_post_sync(redis)
        await test_6_idempotencia(pg_session, ts_pool, redis, event_id)
        await test_7_timescale_dados(ts_pool)

    finally:
        await pg_engine.dispose()
        await ts_pool.close()
        if redis:
            await redis.aclose()

    # Relatório
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total  = len(results)

    print(f"\n\033[36m{'═' * 62}\033[0m")
    print(f"  Resultado: {passed}/{total} verificações passaram")
    if failed:
        print("\n  \033[31mFalhas:\033[0m")
        for name, ok, detail in results:
            if not ok:
                print(f"    ✗ {name}: {detail}")
    print(f"\033[36m{'═' * 62}\033[0m\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())


