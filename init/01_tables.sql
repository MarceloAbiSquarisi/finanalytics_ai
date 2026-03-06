-- ──────────────────────────────────────────────────────────────────────────────
-- FinAnalytics AI — Schema inicial PostgreSQL
-- Executado automaticamente pelo container postgres na primeira inicialização.
-- ──────────────────────────────────────────────────────────────────────────────

-- Portfolios
CREATE TABLE IF NOT EXISTS portfolios (
    id          VARCHAR(36)     PRIMARY KEY,
    user_id     VARCHAR(100)    NOT NULL,
    name        VARCHAR(200)    NOT NULL,
    currency    VARCHAR(3)      NOT NULL DEFAULT 'BRL',
    cash        NUMERIC(18, 2)  NOT NULL DEFAULT 0,
    created_at  TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP       NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_portfolios_user_id ON portfolios(user_id);

-- Positions
CREATE TABLE IF NOT EXISTS positions (
    id              SERIAL          PRIMARY KEY,
    portfolio_id    VARCHAR(36)     NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    ticker          VARCHAR(10)     NOT NULL,
    quantity        NUMERIC(18, 8)  NOT NULL,
    average_price   NUMERIC(18, 2)  NOT NULL,
    asset_class     VARCHAR(30)     NOT NULL DEFAULT 'stock',
    CONSTRAINT uq_portfolio_ticker UNIQUE (portfolio_id, ticker)
);
CREATE INDEX IF NOT EXISTS ix_positions_portfolio_id ON positions(portfolio_id);

-- Events (idempotency store)
CREATE TABLE IF NOT EXISTS market_events (
    event_id    VARCHAR(36)     PRIMARY KEY,
    event_type  VARCHAR(50)     NOT NULL,
    ticker      VARCHAR(10)     NOT NULL,
    payload     JSONB           NOT NULL DEFAULT '{}',
    source      VARCHAR(100)    NOT NULL DEFAULT 'unknown',
    status      VARCHAR(20)     NOT NULL DEFAULT 'pending',
    occurred_at TIMESTAMP       NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_events_ticker      ON market_events(ticker);
CREATE INDEX IF NOT EXISTS ix_events_status      ON market_events(status);
CREATE INDEX IF NOT EXISTS ix_events_occurred_at ON market_events(occurred_at);

-- Alerts
CREATE TABLE IF NOT EXISTS alerts (
    alert_id        VARCHAR(36)     PRIMARY KEY,
    user_id         VARCHAR(100)    NOT NULL,
    ticker          VARCHAR(10)     NOT NULL,
    alert_type      VARCHAR(30)     NOT NULL,
    threshold       NUMERIC(18, 4)  NOT NULL,
    reference_price NUMERIC(18, 4)  NOT NULL DEFAULT 0,
    status          VARCHAR(20)     NOT NULL DEFAULT 'active',
    note            TEXT            NOT NULL DEFAULT '',
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    triggered_at    TIMESTAMP,
    expires_at      TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_alerts_user_id ON alerts(user_id);
CREATE INDEX IF NOT EXISTS ix_alerts_ticker  ON alerts(ticker);
CREATE INDEX IF NOT EXISTS ix_alerts_status  ON alerts(status);

-- Log de alertas disparados (histórico imutável)
CREATE TABLE IF NOT EXISTS alert_history (
    id          SERIAL          PRIMARY KEY,
    alert_id    VARCHAR(36)     NOT NULL,
    ticker      VARCHAR(10)     NOT NULL,
    alert_type  VARCHAR(30)     NOT NULL,
    price       NUMERIC(18, 4)  NOT NULL,
    threshold   NUMERIC(18, 4)  NOT NULL,
    message     TEXT            NOT NULL,
    triggered_at TIMESTAMP      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_alert_history_ticker ON alert_history(ticker);
