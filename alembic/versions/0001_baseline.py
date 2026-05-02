"""baseline: tabelas existentes antes do sprint múltiplas carteiras

Revision ID: 0001_baseline
Revises:
Create Date: 2025-01-01 00:00:00.000000

Representa o estado do banco ANTES da feature múltiplas carteiras.
Esta migration usa CREATE TABLE IF NOT EXISTS para ser idempotente —
pode ser aplicada mesmo que as tabelas já existam (ex: banco migrado
de uma versão anterior sem alembic_version).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Usa SQL direto com IF NOT EXISTS para ser completamente idempotente.
    # op.create_table() falha se a tabela existir; execute() com
    # IF NOT EXISTS nunca falha — ideal para baseline migrations.
    conn = op.get_bind()

    conn.execute(
        op.get_context().config.attributes.get(  # type: ignore[arg-type]
            "__noop__",  # placeholder para satisfazer linter
            None,
        )
        or __import__("sqlalchemy").text("SELECT 1")
    )  # noqa

    # Executamos o DDL completo de uma vez como SQL puro
    conn.execute(
        __import__("sqlalchemy").text("""
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            email VARCHAR(200) NOT NULL UNIQUE,
            hashed_password VARCHAR(200) NOT NULL,
            full_name VARCHAR(200),
            role VARCHAR(50) NOT NULL DEFAULT 'user',
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            last_login_at TIMESTAMP WITHOUT TIME ZONE
        );
        CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

        CREATE TABLE IF NOT EXISTS portfolios (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            user_id VARCHAR(100) NOT NULL,
            name VARCHAR(200) NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'BRL',
            cash NUMERIC(18, 2) NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            updated_at TIMESTAMP WITHOUT TIME ZONE
        );
        CREATE INDEX IF NOT EXISTS ix_portfolios_user_id ON portfolios (user_id);

        CREATE TABLE IF NOT EXISTS positions (
            id SERIAL PRIMARY KEY,
            portfolio_id VARCHAR(36) NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
            ticker VARCHAR(10) NOT NULL,
            quantity NUMERIC(18, 8) NOT NULL,
            average_price NUMERIC(18, 2) NOT NULL,
            asset_class VARCHAR(30) NOT NULL DEFAULT 'stock',
            CONSTRAINT uq_portfolio_ticker UNIQUE (portfolio_id, ticker)
        );
        CREATE INDEX IF NOT EXISTS ix_positions_portfolio_id ON positions (portfolio_id);

        CREATE TABLE IF NOT EXISTS market_events (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            event_type VARCHAR(50) NOT NULL,
            ticker VARCHAR(10) NOT NULL,
            payload TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            idempotency_key VARCHAR(100) NOT NULL UNIQUE,
            occurred_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            processed_at TIMESTAMP WITHOUT TIME ZONE,
            error_message TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS ix_market_events_ticker ON market_events (ticker);
        CREATE INDEX IF NOT EXISTS ix_market_events_status ON market_events (status);

        CREATE TABLE IF NOT EXISTS alerts (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            user_id VARCHAR(100) NOT NULL,
            ticker VARCHAR(10) NOT NULL,
            alert_type VARCHAR(50) NOT NULL,
            threshold NUMERIC(18, 2) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            created_at TIMESTAMP WITHOUT TIME ZONE,
            triggered_at TIMESTAMP WITHOUT TIME ZONE,
            expires_at TIMESTAMP WITHOUT TIME ZONE
        );
        CREATE INDEX IF NOT EXISTS ix_alerts_user_id ON alerts (user_id);

        CREATE TABLE IF NOT EXISTS watchlist_items (
            item_id VARCHAR(36) NOT NULL PRIMARY KEY,
            user_id VARCHAR(100) NOT NULL,
            ticker VARCHAR(10) NOT NULL,
            note VARCHAR(500),
            added_at TIMESTAMP WITHOUT TIME ZONE,
            CONSTRAINT uq_watchlist_user_ticker UNIQUE (user_id, ticker)
        );
        CREATE INDEX IF NOT EXISTS ix_watchlist_items_user_id ON watchlist_items (user_id);

        CREATE TABLE IF NOT EXISTS smart_alerts (
            alert_id VARCHAR(36) NOT NULL PRIMARY KEY,
            ticker VARCHAR(10) NOT NULL,
            user_id VARCHAR(100) NOT NULL,
            alert_type VARCHAR(50) NOT NULL,
            conditions TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            note VARCHAR(500),
            last_triggered_at TIMESTAMP WITHOUT TIME ZONE,
            created_at TIMESTAMP WITHOUT TIME ZONE
        );
        CREATE INDEX IF NOT EXISTS ix_smart_alerts_user_id ON smart_alerts (user_id);
    """)
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        __import__("sqlalchemy").text("""
        DROP TABLE IF EXISTS smart_alerts;
        DROP TABLE IF EXISTS watchlist_items;
        DROP TABLE IF EXISTS alerts;
        DROP TABLE IF EXISTS market_events;
        DROP TABLE IF EXISTS positions;
        DROP TABLE IF EXISTS portfolios;
        DROP TABLE IF EXISTS users;
    """)
    )
