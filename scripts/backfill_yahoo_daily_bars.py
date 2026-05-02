"""
backfill_yahoo_daily_bars.py — N11 (28/abr/2026)

Popula `profit_daily_bars` com daily OHLCV do Yahoo Finance para FIIs e ETFs.
Habilita fetch_candles (e endpoint /api/v1/indicators/{ticker}/levels) para
tickers que não têm dados via DLL Profit nem Fintz.

Le tickers de ticker_ml_config WHERE asset_class IN ('fii','etf'). Idempotente
via ON CONFLICT (time, ticker, exchange).

Uso:
    python scripts/backfill_yahoo_daily_bars.py
    python scripts/backfill_yahoo_daily_bars.py --years 3
    python scripts/backfill_yahoo_daily_bars.py --tickers KNRI11,BOVA11
    python scripts/backfill_yahoo_daily_bars.py --asset-class fii
    python scripts/backfill_yahoo_daily_bars.py --dry-run
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import os
import sys
import time

import psycopg2

DSN = (
    os.environ.get("TIMESCALE_URL")
    or os.environ.get("PROFIT_TIMESCALE_DSN")
    or "postgresql://finanalytics:timescale_secret@localhost:5433/market_data"
)


_SQL_UPSERT = """
INSERT INTO profit_daily_bars
    (time, ticker, exchange, open, high, low, close, volume, qty, trades)
VALUES (%s, %s, 'B', %s, %s, %s, %s, %s, NULL, NULL)
ON CONFLICT (time, ticker, exchange) DO UPDATE SET
    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
    close=EXCLUDED.close, volume=EXCLUDED.volume
"""


def fetch_yahoo_daily(ticker: str, start: date, end: date) -> list[tuple]:
    """Retorna [(date, open, high, low, close, volume), ...] do Yahoo."""
    import yfinance as yf

    yf_sym = f"{ticker}.SA"
    df = yf.download(
        yf_sym,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if df is None or df.empty:
        return []
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] for c in df.columns]
    out: list[tuple] = []
    for idx, row in df.iterrows():
        try:
            d = idx.date() if hasattr(idx, "date") else idx
            o = float(row["Open"])
            h = float(row["High"])
            l = float(row["Low"])
            c = float(row["Close"])
            v = float(row.get("Volume", 0) or 0)
        except (KeyError, ValueError, TypeError):
            continue
        if c <= 0 or h <= 0 or l <= 0:
            continue
        out.append((d, o, h, l, c, v))
    return sorted(out, key=lambda r: r[0])


def load_tickers(conn, asset_class: str | None) -> list[tuple[str, str]]:
    """Retorna [(ticker, asset_class), ...] de ticker_ml_config."""
    sql = "SELECT ticker, asset_class FROM ticker_ml_config WHERE asset_class IN ('fii','etf')"
    params: list = []
    if asset_class:
        sql = "SELECT ticker, asset_class FROM ticker_ml_config WHERE asset_class = %s"
        params.append(asset_class)
    sql += " ORDER BY ticker"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return [(r[0], r[1]) for r in cur.fetchall()]


def upsert_bars(conn, ticker: str, bars: list[tuple]) -> int:
    if not bars:
        return 0
    with conn.cursor() as cur:
        for b in bars:
            d, o, h, l, c, v = b
            cur.execute(_SQL_UPSERT, (d, ticker, o, h, l, c, v))
    conn.commit()
    return len(bars)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, default=2)
    p.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="CSV (default = todos FII+ETF do ticker_ml_config)",
    )
    p.add_argument("--asset-class", choices=["fii", "etf"], default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    end = date.today()
    start = end - timedelta(days=args.years * 365 + 30)

    conn = psycopg2.connect(DSN)
    try:
        if args.tickers:
            tickers = [(t.strip().upper(), "?") for t in args.tickers.split(",") if t.strip()]
        else:
            tickers = load_tickers(conn, args.asset_class)

        print(
            f"[backfill_yahoo_daily_bars] {len(tickers)} tickers | {start} -> {end} | dry={args.dry_run}"
        )

        ok = 0
        skip = 0
        fail = 0
        total = 0
        for i, (t, ac) in enumerate(tickers, 1):
            t0 = time.time()
            try:
                bars = fetch_yahoo_daily(t, start, end)
            except Exception as exc:
                print(f"  [{i:3d}/{len(tickers)}] {t}({ac}): ERRO yahoo {exc}")
                fail += 1
                continue
            if not bars:
                print(f"  [{i:3d}/{len(tickers)}] {t}({ac}): SKIP (sem dados Yahoo)")
                skip += 1
                continue
            if args.dry_run:
                print(f"  [{i:3d}/{len(tickers)}] {t}({ac}): bars={len(bars)} (dry-run)")
            else:
                n = upsert_bars(conn, t, bars)
                total += n
                print(f"  [{i:3d}/{len(tickers)}] {t}({ac}): bars={n} ({time.time() - t0:.1f}s)")
            ok += 1
    finally:
        conn.close()

    print(f"[backfill_yahoo_daily_bars] done | ok={ok} skip={skip} fail={fail} total_rows={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
