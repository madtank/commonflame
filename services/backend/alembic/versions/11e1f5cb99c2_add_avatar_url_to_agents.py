"""add_avatar_url_to_agents

Revision ID: 11e1f5cb99c2
Revises: e9b474bd8dc1
Create Date: 2025-10-03 12:22:15.564219

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '11e1f5cb99c2'
down_revision: Union[str, None] = 'e9b474bd8dc1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add avatar_url column to agents table
    op.add_column('agents', sa.Column('avatar_url', sa.String(length=512), nullable=True))

    # Set Chirpy's avatar for existing chirpy agents
    op.execute("""
        UPDATE agents
        SET avatar_url = '/images/chirpy_logo.png'
        WHERE name = 'chirpy' AND agent_type = 'cloud_gcp'
    """)


def downgrade() -> None:
    # Remove avatar_url column
    op.drop_column('agents', 'avatar_url')
