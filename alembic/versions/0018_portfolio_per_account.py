"""0018_portfolio_per_account

Simplifica modelo: 1 portfolio por conta de investimento (em vez de N).

Motivacao: usuario nao usa multiplos portfolios por conta na pratica
(separacao por estrategia/asset class fica em filtros nas telas, nao em
portfolios distintos). Reduzir o cardinalidade simplifica drasticamente
a UX (selectors, filtros, navegacao).

Estrategia:
  1. Mescla portfolios duplicados por conta. No nosso caso atual:
     - Re-FK "Carteira Principal" (5 positions, R$30k) de BTG Day Trade
       (inativa) -> XP Teste 2 (ativa).
     - Deleta 3 portfolios vazios das contas duplicadas.
     - XP Investimentos e BTG Day Trade (ambas inativas) ficam sem portfolio.
  2. Renomeia os portfolios sobreviventes para "Portfolio" (nome unico
     por conta).
  3. DROP COLUMN portfolios.is_default (vestigial — todas as contas tem
     1 portfolio so, default deixa de fazer sentido).
  4. ADD partial unique index: 1 portfolio ativo por conta.

Notas:
  - investment_account_id continua nullable na coluna (compat com soft-delete
    de conta via FK SET NULL). Invariante "novo portfolio precisa de conta"
    e validado no service, nao no schema.
  - Usar IDs literais aqui e seguro — sao do banco de dev/master atual.
    Em outros ambientes a migration ainda funciona (UPDATE/DELETE no-op
    se IDs nao existirem).

Revision ID: 0018_portfolio_per_account
Revises: 0017_user_is_admin_flag
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa


revision = "0018_portfolio_per_account"
down_revision = "0017_user_is_admin_flag"
branch_labels = None
depends_on = None


# IDs do estado atual (dev/master) — gerados manualmente; UPDATE/DELETE
# por id seguros (no-op em ambientes onde nao existem).
PORTFOLIO_CARTEIRA_PRINCIPAL = "ece83b21-53d6-484b-8af5-7041b003a7a6"
ACCOUNT_BTG = "7c20f341-4fa9-45c9-bffb-f17411633e91"
ACCOUNT_XP_TESTE_2 = "c34b546d-030c-4af7-b051-add8d089d55a"
PORTFOLIO_PRINCIPAL_XP = "31bc3cb9-855b-425a-8d4d-3ca34cb5839c"
PORTFOLIO_RF_XP = "7bdb6c6b-7434-4642-8fd5-eea22c1844f5"
PORTFOLIO_RF_SIM = "78d09d51-7f2f-4905-aad7-53c00b732624"


def upgrade() -> None:
    # 1. Re-FK Carteira Principal (BTG Day Trade inativa) -> XP Teste 2 ativa
    op.execute(
        f"UPDATE portfolios SET investment_account_id = '{ACCOUNT_XP_TESTE_2}' "
        f"WHERE id = '{PORTFOLIO_CARTEIRA_PRINCIPAL}' "
        f"AND investment_account_id = '{ACCOUNT_BTG}'"
    )

    # 2. Deleta portfolios vazios (positions/trades/holdings = 0 confirmado).
    #    Os 2 da XP Teste 2 (vazios) e o RF da Simulador (vazio).
    #    Defesa: so deleta se realmente sem holdings/trades vinculados.
    op.execute(
        f"DELETE FROM portfolios WHERE id IN ("
        f"'{PORTFOLIO_PRINCIPAL_XP}', '{PORTFOLIO_RF_XP}', '{PORTFOLIO_RF_SIM}'"
        f") AND id NOT IN (SELECT DISTINCT portfolio_id FROM positions WHERE portfolio_id IS NOT NULL)"
        f"  AND id NOT IN (SELECT DISTINCT portfolio_id FROM trades WHERE portfolio_id IS NOT NULL)"
        f"  AND id NOT IN (SELECT DISTINCT portfolio_id FROM rf_holdings WHERE portfolio_id IS NOT NULL)"
        f"  AND id NOT IN (SELECT DISTINCT portfolio_id FROM crypto_holdings WHERE portfolio_id IS NOT NULL)"
        f"  AND id NOT IN (SELECT DISTINCT portfolio_id FROM other_assets WHERE portfolio_id IS NOT NULL)"
    )

    # 3. Renomeia portfolios sobreviventes para "Portfolio" (1 por conta agora).
    #    Faz isso para TODOS os portfolios ativos com conta vinculada.
    op.execute(
        "UPDATE portfolios SET name = 'Portfolio', updated_at = NOW() "
        "WHERE is_active = true AND investment_account_id IS NOT NULL"
    )

    # 4. Drop indice + coluna is_default.
    op.execute("DROP INDEX IF EXISTS ix_portfolios_user_is_default")
    op.drop_column("portfolios", "is_default")

    # 5. Partial unique: 1 portfolio ativo por conta.
    op.create_index(
        "ux_portfolios_one_active_per_account",
        "portfolios",
        ["investment_account_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true AND investment_account_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_portfolios_one_active_per_account", table_name="portfolios")
    op.add_column(
        "portfolios",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.alter_column("portfolios", "is_default", server_default=None)
    op.create_index(
        "ix_portfolios_user_is_default",
        "portfolios",
        ["user_id", "is_default"],
    )
    # Os portfolios deletados/renomeados nao podem ser recuperados;
    # esse downgrade so reverte schema, nao dados.
