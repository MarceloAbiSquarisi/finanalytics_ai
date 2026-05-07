"""ts_0008_b3_no_trading_days

Tabela de dias atipicos B3 (sem pregao mesmo nao sendo feriado oficial).

Convencao do projeto (Decisao 23): migrations no ramo ts_* sao registry-only.
DDL real fica em init_timescale/010_b3_no_trading_days.sql.

Revision ID: ts_0008
Revises: ts_0007
Create Date: 2026-05-07
"""

from __future__ import annotations

revision = "ts_0008"
down_revision = "ts_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # DDL aplicado via init_timescale/010_b3_no_trading_days.sql


def downgrade() -> None:
    pass
