"""0026_notifications

Tabelas de configuracao + log de notificacoes Pushover.

  notification_settings — chave/valor para toggle por categoria + master switch.
  notifications_log     — historico de notificacoes enviadas (sucesso/falha).

Categorias canonicas (seed):
  backfill, scheduler, auto_trader, indicator, system, test

Master switch:
  master_enabled (default 'true') — se 'false' pula tudo (alem do
  PUSHOVER_ENABLED env). Permite operador silenciar tudo via UI sem mexer
  em .env / restart.

Revision ID: 0026_notifications
Revises: 0025_b3_delisted_tickers
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op


revision = "0026_notifications"
down_revision = "0025_b3_delisted_tickers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_settings (
            key         VARCHAR(64) PRIMARY KEY,
            value       VARCHAR(255) NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by  VARCHAR(120)
        )
        """
    )
    # Seed defaults idempotente.
    op.execute(
        """
        INSERT INTO notification_settings (key, value) VALUES
            ('master_enabled',      'true'),
            ('cat_backfill',        'true'),
            ('cat_scheduler',       'true'),
            ('cat_auto_trader',     'true'),
            ('cat_indicator',       'true'),
            ('cat_system',          'true'),
            ('cat_test',            'true')
        ON CONFLICT (key) DO NOTHING
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications_log (
            id          BIGSERIAL PRIMARY KEY,
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            category    VARCHAR(32) NOT NULL,
            title       VARCHAR(250) NOT NULL,
            message     TEXT NOT NULL,
            priority    SMALLINT NOT NULL DEFAULT 0,
            critical    BOOLEAN NOT NULL DEFAULT FALSE,
            outcome     VARCHAR(16) NOT NULL,  -- sent | skipped | failed
            skip_reason VARCHAR(64),           -- master_off | category_off | disabled | no_creds
            error_msg   TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notifications_log_sent_at "
        "ON notifications_log (sent_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notifications_log_category_sent "
        "ON notifications_log (category, sent_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notifications_log")
    op.execute("DROP TABLE IF EXISTS notification_settings")
