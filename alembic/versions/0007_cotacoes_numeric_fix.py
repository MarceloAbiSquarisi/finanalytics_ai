"""0007_cotacoes_numeric_fix

Corrige os tipos das colunas de fintz_cotacoes que causavam overflow
no sync Fintz (Sprint I, 2026-03-22).

Problema:
  - Colunas de preco definidas como NUMERIC(18,6) — overflow para valores
    como preco_fechamento_ajustado_desdobramentos com fatores grandes
  - fator_ajuste e fator_ajuste_desdobramentos como NUMERIC(18,8) —
    overflow para valores como 100.0 (maximo 10^10 com scale 8)
  - volume_negociado como NUMERIC(18,6) — overflow para volumes altos

Solucao aplicada (já em producao, esta migration formaliza):
  - Colunas de preco: NUMERIC(18,6) → NUMERIC(24,4)
  - volume_negociado: NUMERIC(18,6) → NUMERIC(24,2)
  - quantidade_negociada: INTEGER → BIGINT
  - quantidade_negocios: NUMERIC → INTEGER
  - fator_ajuste: NUMERIC(18,8) → DOUBLE PRECISION
  - fator_ajuste_desdobramentos: NUMERIC(18,8) → DOUBLE PRECISION

Estrategia de upgrade/downgrade:
  upgrade: ALTER COLUMN TYPE com USING para conversao segura
  downgrade: reverte para tipos originais (pode perder precisao em dados
             com valores acima dos limites antigos — aceitavel em downgrade)
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Colunas de preco: NUMERIC(18,6) → NUMERIC(24,4)
PRICE_COLUMNS = [
    "preco_fechamento",
    "preco_fechamento_ajustado",
    "preco_abertura",
    "preco_minimo",
    "preco_maximo",
    "preco_medio",
    "preco_fechamento_ajustado_desdobramentos",
]


def upgrade() -> None:
    # ── Colunas de preco ──────────────────────────────────────────────────────
    for col in PRICE_COLUMNS:
        op.alter_column(
            "fintz_cotacoes",
            col,
            type_=sa.Numeric(24, 4),
            existing_type=sa.Numeric(18, 6),
            postgresql_using=f"{col}::NUMERIC(24,4)",
        )

    # ── Volume ────────────────────────────────────────────────────────────────
    op.alter_column(
        "fintz_cotacoes",
        "volume_negociado",
        type_=sa.Numeric(24, 2),
        existing_type=sa.Numeric(18, 6),
        postgresql_using="volume_negociado::NUMERIC(24,2)",
    )

    # ── Quantidade negociada: Integer → BigInteger ────────────────────────────
    op.alter_column(
        "fintz_cotacoes",
        "quantidade_negociada",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
        postgresql_using="quantidade_negociada::BIGINT",
    )

    # ── Quantidade negocios: Numeric → Integer ────────────────────────────────
    op.alter_column(
        "fintz_cotacoes",
        "quantidade_negocios",
        type_=sa.Integer(),
        existing_type=sa.Numeric(),
        postgresql_using="quantidade_negocios::INTEGER",
    )

    # ── Fatores de ajuste: NUMERIC(18,8) → DOUBLE PRECISION ──────────────────
    for col in ("fator_ajuste", "fator_ajuste_desdobramentos"):
        op.alter_column(
            "fintz_cotacoes",
            col,
            type_=sa.Float(),  # SQLAlchemy Float = DOUBLE PRECISION
            existing_type=sa.Numeric(18, 8),
            postgresql_using=f"{col}::DOUBLE PRECISION",
        )


def downgrade() -> None:
    # Reverte para tipos originais
    # AVISO: dados com valores acima dos limites antigos serao truncados

    for col in PRICE_COLUMNS:
        op.alter_column(
            "fintz_cotacoes",
            col,
            type_=sa.Numeric(18, 6),
            existing_type=sa.Numeric(24, 4),
            postgresql_using=f"{col}::NUMERIC(18,6)",
        )

    op.alter_column(
        "fintz_cotacoes",
        "volume_negociado",
        type_=sa.Numeric(18, 6),
        existing_type=sa.Numeric(24, 2),
        postgresql_using="volume_negociado::NUMERIC(18,6)",
    )

    op.alter_column(
        "fintz_cotacoes",
        "quantidade_negociada",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
        postgresql_using="quantidade_negociada::INTEGER",
    )

    op.alter_column(
        "fintz_cotacoes",
        "quantidade_negocios",
        type_=sa.Numeric(),
        existing_type=sa.Integer(),
        postgresql_using="quantidade_negocios::NUMERIC",
    )

    for col in ("fator_ajuste", "fator_ajuste_desdobramentos"):
        op.alter_column(
            "fintz_cotacoes",
            col,
            type_=sa.Numeric(18, 8),
            existing_type=sa.Float(),
            postgresql_using=f"{col}::NUMERIC(18,8)",
        )
