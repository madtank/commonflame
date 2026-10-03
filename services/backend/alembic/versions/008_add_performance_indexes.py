"""Add performance indexes for beta launch

Revision ID: 008_add_performance_indexes
Revises: 007_add_task_notes
Create Date: 2025-07-28 15:52:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '008_add_performance_indexes'
down_revision = '007_add_task_notes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add critical performance indexes for beta launch"""

    # Messages table indexes for timeline queries
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_org_created
        ON messages(org_id, created_at DESC)
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_user_created
        ON messages(user_id, created_at DESC)
    """)

    # Tasks table indexes for filtering and sorting
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tasks_org_status_created
        ON tasks(org_id, status, created_at DESC)
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tasks_assigned_to
        ON tasks(assigned_to, status, created_at DESC)
    """)

    # Organization memberships for fast lookups
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_memberships_user_org
        ON organization_memberships(user_id, org_id)
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_org_memberships_org_user
        ON organization_memberships(org_id, user_id)
    """)

    # Agents table for org and user queries
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_org_user
        ON agents(org_id, user_id)
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agents_user_created
        ON agents(user_id, created_at DESC)
    """)

    # Refresh tokens for authentication performance
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_refresh_tokens_hash_expires
        ON refresh_tokens(token_hash, expires_at)
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_refresh_tokens_user_expires
        ON refresh_tokens(user_id, expires_at DESC)
    """)

    # Users table for authentication and admin queries
    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_username_active
        ON users(username, active)
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_email_active
        ON users(email, active)
    """)

    op.execute("""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_org_role
        ON users(org_id, role)
    """)

    # Guardrail violations for admin monitoring (only if table exists)
    connection = op.get_bind()
    result = connection.execute(sa.text("""
        SELECT table_name FROM information_schema.tables
        WHERE table_name = 'guardrail_violations'
    """))

    if result.fetchone():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_guardrail_violations_org_created
            ON guardrail_violations(org_id, created_at DESC)
        """)

        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_guardrail_violations_user_resolved
            ON guardrail_violations(user_id, resolved, created_at DESC)
        """)


def downgrade() -> None:
    """Remove performance indexes"""

    # Drop all indexes in reverse order
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_guardrail_violations_user_resolved")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_guardrail_violations_org_created")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_users_org_role")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_users_email_active")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_users_username_active")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_refresh_tokens_user_expires")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_refresh_tokens_hash_expires")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_agents_user_created")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_agents_org_user")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_org_memberships_org_user")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_org_memberships_user_org")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_tasks_assigned_to")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_tasks_org_status_created")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_user_created")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_org_created")
