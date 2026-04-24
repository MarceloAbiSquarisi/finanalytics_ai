"""
Cadastra os top 500 tickers mais liquidos da B3 (mediana de volume negociado,
ultimos 180 dias de dados Fintz) em:
  - profit_history_tickers (active=TRUE)    -> backfill historico permitido
  - profit_subscribed_tickers (active=FALSE) -> usuario ativa seletivamente
    em realtime dentro do limite da licenca Nelogica (100-300 tickers tipico).

Fonte: fintz_cotacoes_ts (1.32M rows, 200+ tickers B3, 2010-2025).
Ordenacao: mediana de volume_negociado nos ultimos 180 dias de dados disponiveis.

Uso:
  docker exec -i finanalytics_timescale psql -U finanalytics -d market_data \\
    < scripts/bulk_cadastrar_top500_tickers.py
"""

import asyncio
import os

import asyncpg

DSN = os.getenv(
    "PROFIT_TIMESCALE_DSN",
    "postgresql://finanalytics:timescale_secret@localhost:5433/market_data",
).replace("postgresql+asyncpg://", "postgresql://")

TOP_N = 500
WINDOW_DAYS = 180
MIN_DAYS = 30  # ticker precisa ter >= 30 pregoes para entrar no ranking

QUERY_TOP = f"""
WITH latest AS (
    SELECT MAX(time::date) AS d FROM fintz_cotacoes_ts
),
ranked AS (
    SELECT
        f.ticker,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY COALESCE(f.volume_negociado, 0)) AS median_vol,
        COUNT(*) AS days,
        AVG(COALESCE(f.preco_fechamento, 0))::numeric(12,2) AS avg_price
    FROM fintz_cotacoes_ts f, latest
    WHERE f.time::date >= latest.d - INTERVAL '{WINDOW_DAYS} days'
      AND f.volume_negociado IS NOT NULL
      AND f.volume_negociado > 0
    GROUP BY f.ticker
    HAVING COUNT(*) >= {MIN_DAYS}
)
SELECT ticker, median_vol, days, avg_price
FROM ranked
ORDER BY median_vol DESC
LIMIT {TOP_N}
"""

UPSERT_HIST = """
INSERT INTO profit_history_tickers (ticker, exchange, active, notes)
VALUES ($1, 'B', TRUE, $2)
ON CONFLICT (ticker, exchange) DO UPDATE
SET active = TRUE,
    notes = COALESCE(profit_history_tickers.notes, '') || ' | ' || EXCLUDED.notes,
    updated_at = NOW()
"""

UPSERT_RT = """
INSERT INTO profit_subscribed_tickers (ticker, exchange, active, priority, notes)
VALUES ($1, 'B', FALSE, 0, $2)
ON CONFLICT (ticker, exchange) DO NOTHING
"""


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        print(f"Rankando top {TOP_N} tickers (mediana volume, janela {WINDOW_DAYS}d, min {MIN_DAYS} pregoes)...")
        rows = await conn.fetch(QUERY_TOP)
        print(f"  -> {len(rows)} tickers candidatos\n")

        if not rows:
            print("ERRO: nenhum ticker qualificou. Verifique fintz_cotacoes_ts.")
            return

        print("Top 10 por mediana de volume:")
        print(f"  {'TICKER':<8} {'MEDIAN_VOL':>15} {'DIAS':>5} {'PRECO_AVG':>10}")
        for r in rows[:10]:
            print(f"  {r['ticker']:<8} {int(r['median_vol']):>15,} {r['days']:>5} {float(r['avg_price']):>10.2f}")

        note = f"top{TOP_N}_liquidez_{WINDOW_DAYS}d"

        # Contagens antes do INSERT
        h_before = await conn.fetchval("SELECT COUNT(*) FROM profit_history_tickers WHERE active")
        s_before = await conn.fetchval("SELECT COUNT(*) FROM profit_subscribed_tickers")

        print("\nInserindo em profit_history_tickers (backfill historico)...")
        await conn.executemany(UPSERT_HIST, [(r["ticker"], note) for r in rows])

        print("Inserindo em profit_subscribed_tickers (realtime, active=FALSE)...")
        await conn.executemany(UPSERT_RT, [(r["ticker"], note) for r in rows])

        h_after = await conn.fetchval("SELECT COUNT(*) FROM profit_history_tickers WHERE active")
        s_after = await conn.fetchval("SELECT COUNT(*) FROM profit_subscribed_tickers")

        print("\n--- RESULTADO ---")
        print(f"  profit_history_tickers (active): {h_before} -> {h_after}  (delta +{h_after - h_before})")
        print(f"  profit_subscribed_tickers:       {s_before} -> {s_after}  (delta +{s_after - s_before})")
        print(f"\nTodos os {len(rows)} tickers cadastrados em histórico (active=TRUE).")
        print("Para ativar realtime seletivamente (respeitando limite da licenca):")
        print("  UPDATE profit_subscribed_tickers SET active=TRUE WHERE ticker IN ('PETR4','VALE3',...);")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
