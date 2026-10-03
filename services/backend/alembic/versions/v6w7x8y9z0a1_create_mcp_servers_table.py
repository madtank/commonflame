"""create mcp_servers table

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-01-22

Tool Registry for the Double-Key Protocol. Each MCP server has:
- min_user_tier: Required user tier (free, plus, admin). Note: plus includes admins.
- required_runner_tag: Which runner track can use this tool (stable, canary, experimental)
"""
from alembic import op
import sqlalchemy as sa

revision = 'v6w7x8y9z0a1'
down_revision = 'u5v6w7x8y9z0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mcp_servers',
        sa.Column('key', sa.String(50), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(50), nullable=False, server_default='general'),
        sa.Column('min_user_tier', sa.String(20), nullable=False, server_default='free'),
        sa.Column('required_runner_tag', sa.String(20), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index('idx_mcp_servers_category', 'mcp_servers', ['category'])
    op.create_index('idx_mcp_servers_tier', 'mcp_servers', ['min_user_tier'])
    op.create_index('idx_mcp_servers_active', 'mcp_servers', ['is_active'])

    op.execute("""
        INSERT INTO mcp_servers (key, name, description, category, min_user_tier, required_runner_tag)
        VALUES
        ('ax_mcp', 'aX Platform', 'Core aX platform tools: messages, tasks, context, agents, spaces',
         'platform', 'free', NULL),

        ('brave_search', 'Brave Search', 'Web search via Brave Search API',
         'search', 'plus', NULL),

        ('web_fetch', 'Web Fetch', 'Fetch and parse web pages',
         'web', 'free', NULL),

        ('image_gen', 'Image Generation', 'Generate images via AI models',
         'creative', 'plus', 'stable')
        ON CONFLICT (key) DO NOTHING;
    """)


def downgrade():
    op.drop_index('idx_mcp_servers_active', table_name='mcp_servers')
    op.drop_index('idx_mcp_servers_tier', table_name='mcp_servers')
    op.drop_index('idx_mcp_servers_category', table_name='mcp_servers')
    op.drop_table('mcp_servers')
