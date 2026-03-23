# observability/__init__.py
# Re-exporta metricas de metrics.py e utilitarios de logging/tracing
from __future__ import annotations

try:
    from finanalytics_ai.metrics import (
        events_processed_total,
        event_processing_duration_seconds,
        market_data_requests_total,
        portfolio_operations_total,
        handler_events_total,
        handler_duration_seconds,
        fintz_sync_attempts_total,
        fintz_sync_success_total,
        fintz_sync_skips_total,
        fintz_sync_errors_total,
        fintz_rows_upserted_total,
    )
except ImportError:
    events_processed_total = None
    event_processing_duration_seconds = None
    market_data_requests_total = None
    portfolio_operations_total = None
    handler_events_total = None
    handler_duration_seconds = None
    fintz_sync_attempts_total = None
    fintz_sync_success_total = None
    fintz_sync_skips_total = None
    fintz_sync_errors_total = None
    fintz_rows_upserted_total = None

from finanalytics_ai.observability.logging import configure_logging, get_logger
from finanalytics_ai.observability.metrics import NoOpObservability, PrometheusObservability

__all__ = [
    "configure_logging", "get_logger",
    "NoOpObservability", "PrometheusObservability",
    "market_data_requests_total", "handler_events_total", "handler_duration_seconds",
    "fintz_sync_attempts_total", "fintz_sync_success_total", "fintz_sync_skips_total",
    "fintz_sync_errors_total", "fintz_rows_upserted_total",
]

try:
    from finanalytics_ai.observability_legacy import setup_metrics, setup_tracing
except ImportError:
    try:
        import sys, importlib.util
        spec = importlib.util.spec_from_file_location(
            "observability_legacy",
            __file__.replace("__init__.py", "").rstrip("/\\") + "/../observability.py"
        )
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        setup_metrics = getattr(_mod, "setup_metrics", lambda *a, **kw: None)
        setup_tracing = getattr(_mod, "setup_tracing", lambda *a, **kw: None)
    except Exception:
        def setup_metrics(*a, **kw): pass
        def setup_tracing(*a, **kw): return None
