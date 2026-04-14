"""
Candle repository — aggregates OHLCV from TimescaleDB with fallback chain.

Fallback order:
  1. profit_daily_bars  (pre-aggregated, fastest)
  2. market_history_trades  (tick-level, ~63 days of data)
  3. profit_ticks  (real-time ticks, ~16 days)
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import structlog

from finanalytics_ai.config import get_settings
from finanalytics_ai.domain.analytics.models import CandleData, HourlyVWAP
from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool

logger = structlog.get_logger(__name__)

# ── SQL queries ──────────────────────────────────────────────────────────────

_SQL_DAILY_BARS = """
SELECT time::date AS date, open, high, low, close, volume
FROM profit_daily_bars
WHERE ticker = $1 AND time >= $2
ORDER BY time
"""

_SQL_TRADES_OHLCV = """
SELECT
    trade_date::date AS date,
    (array_agg(price ORDER BY trade_date ASC))[1] AS open,
    MAX(price) AS high,
    MIN(price) AS low,
    (array_agg(price ORDER BY trade_date DESC))[1] AS close,
    SUM(quantity) AS volume
FROM market_history_trades
WHERE ticker = $1 AND trade_date >= $2
GROUP BY trade_date::date
ORDER BY date ASC
"""

_SQL_TICKS_OHLCV = """
SELECT
    time::date AS date,
    (array_agg(price ORDER BY time ASC))[1] AS open,
    MAX(price) AS high,
    MIN(price) AS low,
    (array_agg(price ORDER BY time DESC))[1] AS close,
    SUM(quantity) AS volume
FROM profit_ticks
WHERE ticker = $1 AND time >= $2
GROUP BY time::date
ORDER BY date ASC
"""

_SQL_INTRADAY_TICKS = """
SELECT
    time,
    price,
    COALESCE(quantity, 1) AS quantity
FROM profit_ticks
WHERE ticker = $1
  AND time::date = $2
  AND time::time >= $3
  AND time::time <= $4
ORDER BY time ASC
"""

_SQL_INTRADAY_TICKS_LATEST_DATE = """
SELECT MAX(time::date) AS last_date
FROM profit_ticks
WHERE ticker = $1
"""


def _rows_to_candles(rows: list[Any]) -> list[CandleData]:
    """Convert asyncpg rows to CandleData list."""
    return [
        CandleData(
            date=row["date"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]) if row["volume"] else 0.0,
        )
        for row in rows
    ]


async def fetch_candles(
    ticker: str,
    since: date | None = None,
) -> tuple[list[CandleData], str]:
    """
    Fetch OHLCV candles with fallback chain.

    Returns:
        Tuple of (candles, source_name).
        source_name is one of: "daily_bars", "market_history_trades", "profit_ticks"
    """
    pool = await get_timescale_pool()
    ticker_upper = ticker.upper()
    since_ts = datetime.combine(since, time.min) if since else datetime(2020, 1, 1)

    async with pool.acquire() as conn:
        # 1. Try profit_daily_bars
        try:
            rows = await conn.fetch(_SQL_DAILY_BARS, ticker_upper, since_ts)
            if rows:
                logger.debug("candle.source", ticker=ticker_upper, source="daily_bars", count=len(rows))
                return _rows_to_candles(rows), "daily_bars"
        except Exception:
            logger.debug("candle.daily_bars.unavailable", ticker=ticker_upper)

        # 2. Try market_history_trades
        try:
            rows = await conn.fetch(_SQL_TRADES_OHLCV, ticker_upper, since_ts)
            if rows:
                logger.debug(
                    "candle.source",
                    ticker=ticker_upper,
                    source="market_history_trades",
                    count=len(rows),
                )
                return _rows_to_candles(rows), "market_history_trades"
        except Exception:
            logger.debug("candle.trades.unavailable", ticker=ticker_upper)

        # 3. Try profit_ticks
        try:
            rows = await conn.fetch(_SQL_TICKS_OHLCV, ticker_upper, since_ts)
            if rows:
                logger.debug(
                    "candle.source", ticker=ticker_upper, source="profit_ticks", count=len(rows)
                )
                return _rows_to_candles(rows), "profit_ticks"
        except Exception:
            logger.debug("candle.ticks.unavailable", ticker=ticker_upper)

    return [], ""


async def fetch_intraday_ticks(
    ticker: str,
    target_date: date | None = None,
) -> tuple[list[dict[str, Any]], date | None, bool]:
    """
    Fetch intraday ticks for VWAP calculation.

    Returns:
        (ticks, actual_date, mercado_aberto)
        Each tick is {time: datetime, price: float, quantity: float}
    """
    settings = get_settings()
    market_open = time.fromisoformat(settings.analytics_vwap_market_open)
    market_close = time.fromisoformat(settings.analytics_vwap_market_close)

    pool = await get_timescale_pool()
    ticker_upper = ticker.upper()

    now = datetime.now()
    today = now.date()
    current_time = now.time()

    actual_date = target_date or today
    mercado_aberto = (
        actual_date == today and market_open <= current_time <= market_close
    )

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SQL_INTRADAY_TICKS,
            ticker_upper,
            actual_date,
            market_open,
            market_close,
        )

        # If no data for requested date and it's today outside market hours,
        # fall back to the last date with data
        if not rows and (actual_date == today or target_date is None):
            row = await conn.fetchrow(_SQL_INTRADAY_TICKS_LATEST_DATE, ticker_upper)
            if row and row["last_date"]:
                actual_date = row["last_date"]
                mercado_aberto = False
                rows = await conn.fetch(
                    _SQL_INTRADAY_TICKS,
                    ticker_upper,
                    actual_date,
                    market_open,
                    market_close,
                )

    ticks = [
        {
            "time": row["time"],
            "price": float(row["price"]),
            "quantity": float(row["quantity"]),
        }
        for row in rows
    ]

    return ticks, actual_date, mercado_aberto


def compute_vwap_from_ticks(
    ticks: list[dict[str, Any]],
) -> tuple[float | None, list[HourlyVWAP]]:
    """
    Compute global VWAP and hourly profile from intraday ticks.

    Returns:
        (global_vwap, hourly_profile)
    """
    if not ticks:
        return None, []

    total_pv = 0.0
    total_vol = 0.0
    hourly: dict[int, dict[str, float]] = {}

    for t in ticks:
        price = t["price"]
        qty = t["quantity"]
        pv = price * qty
        total_pv += pv
        total_vol += qty

        hour = t["time"].hour
        if hour not in hourly:
            hourly[hour] = {"pv": 0.0, "vol": 0.0, "count": 0}
        hourly[hour]["pv"] += pv
        hourly[hour]["vol"] += qty
        hourly[hour]["count"] += 1

    global_vwap = total_pv / total_vol if total_vol > 0 else None

    hourly_profile = [
        HourlyVWAP(
            hour=h,
            vwap=round(d["pv"] / d["vol"], 4) if d["vol"] > 0 else 0.0,
            volume=d["vol"],
            tick_count=int(d["count"]),
        )
        for h, d in sorted(hourly.items())
    ]

    return global_vwap, hourly_profile
