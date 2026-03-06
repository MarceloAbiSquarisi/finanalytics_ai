"""
FastAPI application factory.

Design decision: create_app() como factory permite criar instâncias
diferentes para testes e produção sem estado global.
Lifespan gerencia startup/shutdown de recursos (engine, conexões).
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from finanalytics_ai.config import get_settings
from finanalytics_ai.exceptions import FinAnalyticsError
from finanalytics_ai.interfaces.api.routes import portfolio, quotes, health
from finanalytics_ai.interfaces.api.routes import dashboard
from finanalytics_ai.infrastructure.database.connection import get_engine

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("api.starting")
    # Força criação do engine no startup
    get_engine()
    yield
    from finanalytics_ai.infrastructure.database.connection import close_engine
    await close_engine()
    logger.info("api.stopped")


def create_app() -> FastAPI:
    settings = get_settings()

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
        allow_origins=["*"],  # Em prod: restrinja para o domínio do dashboard
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Tratamento global de exceções do domínio
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
        status = status_map.get(exc.code, 400)
        return JSONResponse(
            status_code=status,
            content={"error": exc.code, "message": exc.message, "context": exc.context},
        )

    app.include_router(dashboard.router, tags=["Dashboard"])
    app.include_router(health.router, tags=["Health"])
    app.include_router(portfolio.router, prefix="/api/v1/portfolios", tags=["Portfolio"])
    app.include_router(quotes.router, prefix="/api/v1/quotes", tags=["Cotações"])

    return app

# Static files served directly (dashboard fallback)
from fastapi.responses import HTMLResponse
import pathlib

def mount_static(app: FastAPI) -> None:
    static_dir = pathlib.Path(__file__).parent / "static"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def serve_dashboard() -> HTMLResponse:
        html_file = static_dir / "dashboard.html"
        if not html_file.exists():
            return HTMLResponse("<h1>Dashboard não encontrado</h1>", status_code=404)
        return HTMLResponse(html_file.read_text(encoding="utf-8"))