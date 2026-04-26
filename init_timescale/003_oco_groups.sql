-- OCO + Trailing + Splits parciais — Phase A (26/abr/2026)
-- Spec: Design_OCO_Trailing_Splits.md
-- Decisões aplicadas: TP/SL individualmente opcionais (D3), sem limite de níveis (D5),
-- trailing R$ E % suportados (D1), parent fill parcial → re-rateio proporcional (D2).

-- Group: container de uma estratégia OCO (parent + N níveis).
CREATE TABLE IF NOT EXISTS profit_oco_groups (
    group_id          UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_order_id   BIGINT,                         -- local_order_id da ordem mãe (NULL se OCO solo)
    env               TEXT             NOT NULL DEFAULT 'simulation',
    ticker            TEXT             NOT NULL,
    exchange          TEXT             NOT NULL DEFAULT 'B',
    side              SMALLINT         NOT NULL,      -- 1=buy, 2=sell (lado das proteções; geralmente oposto do parent)
    total_qty         BIGINT           NOT NULL,
    remaining_qty     BIGINT           NOT NULL,
    status            VARCHAR(20)      NOT NULL DEFAULT 'awaiting',
                                                      -- awaiting | active | partial | completed | cancelled
    is_daytrade       BOOLEAN          NOT NULL DEFAULT TRUE,
    broker_id         INTEGER,
    account_id        TEXT,
    sub_account_id    TEXT,
    routing_password  TEXT,                           -- pode ser NULL; agent injeta de credentials se ausente
    user_account_id   TEXT,
    portfolio_id      TEXT,
    notes             TEXT,
    created_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    completed_at      TIMESTAMPTZ,
    CONSTRAINT chk_oco_group_qty_pos CHECK (total_qty > 0 AND remaining_qty >= 0),
    CONSTRAINT chk_oco_group_status  CHECK (status IN ('awaiting','active','partial','completed','cancelled'))
);

CREATE INDEX IF NOT EXISTS ix_oco_groups_parent   ON profit_oco_groups (parent_order_id) WHERE parent_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_oco_groups_status   ON profit_oco_groups (status);
CREATE INDEX IF NOT EXISTS ix_oco_groups_ticker   ON profit_oco_groups (ticker, status);

-- Garantia: 1 group ativo por parent (evita duplo attach_oco)
CREATE UNIQUE INDEX IF NOT EXISTS ux_oco_groups_one_awaiting_per_parent
    ON profit_oco_groups (parent_order_id)
    WHERE parent_order_id IS NOT NULL AND status IN ('awaiting','active','partial');


-- Level: 1 nível dentro de um group. Cada nível pode ter TP, SL ou ambos (Decisão 3).
CREATE TABLE IF NOT EXISTS profit_oco_levels (
    level_id          UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id          UUID             NOT NULL REFERENCES profit_oco_groups(group_id) ON DELETE CASCADE,
    level_idx         SMALLINT         NOT NULL,
    qty               BIGINT           NOT NULL,
    -- Take Profit (opcional)
    tp_price          DOUBLE PRECISION,
    tp_order_id       BIGINT,
    tp_status         VARCHAR(20),                    -- pending|sent|filled|cancelled|rejected
    -- Stop Loss (opcional)
    sl_trigger        DOUBLE PRECISION,
    sl_limit          DOUBLE PRECISION,               -- preço limite stop-limit (default = trigger)
    sl_order_id       BIGINT,
    sl_status         VARCHAR(20),
    -- Trailing (opcional, só aplica em SL)
    is_trailing       BOOLEAN          NOT NULL DEFAULT FALSE,
    trail_distance    DOUBLE PRECISION,               -- distância em R$
    trail_pct         DOUBLE PRECISION,               -- distância em %
    trail_high_water  DOUBLE PRECISION,               -- maior preço já visto (long) ou menor (short)
    created_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_oco_level_has_protection CHECK (tp_price IS NOT NULL OR sl_trigger IS NOT NULL),
    CONSTRAINT chk_oco_level_qty_pos CHECK (qty > 0),
    CONSTRAINT chk_oco_level_trail_one CHECK (
        NOT is_trailing OR (trail_distance IS NOT NULL OR trail_pct IS NOT NULL)
    ),
    UNIQUE (group_id, level_idx)
);

CREATE INDEX IF NOT EXISTS ix_oco_levels_orders   ON profit_oco_levels (tp_order_id, sl_order_id);
CREATE INDEX IF NOT EXISTS ix_oco_levels_group    ON profit_oco_levels (group_id, level_idx);
