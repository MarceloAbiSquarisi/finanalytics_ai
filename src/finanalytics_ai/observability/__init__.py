# Métricas re-exportadas para compatibilidade com código legado
from __future__ import annotations

try:
    from finanalytics_ai.metrics import (
        market_data_requests_total,
        handler_events_total,
        fintz_sync_attempts_total,
        fintz_sync_success_total,
        fintz_sync_skips_total,
        fintz_sync_errors_total,
        fintz_rows_upserted_total,
    )
except ImportError:
    market_data_requests_total = None  # type: ignore[assignment]
    handler_events_total = None        # type: ignore[assignment]
    fintz_sync_attempts_total = None   # type: ignore[assignment]
    fintz_sync_success_total = None    # type: ignore[assignment]
    fintz_sync_skips_total = None      # type: ignore[assignment]
    fintz_sync_errors_total = None     # type: ignore[assignment]
    fintz_rows_upserted_total = None   # type: ignore[assignment]

from finanalytics_ai.observability.logging import configure_logging, get_logger
from finanalytics_ai.observability.metrics import NoOpObservability, PrometheusObservability

__all__ = [
    "configure_logging",
    "get_logger",
    "NoOpObservability",
    "PrometheusObservability",
    "market_data_requests_total",
    "handler_events_total",
]
