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
from finanalytics_ai.interfaces.api.routes import ml_forecasting as ml_routes
from finanalytics_ai.interfaces.api.routes import marketdata as marketdata_routes
from finanalytics_ai.interfaces.api.routes import live_market as live_market_routes
from finanalytics_ai.interfaces.api.routes import fundos as fundos_routes
from finanalytics_ai.interfaces.api.routes import accounts as accounts_routes
from finanalytics_ai.application.services.account_service import AccountService
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
    """
    Lifespan do FastAPI — inicializa todos os servicos na ordem correta.
    Cada bloco esta isolado em startup/*.py para facilitar manutencao e testes.
    """
    global _kafka_consumer, _kafka_task, _alert_service, _price_producer, _producer_task, _account_service

    from finanalytics_ai.interfaces.api.startup import db as _db
    from finanalytics_ai.interfaces.api.startup import cache as _cache
    from finanalytics_ai.interfaces.api.startup import kafka as _kafka
    from finanalytics_ai.interfaces.api.startup import market_data as _md
    from finanalytics_ai.interfaces.api.startup import services as _svc
    from finanalytics_ai.interfaces.api.startup import producers as _prod

    settings = get_settings()
    logger.info("api.starting", env=getattr(settings, "env", "production"))

    # 1. PostgreSQL + Bootstrap
    await _db.init_postgres(app)

    # 2. Cache + Rate Limiter
    _cache.init_cache(app, settings)

    # 3. TimescaleDB + chunk warmup
    timescale_ok = await _db.init_timescale()

    # 4. AlertService
    _alert_service = await _svc.init_alert_service(app)

    # 5. AccountService
    _account_service = await _svc.init_account_service(app)

    # 6. Kafka consumer
    _kafka_consumer, _kafka_task = await _kafka.init_kafka(app, _alert_service, timescale_ok)
    kafka_ok = _kafka_consumer is not None

    # 7. Market data client + servicos dependentes
    market_client = await _md.init_market_data(app, settings)

    # 8. Watchlist (cria tabelas)
    await _svc.init_watchlist(app)

    # 9. OHLC services + Tape Service + servicos de dominio
    from finanalytics_ai.interfaces.api.startup import ohlc as _ohlc
    _ohlc_daily_task = await _ohlc.init_ohlc_services(app, timescale_ok)
    await _ohlc.init_tape_service(app, settings)
    await _ohlc.init_domain_services(app, market_client)

    # 10. DiarioRepository + FundamentalAnalysis
    await _svc.init_diario(app)
    await _svc.init_fundamental_analysis(app, market_client)

    # 11. BRAPI Price Producer
    _price_producer, _producer_task = await _prod.init_price_producer(app, settings)
    producer_ok = _price_producer is not None

    logger.info(
        "api.ready",
        postgres=True,
        timescale=timescale_ok,
        kafka=kafka_ok,
        producer=producer_ok,
    )
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await _prod.shutdown(_price_producer, _producer_task)
    await _kafka.shutdown(_kafka_consumer, _kafka_task)
    await _ohlc.shutdown_ohlc(_ohlc_daily_task)
    await _db.shutdown(timescale_ok)
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
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
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
    app.include_router(accounts_routes.router, prefix="/api/v1/accounts", tags=["Contas"])
    app.include_router(producer.router, prefix="/api/v1/producer", tags=["Producer"])
    app.include_router(fundos_routes.router)
    app.include_router(backtest.router, tags=["Backtest"])

    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import diario as diario_routes
        app.include_router(diario_routes.router, tags=["Diário"])
    except Exception as _de:
        import structlog as _sl5
        _sl5.get_logger(__name__).warning("diario.router.FAILED", error=str(_de))
    app.include_router(correlation.router, tags=["Correlation"])
    app.include_router(screener.router, tags=["Screener"])
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import screener_fintz
        app.include_router(screener_fintz.router, tags=["Screener Fintz"])
        logger.info("screener_fintz.route.registered")
    except Exception as _sfe:
        logger.warning("screener_fintz.route.FAILED", error=str(_sfe))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import alerts_indicator
        app.include_router(alerts_indicator.router, tags=["Alertas Indicadores"])
        logger.info("alerts_indicator.route.registered")
    except Exception as _aire:
        logger.warning("alerts_indicator.route.FAILED", error=str(_aire))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import ranking as ranking_routes
        app.include_router(ranking_routes.router, tags=["Ranking"])
        logger.info("ranking.route.registered")
    except Exception as _rre:
        logger.warning("ranking.route.FAILED", error=str(_rre))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import crypto as crypto_routes
        app.include_router(crypto_routes.router, tags=["Crypto"])
        logger.info("crypto.route.registered")
    except Exception as _cre:
        logger.warning("crypto.route.FAILED", error=str(_cre))

    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import tape as tape_routes
        app.include_router(tape_routes.router, tags=["Tape Reading"])
        logger.info("tape.route.registered")
    except Exception as _tre:
        logger.warning("tape.route.FAILED", error=str(_tre))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import whatsapp as whatsapp_routes
        app.include_router(whatsapp_routes.router, tags=["WhatsApp"])
        logger.info("whatsapp.route.registered")
    except Exception as _ware:
        logger.warning("whatsapp.route.FAILED", error=str(_ware))

    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import dividendos as dividendos_routes
        app.include_router(dividendos_routes.router, tags=["Dividendos"])
        logger.info("dividendos.route.registered")
    except Exception as _dre:
        logger.warning("dividendos.route.FAILED", error=str(_dre))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import var as var_routes
        app.include_router(var_routes.router, tags=["VaR"])
        logger.info("var.route.registered")
    except Exception as _vre:
        logger.warning("var.route.FAILED", error=str(_vre))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import sentiment as sentiment_routes
        app.include_router(sentiment_routes.router, tags=["Sentimento"])
        logger.info("sentiment.route.registered")
    except Exception as _sre:
        logger.warning("sentiment.route.FAILED", error=str(_sre))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import opcoes as opcoes_routes
        app.include_router(opcoes_routes.router, tags=["Opcoes"])
        logger.info("opcoes.route.registered")
    except Exception as _ore:
        logger.warning("opcoes.route.FAILED", error=str(_ore))
        logger.info("ranking.route.registered")
    except Exception as _rre:
        logger.warning("ranking.route.FAILED", error=str(_rre))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
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
    app.include_router(live_market_routes.router, tags=["Live Market Data"])
    app.include_router(anomaly.router, tags=["Anomaly"])
    app.include_router(reports.router, tags=["Reports"])
    app.include_router(watchlist.router, tags=["Watchlist"])
    app.include_router(performance.router, tags=["Performance"])

    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import fintz_sync_status
        app.include_router(fintz_sync_status.router, prefix="/api/v1/fintz", tags=["Fintz Sync"])
    except Exception as _fss:
        import structlog as _sl3
        _sl3.get_logger(__name__).warning("fintz_sync_status.router.FAILED", error=str(_fss))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import fintz_data as fintz_data_routes
        app.include_router(fintz_data_routes.router, prefix="/api/v1/fintz", tags=["Fintz Histórico"])
    except Exception as _fe2:
        import structlog as _sl2
        _sl2.get_logger(__name__).warning("fintz_data.router.FAILED", error=str(_fe2))
    app.include_router(fixed_income.router, tags=["Renda Fixa"])

    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import forecast as forecast_routes
        app.include_router(forecast_routes.router, tags=["Forecast"])
    except Exception as _e:
        import structlog as _sl
        _sl.get_logger(__name__).warning("forecast.router.FAILED", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
    try:
        from finanalytics_ai.interfaces.api.routes import macro as macro_routes
        app.include_router(macro_routes.router, tags=["Macro"])
    except ImportError:
        pass

    try:
        from finanalytics_ai.interfaces.api.routes import import_route
        app.include_router(import_route.router, tags=["Import"])
        logger.info("import.router.ok")
    except Exception as _e:
        logger.warning("import.router.SKIP", error=str(_e))
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

    
    @app.get("/daytrade/setups", response_class=HTMLResponse, include_in_schema=False)
    async def serve_daytrade_setups() -> HTMLResponse:
        return _html("daytrade_setups.html")

    @app.get("/daytrade/risco", response_class=HTMLResponse, include_in_schema=False)
    async def serve_daytrade_risco() -> HTMLResponse:
        return _html("daytrade_risco.html")

    
    
    @app.get("/opcoes/estrategias", response_class=HTMLResponse, include_in_schema=False)
    async def serve_opcoes_estrategias() -> HTMLResponse:
        return _html("opcoes_estrategias.html")

    
    
    
    
    
    @app.get("/vol-surface", response_class=HTMLResponse, include_in_schema=False)
    async def serve_vol_surface() -> HTMLResponse:
        return _html("vol_surface.html")

    
    
    
    @app.get("/crypto", response_class=HTMLResponse, include_in_schema=False)
    async def serve_crypto() -> HTMLResponse:
        return _html("crypto.html")

    @app.get("/import", response_class=HTMLResponse, include_in_schema=False)
    async def serve_import() -> HTMLResponse:
        return _html("import.html")

    @app.get("/tape", response_class=HTMLResponse, include_in_schema=False)
    async def serve_tape() -> HTMLResponse:
        return _html("tape.html")

    @app.get("/whatsapp", response_class=HTMLResponse, include_in_schema=False)
    async def serve_whatsapp() -> HTMLResponse:
        return _html("whatsapp.html")

    @app.get("/dividendos", response_class=HTMLResponse, include_in_schema=False)
    async def serve_dividendos() -> HTMLResponse:
        return _html("dividendos.html")

    @app.get("/var", response_class=HTMLResponse, include_in_schema=False)
    async def serve_var() -> HTMLResponse:
        return _html("var.html")

    @app.get("/pnl", response_class=HTMLResponse, include_in_schema=False)
    async def serve_pnl() -> HTMLResponse:
        return _html("pnl.html")

    @app.get("/sentiment", response_class=HTMLResponse, include_in_schema=False)
    async def serve_sentiment() -> HTMLResponse:
        return _html("sentiment.html")

    @app.get("/opcoes", response_class=HTMLResponse, include_in_schema=False)
    async def serve_opcoes() -> HTMLResponse:
        return _html("opcoes.html")

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




