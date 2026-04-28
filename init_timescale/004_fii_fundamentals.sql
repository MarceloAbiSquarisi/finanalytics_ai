-- N5 (27/abr/2026) — Fundamentals FII via Status Invest
-- Snapshots periodicos (geralmente diarios) de DY TTM, P/VP, dividendos 12m
-- e valor de mercado. Populado por scripts/scrape_status_invest_fii.py.
-- Idempotente por (ticker, snapshot_date) — re-rodar no mesmo dia faz UPSERT.

CREATE TABLE IF NOT EXISTS fii_fundamentals (
    ticker          VARCHAR(10)      NOT NULL,
    snapshot_date   DATE             NOT NULL DEFAULT CURRENT_DATE,
    dy_ttm          NUMERIC(8,4),       -- DY trailing 12 meses (% anualizado)
    p_vp            NUMERIC(8,4),       -- preco / valor patrimonial cota
    div_12m         NUMERIC(12,4),      -- soma proventos 12m em R$
    valor_mercado   NUMERIC(18,2),      -- valor de mercado em R$
    source          TEXT             NOT NULL DEFAULT 'status_invest',
    scraped_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS ix_fii_fundamentals_ticker
    ON fii_fundamentals (ticker, snapshot_date DESC);
