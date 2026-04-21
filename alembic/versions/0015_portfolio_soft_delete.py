"""0015_portfolio_soft_delete

Adiciona coluna `is_active` em portfolios para suporte a soft-delete.

Decisao (Sprint UX 21/abr/2026): em vez de deletar portfolios, marcamos
como inativos. Preserva integridade referencial (5 tabelas com FK
portfolio_id ON DELETE RESTRICT) e historico de trades. Inativacao so
permitida quando nenhuma aplicacao do portfolio tem saldo > 0.

Revision ID: 0015_portfolio_soft_delete
Revises: 0014_import_tables
Create Date: 2026-04-21
"""
from alembic import op
import sqlalchemy as sa


revision = "0015_portfolio_soft_delete"
down_revision = "0014_import_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolios",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    # Index parcial: 99% das queries listam apenas ativos. Index pequeno
    # acelera o WHERE is_active = TRUE em users com muitos portfolios.
    op.create_index(
        "ix_portfolios_user_active",
        "portfolios",
        ["user_id"],
        postgresql_where=sa.text("is_active = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("ix_portfolios_user_active", table_name="portfolios")
    op.drop_column("portfolios", "is_active")
