"""
Backfill G1 (refactor 25/abr): cria 1 portfolio chamado 'Portfolio' para cada
conta de investimento ativa que ainda nao tem (modelo simplificado N->1).

Idempotente: pula contas que ja tem portfolio ativo.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def main() -> None:
    from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository

    repo = WalletRepository()
    print("=== Backfill portfolio (1 por conta) para contas ativas ===\n")

    accounts = await repo.list_all_accounts(include_inactive=False)
    print(f"{len(accounts)} contas ativas.\n")

    for acc in accounts:
        label = acc.get("apelido") or acc.get("institution_name") or f"Conta {acc['id'][:8]}"
        print(f"→ {acc['id'][:8]} {label}")
        await repo._ensure_default_portfolios(
            user_id=str(acc["user_id"]),
            account_id=str(acc["id"]),
            account_label=label,
        )

    # Lista estado final
    from sqlalchemy import text

    from finanalytics_ai.infrastructure.database.connection import get_session
    async with get_session() as s:
        rows = (await s.execute(text("""
            SELECT p.id, p.name, p.investment_account_id, a.apelido, a.institution_name
            FROM portfolios p
            LEFT JOIN investment_accounts a ON a.id = p.investment_account_id
            WHERE p.is_active = true
            ORDER BY a.institution_name, p.name
        """))).mappings().all()

    print(f"\nEstado final — {len(rows)} portfolios ativos:")
    for r in rows:
        acc_lbl = (r.get("apelido") or r.get("institution_name") or "(orfão)") if r["investment_account_id"] else "(sem conta)"
        print(f"  {r['id'][:8]} {r['name']:<40} → {acc_lbl}")


if __name__ == "__main__":
    asyncio.run(main())
