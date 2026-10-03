"""Merge migration heads: super_admin and chirpy_index

Revision ID: acf709d5ab30
Revises: 564fe0a0864a, f4a2c8e7d9b1
Create Date: 2025-11-05 11:46:22.351688

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'acf709d5ab30'
down_revision: Union[str, None] = ('564fe0a0864a', 'f4a2c8e7d9b1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
