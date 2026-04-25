"""0017_user_is_admin_flag

Separa ADMIN de role em flag ortogonal users.is_admin.

Motivacao: ate entao UserRole era enum (user|admin|master), mutuamente
exclusivo. Operacionalmente admin e privilegio ortogonal — um user OU
um master podem ter poderes administrativos. A modelagem antiga forcava
escolher entre "enxergar carteiras de outros" (master) e "admin" (admin).

Estrategia:
  1. Adiciona coluna is_admin BOOLEAN NOT NULL DEFAULT FALSE.
  2. Data migration: linhas com role='admin' recebem is_admin=TRUE e
     role='user' (admin nao e mais role valido).
  3. Mantem coluna role como String(20) — no codigo UserRole agora so
     tem USER e MASTER, mas leitura de 'admin' legacy continua tolerada
     no _to_domain para o caso de registros escritos durante transicao.

Revision ID: 0017_user_is_admin_flag
Revises: 0016_portfolio_name_history
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa


revision = "0017_user_is_admin_flag"
down_revision = "0016_portfolio_name_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Data migration: admins antigos viram user + is_admin
    op.execute(
        "UPDATE users SET is_admin = TRUE, role = 'user' WHERE role = 'admin'"
    )
    # Remove server_default apos migracao (queremos default no ORM, nao no DDL)
    op.alter_column("users", "is_admin", server_default=None)


def downgrade() -> None:
    # Reverte admins: volta role='admin' para quem tinha is_admin=true
    # (perde-se a distincao para masters que tinham is_admin — ficam master)
    op.execute(
        "UPDATE users SET role = 'admin' WHERE is_admin = TRUE AND role = 'user'"
    )
    op.drop_column("users", "is_admin")
