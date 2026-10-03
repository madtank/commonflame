"""remove_global_agent_name_unique_add_composite

Revision ID: e9b474bd8dc1
Revises: aedb16ebf773
Create Date: 2025-10-02 11:30:16.123456

Changes:
1. Remove global unique constraint on agent.name
2. Add composite unique constraint on (user_id, name)
3. This allows each user to have their own "chirpy-username" agent

This enables premium system agents like:
- chirpy-madtank (user madtank's Chirpy)
- chirpy-alice (user alice's Chirpy)
- night-owl-madtank (future research agent)

The UI will display as just "Chirpy" with a premium badge.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9b474bd8dc1'
down_revision: Union[str, None] = 'aedb16ebf773'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the global unique constraint on agent names
    op.drop_constraint('agents_name_unique', 'agents', type_='unique')

    # Add composite unique constraint on (user_id, name)
    # This allows multiple users to have agents with the same name
    # but each user can only have one agent with a specific name
    op.create_unique_constraint(
        'agents_user_id_name_unique',
        'agents',
        ['user_id', 'name']
    )


def downgrade() -> None:
    # Remove composite unique constraint
    op.drop_constraint('agents_user_id_name_unique', 'agents', type_='unique')

    # Restore global unique constraint
    # NOTE: This might fail if there are duplicate names across users
    op.create_unique_constraint(
        'agents_name_unique',
        'agents',
        ['name']
    )
