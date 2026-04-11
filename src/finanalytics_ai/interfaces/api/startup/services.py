"""startup/services.py — Servicos de dominio."""
from __future__ import annotations
from typing import Any
import structlog

log = structlog.get_logger(__name__)


async def init_alert_service(app) -> Any:
    from finanalytics_ai.application.services.alert_service import AlertService
    from finanalytics_ai.infrastructure.notifications import get_notification_bus
    from finanalytics_ai.infrastructure.database.connection import get_session
    bus = get_notification_bus()
    svc = AlertService(session_factory=get_session, notification_bus=bus)
    app.state.alert_service = svc
    log.info("alert_service.ready")
    return svc


async def init_account_service(app) -> Any:
    try:
        from finanalytics_ai.application.services.account_service import AccountService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory as _gsf
        from finanalytics_ai.infrastructure.database.repositories.sql_account_repo import TradingAccountModel  # noqa: F401
        svc = AccountService(_gsf())
        app.state.account_service = svc
        log.info("account_service.ready")
        return svc
    except Exception as exc:
        log.warning("account_service.FAILED", error=str(exc))
        return None


async def init_watchlist(app) -> None:
    try:
        from finanalytics_ai.infrastructure.database.connection import Base, get_engine
        from finanalytics_ai.infrastructure.database.repositories.ohlc_repo import OHLCBarModel, OHLCCacheMetaModel  # noqa: F401
        from finanalytics_ai.infrastructure.database.repositories.rf_repo import RFHoldingModel, RFPortfolioModel  # noqa: F401
        from finanalytics_ai.infrastructure.database.repositories.user_repo import UserModel  # noqa: F401
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("watchlist_tables.ok")
    except Exception as exc:
        log.error("watchlist_tables.FAILED", error=str(exc))


async def init_diario(app) -> None:
    try:
        from finanalytics_ai.infrastructure.database.repositories.diario_repo import DiarioRepository
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        app.state.diario_repo = DiarioRepository(get_session_factory())
        log.info("diario_repo.ready")
    except Exception as exc:
        log.warning("diario_repo.FAILED", error=str(exc))


async def init_fundamental_analysis(app, market_client: Any) -> None:
    try:
        from finanalytics_ai.application.services.fundamental_analysis_service import FundamentalAnalysisService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        from finanalytics_ai.infrastructure.market_data.fintz.repository import FintzRepository
        fintz = FintzRepository(get_session_factory())
        app.state.fintz_ts_repo = fintz
        if market_client is not None:
            brapi = getattr(market_client, "brapi_client", None)
            app.state.fundamental_analysis_service = FundamentalAnalysisService(fintz, brapi)
            log.info("fundamental_analysis.ready")
        else:
            log.warning("fundamental_analysis.skipped", reason="market_client ausente")
    except Exception as exc:
        log.warning("fundamental_analysis.FAILED", error=str(exc))

