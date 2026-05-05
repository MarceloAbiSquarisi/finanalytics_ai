-- Robot Trade (R1) — schemas para auto_trader_worker.
--
-- Idempotente. Aplicado por docker-entrypoint do TimescaleDB no boot e via
-- alembic migration ts_0004 (mesmas DDLs).
--
-- Tabelas:
--   robot_strategies     — registry de strategies ativas (config + account)
--   robot_signals_log    — toda decisao do worker (sent_to_dll OR reason_skipped)
--   robot_orders_intent  — ordens originadas pelo robo (separa de profit_orders
--                          manual). Liga via local_order_id ao callback DLL.
--   robot_risk_state     — estado diario de risco + kill switch (paused).

-- ── 1. Strategies registry ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS robot_strategies (
    id              SERIAL          PRIMARY KEY,
    name            TEXT            NOT NULL UNIQUE,
    enabled         BOOLEAN         NOT NULL DEFAULT FALSE,
    -- config_json formato: { "tickers": [...], "params": {...}, "schedule_min": 5 }
    config_json     JSONB           NOT NULL DEFAULT '{}'::jsonb,
    -- conta DayTrade alvo (FK logica para investment_accounts.id)
    account_id      INTEGER,
    description     TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_robot_strategies_enabled
    ON robot_strategies (enabled) WHERE enabled = TRUE;


-- ── 2. Signals log ───────────────────────────────────────────────────────────
--
-- Toda iteracao do strategy loop registra o que decidiu, mesmo quando NAO
-- envia ordem (reason_skipped). Auditoria + base de relatorios "porque robo
-- nao operou hoje?".

CREATE TABLE IF NOT EXISTS robot_signals_log (
    id                  SERIAL          PRIMARY KEY,
    strategy_id         INTEGER,         -- FK logica robot_strategies.id (NULL p/ heartbeat)
    strategy_name       TEXT,
    ticker              TEXT,
    action              TEXT,            -- 'BUY'|'SELL'|'HOLD'|'SKIP'|'HEARTBEAT'
    computed_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    -- Quando enviado: liga ao callback DLL via local_order_id
    sent_to_dll         BOOLEAN         NOT NULL DEFAULT FALSE,
    -- BIGINT: agent retorna IDs ate ~10^14 (formato yymmdd+ms+counter); INT4
    -- overflowa em local_order_id > 2.1B (smoke 05/mai 10:37 quebrou aqui).
    local_order_id      BIGINT,         -- preenche apos POST /agent/order/send retornar
    -- Quando NAO enviado: motivo (kill switch, risk reject, no signal, etc.)
    reason_skipped      TEXT,
    -- Snapshot do contexto p/ debug (preco, sinal_ml, regimen, position size)
    payload_json        JSONB
);

CREATE INDEX IF NOT EXISTS ix_robot_signals_log_computed_at
    ON robot_signals_log (computed_at DESC);
CREATE INDEX IF NOT EXISTS ix_robot_signals_log_strategy_ticker
    ON robot_signals_log (strategy_id, ticker, computed_at DESC);
CREATE INDEX IF NOT EXISTS ix_robot_signals_log_sent
    ON robot_signals_log (sent_to_dll, computed_at DESC) WHERE sent_to_dll = TRUE;


-- ── 3. Orders intent ─────────────────────────────────────────────────────────
--
-- Espelho compacto do que o robo MANDOU. profit_orders mantem o estado
-- canonico (callback DLL); intent guarda parametros originais + signal_id
-- p/ ligar de volta.

CREATE TABLE IF NOT EXISTS robot_orders_intent (
    id                  SERIAL          PRIMARY KEY,
    signal_log_id       INTEGER         NOT NULL,
    strategy_id         INTEGER,
    ticker              TEXT            NOT NULL,
    side                TEXT            NOT NULL,    -- 'buy'|'sell'
    order_type          TEXT            NOT NULL,    -- 'limit'|'market'|'stop'
    quantity            DOUBLE PRECISION NOT NULL,
    price               DOUBLE PRECISION,
    -- OCO attached (TP + SL): NULL se ordem solta
    take_profit         DOUBLE PRECISION,
    stop_loss           DOUBLE PRECISION,
    -- Liga ao callback DLL (preenchido apos resposta /order/send).
    -- BIGINT: ver nota em robot_signals_log.local_order_id acima.
    local_order_id      BIGINT,
    cl_ord_id           TEXT,            -- idempotencia no proxy
    -- Status interno (sucesso/erro envio, NAO o status DLL final)
    sent_at             TIMESTAMPTZ,
    error_msg           TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_robot_orders_intent_signal
    ON robot_orders_intent (signal_log_id);
CREATE INDEX IF NOT EXISTS ix_robot_orders_intent_local_order
    ON robot_orders_intent (local_order_id) WHERE local_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_robot_orders_intent_strategy_created
    ON robot_orders_intent (strategy_id, created_at DESC);


-- ── 4. Risk state (1 row por dia + kill switch persistente) ──────────────────

CREATE TABLE IF NOT EXISTS robot_risk_state (
    date            DATE            PRIMARY KEY,
    total_pnl       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    realized_pnl    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unrealized_pnl  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    max_dd          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    positions_count INTEGER         NOT NULL DEFAULT 0,
    -- Kill switch: TRUE bloqueia novas ordens do robo (manual ou auto-trip).
    -- Dia novo NAO reseta automaticamente — ops sobe via PUT /api/v1/robot/resume.
    paused          BOOLEAN         NOT NULL DEFAULT FALSE,
    paused_at       TIMESTAMPTZ,
    paused_reason   TEXT,
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Singleton row p/ "today" — funcao auxiliar lida com upsert pela aplicacao.
