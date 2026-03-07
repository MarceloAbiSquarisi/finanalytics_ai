"""
Rotas de deteccao de anomalias.

POST /api/v1/anomaly/scan       — escaneia N tickers
GET  /api/v1/anomaly/scan       — escaneia via query string
GET  /api/v1/anomaly/scan/{tk}  — escaneia ticker unico
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from finanalytics_ai.application.services.anomaly_service import (
    MAX_TICKERS,
    AnomalyService,
)
from finanalytics_ai.domain.anomaly.engine import DetectorConfig
from finanalytics_ai.infrastructure.cache.dependencies import (
    cached_route,
    rate_limit,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/anomaly", tags=["Anomaly"])


class ScanRequest(BaseModel):
    tickers:          list[str] = Field(..., min_length=1, max_length=MAX_TICKERS)
    range_period:     str   = Field("3mo")
    zscore_threshold: float = Field(2.5, ge=1.0, le=5.0)
    bollinger_k:      float = Field(2.0, ge=1.0, le=4.0)
    volume_multiplier: float = Field(3.0, ge=1.5, le=10.0)
    lookback_bars:    int   = Field(100, ge=30, le=500)


def _get_service(request: Request) -> AnomalyService:
    svc = getattr(request.app.state, "anomaly_service", None)
    if svc is None:
        raise HTTPException(503, "AnomalyService nao inicializado")
    return svc


def _make_config(body: ScanRequest) -> DetectorConfig:
    return DetectorConfig(
        zscore_threshold   = body.zscore_threshold,
        bollinger_k        = body.bollinger_k,
        volume_multiplier  = body.volume_multiplier,
        lookback_bars      = body.lookback_bars,
    )


@router.post("/scan")
async def scan_anomalies(
    body:    ScanRequest,
    request: Request,
    response: Response,
    _rl: None = Depends(rate_limit(limit=10, window=60)),
) -> dict[str, Any]:
    """
    Detecta anomalias de mercado em N ativos usando 4 algoritmos:
      - Z-Score sobre retornos (spikes de volatilidade)
      - Bollinger Band breakout/breakdown
      - CUSUM (mudanca estrutural de tendencia)
      - Volume Spike (volume anomalo)

    Retorna anomalias ordenadas por severidade (HIGH > MEDIUM > LOW).
    """
    svc = _get_service(request)
    try:
        result = await svc.scan(
            tickers      = body.tickers,
            range_period = body.range_period,
            config       = _make_config(body),
        )
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        logger.error("anomaly.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno na deteccao de anomalias")


@router.get("/scan")
@cached_route(ttl=120, prefix="anomaly_scan")
async def scan_anomalies_get(
    request:          Request,
    response:         Response,
    tickers:          str   = Query(..., description="Tickers separados por virgula"),
    range_period:     str   = Query("3mo"),
    zscore_threshold: float = Query(2.5),
    volume_multiplier: float = Query(3.0),
    _rl: None = Depends(rate_limit(limit=15, window=60)),
) -> dict[str, Any]:
    """Scan via GET — tickers como string separada por virgula."""
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    svc = _get_service(request)
    config = DetectorConfig(
        zscore_threshold  = zscore_threshold,
        volume_multiplier = volume_multiplier,
    )
    try:
        result = await svc.scan(ticker_list, range_period, config)
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        logger.error("anomaly.unexpected_error", error=str(exc))
        raise HTTPException(500, "Erro interno na deteccao de anomalias")


@router.get("/scan/{ticker}")
async def scan_single(
    ticker:       str,
    request:      Request,
    range_period: str = Query("3mo"),
) -> dict[str, Any]:
    """Detecta anomalias para um unico ticker."""
    svc = _get_service(request)
    try:
        result = await svc.scan_single(ticker.upper(), range_period)
        return result.to_dict()
    except Exception as exc:
        logger.error("anomaly.unexpected_error", ticker=ticker, error=str(exc))
        raise HTTPException(500, "Erro interno na deteccao de anomalias")
