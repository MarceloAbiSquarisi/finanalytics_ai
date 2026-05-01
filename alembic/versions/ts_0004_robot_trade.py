"""ts_0004_robot_trade

Schema para auto_trader_worker (R1 — robo de trade autonomo). 4 tabelas
no TimescaleDB:

  robot_strategies     — registry de strategies (config JSONB + account_id + enabled)
  robot_signals_log    — auditoria de toda decisao do worker (envio ou skip)
  robot_orders_intent  — espelho compacto de ordens originadas pelo robo
                         (separa de profit_orders manual; liga via local_order_id)
  robot_risk_state     — estado diario de risco + kill switch (paused)

Notas:
  - Mesmo DDL replicado em init_timescale/006_robot_trade.sql (idempotente
    via IF NOT EXISTS) — convencao do projeto.
  - Diferente das migrations ts_0002/ts_0003 (que sao no-op porque DDL ja foi
    aplicado pelo init_timescale em containers novos), esta migration aplica
    o DDL diretamente para cobrir o caso de container TimescaleDB existente
    que precisa do schema sem rebuild.
  - Idempotencia: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
    Re-rodar e seguro.

Revision ID: ts_0004
Revises: ts_0003
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op

revision = "ts_0004"
down_revision = "ts_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_strategies (
            id              SERIAL          PRIMARY KEY,
            name            TEXT            NOT NULL UNIQUE,
            enabled         BOOLEAN         NOT NULL DEFAULT FALSE,
            config_json     JSONB           NOT NULL DEFAULT '{}'::jsonb,
            account_id      INTEGER,
            description     TEXT,
            created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_robot_strategies_enabled "
        "ON robot_strategies (enabled) WHERE enabled = TRUE"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_signals_log (
            id                  SERIAL          PRIMARY KEY,
            strategy_id         INTEGER,
            strategy_name       TEXT,
            ticker              TEXT,
            action              TEXT,
            computed_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            sent_to_dll         BOOLEAN         NOT NULL DEFAULT FALSE,
            local_order_id      INTEGER,
            reason_skipped      TEXT,
            payload_json        JSONB
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_robot_signals_log_computed_at "
        "ON robot_signals_log (computed_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_robot_signals_log_strategy_ticker "
        "ON robot_signals_log (strategy_id, ticker, computed_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_robot_signals_log_sent "
        "ON robot_signals_log (sent_to_dll, computed_at DESC) WHERE sent_to_dll = TRUE"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_orders_intent (
            id                  SERIAL          PRIMARY KEY,
            signal_log_id       INTEGER         NOT NULL,
            strategy_id         INTEGER,
            ticker              TEXT            NOT NULL,
            side                TEXT            NOT NULL,
            order_type          TEXT            NOT NULL,
            quantity            DOUBLE PRECISION NOT NULL,
            price               DOUBLE PRECISION,
            take_profit         DOUBLE PRECISION,
            stop_loss           DOUBLE PRECISION,
            local_order_id      INTEGER,
            cl_ord_id           TEXT,
            sent_at             TIMESTAMPTZ,
            error_msg           TEXT,
            created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_robot_orders_intent_signal "
        "ON robot_orders_intent (signal_log_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_robot_orders_intent_local_order "
        "ON robot_orders_intent (local_order_id) WHERE local_order_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_robot_orders_intent_strategy_created "
        "ON robot_orders_intent (strategy_id, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_risk_state (
            date            DATE            PRIMARY KEY,
            total_pnl       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            realized_pnl    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            unrealized_pnl  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            max_dd          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            positions_count INTEGER         NOT NULL DEFAULT 0,
            paused          BOOLEAN         NOT NULL DEFAULT FALSE,
            paused_at       TIMESTAMPTZ,
            paused_reason   TEXT,
            updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS robot_risk_state")
    op.execute("DROP TABLE IF EXISTS robot_orders_intent")
    op.execute("DROP TABLE IF EXISTS robot_signals_log")
    op.execute("DROP TABLE IF EXISTS robot_strategies")
