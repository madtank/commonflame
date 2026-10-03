"""Add GIN indexes for full-text search performance

Revision ID: b2c3d4e5f6g7
Revises: fix1m2c3p4o5r6
Create Date: 2026-02-08

Adds GIN indexes on messages, tasks, and agents tables
to speed up full-text search from ~3-5s to sub-second.
Companion to ax-mcp-server PR #95 (search parallelization).

Note: Uses autocommit_block() for CONCURRENTLY support,
following the pattern from f4a2c8e7d9b1 and 39f0ca8875a4.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b2c3d4e5f6g7'
down_revision: str = 'fix1m2c3p4o5r6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY requires running outside a transaction.
    # autocommit_block() temporarily exits the transaction context.
    with op.get_context().autocommit_block():
        # Expression-based GIN indexes for to_tsvector queries
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_content_gin
                ON messages USING GIN (to_tsvector('english', content));
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tasks_title_desc_gin
                ON tasks USING GIN (to_tsvector('english', COALESCE(title, '') || ' ' || COALESCE(description, '')));
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_search_gin
                ON agents USING GIN (to_tsvector('english', COALESCE(name, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(bio, '') || ' ' || COALESCE(specialization, '')));
        """)

        # Partial indexes on search_vector columns (faster when populated)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_search_vector_gin
                ON messages USING GIN (search_vector) WHERE search_vector IS NOT NULL;
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tasks_search_vector_gin
                ON tasks USING GIN (search_vector) WHERE search_vector IS NOT NULL;
        """)
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_search_vector_gin
                ON agents USING GIN (search_vector) WHERE search_vector IS NOT NULL;
        """)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_agents_search_vector_gin;")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_tasks_search_vector_gin;")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_search_vector_gin;")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_agents_search_gin;")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_tasks_title_desc_gin;")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_content_gin;")
