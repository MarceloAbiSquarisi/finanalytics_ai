"""ts_0007_ohlc_cagg_hierarchy

Continuous aggregates hierarquicas: ohlc_5m, ohlc_15m, ohlc_1h, ohlc_1d.

Convencao do projeto (Decisao 23): migrations no ramo ts_* sao registry-only.
DDL real fica em init_timescale/009_ohlc_cagg_hierarchy.sql.

Revision ID: ts_0007
Revises: ts_0006
Create Date: 2026-05-07
"""

from __future__ import annotations

revision = "ts_0007"
down_revision = "ts_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # DDL aplicado via init_timescale/009_ohlc_cagg_hierarchy.sql


def downgrade() -> None:
    pass
