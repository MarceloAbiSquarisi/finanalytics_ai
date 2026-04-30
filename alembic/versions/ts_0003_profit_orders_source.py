"""ts_0003_profit_orders_source

Adiciona profit_orders.source (VARCHAR(32) NULL) + indices para o handshake C5
com o trading-engine. Quando o engine envia ordens via :8002/order/send com
`_source: "trading_engine"` no body, o profit_agent persiste o valor na coluna
e `_maybe_dispatch_diary` usa a coluna para suprimir o hook de fill (engine
mantem journal proprio em trading_engine_orders.trade_journal).

Tambem adiciona indice em cl_ord_id (acelera reconcile do engine pelo
client_order_id deterministico que ele envia em `_client_order_id`).

Spec: c5_handoff_for_finanalyticsai.md (Passo 7)

Notas:
  - profit_orders mora no TimescaleDB (DSN PROFIT_TIMESCALE_DSN, db market_data).
  - DDL de fato esta em init_timescale/002_profit_agent_schema.sql (idempotente
    via ADD COLUMN IF NOT EXISTS), exatamente como validity_type/validity_date.
  - Esta migration apenas registra o schema bump no historico do alembic.
    Em ambientes novos, init_timescale/ roda primeiro; em ambientes existentes,
    o ALTER e idempotente.

Revision ID: ts_0003
Revises: ts_0002
Create Date: 2026-04-30
"""
from __future__ import annotations

revision = "ts_0003"
down_revision = "ts_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # DDL aplicado via init_timescale/002_profit_agent_schema.sql


def downgrade() -> None:
    pass
