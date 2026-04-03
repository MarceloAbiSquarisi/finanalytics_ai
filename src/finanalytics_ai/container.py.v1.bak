"""
Container de injeção de dependências — wiring manual.

Dois bancos:
  - Postgres     → OLTP (events, portfolios, alerts, users)
  - TimescaleDB  → séries temporais (ohlc, cotacoes_ts, indicadores_ts)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from finanalytics_ai.application.rules.fintz_sync_rule import FintzSyncCompletedRule
from finanalytics_ai.application.rules.fintz_sync_failed_rule import FintzSyncFailedRule
from finanalytics_ai.application.services.event_processor import EventProcessor
from finanalytics_ai.config import Settings
from finanalytics_ai.infrastructure.database.repositories.event_repository import (
    PostgresEventRepository,
)
from finanalytics_ai.observability.logging import configure_logging
from finanalytics_ai.observability.metrics import NoOpObservability, PrometheusObservability


def build_engine(settings: Settings) -> AsyncEngine:
    """Engine OLTP principal (finanalytics DB)."""
    return create_async_engine(
        str(settings.database_url),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def build_timescale_engine(settings: Settings) -> AsyncEngine | None:
    """Engine TimescaleDB (market_data DB). None se TIMESCALE_URL não configurado."""
    if not settings.timescale_enabled:
        return None
    return create_async_engine(
        str(settings.timescale_url),
        pool_size=settings.timescale_pool_size,
        max_overflow=settings.timescale_max_overflow,
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"statement_cache_size": 0},
    )


def build_timescale_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def build_observability(
    settings: Settings,
) -> PrometheusObservability | NoOpObservability:
    if settings.metrics_enabled:
        return PrometheusObservability(settings)
    return NoOpObservability()


def build_event_processor(session: AsyncSession, settings: Settings) -> EventProcessor:
    repository = PostgresEventRepository(session)
    observability = build_observability(settings)
    rules = [
        FintzSyncCompletedRule(error_rate_threshold=0.10),
        FintzSyncFailedRule(),
    ]
    return EventProcessor(
        repository=repository,
        rules=rules,
        observability=observability,
        settings=settings,
    )


def bootstrap(settings: Settings) -> None:
    configure_logging(settings)


# ── TimescaleWriter factory ───────────────────────────────────────────────────

def build_timescale_writer(
    settings: Settings,
) -> "NoOpTimescaleWriter | PgTimescaleWriter":
    """
    Retorna PgTimescaleWriter se TIMESCALE_URL configurado,
    NoOpTimescaleWriter caso contrário.

    Permite rodar o sistema sem TimescaleDB em dev/CI.
    """
    from finanalytics_ai.infrastructure.database.repositories.timescale_writer import (
        NoOpTimescaleWriter, PgTimescaleWriter,
    )
    if settings.timescale_enabled:
        return PgTimescaleWriter(str(settings.timescale_url))
    return NoOpTimescaleWriter()


# -- PostSyncOrchestrator factory (Sprint H) ----------------------------------

def build_post_sync_orchestrator(settings, ts_pool=None, cache=None, alert_service=None):
    if not getattr(settings, 'timescale_enabled', False) or ts_pool is None:
        return None
    try:
        from finanalytics_ai.application.rules.fintz_post_sync_rule import PostSyncOrchestrator
        from finanalytics_ai.infrastructure.timescale.fintz_repo import TimescaleFintzRepository
        from finanalytics_ai.infrastructure.cache.backend import InMemoryCache
        return PostSyncOrchestrator(
            ts_repo=TimescaleFintzRepository(ts_pool),
            cache=cache or InMemoryCache(),
            alert_service=alert_service,
        )
    except Exception:
        return None