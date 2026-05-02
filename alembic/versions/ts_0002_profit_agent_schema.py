"""ts_0002_profit_agent_schema

Cria tabelas do ProfitAgent no TimescaleDB.
"""

from __future__ import annotations
from pathlib import Path
from alembic import op

revision = "ts_0002"
down_revision = "53e92a4075c2"
branch_labels = None
depends_on = None

_SQL_FILE = Path(__file__).parent.parent.parent / "init_timescale" / "002_profit_agent_schema.sql"


def upgrade() -> None:
    pass  # tabelas ja criadas no TimescaleDB via init_timescale/


def downgrade() -> None:
    pass
