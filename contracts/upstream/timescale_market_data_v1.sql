-- =============================================================================
-- Contrato C1 — Market data via TimescaleDB (versão V1)
-- =============================================================================
-- Tabelas READ-ONLY que o trading-engine consome em backtest (R-07) e como
-- fallback síncrono em live. Schema replicado de trading_engine_implementacao.md §8.1.
--
-- Quem produz: finanalyticsai (profit_agent.py + db_writer.py).
-- Quem consome: trading-engine (READ-only via role trading_engine_reader).
--
-- Idempotente. Aplicado no banco market_data (TimescaleDB).
--
-- ⚠ Status atual (2026-04-30): este arquivo é CANÔNICO neste repo. O
-- FinAnalyticsAI ainda não tem cópia em /contracts/ — ver
-- docs/c1_handoff_for_finanalyticsai.md.
-- =============================================================================

-- ----------------------------------------------------------------------------
-- ticks_br — hypertable principal de trades B3
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticks_br (
    time      TIMESTAMPTZ      NOT NULL,
    symbol    TEXT             NOT NULL,
    price     DOUBLE PRECISION NOT NULL,
    volume    BIGINT           NOT NULL,
    aggressor SMALLINT  -- 1=BUY, -1=SELL, 0=unknown/leilão
);

SELECT create_hypertable('ticks_br', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_ticks_br_symbol_time
    ON ticks_br (symbol, time DESC);

-- ----------------------------------------------------------------------------
-- ohlcv_1m — Continuous Aggregate (TimescaleDB)
-- ----------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', time) AS bucket,
    symbol,
    first(price, time) AS open,
    max(price)         AS high,
    min(price)         AS low,
    last(price, time)  AS close,
    sum(volume)        AS volume
FROM ticks_br
GROUP BY bucket, symbol
WITH NO DATA;

-- Política de refresh recomendada (não aplicada aqui — decisão operacional
-- do time do FinAnalyticsAI). Sugestão:
--
--   SELECT add_continuous_aggregate_policy('ohlcv_1m',
--       start_offset => INTERVAL '1 hour',
--       end_offset   => INTERVAL '1 minute',
--       schedule_interval => INTERVAL '1 minute');

-- ----------------------------------------------------------------------------
-- Grants para a role trading_engine_reader (criada via setup_postgres_roles.sql)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trading_engine_reader') THEN
        EXECUTE 'GRANT SELECT ON ticks_br TO trading_engine_reader';
        EXECUTE 'GRANT SELECT ON ohlcv_1m TO trading_engine_reader';
    END IF;
END $$;
