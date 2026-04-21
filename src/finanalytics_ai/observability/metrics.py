"""
Adapter de métricas Prometheus + hook de tracing OpenTelemetry.

Implementa ObservabilityPort do domínio.

Decisão de design:
    Usamos uma classe concreta em vez de funções globais porque:
    1. Permite injeção (troca por NoOpObservability em testes).
    2. Inicialização lazy — não registra métricas se METRICS_ENABLED=false.
    3. Possível extensão para múltiplos backends.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import time

from finanalytics_ai.config import Settings

try:
    from prometheus_client import Counter, Histogram, start_http_server

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    PROMETHEUS_AVAILABLE = False


class PrometheusObservability:
    """Implementação concreta do ObservabilityPort usando Prometheus."""

    def __init__(self, settings: Settings) -> None:
        self._enabled = settings.metrics_enabled and PROMETHEUS_AVAILABLE

        if not self._enabled:
            return

        self._events_total = Counter(
            "finanalytics_events_total",
            "Total de eventos processados",
            ["event_type", "status"],
        )
        self._processing_duration = Histogram(
            "finanalytics_event_processing_duration_seconds",
            "Duração do processamento de eventos",
            ["event_type"],
            buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0),
        )
        self._retries_total = Counter(
            "finanalytics_event_retries_total",
            "Total de retentativas de eventos",
            ["event_type", "attempt"],
        )

    def record_event_processed(self, event_type: str, status: str) -> None:
        if self._enabled:
            self._events_total.labels(event_type=event_type, status=status).inc()

    def record_processing_duration(self, event_type: str, duration_s: float) -> None:
        if self._enabled:
            self._processing_duration.labels(event_type=event_type).observe(duration_s)

    def record_retry(self, event_type: str, attempt: int) -> None:
        if self._enabled:
            self._retries_total.labels(event_type=event_type, attempt=str(attempt)).inc()


class NoOpObservability:
    """Implementação nula para testes e ambientes sem Prometheus.

    Satisfaz ObservabilityPort sem efeitos colaterais.
    """

    def record_event_processed(self, event_type: str, status: str) -> None:
        pass

    def record_processing_duration(self, event_type: str, duration_s: float) -> None:
        pass

    def record_retry(self, event_type: str, attempt: int) -> None:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Tracing hook (OpenTelemetry-ready)
# ──────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def trace_span(name: str, **attributes: str) -> AsyncGenerator[None, None]:
    """Context manager de tracing.

    Hoje é um no-op; quando TRACING_ENABLED=true e opentelemetry-sdk instalado,
    troca a implementação sem mudar os call sites.

    Uso:
        async with trace_span("process_event", event_id=str(event.id)):
            await rule.apply(event)
    """
    # TODO: injetar tracer real quando opentelemetry-sdk disponível
    _ = name, attributes
    start = time.perf_counter()
    try:
        yield
    finally:
        _ = time.perf_counter() - start  # reservado para span duration
