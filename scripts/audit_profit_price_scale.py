"""
audit_profit_price_scale.py — diagnóstico read-only do bug de escala em
market_history_trades (pré-patch do commit efba27c, 17/abr/2026).

Contexto:
  O commit efba27c removeu uma divisão por 100 erroneamente aplicada no
  callback V2 do profit_agent. Ticks coletados ANTES desse commit têm
  price dividido por 100 (ex: PETR4 close aparece como 0.49 em vez de 49).
  Sprint 3 (§7) re-coletou 2026-04-13..16 pós-patch, mas a maior parte do
  histórico 2026-01-02..2026-04-10 ainda está com escala errada.

Escopo conhecido (9 tickers DLL principais, amostra 2026-01-02..2026-04-17):
  64-69 de 72 dias têm PELO MENOS 1 tick com price < 5 R$ — incluindo dias
  onde 100% dos ticks estão errados (ex: PETR4 09/10-abr) e dias mistos
  (re-coletas parciais, ex: PETR4 15-16/abr com min=0.47 e max=48).

Heurística de detecção (stocks da watchlist com mediana_vol_brl > 1M):
  Se mediana(price) no dia < max(price) / 10, há mistura; se mediana < 5,
  dia provavelmente inteiro afetado.

Este script é READ-ONLY: lista dias/tickers afetados sem modificar dados.

Uso:
    python scripts/audit_profit_price_scale.py
    python scripts/audit_profit_price_scale.py --ticker PETR4
    python scripts/audit_profit_price_scale.py --start 2026-01-02 --end 2026-04-10
"""
from __future__ import annotations

import argparse
from datetime import date
import os
import sys

import psycopg2

DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


def audit(ticker: str | None, d_start: date, d_end: date) -> None:
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor() as cur:
            if ticker:
                cur.execute(
                    """
                    WITH agg AS (
                        SELECT ticker, trade_date::date AS dia,
                               percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS median_p,
                               min(price) AS min_p, max(price) AS max_p,
                               count(*) AS ticks
                          FROM market_history_trades
                         WHERE ticker = %s
                           AND trade_date >= %s AND trade_date < (%s::date + 1)
                         GROUP BY ticker, trade_date::date
                    )
                    SELECT dia,
                           round(median_p::numeric, 4) AS median,
                           round(min_p::numeric,    4) AS min,
                           round(max_p::numeric,    4) AS max,
                           ticks,
                           CASE
                               WHEN median_p < 5 AND max_p / NULLIF(min_p, 0) < 10 THEN 'FULL_BUG'
                               WHEN max_p / NULLIF(min_p, 0) >= 10 THEN 'MIXED'
                               ELSE 'ok'
                           END AS diag
                      FROM agg
                     ORDER BY dia
                    """,
                    (ticker.upper(), d_start, d_end),
                )
            else:
                cur.execute(
                    """
                    WITH watch AS (
                        SELECT ticker FROM watchlist_tickers
                         WHERE status = 'VERDE'
                           AND mediana_vol_brl > 1000000
                    ),
                    agg AS (
                        SELECT m.ticker, m.trade_date::date AS dia,
                               percentile_cont(0.5) WITHIN GROUP (ORDER BY m.price) AS median_p,
                               min(m.price) AS min_p, max(m.price) AS max_p,
                               count(*) AS ticks
                          FROM market_history_trades m
                          JOIN watch w USING (ticker)
                         WHERE m.trade_date >= %s AND m.trade_date < (%s::date + 1)
                         GROUP BY m.ticker, m.trade_date::date
                    )
                    SELECT ticker,
                           count(*) FILTER (WHERE median_p < 5 AND max_p / NULLIF(min_p, 0) < 10) AS full_bug,
                           count(*) FILTER (WHERE max_p / NULLIF(min_p, 0) >= 10) AS mixed,
                           count(*) AS dias_total
                      FROM agg
                     GROUP BY ticker
                     ORDER BY (count(*) FILTER (WHERE median_p < 5 AND max_p / NULLIF(min_p, 0) < 10)) DESC,
                              ticker
                     LIMIT 40
                    """,
                    (d_start, d_end),
                )
            for row in cur.fetchall():
                print("\t".join(str(x) for x in row))
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--ticker")
    p.add_argument("--start", default="2026-01-02")
    p.add_argument("--end", default="2026-04-17")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    audit(args.ticker, date.fromisoformat(args.start), date.fromisoformat(args.end))
    return 0


if __name__ == "__main__":
    sys.exit(main())
