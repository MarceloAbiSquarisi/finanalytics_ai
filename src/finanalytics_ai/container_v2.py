"""
Container V2 — injecao de dependencias para o EventProcessorService.

Diferenca do container.py original:
- Usa EventProcessorService (V2) em vez de EventProcessor (V1)
- IdempotencyStore como port separado (Redis em prod, InMemory em dev)
- TracingPort injetado opcionalmente (NullTracing = default)
- build_* sao funcoes puras: sem estado global, faceis de testar

Regra de uso:
    - container_v2.py: event processor pipeline (workers, event routes)
    - container.py: servicos legados (FintzSync, alertas, portfolios)

Os dois containers coexistem durante a migracao incremental.
Quando todos os servicos migrarem para V2, container.py sera removido.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finanalytics_ai.application.event_processor.config import EventProcessorConfig
from finanalytics_ai.application.event_processor.factory import create_event_processor_service
from finanalytics_ai.application.event_processor.rules.price_update import PriceUpdateRule
from finanalytics_ai.application.event_processor.rules.price_validation import PriceValidationRule
from finanalytics_ai.application.event_processor.tracing import NullTracing, OtelTracing
from finanalytics_ai.config import Settings
from finanalytics_ai.infrastructure.event_processor.idempotency import (
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
)
from finanalytics_ai.infrastructure.event_processor.observability import PrometheusObservability
from finanalytics_ai.infrastructure.event_processor.repository import SqlEventRepository
from finanalytics_ai.observability.logging import configure_logging

if TYPE_CHECKING:
    from finanalytics_ai.application.event_processor.service import EventProcessorService
    from finanalytics_ai.application.event_processor.tracing import TracingPort
    from finanalytics_ai.infrastructure.event_processor.idempotency import (
        InMemoryIdempotencyStore,
        RedisIdempotencyStore,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Engine / Session
# ─────────────────────────────────────────────────────────────────────────────


def build_engine_v2(settings: Settings):  # type: ignore[return]
    """Engine principal para o event processor (PostgreSQL)."""

    return create_async_engine(
        str(settings.database_url),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


def build_session_factory_v2(engine) -> async_sessionmaker[AsyncSession]:  # type: ignore[type-arg]
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency Store
# Decisao: Redis em producao, InMemory em desenvolvimento/testes.
# A escolha e feita aqui (na borda), nunca no dominio.
# ─────────────────────────────────────────────────────────────────────────────


def build_idempotency_store(settings: Settings):  # type: ignore[return]
    """
    Seleciona backend de idempotencia baseado na configuracao.

    Se REDIS_URL nao estiver configurado, usa InMemory.
    Em producao, REDIS_URL deve estar sempre configurado.

    Warn: InMemoryIdempotencyStore nao tem TTL real — eventos antigos
    nunca expiram. Usar apenas em dev/testes.
    """
    redis_url = getattr(settings, "redis_url", None)
    if redis_url and str(redis_url) not in ("", "None"):
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                str(redis_url),
                encoding="utf-8",
                decode_responses=True,
            )
            return RedisIdempotencyStore(client)
        except ImportError:
            pass  # redis nao instalado — fallback para InMemory
    return InMemoryIdempotencyStore()


# ─────────────────────────────────────────────────────────────────────────────
# Tracing
# ─────────────────────────────────────────────────────────────────────────────


def build_tracing(settings: Settings) -> TracingPort:
    """
    OtelTracing se TRACING_ENABLED=true, NullTracing caso contrario.
    NullTracing tem zero overhead (sem imports OTEL em dev).
    """
    tracing_enabled = getattr(settings, "tracing_enabled", False)
    if tracing_enabled:
        return OtelTracing(tracer_name="finanalytics.event_processor")
    return NullTracing()


# ─────────────────────────────────────────────────────────────────────────────
# Observability
# ─────────────────────────────────────────────────────────────────────────────


def build_observability_v2(settings: Settings):  # type: ignore[return]
    from finanalytics_ai.infrastructure.event_processor.observability import (
        NoOpObservability,
    )

    if settings.metrics_enabled:
        return PrometheusObservability()
    return NoOpObservability()


# ─────────────────────────────────────────────────────────────────────────────
# Rules
# Ordem importa: validacao ANTES de persistencia.
# ─────────────────────────────────────────────────────────────────────────────


def build_rules(timescale_pool=None) -> list:  # type: ignore[type-arg]
    """
    Regras em ordem de aplicacao.

    PriceValidationRule: circuit breaker — rejeita preco fora de banda.
    PriceUpdateRule: persiste preco no TimescaleDB (depende de validacao).

    timescale_pool: pool asyncpg para TimescaleDB (porta 5433).
    Se None, PriceUpdateRule loga warning e retorna sucesso sem persistir.
    """
    return [
        PriceValidationRule(),
        PriceUpdateRule(timescale_pool=timescale_pool),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# EventProcessorService — composicao final
# ─────────────────────────────────────────────────────────────────────────────


def build_event_processor_service_v2(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    timescale_pool=None,  # type: ignore[type-arg]
) -> EventProcessorService:
    """
    Monta o EventProcessorService V2 com todas as dependencias.

    Chamado pelo event_worker em cada ciclo de processamento.
    session_factory e reutilizada — nao criamos engine aqui.
    """
    config = EventProcessorConfig()

    repository = SqlEventRepository(session_factory)
    idempotency = build_idempotency_store(settings)
    observability = build_observability_v2(settings)
    tracing = build_tracing(settings)
    rules = build_rules(timescale_pool)

    return create_event_processor_service(
        repository=repository,
        idempotency_store=idempotency,
        rules=rules,
        observability=observability,
        tracing=tracing,
        config=config,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────────────────────


def bootstrap_v2(settings: Settings) -> None:
    """Inicializacao do sistema V2: logging estruturado."""
    configure_logging(settings)
