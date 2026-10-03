"""add messages agent_id created_at index for warm start optimization

Revision ID: r2s3t4u5v6w7
Revises: z9y8x7w6v5u4
Create Date: 2026-01-09

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'r2s3t4u5v6w7'
down_revision = 'z9y8x7w6v5u4'
branch_labels = None
depends_on = None


def upgrade():
    # Add index to optimize correlated subqueries in MCP warm start
    # These queries do: SELECT MAX(m.created_at) FROM messages m WHERE m.agent_id = a.id
    # Without index: table scan per agent (~500ms+ per query)
    # With index: instant lookup
    #
    # Affected queries:
    # - Agent identity lookup
    # - Teammates query
    # - Collaborators query
    #
    # Using CONCURRENTLY to avoid locking the table during creation
    # Must use autocommit block since CREATE INDEX CONCURRENTLY cannot run in a transaction
    with op.get_context().autocommit_block():
        op.execute(sa.text("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_agent_id_created_at
            ON messages(agent_id, created_at DESC)
        """))


def downgrade():
    with op.get_context().autocommit_block():
        op.execute(sa.text("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_agent_id_created_at"))
