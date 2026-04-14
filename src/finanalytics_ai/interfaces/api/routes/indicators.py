"""
Indicators API — 3 endpoints for technical indicator computation.

GET /api/v1/indicators/{ticker}
GET /api/v1/indicators/{ticker}/summary
GET /api/v1/indicators/{ticker}/vwap/intraday
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
import structlog

from finanalytics_ai.application.analytics import indicator_engine
from finanalytics_ai.config import get_settings
from finanalytics_ai.domain.analytics.exceptions import InsufficientDataError
from finanalytics_ai.infrastructure.market_data.candle_repository import (
    compute_vwap_from_ticks,
    fetch_candles,
    fetch_intraday_ticks,
)
from finanalytics_ai.interfaces.api.routes.indicator_schemas import (
    CandleWithIndicators,
    HourlyVWAPSchema,
    IndicatorResponse,
    IndicatorSummaryResponse,
    SignalSummary,
    VWAPResponse,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/indicators")

_FUTURES = {"WDOFUT", "WINFUT", "DOLFUT", "INDFUT"}


def _ticker_tipo(ticker: str) -> str:
    return "futuro" if ticker.upper() in _FUTURES else "acao"


@router.get("/{ticker}", response_model=IndicatorResponse)
async def get_indicators(
    ticker: str,
    desde: date | None = Query(default=None, description="Data inicial (YYYY-MM-DD)"),
    timeframe: str = Query(default="daily"),
):
    """Compute indicators for all available candles of a ticker."""
    settings = get_settings()
    since = desde or (date.today() - timedelta(days=365))

    candles, source = await fetch_candles(ticker, since)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No data for {ticker.upper()}")

    try:
        results = indicator_engine.compute(
            candles,
            min_candles=settings.analytics_min_candles,
            ticker=ticker.upper(),
        )
    except InsufficientDataError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "InsufficientDataError",
                "ticker": e.ticker,
                "required": e.required,
                "available": e.available,
                "message": str(e),
            },
        ) from e

    candle_list = [
        CandleWithIndicators(
            date=r.date,
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
            ema_8=r.ema_8,
            ema_20=r.ema_20,
            ema_80=r.ema_80,
            ema_200=r.ema_200,
            sma_9=r.sma_9,
            rsi_2=r.rsi_2,
            rsi_9=r.rsi_9,
            rsi_14=r.rsi_14,
            adx_8=r.adx_8,
            atr_14=r.atr_14,
            atr_21=r.atr_21,
            bb_upper=r.bb_upper,
            bb_middle=r.bb_middle,
            bb_lower=r.bb_lower,
            stoch_k=r.stoch_k,
            stoch_d=r.stoch_d,
        )
        for r in results
    ]

    return IndicatorResponse(
        ticker=ticker.upper(),
        source=source,
        timeframe=timeframe,
        candle_count=len(candle_list),
        candles=candle_list,
    )


@router.get("/{ticker}/summary", response_model=IndicatorSummaryResponse)
async def get_indicator_summary(ticker: str):
    """Return the latest candle with indicators and boolean signals."""
    settings = get_settings()
    since = date.today() - timedelta(days=365)

    candles, source = await fetch_candles(ticker, since)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No data for {ticker.upper()}")

    try:
        results = indicator_engine.compute(
            candles,
            min_candles=settings.analytics_min_candles,
            ticker=ticker.upper(),
        )
    except InsufficientDataError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "InsufficientDataError",
                "ticker": e.ticker,
                "required": e.required,
                "available": e.available,
                "message": str(e),
            },
        ) from e

    last = results[-1]
    signals = indicator_engine.compute_summary(last)

    last_candle = CandleWithIndicators(
        date=last.date,
        open=last.open,
        high=last.high,
        low=last.low,
        close=last.close,
        volume=last.volume,
        ema_8=last.ema_8,
        ema_20=last.ema_20,
        ema_80=last.ema_80,
        ema_200=last.ema_200,
        sma_9=last.sma_9,
        rsi_2=last.rsi_2,
        rsi_9=last.rsi_9,
        rsi_14=last.rsi_14,
        adx_8=last.adx_8,
        atr_14=last.atr_14,
        atr_21=last.atr_21,
        bb_upper=last.bb_upper,
        bb_middle=last.bb_middle,
        bb_lower=last.bb_lower,
        stoch_k=last.stoch_k,
        stoch_d=last.stoch_d,
    )

    return IndicatorSummaryResponse(
        ticker=ticker.upper(),
        tipo=_ticker_tipo(ticker),
        source=source,
        last_candle=last_candle,
        signals=SignalSummary(
            rsi2_sobrevendido=signals.rsi2_sobrevendido,
            rsi2_sobrecomprado=signals.rsi2_sobrecomprado,
            preco_acima_ema8=signals.preco_acima_ema8,
            preco_acima_ema20=signals.preco_acima_ema20,
            preco_acima_ema80=signals.preco_acima_ema80,
            preco_abaixo_bb_lower=signals.preco_abaixo_bb_lower,
            preco_acima_bb_upper=signals.preco_acima_bb_upper,
            adx_trending=signals.adx_trending,
            stoch_sobrevendido=signals.stoch_sobrevendido,
            stoch_sobrecomprado=signals.stoch_sobrecomprado,
        ),
    )


@router.get("/{ticker}/vwap/intraday", response_model=VWAPResponse)
async def get_vwap_intraday(
    ticker: str,
    date_param: date | None = Query(default=None, alias="date", description="Data (YYYY-MM-DD)"),
):
    """
    Compute intraday VWAP with hourly profile.

    Outside market hours returns VWAP of the last trading day with data.
    """
    ticks, actual_date, mercado_aberto = await fetch_intraday_ticks(ticker, date_param)

    if not ticks or actual_date is None:
        raise HTTPException(
            status_code=404, detail=f"No intraday data for {ticker.upper()}"
        )

    global_vwap, hourly_profile = compute_vwap_from_ticks(ticks)

    return VWAPResponse(
        ticker=ticker.upper(),
        date=actual_date,
        vwap=round(global_vwap, 4) if global_vwap else None,
        mercado_aberto=mercado_aberto,
        hourly_profile=[
            HourlyVWAPSchema(
                hour=h.hour,
                vwap=round(h.vwap, 4),
                volume=h.volume,
                tick_count=h.tick_count,
            )
            for h in hourly_profile
        ],
    )
