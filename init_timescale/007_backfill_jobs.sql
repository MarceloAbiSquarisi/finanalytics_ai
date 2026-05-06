-- Backfill jobs (admin /admin → aba Backfill).
--
-- Idempotente. Aplicado por docker-entrypoint do TimescaleDB no boot e via
-- scripts/apply_backfill_migration.py em ambientes existentes.
--
-- Tabelas:
--   backfill_jobs       — 1 linha por job disparado pelo admin (range × tickers).
--   backfill_job_items  — granular (1 linha por ticker × dia útil dentro do job).
--                         Status final: pending|running|ok|skip|err.
--
-- Failures dashboard query: SELECT ... FROM backfill_job_items WHERE status='err'
-- AND target_date BETWEEN $1 AND $2.

CREATE TABLE IF NOT EXISTS backfill_jobs (
    id                BIGSERIAL    PRIMARY KEY,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    status            TEXT         NOT NULL DEFAULT 'queued',
    cancel_requested  BOOLEAN      NOT NULL DEFAULT FALSE,
    tickers           TEXT[]       NOT NULL,
    date_start        DATE         NOT NULL,
    date_end          DATE         NOT NULL,
    force_refetch     BOOLEAN      NOT NULL DEFAULT FALSE,
    total_items       INT          NOT NULL DEFAULT 0,
    done_items        INT          NOT NULL DEFAULT 0,
    ok_items          INT          NOT NULL DEFAULT 0,
    err_items         INT          NOT NULL DEFAULT 0,
    skip_items        INT          NOT NULL DEFAULT 0,
    requested_by      TEXT,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS ix_backfill_jobs_created
    ON backfill_jobs (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_backfill_jobs_status
    ON backfill_jobs (status) WHERE status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS backfill_job_items (
    id              BIGSERIAL     PRIMARY KEY,
    job_id          BIGINT        NOT NULL REFERENCES backfill_jobs(id) ON DELETE CASCADE,
    ticker          TEXT          NOT NULL,
    exchange        TEXT          NOT NULL DEFAULT 'B',
    target_date     DATE          NOT NULL,
    status          TEXT          NOT NULL DEFAULT 'pending',
    ticks_returned  INT,
    inserted        INT,
    elapsed_s       NUMERIC(8,2),
    error_msg       TEXT,
    attempts        INT           NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    UNIQUE (job_id, ticker, exchange, target_date)
);

CREATE INDEX IF NOT EXISTS ix_backfill_job_items_job_status
    ON backfill_job_items (job_id, status);

CREATE INDEX IF NOT EXISTS ix_backfill_job_items_failures
    ON backfill_job_items (status, target_date DESC) WHERE status = 'err';

CREATE INDEX IF NOT EXISTS ix_backfill_job_items_ticker
    ON backfill_job_items (ticker, target_date DESC);
