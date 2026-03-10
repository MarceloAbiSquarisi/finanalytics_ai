"""baseline: tabelas existentes antes do sprint múltiplas carteiras

Revision ID: 0001_baseline
Revises:
Create Date: 2025-01-01 00:00:00.000000

Representa o estado do banco ANTES da feature múltiplas carteiras.
Se o banco já existe, rode: alembic stamp 0001_baseline
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(200), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(200), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("role", sa.String(50), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("last_login_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # ── portfolios ─────────────────────────────────────────────────────────
    op.create_table(
        "portfolios",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("cash", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])

    # ── positions ──────────────────────────────────────────────────────────
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "portfolio_id",
            sa.String(36),
            sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("average_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("asset_class", sa.String(30), nullable=False, server_default="stock"),
        sa.UniqueConstraint("portfolio_id", "ticker", name="uq_portfolio_ticker"),
    )

    # ── market_events ──────────────────────────────────────────────────────
    op.create_table(
        "market_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime, nullable=False),
        sa.Column("processed_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_market_events_ticker", "market_events", ["ticker"])
    op.create_index("ix_market_events_status", "market_events", ["status"])

    # ── alerts ─────────────────────────────────────────────────────────────
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False, index=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("threshold", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("triggered_at", sa.DateTime, nullable=True),
        sa.Column("expires_at", sa.DateTime, nullable=True),
    )

    # ── watchlist_items ────────────────────────────────────────────────────
    op.create_table(
        "watchlist_items",
        sa.Column("item_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False, index=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("added_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("user_id", "ticker", name="uq_watchlist_user_ticker"),
    )

    # ── smart_alerts ───────────────────────────────────────────────────────
    op.create_table(
        "smart_alerts",
        sa.Column("alert_id", sa.String(36), primary_key=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("user_id", sa.String(100), nullable=False, index=True),
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column("conditions", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("smart_alerts")
    op.drop_table("watchlist_items")
    op.drop_table("alerts")
    op.drop_table("market_events")
    op.drop_table("positions")
    op.drop_table("portfolios")
    op.drop_table("users")
