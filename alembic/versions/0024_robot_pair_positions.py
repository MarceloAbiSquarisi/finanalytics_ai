"""0024_robot_pair_positions

Tabela robot_pair_positions para R3.2.B.3 — persistência de estado das
posições do PairsTradingStrategy. Substitui o dict in-memory `_pair_positions`
no auto_trader_worker (que perdia estado em restart NSSM/container).

Motivação: smoke 2ª 11h vai ativar PAIRS_TRADING_ENABLED=true. Se worker
restartar entre OPEN e CLOSE, in-memory perde a posição → próximo ciclo
acha que pair está em NONE → reabre OPEN duplicado. Persistência fecha
o gap.

Estrutura:
  - pair_key VARCHAR(64) PK — formato "TICKER_A-TICKER_B" (ordem alfabética
    canônica conforme cointegrated_pairs.canonical_order CHECK).
  - position VARCHAR(20) NOT NULL — 'LONG_SPREAD' | 'SHORT_SPREAD'
    (valor 'NONE' = ausência de row, não persiste).
  - opened_at TIMESTAMPTZ NOT NULL
  - last_dispatch_cl_ord_id VARCHAR(100) — cl_ord_id do leg_a no OPEN
    mais recente (audit + dedup pós-restart).
  - updated_at TIMESTAMPTZ — atualiza em qualquer mudança (CLOSE/STOP
    deleta a row, mas updated_at preserva durante OPEN sustentado).

CHECK constraint p/ position: só LONG_SPREAD ou SHORT_SPREAD.

Convenção: row presente = posição aberta. CLOSE/STOP = DELETE da row.
auto_trader_worker._evaluate_pairs lê estado via SELECT * (load no boot)
+ INSERT em OPEN + DELETE em CLOSE/STOP.

Indice ix_robot_pair_positions_opened_at p/ query "posições mais antigas"
(monitoring stale).

Revision ID: 0024_robot_pair_positions
Revises: 0023_cointegrated_pairs
Create Date: 2026-05-01
"""

from alembic import op

revision = "0024_robot_pair_positions"
down_revision = "0023_cointegrated_pairs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS robot_pair_positions (
            pair_key                  VARCHAR(64)  PRIMARY KEY,
            position                  VARCHAR(20)  NOT NULL,
            opened_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            last_dispatch_cl_ord_id   VARCHAR(100),
            updated_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT robot_pair_positions_position_chk
                CHECK (position IN ('LONG_SPREAD', 'SHORT_SPREAD'))
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_robot_pair_positions_opened_at
            ON robot_pair_positions (opened_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_robot_pair_positions_opened_at;")
    op.execute("DROP TABLE IF EXISTS robot_pair_positions;")
