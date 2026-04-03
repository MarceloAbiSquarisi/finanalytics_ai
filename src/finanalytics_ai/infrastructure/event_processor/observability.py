"""
Implementacoes do ObservabilityPort.

Tres implementacoes:
1. NullObservability: no-op para testes
2. PrometheusObservability: metricas Prometheus
3. CompositeObservability: combina multiplos backends

Decisao: implementacoes separadas + Protocol para composicao sem heranca.
"""
from __future__ import annotations

from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)


class _ObservabilityBackend(Protocol):
    """
    Protocol interno para backends do CompositeObservability.
    Garante que o mypy valide cada backend sem precisar de type: ignore.
    """
    def record_processing_time(self, event_type: str, duration_ms: float) -> None: ...
    def record_event_status(self, event_type: str, status: str) -> None: ...
    def record_retry(self, event_type: str, retry_count: int) -> None: ...


class NullObservability:
    """Implementacao no-op -- para testes e desenvolvimento."""

    def record_processing_time(self, event_type: str, duration_ms: float) -> None:
        pass

    def record_event_status(self, event_type: str, status: str) -> None:
        pass

    def record_retry(self, event_type: str, retry_count: int) -> None:
        pass


class LoggingObservability:
    """Observabilidade via structured logging."""

    def record_processing_time(self, event_type: str, duration_ms: float) -> None:
        logger.info("metric.processing_time", event_type=event_type, duration_ms=duration_ms)

    def record_event_status(self, event_type: str, status: str) -> None:
        logger.info("metric.event_status", event_type=event_type, status=status)

    def record_retry(self, event_type: str, retry_count: int) -> None:
        logger.warning("metric.retry", event_type=event_type, retry_count=retry_count)


class PrometheusObservability:
    """
    Metricas Prometheus.
    Histograma para processing_time captura distribuicao de latencia (p50/p95/p99).
    """

    def __init__(self) -> None:
        try:
            from prometheus_client import Counter, Histogram

            self._processing_time = Histogram(
                "event_processing_duration_ms",
                "Tempo de processamento de eventos em ms",
                ["event_type"],
                buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000],
            )
            self._event_status = Counter(
                "events_processed_total",
                "Total de eventos processados por status",
                ["event_type", "status"],
            )
            self._retries = Counter(
                "event_retries_total",
                "Total de retries por tipo de evento",
                ["event_type"],
            )
            self._available = True
        except ImportError:
            logger.warning("prometheus_client nao instalado -- usando NullObservability")
            self._null = NullObservability()
            self._available = False

    def record_processing_time(self, event_type: str, duration_ms: float) -> None:
        if self._available:
            self._processing_time.labels(event_type=event_type).observe(duration_ms)
        else:
            self._null.record_processing_time(event_type, duration_ms)

    def record_event_status(self, event_type: str, status: str) -> None:
        if self._available:
            self._event_status.labels(event_type=event_type, status=status).inc()
        else:
            self._null.record_event_status(event_type, status)

    def record_retry(self, event_type: str, retry_count: int) -> None:
        if self._available:
            self._retries.labels(event_type=event_type).inc()
        else:
            self._null.record_retry(event_type, retry_count)


class CompositeObservability:
    """
    Composicao de multiplos backends.
    Uso: CompositeObservability([PrometheusObservability(), LoggingObservability()])

    Usa _ObservabilityBackend Protocol para garantir type-safety sem type: ignore.
    """

    def __init__(self, backends: list[_ObservabilityBackend]) -> None:
        self._backends = backends

    def record_processing_time(self, event_type: str, duration_ms: float) -> None:
        for b in self._backends:
            b.record_processing_time(event_type, duration_ms)

    def record_event_status(self, event_type: str, status: str) -> None:
        for b in self._backends:
            b.record_event_status(event_type, status)

    def record_retry(self, event_type: str, retry_count: int) -> None:
        for b in self._backends:
            b.record_retry(event_type, retry_count)

class NoOpObservability:
    """Observabilidade no-op para desenvolvimento sem Prometheus configurado."""

    def record_processing_time(self, event_type: str, duration_ms: float) -> None:
        pass

    def record_event_status(self, event_type: str, status: str) -> None:
        pass

    def record_retry(self, event_type: str, retry_count: int) -> None:
        pass