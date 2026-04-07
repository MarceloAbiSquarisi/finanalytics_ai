"""0013_merge_heads

Merge revision: une os 4 heads paralelos antes de continuar.

Revision ID: 0013_merge_heads
Revises: 0011_event_records, 0011_fundos_cvm, 0012_ml_features, ts_0002
Create Date: 2026-04-07
"""
from alembic import op

revision = "0013_merge_heads"
down_revision = (
    "0011_event_records",
    "0011_fundos_cvm",
    "0012_ml_features",
    "ts_0002",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass