"""Normalize agent_type to match origin (AGENTS-001).

Revision ID: origins01
Revises: ax02_deactivate
Create Date: 2026-03-10

agent_type was freeform (sentinel, general, assistant, guide, cloud_gcp, mcp_client, external).
Per AGENTS-001 spec, agent_type must always equal origin.
This migration normalizes all existing data.
"""
from alembic import op
import sqlalchemy as sa

revision = 'origins01'
down_revision = 'ax02_deactivate'
branch_labels = None
depends_on = None


def upgrade():
    # Phase 0: Normalize the last legacy origin label.
    # Historical "api" agents were created through backend routes before
    # origin became canonical. Preserve dispatchable rows as cloud agents,
    # webhook rows as external_gateway, and treat URL-less API agents as mcp.
    op.execute(sa.text("""
        UPDATE agents
        SET origin = CASE
            WHEN webhook_url IS NOT NULL AND webhook_url != '' THEN 'external_gateway'
            WHEN cloud_function_url IS NOT NULL AND cloud_function_url != '' THEN 'cloud'
            ELSE 'mcp'
        END,
            updated_at = now()
        WHERE origin = 'api'
    """))

    # Phase 1: Normalize agent_type to match origin for all agents
    op.execute(sa.text("""
        UPDATE agents
        SET agent_type = origin,
            updated_at = now()
        WHERE agent_type != origin
          AND origin IS NOT NULL
    """))

    # Phase 2: Fix moltbot — has BOTH cloud_function_url AND webhook_url
    # External gateway agents should only use webhook_url
    op.execute(sa.text("""
        UPDATE agents
        SET cloud_function_url = NULL,
            updated_at = now()
        WHERE origin = 'external_gateway'
          AND cloud_function_url IS NOT NULL
    """))

    # Phase 3: Deactivate zero-message MCP agents with no dispatch capability (dead weight)
    op.execute(sa.text("""
        UPDATE agents
        SET status = 'inactive',
            updated_at = now()
        WHERE origin = 'mcp'
          AND cloud_function_url IS NULL
          AND webhook_url IS NULL
          AND is_internal = false
          AND id NOT IN (SELECT DISTINCT agent_id FROM messages WHERE agent_id IS NOT NULL)
          AND status = 'active'
    """))


def downgrade():
    # Can't perfectly restore freeform agent_type values
    op.execute(sa.text("""
        UPDATE agents
        SET agent_type = 'general'
        WHERE agent_type = origin
          AND origin IN ('cloud', 'mcp', 'external_gateway', 'agentcore')
    """))
