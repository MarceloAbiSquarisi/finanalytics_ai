-- 011_b3_no_trading_segment.sql
--
-- Adiciona coluna `segment` em b3_no_trading_days para diferenciar
-- dias atipicos por segmento da B3:
--   'all'     — fechado em todos os segmentos (default existente)
--   'stocks'  — Bovespa (acoes B3) fechado, BM&F (futuros) operando
--   'futures' — BM&F fechado, Bovespa operando (raro mas possivel)
--
-- Motivacao: B3 historicamente tem "Aniversários Bovespa" antecipados
-- onde apenas o segmento de acoes fecha — futuros (WIN, WDO, IND, DOL)
-- continuam operando. Ex: 2026-04-17 (sex) — todos tickers acionarios
-- com 0 ticks, WDOFUT/WINFUT com pregao normal.
--
-- Idempotente. PK passa a ser (target_date, segment) — permite registros
-- distintos por dia se necessario.

ALTER TABLE b3_no_trading_days
    ADD COLUMN IF NOT EXISTS segment TEXT NOT NULL DEFAULT 'all'
    CHECK (segment IN ('all', 'stocks', 'futures'));

-- Drop old PK e cria nova composta (target_date, segment).
-- IF EXISTS p/ idempotencia.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'b3_no_trading_days_pkey'
          AND conrelid = 'b3_no_trading_days'::regclass
    ) THEN
        ALTER TABLE b3_no_trading_days DROP CONSTRAINT b3_no_trading_days_pkey;
    END IF;
END $$;

ALTER TABLE b3_no_trading_days
    ADD CONSTRAINT b3_no_trading_days_pkey PRIMARY KEY (target_date, segment);

-- Seed do caso descoberto via scan multi-ticker (todos tickers acionarios
-- com 0 ticks em 17/abr/2026, WDOFUT/WINFUT com pregao completo).
INSERT INTO b3_no_trading_days (target_date, segment, notes) VALUES
    ('2026-04-17', 'stocks',
     'Bovespa fechado (apenas segmento de acoes); WDOFUT/WINFUT operaram normalmente')
ON CONFLICT (target_date, segment) DO NOTHING;
