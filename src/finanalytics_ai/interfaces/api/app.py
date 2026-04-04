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
from contextlib import asynccontextmanager, suppress
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from finanalytics_ai.config import get_settings
from finanalytics_ai.exceptions import FinAnalyticsError
from finanalytics_ai.infrastructure.database.connection import close_engine, get_engine
from finanalytics_ai.interfaces.api.routes import admin as admin_routes
from finanalytics_ai.interfaces.api.routes import admin as admin_routes
from finanalytics_ai.interfaces.api.routes import ml_forecasting as ml_routes
from finanalytics_ai.interfaces.api.routes import marketdata as marketdata_routes
from finanalytics_ai.interfaces.api.routes import fundos as fundos_routes
from finanalytics_ai.interfaces.api.routes import (
    wallet,
    alerts,
    anomaly,
    backtest,
    correlation,
    dashboard,
    fundamental_analysis,
    events,
    fixed_income,
    health,
    performance,
    portfolio,
    producer,
    quotes,
    reports,
    screener,
    watchlist
)

try:
    from finanalytics_ai.interfaces.api.routes import etf as etf_routes

    _ETF_AVAILABLE = True
except ImportError:
    _ETF_AVAILABLE = False

try:
    from finanalytics_ai.interfaces.api.routes import portfolio_optimizer as optimizer_routes

    _OPTIMIZER_AVAILABLE = True
except ImportError:
    _OPTIMIZER_AVAILABLE = False

try:
    from finanalytics_ai.interfaces.api.routes import fund_analysis as fund_analysis_routes

    _FUND_ANALYSIS_AVAILABLE = True
except (ImportError, RuntimeError):
    # RuntimeError quando python-multipart ausente (UploadFile/File dependency)
    _FUND_ANALYSIS_AVAILABLE = False

try:
    from finanalytics_ai.interfaces.api.routes import patrimony as patrimony_routes

    _PATRIMONY_AVAILABLE = True
except ImportError:
    _PATRIMONY_AVAILABLE = False
from finanalytics_ai.infrastructure.cache.backend import create_cache_backend
from finanalytics_ai.infrastructure.cache.rate_limiter import create_rate_limiter
from finanalytics_ai.metrics import PrometheusMiddleware, metrics_endpoint
from collections.abc import AsyncGenerator

logger = structlog.get_logger(__name__)

# ── Singletons globais ────────────────────────────────────────────────────────
_kafka_consumer: Any = None
_kafka_task: Any = None
_alert_service: Any = None
_price_producer: Any = None
_producer_task: Any = None

def get_kafka_consumer() -> Any:
    return _kafka_consumer

def get_alert_service() -> Any:
    return _alert_service

def get_price_producer() -> Any:
    return _price_producer

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _kafka_consumer, _kafka_task, _alert_service, _price_producer, _producer_task

    settings = get_settings()
    logger.info("api.starting", env=getattr(settings, "env", "production"))

    # ── 1. PostgreSQL ─────────────────────────────────────────────────────────
    get_engine()
    logger.info("postgres.connected")

    # ── Bootstrap: garante que marceloabisquarisi é sempre MASTER ─────────────
    try:
        from finanalytics_ai.interfaces.api.routes.admin import run_bootstrap
        from finanalytics_ai.infrastructure.database.connection import get_session as _get_bs_session
        async with _get_bs_session() as _bs_session:
            _result = await run_bootstrap(_bs_session)
            logger.info("bootstrap.master", result=_result)
    except Exception as _be:
        logger.warning("bootstrap.FAILED", error=str(_be))

    # ── Bootstrap: garante que marceloabisquarisi é sempre MASTER ─────────────
    try:
        from finanalytics_ai.interfaces.api.routes.admin import run_bootstrap
        from finanalytics_ai.infrastructure.database.connection import get_session as _get_bs_session
        async with _get_bs_session() as _bs_session:
            _result = await run_bootstrap(_bs_session)
            logger.info("bootstrap.master", result=_result)
    except Exception as _be:
        logger.warning("bootstrap.FAILED", error=str(_be))

    # ── 0. Cache + Rate Limiter ───────────────────────────────────────────────
    app.state.cache_backend = create_cache_backend(str(settings.redis_url) if settings.redis_url else None)
    app.state.rate_limiter = create_rate_limiter(str(settings.redis_url) if settings.redis_url else None)
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
    _alert_service = AlertService(
        session_factory=get_session,
        notification_bus=bus
    )
    logger.info("alert_service.ready")

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
                        get_timescale_pool
                    )

                    pool = await get_timescale_pool()
                    repo = TimescalePriceTickRepository(pool)
                    await repo.save_tick(
                        ticker=event.ticker,
                        price=float(event.payload.get("price", 0)),
                        change_pct=event.payload.get("change_pct"),
                        volume=event.payload.get("volume"),
                        source=event.source
                    )
                except Exception as e:
                    logger.warning("timescale.tick.save_failed", error=str(e))

        _kafka_task = asyncio.create_task(consumer.consume_loop(_handle_event))
        kafka_ok = True
        logger.info("kafka.consumer.running", group=settings.kafka_consumer_group)
    except Exception as exc:
            logger.warning("kafka.unavailable", error=str(exc))

    # ── 5. BacktestService + OptimizerService + WalkForwardService ───────────
    if settings.brapi_token:
        from finanalytics_ai.application.services.anomaly_service import AnomalyService
        from finanalytics_ai.application.services.backtest_service import BacktestService
        from finanalytics_ai.application.services.correlation_service import CorrelationService
        from finanalytics_ai.application.services.multi_ticker_service import MultiTickerService
        from finanalytics_ai.application.services.optimizer_service import OptimizerService
        from finanalytics_ai.application.services.screener_service import ScreenerService
        from finanalytics_ai.application.services.walkforward_service import WalkForwardService
        from finanalytics_ai.infrastructure.adapters.market_data_client import (
            create_cached_market_data_client
        )
        from finanalytics_ai.infrastructure.database.connection import get_session_factory

        market_client = create_cached_market_data_client(settings.brapi_token, get_session_factory())
        app.state.backtest_service = BacktestService(market_client)
        app.state.optimizer_service = OptimizerService(market_client)
        app.state.walkforward_service = WalkForwardService(market_client)
        app.state.multi_ticker_service = MultiTickerService(market_client)
        app.state.correlation_service = CorrelationService(market_client)
        app.state.screener_service = ScreenerService(market_client)  # type: ignore[arg-type]
        app.state.anomaly_service = AnomalyService(market_client)
        app.state.market_client = market_client  # <-- acesso direto para outras dependências
        logger.info("market_data_client.composite.ready")
    else:
        # Sem token BRAPI: serviços analíticos indisponíveis, mas watchlist
        # funciona com Yahoo Finance como fonte primária
        from finanalytics_ai.infrastructure.adapters.market_data_client import (
            create_cached_market_data_client
        )
        from finanalytics_ai.infrastructure.database.connection import get_session_factory

        market_client = create_cached_market_data_client(None, get_session_factory())
        app.state.market_client = market_client
        from finanalytics_ai.application.services.anomaly_service import AnomalyService
        from finanalytics_ai.application.services.backtest_service import BacktestService
        from finanalytics_ai.application.services.correlation_service import CorrelationService
        from finanalytics_ai.application.services.multi_ticker_service import MultiTickerService
        from finanalytics_ai.application.services.optimizer_service import OptimizerService
        from finanalytics_ai.application.services.walkforward_service import WalkForwardService
        app.state.backtest_service = BacktestService(market_client)
        app.state.optimizer_service = OptimizerService(market_client)
        app.state.walkforward_service = WalkForwardService(market_client)
        app.state.multi_ticker_service = MultiTickerService(market_client)
        app.state.correlation_service = CorrelationService(market_client)
        app.state.anomaly_service = AnomalyService(market_client)
        logger.info("market_data_client.fintz_fallback.ready")

    # ── 6. WatchlistService: cria tabelas DB ─────────────────────────────────
    # Importa os models ANTES do create_all para registrá-los no metadata.
    # Sem o import, Base.metadata não conhece as tabelas e create_all é no-op.
    from finanalytics_ai.infrastructure.database.connection import Base
    from finanalytics_ai.infrastructure.database.repositories.ohlc_repo import (  # noqa: F401
        OHLCBarModel,
        OHLCCacheMetaModel
    )
    from finanalytics_ai.infrastructure.database.repositories.rf_repo import (
        Base as RFBase
    )
    from finanalytics_ai.infrastructure.database.repositories.rf_repo import (  # noqa: F401
        RFHoldingModel,
        RFPortfolioModel
    )
    from finanalytics_ai.infrastructure.database.repositories.user_repo import (
        UserModel,  # noqa: F401
    )
    from finanalytics_ai.infrastructure.database.repositories.watchlist_repo import (
        WatchlistItemModel,  # noqa: F401
    )
    from finanalytics_ai.infrastructure.database.repositories.diario_repo import (
        DiarioModel,  # noqa: F401 — registra trade_journal na metadata
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
        from finanalytics_ai.infrastructure.database.connection import get_engine as _ge2
        from finanalytics_ai.infrastructure.database.connection import get_session_factory as _gsf2
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
            ts_pool_available
        )
        from finanalytics_ai.infrastructure.timescale.ohlc_ts_repo import OHLCTimescaleRepo
        from finanalytics_ai.infrastructure.timescale.schema import init_schema

        ts_dsn = settings.timescale_url
        if await ts_pool_available(ts_dsn):
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
        from finanalytics_ai.infrastructure.database.connection import get_engine as _get_eng
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        from finanalytics_ai.infrastructure.database.repositories.ticker_repo import (
            Base as TickerBase
        )
        from finanalytics_ai.infrastructure.database.repositories.ticker_service import (
            TickerService
        )

        async with _get_eng().begin() as conn:
            await conn.run_sync(lambda c: TickerBase.metadata.create_all(c, checkfirst=True))
        app.state.ticker_service = TickerService(get_session_factory())
        logger.info("ticker_service.ready")
    except Exception as exc:
        logger.warning("ticker_service.FAILED", error=str(exc))


    # -- IntradaySetupService (deteccao de setups em tempo real)
    try:
        from finanalytics_ai.application.services.intraday_setup_service import IntradaySetupService
        _market = getattr(app.state, 'market_client', None)
        _bus = getattr(app.state, 'notification_bus', None)
        if _market:
            app.state.intraday_setup_service = IntradaySetupService(_market, _bus)
            logger.info("intraday_setup_service.ready")
        else:
            app.state.intraday_setup_service = None
            logger.warning("intraday_setup_service.skipped", reason="market_client ausente")
    except Exception as _ise:
        logger.warning("intraday_setup_service.FAILED", error=str(_ise))
        app.state.intraday_setup_service = None

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
        _notification_bus = getattr(app.state, 'notification_bus', None)
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
        from finanalytics_ai.infrastructure.database.repositories.diario_repo import (
            DiarioRepository,
        )
        from finanalytics_ai.infrastructure.database.connection import get_session_factory

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
                kafka_producer=kprod
            )
            await _price_producer.start()
            _producer_task = asyncio.create_task(_price_producer.run())
            producer_ok = True
            logger.info(
                "price_producer.running",
                tickers=tickers,
                interval=settings.producer_poll_interval_seconds
            )
        except Exception as exc:
            logger.warning("price_producer.unavailable", error=str(exc))
    elif settings.producer_enabled and not settings.brapi_token:
        logger.warning(
            "price_producer.disabled",
            reason="BRAPI_TOKEN não configurado — adicione ao .env para ativar o producer automático"
        )

    # ── Fundamental Analysis Service ──────────────────────────────────────
    try:
        from finanalytics_ai.application.services.fundamental_analysis_service import (
            FundamentalAnalysisService
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

    logger.info(
        "api.ready",
        postgres=True,
        timescale=timescale_ok,
        kafka=kafka_ok,
        producer=producer_ok
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
    # Fecha pool TimescaleDB
    try:
        from finanalytics_ai.infrastructure.timescale.connection import close_ts_pool

        await close_ts_pool()
    except Exception:
        pass
    await close_engine()
    logger.info("api.stopped")

def create_app() -> FastAPI:
    app = FastAPI(
        title="FinAnalytics AI",
        description="Framework de Análise e Busca de Investimentos",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc"
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"]
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
            content={"error": exc.code, "message": exc.message, "context": exc.context}
        )

    if _ETF_AVAILABLE:
        app.include_router(etf_routes.router, tags=["ETF"])
    if _OPTIMIZER_AVAILABLE:
        app.include_router(optimizer_routes.router, tags=["Portfolio Optimizer"])
    if _FUND_ANALYSIS_AVAILABLE:
        app.include_router(fund_analysis_routes.router, tags=["Análise de Lâminas"])
    if _PATRIMONY_AVAILABLE:
        app.include_router(patrimony_routes.router, tags=["Patrimônio"])
    app.include_router(dashboard.router, tags=["Dashboard"])
    app.include_router(health.router, tags=["Health"])
    app.include_router(fundamental_analysis.router)
    app.include_router(wallet.router)

    from finanalytics_ai.interfaces.api.routes import auth as auth_routes
    app.include_router(auth_routes.router, tags=["Autenticação"])
    app.include_router(admin_routes.router, tags=["Admin"])
    try:
        from finanalytics_ai.interfaces.api.routes import system_status as sys_routes
        app.include_router(sys_routes.router, tags=["System"])
    except Exception as _sse:
        import structlog as _sl4
        _sl4.get_logger(__name__).warning("system_status.router.FAILED", error=str(_sse))
    app.include_router(portfolio.router, prefix="/api/v1/portfolios", tags=["Portfolio"])
    app.include_router(quotes.router, prefix="/api/v1/quotes", tags=["Cotações"])
    app.include_router(events.router, prefix="/api/v1/events", tags=["Eventos"])
    app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["Alertas"])
    app.include_router(producer.router, prefix="/api/v1/producer", tags=["Producer"])
    app.include_router(fundos_routes.router)
    app.include_router(backtest.router, tags=["Backtest"])

    try:
        from finanalytics_ai.interfaces.api.routes import diario as diario_routes
        app.include_router(diario_routes.router, tags=["Diário"])
    except Exception as _de:
        import structlog as _sl5
        _sl5.get_logger(__name__).warning("diario.router.FAILED", error=str(_de))
    app.include_router(correlation.router, tags=["Correlation"])
    app.include_router(screener.router, tags=["Screener"])
    try:
        from finanalytics_ai.interfaces.api.routes import screener_fintz
        app.include_router(screener_fintz.router, tags=["Screener Fintz"])
        logger.info("screener_fintz.route.registered")
    except Exception as _sfe:
        logger.warning("screener_fintz.route.FAILED", error=str(_sfe))
    try:
        from finanalytics_ai.interfaces.api.routes import alerts_indicator
        app.include_router(alerts_indicator.router, tags=["Alertas Indicadores"])
        logger.info("alerts_indicator.route.registered")
    except Exception as _aire:
        logger.warning("alerts_indicator.route.FAILED", error=str(_aire))
    try:
        from finanalytics_ai.interfaces.api.routes import ranking as ranking_routes
        app.include_router(ranking_routes.router, tags=["Ranking"])
        logger.info("ranking.route.registered")
    except Exception as _rre:
        logger.warning("ranking.route.FAILED", error=str(_rre))
    try:
        from finanalytics_ai.interfaces.api.routes import setups as setups_routes
        app.include_router(setups_routes.router, tags=["Setups Intraday"])
        logger.info("setups.route.registered")
    except Exception as _stre:
        logger.warning("setups.route.FAILED", error=str(_stre))
        logger.info("ranking.route.registered")
    except Exception as _rre:
        logger.warning("ranking.route.FAILED", error=str(_rre))
        logger.info("alerts_indicator.route.registered")
    except Exception as _aire:
        logger.warning("alerts_indicator.route.FAILED", error=str(_aire))
        logger.info("screener_fintz.route.registered")
    except Exception as _sfe:
        logger.warning("screener_fintz.route.FAILED", error=str(_sfe))
    app.include_router(ml_routes.router, tags=["ML Probabilistico"])
    app.include_router(marketdata_routes.router, tags=["Market Data"])
    app.include_router(anomaly.router, tags=["Anomaly"])
    app.include_router(reports.router, tags=["Reports"])
    app.include_router(watchlist.router, tags=["Watchlist"])
    app.include_router(performance.router, tags=["Performance"])

    try:
        from finanalytics_ai.interfaces.api.routes import fintz_sync_status
        app.include_router(fintz_sync_status.router, prefix="/api/v1/fintz", tags=["Fintz Sync"])
    except Exception as _fss:
        import structlog as _sl3
        _sl3.get_logger(__name__).warning("fintz_sync_status.router.FAILED", error=str(_fss))
    try:
        from finanalytics_ai.interfaces.api.routes import fintz_data as fintz_data_routes
        app.include_router(fintz_data_routes.router, prefix="/api/v1/fintz", tags=["Fintz Histórico"])
    except Exception as _fe2:
        import structlog as _sl2
        _sl2.get_logger(__name__).warning("fintz_data.router.FAILED", error=str(_fe2))
    app.include_router(fixed_income.router, tags=["Renda Fixa"])

    try:
        from finanalytics_ai.interfaces.api.routes import forecast as forecast_routes
        app.include_router(forecast_routes.router, tags=["Forecast"])
    except Exception as _e:
        import structlog as _sl
        _sl.get_logger(__name__).warning("forecast.router.FAILED", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import macro as macro_routes
        app.include_router(macro_routes.router, tags=["Macro"])
    except ImportError:
        pass

    try:
        from finanalytics_ai.interfaces.api.routes import storage_admin
        app.include_router(storage_admin.router, tags=["Storage Admin"])
    except ImportError:
        pass

    try:
        from finanalytics_ai.interfaces.api.routes.ohlc import router as ohlc_router

        app.include_router(ohlc_router, tags=["OHLC"])
    except ImportError:
        pass
    try:
        from finanalytics_ai.interfaces.api.routes.ticker_routes import router as ticker_router

        app.include_router(ticker_router, tags=["Tickers"])
    except ImportError:
        pass

    # ── Páginas HTML estáticas ────────────────────────────────────────────────
    import pathlib

    from fastapi.responses import HTMLResponse

    _static = pathlib.Path(__file__).parent / "static"

    def _html(name: str) -> HTMLResponse:
        f = _static / name
        return HTMLResponse(
            f.read_text(encoding="utf-8") if f.exists() else f"<h1>{name} não encontrado</h1>",
            status_code=200 if f.exists() else 404
        )

    @app.get("/carteira", response_class=HTMLResponse, include_in_schema=False)
    async def serve_carteira() -> HTMLResponse:
        return _html("carteira.html")

    @app.get("/profile", response_class=HTMLResponse, include_in_schema=False)
    async def serve_profile() -> HTMLResponse:
        return _html("profile.html")

    @app.get("/fundamental", response_class=HTMLResponse, include_in_schema=False)
    async def serve_fundamental() -> HTMLResponse:
        return _html("fundamental.html")

    @app.get("/hub", response_class=HTMLResponse, include_in_schema=False)
    async def serve_hub() -> HTMLResponse:
        return _html("hub.html")

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def serve_login() -> HTMLResponse:
        return _html("login.html")

    @app.get("/reset-password", response_class=HTMLResponse, include_in_schema=False)
    async def serve_reset_password() -> HTMLResponse:
        return _html("reset_password.html")

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def serve_dashboard_page() -> HTMLResponse:
        return _html("dashboard.html")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_root() -> HTMLResponse:
        # Tela inicial é o login — o JS redireciona para /dashboard se já autenticado
        return _html("login.html")

    @app.get("/backtest", response_class=HTMLResponse, include_in_schema=False)
    async def serve_backtest() -> HTMLResponse:
        return _html("backtest.html")

    @app.get("/diario", response_class=HTMLResponse, include_in_schema=False)
    async def serve_diario() -> HTMLResponse:
        return _html("diario.html")

    @app.get("/correlation", response_class=HTMLResponse, include_in_schema=False)
    async def serve_correlation() -> HTMLResponse:
        return _html("correlation.html")

    @app.get("/screener", response_class=HTMLResponse, include_in_schema=False)
    async def serve_screener() -> HTMLResponse:
        return _html("screener.html")

    @app.get("/subscriptions", response_class=HTMLResponse, include_in_schema=False)
    async def subscriptions_page():
        return _html("subscriptions.html")

    @app.get("/marketdata", response_class=HTMLResponse, include_in_schema=False)
    async def marketdata_page():
        return _html("marketdata.html")

    @app.get("/ml", response_class=HTMLResponse, include_in_schema=False)
    async def serve_ml() -> HTMLResponse:
        return _html("ml.html")

    @app.get("/anomaly", response_class=HTMLResponse, include_in_schema=False)
    async def serve_anomaly() -> HTMLResponse:
        return _html("anomaly.html")

    @app.get("/watchlist", response_class=HTMLResponse, include_in_schema=False)
    async def serve_watchlist() -> HTMLResponse:
        return _html("watchlist.html")

    @app.get("/performance", response_class=HTMLResponse, include_in_schema=False)
    async def serve_performance() -> HTMLResponse:
        return _html("performance.html")

    @app.get("/fixed-income", response_class=HTMLResponse, include_in_schema=False)
    async def serve_fixed_income() -> HTMLResponse:
        return _html("fixed_income.html")

    @app.get("/etf", response_class=HTMLResponse, include_in_schema=False)
    async def serve_etf() -> HTMLResponse:
        return _html("etf.html")

    @app.get("/optimizer", response_class=HTMLResponse, include_in_schema=False)
    async def serve_optimizer() -> HTMLResponse:
        return _html("optimizer.html")

    @app.get("/fundos", response_class=HTMLResponse, include_in_schema=False)
    async def serve_fundos() -> HTMLResponse:
        return _html("laminas.html")

    @app.get("/laminas", response_class=HTMLResponse, include_in_schema=False)
    async def serve_laminas() -> HTMLResponse:
        return _html("laminas.html")

    @app.get("/patrimony", response_class=HTMLResponse, include_in_schema=False)
    async def serve_patrimony() -> HTMLResponse:
        return _html("patrimony.html")

    @app.get("/forecast", response_class=HTMLResponse, include_in_schema=False)
    async def serve_forecast() -> HTMLResponse:
        return _html("forecast.html")

    @app.get("/macro", response_class=HTMLResponse, include_in_schema=False)
    async def serve_macro() -> HTMLResponse:
        return _html("macro.html")
    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    async def serve_admin() -> HTMLResponse:
        return _html("admin.html")

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    async def serve_admin() -> HTMLResponse:
        return _html("admin.html")

    @app.get("/fintz", response_class=HTMLResponse, include_in_schema=False)
    async def serve_fintz() -> HTMLResponse:
        return _html("fintz.html")

    @app.get("/profit-tickers", response_class=HTMLResponse, include_in_schema=False)
    async def serve_profit_tickers() -> HTMLResponse:
        return _html("tickers.html")

    return app

