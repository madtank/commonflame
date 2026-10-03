"""Enable Row-Level Security on core tables

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-01-07 04:45:00.000000

SECURITY: This migration enables RLS to prevent cross-organization data leakage.
All queries to tasks, messages, and agents will be automatically filtered by org_id.

The application MUST call set_rls_context() at the start of each request to set
the PostgreSQL session variable 'app.current_org_id'.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n8o9p0q1r2s3'
down_revision: Union[str, None] = 'm7n8o9p0q1r2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS and create org_id-based policies on core tables.

    Policy logic:
    - Rows are only visible if org_id matches current_setting('app.current_org_id')
    - This prevents any cross-organization data access at the database level
    - Even if application code has bugs, the database will not return wrong-org data

    IMPORTANT: The application must set 'app.current_org_id' on each connection.
    Without it, queries will return no rows (safe default - fail closed).
    """

    # -----------------------------------------------------------------
    # TASKS TABLE
    # -----------------------------------------------------------------
    # Enable row-level security on tasks
    op.execute("ALTER TABLE tasks ENABLE ROW LEVEL SECURITY")

    # Force RLS for table owner too (prevents bypass even for superuser in some contexts)
    op.execute("ALTER TABLE tasks FORCE ROW LEVEL SECURITY")

    # Create policy: only see rows where org_id matches session variable
    # Using COALESCE to handle case where org_id is NULL (legacy data)
    op.execute("""
        CREATE POLICY tasks_org_isolation ON tasks
        FOR ALL
        USING (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
        WITH CHECK (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
    """)

    # -----------------------------------------------------------------
    # MESSAGES TABLE
    # -----------------------------------------------------------------
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY messages_org_isolation ON messages
        FOR ALL
        USING (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
        WITH CHECK (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
    """)

    # -----------------------------------------------------------------
    # AGENTS TABLE
    # -----------------------------------------------------------------
    op.execute("ALTER TABLE agents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agents FORCE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY agents_org_isolation ON agents
        FOR ALL
        USING (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
        WITH CHECK (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
    """)

    # -----------------------------------------------------------------
    # Index optimization for RLS filter performance
    # -----------------------------------------------------------------
    # Ensure org_id indexes exist (may already exist, using IF NOT EXISTS)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_org_id
        ON tasks (org_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_org_id
        ON messages (org_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_agents_org_id
        ON agents (org_id)
    """)


def downgrade() -> None:
    """Remove RLS policies and disable RLS.

    WARNING: Downgrading removes database-level security. Only do this
    if you have a compelling reason and understand the security implications.
    """
    # Drop indexes
    op.execute("DROP INDEX IF EXISTS idx_agents_org_id")
    op.execute("DROP INDEX IF EXISTS idx_messages_org_id")
    op.execute("DROP INDEX IF EXISTS idx_tasks_org_id")

    # Drop policies
    op.execute("DROP POLICY IF EXISTS agents_org_isolation ON agents")
    op.execute("DROP POLICY IF EXISTS messages_org_isolation ON messages")
    op.execute("DROP POLICY IF EXISTS tasks_org_isolation ON tasks")

    # Disable RLS
    op.execute("ALTER TABLE agents DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tasks DISABLE ROW LEVEL SECURITY")
