"""
fred_ingestion.py — ingestão de séries FRED (St. Louis Fed) via HTTP público.

Usa https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES> (sem API key).
Popula:
  - yield_curves (market='us_treasury', source='fred') para DGS3MO/DGS2/DGS10
    mapeando para vertice_du = 63/504/2520.
  - us_macro_daily (novo) para DFF, CPI, VIX, breakeven, HY spread.

Uso:
    python scripts/fred_ingestion.py                  # full backfill 2020+
    python scripts/fred_ingestion.py --start 2024-01-01
    python scripts/fred_ingestion.py --only DGS10,DFF
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import io
import os
import sys
import time
from typing import Any

import psycopg2
import psycopg2.extras
import requests

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) finanalytics-ai/1.0",
        "Accept": "text/csv,text/plain,*/*",
    }
)


DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

# Séries -> (destino, vertice_du or None)
SERIES_MAP = {
    # Treasury yields → yield_curves
    "DGS3MO": ("yield_curves", 63),
    "DGS2": ("yield_curves", 504),
    "DGS10": ("yield_curves", 2520),
    # Macro → us_macro_daily
    "DFF": ("us_macro_daily", None),
    "CPIAUCSL": ("us_macro_daily", None),
    "VIXCLS": ("us_macro_daily", None),
    "T5YIE": ("us_macro_daily", None),
    "T10YIE": ("us_macro_daily", None),
    "BAMLH0A0HYM2": ("us_macro_daily", None),
}

URL_TMPL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"


def fetch_fred(series: str, start: date, max_retries: int = 4) -> list[tuple[date, float]]:
    url = URL_TMPL.format(series=series, start=start.strftime("%Y-%m-%d"))
    raw = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = _SESSION.get(url, timeout=(10, 90))
            resp.raise_for_status()
            raw = resp.text
            break
        except Exception as e:
            print(
                f"  {series} attempt {attempt}/{max_retries}: {type(e).__name__}: {str(e)[:80]}",
                file=sys.stderr,
            )
            if attempt < max_retries:
                time.sleep(5 * attempt)  # 5, 10, 15s
    if raw is None:
        return []
    out: list[tuple[date, float]] = []
    reader = csv.reader(io.StringIO(raw))
    header = next(reader, None)
    for row in reader:
        if len(row) < 2 or row[0] == "observation_date":
            continue
        try:
            d = date.fromisoformat(row[0])
            # FRED retorna "." para missing
            v = float(row[1]) if row[1] not in (".", "") else None
        except Exception:
            continue
        if v is not None:
            out.append((d, v))
    return out


def upsert_yield_curves(conn, series: str, du: int, data: list[tuple[date, float]]) -> int:
    if not data:
        return 0
    sql = """
        INSERT INTO yield_curves
            (time, market, vertice_du, taxa_aa, source)
        VALUES %s
        ON CONFLICT (time, market, vertice_du, source) DO UPDATE SET
            taxa_aa = EXCLUDED.taxa_aa,
            atualizado_em = now()
    """
    rows = [(d, "us_treasury", du, v, "fred") for d, v in data]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
    return len(rows)


def upsert_us_macro(conn, series: str, data: list[tuple[date, float]]) -> int:
    if not data:
        return 0
    sql = """
        INSERT INTO us_macro_daily (time, series, value, source)
        VALUES %s
        ON CONFLICT (time, series) DO UPDATE SET
            value = EXCLUDED.value,
            atualizado_em = now()
    """
    rows = [(d, series, v, "fred") for d, v in data]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
    return len(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2020-01-02")
    p.add_argument("--only", help='CSV de séries, ex: "DGS10,DFF"')
    return p.parse_args()


def main() -> int:
    args = parse_args()
    d_start = date.fromisoformat(args.start)
    series_filter = {s.strip().upper() for s in args.only.split(",")} if args.only else None

    conn = psycopg2.connect(DSN)
    try:
        total = 0
        for i, (series, (table, du)) in enumerate(SERIES_MAP.items()):
            if series_filter and series not in series_filter:
                continue
            if i > 0:
                time.sleep(2)  # rate-limit gentle
            print(f"[FRED] {series} -> {table}{f'/du={du}' if du else ''}")
            data = fetch_fred(series, d_start)
            print(
                f"  fetched {len(data)} pontos ({data[0][0] if data else '-'} -> {data[-1][0] if data else '-'})"
            )
            if table == "yield_curves":
                n = upsert_yield_curves(conn, series, du, data)
            else:
                n = upsert_us_macro(conn, series, data)
            conn.commit()
            total += n
            print(f"  upserted {n}")
        print(f"\nTotal: {total}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
