"""
Rotas de correlacao entre ativos.

GET  /api/v1/correlation   — calcula via query params (ticker=A&ticker=B&...)
POST /api/v1/correlation   — calcula via body JSON
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from finanalytics_ai.application.services.backtest_service import BacktestError
from finanalytics_ai.application.services.correlation_service import (
    MAX_TICKERS,
    CorrelationService,
)
from finanalytics_ai.infrastructure.cache.dependencies import cached_route, rate_limit

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/correlation", tags=["Correlation"])


class CorrelationRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, max_length=MAX_TICKERS)
    range_period: str = Field("1y")
    rolling_window: int = Field(30, ge=5, le=120)


def _get_service(request: Request) -> CorrelationService:
    svc = getattr(request.app.state, "correlation_service", None)
    if svc is None:
        raise HTTPException(503, "CorrelationService nao inicializado")
    return svc


@router.post("")
async def compute_correlation(
    body: CorrelationRequest,
    request: Request,
    response: Response,
    _rl: None = Depends(rate_limit(limit=15, window=60)),
) -> dict[str, Any]:
    """
    Calcula matriz de correlacao de Pearson entre multiplos ativos.

    Retorna:
      - matrix:       NxN correlacoes de Pearson (-1 a 1)
      - rolling_pairs: correlacao rolante por par {A/B: [{time, correlation}]}
      - most_correlated / least_correlated: top-3 pares
      - diversification_score: 0-1 (maior = mais diversificado)
    """
    svc = _get_service(request)
    try:
        result = await svc.compute(
            tickers=body.tickers,
            range_period=body.range_period,
            rolling_window=body.rolling_window,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        logger.error("correlation.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno na analise de correlacao")


@router.get("")
@cached_route(ttl=180, prefix="correlation")
async def compute_correlation_get(
    request: Request,
    response: Response,
    tickers: str = Query(..., description="Tickers separados por virgula: PETR4,VALE3,ITUB4"),
    range_period: str = Query("1y"),
    rolling_window: int = Query(30),
    _rl: None = Depends(rate_limit(limit=20, window=60)),
) -> dict[str, Any]:
    """Calcula correlacao via GET — tickers como string separada por virgula."""
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    svc = _get_service(request)
    try:
        result = await svc.compute(
            tickers=ticker_list,
            range_period=range_period,
            rolling_window=rolling_window,
        )
        return result.to_dict()
    except BacktestError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        logger.error("correlation.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno na analise de correlacao")
