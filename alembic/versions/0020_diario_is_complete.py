"""0020_diario_is_complete

Adiciona campos de workflow no trade_journal:

- is_complete: BOOL NOT NULL DEFAULT FALSE — flag manual marcando que
  a entrada qualitativa foi finalizada (reason_entry, expectation, etc).
- external_order_id: VARCHAR(64) UNIQUE NULL — ID da ordem origem (DLL
  local_order_id ou similar). Usado para idempotencia quando o hook
  de FILLED dispara criacao automatica da entry no diario; uma segunda
  chamada com mesmo external_order_id e ignorada.

Motivacao: novo fluxo "trade executado -> entry pre-preenchida no diario
+ alerta de pendente ate usuario completar". external_order_id permite
que o callback de FILLED seja idempotente (DLL pode chamar callback
multiplas vezes para a mesma ordem). is_complete da o filtro
"Incompletos" no /diario e o contador no sino topbar.

Notas:
  - Default FALSE para is_complete: entries existentes ficam como
    "incompletas" tecnicamente. UI tem botao "Marcar como completo"
    para o usuario fazer triagem one-time se quiser.
  - external_order_id e nullable porque entries criadas manualmente
    nao tem ordem origem.

Revision ID: 0020_diario_is_complete
Revises: 0019_diario_trade_objective
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_diario_is_complete"
down_revision = "0019_diario_trade_objective"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trade_journal "
        "ADD COLUMN IF NOT EXISTS is_complete BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute("ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS external_order_id VARCHAR(64)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_journal_external_order_id "
        "ON trade_journal(external_order_id) "
        "WHERE external_order_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_trade_journal_external_order_id")
    op.drop_column("trade_journal", "external_order_id")
    op.drop_column("trade_journal", "is_complete")
