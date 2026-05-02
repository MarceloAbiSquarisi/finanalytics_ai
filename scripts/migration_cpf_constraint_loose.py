"""
Migration (24/abr): relaxa constraint CPF.

Antes: UNIQUE (user_id, cpf) WHERE cpf NOT NULL — 1 CPF por usuario.
Depois: UNIQUE (user_id, institution_code, agency, account_number) WHERE
agency IS NOT NULL AND account_number IS NOT NULL — permite multiplas
contas com mesmo CPF desde que (corretora, agencia, numero) seja unico.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://finanalytics:secret@localhost:5432/finanalytics",
).replace("postgresql+asyncpg://", "postgresql://")


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        print("=== Migration: CPF pode ter multiplas contas ===\n")

        # 1. Dropa constraint antigo se existir
        print("[1/2] Dropa UNIQUE (user_id, cpf)...")
        # O constraint pode estar como INDEX UNIQUE ou CONSTRAINT
        try:
            await conn.execute("DROP INDEX IF EXISTS uq_inv_accounts_user_cpf")
            print("  ✓ index uq_inv_accounts_user_cpf removido")
        except Exception as e:
            print(f"  skip (index): {e}")
        try:
            await conn.execute(
                "ALTER TABLE investment_accounts DROP CONSTRAINT IF EXISTS uq_inv_accounts_user_cpf"
            )
            print("  ✓ constraint uq_inv_accounts_user_cpf removido")
        except Exception as e:
            print(f"  skip (constraint): {e}")

        # 2. Novo: unique (user_id, institution_code, agency, account_number)
        print("\n[2/2] Cria UNIQUE (user_id, institution_code, agency, account_number)...")
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_inv_accounts_user_inst_ag_acc
            ON investment_accounts (user_id, institution_code, agency, account_number)
            WHERE agency IS NOT NULL AND account_number IS NOT NULL
        """)
        print("  ✓ novo index criado")

        # Relatorio
        rows = await conn.fetch("""
            SELECT user_id, titular, cpf, institution_name, agency, account_number
            FROM investment_accounts
            ORDER BY user_id, institution_name
        """)
        print(f"\nEstado atual ({len(rows)} contas):")
        for r in rows:
            print(
                f"  {r['titular'] or '—':<25} cpf={r['cpf'] or '—':<14} "
                f"{r['institution_name']:<20} ag={r['agency'] or '—'}/cc={r['account_number'] or '—'}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
