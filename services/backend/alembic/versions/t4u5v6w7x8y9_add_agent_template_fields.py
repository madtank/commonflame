"""add agent template fields

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-01-14

Adds image_gen_enabled toggle and template_type for agent templates.
"""
from alembic import op
import sqlalchemy as sa

revision = 't4u5v6w7x8y9'
down_revision = 's3t4u5v6w7x8'
branch_labels = None
depends_on = None


def upgrade():
    # Add image_gen_enabled - defaults to True for existing agents
    op.add_column(
        'agents',
        sa.Column('image_gen_enabled', sa.Boolean(), nullable=False, server_default='true')
    )

    # Add template_type - identifies which template this agent uses
    # Defaults to 'ax_agent' for existing agents
    op.add_column(
        'agents',
        sa.Column('template_type', sa.String(50), nullable=False, server_default='ax_agent')
    )

    # Add index for template_type queries (filtering by template)
    op.create_index('idx_agents_template_type', 'agents', ['template_type'])


def downgrade():
    op.drop_index('idx_agents_template_type', table_name='agents')
    op.drop_column('agents', 'template_type')
    op.drop_column('agents', 'image_gen_enabled')
