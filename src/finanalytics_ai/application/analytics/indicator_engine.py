"""
Indicator Engine — computes 13 technical indicators via pandas-ta.

Converts list[CandleData] -> pandas DataFrame -> applies pandas-ta -> IndicatorResult list.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]

from finanalytics_ai.domain.analytics.exceptions import InsufficientDataError
from finanalytics_ai.domain.analytics.models import (
    CandleData,
    IndicatorResult,
    SetupSignal,
)

# ── Indicator definitions ────────────────────────────────────────────────────

INDICATORS: dict[str, dict[str, int]] = {
    "ema_8": {"min_candles": 8},
    "ema_20": {"min_candles": 20},
    "ema_80": {"min_candles": 80},
    "ema_200": {"min_candles": 200},
    "sma_9": {"min_candles": 9},
    "rsi_2": {"min_candles": 10},
    "rsi_9": {"min_candles": 10},
    "rsi_14": {"min_candles": 15},
    "adx_8": {"min_candles": 14},
    "atr_14": {"min_candles": 15},
    "atr_21": {"min_candles": 22},
    "bbands": {"min_candles": 20},
    "stoch": {"min_candles": 14},
}


def _candles_to_df(candles: list[CandleData]) -> pd.DataFrame:
    """Convert CandleData list to pandas DataFrame with OHLCV columns."""
    return pd.DataFrame(
        [
            {
                "date": c.date,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
    )


def compute(
    candles: list[CandleData],
    min_candles: int = 50,
    ticker: str = "",
) -> list[IndicatorResult]:
    """
    Compute all 13 indicators for the given candles.

    Args:
        candles: Sorted list of CandleData (oldest first).
        min_candles: Global minimum candles required. Raises InsufficientDataError if not met.
        ticker: Ticker name for error messages.

    Returns:
        List of IndicatorResult, one per candle.
    """
    n = len(candles)
    if n < min_candles:
        raise InsufficientDataError(ticker=ticker, required=min_candles, available=n)

    df = _candles_to_df(candles)

    # ── Compute each indicator ────────────────────────────────────────────
    # EMAs
    if n >= 8:
        df["ema_8"] = ta.ema(df["close"], length=8)
    if n >= 20:
        df["ema_20"] = ta.ema(df["close"], length=20)
    if n >= 80:
        df["ema_80"] = ta.ema(df["close"], length=80)
    if n >= 200:
        df["ema_200"] = ta.ema(df["close"], length=200)

    # SMA
    if n >= 9:
        df["sma_9"] = ta.sma(df["close"], length=9)

    # RSI
    if n >= 10:
        df["rsi_2"] = ta.rsi(df["close"], length=2)
    if n >= 10:
        df["rsi_9"] = ta.rsi(df["close"], length=9)
    if n >= 15:
        df["rsi_14"] = ta.rsi(df["close"], length=14)

    # ADX
    if n >= 14:
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=8)
        if adx_df is not None:
            df["adx_8"] = adx_df.iloc[:, 0]  # ADX column

    # ATR
    if n >= 15:
        df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    if n >= 22:
        df["atr_21"] = ta.atr(df["high"], df["low"], df["close"], length=21)

    # Bollinger Bands
    if n >= 20:
        bb = ta.bbands(df["close"], length=20, std=2)
        if bb is not None:
            df["bb_lower"] = bb.iloc[:, 0]
            df["bb_middle"] = bb.iloc[:, 1]
            df["bb_upper"] = bb.iloc[:, 2]

    # Stochastic
    if n >= 14:
        stoch = ta.stoch(df["high"], df["low"], df["close"], k=8, d=3, smooth_k=3)
        if stoch is not None:
            df["stoch_k"] = stoch.iloc[:, 0]
            df["stoch_d"] = stoch.iloc[:, 1]

    # ── Convert to IndicatorResult list ───────────────────────────────────
    results: list[IndicatorResult] = []
    for _, row in df.iterrows():
        results.append(
            IndicatorResult(
                date=row["date"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                ema_8=_safe_float(row.get("ema_8")),
                ema_20=_safe_float(row.get("ema_20")),
                ema_80=_safe_float(row.get("ema_80")),
                ema_200=_safe_float(row.get("ema_200")),
                sma_9=_safe_float(row.get("sma_9")),
                rsi_2=_safe_float(row.get("rsi_2")),
                rsi_9=_safe_float(row.get("rsi_9")),
                rsi_14=_safe_float(row.get("rsi_14")),
                adx_8=_safe_float(row.get("adx_8")),
                atr_14=_safe_float(row.get("atr_14")),
                atr_21=_safe_float(row.get("atr_21")),
                bb_upper=_safe_float(row.get("bb_upper")),
                bb_middle=_safe_float(row.get("bb_middle")),
                bb_lower=_safe_float(row.get("bb_lower")),
                stoch_k=_safe_float(row.get("stoch_k")),
                stoch_d=_safe_float(row.get("stoch_d")),
            )
        )
    return results


def compute_summary(result: IndicatorResult) -> SetupSignal:
    """
    Derive boolean signals from the latest IndicatorResult.
    """
    close = result.close
    return SetupSignal(
        rsi2_sobrevendido=result.rsi_2 is not None and result.rsi_2 < 25,
        rsi2_sobrecomprado=result.rsi_2 is not None and result.rsi_2 > 75,
        preco_acima_ema8=result.ema_8 is not None and close > result.ema_8,
        preco_acima_ema20=result.ema_20 is not None and close > result.ema_20,
        preco_acima_ema80=result.ema_80 is not None and close > result.ema_80,
        preco_abaixo_bb_lower=result.bb_lower is not None and close < result.bb_lower,
        preco_acima_bb_upper=result.bb_upper is not None and close > result.bb_upper,
        adx_trending=result.adx_8 is not None and result.adx_8 > 20,
        stoch_sobrevendido=result.stoch_k is not None and result.stoch_k < 20,
        stoch_sobrecomprado=result.stoch_k is not None and result.stoch_k > 80,
    )


def _safe_float(val: object) -> float | None:
    """Convert a pandas value to float, returning None for NaN/missing."""
    if val is None:
        return None
    try:
        f = float(val)
        if pd.isna(f):
            return None
        return round(f, 6)
    except (TypeError, ValueError):
        return None
