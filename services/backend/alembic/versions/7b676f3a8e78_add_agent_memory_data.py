"""add_agent_memory_data

Revision ID: 7b676f3a8e78
Revises: 61ce494956a3
Create Date: 2025-11-28 17:25:54.962763

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b676f3a8e78'
down_revision: Union[str, None] = '61ce494956a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('memory_data', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('agents', 'memory_data')
