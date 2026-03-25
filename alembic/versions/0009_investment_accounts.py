"""
Sprint R — Multi-usuário + Contas de Investimento + Carteira Completa
Revision ID: 0009_investment_accounts
Revises: 0008_user_2fa_remember

Cria:
  - Coluna users.role aceita 'master' (além de 'user' e 'admin')
  - investment_accounts  — conta bancária/corretora do usuário
  - trades               — histórico de compras/vendas (ações, ETFs, cripto)
                           base para cálculo de preço médio
  - crypto_holdings      — posição consolidada de criptomoedas
  - other_assets         — ativos genéricos (imóveis, FII, previdência etc.)
  
Altera:
  - portfolios: adiciona investment_account_id (FK nullable, retrocompatível)
  - positions:  adiciona investment_account_id (FK nullable)
  - rf_holdings: adiciona investment_account_id (FK nullable)
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0009_investment_accounts"
down_revision: Union[str, None] = "0008_user_2fa_remember"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. investment_accounts ─────────────────────────────────────────────
    op.create_table(
        "investment_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("institution_name", sa.String(200), nullable=False),
        sa.Column("institution_code", sa.String(20), nullable=True),   # CNPJ/SWIFT/etc
        sa.Column("agency", sa.String(20), nullable=True),
        sa.Column("account_number", sa.String(50), nullable=True),
        sa.Column("country", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("account_type", sa.String(30), nullable=False, server_default="corretora"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_inv_accounts_user_id", "investment_accounts", ["user_id"])

    # ── 2. trades — histórico de compras/vendas para preço médio ──────────
    op.create_table(
        "trades",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("investment_account_id", sa.String(36), nullable=True),
        sa.Column("portfolio_id", sa.String(36), nullable=True),
        # Ativo
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("asset_class", sa.String(30), nullable=False),  # stock|etf|crypto|fii|bdr
        sa.Column("operation", sa.String(10), nullable=False),    # buy|sell|split|bonus
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("unit_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("total_cost", sa.Numeric(24, 8), nullable=False),  # qty * price + fees
        sa.Column("fees", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["investment_account_id"], ["investment_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_trades_user_id", "trades", ["user_id"])
    op.create_index("ix_trades_ticker", "trades", ["ticker"])
    op.create_index("ix_trades_portfolio_id", "trades", ["portfolio_id"])
    op.create_index("ix_trades_trade_date", "trades", ["trade_date"])

    # ── 3. crypto_holdings — posição consolidada de cripto ─────────────────
    op.create_table(
        "crypto_holdings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("investment_account_id", sa.String(36), nullable=True),
        sa.Column("portfolio_id", sa.String(36), nullable=True),
        sa.Column("symbol", sa.String(20), nullable=False),      # BTC, ETH, SOL...
        sa.Column("quantity", sa.Numeric(30, 12), nullable=False),
        sa.Column("average_price_brl", sa.Numeric(24, 8), nullable=False),
        sa.Column("average_price_usd", sa.Numeric(24, 8), nullable=True),
        sa.Column("exchange", sa.String(100), nullable=True),     # Binance, Coinbase...
        sa.Column("wallet_address", sa.String(200), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["investment_account_id"], ["investment_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_crypto_holdings_user_id", "crypto_holdings", ["user_id"])
    op.create_unique_constraint("uq_crypto_user_symbol_account", "crypto_holdings",
                                ["user_id", "symbol", "investment_account_id"])

    # ── 4. other_assets — ativos genéricos ────────────────────────────────
    op.create_table(
        "other_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("investment_account_id", sa.String(36), nullable=True),
        sa.Column("portfolio_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("asset_type", sa.String(50), nullable=False),   # imovel|previdencia|coe|debenture|outro
        sa.Column("current_value", sa.Numeric(24, 8), nullable=False),
        sa.Column("invested_value", sa.Numeric(24, 8), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("acquisition_date", sa.Date, nullable=True),
        sa.Column("maturity_date", sa.Date, nullable=True),
        sa.Column("ir_exempt", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["investment_account_id"], ["investment_accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_other_assets_user_id", "other_assets", ["user_id"])

    # ── 5. Altera portfolios — adiciona investment_account_id ──────────────
    op.add_column("portfolios",
        sa.Column("investment_account_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_portfolios_inv_account",
        "portfolios", "investment_accounts",
        ["investment_account_id"], ["id"], ondelete="SET NULL"
    )

    # ── 6. Altera positions — adiciona investment_account_id ───────────────
    op.add_column("positions",
        sa.Column("investment_account_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_positions_inv_account",
        "positions", "investment_accounts",
        ["investment_account_id"], ["id"], ondelete="SET NULL"
    )

    # ── 7. Altera rf_holdings — adiciona investment_account_id ────────────
    op.add_column("rf_holdings",
        sa.Column("investment_account_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_rf_holdings_inv_account",
        "rf_holdings", "investment_accounts",
        ["investment_account_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_rf_holdings_inv_account", "rf_holdings", type_="foreignkey")
    op.drop_column("rf_holdings", "investment_account_id")
    op.drop_constraint("fk_positions_inv_account", "positions", type_="foreignkey")
    op.drop_column("positions", "investment_account_id")
    op.drop_constraint("fk_portfolios_inv_account", "portfolios", type_="foreignkey")
    op.drop_column("portfolios", "investment_account_id")
    op.drop_table("other_assets")
    op.drop_table("crypto_holdings")
    op.drop_table("trades")
    op.drop_table("investment_accounts")
