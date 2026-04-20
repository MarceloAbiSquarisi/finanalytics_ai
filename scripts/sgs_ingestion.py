"""
sgs_ingestion.py — ingestão de séries BCB SGS (SELIC, IPCA, CDI, PTAX).

API pública BCB: https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados
Sem API key necessária.

Populate: br_macro_daily (schema criado inline).

Códigos SGS padrão:
  11   SELIC over (% ao dia)
  12   CDI over (% ao dia)
  432  SELIC Meta anualizada (% ao ano, decisão COPOM)
  433  IPCA mensal (% mês)
  1178 PTAX venda (BRL/USD)

Uso:
    python scripts/sgs_ingestion.py                  # full backfill 2020+
    python scripts/sgs_ingestion.py --start 2024-01-01
    python scripts/sgs_ingestion.py --only SELIC_OVER,IPCA
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime
from typing import Any

import psycopg2
import psycopg2.extras


DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)

# name -> (codigo_sgs, description)
SERIES_MAP = {
    "SELIC_OVER":  (11,   "SELIC over, % ao dia"),
    "CDI_OVER":    (12,   "CDI over, % ao dia"),
    "SELIC_META":  (432,  "SELIC Meta anualizada"),
    "IPCA":        (433,  "IPCA mensal"),
    "PTAX_VENDA":  (1178, "PTAX venda BRL/USD"),
}

URL_TMPL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{cod}/dados?formato=json&dataInicial={di}&dataFinal={df}"


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS br_macro_daily (
                time           timestamptz       NOT NULL,
                series         text              NOT NULL,
                value          double precision,
                source         text              NOT NULL DEFAULT 'bcb_sgs',
                atualizado_em  timestamptz       NOT NULL DEFAULT now(),
                PRIMARY KEY (time, series)
            );
            CREATE INDEX IF NOT EXISTS idx_br_macro_series_time
                ON br_macro_daily (series, time DESC);
            COMMENT ON TABLE br_macro_daily IS
                'Séries BCB/SGS (SELIC over, CDI, IPCA, PTAX, SELIC Meta). Sprint T2-HMM.';
            """
        )
    conn.commit()


def fetch_sgs(codigo: int, d_start: date, d_end: date) -> list[tuple[date, float]]:
    url = URL_TMPL.format(
        cod=codigo,
        di=d_start.strftime("%d/%m/%Y"),
        df=d_end.strftime("%d/%m/%Y"),
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "finanalytics-ai/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  SGS {codigo}: fetch failed {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return []
    try:
        items = json.loads(raw)
    except Exception as e:
        print(f"  SGS {codigo}: parse failed {type(e).__name__}", file=sys.stderr)
        return []
    out: list[tuple[date, float]] = []
    for item in items:
        try:
            # BCB retorna dd/mm/yyyy
            d = datetime.strptime(item["data"], "%d/%m/%Y").date()
            v = float(item["valor"].replace(",", "."))
        except Exception:
            continue
        out.append((d, v))
    return out


def upsert(conn, series_name: str, data: list[tuple[date, float]]) -> int:
    if not data:
        return 0
    sql = """
        INSERT INTO br_macro_daily (time, series, value, source)
        VALUES %s
        ON CONFLICT (time, series) DO UPDATE SET
            value = EXCLUDED.value,
            atualizado_em = now()
    """
    rows = [(d, series_name, v, "bcb_sgs") for d, v in data]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
    return len(rows)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2020-01-02")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (default hoje)")
    p.add_argument("--only", help='CSV, ex: "SELIC_OVER,IPCA"')
    return p.parse_args()


def main() -> int:
    args = parse_args()
    d_start = date.fromisoformat(args.start)
    d_end = date.fromisoformat(args.end) if args.end else date.today()
    only = set(s.strip().upper() for s in args.only.split(",")) if args.only else None

    conn = psycopg2.connect(DSN)
    try:
        ensure_schema(conn)
        total = 0
        for name, (cod, desc) in SERIES_MAP.items():
            if only and name not in only:
                continue
            print(f"[SGS] {name} (cod {cod}) — {desc}")
            data = fetch_sgs(cod, d_start, d_end)
            print(f"  fetched {len(data)} pontos ({data[0][0] if data else '-'} -> {data[-1][0] if data else '-'})")
            n = upsert(conn, name, data)
            conn.commit()
            total += n
            print(f"  upserted {n}")
        print(f"\nTotal: {total}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
