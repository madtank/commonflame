"""add_agent_lookup_index_for_chirpy

Revision ID: f4a2c8e7d9b1
Revises: 11e1f5cb99c2
Create Date: 2025-10-20 23:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f4a2c8e7d9b1'
down_revision = '11e1f5cb99c2'
branch_labels = None
depends_on = None


def upgrade():
    """
    Add missing index for Chirpy agent lookups.

    Performance Issue: Chirpy lookups were doing full table scans
    Query: SELECT * FROM agents WHERE org_id = X AND name = 'chirpy' AND agent_type = 'cloud_gcp'

    Impact: Reduces lookup time from ~73 seconds to <100ms
    """
    # Concurrent index creation requires running outside transaction
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_org_name_type
            ON agents(org_id, name, agent_type)
        """)


def downgrade():
    """Remove the agent lookup index"""
    # Concurrent index drop requires running outside transaction
    with op.get_context().autocommit_block():
        op.execute("""
            DROP INDEX CONCURRENTLY IF EXISTS idx_agents_org_name_type
        """)
