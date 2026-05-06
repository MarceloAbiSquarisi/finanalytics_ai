"""ts_0005_backfill_jobs

Tabelas backfill_jobs + backfill_job_items no TimescaleDB para a aba
/admin → Backfill. Persiste estado de jobs e dashboard de falhas.

Convencao do projeto (Decisao 23 + nota em ts_0004): migrations no ramo
ts_* sao registry-only. DDL real fica em init_timescale/007_backfill_jobs.sql
(idempotente, IF NOT EXISTS). Em ambientes existentes, aplicar via:
  python scripts/apply_backfill_migration.py

Revision ID: ts_0005
Revises: ts_0004
Create Date: 2026-05-06
"""

from __future__ import annotations

revision = "ts_0005"
down_revision = "ts_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # DDL aplicado via init_timescale/007_backfill_jobs.sql


def downgrade() -> None:
    pass
