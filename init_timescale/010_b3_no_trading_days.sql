-- 010_b3_no_trading_days.sql
--
-- Tabela de dias atipicos B3: dias que parecem trading day pela lib
-- `holidays` (e por weekday <5) mas que de fato NAO tiveram pregao
-- coletavel — descobertos automaticamente quando "Preencher gaps"
-- retorna ok com 0 ticks ou inseridos manualmente.
--
-- Casos conhecidos:
--   - 2021-01-25: Aniversario cidade de SP (B3 fechou)
--   - 2021-07-09: Revolução Constitucionalista (B3 fechou esse ano)
--   - 2022-12-30: Aniversario Bovespa antecipado p/ ultimo dia util
--   - 2023-12-29: Aniversario Bovespa antecipado p/ ultimo dia util
--
-- Ao inserir um registro aqui, is_trading_day() passa a retornar False
-- p/ esse dia, removendo-o de queries de gap automaticamente. Auto-popular
-- pelo backfill_runner quando ticker x dia coleta retorna 0 ticks
-- (final='ok' AND ticks_returned=0): isso evita o loop "preencher → 0
-- ticks → reaparece como gap".
--
-- Idempotente. Aplicado por docker-entrypoint em containers novos.

CREATE TABLE IF NOT EXISTS b3_no_trading_days (
    target_date          DATE PRIMARY KEY,
    discovered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    discovered_by_job_id BIGINT,
    notes                TEXT
);

-- Seed dos casos conhecidos (idempotente). Notes documenta a razao.
INSERT INTO b3_no_trading_days (target_date, notes) VALUES
    ('2021-01-25', 'Aniversário cidade de São Paulo — B3 fechou (atípico)'),
    ('2021-07-09', 'Revolução Constitucionalista — B3 fechou em 2021 (atípico)'),
    ('2022-12-30', 'Aniversário Bovespa antecipado — sessão única ou fechado'),
    ('2023-12-29', 'Aniversário Bovespa antecipado — sessão única ou fechado')
ON CONFLICT (target_date) DO NOTHING;
