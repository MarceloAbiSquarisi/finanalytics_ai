"""startup/market_data.py — Market data client e servicos dependentes."""
from __future__ import annotations
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_market_data(app, settings) -> Any:
    from finanalytics_ai.application.services.anomaly_service import AnomalyService
    from finanalytics_ai.application.services.backtest_service import BacktestService
    from finanalytics_ai.application.services.correlation_service import CorrelationService
    from finanalytics_ai.application.services.multi_ticker_service import MultiTickerService
    from finanalytics_ai.application.services.optimizer_service import OptimizerService
    from finanalytics_ai.application.services.screener_service import ScreenerService
    from finanalytics_ai.application.services.walkforward_service import WalkForwardService

    market_client = None
    try:
        from finanalytics_ai.infrastructure.adapters.market_data_client import create_market_data_client
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        brapi_key = getattr(settings, "brapi_api_key", None)
        market_client = create_market_data_client(
            brapi_key=str(brapi_key) if brapi_key else None,
            session_factory=get_session_factory(),
        )
        log.info("market_data_client.composite.ready")
    except Exception as exc:
        log.warning("market_data_client.composite.FAILED", error=str(exc))

    if market_client is None:
        try:
            from finanalytics_ai.infrastructure.adapters.market_data_client import create_cached_market_data_client
            from finanalytics_ai.infrastructure.database.connection import get_session_factory
            market_client = create_cached_market_data_client(None, get_session_factory())
            log.info("market_data_client.fintz_fallback.ready")
        except Exception as exc:
            log.warning("market_data_client.ALL.FAILED", error=str(exc))
            return None

    app.state.market_client        = market_client
    app.state.backtest_service     = BacktestService(market_client)
    app.state.optimizer_service    = OptimizerService(market_client)
    app.state.walkforward_service  = WalkForwardService(market_client)
    app.state.multi_ticker_service = MultiTickerService(market_client)
    app.state.correlation_service  = CorrelationService(market_client)
    app.state.screener_service     = ScreenerService(market_client)  # type: ignore[arg-type]
    app.state.anomaly_service      = AnomalyService(market_client)
    log.info("market_data_services.ready")
    return market_client
