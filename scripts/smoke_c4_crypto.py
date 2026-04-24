"""Smoke test C4: aporte + resgate cripto gera cash tx settled D+0."""
from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from finanalytics_ai.infrastructure.database.repositories.wallet_repo import WalletRepository


async def main() -> None:
    repo = WalletRepository()
    accounts = await repo.list_all_accounts()
    acc = accounts[0]
    print(f"Conta: {acc['id'][:8]} {acc.get('apelido') or acc['institution_name']}")
    print(f"Cash antes: R$ {acc.get('cash_balance', 0)}\n")

    from sqlalchemy import text
    from finanalytics_ai.infrastructure.database.connection import get_session
    async with get_session() as s:
        pfid = (await s.execute(text("SELECT id FROM portfolios WHERE user_id = :u LIMIT 1"), {"u": acc["user_id"]})).scalar()

    # Aporte 1: cria holding BTC 0.1 @ R$ 200.000 = 20k
    h1 = await repo.upsert_crypto({
        "user_id": acc["user_id"],
        "symbol": "BTC",
        "quantity": Decimal("0.1"),
        "average_price_brl": Decimal("200000"),
        "investment_account_id": acc["id"],
        "portfolio_id": pfid,
    })
    print(f"Aporte 1: BTC 0.1 @ 200k → cash_tx_created={h1.get('cash_tx_created')}")
    print(f"  crypto_id: {h1['id'][:8]}")

    summary = await repo.get_cash_summary(acc["id"], acc["user_id"])
    print(f"  cash agora: R$ {summary['cash_balance']}\n")

    # Aporte 2 (update): aumenta pra 0.15 @ R$ 210.000 (delta 0.05 x 210k = 10.5k debito)
    h2 = await repo.upsert_crypto({
        "user_id": acc["user_id"],
        "symbol": "BTC",
        "quantity": Decimal("0.15"),
        "average_price_brl": Decimal("210000"),
        "investment_account_id": acc["id"],
        "portfolio_id": pfid,
    })
    print(f"Aporte 2: BTC 0.05 extra @ 210k → cash_tx_created={h2.get('cash_tx_created')}")
    summary = await repo.get_cash_summary(acc["id"], acc["user_id"])
    print(f"  cash: R$ {summary['cash_balance']}\n")

    # Resgate parcial: 0.08 @ 210k = 16.8k credito
    r1 = await repo.redeem_crypto(h1["id"], acc["user_id"], 0.08)
    print(f"Resgate parcial 0.08 → {r1}")
    summary = await repo.get_cash_summary(acc["id"], acc["user_id"])
    print(f"  cash: R$ {summary['cash_balance']}\n")

    # Delete (fecha restante: 0.07 @ 210k = 14.7k)
    await repo.delete_crypto(h1["id"], acc["user_id"])
    print(f"Delete (fecha posicao restante)")
    summary = await repo.get_cash_summary(acc["id"], acc["user_id"])
    print(f"  cash final: R$ {summary['cash_balance']}")

    # Lista todas as tx crypto criadas
    txs = await repo.list_transactions(user_id=acc["user_id"], account_id=acc["id"], limit=10)
    print(f"\nUltimas {len(txs)} tx da conta:")
    for tx in txs[:6]:
        print(f"  {tx['tx_type']:<12} R$ {tx['amount']:>10} status={tx['status']} note={tx.get('note','')}")


if __name__ == "__main__":
    asyncio.run(main())
