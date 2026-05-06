-- Adiciona colunas extras a ohlc_1m para suportar arquivos Nelogica:
--   aftermarket BOOLEAN  -- TRUE se o bar foi negociado em after-market
--   quantidade  BIGINT   -- quantidade negociada (#acoes/contratos)
--                          distinta de volume (R$ negociados em alguns
--                          formatos de export)
--
-- Idempotente. Aplicado por docker-entrypoint em containers novos e via
-- `docker exec finanalytics_timescale psql ... < 008_*.sql` em existentes.

ALTER TABLE ohlc_1m ADD COLUMN IF NOT EXISTS aftermarket BOOLEAN;
ALTER TABLE ohlc_1m ADD COLUMN IF NOT EXISTS quantidade  BIGINT;
