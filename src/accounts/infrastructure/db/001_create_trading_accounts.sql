-- migrations/001_create_trading_accounts.sql
-- Executar via: psql $DATABASE_URL -f migrations/001_create_trading_accounts.sql
-- Ou via PostgresAccountRepository.migrate() no startup.

CREATE TABLE IF NOT EXISTS trading_accounts (
    uuid              TEXT        PRIMARY KEY,
    broker_id         TEXT        NOT NULL,
    account_id        TEXT        NOT NULL,
    account_type      TEXT        NOT NULL CHECK (account_type IN ('real', 'simulator')),
    label             TEXT        NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'inactive'
                                  CHECK (status IN ('active', 'inactive')),
    routing_password  TEXT,
    broker_name       TEXT,
    sub_account_id    TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_trading_account UNIQUE (broker_id, account_id, account_type)
);

CREATE INDEX IF NOT EXISTS idx_trading_accounts_status
    ON trading_accounts (status)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_trading_accounts_type
    ON trading_accounts (account_type);

COMMENT ON TABLE trading_accounts IS
    'Contas de negociação. Apenas uma pode estar ativa por vez (status=active).';

COMMENT ON COLUMN trading_accounts.routing_password IS
    'Senha de roteamento em plaintext. Candidato a migrar para vault/secrets manager.';
