"""0016_portfolio_name_history

Cria tabela portfolio_name_history para auditoria de renames de portfolio.
Cada registro = uma transicao old_name -> new_name (timestamp + autor).

Decisao (Sprint UX 21/abr/2026): historico completo (multi-rename) em
tabela dedicada em vez de colunas previous_name no portfolios. Permite
timeline na UI e investigacao de auditoria.

Revision ID: 0016_portfolio_name_history
Revises: 0015_portfolio_soft_delete
Create Date: 2026-04-21
"""
from alembic import op
import sqlalchemy as sa


revision = "0016_portfolio_name_history"
down_revision = "0015_portfolio_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_name_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "portfolio_id",
            sa.String(36),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("old_name", sa.String(200), nullable=False),
        sa.Column("new_name", sa.String(200), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # changed_by nullable: futuras renames automaticas (sistema/import)
        # podem ficar sem autor explicito.
        sa.Column("changed_by", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_pf_name_history_portfolio_changed",
        "portfolio_name_history",
        ["portfolio_id", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pf_name_history_portfolio_changed", table_name="portfolio_name_history")
    op.drop_table("portfolio_name_history")
