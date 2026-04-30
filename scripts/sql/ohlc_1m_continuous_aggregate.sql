-- ohlc_1m_continuous_aggregate.sql
-- Sprint Dashboard Bars (22/abr/2026)
-- Sessão 30/abr/2026: filtro horário pregão B3
--
-- Destrava bars 1m em tempo real para tickers DLL (WDOFUT, WINFUT,
-- DI1F*, etc) que nao passam pelo ohlc_1m_ingestor (BRAPI-only).
--
-- Pattern:
--   1. ohlc_1m_from_ticks: continuous aggregate sobre profit_ticks.
--      materialized_only=false => SELECT combina materializado +
--      raw on-the-fly do bucket atual. Bar do minuto presente
--      atualiza em tempo real conforme novos ticks chegam.
--      WHERE EXTRACT(hour FROM time) BETWEEN 13 AND 20 (UTC)
--      filtra ticks fora do pregão B3 (heartbeats trade_type=3 com
--      price=last_close constante na madrugada inflavam OHLC) E também
--      leilão pre-abertura (call auction com preço estático/baixo volume).
--      Janela 13-20 UTC = 10h-17h59 BRT cobre:
--        - pregão regular B3 (13:00-20:00 UTC = 10:00-17:00 BRT)
--        - leilão fechamento (20:50-20:59 UTC = 17:50-17:59 BRT)
--        - mini-futuros WDO/WIN intra-pregão
--      EXCLUI:
--        - leilão pre-abertura (12:00-12:59 UTC) — preço estático, polui chart
--        - after-market (21:00-21:59 UTC) — baixa liquidez/relevância
--      Para incluir esses, ajustar BETWEEN 12 AND 21.
--   2. Refresh policy 30s materializa minutos fechados (>= 1min).
--   3. View ohlc_1m_unified: UNION ALL ohlc_1m (BRAPI ingestor) +
--      ohlc_1m_from_ticks (DLL profit_ticks). NOT EXISTS evita
--      dupla entrada quando ticker tem origem dupla.
--   4. resampled_repository.py muda FROM ohlc_1m -> ohlc_1m_unified
--      (commit Python separado).
--
-- Idempotente: usa IF NOT EXISTS + CREATE OR REPLACE. Re-rodar e seguro.
--
-- Aplicar (re-create completo após mudança de filtro WHERE):
--   docker exec -i finanalytics_timescale psql -U finanalytics -d market_data \
--       < scripts/sql/ohlc_1m_continuous_aggregate.sql
--
-- Backfill inicial (lento — 7M+ ticks):
--   CALL refresh_continuous_aggregate('ohlc_1m_from_ticks', NULL, NOW() - INTERVAL '1 minute');

-- ── 0. Drop antigos (CASCADE remove view dependente) ────────────────────────

DROP VIEW IF EXISTS ohlc_1m_unified;
DROP MATERIALIZED VIEW IF EXISTS ohlc_1m_from_ticks CASCADE;

-- ── 1. Continuous aggregate ──────────────────────────────────────────────────

CREATE MATERIALIZED VIEW ohlc_1m_from_ticks
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket('1 minute', time) AS time,
    ticker,
    first(price, time)            AS open,
    max(price)                    AS high,
    min(price)                    AS low,
    last(price, time)             AS close,
    sum(quantity)::bigint         AS volume,
    count(*)::int                 AS trades
FROM profit_ticks
WHERE EXTRACT(hour FROM time) BETWEEN 13 AND 20
GROUP BY 1, 2
WITH NO DATA;

-- ── 2. Refresh policy ────────────────────────────────────────────────────────

SELECT add_continuous_aggregate_policy(
    'ohlc_1m_from_ticks',
    start_offset      => INTERVAL '1 hour',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '30 seconds',
    if_not_exists     => true
);

-- ── 3. View unificada ────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW ohlc_1m_unified AS
SELECT time, ticker, open, high, low, close, volume, trades, vwap, source
  FROM ohlc_1m
UNION ALL
SELECT
    time, ticker,
    open::numeric(18, 4),
    high::numeric(18, 4),
    low::numeric(18, 4),
    close::numeric(18, 4),
    volume,
    trades,
    NULL::numeric(18, 4)         AS vwap,
    'profit_ticks_agg'::text     AS source
  FROM ohlc_1m_from_ticks t
 WHERE NOT EXISTS (
     SELECT 1
       FROM ohlc_1m o
      WHERE o.ticker = t.ticker
        AND o.time   = t.time
 );

COMMENT ON VIEW ohlc_1m_unified IS
'Sprint Dashboard Bars (22/abr/2026): UNION ohlc_1m (BRAPI) + ohlc_1m_from_ticks (DLL). Filtro pregão regular 13-20 UTC desde 30/abr (exclui call auction + after-market).';