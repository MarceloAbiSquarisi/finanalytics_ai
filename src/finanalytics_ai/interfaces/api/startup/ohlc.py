"""startup/ohlc.py — OHLC services, Tape Service e servicos de dominio."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

log = structlog.get_logger(__name__)


async def init_ohlc_services(app, timescale_ok: bool) -> asyncio.Task | None:
    ohlc_daily_task = None
    try:
        from finanalytics_ai.application.services.ohlc_1m_service import OHLC1mService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory

        if timescale_ok:
            from finanalytics_ai.infrastructure.timescale.ohlc_ts_repo import (
                TimescaleOHLCRepository,
            )
            from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool

            pool = await get_timescale_pool()
            ts_repo = TimescaleOHLCRepository(pool)
            app.state.ohlc_1m_service = OHLC1mService(get_session_factory(), brapi_client=None)
        else:
            app.state.ohlc_1m_service = OHLC1mService(get_session_factory(), brapi_client=None)
            log.warning("timescale.unavailable — OHLC endpoints retornam 503")

        log.info("ohlc_1m_service.ready")

        async def _daily():
            while True:
                await asyncio.sleep(3600)
                try:
                    await app.state.ohlc_1m_service.update_daily()
                except Exception as e:
                    log.warning("ohlc.daily_update.failed", error=str(e))

        ohlc_daily_task = asyncio.create_task(_daily())
    except Exception as exc:
        log.warning("ohlc_1m_service.FAILED", error=str(exc))
    return ohlc_daily_task


async def init_tape_service(app, settings) -> None:
    try:
        from finanalytics_ai.application.services.tape_service import TapeService

        redis_url = str(settings.redis_url) if settings.redis_url else "redis://localhost:6379/0"
        svc = TapeService()
        app.state.tape_service = svc
        svc.start_redis_consumer()
        log.info("tape_service.ready")
        log.info("tape_service.redis_consumer_launched", redis_url=redis_url)
    except Exception as exc:
        log.warning("tape_service.FAILED", error=str(exc))


async def init_domain_services(app, market_client: Any) -> None:
    import importlib

    from finanalytics_ai.infrastructure.database.connection import get_session_factory

    sf = get_session_factory()
    _map = {
        "var_service": ("finanalytics_ai.application.services.var_service", "VaRService"),
        "sentiment_service": (
            "finanalytics_ai.application.services.sentiment_service",
            "SentimentService",
        ),
        "options_service": (
            "finanalytics_ai.application.services.options_service",
            "OptionsService",
        ),
        "ranking_service": (
            "finanalytics_ai.application.services.ranking_service",
            "RankingService",
        ),
        "indicator_alert_service": (
            "finanalytics_ai.application.services.indicator_alert_service",
            "IndicatorAlertService",
        ),
        "fintz_screener_service": (
            "finanalytics_ai.application.services.fintz_screener_service",
            "FintzScreenerService",
        ),
        "intraday_setup_service": (
            "finanalytics_ai.application.services.intraday_setup_service",
            "IntradaySetupService",
        ),
    }
    for attr, (mod_path, cls_name) in _map.items():
        try:
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            try:
                svc = cls(market_client, sf) if market_client else cls(sf)
            except TypeError:
                try:
                    svc = cls(sf)
                except TypeError:
                    svc = cls()
            setattr(app.state, attr, svc)
            log.info(f"{attr}.ready")
        except Exception as exc:
            log.warning(f"{attr}.FAILED", error=str(exc))


async def shutdown_ohlc(task: asyncio.Task | None) -> None:
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
