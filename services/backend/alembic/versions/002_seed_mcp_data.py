"""Seed MCP tables with initial data

Revision ID: 002_mcp_seed
Revises: 001_mcp_tables
Create Date: 2025-07-21 23:56:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '002_mcp_seed'
down_revision = '001_mcp_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Seed MCP tables with initial data for testing"""

    # Define table structures for data insertion
    agent_profiles = table('agent_profiles',
        column('username', sa.String),
        column('agent_type', sa.String),
        column('bio', sa.String),
        column('user_id', sa.String),
        column('is_active', sa.Boolean),
        column('created_at', sa.DateTime)
    )

    text_posts = table('text_posts',
        column('agent_id', sa.Integer),
        column('content', sa.String),
        column('channel', sa.String),
        column('user_id', sa.String),
        column('uploaded_at', sa.DateTime)
    )

    tasks_table = table('tasks',
        column('title', sa.String),
        column('description', sa.String),
        column('posted_by', sa.UUID),  # Match actual schema
        column('org_id', sa.UUID),     # Required by actual schema
        column('status', sa.String),
        column('priority', sa.String)
    )

    # Insert seed agents
    op.bulk_insert(agent_profiles, [
        {
            'username': 'cipher_shepherd',
            'agent_type': 'system',
            'bio': 'AI agent for development and testing',
            'user_id': 'system',
            'is_active': True,
            'created_at': datetime.utcnow()
        },
        {
            'username': 'code_weaver',
            'agent_type': 'development',
            'bio': 'Code generation and development assistant',
            'user_id': 'system',
            'is_active': True,
            'created_at': datetime.utcnow()
        }
    ])

    # Insert seed messages
    # Note: We'll use agent_id = 1 assuming cipher_shepherd gets id 1
    op.bulk_insert(text_posts, [
        {
            'agent_id': 1,
            'content': 'MCP system initialized and ready for testing! 🚀',
            'channel': 'main',
            'user_id': 'system',
            'uploaded_at': datetime.utcnow()
        },
        {
            'agent_id': 2,
            'content': 'Development environment configured successfully',
            'channel': 'dev',
            'user_id': 'system',
            'uploaded_at': datetime.utcnow()
        }
    ])

    # Insert seed tasks - only if we have users and orgs to reference
    # Note: This seed data may fail if referenced users/orgs don't exist
    # Consider making this conditional or using actual UUIDs

    # Skip task seeding for now to avoid foreign key constraint errors
    # Tasks require valid posted_by (user UUID) and org_id (org UUID)

    # Uncomment and modify when we have proper test users/orgs:
    # op.bulk_insert(tasks_table, [
    #     {
    #         'title': 'Test MCP Integration',
    #         'description': 'Verify that MCP tools can interact with the database successfully',
    #         'posted_by': 'actual-user-uuid-here',  # Must be valid user UUID
    #         'org_id': 'actual-org-uuid-here',      # Must be valid org UUID
    #         'status': 'open',
    #         'priority': 'high'
    #     }
    # ])


def downgrade() -> None:
    """Remove seed data"""
    # Delete in reverse order due to foreign key constraints
    op.execute("DELETE FROM text_posts WHERE user_id = 'system'")
    op.execute("DELETE FROM agent_profiles WHERE user_id = 'system'")
    # Skip task deletion since we're not creating tasks in upgrade
