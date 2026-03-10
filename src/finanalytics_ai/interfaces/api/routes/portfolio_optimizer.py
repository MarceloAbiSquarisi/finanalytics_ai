"""finanalytics_ai.interfaces.api.routes.portfolio_optimizer"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from starlette.requests import Request

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/optimizer", tags=["Portfolio Optimizer"])


def _svc(request: Request):
    from finanalytics_ai.application.services.portfolio_optimizer_service import (
        PortfolioOptimizerService,
    )

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(503, "Market data client não disponível")
    return PortfolioOptimizerService(market)


class BLView(BaseModel):
    ticker: str
    returns: float = Field(..., description="Retorno anual esperado (decimal, ex: 0.15 = 15%)")


class OptimizeRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, max_length=15)
    period: str = Field(default="1y", pattern="^(3mo|6mo|1y|2y|5y)$")
    risk_free: float = Field(default=10.65, gt=0, description="CDI % a.a.")
    views: list[BLView] = Field(default_factory=list, description="Visões Black-Litterman")
    rf_tickers: list[str] = Field(
        default_factory=list, description="Tickers tratados como Renda Fixa"
    )
    bl_tau: float = Field(default=0.05, gt=0, le=0.5)
    bl_risk_aversion: float = Field(default=3.0, gt=0, le=10.0)


@router.post("/optimize")
async def optimize_portfolio(body: OptimizeRequest, request: Request) -> dict:
    """
    Executa Markowitz, Risk Parity e Black-Litterman para a lista de ativos.
    Retorna pesos ótimos, métricas e fronteira eficiente para cada método.
    """
    try:
        return await _svc(request).optimize(
            tickers=body.tickers,
            period=body.period,
            risk_free=body.risk_free / 100,
            views=[{"ticker": v.ticker, "return": v.returns} for v in body.views],
            rf_tickers=body.rf_tickers,
            bl_tau=body.bl_tau,
            bl_risk_aversion=body.bl_risk_aversion,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        logger.error("optimizer.error", error=str(e))
        raise HTTPException(500, f"Erro na otimização: {e}") from e


@router.get("/presets")
async def optimizer_presets() -> list[dict]:
    """Sugestões de carteiras predefinidas para usar na otimização."""
    return [
        {
            "name": "ETFs Diversificados",
            "tickers": ["BOVA11", "IVVB11", "SMAL11", "NTNB11", "GOLD11"],
            "description": "Ações BR + EUA + RF + Ouro",
        },
        {
            "name": "ETFs + Cripto",
            "tickers": ["BOVA11", "IVVB11", "NTNB11", "HASH11", "GOLD11"],
            "description": "Diversificação com exposição cripto",
        },
        {
            "name": "Blue Chips BR",
            "tickers": ["VALE3", "PETR4", "ITUB4", "BBDC4", "WEGE3", "ABEV3"],
            "description": "Maiores empresas do Ibovespa",
        },
        {
            "name": "Global + RF",
            "tickers": ["IVVB11", "ACWI11", "BOVA11", "NTNB11", "IRFM11"],
            "description": "Exposição global com proteção de RF",
        },
        {
            "name": "Renda Variável Pura",
            "tickers": ["BOVA11", "IVVB11", "SMAL11", "ACWI11", "HASH11"],
            "description": "Alto risco / alto retorno potencial",
        },
    ]
