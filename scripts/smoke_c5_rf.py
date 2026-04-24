"""Smoke test C5: aplicacao RF (debita D+0) + resgate RF (credita D+liquidity)."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


async def main() -> None:
    from finanalytics_ai.infrastructure.database.connection import get_session
    from finanalytics_ai.infrastructure.database.repositories.rf_repo import RFPortfolioRepository
    from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository

    w = WalletRepository()
    accs = await w.list_all_accounts()
    acc = accs[0]
    print(f"Conta: {acc['id'][:8]} {acc.get('apelido') or acc['institution_name']}")
    print(f"Cash antes: R$ {acc.get('cash_balance', 0)}\n")

    from sqlalchemy import text
    async with get_session() as s:
        pfid = (await s.execute(text("SELECT id FROM portfolios WHERE user_id = :u LIMIT 1"), {"u": acc["user_id"]})).scalar()
        # vincula portfolio a investment_account se nao estiver
        await s.execute(text("UPDATE portfolios SET investment_account_id = :a WHERE id = :p AND investment_account_id IS NULL"),
                        {"a": acc["id"], "p": pfid})
        await s.commit()
    print(f"Portfolio: {pfid[:8]}\n")

    # Cria holding LCI D+30
    async with get_session() as s:
        repo = RFPortfolioRepository(s)
        h = await repo.add_holding(
            portfolio_id=pfid,
            bond_id="LCI-BTG-2027",
            bond_name="LCI BTG 2027",
            bond_type="lci",
            indexer="CDI",
            issuer="BTG",
            invested=10000.0,
            rate_annual=95.0,
            rate_pct_indexer=True,
            purchase_date=date.today(),
            maturity_date=date(2027, 4, 24),
            ir_exempt=True,
            note="LCI teste C5",
        )
        await s.commit()
        print(f"LCI aplicada: {h.holding_id[:8]} R$ {h.invested} (lci → D+30)")

    summary = await w.get_cash_summary(acc["id"], acc["user_id"])
    print(f"  cash: R$ {summary['cash_balance']} (aplicacao settled imediata)\n")

    # Resgate parcial R$ 3000 — cria tx pending D+30
    async with get_session() as s2:
        repo2 = RFPortfolioRepository(s2)
        r = await repo2.redeem_holding(h.holding_id, pfid, 3000.0)
        await s2.commit()
        print(f"Resgate 3000: status={r.get('status')} D+{r.get('liquidity_days')} settle={r.get('settlement_date')}")

    summary = await w.get_cash_summary(acc["id"], acc["user_id"])
    print(f"\nResumo cash:")
    print(f"  cash_balance: R$ {summary['cash_balance']} (ainda nao liquidou)")
    print(f"  pending_in:   R$ {summary['pending_in']} (resgate futuro D+30)")
    print(f"  pending_out:  R$ {summary['pending_out']}")
    print(f"  available:    R$ {summary['available_to_invest']}")

    # Simula o scheduler rodando em T+30 para liquidar
    from datetime import date as _date, timedelta as _td
    future = _date.today() + _td(days=31)
    print(f"\n== Simulando scheduler rodando em {future} ==")
    settled = await w.settle_due_transactions(future)
    print(f"Transacoes liquidadas: {settled}")

    summary = await w.get_cash_summary(acc["id"], acc["user_id"])
    print(f"\nApos settle:")
    print(f"  cash_balance: R$ {summary['cash_balance']}")
    print(f"  pending_in:   R$ {summary['pending_in']}")

    # Cleanup
    async with get_session() as s3:
        repo3 = RFPortfolioRepository(s3)
        await repo3.delete_holding(h.holding_id, pfid)
        await s3.commit()
        print(f"\nHolding deletado, tx canceladas, cash revertido.")

    summary = await w.get_cash_summary(acc["id"], acc["user_id"])
    print(f"Cash final: R$ {summary['cash_balance']}")


if __name__ == "__main__":
    asyncio.run(main())
