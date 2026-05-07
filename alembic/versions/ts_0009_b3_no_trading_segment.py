"""ts_0009_b3_no_trading_segment

Adiciona coluna segment em b3_no_trading_days. PK passa a ser
(target_date, segment) — permite registros distintos por dia/segmento.

Convencao do projeto (Decisao 23): migrations no ramo ts_* sao registry-only.
DDL real fica em init_timescale/011_b3_no_trading_segment.sql.

Revision ID: ts_0009
Revises: ts_0008
Create Date: 2026-05-07
"""

from __future__ import annotations

revision = "ts_0009"
down_revision = "ts_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # DDL aplicado via init_timescale/011_b3_no_trading_segment.sql


def downgrade() -> None:
    pass
