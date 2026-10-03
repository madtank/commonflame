"""add ax_mcp_enabled column to agents

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-01-03

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'k5l6m7n8o9p0'
down_revision = 'j4k5l6m7n8o9'
branch_labels = None
depends_on = None


def upgrade():
    # Add ax_mcp_enabled column - controls access to aX Platform MCP tools
    # Default True for backward compatibility (existing agents keep MCP access)
    op.add_column('agents', sa.Column('ax_mcp_enabled', sa.Boolean(), nullable=False, server_default='true'))


def downgrade():
    op.drop_column('agents', 'ax_mcp_enabled')
