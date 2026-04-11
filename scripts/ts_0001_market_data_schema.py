"""0001_timescale_market_data

Cria schema de séries temporais no TimescaleDB:
- ohlc_1m         hypertable (chunk 7 dias, compressão após 7 dias)
- ohlc_agg_5m     continuous aggregate
- ohlc_agg_1h     continuous aggregate
- ohlc_agg_1d     continuous aggregate
- cotacoes_ts     hypertable (chunk 30 dias)
- indicadores_ts  hypertable (chunk 90 dias)
- itens_contabeis_ts hypertable (chunk 90 dias)

Decisão: usamos SQL puro via op.execute() porque o DDL TimescaleDB
(create_hypertable, add_compression_policy) não tem equivalente no
Alembic ORM — e não vale criar abstrações para isso.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001_ts"
down_revision = None
branch_labels = ("timescale",)  # branch separado do Postgres principal
depends_on = None

_SQL_FILE = Path(__file__).parent.parent.parent / "init_timescale" / "001_market_data_schema.sql"


def upgrade() -> None:
    sql = _SQL_FILE.read_text(encoding="utf-8")
    # Remove comentários de linha para evitar problemas com alguns drivers
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    op.execute("\n".join(lines))


def downgrade() -> None:
    op.execute("""
        DROP VIEW  IF EXISTS ohlc_auto CASCADE;
        DROP TABLE IF EXISTS itens_contabeis_ts CASCADE;
        DROP TABLE IF EXISTS indicadores_ts CASCADE;
        DROP TABLE IF EXISTS cotacoes_ts CASCADE;
        DROP TABLE IF EXISTS ohlc_1m CASCADE;
    """)
