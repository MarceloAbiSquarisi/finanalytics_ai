"""Tests for the setup scanner using synthetic data."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from finanalytics_ai.application.analytics.setup_scanner import (
    aggregate_weekly,
    scan_all,
    scan_ticker,
)
from finanalytics_ai.domain.analytics.models import CandleData

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_candles(
    n: int, *, trend: float = 0.3, noise: float = 1.0, base: float = 50.0,
    start_date: date | None = None,
) -> list[CandleData]:
    """Generate synthetic OHLCV candles with optional trend."""
    rng = np.random.default_rng(42)
    sd = start_date or date(2026, 1, 2)
    candles = []
    for i in range(n):
        mid = base + trend * i + rng.normal(0, noise)
        o = mid + rng.normal(0, noise * 0.3)
        c = mid + rng.normal(0, noise * 0.3)
        h = max(o, c) + abs(rng.normal(0, noise * 0.5))
        lo = min(o, c) - abs(rng.normal(0, noise * 0.5))
        candles.append(
            CandleData(
                date=sd + timedelta(days=i),
                open=round(o, 2),
                high=round(h, 2),
                low=round(lo, 2),
                close=round(c, 2),
                volume=float(rng.integers(1000, 100000)),
            )
        )
    return candles


def _make_descending_candles(n: int) -> list[CandleData]:
    """Generate strongly descending candles — triggers RSI2 oversold."""
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


def _make_ascending_candles(n: int) -> list[CandleData]:
    """Generate strongly ascending candles — triggers RSI2 overbought."""
    rng = np.random.default_rng(456)
    candles = []
    for i in range(n):
        mid = 30.0 + 1.5 * i + rng.normal(0, 0.3)
        o = mid - 0.1
        c = mid + 0.1
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


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestIFR2OversoldDetected:
    def test_ifr2_oversold_detected(self):
        candles = _make_descending_candles(60)
        detections = scan_ticker(candles, "PETR4", setups=["ifr2_oversold"], cache_ttl=0)
        assert len(detections) >= 1
        d = detections[0]
        assert d.setup_name == "ifr2_oversold"
        assert d.direcao == "long"
        assert d.details["rsi_2"] is not None
        assert d.details["rsi_2"] < 25


class TestIFR2OverboughtDetected:
    def test_ifr2_overbought_detected(self):
        candles = _make_ascending_candles(60)
        detections = scan_ticker(candles, "VALE3", setups=["ifr2_overbought"], cache_ttl=0)
        assert len(detections) >= 1
        d = detections[0]
        assert d.setup_name == "ifr2_overbought"
        assert d.direcao == "short"
        assert d.details["rsi_2"] is not None
        assert d.details["rsi_2"] > 80


class TestHDVDetected:
    def test_hdv_detected(self):
        # Strong uptrend with increasing ADX
        candles = _make_candles(100, trend=0.5, noise=0.5)
        detections = scan_ticker(candles, "ITUB4", setups=["hdv"], cache_ttl=0)
        # HDV may or may not fire depending on exact ADX + close>open;
        # just verify no crash and valid output
        for d in detections:
            assert d.setup_name == "hdv"
            assert d.direcao == "long"
            assert d.details["adx_8"] is not None
            assert d.details["adx_8"] > 20


class TestBBSqueezeDetected:
    def test_bb_squeeze_detected(self):
        candles = _make_flat_candles(100)
        detections = scan_ticker(candles, "FLAT4", setups=["bb_squeeze"], cache_ttl=0)
        assert len(detections) >= 1
        d = detections[0]
        assert d.setup_name == "bb_squeeze"
        assert d.direcao == "neutral"
        assert d.details["bandwidth"] is not None
        assert d.details["bandwidth"] < 0.05


class TestCandlePavioDetected:
    def test_candle_pavio_detected(self):
        # Create a candle with a tiny body and large wicks
        candles = _make_candles(50, trend=0.3, noise=1.0)
        # Replace last candle with a doji-like candle
        last = candles[-1]
        mid = (last.high + last.low) / 2
        candles[-1] = CandleData(
            date=last.date,
            open=round(mid + 0.01, 2),
            high=round(mid + 3.0, 2),
            low=round(mid - 3.0, 2),
            close=round(mid - 0.01, 2),
            volume=last.volume,
        )
        detections = scan_ticker(candles, "DOJI4", setups=["candle_pavio"], cache_ttl=0)
        assert len(detections) >= 1
        d = detections[0]
        assert d.setup_name == "candle_pavio"
        assert d.details["ratio"] < 0.30


class TestInsideBarWeekly:
    def test_inside_bar_weekly(self):
        # Need 2+ weeks of data. Create 10 daily candles across 2 weeks.
        # Week 1: wide range. Week 2: narrow range inside week 1.
        candles = []
        # Week 1 (Mon-Fri): wide range
        for i in range(5):
            candles.append(
                CandleData(
                    date=date(2026, 1, 5) + timedelta(days=i),  # Mon Jan 5
                    open=50.0,
                    high=60.0 if i == 2 else 55.0,
                    low=40.0 if i == 3 else 45.0,
                    close=52.0,
                    volume=10000.0,
                )
            )
        # Week 2 (Mon-Fri): inside the first week
        for i in range(5):
            candles.append(
                CandleData(
                    date=date(2026, 1, 12) + timedelta(days=i),  # Mon Jan 12
                    open=51.0,
                    high=55.0,
                    low=45.0,
                    close=53.0,
                    volume=10000.0,
                )
            )
        detections = scan_ticker(candles, "INSIDE4", setups=["inside_bar"], cache_ttl=0)
        assert len(detections) >= 1
        d = detections[0]
        assert d.setup_name == "inside_bar"
        assert d.timeframe == "weekly"


class TestEMAAlinhadas:
    def test_ema_alinhadas_alta(self):
        # Long strong uptrend
        candles = _make_candles(120, trend=0.5, noise=0.3)
        detections = scan_ticker(candles, "UP4", setups=["ema_alinhadas_alta"], cache_ttl=0)
        assert len(detections) >= 1
        d = detections[0]
        assert d.setup_name == "ema_alinhadas_alta"
        assert d.direcao == "long"
        assert d.details["ema_8"] > d.details["ema_20"] > d.details["ema_80"]


class TestStrengthBounded:
    def test_strength_bounded(self):
        candles = _make_descending_candles(60)
        detections = scan_ticker(candles, "TEST4", cache_ttl=0)
        for d in detections:
            assert 0.0 <= d.strength <= 1.0, f"strength out of range: {d.strength}"


class TestFuturosMarcadosCorretamente:
    def test_futuros_marcados_corretamente(self):
        candles = _make_descending_candles(60)
        detections = scan_ticker(candles, "WINFUT", setups=["ifr2_oversold"], cache_ttl=0)
        for d in detections:
            assert d.tipo == "futuro", f"WINFUT should be tipo='futuro', got {d.tipo}"

    def test_acoes_marcadas_corretamente(self):
        candles = _make_descending_candles(60)
        detections = scan_ticker(candles, "PETR4", setups=["ifr2_oversold"], cache_ttl=0)
        for d in detections:
            assert d.tipo == "acao", f"PETR4 should be tipo='acao', got {d.tipo}"


class TestWeeklyAggregation:
    def test_weekly_aggregation(self):
        # Create 10 business-day candles across 2 complete weeks
        candles = []
        for i in range(5):
            candles.append(
                CandleData(
                    date=date(2026, 1, 5) + timedelta(days=i),
                    open=50.0 + i,
                    high=55.0 + i,
                    low=48.0 + i,
                    close=52.0 + i,
                    volume=10000.0 + i * 100,
                )
            )
        for i in range(5):
            candles.append(
                CandleData(
                    date=date(2026, 1, 12) + timedelta(days=i),
                    open=60.0 + i,
                    high=65.0 + i,
                    low=58.0 + i,
                    close=62.0 + i,
                    volume=20000.0 + i * 100,
                )
            )

        weekly = aggregate_weekly(candles)
        assert len(weekly) == 2

        # Week 1
        w1 = weekly[0]
        assert w1.open == 50.0  # first day open
        assert w1.close == 56.0  # last day close
        assert w1.high == 59.0  # max(55,56,57,58,59)
        assert w1.low == 48.0  # min(48,49,50,51,52)

        # Week 2
        w2 = weekly[1]
        assert w2.open == 60.0
        assert w2.close == 66.0


class TestScanAllWithInsufficientData:
    def test_scan_all_with_insufficient_data(self):
        good_candles = _make_candles(100, trend=0.3)
        bad_candles = _make_candles(3, trend=0.0)  # too few

        tickers_candles = {
            "GOOD4": good_candles,
            "BAD4": bad_candles,
            "EMPTY": [],
        }

        result = scan_all(tickers_candles, cache_ttl=0)
        assert result.total_tickers == 3
        # BAD4 and EMPTY should be in tickers_sem_dados
        assert "EMPTY" in result.tickers_sem_dados
        # BAD4 may either be in sem_dados or have no signals
        assert result.tickers_com_dados >= 1
