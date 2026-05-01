"""
Migration C3b (24/abr/2026): cria tabela etf_metadata.

Guarda metadados do ticker ETF (benchmark, taxa administracao, taxa
performance) — sao atributos do PAPEL, nao de cada trade individual.
Ao criar um trade com asset_class='etf', o usuario preenche/atualiza
esses campos no cadastro do ETF (form separado).

Seed: populamos com os ETFs brasileiros mais liquidos listados na B3.

Uso:
  .venv\\Scripts\\python.exe scripts\\migration_etf_metadata.py
"""
from __future__ import annotations

import asyncio
import os

import asyncpg

DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://finanalytics:secret@localhost:5432/finanalytics",
).replace("postgresql+asyncpg://", "postgresql://")


CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS etf_metadata (
    ticker       VARCHAR(20)  PRIMARY KEY,
    name         VARCHAR(200),
    benchmark    VARCHAR(100),
    mgmt_fee     NUMERIC(6,4),    -- taxa de administracao em % (ex: 0.1000 = 0.10%)
    perf_fee     NUMERIC(6,4),    -- taxa de performance em % (normalmente 0 nos ETFs)
    isin         VARCHAR(12),
    note         TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by   VARCHAR(100)
)
"""

# Seed com ETFs mais liquidos da B3 (taxas publicas, dados 2026)
SEED = [
    ("BOVA11", "iShares Ibovespa", "IBOVESPA",       0.10, 0.00, "BRBOVACTF007"),
    ("SMAL11", "iShares Small Cap", "SMLL",          0.50, 0.00, "BRSMALCTF004"),
    ("IVVB11", "iShares S&P 500",  "S&P 500 (BRL)",  0.23, 0.00, "BRIVVBCTF000"),
    ("HASH11", "Hashdex Nasdaq Cryp","Nasdaq Crypto",1.30, 0.00, None),
    ("BBSD11", "BB ETF S&P Div BR","S&P Dividendos", 0.50, 0.00, None),
    ("DIVO11", "ETF Div BR",      "IDIV",            0.50, 0.00, None),
    ("FIND11", "Itau Financ",     "IFNC",            0.50, 0.00, None),
    ("MATB11", "Itau Materiais",  "IMAT",            0.50, 0.00, None),
    ("SPXI11", "iShares S&P 500 Div","S&P 500 Div",  0.28, 0.00, None),
    ("ECOO11", "iShares Carbono", "ICO2",            0.28, 0.00, None),
]


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        print("=== Migration C3b: etf_metadata ===\n")
        print("[1/2] Criando tabela...")
        await conn.execute(CREATE_TABLE)
        print("  ✓ tabela garantida")

        print("\n[2/2] Seed com ETFs liquidos da B3...")
        for ticker, name, bench, mgmt, perf, isin in SEED:
            await conn.execute(
                """
                INSERT INTO etf_metadata (ticker, name, benchmark, mgmt_fee, perf_fee, isin, updated_by, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'migration', NOW())
                ON CONFLICT (ticker) DO NOTHING
                """,
                ticker, name, bench, mgmt, perf, isin,
            )
        rows = await conn.fetch("SELECT ticker, name, benchmark, mgmt_fee FROM etf_metadata ORDER BY ticker")
        print(f"  ✓ {len(rows)} ETFs na tabela:")
        for r in rows:
            print(f"    {r['ticker']:<8} {r['name']:<30} benchmark={r['benchmark']:<16} adm={r['mgmt_fee']}%")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
