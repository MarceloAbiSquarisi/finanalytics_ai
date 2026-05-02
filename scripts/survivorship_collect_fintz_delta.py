"""
Survivorship bias step 1 — bridge CNPJ→ticker via Fintz delta.

Estrategia (substitui plano original PDF IBOV):
  1. Listar todos os tickers em fintz_cotacoes_ts (Timescale, 884 unicos
     cobrindo 2010→2025-12-30).
  2. Para cada ticker, pegar MAX(time) e o close correspondente.
  3. Filtrar:
     - HIGH-CONFIDENCE: last_date < 2024-01-01 — alta probabilidade de
       delisting real (ENBR3, BRPR3, ALSO3, VIIA3, BOAS3, etc.).
     - BORDERLINE: 2024-01-01 <= last_date < 2025-06-01 — pode ser
       delisting ou ticker de baixa liquidez. Marcar com notes.
     - DESCARTAR: last_date >= 2025-06-01 — artefato do dataset Fintz
       (cutoff de freeze foi ~03/11/2025; 434 tickers param nessa janela
       sao tickers VIVOS hoje, ex: ITUB3, ELET3, CSNA3).
  4. Cruzar com profit_subscribed_tickers (Timescale): se candidato esta
     subscribed hoje, NAO e' delisted (provavelmente Fintz parou de
     publicar mas o ativo continua negociado).
  5. UPSERT em b3_delisted_tickers (Postgres) com:
     - ticker = ticker REAL (nao placeholder!)
     - delisting_date = last_date (aproximacao)
     - last_known_price, last_known_date
     - source = 'FINTZ'
     - notes = 'high_confidence' | 'borderline_validar'

Uso:
  python scripts/survivorship_collect_fintz_delta.py --dry
  python scripts/survivorship_collect_fintz_delta.py --persist

Envs:
  TIMESCALE_DSN_SYNC  default postgresql://finanalytics:timescale_secret@localhost:5433/market_data
  DATABASE_URL_SYNC   default postgresql://finanalytics:secret@localhost:5432/finanalytics
"""

from __future__ import annotations

import argparse
from datetime import date
import os
import sys

import psycopg2

HIGH_CONF_CUTOFF = date(2024, 1, 1)
BORDERLINE_CUTOFF = date(2025, 6, 1)


def fetch_candidates(timescale_dsn: str) -> list[dict]:
    """
    Consulta Timescale para listar candidatos a delisted.

    Estrategia: para cada ticker em fintz_cotacoes_ts com last_date <
    BORDERLINE_CUTOFF, buscar last_date + close correspondente. Excluir
    tickers que estao em profit_subscribed_tickers (ativos hoje).
    """
    sql = """
        WITH ticker_last AS (
            SELECT ticker, MAX(time)::date AS last_date
            FROM fintz_cotacoes_ts GROUP BY ticker
        ),
        ticker_close AS (
            SELECT t.ticker, t.last_date, c.preco_fechamento AS last_close
            FROM ticker_last t
            JOIN fintz_cotacoes_ts c
              ON c.ticker = t.ticker AND c.time::date = t.last_date
        ),
        active_today AS (
            SELECT DISTINCT ticker FROM profit_subscribed_tickers
        )
        SELECT tc.ticker, tc.last_date, tc.last_close
        FROM ticker_close tc
        LEFT JOIN active_today a ON a.ticker = tc.ticker
        WHERE tc.last_date < %s
          AND a.ticker IS NULL  -- nao esta no universo subscribed hoje
        ORDER BY tc.last_date DESC, tc.ticker;
    """
    rows: list[dict] = []
    with psycopg2.connect(timescale_dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, (BORDERLINE_CUTOFF,))
        for ticker, last_date, last_close in cur.fetchall():
            rows.append(
                {
                    "ticker": ticker,
                    "last_date": last_date,
                    "last_close": last_close,
                    "confidence": "high" if last_date < HIGH_CONF_CUTOFF else "borderline",
                }
            )
    return rows


def upsert_delisted(postgres_dsn: str, rows: list[dict]) -> int:
    """
    UPSERT em b3_delisted_tickers (Postgres). source='FINTZ', notes
    indica confidence. Mantem tickers placeholders UNK_* da CVM intactos
    (PK e' diferente).
    """
    sql = """
        INSERT INTO b3_delisted_tickers
            (ticker, cnpj, razao_social, delisting_date, delisting_reason,
             last_known_price, last_known_date, source, notes)
        VALUES (%s, NULL, NULL, %s, %s, %s, %s, 'FINTZ', %s)
        ON CONFLICT (ticker) DO UPDATE SET
            delisting_date    = EXCLUDED.delisting_date,
            last_known_price  = EXCLUDED.last_known_price,
            last_known_date   = EXCLUDED.last_known_date,
            source            = EXCLUDED.source,
            notes             = EXCLUDED.notes,
            updated_at        = NOW()
    """
    inserted = 0
    with psycopg2.connect(postgres_dsn) as conn, conn.cursor() as cur:
        for r in rows:
            notes = (
                "fintz_delta high_confidence (last_date < 2024)"
                if r["confidence"] == "high"
                else "fintz_delta borderline_validar (2024 <= last_date < 2025-06)"
            )
            cur.execute(
                sql,
                (
                    r["ticker"],
                    r["last_date"],
                    "OUTRO",  # delisting_reason — nao temos dado preciso
                    r["last_close"],
                    r["last_date"],
                    notes,
                ),
            )
            inserted += 1
        conn.commit()
    return inserted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry", action="store_true", help="So' lista, nao persiste")
    ap.add_argument("--persist", action="store_true", help="UPSERT em b3_delisted_tickers")
    args = ap.parse_args()
    if not args.dry and not args.persist:
        ap.error("Use --dry ou --persist")

    timescale_dsn = os.environ.get(
        "TIMESCALE_DSN_SYNC",
        "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
    )
    postgres_dsn = os.environ.get(
        "DATABASE_URL_SYNC",
        "postgresql://finanalytics:secret@localhost:5432/finanalytics",
    )
    if "asyncpg" in postgres_dsn:
        postgres_dsn = postgres_dsn.replace("+asyncpg", "")

    print(f"Lendo candidatos de {timescale_dsn.split('@')[-1]}...")
    candidates = fetch_candidates(timescale_dsn)
    high = [r for r in candidates if r["confidence"] == "high"]
    borderline = [r for r in candidates if r["confidence"] == "borderline"]
    print(f"  HIGH:       {len(high):4d} candidatos (last_date < 2024)")
    print(f"  BORDERLINE: {len(borderline):4d} candidatos (2024 <= last < 2025-06)")
    print(f"  TOTAL:      {len(candidates):4d}\n")

    print(f"{'TICKER':<10} {'LAST_DATE':<12} {'CLOSE':>10}  {'CONF':<10}")
    print("-" * 50)
    for r in candidates[:25]:
        close_str = f"{float(r['last_close']):.2f}" if r["last_close"] else "-"
        print(
            f"{r['ticker']:<10} {str(r['last_date']):<12} "
            f"{close_str:>10}  {r['confidence']:<10}"
        )
    if len(candidates) > 25:
        print(f"... ({len(candidates) - 25} restantes ocultos)")

    if args.persist:
        print(f"\nPersistindo em {postgres_dsn.split('@')[-1]}...")
        try:
            n = upsert_delisted(postgres_dsn, candidates)
            print(f"OK -- {n} rows UPSERT em b3_delisted_tickers (source=FINTZ)")
        except Exception as exc:
            print(f"ERRO no UPSERT: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
