"""portfolios: adicionar description, benchmark, is_default

Revision ID: 0002_portfolio_multi
Revises: 0001_baseline
Create Date: 2025-01-15 00:00:00.000000

Sprint: múltiplas carteiras por usuário.

Novas colunas:
  - description (String 500, nullable)   — descrição livre da carteira
  - benchmark   (String 20,  nullable)   — código do índice de referência (ex: IBOV, CDI)
  - is_default  (Boolean, NOT NULL, default False) — flag de carteira padrão do usuário

Constraint: apenas uma carteira por usuário pode ter is_default = TRUE.
Implementada na camada de aplicação (portfolio_service.clear_default) e não
como UNIQUE parcial porque nem todo banco suporta índice parcial via Alembic
de forma portável. Documentado como trade-off consciente.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_portfolio_multi"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Adiciona as três novas colunas ───────────────────────────────────
    op.add_column(
        "portfolios",
        sa.Column("description", sa.String(500), nullable=True),
    )
    op.add_column(
        "portfolios",
        sa.Column("benchmark", sa.String(20), nullable=True),
    )
    op.add_column(
        "portfolios",
        sa.Column(
            "is_default",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),  # Não quebra linhas existentes
        ),
    )

    # ── 2. Elege a carteira mais antiga de cada usuário como padrão ─────────
    # Necessário para que usuários existentes tenham sempre exatamente
    # uma carteira padrão após a migration (invariante de domínio).
    op.execute("""
        UPDATE portfolios
        SET is_default = TRUE
        WHERE id IN (
            SELECT DISTINCT ON (user_id) id
            FROM portfolios
            ORDER BY user_id, created_at ASC NULLS LAST
        )
    """)

    # ── 3. Índice para acelerar queries de carteira padrão ──────────────────
    # Ex: SELECT * FROM portfolios WHERE user_id = $1 AND is_default = TRUE
    op.create_index(
        "ix_portfolios_user_is_default",
        "portfolios",
        ["user_id", "is_default"],
    )


def downgrade() -> None:
    op.drop_index("ix_portfolios_user_is_default", table_name="portfolios")
    op.drop_column("portfolios", "is_default")
    op.drop_column("portfolios", "benchmark")
    op.drop_column("portfolios", "description")
