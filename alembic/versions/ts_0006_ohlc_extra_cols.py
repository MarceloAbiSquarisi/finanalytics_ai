"""ts_0006_ohlc_extra_cols

Adiciona ohlc_1m.aftermarket (BOOLEAN) + ohlc_1m.quantidade (BIGINT) para
suportar arquivos Nelogica que trazem essas duas dimensoes.

Convencao do projeto (Decisao 23): migrations no ramo ts_* sao registry-only.
DDL real fica em init_timescale/008_ohlc_1m_extra_cols.sql.

Revision ID: ts_0006
Revises: ts_0005
Create Date: 2026-05-06
"""

from __future__ import annotations

revision = "ts_0006"
down_revision = "ts_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # DDL aplicado via init_timescale/008_ohlc_1m_extra_cols.sql


def downgrade() -> None:
    pass
