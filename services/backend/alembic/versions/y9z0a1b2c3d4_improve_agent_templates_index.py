"""improve agent_templates index for query performance

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-01-23

"""
from alembic import op

revision = 'y9z0a1b2c3d4'
down_revision = 'x8y9z0a1b2c3'
branch_labels = None
depends_on = None


def upgrade():
    # Drop old index and create improved one that matches the API query:
    # WHERE is_active = true AND is_admin_only = false ORDER BY is_top_level, display_order
    op.drop_index('idx_agent_templates_display', 'agent_templates')
    op.create_index(
        'idx_agent_templates_display',
        'agent_templates',
        ['is_active', 'is_admin_only', 'is_top_level', 'display_order']
    )


def downgrade():
    op.drop_index('idx_agent_templates_display', 'agent_templates')
    op.create_index(
        'idx_agent_templates_display',
        'agent_templates',
        ['is_active', 'is_top_level', 'display_order']
    )
