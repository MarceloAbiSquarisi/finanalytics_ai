"""0019_diario_trade_objective

Adiciona coluna trade_journal.trade_objective para registrar a intencao
de holding period da operacao: daytrade | swing | buy_hold.

Motivacao: o diario ja registra setup tecnico, mas nao distingue se o
trade foi planejado como Day Trade (entrada/saida no mesmo pregao),
Swing (dias a semanas) ou Buy & Hold (longo prazo). Esse eixo e
ortogonal ao setup e a direction, e e util para metricas separadas
por horizonte (win rate de DT vs Swing tem distribuicoes diferentes).

Notas:
  - Coluna nullable — entradas existentes ficam sem objetivo registrado.
  - Validacao de valor (^(daytrade|swing|buy_hold)$) ocorre no Pydantic
    da rota, nao via CHECK no schema (mantem consistencia com setup,
    emotional_state, timeframe que tambem nao tem CHECK).
  - DB de dev ja recebeu ALTER TABLE manual (IF NOT EXISTS) — esta
    migration formaliza o registro no historico do alembic. Em ambientes
    novos, roda como ADD COLUMN normal.

Revision ID: 0019_diario_trade_objective
Revises: 0018_portfolio_per_account
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_diario_trade_objective"
down_revision = "0018_portfolio_per_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS — idempotente para ambientes onde o ALTER manual ja rodou.
    op.execute("ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS trade_objective VARCHAR(20)")


def downgrade() -> None:
    op.drop_column("trade_journal", "trade_objective")
