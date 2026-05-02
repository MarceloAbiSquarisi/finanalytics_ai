"""password reset tokens

Revision ID: 0003_password_reset
Revises: 0002_portfolio_multi
Create Date: 2026-03-15

Adiciona colunas de reset de senha na tabela users:
  - reset_token       — token UUID gerado no pedido de reset
  - reset_token_exp   — validade do token (30 minutos)
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_password_reset"
down_revision = "0002_portfolio_multi"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("reset_token", sa.String(64), nullable=True, server_default=None)
    )
    op.add_column("users", sa.Column("reset_token_exp", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_users_reset_token",
        "users",
        ["reset_token"],
        unique=True,
        postgresql_where=sa.text("reset_token IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_reset_token", table_name="users")
    op.drop_column("users", "reset_token_exp")
    op.drop_column("users", "reset_token")
