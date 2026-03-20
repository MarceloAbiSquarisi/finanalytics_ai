"""fintz schema fix

Revision ID: 0005_fintz_schema_fix
Revises: 0004_fintz
Create Date: 2026-03-20

Corrige o schema das tabelas Fintz para refletir o schema real dos parquets:

  fintz_cotacoes:
    - volume_negociado: BigInteger → Numeric(24,2)  (parquet é float64)
    - adiciona: preco_medio, quantidade_negociada, quantidade_negocios,
                fator_ajuste_desdobramentos, preco_fechamento_ajustado_desdobramentos

  fintz_itens_contabeis:
    - remove: ano, trimestre, tipo_demonstracao  (não existem no parquet)
    - tipo_periodo permanece (derivado do endpoint chamado)
    - recria unique constraint sem ano/trimestre

  fintz_indicadores:
    - valor: Numeric(24,8) → Numeric(32,12) para evitar overflow em cast
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_fintz_schema_fix"
down_revision = "0004_fintz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── fintz_cotacoes ────────────────────────────────────────────────────────
    # volume_negociado: BigInteger → Numeric (parquet é float64)
    op.alter_column(
        "fintz_cotacoes", "volume_negociado",
        type_=sa.Numeric(24, 2),
        postgresql_using="volume_negociado::numeric",
    )
    # Novas colunas presentes no parquet
    op.add_column("fintz_cotacoes", sa.Column("preco_medio",                              sa.Numeric(18, 6), nullable=True))
    op.add_column("fintz_cotacoes", sa.Column("quantidade_negociada",                     sa.BigInteger(),   nullable=True))
    op.add_column("fintz_cotacoes", sa.Column("quantidade_negocios",                      sa.BigInteger(),   nullable=True))
    op.add_column("fintz_cotacoes", sa.Column("fator_ajuste_desdobramentos",              sa.Numeric(18, 10), nullable=True))
    op.add_column("fintz_cotacoes", sa.Column("preco_fechamento_ajustado_desdobramentos", sa.Numeric(18, 6),  nullable=True))

    # ── fintz_itens_contabeis ─────────────────────────────────────────────────
    # Recria a tabela: parquet não tem ano/trimestre/tipo_demonstracao
    op.drop_table("fintz_itens_contabeis")
    op.create_table(
        "fintz_itens_contabeis",
        sa.Column("ticker",          sa.String(20),     nullable=False),
        sa.Column("item",            sa.String(80),     nullable=False),
        sa.Column("tipo_periodo",    sa.String(16),     nullable=False),  # derivado do endpoint
        sa.Column("data_publicacao", sa.Date(),         nullable=False),
        sa.Column("valor",           sa.Numeric(24, 4), nullable=True),
        sa.UniqueConstraint(
            "ticker", "item", "tipo_periodo", "data_publicacao",
            name="uq_fintz_itens_pit",
        ),
    )
    op.create_index("ix_fintz_itens_ticker",       "fintz_itens_contabeis", ["ticker"])
    op.create_index("ix_fintz_itens_item_periodo", "fintz_itens_contabeis", ["item", "tipo_periodo"])

    # ── fintz_indicadores ─────────────────────────────────────────────────────
    # Amplia precisão para evitar overflow
    op.alter_column(
        "fintz_indicadores", "valor",
        type_=sa.Numeric(32, 12),
        postgresql_using="valor::numeric",
    )


def downgrade() -> None:
    # Indicadores
    op.alter_column(
        "fintz_indicadores", "valor",
        type_=sa.Numeric(24, 8),
        postgresql_using="valor::numeric",
    )

    # Itens contábeis — restaura versão original
    op.drop_table("fintz_itens_contabeis")
    op.create_table(
        "fintz_itens_contabeis",
        sa.Column("ticker",            sa.String(20),  nullable=False),
        sa.Column("item",              sa.String(80),  nullable=False),
        sa.Column("tipo_periodo",      sa.String(16),  nullable=False),
        sa.Column("tipo_demonstracao", sa.String(20),  nullable=True),
        sa.Column("data_publicacao",   sa.Date(),      nullable=False),
        sa.Column("ano",               sa.Integer(),   nullable=False),
        sa.Column("trimestre",         sa.Integer(),   nullable=False),
        sa.Column("valor",             sa.Numeric(24, 4), nullable=True),
        sa.UniqueConstraint(
            "ticker", "item", "tipo_periodo", "data_publicacao",
            name="uq_fintz_itens_pit",
        ),
    )

    # Cotações — remove novas colunas e reverte volume
    for col in (
        "preco_medio", "quantidade_negociada", "quantidade_negocios",
        "fator_ajuste_desdobramentos", "preco_fechamento_ajustado_desdobramentos",
    ):
        op.drop_column("fintz_cotacoes", col)

    op.alter_column(
        "fintz_cotacoes", "volume_negociado",
        type_=sa.BigInteger(),
        postgresql_using="volume_negociado::bigint",
    )
