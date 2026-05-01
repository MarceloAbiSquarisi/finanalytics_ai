"""0022_email_research

Tabela email_research para pipeline E1 (Gmail research bulletins -> tags por
ticker -> enrich /signals).

Motivacao: research institucional (BTG, XP, Genial) move preco em B3 com
event study 1-3d pos-publicacao. Capturar ticker, sentiment, target_price
e action recomendada permite enriquecer ML signals com sinal de mercado
real (analyst consensus / dispersao de targets).

Estrutura:
  - Identidade: id (uuid), msg_id (Gmail message ID — UNIQUE p/ idempotencia),
    broker_source (btg|xp|genial|...)
  - Classificacao: ticker, sentiment (BULLISH|NEUTRAL|BEARISH), action
    (BUY|HOLD|SELL|null), target_price, time_horizon
  - Confidence LLM: confidence (0-1) p/ filtro qualitativo no consumer
  - Conteudo bruto: raw_text_excerpt (primeiros ~500 chars do email apos
    parse) p/ debug e auditoria
  - Auditoria: received_at (timestamp do email), classified_at (quando rodou
    o LLM), created_at (insert)

Indice: msg_id UNIQUE (idempotencia worker); ticker+received_at btree
(query "research recente por ticker"); broker_source+received_at p/
filtro UI por fonte.

Nota: 1 email pode mencionar N tickers — geramos N rows com mesmo msg_id
quebrando UNIQUE. Solucao: PRIMARY KEY composto (msg_id, ticker) em vez
de id+msg_id_unique. Worker faz INSERT ... ON CONFLICT (msg_id, ticker)
DO NOTHING.

Revision ID: 0022_email_research
Revises: 0021_backtest_results
Create Date: 2026-05-01
"""

from alembic import op

revision = "0022_email_research"
down_revision = "0021_backtest_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS email_research (
            msg_id              VARCHAR(255) NOT NULL,
            ticker              VARCHAR(20)  NOT NULL,
            broker_source       VARCHAR(50)  NOT NULL,
            sentiment           VARCHAR(20)  NOT NULL,
            action              VARCHAR(10),
            target_price        DOUBLE PRECISION,
            time_horizon        VARCHAR(50),
            confidence          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            raw_text_excerpt    TEXT,
            received_at         TIMESTAMPTZ  NOT NULL,
            classified_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (msg_id, ticker),
            CONSTRAINT email_research_sentiment_chk
                CHECK (sentiment IN ('BULLISH', 'NEUTRAL', 'BEARISH')),
            CONSTRAINT email_research_action_chk
                CHECK (action IS NULL OR action IN ('BUY', 'HOLD', 'SELL'))
        );
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_research_ticker_received
            ON email_research (ticker, received_at DESC);
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_email_research_broker_received
            ON email_research (broker_source, received_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_email_research_broker_received;")
    op.execute("DROP INDEX IF EXISTS ix_email_research_ticker_received;")
    op.execute("DROP TABLE IF EXISTS email_research;")
