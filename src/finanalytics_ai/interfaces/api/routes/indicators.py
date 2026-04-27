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


@router.get("/{ticker}/levels")
async def get_support_resistance(
    ticker: str,
    methods: str = Query(
        "swing,classic,williams",
        description="CSV: swing,classic,williams (subset livre)",
    ),
    lookback: int = Query(5, ge=2, le=30, description="Barras de cada lado p/ swing"),
    cluster_pct: float = Query(
        0.005, ge=0.0001, le=0.05, description="Tolerância de cluster (0.005 = 0.5%)"
    ),
    desde: date | None = Query(None, description="Data inicial (default: 1 ano)"),
):
    """Retorna níveis de suporte/resistência por 3 métodos.

    - **swing**: pivots de N barras (lookback) clusterizados por proximidade
    - **classic**: PP, R1-R3, S1-S3 a partir do high/low/close anterior
    - **williams**: fractais 5-barra (Bill Williams)

    Cada método retorna `Level{price, kind, strength, bar_index}`.
    `kind` ∈ {support, resistance, pivot}; `strength` indica força (toques).
    """
    from finanalytics_ai.domain.indicators.support_resistance import (
        compute_classic_pivots,
        compute_swing_levels,
        compute_williams_fractals,
    )

    requested = {m.strip().lower() for m in methods.split(",") if m.strip()}
    valid = {"swing", "classic", "williams"}
    if not requested or not (requested <= valid):
        raise HTTPException(
            400, f"methods inválido — use combinação de {sorted(valid)}"
        )

    since = desde or (date.today() - timedelta(days=365))
    candles, source = await fetch_candles(ticker, since)
    if not candles:
        raise HTTPException(status_code=404, detail=f"No data for {ticker.upper()}")

    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    closes = [float(c.close) for c in candles]
    timestamps = [c.date.isoformat() if hasattr(c.date, "isoformat") else str(c.date) for c in candles]

    result: dict = {
        "ticker": ticker.upper(),
        "source": source,
        "candle_count": len(candles),
        "last_close": round(closes[-1], 4) if closes else None,
        "methods": sorted(requested),
        "timestamps": timestamps,
    }

    if "swing" in requested:
        result["swing"] = compute_swing_levels(
            highs, lows, lookback=lookback, cluster_pct=cluster_pct
        )

    if "classic" in requested:
        if len(candles) < 2:
            result["classic"] = None
        else:
            # Usa penúltima barra como "período anterior" — projeta níveis pra última
            prev = candles[-2]
            result["classic"] = compute_classic_pivots(
                float(prev.high), float(prev.low), float(prev.close)
            )

    if "williams" in requested:
        result["williams"] = compute_williams_fractals(highs, lows)

    return result


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
        raise HTTPException(status_code=404, detail=f"No intraday data for {ticker.upper()}")

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
