"""2FA TOTP e remember_me para users
Revision ID: 0008_user_2fa_remember
Revises: 53e92a4075c2
Create Date: 2026-03-23

Adiciona suporte a:
  - 2FA via TOTP (Google Authenticator, Authy)
  - remember_me: duração de sessão configurável
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_user_2fa_remember"
down_revision = "53e92a4075c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "totp_secret", sa.String(64), nullable=True, server_default=None
    ))
    op.add_column("users", sa.Column(
        "totp_enabled", sa.Boolean, nullable=False, server_default=sa.false()
    ))


def downgrade() -> None:
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret")
