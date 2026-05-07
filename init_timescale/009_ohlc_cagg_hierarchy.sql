-- 009_ohlc_cagg_hierarchy.sql
--
-- 4 continuous aggregates hierárquicas em cima de ohlc_1m, com refresh
-- policies. Backtests multi-timeframe leem dessas views direto, mesma
-- estrutura de ohlc_1m.
--
-- Hierarquia (cada nivel agrega o anterior, nao o ohlc_1m raw):
--   ohlc_1m (hypertable, source-of-truth)
--      ↓ time_bucket('5m')
--   ohlc_5m  (CAGG)
--      ↓ time_bucket('15m', usando time_bucket de 5m alinhado)
--   ohlc_15m (CAGG sobre ohlc_5m)
--      ↓ time_bucket('1h')
--   ohlc_1h  (CAGG sobre ohlc_15m)
--      ↓ time_bucket('1d')
--   ohlc_1d  (CAGG sobre ohlc_1h)
--
-- Por que hierarquica e nao cada uma direto do 1m:
--   - Refresh propaga: alterou bar 1m -> TS reagenda recompute em todas
--     as camadas dependentes automaticamente (CAGG-on-CAGG, TS >=2.9).
--   - Compute menor: 1d le 9h da view 1h (~9 buckets/ticker) em vez de
--     varrer 540 linhas/ticker em ohlc_1m.
--
-- Agregadores OHLC corretos:
--   open  = first(open, time)   <- PRIMEIRO open do bucket cronologico
--   close = last(close, time)   <- ULTIMO close
--   high/low/volume/trades = max/min/sum (idempotentes em qualquer ordem)
--
-- Idempotencia: CREATE MATERIALIZED VIEW IF NOT EXISTS funciona.
-- Refresh policies usam add_continuous_aggregate_policy com
-- if_not_exists=true.
--
-- Aplicado por docker-entrypoint em containers novos. Em existentes:
--   docker exec finanalytics_timescale psql -U finanalytics -d market_data \
--     -f /docker-entrypoint-initdb.d/009_ohlc_cagg_hierarchy.sql

-- ── ohlc_5m ──────────────────────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlc_5m
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '5 minutes', time) AS time,
    ticker,
    first(open,  time) AS open,
    max(high)          AS high,
    min(low)           AS low,
    last(close,  time) AS close,
    sum(volume)::bigint AS volume,
    sum(trades)::int    AS trades
FROM ohlc_1m
GROUP BY 1, 2
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlc_5m',
    start_offset      => INTERVAL '1 hour',
    end_offset        => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists     => true
);

-- ── ohlc_15m (sobre ohlc_5m) ─────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlc_15m
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '15 minutes', time) AS time,
    ticker,
    first(open,  time) AS open,
    max(high)          AS high,
    min(low)           AS low,
    last(close,  time) AS close,
    sum(volume)::bigint AS volume,
    sum(trades)::int    AS trades
FROM ohlc_5m
GROUP BY 1, 2
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlc_15m',
    start_offset      => INTERVAL '4 hours',
    end_offset        => INTERVAL '15 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists     => true
);

-- ── ohlc_1h (sobre ohlc_15m) ─────────────────────────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlc_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 hour', time) AS time,
    ticker,
    first(open,  time) AS open,
    max(high)          AS high,
    min(low)           AS low,
    last(close,  time) AS close,
    sum(volume)::bigint AS volume,
    sum(trades)::int    AS trades
FROM ohlc_15m
GROUP BY 1, 2
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlc_1h',
    start_offset      => INTERVAL '1 day',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists     => true
);

-- ── ohlc_1d (sobre ohlc_1h) ──────────────────────────────────────────────────
-- Refresh menos frequente — bars diarias mudam pouco intra-pregao;
-- final do pregao B3 e' 18:30 BRT, refresh hourly basta.
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlc_1d
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 day', time) AS time,
    ticker,
    first(open,  time) AS open,
    max(high)          AS high,
    min(low)           AS low,
    last(close,  time) AS close,
    sum(volume)::bigint AS volume,
    sum(trades)::int    AS trades
FROM ohlc_1h
GROUP BY 1, 2
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'ohlc_1d',
    start_offset      => INTERVAL '7 days',
    end_offset        => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => true
);

-- ── Indices p/ acelerar queries de backtest (ticker, time DESC) ──────────────
-- TS cria indice automatico por bucket; (ticker, time) acelera filtro
-- WHERE ticker=$1 AND time BETWEEN $2 AND $3.
CREATE INDEX IF NOT EXISTS ix_ohlc_5m_ticker_time
    ON ohlc_5m (ticker, time DESC);
CREATE INDEX IF NOT EXISTS ix_ohlc_15m_ticker_time
    ON ohlc_15m (ticker, time DESC);
CREATE INDEX IF NOT EXISTS ix_ohlc_1h_ticker_time
    ON ohlc_1h (ticker, time DESC);
CREATE INDEX IF NOT EXISTS ix_ohlc_1d_ticker_time
    ON ohlc_1d (ticker, time DESC);

-- ── Initial backfill: refresh manual da serie historica completa ─────────────
-- (apenas no primeiro deploy; idempotente se rodado novamente).
-- Refresh propaga 1m -> 5m -> 15m -> 1h -> 1d na ordem.
-- Range generoso (2020 -> futuro) garante cobertura dos backfills atuais.
CALL refresh_continuous_aggregate('ohlc_5m',  '2020-01-01'::timestamptz, '2030-01-01'::timestamptz);
CALL refresh_continuous_aggregate('ohlc_15m', '2020-01-01'::timestamptz, '2030-01-01'::timestamptz);
CALL refresh_continuous_aggregate('ohlc_1h',  '2020-01-01'::timestamptz, '2030-01-01'::timestamptz);
CALL refresh_continuous_aggregate('ohlc_1d',  '2020-01-01'::timestamptz, '2030-01-01'::timestamptz);
