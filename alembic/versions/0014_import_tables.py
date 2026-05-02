"""0014_import_tables

Tabelas para o módulo de importação de arquivos financeiros.

Revision ID: 0014_import_tables
Revises: 0013_merge_heads
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_import_tables"
down_revision = "0013_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("rows_imported", sa.Integer(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("raw_meta", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_import_history_user_id", "import_history", ["user_id"])
    op.create_index("ix_import_history_status", "import_history", ["status"])

    op.create_table(
        "import_transactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "import_id",
            sa.Integer(),
            sa.ForeignKey("import_history.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("operation", sa.String(10), nullable=False),  # C / V
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("gross_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("fees", sa.Numeric(18, 2), nullable=True, server_default="0"),
        sa.Column("net_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("broker", sa.String(50), nullable=True),
        # hash(user_id + trade_date + ticker + quantity + unit_price + operation)
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("raw_row", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_import_tx_user_date", "import_transactions", ["user_id", "trade_date"])
    op.create_index("ix_import_tx_ticker", "import_transactions", ["ticker"])


def downgrade() -> None:
    op.drop_table("import_transactions")
    op.drop_table("import_history")
