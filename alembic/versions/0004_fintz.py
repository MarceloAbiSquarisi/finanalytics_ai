"""fintz tables

Revision ID: 0004_fintz
Revises: 0003_password_reset
Create Date: 2026-03-18

Cria as 4 tabelas do pipeline Fintz:

  fintz_sync_log         — registro de idempotência por dataset (1 linha/dataset)
  fintz_cotacoes         — cotações OHLC diárias, todos os tickers B3 desde 2010
  fintz_itens_contabeis  — itens contábeis point-in-time (PIT)
  fintz_indicadores      — indicadores financeiros PIT

Design decisions:

  fintz_sync_log com UNIQUE(dataset_key):
    Cada dataset tem exatamente 1 linha — o estado do último sync.
    O service faz UPSERT pelo dataset_key. Histórico de syncs fica nos
    logs estruturados (structlog), não no banco, para manter a tabela
    pequena e rápida.

  fintz_cotacoes — PRIMARY KEY (ticker, data):
    Chave natural da série temporal. ON CONFLICT DO UPDATE no upsert
    é direto. Não há surrogate key: seria overhead sem benefício para
    leituras analíticas.

  fintz_itens_contabeis — UNIQUE(ticker, item, tipo_periodo, data_publicacao):
    "data_publicacao" é a data em que a informação se tornou pública (PIT).
    O mesmo item/período pode ter múltiplos "data_publicacao" ao longo do
    tempo (ex: restatements). A chave garante que cada ponto no tempo é
    único, preservando o histórico PIT completo.

  fintz_indicadores — UNIQUE(ticker, indicador, data_publicacao):
    Análogo ao item contábil.

  Índices:
    Índices em (ticker) em todas as tabelas de dados — o padrão de
    acesso dominante é "todos os dados de um ticker para backtest".
    Índice em (data) em fintz_cotacoes — acesso por janela temporal.
    Índice em (item, tipo_periodo) em itens_contabeis — queries de
    cross-section (todos os tickers para um dado item/período).
    Índice em (indicador) em fintz_indicadores — idem.

  Sem TimescaleDB neste migration:
    As tabelas Fintz ficam no PostgreSQL principal (mesma engine do
    resto da aplicação). Migrar para TimescaleDB hypertable é possível
    no futuro com ALTER TABLE ... SET (timescaledb.hypertable) —
    sem alteração de schema.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_fintz"
down_revision = "0003_password_reset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── fintz_sync_log ────────────────────────────────────────────────────────
    op.create_table(
        "fintz_sync_log",
        sa.Column("dataset_key", sa.String(128), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("rows_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),  # ok | error | skip
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("dataset_key"),
    )

    # ── fintz_cotacoes ────────────────────────────────────────────────────────
    op.create_table(
        "fintz_cotacoes",
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("preco_fechamento", sa.Numeric(18, 6), nullable=True),
        sa.Column("preco_fechamento_ajustado", sa.Numeric(18, 6), nullable=True),
        sa.Column("preco_abertura", sa.Numeric(18, 6), nullable=True),
        sa.Column("preco_minimo", sa.Numeric(18, 6), nullable=True),
        sa.Column("preco_maximo", sa.Numeric(18, 6), nullable=True),
        sa.Column("volume_negociado", sa.BigInteger(), nullable=True),
        sa.Column("fator_ajuste", sa.Numeric(18, 10), nullable=True),
        sa.PrimaryKeyConstraint("ticker", "data"),
    )
    op.create_index("ix_fintz_cotacoes_ticker", "fintz_cotacoes", ["ticker"])
    op.create_index("ix_fintz_cotacoes_data", "fintz_cotacoes", ["data"])

    # ── fintz_itens_contabeis ─────────────────────────────────────────────────
    op.create_table(
        "fintz_itens_contabeis",
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("item", sa.String(80), nullable=False),
        sa.Column("tipo_periodo", sa.String(16), nullable=False),  # 12M | TRIMESTRAL
        sa.Column("tipo_demonstracao", sa.String(20), nullable=True),  # CONSOLIDADO | INDIVIDUAL
        sa.Column("data_publicacao", sa.Date(), nullable=False),
        sa.Column("ano", sa.Integer(), nullable=False),
        sa.Column("trimestre", sa.Integer(), nullable=False),
        sa.Column("valor", sa.Numeric(24, 4), nullable=True),
        sa.UniqueConstraint(
            "ticker",
            "item",
            "tipo_periodo",
            "data_publicacao",
            name="uq_fintz_itens_pit",
        ),
    )
    op.create_index("ix_fintz_itens_ticker", "fintz_itens_contabeis", ["ticker"])
    op.create_index(
        "ix_fintz_itens_item_periodo",
        "fintz_itens_contabeis",
        ["item", "tipo_periodo"],
    )

    # ── fintz_indicadores ─────────────────────────────────────────────────────
    op.create_table(
        "fintz_indicadores",
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("indicador", sa.String(80), nullable=False),
        sa.Column("data_publicacao", sa.Date(), nullable=False),
        sa.Column("valor", sa.Numeric(24, 8), nullable=True),
        sa.UniqueConstraint(
            "ticker",
            "indicador",
            "data_publicacao",
            name="uq_fintz_indicadores_pit",
        ),
    )
    op.create_index("ix_fintz_indicadores_ticker", "fintz_indicadores", ["ticker"])
    op.create_index("ix_fintz_indicadores_indicador", "fintz_indicadores", ["indicador"])


def downgrade() -> None:
    op.drop_table("fintz_indicadores")
    op.drop_table("fintz_itens_contabeis")
    op.drop_table("fintz_cotacoes")
    op.drop_table("fintz_sync_log")
