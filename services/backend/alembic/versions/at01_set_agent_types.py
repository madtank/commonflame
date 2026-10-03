"""Set agent_type values for concierge routing SLA

Revision ID: at01_set_agent_types
Revises: asa01_agent_space_access
Create Date: 2026-03-13

Sets agent_type for existing agents:
- origin='space_agent' → 'sentinel' (always-on, monitoring)
- Everything else stays 'assistant' (on-demand, dispatched)
"""
from alembic import op
from sqlalchemy import text

revision: str = "at01_set_agent_types"
down_revision: str = "asa01_agent_space_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the overly restrictive constraint that forces agent_type = origin.
    # agent_type is an independent classification (sentinel, assistant, etc.)
    op.execute(text("""
        ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agent_type_matches_origin
    """))

    # Space agents (aX) are sentinel — always-on, fast response
    op.execute(text("""
        UPDATE agents
        SET agent_type = 'sentinel'
        WHERE origin = 'space_agent'
          AND (agent_type IS NULL OR agent_type != 'sentinel')
    """))

    # All other agents default to assistant — on-demand, dispatched
    op.execute(text("""
        UPDATE agents
        SET agent_type = 'assistant'
        WHERE agent_type IS NULL
    """))


def downgrade() -> None:
    # Reset agent_type to NULL (original state before this migration)
    op.execute(text("""
        UPDATE agents
        SET agent_type = NULL
        WHERE agent_type IN ('sentinel', 'assistant')
    """))

    # Restore the constraint
    op.execute(text("""
        ALTER TABLE agents ADD CONSTRAINT ck_agent_type_matches_origin
        CHECK (agent_type = origin)
    """))
