"""finanalytics_ai.interfaces.api.routes.etf — Rotas REST para análise de ETFs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from starlette.requests import Request

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/etf", tags=["ETF"])


def _svc(request: Request):
    from finanalytics_ai.application.services.etf_service import ETFService

    market = getattr(request.app.state, "market_client", None)
    if market is None:
        raise HTTPException(503, "Market data client não disponível")
    return ETFService(market)


# ── Catálogo ──────────────────────────────────────────────────────────────────


@router.get("/catalog")
async def etf_catalog(
    category: str | None = Query(None),
) -> list[dict]:
    """Lista todos os ETFs do catálogo, opcionalmente filtrado por categoria."""
    from finanalytics_ai.domain.etf.entities import ETF_CATALOG

    etfs = ETF_CATALOG
    if category:
        etfs = [e for e in etfs if e.category.lower() == category.lower()]
    return [
        {
            "ticker": e.ticker,
            "name": e.name,
            "benchmark": e.benchmark,
            "category": e.category,
            "ter": e.ter,
            "currency": e.currency,
            "description": e.description,
        }
        for e in etfs
    ]


@router.get("/categories")
async def etf_categories() -> list[str]:
    from finanalytics_ai.domain.etf.entities import ETF_CATEGORIES

    return ETF_CATEGORIES


# ── Comparativo ───────────────────────────────────────────────────────────────


class CompareRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, max_length=10)
    period: str = Field(default="1y", pattern="^(3mo|6mo|1y|2y|5y)$")
    risk_free: float = Field(default=10.65, gt=0, description="CDI % a.a.")


@router.post("/compare")
async def compare_etfs(body: CompareRequest, request: Request) -> dict:
    """
    Compara N ETFs: retorno total, retorno anual, volatilidade, Sharpe,
    drawdown máximo, VaR 95%. Retorna também séries normalizadas (base 100)
    para gráfico de performance.
    """
    try:
        return await _svc(request).compare(
            tickers=body.tickers,
            period=body.period,
            risk_free=body.risk_free / 100,
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# ── Tracking Error ────────────────────────────────────────────────────────────


@router.get("/tracking-error/{ticker}")
async def tracking_error(
    ticker: str,
    period: str = Query(default="1y", pattern="^(3mo|6mo|1y|2y|5y)$"),
    request: Request = None,
) -> dict:
    """
    Tracking error do ETF vs benchmark definido no catálogo.
    Calcula: TE anualizado, tracking difference, correlação, beta, R², information ratio.
    """
    try:
        return await _svc(request).tracking_error(ticker, period)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# ── Correlação ────────────────────────────────────────────────────────────────


class CorrelationRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, max_length=12)
    period: str = Field(default="1y", pattern="^(3mo|6mo|1y|2y|5y)$")


@router.post("/correlation")
async def etf_correlation(body: CorrelationRequest, request: Request) -> dict:
    """
    Matriz de correlação entre ETFs.
    Retorna matriz NxN, pares mais/menos correlacionados.
    """
    try:
        return await _svc(request).correlation_heatmap(body.tickers, body.period)
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# ── Rebalanceamento ───────────────────────────────────────────────────────────


class RebalancePosition(BaseModel):
    ticker: str
    current_value: float = Field(..., ge=0)


class RebalanceRequest(BaseModel):
    positions: list[RebalancePosition]
    target_weights: dict[str, float]  # {ticker: weight_pct}
    new_contribution: float = Field(default=0.0, ge=0)


@router.post("/rebalance")
async def rebalance(body: RebalanceRequest, request: Request) -> dict:
    """
    Calcula rebalanceamento da carteira de ETFs.
    Retorna ações (COMPRAR/VENDER/MANTER) com valores em R$ e unidades aproximadas.
    """
    try:
        return await _svc(request).rebalance(
            positions=[p.model_dump() for p in body.positions],
            target_weights=body.target_weights,
            new_contribution=body.new_contribution,
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e
