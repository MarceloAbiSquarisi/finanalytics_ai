"""0021_backtest_results

Tabela backtest_results para historico comparativo de runs de backtest (R5).

Motivacao: ate 30/abr/2026 cada run de scripts/backtest_demo_dsr.py ou
da API era volatil (so JSON em backtest_runs/). Sem persistencia, e
impossivel comparar evolucao de uma mesma strategy ao longo do tempo
(adicionar slippage, calibrar thresholds, novos dados Nelogica) ou
detectar quando um candidato vencedor degrada.

Idempotencia: config_hash (SHA256) e UNIQUE — re-rodar o mesmo config
faz UPSERT no lugar de duplicar (`ON CONFLICT DO UPDATE` no repo).
Inclui parametros da strategy + flag de slippage + range, garantindo
que mudanca em qualquer dimensao gera nova row.

Estrutura:
  - Identificacao: id (uuid), config_hash, user_id (NULL = system/demo)
  - Config: ticker, strategy, params, range_period, start/end_date,
    initial_capital, objective, slippage_applied
  - Metricas core: total_return_pct, sharpe_ratio, max_drawdown_pct,
    win_rate_pct, profit_factor, calmar_ratio, total_trades, bars_count
  - DSR (LdP 2014): deflated_sharpe, prob_real, num_trials, sample_size
  - Payload completo: full_result_json (JSONB) — top runs + heatmap +
    metricas detalhadas para UI/analytics futuros
  - Auditoria: created_at, updated_at

Indices: config_hash UNIQUE; ticker/strategy/created_at btree p/ queries
"melhores runs por ticker", "evolucao temporal", etc.

Revision ID: 0021_backtest_results
Revises: 0020_diario_is_complete
Create Date: 2026-04-30
"""

from alembic import op

revision = "0021_backtest_results"
down_revision = "0020_diario_is_complete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_results (
            id              VARCHAR(36)   PRIMARY KEY,
            config_hash     VARCHAR(64)   NOT NULL,
            user_id         VARCHAR(100),

            ticker          VARCHAR(20)   NOT NULL,
            strategy        VARCHAR(50)   NOT NULL,
            range_period    VARCHAR(50),
            start_date      DATE,
            end_date        DATE,
            initial_capital FLOAT,
            objective       VARCHAR(20),
            slippage_applied BOOLEAN      NOT NULL DEFAULT TRUE,

            total_return_pct FLOAT,
            sharpe_ratio     FLOAT,
            max_drawdown_pct FLOAT,
            win_rate_pct     FLOAT,
            profit_factor    FLOAT,
            calmar_ratio     FLOAT,
            total_trades     INTEGER,
            bars_count       INTEGER,

            deflated_sharpe  FLOAT,
            prob_real        FLOAT,
            num_trials       INTEGER,
            sample_size      INTEGER,

            params_json      JSONB,
            full_result_json JSONB,

            created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_backtest_results_config_hash "
        "ON backtest_results (config_hash)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_backtest_results_ticker ON backtest_results (ticker)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_backtest_results_strategy ON backtest_results (strategy)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_backtest_results_created_at "
        "ON backtest_results (created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_backtest_results_created_at")
    op.execute("DROP INDEX IF EXISTS ix_backtest_results_strategy")
    op.execute("DROP INDEX IF EXISTS ix_backtest_results_ticker")
    op.execute("DROP INDEX IF EXISTS ux_backtest_results_config_hash")
    op.execute("DROP TABLE IF EXISTS backtest_results")
