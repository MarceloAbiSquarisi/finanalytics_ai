"""
Registro de routers FastAPI — extraido de app.py em 01/mai/2026.

register_routers(app, logger) inclui ~50 routers no app FastAPI.
Mantem app.py focado em criar FastAPI + middlewares + lifespan.

Imports top-level + flags _XXX_AVAILABLE preservados aqui (proximos
do uso). Imports dinamicos dentro de try/except mantem fail-soft em
producao (router individual pode quebrar sem derrubar o servico).
"""

from __future__ import annotations

from fastapi import FastAPI
import structlog as _structlog_for_inline

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
    _FUND_ANALYSIS_AVAILABLE = False

try:
    from finanalytics_ai.interfaces.api.routes import patrimony as patrimony_routes

    _PATRIMONY_AVAILABLE = True
except ImportError:
    _PATRIMONY_AVAILABLE = False

from finanalytics_ai.interfaces.api.routes import (
    accounts as accounts_routes,
    admin as admin_routes,
    admin_db as admin_db_routes,
    alerts,
    anomaly,
    backtest,
    correlation,
    dashboard,
    events,
    fixed_income,
    fundamental_analysis,
    fundos as fundos_routes,
    health,
    hub as hub_routes,
    marketdata as marketdata_routes,
    ml_forecasting as ml_routes,
    pairs as pairs_routes,
    performance,
    portfolio,
    predict_mvp as predict_mvp_routes,
    producer,
    quotes,
    reports,
    robot as robot_routes,
    screener,
    wallet,
    watchlist,
)


def register_routers(app: FastAPI, logger=None) -> None:
    """Registra todos os routers no app FastAPI.

    logger e parametro opcional pra log de fail-soft em routers que
    falham no import dinamico (mantem comportamento original do app.py).
    """
    if logger is None:
        logger = _structlog_for_inline.get_logger("api.routers")

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
    app.include_router(admin_db_routes.router, tags=["Admin DB Explorer"])
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
    try:
        from finanalytics_ai.interfaces.api.routes import fundos_analytics

        app.include_router(fundos_analytics.router)
        logger.info("fundos_analytics.router.ok")
    except Exception as _fae:
        logger.warning("fundos_analytics.router.FAILED", error=str(_fae))
    app.include_router(backtest.router, tags=["Backtest"])

    try:
        from finanalytics_ai.interfaces.api.routes import trading_engine as trading_engine_routes

        app.include_router(trading_engine_routes.router)
        logger.info("trading_engine.router.ok")
    except Exception as _te:
        logger.warning("trading_engine.router.SKIP", error=str(_te))

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
        from finanalytics_ai.interfaces.api.routes import rf_regime as rf_regime_routes

        app.include_router(rf_regime_routes.router)
        logger.info("rf_regime.router.ok")
    except Exception as _rfe:
        logger.warning("rf_regime.router.FAILED", error=str(_rfe))
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
    app.include_router(predict_mvp_routes.router, tags=["ML Probabilistico"])
    try:
        from finanalytics_ai.interfaces.api.routes import agent as agent_routes

        app.include_router(agent_routes.router, tags=["Agent"])
        logger.info("agent.route.registered")
    except Exception as _are:
        logger.warning("agent.route.FAILED", error=str(_are))

    try:
        from finanalytics_ai.interfaces.api.routes import indicators as indicators_routes

        app.include_router(indicators_routes.router, tags=["Indicadores Técnicos"])
        logger.info("indicators.route.registered")
    except Exception as _ire:
        logger.warning("indicators.route.FAILED", error=str(_ire))

    try:
        from finanalytics_ai.interfaces.api.routes import scanner as scanner_routes

        app.include_router(scanner_routes.router, tags=["Scanner Setups"])
        logger.info("scanner.route.registered")
    except Exception as _scre:
        logger.warning("scanner.route.FAILED", error=str(_scre))

    # ── Hub (Event Pipeline) ─────────────────────────────────────────────────
    app.include_router(hub_routes.router, tags=["Hub"])

    # events_admin: rotas dedicadas /api/v1/events/* (admin/master only via _require_admin)
    # — listagem dead-letter/failed e reprocess. Complementa o hub.py (que opera no
    # mesmo conjunto mas tem prefixo /hub e UI propria).
    from finanalytics_ai.interfaces.api.routes import events_admin as events_admin_routes

    app.include_router(events_admin_routes.router, tags=["Events Admin"])
    # FastAPI dependency_overrides espera async generator nu (sem @asynccontextmanager).
    # get_session em connection.py e' context manager — usar get_db_session (ja tem o shape certo).
    from finanalytics_ai.interfaces.api.dependencies import get_db_session

    app.dependency_overrides[hub_routes.get_db] = get_db_session

    app.include_router(marketdata_routes.router, prefix="/api/v1/marketdata", tags=["Market Data"])
    try:
        from finanalytics_ai.interfaces.api.routes import live_market as live_market_routes

        app.include_router(live_market_routes.router, tags=["Live Market Data"])
        logger.info("live_market.route.registered")
    except Exception as _lme:
        logger.warning("live_market.route.FAILED", error=str(_lme))
    app.include_router(anomaly.router, tags=["Anomaly"])
    app.include_router(reports.router, tags=["Reports"])
    app.include_router(robot_routes.router, tags=["Robot"])
    app.include_router(pairs_routes.router, tags=["Pairs"])
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

        app.include_router(
            fintz_data_routes.router, prefix="/api/v1/fintz", tags=["Fintz Histórico"]
        )
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

    # __file__ aqui e' startup/routers.py — sobe 1 nivel pra interfaces/api/static.
    # Bug introduzido em 01/mai durante extracao de register_routers (sessao limpeza):
    # pre-extracao __file__ era app.py em interfaces/api/, agora e' startup/.
    _static = pathlib.Path(__file__).parent.parent / "static"

    def _html(name: str) -> HTMLResponse:
        f = _static / name
        return HTMLResponse(
            f.read_text(encoding="utf-8") if f.exists() else f"<h1>{name} não encontrado</h1>",
            status_code=200 if f.exists() else 404,
        )

    from fastapi.responses import Response as _StaticResponse

    # Partials HTML compartilhados (sidebar, futuros componentes).
    # Extensao .html eh whitelist nominal para evitar exposicao de
    # paginas completas via /static/ (que tem rotas dedicadas).
    _ALLOWED_PARTIALS: set[str] = {"sidebar.html", "sw_kill.html"}

    @app.get("/static/{filename}", include_in_schema=False)
    async def serve_static_asset(filename: str) -> _StaticResponse:
        """Servidor minimal para assets em static/ (JS, CSS, partials HTML).

        Path traversal bloqueado por comparacao do parent resolvido.
        """
        is_partial = filename in _ALLOWED_PARTIALS
        if not (is_partial or filename.endswith((".js", ".css", ".svg", ".png", ".ico", ".json"))):
            return _StaticResponse(status_code=404)
        target = (_static / filename).resolve()
        if _static.resolve() not in target.parents and target.parent != _static.resolve():
            return _StaticResponse(status_code=404)
        if not target.is_file():
            return _StaticResponse(status_code=404)
        if filename.endswith(".js"):
            media = "application/javascript"
        elif filename.endswith(".css"):
            media = "text/css"
        elif filename.endswith(".svg"):
            media = "image/svg+xml"
        elif filename.endswith(".png"):
            media = "image/png"
        elif filename.endswith(".ico"):
            media = "image/x-icon"
        elif filename.endswith(".json"):
            media = "application/json"
        else:
            media = "text/html; charset=utf-8"
        # Cache-Control: assets sem versionamento por hash; TTL 1h para
        # reduzir requests em sessao (Sprint UI Y, 21/abr). Browser
        # revalida via If-Modified-Since em primeira request pos-TTL.
        # SVG mais agressivo (1d) — favicon raramente muda.
        max_age = 86400 if filename.endswith(".svg") else 3600
        return _StaticResponse(
            content=target.read_bytes(),
            media_type=media,
            headers={"Cache-Control": f"public, max-age={max_age}"},
        )

    # PWA — Sprint UI F (21/abr/2026): manifest e service worker
    # servidos no root para que SW tenha scope='/' (sem precisar
    # do header Service-Worker-Allowed). SW: cache-control: no-store
    # para que browser sempre veja a versao mais recente do scope/sw.js.
    @app.get("/manifest.json", include_in_schema=False)
    async def serve_manifest() -> _StaticResponse:
        target = (_static / "manifest.json").resolve()
        if not target.is_file():
            return _StaticResponse(status_code=404)
        return _StaticResponse(
            content=target.read_bytes(),
            media_type="application/manifest+json",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/sw.js", include_in_schema=False)
    async def serve_service_worker() -> _StaticResponse:
        target = (_static / "sw.js").resolve()
        if not target.is_file():
            return _StaticResponse(status_code=404)
        return _StaticResponse(
            content=target.read_bytes(),
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Service-Worker-Allowed": "/",
            },
        )

    @app.get("/carteira", response_class=HTMLResponse, include_in_schema=False)
    async def serve_carteira() -> HTMLResponse:
        return _html("carteira.html")

    @app.get("/overview", response_class=HTMLResponse, include_in_schema=False)
    async def serve_overview() -> HTMLResponse:
        return _html("overview.html")

    @app.get("/movimentacoes", response_class=HTMLResponse, include_in_schema=False)
    async def serve_movimentacoes() -> HTMLResponse:
        return _html("movimentacoes.html")

    @app.get("/alerts", response_class=HTMLResponse, include_in_schema=False)
    async def serve_alerts() -> HTMLResponse:
        return _html("alerts.html")

    @app.get("/portfolios", include_in_schema=False)
    async def serve_portfolios():
        # Refactor 25/abr: modelo simplificado para 1 portfolio por conta.
        # /portfolios deprecada — gerenciamento centralizado em /profile#invest.
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/profile#invest", status_code=302)

    @app.get("/fundos", response_class=HTMLResponse, include_in_schema=False)
    async def serve_fundos() -> HTMLResponse:
        return _html("fundos.html")

    @app.get("/daytrade/setups", response_class=HTMLResponse, include_in_schema=False)
    async def serve_daytrade_setups() -> HTMLResponse:
        return _html("daytrade_setups.html")

    @app.get("/daytrade/risco", response_class=HTMLResponse, include_in_schema=False)
    async def serve_daytrade_risco() -> HTMLResponse:
        return _html("daytrade_risco.html")

    @app.get("/opcoes/estrategias", response_class=HTMLResponse, include_in_schema=False)
    async def serve_opcoes_estrategias() -> HTMLResponse:
        return _html("opcoes_estrategias.html")

    @app.get("/opcoes/lancamento", response_class=HTMLResponse, include_in_schema=False)
    async def serve_opcoes_lancamento() -> HTMLResponse:
        return _html("opcoes_lancamento.html")

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

    @app.get("/trade-engine", response_class=HTMLResponse, include_in_schema=False)
    async def serve_trade_engine() -> HTMLResponse:
        return _html("trade-engine.html")

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

    @app.get("/robot", response_class=HTMLResponse, include_in_schema=False)
    async def serve_robot() -> HTMLResponse:
        return _html("robot.html")

    @app.get("/pairs", response_class=HTMLResponse, include_in_schema=False)
    async def serve_pairs() -> HTMLResponse:
        return _html("pairs.html")

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
