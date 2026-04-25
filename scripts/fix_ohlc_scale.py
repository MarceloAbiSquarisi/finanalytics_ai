"""Fix bars com escala errada em ohlc_1m (TimescaleDB) — versão per-bar.

Background:
    Tabela ohlc_1m tem bars em escala 100× menor (legacy ingestor 'tick_agg_v1').
    Cada ticker pode ter bars em escala errada MISTURADAS com bars corretas.
    Penny stocks legítimos B3 existem (AMBP3 ~0.22, AZEV4 ~0.14) — não multiplicar cego.

Estratégia per-bar (correto):
    1. Para cada ticker, busca preço de referência (profit_ticks ou ohlc_1m_from_ticks).
    2. Para cada bar com source='tick_agg_v1' e close<1:
       - Se |close * 100 - ref| < |close - ref| → multiplicar ×100 (bar errado).
       - Senão → manter (bar legítimo penny stock).
    3. DELETE bars com inconsistência interna.

SQL único faz tudo numa transação via UPDATE FROM ... WHERE ABS(close*100-ref) < ABS(close-ref).

Uso:
    python scripts/fix_ohlc_scale.py --dry-run
    python scripts/fix_ohlc_scale.py --apply
    python scripts/fix_ohlc_scale.py --ticker PETR4 --apply

Idempotência: re-run não muda nada porque após fix, |close*100-ref| > |close-ref|.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg


DEFAULT_DSN = os.environ.get(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
)


async def _connect(dsn: str) -> asyncpg.Connection:
    if "+asyncpg" in dsn:
        dsn = dsn.replace("+asyncpg", "")
    return await asyncpg.connect(dsn)


# CTE de referências: prefere profit_ticks (mais real-time) sobre ohlc_1m_from_ticks
_REFS_CTE = """
    WITH refs AS (
        SELECT ticker, AVG(price)::numeric AS ref
        FROM profit_ticks
        GROUP BY ticker
        UNION ALL
        SELECT ticker, AVG(close)::numeric AS ref
        FROM ohlc_1m_from_ticks
        WHERE ticker NOT IN (SELECT DISTINCT ticker FROM profit_ticks)
        GROUP BY ticker
    )
"""


async def analyze(conn: asyncpg.Connection, ticker_filter: str | None) -> dict:
    """Conta candidates per-bar."""
    where_extra = " AND o.ticker=$1" if ticker_filter else ""
    args = (ticker_filter,) if ticker_filter else ()

    sql = f"""
        {_REFS_CTE}
        SELECT
            COUNT(*) FILTER (
                WHERE o.source='tick_agg_v1'
                  AND o.close<1 AND o.open<1 AND o.high<1 AND o.low<1
                  AND ABS(o.close*100 - r.ref) < ABS(o.close - r.ref)
            ) AS bars_to_multiply,
            COUNT(*) FILTER (
                WHERE o.source='tick_agg_v1'
                  AND o.close<1
                  AND ABS(o.close*100 - r.ref) >= ABS(o.close - r.ref)
            ) AS bars_legit_penny,
            COUNT(*) FILTER (
                WHERE o.source='tick_agg_v1'
                  AND ((o.open<1) <> (o.close<1) OR (o.open<1) <> (o.high<1) OR (o.open<1) <> (o.low<1))
            ) AS bars_inconsistent,
            COUNT(DISTINCT o.ticker) FILTER (
                WHERE o.source='tick_agg_v1'
                  AND o.close<1 AND o.open<1 AND o.high<1 AND o.low<1
                  AND ABS(o.close*100 - r.ref) < ABS(o.close - r.ref)
            ) AS tickers_to_fix
        FROM ohlc_1m o
        LEFT JOIN refs r ON r.ticker = o.ticker
        WHERE r.ref IS NOT NULL {where_extra}
    """
    row = await conn.fetchrow(sql, *args)
    if row is None:
        return {}

    # Sample
    sample_sql = f"""
        {_REFS_CTE}
        SELECT o.ticker, o.time, o.close, r.ref
        FROM ohlc_1m o JOIN refs r ON r.ticker = o.ticker
        WHERE o.source='tick_agg_v1'
          AND o.close<1 AND o.open<1 AND o.high<1 AND o.low<1
          AND ABS(o.close*100 - r.ref) < ABS(o.close - r.ref)
          {where_extra}
        ORDER BY o.time DESC LIMIT 5
    """
    sample = await conn.fetch(sample_sql, *args)

    return {
        "bars_to_multiply": row["bars_to_multiply"],
        "tickers_to_fix": row["tickers_to_fix"],
        "bars_legit_penny": row["bars_legit_penny"],
        "bars_inconsistent": row["bars_inconsistent"],
        "sample": [dict(r) for r in sample],
    }


async def apply_fix(conn: asyncpg.Connection, ticker_filter: str | None) -> dict[str, int]:
    """Executa UPDATE per-bar + DELETE inconsistências."""
    where_extra = " AND o.ticker=$1" if ticker_filter else ""
    args = (ticker_filter,) if ticker_filter else ()

    # TimescaleDB: remove limite de 100k tuplas/DML em hypertables comprimidas (3.4M bars total)
    await conn.execute("SET LOCAL timescaledb.max_tuples_decompressed_per_dml_transaction = 0")

    upd_sql = f"""
        {_REFS_CTE}
        UPDATE ohlc_1m o
        SET open = o.open * 100,
            high = o.high * 100,
            low = o.low * 100,
            close = o.close * 100
        FROM refs r
        WHERE o.ticker = r.ticker
          AND o.source='tick_agg_v1'
          AND o.close<1 AND o.open<1 AND o.high<1 AND o.low<1
          AND ABS(o.close*100 - r.ref) < ABS(o.close - r.ref)
          {where_extra}
    """
    upd_res = await conn.execute(upd_sql, *args)
    updated = int(upd_res.split()[-1]) if upd_res.startswith("UPDATE") else 0

    del_where = "source='tick_agg_v1' AND ((open<1) <> (close<1) OR (open<1) <> (high<1) OR (open<1) <> (low<1))"
    if ticker_filter:
        del_where += " AND ticker=$1"
    del_sql = f"DELETE FROM ohlc_1m WHERE {del_where}"
    del_res = await conn.execute(del_sql, *args)
    deleted = int(del_res.split()[-1]) if del_res.startswith("DELETE") else 0

    return {"updated": updated, "deleted_inconsistent": deleted}


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--ticker", help="Limita a um ticker específico")
    p.add_argument("--dsn", default=DEFAULT_DSN)
    args = p.parse_args()

    if not (args.dry_run or args.apply):
        p.error("Use --dry-run ou --apply")

    conn = await _connect(args.dsn)
    try:
        print(f"\n[fix_ohlc_scale] target: {'ticker=' + args.ticker if args.ticker else 'TODOS tickers'}")
        print(f"[fix_ohlc_scale] mode: {'APPLY' if args.apply else 'DRY-RUN'}\n")

        print("--- ANALISE PRE-FIX (per-bar logic com referência profit_ticks/ohlc_1m_from_ticks) ---")
        pre = await analyze(conn, args.ticker)
        print(f"  bars para multiplicar ×100 (escala errada confirmada): {pre['bars_to_multiply']:,}")
        print(f"  tickers com bars a corrigir: {pre['tickers_to_fix']}")
        print(f"  bars legítimos penny stocks (NÃO mexer): {pre['bars_legit_penny']:,}")
        print(f"  bars inconsistentes (DELETE): {pre['bars_inconsistent']}")
        if pre["sample"]:
            print("  sample:")
            for s in pre["sample"]:
                print(f"    {s['ticker']:8s} {s['time']} close={float(s['close']):.4f} → {float(s['close'])*100:.2f} (ref={float(s['ref']):.2f})")

        if pre["bars_to_multiply"] == 0 and pre["bars_inconsistent"] == 0:
            print("\n[OK] Nada para corrigir. DB limpo.")
            return 0

        if args.dry_run:
            print(f"\n[DRY-RUN] Use --apply para multiplicar ×100 em {pre['bars_to_multiply']:,} bars + DELETE {pre['bars_inconsistent']} inconsistentes.")
            return 0

        print("\n--- EXECUTANDO ---")
        async with conn.transaction():
            res = await apply_fix(conn, args.ticker)
        print(f"  UPDATE ×100: {res['updated']:,}")
        print(f"  DELETE inconsistentes: {res['deleted_inconsistent']}")

        print("\n--- VERIFICAÇÃO ---")
        post = await analyze(conn, args.ticker)
        print(f"  bars remanescentes para multiplicar: {post['bars_to_multiply']:,}")
        print(f"  bars legítimos penny stocks (preservados): {post['bars_legit_penny']:,}")
        if post["bars_to_multiply"] == 0:
            print("  [OK] Migration completa.")
            return 0
        else:
            print(f"  [WARN] Ainda há {post['bars_to_multiply']:,} bars (provavelmente edge case)")
            return 2
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
