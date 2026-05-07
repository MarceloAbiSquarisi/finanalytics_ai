"""0028_paper_trading

Tabelas para forward-test paper trading derivado do R5 harness.

Motivação: harness backtesta config id=11 (h=10, retrain=63, vol=0.015)
sobre histórico até Apr/2026. Validação OOS exige ROD live: gerar signals
diários a partir de "hoje" usando o mesmo pipeline ML, simular execution
(paper, sem capital real), trackear equity curve real e comparar com
backtest expectations.

Estrutura:
  paper_runs (header — 1 row por instância de paper-run nomeada)
    - name UNIQUE (ex.: 'r5-id11-top18'), config_json (params do harness),
      universe (lista de tickers), capital + slots, last_step_date,
      state_json (positions, cash, equity_curve, trades_history)
    - state_json é mutado a cada step. Estrutura:
        {"positions": {ticker: {open_date, open_price, qty, slot_value}},
         "cash": float, "equity_curve": [{date, cash, pos_value, total}],
         "trades_history": [{ticker, open_date, close_date, pnl, pnl_pct}]}

  paper_signals (1 row por ticker × signal_date — daily snapshots)
    - Permite auditoria: por que abriu/fechou em X data?
    - score, prob_pos, p10/p50/p90 (fórmula MLQuantile do harness)
    - current_close (preço de execução), vol_21d (sizing context)

Idempotência: paper_signals UNIQUE (run_id, signal_date, ticker). Script
de step verifica se já tem signal pra hoje antes de regerar.

Revision ID: 0028_paper_trading
Revises: 0027_r5_harness_runs
Create Date: 2026-05-07
"""
from __future__ import annotations

from alembic import op


revision = "0028_paper_trading"
down_revision = "0027_r5_harness_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_runs (
            id              BIGSERIAL    PRIMARY KEY,
            name            VARCHAR(100) UNIQUE NOT NULL,
            config_json     JSONB        NOT NULL,
            universe        TEXT[]       NOT NULL,
            initial_capital NUMERIC(14, 2) NOT NULL DEFAULT 100000,
            n_slots         INTEGER      NOT NULL,
            started_at      DATE         NOT NULL DEFAULT CURRENT_DATE,
            last_step_date  DATE,
            is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
            state_json      JSONB        NOT NULL DEFAULT '{}'::jsonb,
            notes           TEXT,
            created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_signals (
            id            BIGSERIAL    PRIMARY KEY,
            paper_run_id  BIGINT       NOT NULL REFERENCES paper_runs(id) ON DELETE CASCADE,
            signal_date   DATE         NOT NULL,
            ticker        VARCHAR(20)  NOT NULL,
            signal        VARCHAR(10)  NOT NULL,
            score         NUMERIC(10, 4),
            prob_pos      NUMERIC(8, 4),
            p10           NUMERIC(10, 4),
            p50           NUMERIC(10, 4),
            p90           NUMERIC(10, 4),
            current_close NUMERIC(12, 4),
            vol_21d       NUMERIC(8, 4),
            raw_features  JSONB,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT ux_paper_signals UNIQUE (paper_run_id, signal_date, ticker)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_signals_run_date "
        "ON paper_signals (paper_run_id, signal_date DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_paper_signals_ticker "
        "ON paper_signals (ticker, signal_date DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_paper_signals_ticker")
    op.execute("DROP INDEX IF EXISTS ix_paper_signals_run_date")
    op.execute("DROP TABLE IF EXISTS paper_signals")
    op.execute("DROP TABLE IF EXISTS paper_runs")
