"""
resample_ohlc.py — agrega ohlc_1m em barras de N minutos (5/15/30/60/...).

Le ohlc_1m, agrega via TimescaleDB time_bucket('N minutes', time), e faz
upsert em ohlc_resampled (interval_minutes = N).

Suporta qualquer N (em minutos) — typical: 5, 15, 30, 60, 240. Para
intraday B3 (10:00-17:00 = 7h), bons valores: 5, 15, 30, 60.

OHLC: open=primeiro do bucket, close=ultimo, high=max, low=min,
volume=soma, trades=soma, vwap=volume-weighted close approximation.

Uso:
    python scripts/resample_ohlc.py --intervals 5,15,60 --tickers PETR4,VALE3
    python scripts/resample_ohlc.py --intervals 5 --all-tickers
    python scripts/resample_ohlc.py --intervals 5,15 --since 2025-01-01
    python scripts/resample_ohlc.py --intervals 5 --tickers PETR4 --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
import time as _time
from datetime import date

import psycopg2


DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


# Aggregate ohlc_1m -> ohlc_resampled em N minutos.
# Usa array_agg para open (primeiro)/close (ultimo) — robusto contra ordem.
# VWAP: media ponderada do close por volume; para 1m bars sem trades distintos,
# essa e a melhor aproximacao disponivel (mid-bar nao temos).
_RESAMPLE_SQL = """
INSERT INTO ohlc_resampled
    (time, ticker, interval_minutes, open, high, low, close,
     volume, trades, vwap, source)
SELECT
    time_bucket(make_interval(mins => %s), time)        AS bucket,
    ticker,
    %s::smallint                                        AS interval_minutes,
    (array_agg(open  ORDER BY time ASC))[1]             AS open,
    MAX(high)                                           AS high,
    MIN(low)                                            AS low,
    (array_agg(close ORDER BY time DESC))[1]            AS close,
    COALESCE(SUM(volume), 0)::bigint                    AS volume,
    COALESCE(SUM(trades), 0)::integer                   AS trades,
    CASE WHEN COALESCE(SUM(volume), 0) > 0
         THEN SUM(close::numeric * volume::numeric) / SUM(volume::numeric)
         ELSE AVG(close::numeric)
    END                                                 AS vwap,
    'resample_1m'                                       AS source
  FROM ohlc_1m
 WHERE ticker = %s
   AND time >= %s
 GROUP BY bucket, ticker
 HAVING COUNT(*) > 0
ON CONFLICT (time, ticker, interval_minutes) DO UPDATE SET
    open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
    close=EXCLUDED.close, volume=EXCLUDED.volume, trades=EXCLUDED.trades,
    vwap=EXCLUDED.vwap, source=EXCLUDED.source
"""

# Dry-run: conta sem inserir.
_RESAMPLE_DRY_SQL = """
SELECT count(*)
  FROM (
    SELECT time_bucket(make_interval(mins => %s), time) AS bucket, ticker
      FROM ohlc_1m
     WHERE ticker = %s AND time >= %s
     GROUP BY bucket, ticker
  ) sub
"""


def list_tickers(conn, args) -> list[str]:
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    with conn.cursor() as cur:
        if args.all_tickers:
            cur.execute("SELECT DISTINCT ticker FROM ohlc_1m ORDER BY ticker")
        elif args.watchlist_verde:
            cur.execute(
                "SELECT ticker FROM watchlist_tickers WHERE status='VERDE' "
                "ORDER BY mediana_vol_brl DESC NULLS LAST"
            )
        else:
            return []
        return [r[0] for r in cur.fetchall()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Resample ohlc_1m -> ohlc_resampled")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tickers", help="CSV de tickers")
    g.add_argument("--all-tickers", action="store_true",
                   help="Todos tickers em ohlc_1m")
    g.add_argument("--watchlist-verde", action="store_true",
                   help="Tickers VERDE da watchlist")
    p.add_argument("--intervals", required=True,
                   help="CSV de minutos: 5,15,30,60")
    p.add_argument("--since", default="2020-01-01",
                   help="Data minima (YYYY-MM-DD); default 2020-01-01")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    intervals: list[int] = []
    for i in args.intervals.split(","):
        i = i.strip()
        if not i: continue
        try:
            n = int(i)
            if n <= 0 or n > 1440:
                print(f"intervalo invalido (1-1440): {i}", file=sys.stderr); return 2
            intervals.append(n)
        except ValueError:
            print(f"intervalo nao-numerico: {i}", file=sys.stderr); return 2

    try:
        since_date = date.fromisoformat(args.since)
    except ValueError:
        print(f"--since invalido (use YYYY-MM-DD): {args.since}", file=sys.stderr); return 2

    conn = psycopg2.connect(DSN)
    try:
        tickers = list_tickers(conn, args)
        if not tickers:
            print("nenhum ticker selecionado", file=sys.stderr); return 2
        print(f"resample: {len(tickers)} tickers x {len(intervals)} intervals "
              f"({intervals}) since={since_date} dry_run={args.dry_run}")

        totals: dict[int, int] = {n: 0 for n in intervals}
        t0 = _time.time()
        for i, ticker in enumerate(tickers, 1):
            for n_min in intervals:
                t_t = _time.time()
                try:
                    with conn.cursor() as cur:
                        if args.dry_run:
                            cur.execute(_RESAMPLE_DRY_SQL, (n_min, ticker, since_date))
                            n_rows = int(cur.fetchone()[0] or 0)
                        else:
                            cur.execute(_RESAMPLE_SQL,
                                        (n_min, n_min, ticker, since_date))
                            n_rows = cur.rowcount
                            conn.commit()
                    totals[n_min] += n_rows
                    elapsed_t = _time.time() - t_t
                    print(f"  [{i}/{len(tickers)}] {ticker:<7} {n_min:>3}m: "
                          f"{n_rows:>6} bars ({elapsed_t:.2f}s)")
                except Exception as exc:
                    conn.rollback()
                    print(f"  ERRO {ticker} {n_min}m: {type(exc).__name__}: {str(exc)[:120]}")

        elapsed = _time.time() - t0
        print(f"\n=== RESUMO ===")
        for n_min in intervals:
            print(f"  {n_min:>3}m: {totals[n_min]:>8} bars (total {len(tickers)} tickers)")
        print(f"  elapsed: {elapsed:.1f}s")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
