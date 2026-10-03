"""add org tier

Revision ID: 021_add_org_tier
Revises: 020_add_cloud_agent_fields
Create Date: 2025-11-20 21:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '021_add_org_tier'
down_revision = '020_cloud_agent'
branch_labels = None
depends_on = None


def upgrade():
    # Add tier column with default 'free'
    # Check if column exists first to be idempotent
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('organizations')]
    if 'tier' not in columns:
        op.add_column('organizations', sa.Column('tier', sa.String(length=20), nullable=False, server_default='free'))


def downgrade():
    op.drop_column('organizations', 'tier')
