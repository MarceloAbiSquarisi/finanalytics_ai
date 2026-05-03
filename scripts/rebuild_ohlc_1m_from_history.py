"""
rebuild_ohlc_1m_from_history.py — Reconstrói bars 1m a partir de market_history_trades.

Aplica time_bucket('1 minute') sobre os ticks históricos e popula ohlc_1m
com source='tick_agg_v1'. Filtra ticks fora do pregão B3 (13-20 UTC) pra
match exato com continuous aggregate ohlc_1m_from_ticks.

Uso:
    python scripts/rebuild_ohlc_1m_from_history.py                          # 19abr-30abr
    python scripts/rebuild_ohlc_1m_from_history.py --start 2026-04-19 --end 2026-04-30
    python scripts/rebuild_ohlc_1m_from_history.py --ticker PETR4
    python scripts/rebuild_ohlc_1m_from_history.py --dry-run

Lógica:
  1. DELETE FROM ohlc_1m WHERE source='tick_agg_v1' AND time IN range — limpa stale.
  2. INSERT ... SELECT time_bucket('1 minute', trade_date), ticker, OHLCV FROM
     market_history_trades WHERE EXTRACT(hour) BETWEEN 13 AND 20.
  3. ON CONFLICT (time, ticker) DO NOTHING — preserva rows BRAPI/external_1m.

Pregão B3 filter (13-20 UTC = 10:00-17:59 BRT): mesma janela do CA pra evitar
poluição por leilão pre-abertura, after-market, heartbeats trade_type=3.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import sys
import time

_env_file = Path(__file__).resolve().parents[1] / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k not in os.environ:
                os.environ[_k] = _v

DB_DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def emit(tag: str, **kw) -> None:
    print(" ".join([tag] + [f"{k}={v}" for k, v in kw.items()]), flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--start",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date(2026, 4, 19),
        help="Data inicial (YYYY-MM-DD)",
    )
    p.add_argument(
        "--end",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=date(2026, 4, 30),
        help="Data final inclusiva (YYYY-MM-DD)",
    )
    p.add_argument("--ticker", default=None, help="Ticker específico (default: todos)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra plano sem executar DELETE/INSERT",
    )
    return p.parse_args()


_SQL_DELETE = """
DELETE FROM ohlc_1m
WHERE source = 'tick_agg_v1'
  AND time >= %s
  AND time <  %s
  {ticker_filter}
"""

_SQL_REBUILD = """
INSERT INTO ohlc_1m (time, ticker, open, high, low, close, volume, trades, source)
SELECT
    time_bucket('1 minute', trade_date) AS time,
    ticker,
    (array_agg(price ORDER BY trade_date ASC, trade_number ASC))[1]::numeric(18,4)  AS open,
    MAX(price)::numeric(18,4)  AS high,
    MIN(price)::numeric(18,4)  AS low,
    (array_agg(price ORDER BY trade_date DESC, trade_number DESC))[1]::numeric(18,4) AS close,
    SUM(quantity)::bigint      AS volume,
    COUNT(*)::int              AS trades,
    'tick_agg_v1'              AS source
FROM market_history_trades
WHERE trade_date >= %s
  AND trade_date <  %s
  AND EXTRACT(hour FROM trade_date) BETWEEN 13 AND 20
  AND EXTRACT(dow  FROM trade_date) BETWEEN 1 AND 5
  {ticker_filter}
GROUP BY time_bucket('1 minute', trade_date), ticker
ON CONFLICT (time, ticker) DO NOTHING
"""


def rebuild_day(conn, day: date, ticker: str | None) -> tuple[int, int]:
    next_day = day + timedelta(days=1)
    ticker_filter = "AND ticker = %s" if ticker else ""
    delete_sql = _SQL_DELETE.format(ticker_filter=ticker_filter)
    rebuild_sql = _SQL_REBUILD.format(ticker_filter=ticker_filter)

    delete_params = [day, next_day] + ([ticker] if ticker else [])
    rebuild_params = [day, next_day] + ([ticker] if ticker else [])

    cur = conn.cursor()
    cur.execute(delete_sql, delete_params)
    deleted = cur.rowcount
    cur.execute(rebuild_sql, rebuild_params)
    inserted = cur.rowcount
    cur.close()
    return deleted, inserted


def main() -> int:
    args = parse_args()
    if args.start > args.end:
        emit("ERROR", msg="start > end")
        return 1

    import psycopg2

    days = []
    d = args.start
    while d <= args.end:
        days.append(d)
        d += timedelta(days=1)

    emit(
        "START",
        start=args.start.isoformat(),
        end=args.end.isoformat(),
        days=len(days),
        ticker=args.ticker or "ALL",
        dry_run=args.dry_run,
    )

    if args.dry_run:
        emit("PLAN", action="DELETE source=tick_agg_v1 + INSERT from market_history_trades")
        emit("PLAN", filter="EXTRACT(hour) BETWEEN 13 AND 20 (pregão B3)")
        emit("PLAN", on_conflict="DO NOTHING (preserva BRAPI/external_1m)")
        return 0

    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False

    total_deleted = 0
    total_inserted = 0
    t0 = time.time()
    try:
        for day in days:
            day_t0 = time.time()
            try:
                deleted, inserted = rebuild_day(conn, day, args.ticker)
                conn.commit()
                total_deleted += deleted
                total_inserted += inserted
                emit(
                    "PROGRESS",
                    day=day.isoformat(),
                    deleted=deleted,
                    inserted=inserted,
                    elapsed_s=round(time.time() - day_t0, 1),
                )
            except Exception as exc:
                conn.rollback()
                emit("ERROR", day=day.isoformat(), err=str(exc)[:200])
        emit(
            "DONE",
            total_deleted=total_deleted,
            total_inserted=total_inserted,
            net=total_inserted - total_deleted,
            duration_min=round((time.time() - t0) / 60, 2),
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
