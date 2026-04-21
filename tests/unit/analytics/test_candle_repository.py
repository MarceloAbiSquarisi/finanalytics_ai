"""Tests for the candle repository with mocked asyncpg pool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from finanalytics_ai.domain.analytics.models import CandleData, HourlyVWAP
from finanalytics_ai.infrastructure.market_data.candle_repository import (
    compute_vwap_from_ticks,
    fetch_candles,
    fetch_intraday_ticks,
)


def _make_trade_rows(n: int = 5):
    """Create fake asyncpg rows for market_history_trades aggregation."""
    rows = []
    for i in range(n):
        d = date(2026, 3, 1) + timedelta(days=i)
        row = {
            "date": d,
            "open": 50.0 + i,
            "high": 52.0 + i,
            "low": 49.0 + i,
            "close": 51.0 + i,
            "volume": 10000 + i * 100,
        }
        rows.append(row)
    return rows


def _make_tick_rows():
    """Create fake intraday tick rows."""
    base_time = datetime(2026, 4, 11, 10, 0, 0)
    return [
        {"time": base_time + timedelta(minutes=i * 10), "price": 50.0 + i * 0.1, "quantity": 100.0}
        for i in range(10)
    ]


def _mock_pool(mock_conn):
    """Create a properly mocked asyncpg pool with async context manager support."""

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    pool = AsyncMock()
    pool.acquire = _acquire
    return pool


class TestFetchCandlesFallback:
    @pytest.mark.asyncio
    async def test_fallback_from_daily_bars_to_trades(self):
        """When daily_bars is empty, should fall back to market_history_trades."""
        mock_conn = AsyncMock()
        trade_rows = _make_trade_rows(5)

        # First call (daily_bars) returns empty, second call (trades) returns data
        mock_conn.fetch = AsyncMock(side_effect=[[], trade_rows])

        pool = _mock_pool(mock_conn)

        with patch(
            "finanalytics_ai.infrastructure.market_data.candle_repository.get_timescale_pool",
            new_callable=AsyncMock,
            return_value=pool,
        ):
            candles, source = await fetch_candles("PETR4", date(2026, 1, 1))

        assert source == "market_history_trades"
        assert len(candles) == 5
        assert isinstance(candles[0], CandleData)
        assert candles[0].open == 50.0

    @pytest.mark.asyncio
    async def test_no_data_returns_empty(self):
        """When all sources return empty, return empty list."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        pool = _mock_pool(mock_conn)

        with patch(
            "finanalytics_ai.infrastructure.market_data.candle_repository.get_timescale_pool",
            new_callable=AsyncMock,
            return_value=pool,
        ):
            candles, source = await fetch_candles("UNKNOWN", date(2026, 1, 1))

        assert candles == []
        assert source == ""


class TestVWAPComputation:
    def test_vwap_intraday_computation(self):
        """Test VWAP calculation from tick data."""
        ticks = _make_tick_rows()

        global_vwap, hourly = compute_vwap_from_ticks(ticks)

        assert global_vwap is not None
        assert global_vwap > 0
        assert len(hourly) >= 1
        assert isinstance(hourly[0], HourlyVWAP)
        assert hourly[0].tick_count > 0

    def test_vwap_empty_ticks(self):
        """Empty ticks should return None VWAP."""
        global_vwap, hourly = compute_vwap_from_ticks([])
        assert global_vwap is None
        assert hourly == []

    @pytest.mark.asyncio
    async def test_vwap_intraday_market_closed_fallback(self):
        """When no data for today, should fall back to last available date."""
        tick_rows = _make_tick_rows()

        mock_conn = AsyncMock()
        # First fetch (today): empty. Second fetch (fallback date): data
        mock_conn.fetch = AsyncMock(side_effect=[[], tick_rows])
        mock_conn.fetchrow = AsyncMock(return_value={"last_date": date(2026, 4, 10)})

        pool = _mock_pool(mock_conn)

        with patch(
            "finanalytics_ai.infrastructure.market_data.candle_repository.get_timescale_pool",
            new_callable=AsyncMock,
            return_value=pool,
        ):
            ticks, actual_date, mercado_aberto = await fetch_intraday_ticks("PETR4")

        assert actual_date == date(2026, 4, 10)
        assert mercado_aberto is False
        assert len(ticks) == 10
