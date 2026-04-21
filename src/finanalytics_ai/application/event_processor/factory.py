"""
Factory de injecao de dependencia.
U6: adiciona tracing (opcional, default NullTracing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from finanalytics_ai.application.event_processor.config import EventProcessorConfig
from finanalytics_ai.application.event_processor.service import EventProcessorService
from finanalytics_ai.application.event_processor.tracing import NullTracing

if TYPE_CHECKING:
    from finanalytics_ai.application.event_processor.ports import (
        EventRepository,
        IdempotencyStore,
        ObservabilityPort,
    )
from finanalytics_ai.application.event_processor.tracing import TracingPort
from finanalytics_ai.domain.events.rules import BusinessRule


def create_event_processor_service(
    *,
    repository: EventRepository,
    idempotency_store: IdempotencyStore,
    rules: list[BusinessRule],
    observability: ObservabilityPort,
    tracing: TracingPort | None = None,
    config: EventProcessorConfig | None = None,
) -> EventProcessorService:
    """
    Monta o EventProcessorService com todas as dependencias.

    tracing: opcional. Se None, usa NullTracing (zero overhead).
    Em producao: passar OtelTracing(tracer_name="finanalytics.event_processor").
    """
    cfg = config or EventProcessorConfig()

    return EventProcessorService(
        repository=repository,
        idempotency_store=idempotency_store,
        rules=rules,
        observability=observability,
        tracing=tracing or NullTracing(),
        idempotency_ttl=cfg.idempotency_ttl,
        idempotency_prefix=cfg.idempotency_key_prefix,
        max_retries=cfg.max_retries,
    )
