"""
Sprint F — Fundos de Investimento CVM
Revision ID: 0011_fundos_cvm
Revises: 0010_financial_agents
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0011_fundos_cvm"
down_revision: Union[str, None] = "0010_financial_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Cadastro de fundos (CVM cad_fi.csv) ──────────────────────────────────
    op.create_table(
        "fundos_cadastro",
        sa.Column("cnpj", sa.String(18), primary_key=True),
        sa.Column("denominacao", sa.String(200), nullable=True),
        sa.Column("nome_abrev", sa.String(100), nullable=True),
        sa.Column("tipo", sa.String(50), nullable=True),       # FIA, FIM, FI, FII, FIP...
        sa.Column("classe", sa.String(100), nullable=True),
        sa.Column("situacao", sa.String(20), nullable=True),   # EM FUNCIONAMENTO NORMAL, CANCELADO...
        sa.Column("data_registro", sa.Date, nullable=True),
        sa.Column("data_cancel", sa.Date, nullable=True),
        sa.Column("gestor", sa.String(200), nullable=True),
        sa.Column("administrador", sa.String(200), nullable=True),
        sa.Column("custodiante", sa.String(200), nullable=True),
        sa.Column("auditor", sa.String(200), nullable=True),
        sa.Column("publico_alvo", sa.String(100), nullable=True),  # GERAL, QUALIFICADO, PROFISSIONAL
        sa.Column("taxa_adm", sa.Numeric(8, 4), nullable=True),
        sa.Column("taxa_perfm", sa.Numeric(8, 4), nullable=True),
        sa.Column("benchmark", sa.String(100), nullable=True),
        sa.Column("prazo_resgate", sa.Integer, nullable=True),  # dias
        sa.Column("prazo_cotizacao", sa.Integer, nullable=True),
        sa.Column("pl_atual", sa.Numeric(20, 2), nullable=True),
        sa.Column("cotistas", sa.Integer, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index("ix_fundos_tipo", "fundos_cadastro", ["tipo"])
    op.create_index("ix_fundos_situacao", "fundos_cadastro", ["situacao"])
    op.create_index("ix_fundos_gestor", "fundos_cadastro", ["gestor"])

    # ── Informe diário (CVM inf_diario_fi_AAAAMM.csv) ────────────────────────
    op.create_table(
        "fundos_informe_diario",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cnpj", sa.String(18), nullable=False),
        sa.Column("data_ref", sa.Date, nullable=False),
        sa.Column("vl_total", sa.Numeric(20, 2), nullable=True),   # carteira total
        sa.Column("vl_quota", sa.Numeric(20, 8), nullable=True),   # valor da cota
        sa.Column("vl_patrim_liq", sa.Numeric(20, 2), nullable=True),
        sa.Column("captacao_dia", sa.Numeric(20, 2), nullable=True),
        sa.Column("resgat_dia", sa.Numeric(20, 2), nullable=True),
        sa.Column("nr_cotst", sa.Integer, nullable=True),
    )
    op.create_index("ix_fid_cnpj_data", "fundos_informe_diario",
                    ["cnpj", "data_ref"], unique=True)
    op.create_index("ix_fid_data", "fundos_informe_diario", ["data_ref"])

    # ── Rentabilidade calculada (gerada pelo sync) ────────────────────────────
    op.create_table(
        "fundos_rentabilidade",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cnpj", sa.String(18), nullable=False),
        sa.Column("data_ref", sa.Date, nullable=False),
        sa.Column("rent_dia", sa.Numeric(12, 6), nullable=True),
        sa.Column("rent_mes", sa.Numeric(12, 6), nullable=True),
        sa.Column("rent_ano", sa.Numeric(12, 6), nullable=True),
        sa.Column("rent_12m", sa.Numeric(12, 6), nullable=True),
        sa.Column("rent_24m", sa.Numeric(12, 6), nullable=True),
        sa.Column("rent_36m", sa.Numeric(12, 6), nullable=True),
        sa.Column("volatilidade_12m", sa.Numeric(12, 6), nullable=True),
        sa.Column("sharpe_12m", sa.Numeric(12, 6), nullable=True),
        sa.Column("drawdown_max", sa.Numeric(12, 6), nullable=True),
    )
    op.create_index("ix_fr_cnpj_data", "fundos_rentabilidade",
                    ["cnpj", "data_ref"], unique=True)

    # ── Controle de sync ──────────────────────────────────────────────────────
    op.create_table(
        "fundos_sync_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("competencia", sa.String(7), nullable=False),   # AAAAMM
        sa.Column("tipo", sa.String(20), nullable=False),          # cadastro, informe_diario
        sa.Column("status", sa.String(20), nullable=False),        # ok, erro, em_andamento
        sa.Column("registros", sa.Integer, nullable=True),
        sa.Column("erro", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("fundos_sync_log")
    op.drop_table("fundos_rentabilidade")
    op.drop_table("fundos_informe_diario")
    op.drop_table("fundos_cadastro")
