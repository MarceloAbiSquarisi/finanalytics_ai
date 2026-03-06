"""001 — Initial schema: portfolios, positions, market_events

Revision ID: 001
Revises: 
Create Date: 2025-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(100), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, default="BRL"),
        sa.Column("cash", sa.Numeric(18, 2), nullable=False, default=0),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("portfolio_id", sa.String(36), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("average_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("asset_class", sa.String(30), nullable=False, default="stock"),
        sa.UniqueConstraint("portfolio_id", "ticker", name="uq_portfolio_ticker"),
    )
    op.create_table(
        "market_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False, index=True),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("source", sa.String(100), default="unknown"),
        sa.Column("status", sa.String(20), default="pending", index=True),
        sa.Column("retry_count", sa.Integer, default=0),
        sa.Column("error_message", sa.Text, default=""),
        sa.Column("occurred_at", sa.DateTime, nullable=False),
        sa.Column("processed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("market_events")
    op.drop_table("positions")
    op.drop_table("portfolios")
