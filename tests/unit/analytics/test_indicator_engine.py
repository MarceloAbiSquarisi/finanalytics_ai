"""Tests for the indicator engine using synthetic data."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from finanalytics_ai.application.analytics.indicator_engine import (
    compute,
    compute_summary,
)
from finanalytics_ai.domain.analytics.exceptions import InsufficientDataError
from finanalytics_ai.domain.analytics.models import CandleData


def _make_candles(n: int, *, trend: float = 0.3, noise: float = 1.0) -> list[CandleData]:
    """Generate synthetic OHLCV candles with upward trend + noise."""
    rng = np.random.default_rng(42)
    base = 50.0
    candles = []
    for i in range(n):
        mid = base + trend * i + rng.normal(0, noise)
        o = mid + rng.normal(0, noise * 0.3)
        c = mid + rng.normal(0, noise * 0.3)
        h = max(o, c) + abs(rng.normal(0, noise * 0.5))
        lo = min(o, c) - abs(rng.normal(0, noise * 0.5))
        candles.append(
            CandleData(
                date=date(2026, 1, 2) + timedelta(days=i),
                open=round(o, 2),
                high=round(h, 2),
                low=round(lo, 2),
                close=round(c, 2),
                volume=float(rng.integers(1000, 100000)),
            )
        )
    return candles


def _make_descending_candles(n: int) -> list[CandleData]:
    """Generate candles with strongly descending prices."""
    rng = np.random.default_rng(123)
    candles = []
    for i in range(n):
        mid = 100.0 - 1.5 * i + rng.normal(0, 0.3)
        o = mid + 0.1
        c = mid - 0.1
        h = max(o, c) + 0.2
        lo = min(o, c) - 0.2
        candles.append(
            CandleData(
                date=date(2026, 1, 2) + timedelta(days=i),
                open=round(o, 2),
                high=round(h, 2),
                low=round(lo, 2),
                close=round(c, 2),
                volume=float(rng.integers(1000, 50000)),
            )
        )
    return candles


def _make_flat_candles(n: int, price: float = 50.0) -> list[CandleData]:
    """Generate flat candles with very low volatility."""
    rng = np.random.default_rng(99)
    candles = []
    for i in range(n):
        noise = rng.normal(0, 0.05)
        o = price + noise
        c = price + noise
        h = price + abs(noise) + 0.01
        lo = price - abs(noise) - 0.01
        candles.append(
            CandleData(
                date=date(2026, 1, 2) + timedelta(days=i),
                open=round(o, 4),
                high=round(h, 4),
                low=round(lo, 4),
                close=round(c, 4),
                volume=float(rng.integers(1000, 50000)),
            )
        )
    return candles


class TestEMAValues:
    def test_ema_values_correct(self):
        candles = _make_candles(300)
        results = compute(candles, min_candles=50, ticker="TEST")

        # EMA8 and EMA20 should be present for last candle
        last = results[-1]
        assert last.ema_8 is not None
        assert last.ema_20 is not None
        assert last.ema_80 is not None
        assert last.ema_200 is not None

        # EMA8 should be closer to current price than EMA200 (upward trend)
        assert abs(last.close - last.ema_8) < abs(last.close - last.ema_200)


class TestRSI:
    def test_rsi2_oversold_detected(self):
        candles = _make_descending_candles(60)
        results = compute(candles, min_candles=50, ticker="TEST")

        last = results[-1]
        assert last.rsi_2 is not None
        assert last.rsi_2 < 25, f"RSI2 should be < 25 for descending series, got {last.rsi_2}"


class TestInsufficientData:
    def test_insufficient_data_raises(self):
        candles = _make_candles(30)
        with pytest.raises(InsufficientDataError) as exc_info:
            compute(candles, min_candles=50, ticker="SHORT")

        assert exc_info.value.ticker == "SHORT"
        assert exc_info.value.required == 50
        assert exc_info.value.available == 30


class TestADX:
    def test_adx_trending(self):
        candles = _make_candles(300, trend=0.5, noise=0.5)
        results = compute(candles, min_candles=50, ticker="TREND")

        last = results[-1]
        assert last.adx_8 is not None
        assert last.adx_8 > 20, f"ADX should be > 20 for trending series, got {last.adx_8}"


class TestBollingerSqueeze:
    def test_bollinger_squeeze(self):
        candles = _make_flat_candles(100)
        results = compute(candles, min_candles=50, ticker="FLAT")

        last = results[-1]
        assert last.bb_upper is not None
        assert last.bb_lower is not None
        assert last.bb_middle is not None
        # Bandwidth should be narrow for flat series
        bandwidth = last.bb_upper - last.bb_lower
        assert bandwidth < 1.0, (
            f"Bollinger bandwidth should be < 1 for flat series, got {bandwidth}"
        )


class TestSummarySignals:
    def test_summary_signals_oversold(self):
        candles = _make_descending_candles(60)
        results = compute(candles, min_candles=50, ticker="TEST")
        signals = compute_summary(results[-1])

        assert signals.rsi2_sobrevendido is True
        assert signals.rsi2_sobrecomprado is False

    def test_summary_signals_trending_up(self):
        candles = _make_candles(300, trend=0.5, noise=0.3)
        results = compute(candles, min_candles=50, ticker="UP")
        signals = compute_summary(results[-1])

        assert signals.preco_acima_ema8 is True
        assert signals.preco_acima_ema20 is True
        assert signals.adx_trending is True
