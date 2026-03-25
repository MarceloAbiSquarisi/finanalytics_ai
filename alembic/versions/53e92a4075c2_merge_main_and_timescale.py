"""merge_main_and_timescale

Revision ID: 53e92a4075c2
Revises: 0007, 0001_ts
Create Date: 2026-03-22 17:05:06.876805

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53e92a4075c2'
down_revision: Union[str, None] = ('0007', '0001_ts')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
