"""
Resampled OHLC repository — le ohlc_resampled (agregado de ohlc_1m).

Quando ohlc_resampled estiver vazio para o intervalo solicitado, faz
agregacao on-the-fly via time_bucket (mais lento mas idempotente).
Permite que clientes consumam sem precisar pre-materializar tudo.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import structlog

from finanalytics_ai.infrastructure.timescale.repository import get_timescale_pool

logger = structlog.get_logger(__name__)


_SQL_FETCH_MATERIALIZED = """
SELECT time, open, high, low, close, volume, trades, vwap, source
  FROM ohlc_resampled
 WHERE ticker = $1 AND interval_minutes = $2 AND time >= $3
 ORDER BY time
"""

# Fallback on-the-fly: agrega a partir de ohlc_1m
_SQL_FETCH_ONTHEFLY = """
SELECT
    time_bucket(make_interval(mins => $2::int), time) AS time,
    (array_agg(open  ORDER BY time ASC))[1]           AS open,
    MAX(high)                                          AS high,
    MIN(low)                                           AS low,
    (array_agg(close ORDER BY time DESC))[1]           AS close,
    COALESCE(SUM(volume), 0)::bigint                   AS volume,
    COALESCE(SUM(trades), 0)::integer                  AS trades,
    CASE WHEN COALESCE(SUM(volume), 0) > 0
         THEN SUM(close::numeric * volume::numeric) / SUM(volume::numeric)
         ELSE AVG(close::numeric)
    END                                                AS vwap,
    'on_the_fly'::text                                 AS source
  FROM ohlc_1m
 WHERE ticker = $1 AND time >= $3
 GROUP BY time_bucket(make_interval(mins => $2::int), time)
 HAVING COUNT(*) > 0
 ORDER BY time
"""


VALID_INTERVALS = (1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 240, 480, 1440)


async def fetch_resampled(
    ticker: str,
    interval_minutes: int,
    since: date | datetime | None = None,
    allow_on_the_fly: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    """Retorna (bars, source).
    bars[i] = {time, open, high, low, close, volume, trades, vwap}.
    source = "materialized" | "on_the_fly" | "" (sem dados).

    interval_minutes: numero positivo. interval=1 retorna direto de ohlc_1m
    via on-the-fly (sem grupo).
    """
    if interval_minutes <= 0 or interval_minutes > 1440:
        raise ValueError(f"interval_minutes invalido (1-1440): {interval_minutes}")

    if since is None:
        since_ts = datetime(2020, 1, 1)
    elif isinstance(since, date) and not isinstance(since, datetime):
        since_ts = datetime.combine(since, time.min)
    else:
        since_ts = since

    pool = await get_timescale_pool()
    ticker_upper = ticker.upper()

    async with pool.acquire() as conn:
        # Tenta materialized (rapido)
        try:
            rows = await conn.fetch(
                _SQL_FETCH_MATERIALIZED, ticker_upper, interval_minutes, since_ts,
            )
            if rows:
                logger.debug(
                    "resampled.source", ticker=ticker_upper,
                    interval=interval_minutes, source="materialized", count=len(rows),
                )
                return [_row_to_dict(r) for r in rows], "materialized"
        except Exception:
            logger.debug("resampled.materialized.unavailable", ticker=ticker_upper)

        if not allow_on_the_fly:
            return [], ""

        # Fallback on-the-fly
        try:
            rows = await conn.fetch(
                _SQL_FETCH_ONTHEFLY, ticker_upper, interval_minutes, since_ts,
            )
            if rows:
                logger.debug(
                    "resampled.source", ticker=ticker_upper,
                    interval=interval_minutes, source="on_the_fly", count=len(rows),
                )
                return [_row_to_dict(r) for r in rows], "on_the_fly"
        except Exception:
            logger.debug("resampled.ohlc_1m.unavailable", ticker=ticker_upper)

    return [], ""


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "time":   row["time"],
        "open":   float(row["open"])  if row["open"]  is not None else None,
        "high":   float(row["high"])  if row["high"]  is not None else None,
        "low":    float(row["low"])   if row["low"]   is not None else None,
        "close":  float(row["close"]) if row["close"] is not None else None,
        "volume": int(row["volume"])  if row["volume"] is not None else 0,
        "trades": int(row["trades"])  if row["trades"] is not None else 0,
        "vwap":   float(row["vwap"])  if row["vwap"]   is not None else None,
        "source": row.get("source") if hasattr(row, "get") else (row["source"] if "source" in row.keys() else None),
    }
