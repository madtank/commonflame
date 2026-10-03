"""add enabled_tools JSONB column

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-01-22

Consolidates individual tool toggles into a single JSONB column.
This eliminates the need to add new columns/migrations for each new tool.

Migration strategy:
1. Add enabled_tools JSONB column with default {}
2. Populate from existing boolean columns
3. Keep boolean columns for backwards compatibility (deprecated)

Future tools just need to be added to the toggle registry - no new migrations.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'x8y9z0a1b2c3'
down_revision = 'w7x8y9z0a1b2'
branch_labels = None
depends_on = None


def upgrade():
    # Add enabled_tools JSONB column
    op.add_column(
        'agents',
        sa.Column('enabled_tools', JSONB, nullable=False, server_default='{}')
    )

    # Migrate existing boolean columns into JSONB
    # This preserves all current toggle states
    op.execute("""
        UPDATE agents
        SET enabled_tools = jsonb_build_object(
            'ax_mcp', COALESCE(ax_mcp_enabled, true),
            'web_fetch', COALESCE(web_fetch_enabled, false),
            'brave_search', COALESCE(brave_search_enabled, false),
            'image_gen', COALESCE(image_gen_enabled, false)
        )
    """)

    # Create index for JSONB queries (e.g., find all agents with web_fetch enabled)
    op.create_index(
        'idx_agents_enabled_tools',
        'agents',
        ['enabled_tools'],
        postgresql_using='gin'
    )


def downgrade():
    op.drop_index('idx_agents_enabled_tools', table_name='agents')
    op.drop_column('agents', 'enabled_tools')
