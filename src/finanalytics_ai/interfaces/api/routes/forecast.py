"""
Forecast routes.

GET  /api/v1/forecast/{ticker}   — runs ensemble forecast + narrative
GET  /api/v1/forecast/{ticker}/models — returns available models and status

Design:
  - ForecastService and NarrativeService are instantiated lazily on first
    call and cached on app.state to avoid cold-start per request.
  - Indicators are fetched in parallel with the historical data fetch
    to minimize latency.
  - Result is cached in-memory (TTL configurable via FORECAST_CACHE_TTL_SECONDS).
"""
from __future__ import annotations

import time
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from finanalytics_ai.config import get_settings
from finanalytics_ai.domain.value_objects.money import Ticker
from finanalytics_ai.interfaces.api.dependencies import get_brapi_client

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/forecast", tags=["Forecast"])

# Simple in-memory cache: key → (timestamp, result_dict)
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _get_forecast_service(request: Request) -> Any:
    svc = getattr(request.app.state, "forecast_service", None)
    if svc is None:
        from finanalytics_ai.application.services.forecast_service import ForecastService
        settings = get_settings()
        svc = ForecastService(data_dir=settings.data_dir)
        request.app.state.forecast_service = svc
    return svc


def _get_narrative_service(request: Request) -> Any:
    svc = getattr(request.app.state, "narrative_service", None)
    if svc is None:
        from finanalytics_ai.application.services.narrative_service import NarrativeService
        settings = get_settings()
        svc = NarrativeService(
            ollama_url=settings.ollama_url,
            ollama_model=settings.ollama_model,
            anthropic_api_key=settings.anthropic_api_key,
        )
        request.app.state.narrative_service = svc
    return svc


@router.get("/{ticker}")
async def run_forecast(
    ticker: str,
    request: Request,
    horizon: Annotated[int, Query(ge=5, le=90, description="Dias de forecast")] = 30,
    range_period: Annotated[str, Query(description="Histórico")] = "2y",
    models: Annotated[str, Query(description="all | prophet | lstm | tft")] = "all",
) -> dict[str, Any]:
    """
    Run ensemble price forecast for a ticker.

    Returns:
    - history: last 120 bars for chart rendering
    - forecast: ensemble prediction points with confidence interval
    - models: per-model MAPE, weight, and availability
    - signal: COMPRA | VENDA | NEUTRO
    - analysis: LLM narrative from Ollama/Claude
    """
    settings = get_settings()
    cache_key = f"{ticker.upper()}:{horizon}:{range_period}:{models}"
    ttl = settings.forecast_cache_ttl_seconds

    # Check cache
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if time.time() - ts < ttl:
            logger.info("forecast.cache_hit", ticker=ticker)
            return {**data, "cached": True}

    market_data = get_brapi_client()

    # Fetch historical bars — use 2y for enough training data
    try:
        bars = await market_data.get_ohlc_bars(
            Ticker(ticker.upper()), range_period=range_period, interval="1d"
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Erro ao buscar dados de {ticker}: {e}") from e

    if not bars or len(bars) < 60:
        raise HTTPException(
            status_code=422,
            detail=f"Dados insuficientes para {ticker} ({len(bars or [])} barras). Mínimo: 60 dias.",
        )

    # Fetch indicators in parallel for narrative context
    indicators: dict[str, Any] = {}
    try:
        from finanalytics_ai.domain.indicators.technical import compute_all
        ind = compute_all(bars)
        rsi_vals = [v for v in (ind.get("rsi", {}).get("values") or []) if v is not None]
        macd_data = ind.get("macd", {})
        bb_data = ind.get("bollinger", {})

        rsi_last = round(rsi_vals[-1], 1) if rsi_vals else None
        macd_hist = (macd_data.get("histogram") or [])
        macd_last = [h for h in macd_hist if h is not None]
        macd_signal_str = "Alta" if macd_last and macd_last[-1] > 0 else "Baixa"

        bb_pctb = (bb_data.get("pct_b") or [])
        bb_last = [v for v in bb_pctb if v is not None]
        if bb_last:
            pb = bb_last[-1]
            bb_pos = "Acima superior" if pb > 0.8 else ("Abaixo inferior" if pb < 0.2 else f"Central ({pb:.2f})")
        else:
            bb_pos = "N/A"

        indicators = {
            "rsi": str(rsi_last) if rsi_last else "N/A",
            "macd_signal": macd_signal_str,
            "bb_position": bb_pos,
        }
    except Exception as e:
        logger.warning("forecast.indicators.failed", error=str(e))

    # Run forecast
    forecast_svc = _get_forecast_service(request)
    try:
        result = await forecast_svc.forecast(
            ticker=ticker.upper(),
            bars=bars,
            horizon=horizon,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    # Generate narrative
    narrative_svc = _get_narrative_service(request)
    narrative_ctx = {
        "ticker": ticker.upper(),
        "last_price": result.last_price,
        "target_price": result.target_price,
        "change_pct": result.change_pct,
        "horizon_days": result.horizon_days,
        "ci_lower": result.ci_lower,
        "ci_upper": result.ci_upper,
        "models": ", ".join(k for k, v in result.models.items() if v.get("available")),
        "weights": str({k: v.get("weight", 0) for k, v in result.models.items() if v.get("available")}),
        **indicators,
        "patterns": "Não disponível nesta requisição",
    }

    narrative = await narrative_svc.analyze(narrative_ctx)
    result.signal = narrative.signal
    result.confidence = narrative.confidence
    result.analysis = f"{narrative.summary}\n\n{narrative.reasoning}\n\nRiscos: {narrative.risks}"
    result.narrative_provider = narrative.provider

    data = result.to_dict()
    _cache[cache_key] = (time.time(), data)

    logger.info(
        "forecast.complete",
        ticker=ticker,
        horizon=horizon,
        signal=result.signal,
        change_pct=result.change_pct,
        provider=result.narrative_provider,
    )
    return {**data, "cached": False}


@router.get("/{ticker}/models")
async def get_model_status(ticker: str, request: Request) -> dict[str, Any]:
    """Check which models are available without running a full forecast."""
    available: dict[str, Any] = {}
    for name, lib in [("prophet", "prophet"), ("lstm", "torch"), ("tft", "pytorch_forecasting")]:
        try:
            __import__(lib)
            available[name] = {"available": True}
        except ImportError:
            available[name] = {"available": False, "install": f"pip install {lib}"}

    import torch
    available["cuda"] = {
        "available": torch.cuda.is_available() if "torch" in dir() else False,
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
    } if available.get("lstm", {}).get("available") else {"available": False}

    return {"ticker": ticker.upper(), "models": available}
