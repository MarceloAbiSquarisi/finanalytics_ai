"""
finanalytics_ai.observability
──────────────────────────────
Hooks de observabilidade: tracing (OpenTelemetry) + metrics (Prometheus).

Design decision: Observabilidade como camada transversal injetável.
Nenhum módulo de domínio ou aplicação importa diretamente este módulo —
eles recebem um `Tracer` / `Counter` via DI, ou usam decorators.

Isso garante que o domínio permaneça puro e testável sem infra de OTel.

Pattern: Context manager para spans manuais, decorator para funções.
"""

from __future__ import annotations

import functools
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import structlog
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from prometheus_client import Counter, Histogram, start_http_server

from finanalytics_ai.config import get_settings

logger = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ── Metrics (Prometheus) ──────────────────────────────────────────────────────

events_processed_total = Counter(
    name="finanalytics_events_processed_total",
    documentation="Total de eventos de mercado processados",
    labelnames=["event_type", "status"],
)

event_processing_duration_seconds = Histogram(
    name="finanalytics_event_processing_duration_seconds",
    documentation="Duração do processamento de eventos em segundos",
    labelnames=["event_type"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

market_data_requests_total = Counter(
    name="finanalytics_market_data_requests_total",
    documentation="Total de requisições a APIs de dados de mercado",
    labelnames=["provider", "status"],
)

portfolio_operations_total = Counter(
    name="finanalytics_portfolio_operations_total",
    documentation="Operações em portfólio (buy/sell/rebalance)",
    labelnames=["operation", "asset_class"],
)

handler_events_total = Counter(
    name="finanalytics_handler_events_total",
    documentation="Total de eventos por handler específico",
    labelnames=["handler", "status"],
)

handler_duration_seconds = Histogram(
    name="finanalytics_handler_duration_seconds",
    documentation="Latência de cada handler de evento em segundos",
    labelnames=["handler"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)


# ── Tracing (OpenTelemetry) ───────────────────────────────────────────────────


def setup_tracing() -> trace.Tracer:
    """
    Configura o TracerProvider e retorna um Tracer.

    Em produção, troque ConsoleSpanExporter por OTLPSpanExporter.
    """
    settings = get_settings()

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.1.0",
            "deployment.environment": settings.app_env.value,
        }
    )

    provider = TracerProvider(resource=resource)
    # TODO: Em produção substitua por OTLPSpanExporter
    provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)

    return trace.get_tracer(settings.otel_service_name)


def setup_metrics() -> None:
    """Inicia o servidor HTTP do Prometheus em background."""
    settings = get_settings()
    if settings.metrics_enabled:
        start_http_server(settings.prometheus_port)
        logger.info("metrics.server.started", port=settings.prometheus_port)


@asynccontextmanager
async def trace_span(
    tracer: trace.Tracer,
    span_name: str,
    **attributes: str | int | float | bool,
) -> AsyncGenerator[trace.Span, None]:
    """
    Context manager assíncrono para criar spans manualmente.

    Usage:
        async with trace_span(tracer, "process_order", ticker="PETR4"):
            await do_something()
    """
    with tracer.start_as_current_span(span_name) as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR, str(exc))
            raise


def track_duration(metric: Histogram, label: str) -> Callable[[F], F]:
    """
    Decorator que mede a duração de uma função assíncrona e registra no Histogram.

    Usage:
        @track_duration(event_processing_duration_seconds, "price_update")
        async def process_price_update(event: MarketEvent) -> None: ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                metric.labels(event_type=label).observe(elapsed)

        return wrapper  # type: ignore[return-value]

    return decorator
