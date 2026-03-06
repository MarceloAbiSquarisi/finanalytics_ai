"""
Rotas de cotações, histórico OHLC e busca de ativos.

Endpoints:
  GET /quotes/{ticker}           — Cotação atual
  GET /quotes/{ticker}/history   — Histórico OHLC (candlestick)
  GET /quotes/{ticker}/detail    — Dados completos (52w, volume, etc.)
  GET /quotes?q=...              — Busca de ativos
"""
from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, Depends, Query
from finanalytics_ai.infrastructure.adapters.brapi_client import BrapiClient
from finanalytics_ai.domain.value_objects.money import Ticker
from finanalytics_ai.interfaces.api.dependencies import get_brapi_client

router = APIRouter()

RangePeriod = Literal["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]


@router.get("/{ticker}/history")
async def get_history(
    ticker: str,
    range: RangePeriod = Query(default="3mo", description="Período: 1d|5d|1mo|3mo|6mo|1y|2y|5y|max"),
    interval: str | None = Query(default=None, description="Intervalo (auto se omitido)"),
    brapi: BrapiClient = Depends(get_brapi_client),
) -> dict:
    """
    Retorna barras OHLC para candlestick chart.
    Formato compatível com TradingView Lightweight Charts.
    """
    t = Ticker(ticker)
    bars = await brapi.get_ohlc_bars(t, range_period=range, interval=interval)
    return {
        "ticker": ticker.upper(),
        "range": range,
        "bars": bars,
        "count": len(bars),
    }


@router.get("/{ticker}/detail")
async def get_detail(
    ticker: str,
    brapi: BrapiClient = Depends(get_brapi_client),
) -> dict:
    """Dados completos do ativo: preço, variação, volume, 52w high/low."""
    t = Ticker(ticker)
    return await brapi.get_quote_full(t)


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
