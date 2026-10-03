"""fix mcp agent origin field

Revision ID: fix1m2c3p4o5r6
Revises: wh1b2k3d4sp5
Create Date: 2026-02-01

Bug fix: MCP agents (created via UI or OAuth auto-registration) were
incorrectly getting origin='cloud' (the column default) instead of
origin='mcp'. This prevented them from switching workspaces.

This migration backfills the correct origin for agents that:
- Have origin='cloud' (incorrect default)
- Have no cloud_function_url (not actually cloud agents)
- Are not webhook agents (webhook_url IS NULL)

The root cause was fixed in:
- agents.py register_agent: Set origin based on enable_cloud_agent
- oauth_shim.py: Explicitly set origin='mcp' for auto-registered MCP clients
"""
from alembic import op


revision = 'fix1m2c3p4o5r6'
down_revision = 'wh1b2k3d4sp5'
branch_labels = None
depends_on = None


def upgrade():
    # Fix MCP agents that were incorrectly stored with origin='cloud'
    # Criteria:
    # - origin='cloud': Currently has the wrong value
    # - cloud_function_url IS NULL: Not actually a cloud agent
    # - webhook_url IS NULL: Not an external gateway agent
    # These are MCP agents that should be able to switch workspaces
    op.execute("""
        UPDATE agents
        SET origin = 'mcp'
        WHERE origin = 'cloud'
          AND cloud_function_url IS NULL
          AND (webhook_url IS NULL OR webhook_url = '')
    """)


def downgrade():
    # Reverting would re-introduce the bug, but for completeness:
    # Note: This is lossy - we can't distinguish which agents were
    # originally created as MCP vs incorrectly defaulted to cloud
    op.execute("""
        UPDATE agents
        SET origin = 'cloud'
        WHERE origin = 'mcp'
          AND cloud_function_url IS NULL
          AND (webhook_url IS NULL OR webhook_url = '')
    """)
