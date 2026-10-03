"""add_global_unique_constraint_to_agent_names

Revision ID: 5d370ae6fbb2
Revises: 016_github_sso
Create Date: 2025-08-07 21:18:58.441712

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d370ae6fbb2'
down_revision: Union[str, None] = '016_github_sso'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add unique constraint on agent names globally
    # This ensures agent names are unique across all users (like email addresses)
    op.create_unique_constraint(
        'agents_name_unique',
        'agents',
        ['name']
    )

    # Also create a case-insensitive unique index for better UX
    op.create_index(
        'idx_agents_name_lower',
        'agents',
        [sa.text('LOWER(name)')],
        unique=True
    )


def downgrade() -> None:
    # Remove the unique constraint and index
    op.drop_constraint('agents_name_unique', 'agents', type_='unique')
    op.drop_index('idx_agents_name_lower', 'agents')
