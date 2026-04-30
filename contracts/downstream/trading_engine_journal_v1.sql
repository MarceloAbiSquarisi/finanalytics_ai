-- =============================================================================
-- DDL canonico de `trading_engine_orders.trade_journal` (downstream contract).
--
-- ESTE ARQUIVO NAO CRIA A TABELA. A criacao e responsabilidade do migration
-- runner do repo finanalyticsai-trading-engine no R-06. Mantemos esta copia
-- aqui apenas para:
--   1. Validacao cruzada (schema-drift-check em CI quando ativado)
--   2. Documentacao da estrutura que a VIEW `public.unified_trade_journal`
--      vai consumir (ver alembic 00XX_unified_trade_journal_view.py)
--
-- Spec viva: `trading_engine_implementacao.md` §8.5 no repo finanalyticsai-trading-engine
-- Handoff: `c5_handoff_for_finanalyticsai.md` neste repo
--
-- Tipos exatos espelham `public.trade_journal` (ver diario_repo.DiarioModel).
-- Qualquer mudanca aqui PRECISA de PR pareado nos dois repos + sync da VIEW.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS trading_engine_orders;

CREATE TABLE IF NOT EXISTS trading_engine_orders.trade_journal (
    id                  VARCHAR(36)      PRIMARY KEY,
    user_id             VARCHAR(100)     NOT NULL DEFAULT 'trading-engine',
    ticker              VARCHAR(20)      NOT NULL,
    direction           VARCHAR(4)       NOT NULL,
    entry_date          TIMESTAMPTZ      NOT NULL,
    exit_date           TIMESTAMPTZ,
    entry_price         FLOAT            NOT NULL,
    exit_price          FLOAT,
    quantity            FLOAT            NOT NULL,
    setup               VARCHAR(50),
    timeframe           VARCHAR(10),
    trade_objective     VARCHAR(20),
    reason_entry        TEXT,
    expectation         TEXT,
    what_happened       TEXT,
    mistakes            TEXT,
    lessons             TEXT,
    emotional_state     VARCHAR(30),
    rating              INTEGER,
    tags                VARCHAR(500),
    pnl                 FLOAT,
    pnl_pct             FLOAT,
    is_winner           BOOLEAN,
    is_complete         BOOLEAN          NOT NULL DEFAULT TRUE,
    external_order_id   VARCHAR(64)      UNIQUE,
    created_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_te_trade_journal_user_id
    ON trading_engine_orders.trade_journal (user_id);
CREATE INDEX IF NOT EXISTS ix_te_trade_journal_ticker
    ON trading_engine_orders.trade_journal (ticker);
CREATE INDEX IF NOT EXISTS ix_te_trade_journal_entry_date
    ON trading_engine_orders.trade_journal (entry_date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_te_trade_journal_external_order_id
    ON trading_engine_orders.trade_journal (external_order_id)
    WHERE external_order_id IS NOT NULL;

-- GRANTs sao aplicados pelo migration runner do engine; replicamos aqui o
-- minimo necessario para que `public.unified_trade_journal` consiga ler.
-- GRANT USAGE ON SCHEMA trading_engine_orders TO finanalytics;
-- GRANT SELECT ON trading_engine_orders.trade_journal TO finanalytics;
