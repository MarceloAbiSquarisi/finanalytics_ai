"""0027_r5_harness_runs

Tabelas para persistir runs do R5 multi-ticker walk-forward harness.

Motivacao: scripts/r5_harness.py gera JSON em backtest_runs/ — efemero,
nao consultavel. Precisamos de:
  - histórico de runs (config + agregado) para comparar evolucão
    quando muda parâmetros (vol-target, thresholds, retrain_days)
  - per-ticker results queryáveis: "como CSMG3 se comportou em runs
    com vol-target ativo vs sem?"
  - DSR + Neff persistidos (skew/kurt/prob_real do best_ticker), nao
    so' o sharpe.

Por que nao reusar backtest_results (0021): aquela tabela e' por (ticker,
config) — nao tem o conceito de "run multi-ticker com Neff agregado".
Modelar como N rows ali perderia agregados de runa (Neff, dsr_proxy,
elapsed_total). Modelo mais limpo: header r5_runs + filhas r5_ticker_results.

Estrutura:
  r5_runs (header)
    - id, generated_at, version, elapsed_total_s
    - params: horizon, retrain_days, commission, th_buy, th_sell, train_end
    - filtros R5: min_close, target_vol, vol_pos_floor, vol_pos_cap
    - aggregate: sharpe/dd/return distributional stats + best/worst ticker
    - Neff: mean_corr, n_eff_var, n_eff_eig
    - DSR full do best_ticker: observed_sharpe, deflated_sharpe, prob_real,
      e_max, skew, kurt, sample_size
    - raw_payload JSONB para preservar tudo

  r5_ticker_results (1:N filha)
    - run_id FK + ticker + ok/error
    - metricas: sharpe_ratio, total_return_pct, max_drawdown_pct,
      win_rate_pct, profit_factor, calmar_ratio, avg_win/loss_pct
    - filtros: position_size, train_median_close, train_mean_vol_21d
    - raw_metrics JSONB

Indices:
  r5_runs: generated_at DESC, train_end (para queries por janela)
  r5_ticker_results: (run_id, ticker) UNIQUE; ticker (cross-run queries)

Revision ID: 0027_r5_harness_runs
Revises: 0026_notifications
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op


revision = "0027_r5_harness_runs"
down_revision = "0026_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS r5_runs (
            id              BIGSERIAL    PRIMARY KEY,
            generated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            version         VARCHAR(50),
            elapsed_total_s NUMERIC(10, 2),

            horizon         INTEGER      NOT NULL,
            retrain_days    INTEGER      NOT NULL,
            commission      NUMERIC(8, 6),
            th_buy          NUMERIC(8, 4),
            th_sell         NUMERIC(8, 4),
            train_end       DATE,
            min_close       NUMERIC(8, 2),
            target_vol      NUMERIC(8, 4),
            vol_pos_floor   NUMERIC(6, 3),
            vol_pos_cap     NUMERIC(6, 3),

            n_valid         INTEGER,
            n_total         INTEGER,
            n_trades_total  INTEGER,
            sharpe_avg      NUMERIC(10, 4),
            sharpe_median   NUMERIC(10, 4),
            sharpe_std      NUMERIC(10, 4),
            sharpe_max      NUMERIC(10, 4),
            sharpe_min      NUMERIC(10, 4),
            drawdown_avg    NUMERIC(10, 4),
            drawdown_max    NUMERIC(10, 4),
            win_rate_avg    NUMERIC(8, 2),
            return_avg      NUMERIC(12, 2),
            return_median   NUMERIC(12, 2),
            return_total_sum NUMERIC(14, 2),
            n_negative_sharpe INTEGER,
            best_ticker     VARCHAR(20),
            worst_ticker    VARCHAR(20),

            mean_corr       NUMERIC(8, 4),
            n_eff_var       NUMERIC(10, 2),
            n_eff_eig       NUMERIC(10, 2),
            n_raw           INTEGER,
            expected_max_sharpe_raw  NUMERIC(10, 4),
            expected_max_sharpe_neff NUMERIC(10, 4),
            dsr_proxy_raw_n NUMERIC(10, 4),
            dsr_proxy_neff  NUMERIC(10, 4),

            dsr_full_observed_sharpe NUMERIC(10, 4),
            dsr_full_z              NUMERIC(10, 4),
            dsr_full_prob_real      NUMERIC(8, 4),
            dsr_full_e_max          NUMERIC(10, 4),
            dsr_full_num_trials     INTEGER,
            dsr_full_sample_size    INTEGER,
            dsr_full_skew           NUMERIC(10, 4),
            dsr_full_kurtosis       NUMERIC(10, 4),

            raw_payload     JSONB,
            notes           TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_r5_runs_generated_at "
        "ON r5_runs (generated_at DESC)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_r5_runs_train_end ON r5_runs (train_end DESC)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS r5_ticker_results (
            id                BIGSERIAL    PRIMARY KEY,
            run_id            BIGINT       NOT NULL REFERENCES r5_runs(id) ON DELETE CASCADE,
            ticker            VARCHAR(20)  NOT NULL,
            ok                BOOLEAN      NOT NULL DEFAULT TRUE,
            error             TEXT,

            trades            INTEGER,
            winners           INTEGER,
            test_len          INTEGER,
            retrains          INTEGER,
            horizon           INTEGER,
            elapsed_s         NUMERIC(8, 2),

            sharpe_ratio      NUMERIC(10, 4),
            total_return_pct  NUMERIC(12, 2),
            max_drawdown_pct  NUMERIC(10, 2),
            win_rate_pct      NUMERIC(8, 2),
            profit_factor     NUMERIC(10, 4),
            calmar_ratio      NUMERIC(10, 4),
            avg_win_pct       NUMERIC(12, 2),
            avg_loss_pct      NUMERIC(12, 2),
            avg_duration_days NUMERIC(8, 2),
            final_equity      NUMERIC(16, 2),

            position_size       NUMERIC(6, 3),
            train_median_close  NUMERIC(12, 4),
            train_mean_vol_21d  NUMERIC(8, 4),

            raw_metrics       JSONB,
            CONSTRAINT ux_r5_ticker_results UNIQUE (run_id, ticker)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_r5_ticker_results_run "
        "ON r5_ticker_results (run_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_r5_ticker_results_ticker "
        "ON r5_ticker_results (ticker)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_r5_ticker_results_ticker")
    op.execute("DROP INDEX IF EXISTS ix_r5_ticker_results_run")
    op.execute("DROP TABLE IF EXISTS r5_ticker_results")
    op.execute("DROP INDEX IF EXISTS ix_r5_runs_train_end")
    op.execute("DROP INDEX IF EXISTS ix_r5_runs_generated_at")
    op.execute("DROP TABLE IF EXISTS r5_runs")
