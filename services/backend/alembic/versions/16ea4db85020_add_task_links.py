"""add_task_links

Revision ID: 16ea4db85020
Revises: 70eb6b63f37a
Create Date: 2025-11-26 21:13:06.214537

Add JSONB links array to tasks table for storing artifact URLs (GitHub PRs, branches, deployment URLs)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '16ea4db85020'
down_revision: Union[str, None] = '70eb6b63f37a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add links column as JSONB array (default empty array for backward compatibility)
    op.add_column('tasks',
        sa.Column('links',
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True,
                  server_default='[]',
                  comment='Array of artifact links (GitHub PRs, branches, deployment URLs)'))

    # Create GIN index for efficient JSONB operations
    op.create_index('idx_tasks_links_gin', 'tasks', ['links'],
                    postgresql_using='gin')


def downgrade() -> None:
    # Drop index first, then column
    op.drop_index('idx_tasks_links_gin', 'tasks')
    op.drop_column('tasks', 'links')
