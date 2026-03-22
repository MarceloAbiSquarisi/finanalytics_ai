-- ──────────────────────────────────────────────────────────────────────────────
-- TimescaleDB — Schema de séries temporais (v2 — colunas corrigidas)
-- ──────────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ── fintz_itens_contabeis_ts ──────────────────────────────────────────────────
-- Espelho da tabela OLTP fintz_itens_contabeis como hypertable.
-- 121M linhas / 17 GB → após compressão TimescaleDB: ~1-2 GB
CREATE TABLE IF NOT EXISTS fintz_itens_contabeis_ts (
    time             TIMESTAMPTZ    NOT NULL,  -- data_publicacao
    ticker           VARCHAR(20)    NOT NULL,
    item             VARCHAR(80)    NOT NULL,
    tipo_periodo     VARCHAR(16)    NOT NULL,
    valor            NUMERIC(24,4)
);

SELECT create_hypertable(
    'fintz_itens_contabeis_ts', 'time',
    chunk_time_interval => INTERVAL '90 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_itens_ts_ticker_time
    ON fintz_itens_contabeis_ts (ticker, time DESC);
CREATE INDEX IF NOT EXISTS ix_itens_ts_item_time
    ON fintz_itens_contabeis_ts (item, time DESC);

ALTER TABLE fintz_itens_contabeis_ts SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker, item',
    timescaledb.compress_orderby   = 'time DESC'
);

SELECT add_compression_policy('fintz_itens_contabeis_ts',
    compress_after => INTERVAL '90 days', if_not_exists => TRUE);

-- ── fintz_indicadores_ts ──────────────────────────────────────────────────────
-- 99M linhas / 11 GB → após compressão: ~800 MB
CREATE TABLE IF NOT EXISTS fintz_indicadores_ts (
    time             TIMESTAMPTZ    NOT NULL,  -- data_publicacao
    ticker           VARCHAR(20)    NOT NULL,
    indicador        VARCHAR(80)    NOT NULL,
    valor            NUMERIC(32,12)
);

SELECT create_hypertable(
    'fintz_indicadores_ts', 'time',
    chunk_time_interval => INTERVAL '90 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_indicadores_ts_ticker_time
    ON fintz_indicadores_ts (ticker, time DESC);
CREATE INDEX IF NOT EXISTS ix_indicadores_ts_indicador_time
    ON fintz_indicadores_ts (indicador, time DESC);

ALTER TABLE fintz_indicadores_ts SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker, indicador',
    timescaledb.compress_orderby   = 'time DESC'
);

SELECT add_compression_policy('fintz_indicadores_ts',
    compress_after => INTERVAL '90 days', if_not_exists => TRUE);

-- ── fintz_cotacoes_ts ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS fintz_cotacoes_ts (
    time                                      TIMESTAMPTZ    NOT NULL,  -- data
    ticker                                    VARCHAR(20)    NOT NULL,
    preco_fechamento                          NUMERIC(18,6),
    preco_fechamento_ajustado                 NUMERIC(18,6),
    preco_abertura                            NUMERIC(18,6),
    preco_minimo                              NUMERIC(18,6),
    preco_maximo                              NUMERIC(18,6),
    volume_negociado                          NUMERIC(24,2),
    fator_ajuste                              NUMERIC(18,10),
    preco_medio                               NUMERIC(18,6),
    quantidade_negociada                      BIGINT,
    quantidade_negocios                       BIGINT,
    fator_ajuste_desdobramentos               NUMERIC(18,10),
    preco_fechamento_ajustado_desdobramentos  NUMERIC(18,6)
);

SELECT create_hypertable(
    'fintz_cotacoes_ts', 'time',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_cotacoes_ts_ticker_time
    ON fintz_cotacoes_ts (ticker, time DESC);

ALTER TABLE fintz_cotacoes_ts SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker',
    timescaledb.compress_orderby   = 'time DESC'
);

SELECT add_compression_policy('fintz_cotacoes_ts',
    compress_after => INTERVAL '30 days', if_not_exists => TRUE);

-- ── ohlc_1m ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ohlc_1m (
    time    TIMESTAMPTZ    NOT NULL,
    ticker  TEXT           NOT NULL,
    open    NUMERIC(18,4)  NOT NULL,
    high    NUMERIC(18,4)  NOT NULL,
    low     NUMERIC(18,4)  NOT NULL,
    close   NUMERIC(18,4)  NOT NULL,
    volume  BIGINT         NOT NULL DEFAULT 0,
    trades  INT            NOT NULL DEFAULT 0,
    vwap    NUMERIC(18,4),
    source  TEXT           NOT NULL DEFAULT 'brapi'
);

SELECT create_hypertable(
    'ohlc_1m', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS ix_ohlc_1m_ticker_time
    ON ohlc_1m (ticker, time DESC);

ALTER TABLE ohlc_1m SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ticker',
    timescaledb.compress_orderby   = 'time DESC'
);

SELECT add_compression_policy('ohlc_1m',
    compress_after => INTERVAL '7 days', if_not_exists => TRUE);

-- Relatório final
SELECT hypertable_name, num_chunks
FROM timescaledb_information.hypertables
ORDER BY hypertable_name;
