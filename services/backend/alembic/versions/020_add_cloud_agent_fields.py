"""add_cloud_agent_fields

Revision ID: 020_cloud_agent
Revises: f4a2c8e7d9b1
Create Date: 2025-11-20 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '020_cloud_agent'
down_revision = '3f77e5a32f22'
branch_labels = None
depends_on = None


def upgrade():
    """Add system_prompt and cloud_function_url to agents table"""
    op.add_column('agents', sa.Column('system_prompt', sa.Text(), nullable=True))
    op.add_column('agents', sa.Column('cloud_function_url', sa.String(length=512), nullable=True))


def downgrade():
    """Remove system_prompt and cloud_function_url from agents table"""
    op.drop_column('agents', 'cloud_function_url')
    op.drop_column('agents', 'system_prompt')
