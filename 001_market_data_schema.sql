-- ──────────────────────────────────────────────────────────────────────────────
-- TimescaleDB — Schema de séries temporais
-- Arquivo: init_timescale/001_market_data_schema.sql
--
-- Por que TimescaleDB e não Postgres puro?
--   - Compressão nativa de séries temporais: 90-95% de redução de espaço
--   - Chunk-based storage: queries por período são O(chunks) não O(tabela)
--   - Políticas de retenção automáticas (ex: manter 5 anos de OHLC 1m)
--   - Continuous aggregates: OHLC 1m → 5m → 1h → 1d em background
--   - Com 196 GB RAM: chunks ativos ficam inteiros em memória
--
-- Estrutura:
--   market_data (db)
--     ├── ohlc_1m          → hypertable (chunk por semana)
--     ├── ohlc_agg_5m      → continuous aggregate sobre ohlc_1m
--     ├── ohlc_agg_1h      → continuous aggregate sobre ohlc_5m
--     ├── ohlc_agg_1d      → continuous aggregate sobre ohlc_1h
--     ├── cotacoes_ts      → cotações Fintz como hypertable (chunk por mês)
--     ├── indicadores_ts   → indicadores fundamentalistas (chunk por trimestre)
--     └── market_events_ts → eventos de mercado (chunk por semana)
-- ──────────────────────────────────────────────────────────────────────────────

-- Habilita TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ── OHLC 1 minuto ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ohlc_1m (
    time        TIMESTAMPTZ NOT NULL,
    ticker      TEXT        NOT NULL,
    open        NUMERIC(18, 4) NOT NULL,
    high        NUMERIC(18, 4) NOT NULL,
    low         NUMERIC(18, 4) NOT NULL,
    close       NUMERIC(18, 4) NOT NULL,
    volume      BIGINT      NOT NULL DEFAULT 0,
    trades      INT         NOT NULL DEFAULT 0,
    vwap        NUMERIC(18, 4),
    source      TEXT        NOT NULL DEFAULT 'brapi'
);

-- Converte em hypertable particionada por semana
-- chunk_time_interval = 7 dias: com dados de ~500 tickers x 390 candles/dia,
-- cada chunk fica ~1.4M linhas → cabe confortavelmente em memória
SELECT create_hypertable(
    'ohlc_1m', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Índices para queries típicas: ticker + range de tempo
CREATE INDEX IF NOT EXISTS ix_ohlc_1m_ticker_time
    ON ohlc_1m (ticker, time DESC);

CREATE INDEX IF NOT EXISTS ix_ohlc_1m_time
    ON ohlc_1m (time DESC);

-- Compressão: chunks com mais de 7 dias são comprimidos automaticamente
-- Com i9-14900K e 16 workers: compressão roda em paralelo sem impacto
ALTER TABLE ohlc_1m SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker',
    timescaledb.compress_orderby = 'time DESC'
);

SELECT add_compression_policy('ohlc_1m',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Retenção: mantém 5 anos de dados 1m (ajuste conforme necessidade)
SELECT add_retention_policy('ohlc_1m',
    drop_after => INTERVAL '5 years',
    if_not_exists => TRUE
);

-- ── Continuous Aggregates ────────────────────────────────────────────────────
-- OHLC 5 minutos — gerado automaticamente a partir do 1m
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlc_agg_5m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('5 minutes', time) AS time,
    ticker,
    FIRST(open, time)              AS open,
    MAX(high)                      AS high,
    MIN(low)                       AS low,
    LAST(close, time)              AS close,
    SUM(volume)                    AS volume,
    SUM(trades)                    AS trades,
    SUM(volume * vwap) / NULLIF(SUM(volume), 0) AS vwap
FROM ohlc_1m
GROUP BY time_bucket('5 minutes', time), ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy('ohlc_agg_5m',
    start_offset => INTERVAL '1 day',
    end_offset   => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

-- OHLC 1 hora
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlc_agg_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS time,
    ticker,
    FIRST(open, time)           AS open,
    MAX(high)                   AS high,
    MIN(low)                    AS low,
    LAST(close, time)           AS close,
    SUM(volume)                 AS volume,
    SUM(trades)                 AS trades,
    SUM(volume * vwap) / NULLIF(SUM(volume), 0) AS vwap
FROM ohlc_agg_5m
GROUP BY time_bucket('1 hour', time), ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy('ohlc_agg_1h',
    start_offset => INTERVAL '2 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- OHLC diário
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlc_agg_1d
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS time,
    ticker,
    FIRST(open, time)          AS open,
    MAX(high)                  AS high,
    MIN(low)                   AS low,
    LAST(close, time)          AS close,
    SUM(volume)                AS volume,
    SUM(trades)                AS trades,
    SUM(volume * vwap) / NULLIF(SUM(volume), 0) AS vwap
FROM ohlc_agg_1h
GROUP BY time_bucket('1 day', time), ticker
WITH NO DATA;

SELECT add_continuous_aggregate_policy('ohlc_agg_1d',
    start_offset => INTERVAL '7 days',
    end_offset   => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- ── Cotações Fintz (série temporal) ──────────────────────────────────────────
-- Diferente do ohlc_1m (intraday), aqui guardamos o snapshot diário Fintz
-- com todos os campos do endpoint de cotações
CREATE TABLE IF NOT EXISTS cotacoes_ts (
    time            TIMESTAMPTZ NOT NULL,  -- data de referência
    ticker          TEXT        NOT NULL,
    close           NUMERIC(18, 4),
    open            NUMERIC(18, 4),
    high            NUMERIC(18, 4),
    low             NUMERIC(18, 4),
    volume          BIGINT,
    market_cap      NUMERIC(24, 2),
    p_l             NUMERIC(10, 4),
    p_vp            NUMERIC(10, 4),
    dividend_yield  NUMERIC(10, 4),
    roe             NUMERIC(10, 4),
    roic            NUMERIC(10, 4),
    ebitda          NUMERIC(24, 2),
    net_revenue     NUMERIC(24, 2),
    net_income      NUMERIC(24, 2),
    gross_margin    NUMERIC(10, 4),
    net_margin      NUMERIC(10, 4),
    sync_id         UUID        -- FK para fintz_sync_log
);

SELECT create_hypertable(
    'cotacoes_ts', 'time',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_cotacoes_ts_ticker_time
    ON cotacoes_ts (ticker, time DESC);

ALTER TABLE cotacoes_ts SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker',
    timescaledb.compress_orderby = 'time DESC'
);

SELECT add_compression_policy('cotacoes_ts',
    compress_after => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- ── Indicadores Fundamentalistas (série temporal) ─────────────────────────────
CREATE TABLE IF NOT EXISTS indicadores_ts (
    time            TIMESTAMPTZ NOT NULL,  -- data de referência (trimestre)
    ticker          TEXT        NOT NULL,
    periodo         TEXT        NOT NULL,  -- ex: '2024Q4'
    receita_liq     NUMERIC(24, 2),
    lucro_liq       NUMERIC(24, 2),
    ebitda          NUMERIC(24, 2),
    divida_liq      NUMERIC(24, 2),
    patrimonio_liq  NUMERIC(24, 2),
    ativo_total     NUMERIC(24, 2),
    roe             NUMERIC(10, 4),
    roic            NUMERIC(10, 4),
    margem_liq      NUMERIC(10, 4),
    margem_ebitda   NUMERIC(10, 4),
    dl_ebitda       NUMERIC(10, 4),
    sync_id         UUID
);

SELECT create_hypertable(
    'indicadores_ts', 'time',
    chunk_time_interval => INTERVAL '90 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_indicadores_ts_ticker_time
    ON indicadores_ts (ticker, time DESC);

ALTER TABLE indicadores_ts SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker',
    timescaledb.compress_orderby = 'time DESC'
);

SELECT add_compression_policy('indicadores_ts',
    compress_after => INTERVAL '90 days',
    if_not_exists => TRUE
);

-- ── Itens Contábeis (série temporal) ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS itens_contabeis_ts (
    time        TIMESTAMPTZ NOT NULL,
    ticker      TEXT        NOT NULL,
    periodo     TEXT        NOT NULL,
    codigo      TEXT        NOT NULL,  -- código CVM do item
    descricao   TEXT,
    valor       NUMERIC(24, 2),
    tipo        TEXT,                  -- 'ativo', 'passivo', 'dre', 'fluxo'
    sync_id     UUID
);

SELECT create_hypertable(
    'itens_contabeis_ts', 'time',
    chunk_time_interval => INTERVAL '90 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_itens_contabeis_ts_ticker_time
    ON itens_contabeis_ts (ticker, time DESC);

CREATE INDEX IF NOT EXISTS ix_itens_contabeis_ts_codigo
    ON itens_contabeis_ts (ticker, codigo, time DESC);

ALTER TABLE itens_contabeis_ts SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker',
    timescaledb.compress_orderby = 'time DESC'
);

SELECT add_compression_policy('itens_contabeis_ts',
    compress_after => INTERVAL '90 days',
    if_not_exists => TRUE
);

-- ── View de conveniência para OHLC multi-resolução ───────────────────────────
-- Útil para o frontend: escolhe automaticamente a resolução correta
-- baseada no intervalo de tempo solicitado
CREATE OR REPLACE VIEW ohlc_auto AS
    SELECT time, ticker, open, high, low, close, volume, trades, vwap,
           '1m'::text AS resolution
    FROM ohlc_1m
    UNION ALL
    SELECT time, ticker, open, high, low, close, volume, trades, vwap,
           '5m'::text
    FROM ohlc_agg_5m
    UNION ALL
    SELECT time, ticker, open, high, low, close, volume, trades, vwap,
           '1h'::text
    FROM ohlc_agg_1h
    UNION ALL
    SELECT time, ticker, open, high, low, close, volume, trades, vwap,
           '1d'::text
    FROM ohlc_agg_1d;

-- ── Estatísticas iniciais ─────────────────────────────────────────────────────
SELECT
    hypertable_name,
    num_chunks,
    pg_size_pretty(hypertable_size(format('%I', hypertable_name)::regclass)) AS size
FROM timescaledb_information.hypertables
ORDER BY hypertable_name;
