"""0023_cointegrated_pairs

Tabela cointegrated_pairs para pipeline R3.1 (pares estatisticamente
cointegrados na B3 — Engle-Granger 2-step + half-life Ornstein-Uhlenbeck).

Motivacao: bancos (ITUB4/BBDC4/SANB11/BBAS3) e petro (PETR3/PETR4)
historicamente cointegrados ha 10+ anos. Z-score do spread cruza thresholds
(±2 entrada, 0.5 saida, 4 stop) com Sharpe documentado 1-1.5 em B3.
Estrategia roda no R3.2 — esta tabela e' a fonte de verdade do que e'
cointegrado HOJE (regime change pode quebrar; re-test diario obrigatorio).

Estrutura:
  - Identidade: id (serial), (ticker_a, ticker_b) UNIQUE em ordem alfabetica
    canonica (ITUB4,BBDC4 sempre virou BBDC4,ITUB4) p/ evitar duplicatas
  - Hedge: beta (coef OLS A ~ B), rho (Pearson)
  - Cointegracao: p_value_adf (Augmented Dickey-Fuller no spread),
    cointegrated (BOOL = p_value_adf < 0.05), half_life (dias p/ reverter
    metade do desvio — Ornstein-Uhlenbeck via AR(1) no diff do residuo)
  - Janela: lookback_days (default 252 = 1 ano util)
  - Auditoria: last_test_date (DATE quando rodou o screening), updated_at,
    created_at

Indices:
  - UNIQUE (ticker_a, ticker_b) — ordem alfabetica canonica
  - btree (cointegrated, last_test_date DESC) p/ filtro "pares ativos hoje"

Convencoes:
  - Strategy NUNCA escreve aqui — apenas le. Job offline (R3.1) faz UPSERT.
  - Quando p_value > 0.05 (cointegrado quebrou), row e' UPDATE com
    cointegrated=false; NAO deletada (preserva historico p/ analise).

Revision ID: 0023_cointegrated_pairs
Revises: 0022_email_research
Create Date: 2026-05-01
"""

from alembic import op

revision = "0023_cointegrated_pairs"
down_revision = "0022_email_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS cointegrated_pairs (
            id              SERIAL          PRIMARY KEY,
            ticker_a        VARCHAR(20)     NOT NULL,
            ticker_b        VARCHAR(20)     NOT NULL,
            beta            DOUBLE PRECISION NOT NULL,
            rho             DOUBLE PRECISION NOT NULL,
            p_value_adf     DOUBLE PRECISION NOT NULL,
            cointegrated    BOOLEAN         NOT NULL,
            half_life       DOUBLE PRECISION,
            lookback_days   INTEGER         NOT NULL,
            last_test_date  DATE            NOT NULL,
            created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
            CONSTRAINT cointegrated_pairs_canonical_order
                CHECK (ticker_a < ticker_b),
            CONSTRAINT cointegrated_pairs_unique
                UNIQUE (ticker_a, ticker_b)
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_cointegrated_pairs_active
            ON cointegrated_pairs (cointegrated, last_test_date DESC)
            WHERE cointegrated = TRUE;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cointegrated_pairs_active;")
    op.execute("DROP TABLE IF EXISTS cointegrated_pairs;")
