# observability/__init__.py
# Re-exporta metricas Prometheus e utilitarios de logging/tracing
from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    events_processed_total = Counter(
        name="finanalytics_events_processed_total",
        documentation="Total de eventos de mercado processados",
        labelnames=["event_type", "status"],
    )

    event_processing_duration_seconds = Histogram(
        name="finanalytics_event_processing_duration_seconds",
        documentation="Duracao do processamento de eventos em segundos",
        labelnames=["event_type"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    )

    market_data_requests_total = Counter(
        name="finanalytics_market_data_requests_total",
        documentation="Total de requisicoes a APIs de dados de mercado",
        labelnames=["provider", "status"],
    )

    portfolio_operations_total = Counter(
        name="finanalytics_portfolio_operations_total",
        documentation="Operacoes em portfolio (buy/sell/rebalance)",
        labelnames=["operation", "asset_class"],
    )

    handler_events_total = Counter(
        name="finanalytics_handler_events_total",
        documentation="Total de eventos por handler especifico",
        labelnames=["handler", "status"],
    )

    handler_duration_seconds = Histogram(
        name="finanalytics_handler_duration_seconds",
        documentation="Latencia de cada handler de evento em segundos",
        labelnames=["handler"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    )

    fintz_sync_attempts_total = Counter(
        name="finanalytics_fintz_sync_attempts_total",
        documentation="Total de tentativas de sync Fintz",
        labelnames=["dataset_type"],
    )

    fintz_sync_success_total = Counter(
        name="finanalytics_fintz_sync_success_total",
        documentation="Total de syncs Fintz bem-sucedidos",
        labelnames=["dataset_type"],
    )

    fintz_sync_skips_total = Counter(
        name="finanalytics_fintz_sync_skips_total",
        documentation="Total de syncs Fintz ignorados (hash identico)",
        labelnames=["dataset_type"],
    )

    fintz_sync_errors_total = Counter(
        name="finanalytics_fintz_sync_errors_total",
        documentation="Total de erros de sync Fintz",
        labelnames=["dataset_type"],
    )

    fintz_rows_upserted_total = Counter(
        name="finanalytics_fintz_rows_upserted_total",
        documentation="Total de linhas upserted pelo sync Fintz",
        labelnames=["dataset_type"],
    )

except ImportError:
    events_processed_total = None  # type: ignore[assignment]
    event_processing_duration_seconds = None  # type: ignore[assignment]
    market_data_requests_total = None  # type: ignore[assignment]
    portfolio_operations_total = None  # type: ignore[assignment]
    handler_events_total = None  # type: ignore[assignment]
    handler_duration_seconds = None  # type: ignore[assignment]
    fintz_sync_attempts_total = None  # type: ignore[assignment]
    fintz_sync_success_total = None  # type: ignore[assignment]
    fintz_sync_skips_total = None  # type: ignore[assignment]
    fintz_sync_errors_total = None  # type: ignore[assignment]
    fintz_rows_upserted_total = None  # type: ignore[assignment]

from finanalytics_ai.observability.logging import configure_logging, get_logger
from finanalytics_ai.observability.metrics import NoOpObservability, PrometheusObservability

__all__ = [
    "configure_logging",
    "get_logger",
    "NoOpObservability",
    "PrometheusObservability",
    "market_data_requests_total",
    "handler_events_total",
    "handler_duration_seconds",
    "fintz_sync_attempts_total",
    "fintz_sync_success_total",
    "fintz_sync_skips_total",
    "fintz_sync_errors_total",
    "fintz_rows_upserted_total",
]
