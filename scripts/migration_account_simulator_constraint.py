"""
Migration: conta Simulador é única no sistema.

Motivação: DLL Nelogica tem apenas 1 credencial de simulador
(PROFIT_SIM_BROKER_ID/ACCOUNT_ID/ROUTING_PASSWORD no .env). Não faz sentido
ter múltiplas contas account_type='simulator' no trading_accounts.

O que faz (idempotente):
  1. DELETE de contas 'simulator' claramente fake ou duplicadas
     (mantém a que bate com PROFIT_SIM_BROKER_ID do .env).
  2. CREATE UNIQUE INDEX ux_only_one_simulator garantindo que só
     UMA linha com account_type='simulator' pode existir.

Uso:
  .venv\\Scripts\\python.exe scripts\\migration_account_simulator_constraint.py
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://finanalytics:secret@localhost:5432/finanalytics",
).replace("postgresql+asyncpg://", "postgresql://")

SIM_BROKER_ID = os.getenv("PROFIT_SIM_BROKER_ID", "15011")
SIM_ACCOUNT_ID = os.getenv("PROFIT_SIM_ACCOUNT_ID", "216541264267275")


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        print("=== Migration: consolidar conta Simulador ===\n")

        # 1. Lista contas simulator existentes
        sims = await conn.fetch(
            """
            SELECT uuid, broker_id, account_id, label, status
            FROM trading_accounts
            WHERE account_type = 'simulator'
            ORDER BY (broker_id = $1 AND account_id = $2) DESC, created_at
        """,
            SIM_BROKER_ID,
            SIM_ACCOUNT_ID,
        )
        print(f"Contas 'simulator' encontradas: {len(sims)}")
        for s in sims:
            tag = (
                "  KEEPING "
                if s["broker_id"] == SIM_BROKER_ID and s["account_id"] == SIM_ACCOUNT_ID
                else "  REMOVE  "
            )
            print(
                f"  {tag} {s['uuid']} broker={s['broker_id']} acc={s['account_id']} label='{s['label']}' status={s['status']}"
            )

        # 2. Deleta todas exceto a que bate com PROFIT_SIM_*
        keep_uuid = None
        for s in sims:
            if s["broker_id"] == SIM_BROKER_ID and s["account_id"] == SIM_ACCOUNT_ID:
                keep_uuid = s["uuid"]
                break

        if not keep_uuid and sims:
            print(
                f"\nATENÇÃO: nenhuma conta bate com PROFIT_SIM_* ({SIM_BROKER_ID}/{SIM_ACCOUNT_ID})."
            )
            print("Mantendo a primeira encontrada. Ajuste manualmente depois se necessário.")
            keep_uuid = sims[0]["uuid"]

        if keep_uuid:
            deleted = await conn.execute(
                """
                DELETE FROM trading_accounts
                WHERE account_type = 'simulator' AND uuid <> $1
            """,
                keep_uuid,
            )
            print(f"\nDeletadas (simulator duplicadas): {deleted}")

        # 3. Cria unique partial index (idempotente)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_trading_accounts_only_one_simulator
            ON trading_accounts (account_type)
            WHERE account_type = 'simulator'
        """)
        print("Unique index 'ux_trading_accounts_only_one_simulator' garantido.")

        # 4. Estado final
        final = await conn.fetch("""
            SELECT uuid, broker_id, account_id, account_type, label, status
            FROM trading_accounts
            ORDER BY account_type, label
        """)
        print(f"\nEstado final ({len(final)} contas):")
        for r in final:
            print(
                f"  [{r['account_type']:10s}] {r['label']:30s} {r['broker_id']:>6}/{r['account_id']:<18} {r['status']}"
            )

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
