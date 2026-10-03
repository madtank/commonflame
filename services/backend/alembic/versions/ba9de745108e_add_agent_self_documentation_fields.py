"""add_agent_self_documentation_fields

Revision ID: ba9de745108e
Revises: 70eb6b63f37a
Create Date: 2025-11-27 21:27:59.689479

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba9de745108e'
down_revision: Union[str, None] = '70eb6b63f37a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add agent self-documentation fields
    # These fields are agent-owned and editable via MCP tools (whoami/update_self)
    # Users can view but not edit these fields

    # bio: Short agent biography (agent-maintained, user-visible)
    bind = op.get_bind()
    from sqlalchemy import inspect
    inspector = inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('agents')]

    if 'bio' not in columns:
        op.add_column('agents', sa.Column('bio', sa.Text(), nullable=True))

    # specialization: What the agent specializes in (tags/keywords, agent-maintained)
    if 'specialization' not in columns:
        op.add_column('agents', sa.Column('specialization', sa.Text(), nullable=True))

    # Note: capabilities (JSON) and system_prompt (Text) already exist
    # Agents can update these via update_self action along with bio/specialization


def downgrade() -> None:
    # Remove agent self-documentation fields
    op.drop_column('agents', 'specialization')
    op.drop_column('agents', 'bio')
