"""Smoke test C3: cria um trade e valida que gerou account_transaction pending D+1."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository


async def main() -> None:
    repo = WalletRepository()
    # Pega primeira conta disponivel
    accounts = await repo.list_all_accounts()
    if not accounts:
        print("Nenhuma investment_account — cria uma antes.")
        return
    acc = accounts[0]
    print(f"Conta: {acc['id'][:8]} {acc.get('apelido') or acc['institution_name']}")
    cash_before = acc.get("cash_balance", 0)
    print(f"Cash antes: R$ {cash_before}")

    # Pega um portfolio (trades.portfolio_id e NOT NULL)
    from sqlalchemy import select, text

    from finanalytics_ai.infrastructure.database.connection import get_session

    async with get_session() as s:
        pfid = (
            await s.execute(
                text("SELECT id FROM portfolios WHERE user_id = :u LIMIT 1"), {"u": acc["user_id"]}
            )
        ).scalar()
    print(f"Portfolio: {pfid[:8] if pfid else 'NONE'}")

    # Cria trade BUY
    trade = await repo.create_trade(
        {
            "user_id": acc["user_id"],
            "ticker": "PETR4",
            "asset_class": "stock",
            "operation": "buy",
            "quantity": 100,
            "unit_price": 30.00,
            "fees": 5.00,
            "trade_date": date.today(),
            "investment_account_id": acc["id"],
            "portfolio_id": pfid,
        }
    )
    print(
        f"\nTrade criado: {trade['id'][:8]} {trade['ticker']} "
        f"{trade['operation']} x{trade['quantity']} total=R$ {trade['total_cost']}"
    )
    print(f"  cash_tx_created: {trade.get('cash_tx_created')}")
    print(f"  warning: {trade.get('warning') or 'nenhum'}")

    # Verifica tx pendente
    txs = await repo.list_transactions(
        user_id=acc["user_id"], account_id=acc["id"], status="pending", limit=5
    )
    print(f"\nTransacoes pendentes da conta ({len(txs)}):")
    for tx in txs[:3]:
        print(
            f"  [{tx['id'][:8]}] {tx['tx_type']} R$ {tx['amount']} "
            f"ref={tx['reference_date']} settle={tx['settlement_date']} "
            f"related={tx['related_type']}/{(tx.get('related_id') or '')[:8]}"
        )
        print(f"    note: {tx.get('note')}")

    # Resumo
    summary = await repo.get_cash_summary(acc["id"], acc["user_id"])
    print("\nResumo cash da conta:")
    print(f"  cash_balance: R$ {summary['cash_balance']}")
    print(f"  pending_in: R$ {summary['pending_in']}")
    print(f"  pending_out: R$ {summary['pending_out']}")
    print(f"  available_to_invest: R$ {summary['available_to_invest']}")

    # Cleanup: deleta o trade de teste
    await repo.delete_trade(trade["id"], acc["user_id"])
    print("\nTrade deletado (reverte tx).")
    summary2 = await repo.get_cash_summary(acc["id"], acc["user_id"])
    print("Resumo apos delete:")
    print(f"  cash_balance: R$ {summary2['cash_balance']}")
    print(f"  pending_in: R$ {summary2['pending_in']}")
    print(f"  pending_out: R$ {summary2['pending_out']}")


if __name__ == "__main__":
    asyncio.run(main())
