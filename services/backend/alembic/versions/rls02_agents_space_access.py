"""Extend agents RLS policy to check agent_space_access membership.

Revision ID: rls02_agents_space_access
Revises: attrls01_attachments_rls
Create Date: 2026-04-11

The original agents_space_isolation policy only checked agents.space_id
(the legacy home-space column). This missed agents operating in spaces
via agent_space_access — they were invisible at the RLS level even though
the application layer granted them access.

This migration replaces the policy to also allow agents visible through
active agent_space_access rows, fixing the "ghost agent" bug where
agents appeared in the roster but were blocked by RLS for routing/dispatch.

The legacy agents.space_id check is kept for backwards compatibility
during the migration period.
"""

from alembic import op

revision = "rls02_agents_space_access"
down_revision = ("attrls01_attachments_rls", "tc04_message_id_idx")
branch_labels = None
depends_on = None

_PRIV = "current_setting('app.is_privileged', true) = 'true'"
_SPACE = "space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid"
_SPACE_ACCESS = """
    id IN (
        SELECT agent_id FROM agent_space_access
        WHERE space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
        AND state = 'active'
    )
"""


def upgrade():
    # Drop old policy
    op.execute("DROP POLICY IF EXISTS agents_space_isolation ON agents")

    # Create new policy that checks BOTH legacy space_id AND agent_space_access
    op.execute(f"""
        CREATE POLICY agents_space_isolation ON agents
        FOR ALL
        USING ({_PRIV} OR {_SPACE} OR {_SPACE_ACCESS})
        WITH CHECK ({_PRIV} OR {_SPACE} OR {_SPACE_ACCESS})
    """)


def downgrade():
    # Revert to legacy-only policy
    op.execute("DROP POLICY IF EXISTS agents_space_isolation ON agents")
    op.execute(f"""
        CREATE POLICY agents_space_isolation ON agents
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)
