"""0025_b3_delisted_tickers

Tabela b3_delisted_tickers — survivorship bias step 0 (R5 último item aberto).

Motivacao: backtest harness (R5) hoje opera so' sobre tickers com dados em
fintz_cotacoes_ts ou ohlc_1m. Empresas que sairam da B3 (delisting voluntario,
cancelamento de registro, OPA, fusao, falencia) nao aparecem no universo,
gerando survivorship bias positivo: backtest sobre IBOV histórico inclui
implicitamente o vies "sobreviventes". DSR + walk-forward NAO corrigem isso.

Esta tabela arma a infraestrutura. Step 1 (defer): popular via CVM CSV +
bridge CNPJ→ticker da B3.

Estrutura:
  - ticker VARCHAR(20) PK (acomoda placeholder UNK_<14 digitos cnpj> ate' step 1)
  - cnpj VARCHAR(18) — chave de cruzamento com CVM
  - razao_social VARCHAR(200)
  - delisting_date DATE — quando saiu da bolsa (NULL se nao temos data exata)
  - delisting_reason VARCHAR(50) — 'CANCELAMENTO_REGISTRO' | 'OPA' | 'FUSAO' |
                                    'CISAO' | 'FALENCIA' | 'OUTRO'
  - last_known_price DECIMAL(12,4) — ultimo close conhecido (informativo)
  - last_known_date DATE — data desse ultimo close
  - source VARCHAR(20) — 'CVM' | 'B3' | 'MANUAL' | 'NEWS' | 'FINTZ'
  - notes TEXT — observacoes livres (ex.: ticker novo apos fusao)
  - created_at, updated_at TIMESTAMPTZ

Indices: ticker (PK), delisting_date (para queries "delistou em [intervalo]").

Uso esperado por R5:
  - candle_repository.get_ohlc_bars: ao listar tickers do universo de backtest,
    INCLUIR tickers de b3_delisted_tickers cuja delisting_date >= data_inicial
    do backtest. Bars depois de delisting_date sao truncados (posicao force-close
    com last_known_price + 0% slippage adicional).
  - signals_history backfill: skip tickers da b3_delisted (ja saíram da watchlist).

Revision ID: 0025_b3_delisted_tickers
Revises: 0024_robot_pair_positions
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "0025_b3_delisted_tickers"
down_revision = "0024_robot_pair_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS b3_delisted_tickers (
            ticker            VARCHAR(20) PRIMARY KEY,
            cnpj              VARCHAR(18),
            razao_social      VARCHAR(200),
            delisting_date    DATE,
            delisting_reason  VARCHAR(50),
            last_known_price  DECIMAL(12, 4),
            last_known_date   DATE,
            source            VARCHAR(20) NOT NULL DEFAULT 'CVM',
            notes             TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT b3_delisted_reason_chk CHECK (
                delisting_reason IS NULL OR delisting_reason IN (
                    'CANCELAMENTO_REGISTRO', 'OPA', 'FUSAO', 'CISAO',
                    'FALENCIA', 'OUTRO'
                )
            ),
            CONSTRAINT b3_delisted_source_chk CHECK (
                source IN ('CVM', 'B3', 'MANUAL', 'NEWS', 'FINTZ')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_b3_delisted_delisting_date "
        "ON b3_delisted_tickers (delisting_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_b3_delisted_cnpj "
        "ON b3_delisted_tickers (cnpj) WHERE cnpj IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS b3_delisted_tickers")
