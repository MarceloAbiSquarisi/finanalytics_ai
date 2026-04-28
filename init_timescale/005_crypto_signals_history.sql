-- N6 (28/abr/2026) — Snapshot diario de crypto signals
-- Persiste o output de /api/v1/crypto/signal/{symbol} para analise
-- multi-horizon (h7d/h14d/h30d) sem dependencia de OHLC intraday.
-- Populado por scripts/snapshot_crypto_signals.py (job diario 9h BRT).

CREATE TABLE IF NOT EXISTS crypto_signals_history (
    symbol          VARCHAR(10)      NOT NULL,
    snapshot_date   DATE             NOT NULL DEFAULT CURRENT_DATE,
    signal          VARCHAR(10)      NOT NULL,   -- BUY | SELL | HOLD
    score           INT              NOT NULL,
    current_price   NUMERIC(18,4),
    rsi             NUMERIC(8,4),
    macd_hist       NUMERIC(12,8),
    ema9            NUMERIC(18,4),
    ema21           NUMERIC(18,4),
    bb_upper        NUMERIC(18,4),
    bb_lower        NUMERIC(18,4),
    vs_currency     VARCHAR(5)       DEFAULT 'usd',
    snapshot_at     TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, snapshot_date, vs_currency)
);

CREATE INDEX IF NOT EXISTS ix_crypto_signals_history_symbol
    ON crypto_signals_history (symbol, snapshot_date DESC);
