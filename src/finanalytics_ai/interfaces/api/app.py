"""
FastAPI application factory — Sprint 7: BRAPI Price Producer.

Lifespan startup order:
  1. PostgreSQL
  2. TimescaleDB
  3. NotificationBus + AlertService
  4. Kafka consumer (avalia alertas + persiste ticks)
  5. BrapiPriceProducer (polling BRAPI → Kafka) — apenas se PRODUCER_ENABLED=true
"""

import asyncio

# Routers + flags _XXX_AVAILABLE foram movidos pra startup/routers.py
# em 01/mai/2026 (sessao limpeza profunda). Imports duplicados removidos.
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog

from finanalytics_ai.application.services.account_service import AccountService
from finanalytics_ai.config import get_settings
from finanalytics_ai.exceptions import FinAnalyticsError
from finanalytics_ai.infrastructure.cache.backend import create_cache_backend
from finanalytics_ai.infrastructure.cache.rate_limiter import create_rate_limiter
from finanalytics_ai.infrastructure.database.connection import close_engine, get_engine
from finanalytics_ai.metrics import PrometheusMiddleware, metrics_endpoint
from finanalytics_ai.observability.correlation import CorrelationMiddleware

logger = structlog.get_logger(__name__)

# ── Singletons globais ────────────────────────────────────────────────────────
_kafka_consumer: Any = None
_kafka_task: Any = None
_alert_service: Any = None
_price_producer: Any = None
_producer_task: Any = None
_account_service: AccountService | None = None


def get_account_service() -> AccountService | None:
    return _account_service


def get_kafka_consumer() -> Any:
    return _kafka_consumer


def get_alert_service() -> Any:
    return _alert_service


def get_price_producer() -> Any:
    return _price_producer


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global \
        _kafka_consumer, \
        _kafka_task, \
        _alert_service, \
        _price_producer, \
        _producer_task, \
        _account_service

    settings = get_settings()
    logger.info("api.starting", env=getattr(settings, "env", "production"))

    # ── 1. PostgreSQL ─────────────────────────────────────────────────────────
    get_engine()
    logger.info("postgres.connected")

    # ── Bootstrap: garante que marceloabisquarisi é sempre MASTER ─────────────
    try:
        from finanalytics_ai.infrastructure.database.connection import (
            get_session as _get_bs_session,
        )
        from finanalytics_ai.interfaces.api.routes.admin import run_bootstrap

        async with _get_bs_session() as _bs_session:
            _result = await run_bootstrap(_bs_session)
            logger.info("bootstrap.master", result=_result)
    except Exception as _be:
        logger.warning("bootstrap.FAILED", error=str(_be))

    # ── 0. Cache + Rate Limiter ───────────────────────────────────────────────
    app.state.cache_backend = create_cache_backend(
        str(settings.redis_url) if settings.redis_url else None
    )
    app.state.rate_limiter = create_rate_limiter(
        str(settings.redis_url) if settings.redis_url else None
    )
    logger.info("cache.ready", backend=type(app.state.cache_backend).__name__)
    logger.info("rate_limiter.ready", backend=type(app.state.rate_limiter).__name__)

    # ── 2. TimescaleDB ────────────────────────────────────────────────────────
    timescale_ok = False
    try:
        from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool

        await get_timescale_pool()
        timescale_ok = True
        logger.info("timescale.connected")
    except Exception as exc:
        logger.warning("timescale.unavailable", error=str(exc))

    # ── 3. NotificationBus + AlertService ─────────────────────────────────────
    from finanalytics_ai.application.services.alert_service import AlertService
    from finanalytics_ai.infrastructure.database.connection import get_session
    from finanalytics_ai.infrastructure.notifications import get_notification_bus

    bus = get_notification_bus()
    _alert_service = AlertService(session_factory=get_session, notification_bus=bus)
    logger.info("alert_service.ready")

    # ── Pushover subscriber (Sprint Fix Alerts D, 21/abr) ─────────────────────
    # Background task que consome NotificationBus e encaminha alertas
    # de indicador para Pushover. Nao-blocking: retorna None se
    # PUSHOVER_USER_KEY/APP_TOKEN ausentes.
    app.state.pushover_task = None
    try:
        from finanalytics_ai.infrastructure.notifications.pushover import (
            subscribe_to_bus as _subscribe_pushover,
        )

        app.state.pushover_task = _subscribe_pushover(bus)
        if app.state.pushover_task:
            logger.info("pushover.subscriber.scheduled")
    except Exception as _pe:
        logger.warning("pushover.subscriber.FAILED", error=str(_pe))

    # ── AccountService (DEPRECATED — Unificacao U3 24/abr) ───────────────────
    # trading_accounts foi unificado em investment_accounts. O agent.py proxy
    # agora consulta WalletRepository.get_dll_active() em vez de AccountService.
    # Bloco desativado para permitir DROP TABLE trading_accounts.
    _account_service = None
    logger.info("account_service.deprecated use_investment_accounts_instead")

    # ── 4. Kafka consumer ─────────────────────────────────────────────────────
    kafka_ok = False
    try:
        from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventConsumer

        consumer = KafkaMarketEventConsumer()
        await consumer.start()
        _kafka_consumer = consumer

        async def _handle_event(event: Any) -> None:
            from finanalytics_ai.domain.entities.event import EventType, MarketEvent

            if not isinstance(event, MarketEvent):
                return
            logger.debug("kafka.event.received", type=event.event_type, ticker=event.ticker)

            if event.event_type == EventType.PRICE_UPDATE and _alert_service:
                price = event.payload.get("price")
                if price:
                    triggered = await _alert_service.evaluate_price(event.ticker, float(price))
                    if triggered:
                        logger.info("alerts.triggered", ticker=event.ticker, count=triggered)

            if event.event_type == EventType.PRICE_UPDATE and timescale_ok:
                try:
                    from finanalytics_ai.infrastructure.timescale.repository import (
                        TimescalePriceTickRepository,
                        get_timescale_pool,
                    )

                    pool = await get_timescale_pool()
                    repo = TimescalePriceTickRepository(pool)
                    await repo.save_tick(
                        ticker=event.ticker,
                        price=float(event.payload.get("price", 0)),
                        change_pct=event.payload.get("change_pct"),
                        volume=event.payload.get("volume"),
                        source=event.source,
                    )
                except Exception as e:
                    logger.warning("timescale.tick.save_failed", error=str(e))

        _kafka_task = asyncio.create_task(consumer.consume_loop(_handle_event))
        kafka_ok = True
        logger.info("kafka.consumer.running", group=settings.kafka_consumer_group)
    except Exception as exc:
        logger.warning("kafka.unavailable", error=str(exc))

    # ── 5. BacktestService + OptimizerService + WalkForwardService ───────────
    from finanalytics_ai.application.services.anomaly_service import AnomalyService
    from finanalytics_ai.application.services.backtest_service import BacktestService
    from finanalytics_ai.application.services.correlation_service import CorrelationService
    from finanalytics_ai.application.services.multi_ticker_service import MultiTickerService
    from finanalytics_ai.application.services.optimizer_service import OptimizerService
    from finanalytics_ai.application.services.walkforward_service import WalkForwardService
    from finanalytics_ai.infrastructure.adapters.market_data_client import (
        create_cached_market_data_client,
    )
    from finanalytics_ai.infrastructure.database.connection import get_session_factory
    from finanalytics_ai.infrastructure.database.repositories.backtest_repo import (
        BacktestResultRepository,
    )

    backtest_result_repo = BacktestResultRepository(get_session_factory())
    app.state.backtest_result_repo = backtest_result_repo

    if settings.brapi_token:
        from finanalytics_ai.application.services.screener_service import ScreenerService

        market_client = create_cached_market_data_client(
            settings.brapi_token, get_session_factory()
        )
        app.state.backtest_service = BacktestService(market_client)
        app.state.optimizer_service = OptimizerService(
            market_client, result_repo=backtest_result_repo
        )
        app.state.walkforward_service = WalkForwardService(
            market_client, result_repo=backtest_result_repo
        )
        app.state.multi_ticker_service = MultiTickerService(market_client)
        app.state.correlation_service = CorrelationService(market_client)
        app.state.screener_service = ScreenerService(market_client)  # type: ignore[arg-type]
        app.state.anomaly_service = AnomalyService(market_client)
        app.state.market_client = market_client  # <-- acesso direto para outras dependências
        logger.info("market_data_client.composite.ready")
    else:
        # Sem token BRAPI: serviços analíticos indisponíveis, mas watchlist
        # funciona com Yahoo Finance como fonte primária
        market_client = create_cached_market_data_client(None, get_session_factory())
        app.state.market_client = market_client

        app.state.backtest_service = BacktestService(market_client)
        app.state.optimizer_service = OptimizerService(
            market_client, result_repo=backtest_result_repo
        )
        app.state.walkforward_service = WalkForwardService(
            market_client, result_repo=backtest_result_repo
        )
        app.state.multi_ticker_service = MultiTickerService(market_client)
        app.state.correlation_service = CorrelationService(market_client)
        app.state.anomaly_service = AnomalyService(market_client)
        logger.info("market_data_client.fintz_fallback.ready")

    # ── 6. WatchlistService: cria tabelas DB ─────────────────────────────────
    # Importa os models ANTES do create_all para registrá-los no metadata.
    # Sem o import, Base.metadata não conhece as tabelas e create_all é no-op.
    from finanalytics_ai.infrastructure.database.connection import Base
    from finanalytics_ai.infrastructure.database.repositories.diario_repo import (
        DiarioModel,  # noqa: F401 — registra trade_journal na metadata
    )
    from finanalytics_ai.infrastructure.database.repositories.ohlc_repo import (  # noqa: F401
        OHLCBarModel,
        OHLCCacheMetaModel,
    )
    from finanalytics_ai.infrastructure.database.repositories.rf_repo import (  # noqa: F401
        Base as RFBase,
        RFHoldingModel,
        RFPortfolioModel,
    )
    from finanalytics_ai.infrastructure.database.repositories.user_repo import (
        UserModel,  # noqa: F401
    )
    from finanalytics_ai.infrastructure.database.repositories.watchlist_repo import (
        WatchlistItemModel,  # noqa: F401
    )

    try:
        async with get_engine().begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, checkfirst=True))
            await conn.run_sync(lambda c: RFBase.metadata.create_all(c, checkfirst=True))
        logger.info("watchlist_tables.ok")
    except Exception as exc:
        logger.error("watchlist_tables.FAILED", error=str(exc))
        logger.info("correlation_service.ready")
        logger.info("screener_service.ready")
        logger.info("anomaly_service.ready")

    # ── OHLC: TimescaleDB + Updater ───────────────────────────────────────────

    # ── OHLC 1m Service (cache + agregacao livre de intervalos) ──────────────
    app.state.ohlc_1m_service = None
    try:
        from finanalytics_ai.application.services.ohlc_1m_service import OHLC1mService as _S1m
        from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient as _BC2
        from finanalytics_ai.infrastructure.database.connection import (
            get_engine as _ge2,
            get_session_factory as _gsf2,
        )
        from finanalytics_ai.infrastructure.database.repositories.ohlc_1m_repo import Base as _B1m

        async with _ge2().begin() as _c2:
            await _c2.run_sync(_B1m.metadata.create_all)
        _bt2 = getattr(get_settings(), "brapi_token", "")
        _bc2 = _BC2(token=_bt2)
        app.state.ohlc_1m_service = _S1m(session_factory=_gsf2(), brapi_client=_bc2)
        logger.info("ohlc_1m_service.ready")
    except Exception as _e1m:
        logger.warning("ohlc_1m_service.FAILED", error=str(_e1m))

    app.state.ohlc_ts_repo = None
    app.state.ohlc_updater = None
    _ohlc_daily_task = None
    try:
        from finanalytics_ai.application.services.ohlc_updater import OHLCUpdaterService
        from finanalytics_ai.infrastructure.timescale.connection import (
            init_ts_pool,
            ts_pool_available,
        )
        from finanalytics_ai.infrastructure.timescale.ohlc_ts_repo import OHLCTimescaleRepo
        from finanalytics_ai.infrastructure.timescale.schema import init_schema

        ts_dsn = str(settings.timescale_url) if settings.timescale_url else ""
        if ts_dsn and await ts_pool_available(ts_dsn):
            ts_pool = await init_ts_pool(ts_dsn, min_size=2, max_size=8)
            await init_schema(ts_pool)
            repo = OHLCTimescaleRepo(ts_pool)
            updater = OHLCUpdaterService(repo, market_client)
            app.state.ohlc_ts_repo = repo
            app.state.ohlc_updater = updater
            # Inicia loop de atualização diária em background
            _ohlc_daily_task = asyncio.create_task(updater.run_daily_loop())
            logger.info("timescale.ohlc.ready")
        else:
            logger.warning("timescale.unavailable — OHLC endpoints retornam 503")
    except Exception as exc:
        logger.warning("timescale.init.FAILED", error=str(exc))

    # -- Ticker Service ------------------------------------------------
    app.state.ticker_service = None
    try:
        from finanalytics_ai.infrastructure.database.connection import (
            get_engine as _get_eng,
            get_session_factory,
        )
        from finanalytics_ai.infrastructure.database.repositories.ticker_repo import (
            Base as TickerBase,
        )
        from finanalytics_ai.infrastructure.database.repositories.ticker_service import (
            TickerService,
        )

        async with _get_eng().begin() as conn:
            await conn.run_sync(lambda c: TickerBase.metadata.create_all(c, checkfirst=True))
        # session_factory tambem em app.state para ticker_routes/dividendos/etc
        # (Sprint Fix UI 22/abr): faltava em recreate, causava 500 em /subscriptions.
        app.state.session_factory = get_session_factory()
        app.state.ticker_service = TickerService(get_session_factory())
        logger.info("ticker_service.ready")
    except Exception as exc:
        logger.warning("ticker_service.FAILED", error=str(exc))

    # -- IntradaySetupService (deteccao de setups em tempo real)
    try:
        from finanalytics_ai.application.services.intraday_setup_service import IntradaySetupService

        _market = getattr(app.state, "market_client", None)
        _bus = getattr(app.state, "notification_bus", None)
        if _market:
            app.state.intraday_setup_service = IntradaySetupService(_market, _bus)
            logger.info("intraday_setup_service.ready")
        else:
            app.state.intraday_setup_service = None
            logger.warning("intraday_setup_service.skipped", reason="market_client ausente")
    except Exception as _ise:
        logger.warning("intraday_setup_service.FAILED", error=str(_ise))
        app.state.intraday_setup_service = None

    # -- TapeService (Tape Reading via ProfitDLL)
    try:
        from finanalytics_ai.application.services.tape_service import TapeService

        tape_svc = TapeService()
        app.state.tape_service = tape_svc
        logger.info("tape_service.ready")
        # Inicia consumer Redis (recebe ticks do profit_market_worker)
        import asyncio as _asyncio

        from finanalytics_ai.config import get_settings as _gs

        _redis_url = _gs().redis_url if hasattr(_gs(), "redis_url") else "redis://redis:6379/0"
        _tape_task = _asyncio.create_task(tape_svc.start_redis_consumer(_redis_url))
        app.state.tape_redis_task = _tape_task
        logger.info("tape_service.redis_consumer_launched", redis_url=_redis_url)
    except Exception as _tse:
        logger.warning("tape_service.FAILED", error=str(_tse))
        app.state.tape_service = None
    # -- VaRService (Value at Risk)
    try:
        from finanalytics_ai.application.services.var_service import VaRService

        _var_market = getattr(app.state, "market_client", None)
        if _var_market:
            app.state.var_service = VaRService(_var_market)
            logger.info("var_service.ready")
        else:
            app.state.var_service = None
            logger.warning("var_service.SKIPPED", reason="market_client nao disponivel")
    except Exception as _vse:
        logger.warning("var_service.FAILED", error=str(_vse))
        app.state.var_service = None
    # -- SentimentService (analise de noticias via Claude Haiku 4.5)
    try:
        from finanalytics_ai.application.services.sentiment_service import SentimentService

        _anthropic_key = getattr(settings, "anthropic_api_key", "") or ""
        if _anthropic_key:
            _redis_client = None
            try:
                from redis.asyncio import from_url as _redis_from_url

                _redis_client = _redis_from_url(str(settings.redis_url))
            except Exception:
                pass
            app.state.sentiment_service = SentimentService(
                api_key=_anthropic_key,
                redis_client=_redis_client,
            )
            logger.info("sentiment_service.ready")
        else:
            logger.warning("sentiment_service.SKIPPED", reason="ANTHROPIC_API_KEY nao configurada")
            app.state.sentiment_service = None
    except Exception as _sse:
        logger.warning("sentiment_service.FAILED", error=str(_sse))
        app.state.sentiment_service = None
    # -- OptionsService (calculadora de opcoes Black-Scholes)
    try:
        from finanalytics_ai.application.services.options_service import OptionsService

        app.state.options_service = OptionsService()
        logger.info("options_service.ready")
    except Exception as _ose:
        logger.warning("options_service.FAILED", error=str(_ose))
        app.state.options_service = None

    # -- RankingService (ranking de acoes por metodologia)
    try:
        from finanalytics_ai.application.services.ranking_service import RankingService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory as _gsf3

        app.state.ranking_service = RankingService(_gsf3())
        logger.info("ranking_service.ready")
    except Exception as _rke:
        logger.warning("ranking_service.FAILED", error=str(_rke))
        app.state.ranking_service = None

    # -- IndicatorAlertService (alertas de indicadores Fintz)
    try:
        from finanalytics_ai.application.services.indicator_alert_service import (
            IndicatorAlertService,
        )
        from finanalytics_ai.infrastructure.database.connection import get_session_factory as _gsf2

        _notification_bus = getattr(app.state, "notification_bus", None)
        app.state.indicator_alert_service = IndicatorAlertService(_gsf2(), _notification_bus)
        logger.info("indicator_alert_service.ready")
    except Exception as _iae:
        logger.warning("indicator_alert_service.FAILED", error=str(_iae))
        app.state.indicator_alert_service = None

    # -- FintzScreenerService (dados locais Fintz)
    try:
        from finanalytics_ai.application.services.fintz_screener_service import FintzScreenerService
        from finanalytics_ai.infrastructure.database.connection import get_session_factory as _gsf

        app.state.fintz_screener_service = FintzScreenerService(_gsf())
        logger.info("fintz_screener_service.ready")
    except Exception as _fse:
        logger.warning("fintz_screener_service.FAILED", error=str(_fse))
        app.state.fintz_screener_service = None

    # ── DiarioRepository ──────────────────────────────────────────────────────

    try:
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        from finanalytics_ai.infrastructure.database.repositories.diario_repo import (
            DiarioRepository,
        )

        app.state.diario_repo = DiarioRepository(get_session_factory())
        logger.info("diario_repo.ready")
    except Exception as exc:
        logger.warning("diario_repo.FAILED", error=str(exc))
        app.state.diario_repo = None

    # ── 6. BRAPI Price Producer ───────────────────────────────────────────────
    producer_ok = False
    if settings.producer_enabled and kafka_ok and settings.brapi_token:
        try:
            from finanalytics_ai.application.services.price_producer import BrapiPriceProducer
            from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
            from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventProducer

            tickers = [t.strip() for t in settings.producer_tickers.split(",") if t.strip()]
            brapi = BrapiClient()
            kprod = KafkaMarketEventProducer()

            _price_producer = BrapiPriceProducer(
                tickers=tickers,
                poll_interval=settings.producer_poll_interval_seconds,
                brapi_client=brapi,
                kafka_producer=kprod,
            )
            await _price_producer.start()
            _producer_task = asyncio.create_task(_price_producer.run())
            producer_ok = True
            logger.info(
                "price_producer.running",
                tickers=tickers,
                interval=settings.producer_poll_interval_seconds,
            )
        except Exception as exc:
            logger.warning("price_producer.unavailable", error=str(exc))
    elif settings.producer_enabled and not settings.brapi_token:
        logger.warning(
            "price_producer.disabled",
            reason="BRAPI_TOKEN não configurado — adicione ao .env para ativar o producer automático",
        )

    # ── Fundamental Analysis Service ──────────────────────────────────────
    try:
        from finanalytics_ai.application.services.fundamental_analysis_service import (
            FundamentalAnalysisService,
        )
        from finanalytics_ai.infrastructure.database.repositories.fintz_repo import FintzRepo

        _fintz_repo = FintzRepo()
        app.state.fintz_ts_repo = _fintz_repo
        _brapi = getattr(app.state, "market_client", None)
        if _brapi:
            app.state.fundamental_analysis_service = FundamentalAnalysisService(_fintz_repo, _brapi)
            logger.info("fundamental_analysis.ready")
        else:
            logger.warning("fundamental_analysis.skipped", reason="market_client ausente")
    except Exception as _exc:
        logger.warning("fundamental_analysis.FAILED", error=str(_exc))

    # ── ML metrics refresh (Sprint Fix Alerts E, 21/abr) ──────────────────────
    # Background task que atualiza Gauges Prometheus (ml_drift_count,
    # ml_snapshot_age_days, ml_signals_by_status) a cada 5min — destrava
    # alert rules de drift/snapshot no Grafana sem precisar pollar JSON.
    app.state.ml_metrics_task = None
    try:
        from finanalytics_ai.application.services.ml_metrics_refresh import (
            refresh_loop as _ml_refresh,
        )

        app.state.ml_metrics_task = asyncio.create_task(_ml_refresh())
        logger.info("ml_metrics_refresh.scheduled")
    except Exception as _mexc:
        logger.warning("ml_metrics_refresh.FAILED", error=str(_mexc))

    # ── B3 market_open gauge (Sprint Pregão Mute, 22/abr) ─────────────────────
    # Atualiza finanalytics_market_open a cada 60s — usado em alert rules
    # market-data com filtro `AND on() finanalytics_market_open == 1`.
    # Cobre feriados B3 (que mute_time_intervals NÃO cobre).
    app.state.market_open_task = None
    try:
        from finanalytics_ai.application.services.market_open_refresh import (
            refresh_loop as _mo_refresh,
        )

        app.state.market_open_task = asyncio.create_task(_mo_refresh())
        logger.info("market_open_refresh.scheduled")
    except Exception as _moe:
        logger.warning("market_open_refresh.FAILED", error=str(_moe))

    # Carrega cache de b3_no_trading_days (dias atipicos B3 sem pregao
    # mesmo nao sendo feriado nacional). Necessario antes do primeiro
    # is_trading_day() — gaps query, backfill scheduling, etc.
    try:
        from finanalytics_ai.infrastructure.database.repositories import (
            backfill_repo as _bfr,
        )
        await _bfr.load_b3_no_trading_days()
        logger.info("b3_no_trading_days.loaded", count=len(_bfr._B3_NO_TRADING_DAYS))
    except Exception as _e:
        logger.warning("b3_no_trading_days.load_failed", error=str(_e))

    logger.info(
        "api.ready", postgres=True, timescale=timescale_ok, kafka=kafka_ok, producer=producer_ok
    )
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _producer_task:
        if _price_producer:
            await _price_producer.stop()
        _producer_task.cancel()
        with suppress(asyncio.CancelledError):
            await _producer_task

    if _kafka_task:
        _kafka_task.cancel()
        with suppress(asyncio.CancelledError):
            await _kafka_task
    if _kafka_consumer:
        with suppress(Exception):
            await _kafka_consumer.stop()
    if timescale_ok:
        try:
            from finanalytics_ai.infrastructure.timescale.repository import close_timescale_pool

            await close_timescale_pool()
        except Exception:
            pass
    # Cancela loop de atualização OHLC
    if _ohlc_daily_task:
        _ohlc_daily_task.cancel()
        with suppress(asyncio.CancelledError):
            await _ohlc_daily_task
    # Cancela ML metrics refresh (Sprint Fix Alerts E)
    _ml_task = getattr(app.state, "ml_metrics_task", None)
    if _ml_task:
        _ml_task.cancel()
        with suppress(asyncio.CancelledError):
            await _ml_task
    # Cancela Pushover subscriber (Sprint Fix Alerts D)
    _push_task = getattr(app.state, "pushover_task", None)
    if _push_task:
        _push_task.cancel()
        with suppress(asyncio.CancelledError):
            await _push_task
    # Cancela market_open refresh (Sprint Pregão Mute)
    _mo_task = getattr(app.state, "market_open_task", None)
    if _mo_task:
        _mo_task.cancel()
        with suppress(asyncio.CancelledError):
            await _mo_task
    # Fecha pool TimescaleDB
    try:
        from finanalytics_ai.infrastructure.timescale.connection import close_ts_pool

        await close_ts_pool()
    except Exception:
        pass
    await close_engine()
    try:
        from finanalytics_ai.infrastructure.database.connection_trading import (
            close_trading_engine,
        )

        await close_trading_engine()
    except Exception:
        pass
    logger.info("api.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinAnalytics AI",
        description="Framework de Análise e Busca de Investimentos",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(CorrelationMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(PrometheusMiddleware)

    app.add_route("/metrics", metrics_endpoint, include_in_schema=False)

    @app.exception_handler(FinAnalyticsError)
    async def finanalytics_error_handler(request: object, exc: FinAnalyticsError) -> JSONResponse:
        logger.warning("api.domain_error", code=exc.code, message=exc.message)
        status_map = {
            "PORTFOLIO_NOT_FOUND": 404,
            "INVALID_TICKER": 422,
            "INSUFFICIENT_FUNDS": 422,
            "INVALID_QUANTITY": 422,
            "DUPLICATE_EVENT": 409,
        }
        return JSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"error": exc.code, "message": exc.message, "context": exc.context},
        )

    # Routers movidos para startup/routers.py em 01/mai/2026.
    from finanalytics_ai.interfaces.api.startup.routers import register_routers

    register_routers(app, logger=logger)

    return app
