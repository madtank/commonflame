"""Add CHECK constraint: agent_type must equal origin.

Revision ID: origins02
Revises: origins01
Create Date: 2026-03-10

After data normalization, lock it down so agent_type can never drift from origin again.
Also add CHECK constraint on origin to only allow valid values.
"""
from alembic import op
import sqlalchemy as sa

revision = 'origins02'
down_revision = 'origins01'
branch_labels = None
depends_on = None


def upgrade():
    # Constraint 1: agent_type must equal origin
    op.execute(sa.text("""
        ALTER TABLE agents
        ADD CONSTRAINT ck_agent_type_matches_origin
        CHECK (agent_type = origin)
    """))

    # Constraint 2: origin must be one of the valid values
    op.execute(sa.text("""
        ALTER TABLE agents
        ADD CONSTRAINT ck_valid_origin
        CHECK (origin IN ('space_agent', 'cloud', 'external_gateway', 'agentcore', 'mcp'))
    """))


def downgrade():
    op.execute(sa.text("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agent_type_matches_origin"))
    op.execute(sa.text("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_valid_origin"))
