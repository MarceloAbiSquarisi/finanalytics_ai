"""
FastAPI application factory — Sprint 7: BRAPI Price Producer.

Lifespan startup order:
  1. PostgreSQL
  2. TimescaleDB
  3. NotificationBus + AlertService
  4. Kafka consumer (avalia alertas + persiste ticks)
  5. BrapiPriceProducer (polling BRAPI → Kafka) — apenas se PRODUCER_ENABLED=true
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from finanalytics_ai.config import EventQueueBackend, get_settings
from finanalytics_ai.exceptions import FinAnalyticsError
from finanalytics_ai.infrastructure.database.connection import close_engine, get_engine
from finanalytics_ai.interfaces.api.routes import dashboard, health, portfolio, quotes, events, alerts, producer, backtest, correlation, screener, anomaly, reports, watchlist, performance, fixed_income
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
from finanalytics_ai.metrics import PrometheusMiddleware, metrics_endpoint
from finanalytics_ai.infrastructure.cache.backend import create_cache_backend
from finanalytics_ai.infrastructure.cache.rate_limiter import create_rate_limiter

logger = structlog.get_logger(__name__)

# ── Singletons globais ────────────────────────────────────────────────────────
_kafka_consumer:   Any = None
_kafka_task:       Any = None
_alert_service:    Any = None
_price_producer:   Any = None
_producer_task:    Any = None


def get_kafka_consumer()  -> Any: return _kafka_consumer
def get_alert_service()   -> Any: return _alert_service
def get_price_producer()  -> Any: return _price_producer


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _kafka_consumer, _kafka_task, _alert_service, _price_producer, _producer_task

    settings = get_settings()
    logger.info("api.starting", env=settings.app_env)

    # ── 1. PostgreSQL ─────────────────────────────────────────────────────────
    get_engine()
    logger.info("postgres.connected")

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
    from finanalytics_ai.infrastructure.notifications import get_notification_bus
    from finanalytics_ai.infrastructure.database.connection import get_session
    from finanalytics_ai.application.services.alert_service import AlertService

    bus = get_notification_bus()
    _alert_service = AlertService(
        session_factory=get_session,
        notification_bus=bus,
    )
    logger.info("alert_service.ready")

    # ── 4. Kafka consumer ─────────────────────────────────────────────────────
    kafka_ok = False
    if settings.event_queue_backend == EventQueueBackend.KAFKA:
        try:
            from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventConsumer
            consumer = KafkaMarketEventConsumer()
            await consumer.start()
            _kafka_consumer = consumer

            async def _handle_event(event: Any) -> None:
                from finanalytics_ai.domain.entities.event import MarketEvent, EventType
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
                            TimescalePriceTickRepository, get_timescale_pool,
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
    if settings.brapi_token:
        from finanalytics_ai.application.services.backtest_service import BacktestService
        from finanalytics_ai.application.services.optimizer_service import OptimizerService
        from finanalytics_ai.application.services.walkforward_service import WalkForwardService
        from finanalytics_ai.infrastructure.adapters.market_data_client import create_cached_market_data_client
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        from finanalytics_ai.application.services.multi_ticker_service import MultiTickerService
        from finanalytics_ai.application.services.correlation_service import CorrelationService
        from finanalytics_ai.application.services.screener_service import ScreenerService
        from finanalytics_ai.application.services.anomaly_service import AnomalyService
        market_client = create_cached_market_data_client(settings.brapi_token, get_session_factory())
        app.state.backtest_service     = BacktestService(market_client)
        app.state.optimizer_service    = OptimizerService(market_client)
        app.state.walkforward_service  = WalkForwardService(market_client)
        app.state.multi_ticker_service = MultiTickerService(market_client)
        app.state.correlation_service  = CorrelationService(market_client)
        app.state.screener_service     = ScreenerService(market_client)
        app.state.anomaly_service      = AnomalyService(market_client)
        app.state.market_client        = market_client   # <-- acesso direto para outras dependências
        logger.info("market_data_client.composite.ready")
    else:
        # Sem token BRAPI: serviços analíticos indisponíveis, mas watchlist
        # funciona com Yahoo Finance como fonte primária
        from finanalytics_ai.infrastructure.adapters.market_data_client import create_cached_market_data_client
        from finanalytics_ai.infrastructure.database.connection import get_session_factory
        market_client = create_cached_market_data_client(None, get_session_factory())
        app.state.market_client    = market_client
        app.state.backtest_service     = None
        app.state.optimizer_service    = None
        app.state.walkforward_service  = None
        app.state.multi_ticker_service = None
        app.state.correlation_service  = None
        app.state.screener_service     = None
        app.state.anomaly_service      = None
        logger.warning("brapi_token.missing — analytic services disabled, watchlist uses Yahoo")

    # ── 6. WatchlistService: cria tabelas DB ─────────────────────────────────
    # Importa os models ANTES do create_all para registrá-los no metadata.
    # Sem o import, Base.metadata não conhece as tabelas e create_all é no-op.
    from finanalytics_ai.infrastructure.database.repositories.watchlist_repo import (
        WatchlistItemModel, WatchlistAlertModel,  # noqa: F401
    )
    from finanalytics_ai.infrastructure.database.connection import Base
    from finanalytics_ai.infrastructure.database.repositories.user_repo import UserModel  # noqa: F401
    from finanalytics_ai.infrastructure.database.repositories.rf_repo import (  # noqa: F401
        RFPortfolioModel, RFHoldingModel, Base as RFBase,
    )
    from finanalytics_ai.infrastructure.database.repositories.ohlc_repo import (  # noqa: F401
        OHLCBarModel, OHLCCacheMetaModel,
    )
    try:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(RFBase.metadata.create_all)
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
        from finanalytics_ai.infrastructure.database.repositories.ohlc_1m_repo import Base as _B1m
        from finanalytics_ai.application.services.ohlc_1m_service import OHLC1mService as _S1m
        from finanalytics_ai.infrastructure.database.connection import get_engine as _ge2, get_session_factory as _gsf2
        from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient as _BC2
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
    _ohlc_daily_task       = None
    try:
        from finanalytics_ai.infrastructure.timescale.connection import (
            init_ts_pool, ts_pool_available,
        )
        from finanalytics_ai.infrastructure.timescale.schema import init_schema
        from finanalytics_ai.infrastructure.timescale.ohlc_ts_repo import OHLCTimescaleRepo
        from finanalytics_ai.application.services.ohlc_updater import OHLCUpdaterService

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
        from finanalytics_ai.infrastructure.database.repositories.ticker_repo import TickerModel, Base as TickerBase
        from finanalytics_ai.infrastructure.database.repositories.ticker_service import TickerService
        from finanalytics_ai.infrastructure.database.connection import get_engine as _get_eng, get_session_factory
        async with _get_eng().begin() as conn:
            await conn.run_sync(TickerBase.metadata.create_all)
        app.state.ticker_service = TickerService(get_session_factory())
        logger.info('ticker_service.ready')
    except Exception as exc:
        logger.warning('ticker_service.FAILED', error=str(exc))

    # ── 6. BRAPI Price Producer ───────────────────────────────────────────────
    producer_ok = False
    if settings.producer_enabled and kafka_ok and settings.brapi_token:
        try:
            from finanalytics_ai.application.services.price_producer import BrapiPriceProducer
            from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
            from finanalytics_ai.infrastructure.queue.kafka_adapter import KafkaMarketEventProducer

            tickers = [t.strip() for t in settings.producer_tickers.split(",") if t.strip()]
            brapi  = BrapiClient()
            kprod  = KafkaMarketEventProducer()

            _price_producer = BrapiPriceProducer(
                tickers        = tickers,
                poll_interval  = settings.producer_poll_interval_seconds,
                brapi_client   = brapi,
                kafka_producer = kprod,
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

    logger.info(
        "api.ready",
        postgres=True,
        timescale=timescale_ok,
        kafka=kafka_ok,
        producer=producer_ok,
    )
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if _producer_task:
        if _price_producer:
            await _price_producer.stop()
        _producer_task.cancel()
        try:
            await _producer_task
        except asyncio.CancelledError:
            pass

    if _kafka_task:
        _kafka_task.cancel()
        try:
            await _kafka_task
        except asyncio.CancelledError:
            pass
    if _kafka_consumer:
        try:
            await _kafka_consumer.stop()
        except Exception:
            pass
    if timescale_ok:
        try:
            from finanalytics_ai.infrastructure.timescale.repository import close_timescale_pool
            await close_timescale_pool()
        except Exception:
            pass
    # Cancela loop de atualização OHLC
    if _ohlc_daily_task:
        _ohlc_daily_task.cancel()
        try:
            await _ohlc_daily_task
        except asyncio.CancelledError:
            pass
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
        redoc_url="/redoc",
    )

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
            "PORTFOLIO_NOT_FOUND": 404, "INVALID_TICKER": 422,
            "INSUFFICIENT_FUNDS": 422,  "INVALID_QUANTITY": 422,
            "DUPLICATE_EVENT": 409,
        }
        return JSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"error": exc.code, "message": exc.message, "context": exc.context},
        )

    if _ETF_AVAILABLE:
        app.include_router(etf_routes.router, tags=["ETF"])
    if _OPTIMIZER_AVAILABLE:
        app.include_router(optimizer_routes.router, tags=["Portfolio Optimizer"])
    if _FUND_ANALYSIS_AVAILABLE:
        app.include_router(fund_analysis_routes.router, tags=["Análise de Lâminas"])
    if _PATRIMONY_AVAILABLE:
        app.include_router(patrimony_routes.router, tags=["Patrimônio"])
    app.include_router(dashboard.router,  tags=["Dashboard"])
    app.include_router(health.router,     tags=["Health"])
    app.include_router(portfolio.router,  prefix="/api/v1/portfolios", tags=["Portfolio"])
    app.include_router(quotes.router,     prefix="/api/v1/quotes",     tags=["Cotações"])
    app.include_router(events.router,     prefix="/api/v1/events",     tags=["Eventos"])
    app.include_router(alerts.router,     prefix="/api/v1/alerts",     tags=["Alertas"])
    app.include_router(producer.router,   prefix="/api/v1/producer",   tags=["Producer"])
    app.include_router(backtest.router,     tags=["Backtest"])
    app.include_router(correlation.router,  tags=["Correlation"])
    app.include_router(screener.router,     tags=["Screener"])
    app.include_router(anomaly.router,      tags=["Anomaly"])
    app.include_router(reports.router,      tags=["Reports"])
    app.include_router(watchlist.router,    tags=["Watchlist"])
    app.include_router(performance.router,     tags=["Performance"])
    app.include_router(fixed_income.router,    tags=["Renda Fixa"])

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
        return HTMLResponse(f.read_text(encoding="utf-8") if f.exists()
                            else f"<h1>{name} não encontrado</h1>", status_code=200 if f.exists() else 404)

    @app.get("/hub",         response_class=HTMLResponse, include_in_schema=False)
    async def serve_hub()         -> HTMLResponse: return _html("hub.html")

    @app.get("/",            response_class=HTMLResponse, include_in_schema=False)
    async def serve_dashboard()   -> HTMLResponse: return _html("dashboard.html")

    @app.get("/backtest",    response_class=HTMLResponse, include_in_schema=False)
    async def serve_backtest()    -> HTMLResponse: return _html("backtest.html")

    @app.get("/correlation", response_class=HTMLResponse, include_in_schema=False)
    async def serve_correlation() -> HTMLResponse: return _html("correlation.html")

    @app.get("/screener",    response_class=HTMLResponse, include_in_schema=False)
    async def serve_screener()    -> HTMLResponse: return _html("screener.html")

    @app.get("/anomaly",     response_class=HTMLResponse, include_in_schema=False)
    async def serve_anomaly()     -> HTMLResponse: return _html("anomaly.html")

    @app.get("/watchlist",   response_class=HTMLResponse, include_in_schema=False)
    async def serve_watchlist()   -> HTMLResponse: return _html("watchlist.html")

    @app.get("/performance", response_class=HTMLResponse, include_in_schema=False)
    async def serve_performance() -> HTMLResponse: return _html("performance.html")

    @app.get("/fixed-income", response_class=HTMLResponse, include_in_schema=False)
    async def serve_fixed_income() -> HTMLResponse: return _html("fixed_income.html")

    @app.get("/etf",       response_class=HTMLResponse, include_in_schema=False)
    async def serve_etf()       -> HTMLResponse: return _html("etf.html")

    @app.get("/optimizer", response_class=HTMLResponse, include_in_schema=False)
    async def serve_optimizer() -> HTMLResponse: return _html("optimizer.html")

    @app.get("/laminas", response_class=HTMLResponse, include_in_schema=False)
    async def serve_laminas() -> HTMLResponse: return _html("laminas.html")

    @app.get("/patrimony", response_class=HTMLResponse, include_in_schema=False)
    async def serve_patrimony() -> HTMLResponse: return _html("patrimony.html")


    return app
