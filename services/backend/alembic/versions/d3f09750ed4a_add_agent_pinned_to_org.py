"""add_agent_pinned_to_org

Revision ID: d3f09750ed4a
Revises: 5d370ae6fbb2
Create Date: 2025-08-13 23:30:38.853423

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd3f09750ed4a'
down_revision: Union[str, None] = '5d370ae6fbb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add pinned_to_org column to agents table
    op.add_column('agents', sa.Column('pinned_to_org', postgresql.UUID(as_uuid=True), nullable=True))

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_agents_pinned_to_org',
        'agents', 'organizations',
        ['pinned_to_org'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Drop foreign key constraint
    op.drop_constraint('fk_agents_pinned_to_org', 'agents', type_='foreignkey')

    # Drop the column
    op.drop_column('agents', 'pinned_to_org')
