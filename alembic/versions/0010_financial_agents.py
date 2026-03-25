"""
Sprint S — Agentes Financeiros + Página Admin
Revision ID: 0010_financial_agents
Revises: 0009_investment_accounts
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0010_financial_agents"
down_revision: Union[str, None] = "0009_investment_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "financial_agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("code", sa.String(20), nullable=True),        # CNPJ / SWIFT / BIC
        sa.Column("agent_type", sa.String(30), nullable=False, server_default="corretora"),
        sa.Column("country", sa.String(3), nullable=False, server_default="BRA"),
        sa.Column("website", sa.String(300), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_financial_agents_name", "financial_agents", ["name"])
    op.create_index("ix_financial_agents_type", "financial_agents", ["agent_type"])


def downgrade() -> None:
    op.drop_table("financial_agents")
