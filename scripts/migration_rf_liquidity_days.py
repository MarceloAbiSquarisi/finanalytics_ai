"""
Migration C5 (24/abr/2026): adiciona rf_holdings.liquidity_days (dias para
liquidar resgate). Default por bond_type:
  - poupanca        : D+0
  - tesouro_direto  : D+1 (normalmente)
  - cdb             : D+1 (liquidez diaria) ou variavel (ver maturity_date)
  - lci / lca / lcd : D+30 (carencia tipica)
  - debenture       : D+3
  - fundo_rf        : D+30
Campo editavel ao cadastrar o titulo — esses sao apenas defaults.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://finanalytics:secret@localhost:5432/finanalytics",
).replace("postgresql+asyncpg://", "postgresql://")


DEFAULTS = {
    "poupanca": 0,
    "tesouro_direto": 1,
    "tesouro": 1,
    "cdb": 1,
    "lci": 30,
    "lca": 30,
    "lcd": 30,
    "cra": 30,
    "cri": 30,
    "debenture": 3,
    "debentures": 3,
    "fundo_rf": 30,
    "fundo": 30,
}


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        print("=== Migration C5: rf_holdings.liquidity_days ===\n")
        await conn.execute(
            "ALTER TABLE rf_holdings ADD COLUMN IF NOT EXISTS liquidity_days INTEGER NOT NULL DEFAULT 1"
        )
        print("[1/2] ✓ coluna liquidity_days adicionada (default 1)")

        print("\n[2/2] Backfill defaults por bond_type:")
        for bt, days in DEFAULTS.items():
            r = await conn.execute(
                "UPDATE rf_holdings SET liquidity_days = $1 WHERE LOWER(bond_type) = $2 AND liquidity_days = 1 AND $1 <> 1",
                days,
                bt,
            )
            if r and r.split()[-1] != "0":
                print(f"  {bt:<18} → D+{days} ({r})")

        rows = await conn.fetch(
            "SELECT bond_type, bond_name, liquidity_days FROM rf_holdings ORDER BY bond_type"
        )
        if rows:
            print(f"\nEstado atual ({len(rows)} holdings):")
            for r in rows:
                print(f"  {r['bond_type']:<15} {r['bond_name']:<40} D+{r['liquidity_days']}")
        else:
            print("\nNenhum holding RF cadastrado (migration apenas add coluna).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
