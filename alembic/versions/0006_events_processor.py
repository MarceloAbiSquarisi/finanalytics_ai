"""0006_events_processor

Cria tabelas para o serviço de processamento assíncrono de eventos.

Decisões de schema:
- events.id é UUID (não SERIAL) para evitar coordenação entre instâncias.
- event_processing_records.event_id tem ON DELETE CASCADE para facilitar limpeza.
- Index em (status, created_at) otimiza a query get_pending_events.
- result_metadata é JSONB para flexibilidade sem migrações frequentes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005_fintz_schema_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

    op.create_table(
        "events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("correlation_id", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_created_at", "events", ["created_at"])
    op.create_index("ix_events_correlation_id", "events", ["correlation_id"])

    op.create_table(
        "event_processing_records",
        sa.Column("event_id", sa.UUID(), primary_key=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_metadata", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_epr_status_created",
        "event_processing_records",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_epr_status",
        "event_processing_records",
        ["status"],
    )


def downgrade() -> None:
    op.drop_table("event_processing_records")
    op.drop_table("events")
