CREATE TABLE IF NOT EXISTS profit_ticks (
    time          TIMESTAMPTZ      NOT NULL,
    ticker        TEXT             NOT NULL,
    exchange      TEXT             NOT NULL DEFAULT 'B',
    price         DOUBLE PRECISION NOT NULL,
    quantity      INTEGER          NOT NULL,
    volume        DOUBLE PRECISION,
    buy_agent     INTEGER,
    sell_agent    INTEGER,
    trade_number  INTEGER,
    trade_type    INTEGER,
    is_edit       BOOLEAN          NOT NULL DEFAULT FALSE
);
SELECT create_hypertable('profit_ticks','time',chunk_time_interval=>INTERVAL '1 day',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS ix_profit_ticks_ticker_time ON profit_ticks (ticker, time DESC);
ALTER TABLE profit_ticks SET (timescaledb.compress,timescaledb.compress_segmentby='ticker',timescaledb.compress_orderby='time DESC');
SELECT add_compression_policy('profit_ticks',compress_after=>INTERVAL '2 days',if_not_exists=>TRUE);

CREATE TABLE IF NOT EXISTS profit_daily_bars (
    time            TIMESTAMPTZ      NOT NULL,
    ticker          TEXT             NOT NULL,
    exchange        TEXT             NOT NULL DEFAULT 'B',
    open            DOUBLE PRECISION,
    high            DOUBLE PRECISION,
    low             DOUBLE PRECISION,
    close           DOUBLE PRECISION,
    volume          DOUBLE PRECISION,
    adjust          DOUBLE PRECISION,
    max_limit       DOUBLE PRECISION,
    min_limit       DOUBLE PRECISION,
    vol_buyer       DOUBLE PRECISION,
    vol_seller      DOUBLE PRECISION,
    qty             INTEGER,
    trades          INTEGER,
    open_contracts  INTEGER,
    qty_buyer       INTEGER,
    qty_seller      INTEGER,
    neg_buyer       INTEGER,
    neg_seller      INTEGER,
    PRIMARY KEY (time, ticker, exchange)
);
SELECT create_hypertable('profit_daily_bars','time',chunk_time_interval=>INTERVAL '1 month',if_not_exists=>TRUE,migrate_data=>TRUE);
CREATE INDEX IF NOT EXISTS ix_profit_daily_ticker_time ON profit_daily_bars (ticker, time DESC);

CREATE TABLE IF NOT EXISTS profit_order_book (
    time          TIMESTAMPTZ      NOT NULL,
    ticker        TEXT             NOT NULL,
    exchange      TEXT             NOT NULL DEFAULT 'B',
    side          SMALLINT         NOT NULL,
    position      INTEGER          NOT NULL,
    price         DOUBLE PRECISION,
    quantity      INTEGER,
    count         INTEGER,
    is_theoric    BOOLEAN          NOT NULL DEFAULT FALSE
);
SELECT create_hypertable('profit_order_book','time',chunk_time_interval=>INTERVAL '1 day',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS ix_profit_book_ticker_time ON profit_order_book (ticker, time DESC);
ALTER TABLE profit_order_book SET (timescaledb.compress,timescaledb.compress_segmentby='ticker',timescaledb.compress_orderby='time DESC');
SELECT add_compression_policy('profit_order_book',compress_after=>INTERVAL '2 days',if_not_exists=>TRUE);

CREATE TABLE IF NOT EXISTS profit_assets (
    ticker               TEXT        NOT NULL,
    exchange             TEXT        NOT NULL DEFAULT 'B',
    name                 TEXT,
    description          TEXT,
    security_type        INTEGER,
    security_subtype     INTEGER,
    min_order_qty        INTEGER,
    max_order_qty        INTEGER,
    lot_size             INTEGER,
    min_price_increment  DOUBLE PRECISION,
    contract_multiplier  DOUBLE PRECISION,
    valid_date           DATE,
    isin                 TEXT,
    sector               TEXT,
    sub_sector           TEXT,
    segment              TEXT,
    feed_type            INTEGER     NOT NULL DEFAULT 0,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, exchange)
);
CREATE INDEX IF NOT EXISTS ix_profit_assets_sector ON profit_assets (sector);
CREATE INDEX IF NOT EXISTS ix_profit_assets_isin   ON profit_assets (isin);

CREATE TABLE IF NOT EXISTS profit_orders (
    id                SERIAL           PRIMARY KEY,
    local_order_id    BIGINT,
    message_id        BIGINT,
    cl_ord_id         TEXT,
    broker_id         INTEGER,
    account_id        TEXT,
    sub_account_id    TEXT,
    env               TEXT             NOT NULL DEFAULT 'simulation',
    ticker            TEXT             NOT NULL,
    exchange          TEXT             NOT NULL DEFAULT 'B',
    order_type        INTEGER          NOT NULL,
    order_side        INTEGER          NOT NULL,
    price             DOUBLE PRECISION,
    stop_price        DOUBLE PRECISION,
    quantity          INTEGER          NOT NULL,
    filled_qty        INTEGER          NOT NULL DEFAULT 0,
    avg_fill_price    DOUBLE PRECISION,
    order_status      SMALLINT         NOT NULL DEFAULT 10,
    error_message     TEXT,
    created_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_profit_orders_ticker   ON profit_orders (ticker);
CREATE INDEX IF NOT EXISTS ix_profit_orders_status   ON profit_orders (order_status);
CREATE INDEX IF NOT EXISTS ix_profit_orders_local_id ON profit_orders (local_order_id);

CREATE TABLE IF NOT EXISTS profit_adjustments (
    id                 SERIAL  PRIMARY KEY,
    ticker             TEXT    NOT NULL,
    exchange           TEXT    NOT NULL DEFAULT 'B',
    adjust_date        DATE,
    deliberation_date  DATE,
    payment_date       DATE,
    adjust_type        TEXT,
    value              DOUBLE PRECISION,
    multiplier         DOUBLE PRECISION,
    flags              INTEGER,
    observation        TEXT,
    UNIQUE (ticker, exchange, adjust_date, adjust_type)
);
CREATE INDEX IF NOT EXISTS ix_profit_adjustments_ticker ON profit_adjustments (ticker);

CREATE TABLE IF NOT EXISTS profit_agent_status (
    id                  INTEGER      PRIMARY KEY DEFAULT 1,
    started_at          TIMESTAMPTZ,
    version             TEXT,
    last_heartbeat      TIMESTAMPTZ,
    is_connected        BOOLEAN      NOT NULL DEFAULT FALSE,
    market_connected    BOOLEAN      NOT NULL DEFAULT FALSE,
    routing_connected   BOOLEAN      NOT NULL DEFAULT FALSE,
    subscribed_tickers  TEXT[]       NOT NULL DEFAULT '{}',
    total_ticks         BIGINT       NOT NULL DEFAULT 0,
    total_orders        INTEGER      NOT NULL DEFAULT 0,
    CONSTRAINT single_row CHECK (id = 1)
);
INSERT INTO profit_agent_status (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS profit_subscribed_tickers (
    ticker      TEXT        NOT NULL,
    exchange    TEXT        NOT NULL DEFAULT 'B',
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes       TEXT,
    PRIMARY KEY (ticker, exchange)
);