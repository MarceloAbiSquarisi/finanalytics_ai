"""
Migration C1a (24/abr/2026): cria tabela account_transactions + cash_balance
em investment_accounts.

Modelo de saldo:
  - investment_accounts.cash_balance (NUMERIC): dinheiro liquido disponivel
    (saldo 'settled') — cache mantido pelo AccountTransactionService.
  - account_transactions: todas as operacoes de caixa. amount positivo = credito,
    negativo = debito. status pending|settled|cancelled.

Derivacoes em tempo real (via queries, nao colunas):
  - pending_out = SUM(amount WHERE amount < 0 AND status='pending')  -> saidas agendadas
  - pending_in  = SUM(amount WHERE amount > 0 AND status='pending')  -> entradas agendadas
  - available_to_invest = cash_balance + pending_in + pending_out     -> o que pode aplicar

Liquidacao: tx com settlement_date <= today viram 'settled' via scheduler
diario (C5 roda no open BRT). Manual via /settle-now (admin).

Uso:
  .venv\\Scripts\\python.exe scripts\\migration_account_transactions.py
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
CREATE TABLE IF NOT EXISTS account_transactions (
    id              VARCHAR(36)  PRIMARY KEY,
    user_id         VARCHAR(100) NOT NULL,
    account_id      VARCHAR(36)  NOT NULL REFERENCES investment_accounts(id) ON DELETE CASCADE,
    tx_type         VARCHAR(30)  NOT NULL,
    amount          NUMERIC(18,2) NOT NULL,
    currency        VARCHAR(3)   NOT NULL DEFAULT 'BRL',
    status          VARCHAR(20)  NOT NULL DEFAULT 'settled',
    reference_date  DATE         NOT NULL,
    settlement_date DATE,
    related_type    VARCHAR(30),
    related_id      VARCHAR(36),
    note            TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    settled_at      TIMESTAMPTZ,
    CONSTRAINT ck_tx_status CHECK (status IN ('pending','settled','cancelled')),
    CONSTRAINT ck_tx_type CHECK (tx_type IN (
        'deposit','withdraw',
        'trade_buy','trade_sell',
        'crypto_buy','crypto_sell',
        'rf_apply','rf_redeem',
        'dividend','interest','fee','tax',
        'adjustment'
    ))
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_acct_tx_user_id ON account_transactions(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_acct_tx_account_id ON account_transactions(account_id)",
    "CREATE INDEX IF NOT EXISTS ix_acct_tx_status ON account_transactions(status) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS ix_acct_tx_settlement ON account_transactions(settlement_date) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS ix_acct_tx_related ON account_transactions(related_type, related_id) WHERE related_id IS NOT NULL",
]


async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        print("=== Migration C1a: account_transactions + cash_balance ===\n")

        # 1. Coluna cash_balance em investment_accounts
        print("[1/3] investment_accounts.cash_balance...")
        await conn.execute(
            "ALTER TABLE investment_accounts "
            "ADD COLUMN IF NOT EXISTS cash_balance NUMERIC(18,2) NOT NULL DEFAULT 0"
        )
        print("  ✓ coluna garantida")

        # 2. Tabela account_transactions
        print("\n[2/3] account_transactions...")
        await conn.execute(CREATE_TABLE)
        for idx_sql in INDEXES:
            await conn.execute(idx_sql)
        print("  ✓ tabela + 5 indexes garantidos")

        # 3. Relatorio
        print("\n[3/3] Estado atual:")
        accounts = await conn.fetch(
            "SELECT id, user_id, apelido, institution_name, cash_balance "
            "FROM investment_accounts ORDER BY user_id, institution_name"
        )
        tx_count = await conn.fetchval("SELECT COUNT(*) FROM account_transactions")
        print(f"  investment_accounts: {len(accounts)} registros")
        for a in accounts:
            print(
                f"    [{a['id'][:8]}] {a['apelido'] or '(sem apelido)':<25} "
                f"{a['institution_name']:<22} cash=R$ {a['cash_balance']}"
            )
        print(f"  account_transactions: {tx_count} registros")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
