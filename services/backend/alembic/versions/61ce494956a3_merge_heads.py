"""merge heads

Revision ID: 61ce494956a3
Revises: 16ea4db85020, ba9de745108e
Create Date: 2025-11-28 17:25:50.829150

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61ce494956a3'
down_revision: Union[str, None] = ('16ea4db85020', 'ba9de745108e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
