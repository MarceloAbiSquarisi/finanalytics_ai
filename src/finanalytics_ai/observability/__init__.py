# observability/__init__.py
from __future__ import annotations

try:
    from finanalytics_ai.metrics import (
        alerts_active,
        alerts_triggered_total,
        anomaly_detections_total,
        anomaly_scans_total,
        anomaly_tickers_scanned,
        backtest_duration_seconds,
        backtest_runs_total,
        brapi_request_duration_seconds,
        brapi_requests_total,
        build_info,
        correlation_runs_total,
        event_processing_duration_seconds,
        events_processed_total,
        fintz_rows_upserted_total,
        fintz_sync_attempts_total,
        fintz_sync_errors_total,
        fintz_sync_skips_total,
        fintz_sync_success_total,
        handler_duration_seconds,
        handler_events_total,
        http_request_duration_seconds,
        http_requests_in_flight,
        http_requests_total,
        kafka_messages_consumed_total,
        market_data_requests_total,
        optimizer_runs_total,
        portfolio_operations_total,
        price_producer_polls_total,
        price_producer_tickers_updated,
        screener_pass_rate,
        screener_runs_total,
        screener_stocks_passed_total,
        screener_stocks_scanned_total,
    )
except Exception:
    pass

from finanalytics_ai.observability.logging import configure_logging, get_logger
from finanalytics_ai.observability.metrics import NoOpObservability, PrometheusObservability

try:
    from finanalytics_ai.observability_legacy import setup_metrics, setup_tracing
except Exception:
    try:
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "observability_legacy",
            __file__.replace("__init__.py", "").rstrip("/\\") + "/../observability.py",
        )
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
        setup_metrics = getattr(_mod, "setup_metrics", lambda *a, **kw: None)
        setup_tracing = getattr(_mod, "setup_tracing", lambda *a, **kw: None)
    except Exception:

        def setup_metrics(*a, **kw):
            pass

        def setup_tracing(*a, **kw):
            return None
