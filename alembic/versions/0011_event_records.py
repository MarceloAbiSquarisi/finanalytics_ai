"""0011 -- event_records table

Revision ID: 0011_event_records
Revises: 0010_financial_agents
Create Date: 2026-03-25 13:59:57
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_event_records"
down_revision = "0010_financial_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_records",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("correlation_id", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("payload_data", postgresql.JSONB, nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_event_records_status", "event_records", ["status"])
    op.create_index("idx_event_records_event_type", "event_records", ["event_type"])
    op.create_index("idx_event_records_created_at", "event_records", ["created_at"])
    op.create_index(
        "idx_event_records_source_type", "event_records", ["source", "event_type"]
    )


def downgrade() -> None:
    op.drop_table("event_records")
