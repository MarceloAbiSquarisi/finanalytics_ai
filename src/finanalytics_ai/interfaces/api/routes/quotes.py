"""
Rotas de cotações, histórico OHLC, indicadores técnicos e busca de ativos.

Endpoints:
  GET /quotes/{ticker}                 — Cotação atual
  GET /quotes/{ticker}/history         — Histórico OHLC (candlestick)
  GET /quotes/{ticker}/detail          — Dados completos (52w, volume, etc.)
  GET /quotes/{ticker}/indicators      — RSI + MACD + Bollinger calculados server-side
  GET /quotes?q=...                    — Busca de ativos
"""
from __future__ import annotations

from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
import structlog

from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.domain.value_objects.money import Ticker
from finanalytics_ai.domain.indicators.technical import compute_all
from finanalytics_ai.interfaces.api.dependencies import get_brapi_client

router = APIRouter()
logger = structlog.get_logger(__name__)

RangePeriod = Literal["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]


@router.get("/{ticker}/indicators")
async def get_indicators(
    ticker: str,
    range: RangePeriod = Query(default="3mo"),
    rsi_period: Annotated[int, Query(ge=2, le=50)]   = 14,
    macd_fast:  Annotated[int, Query(ge=2, le=50)]   = 12,
    macd_slow:  Annotated[int, Query(ge=3, le=200)]  = 26,
    macd_signal:Annotated[int, Query(ge=2, le=50)]   = 9,
    bb_period:  Annotated[int, Query(ge=2, le=200)]  = 20,
    bb_std:     Annotated[float, Query(ge=0.5, le=5)] = 2.0,
    brapi: BrapiClient = Depends(get_brapi_client),
) -> dict:
    """
    Retorna RSI, MACD e Bollinger Bands calculados server-side.

    Todos os arrays têm comprimento igual ao número de barras históricas.
    Índices de warmup contêm null — o frontend deve ignorá-los.

    Design decision: o cálculo é feito no servidor para:
      1) Manter lógica de negócio no backend (testável, versionável)
      2) Evitar duplicação de código em múltiplos clientes
      3) Permitir cache futura na camada de infraestrutura
    """
    t = Ticker(ticker)
    bars = await brapi.get_ohlc_bars(t, range_period=range)

    if not bars:
        return {
            "ticker": ticker.upper(),
            "range": range,
            "count": 0,
            "timestamps": [],
            "rsi": {"values": [], "overbought": 70.0, "oversold": 30.0, "period": rsi_period},
            "macd": {"macd": [], "signal": [], "histogram": [],
                     "fast": macd_fast, "slow": macd_slow, "signal_period": macd_signal},
            "bollinger": {"upper": [], "middle": [], "lower": [],
                          "bandwidth": [], "pct_b": [], "period": bb_period, "std_dev": bb_std},
        }

    result = compute_all(
        bars,
        rsi_period=rsi_period,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        bb_period=bb_period,
        bb_std=bb_std,
    )
    result["ticker"] = ticker.upper()
    result["range"]  = range

    logger.info(
        "indicators.computed",
        ticker=ticker.upper(),
        range=range,
        bars=len(bars),
        rsi_valid=sum(1 for v in result["rsi"]["values"] if v is not None),
    )
    return result


@router.get("/{ticker}/history")
async def get_history(
    ticker: str,
    range: RangePeriod = Query(default="3mo"),
    interval: str | None = Query(default=None),
    brapi: BrapiClient = Depends(get_brapi_client),
) -> dict:
    """Retorna barras OHLC para candlestick chart (TradingView Lightweight Charts)."""
    bars = await brapi.get_ohlc_bars(Ticker(ticker), range_period=range, interval=interval)
    return {"ticker": ticker.upper(), "range": range, "bars": bars, "count": len(bars)}


@router.get("/{ticker}/detail")
async def get_detail(
    ticker: str,
    brapi: BrapiClient = Depends(get_brapi_client),
) -> dict:
    return await brapi.get_quote_full(Ticker(ticker))


@router.get("/{ticker}")
async def get_quote(
    ticker: str,
    brapi: BrapiClient = Depends(get_brapi_client),
) -> dict:
    price = await brapi.get_quote(Ticker(ticker))
    return {"ticker": ticker.upper(), "price": str(price.amount), "currency": price.currency}


@router.get("")
async def search_assets(
    q: str = Query(..., min_length=1),
    brapi: BrapiClient = Depends(get_brapi_client),
) -> list[dict]:
    return await brapi.search_assets(q)
