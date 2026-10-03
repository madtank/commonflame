"""add user_sessions table for session recovery

Revision ID: 035_add_user_sessions
Revises: 020_oauth_clients
Create Date: 2025-11-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '035_add_user_sessions'
down_revision = '020_oauth_clients'
branch_labels = None
depends_on = None


def upgrade():
    """
    Create user_sessions table for session recovery across Cloud Run deployments.

    This table provides database-backed session storage that survives:
    - Cloud Run service restarts
    - Redis connection resets
    - Deployment-triggered Redis flushes

    When Redis is unavailable, sessions can be recovered from this table.
    """
    op.create_table(
        'user_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', sa.String(255), nullable=False, unique=True),
        sa.Column('access_token_hash', sa.String(64), nullable=False),  # SHA-256 hash
        sa.Column('session_data', postgresql.JSONB, nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )

    op.create_index(
        'idx_user_sessions_user_id',
        'user_sessions',
        ['user_id']
    )

    # Index for cleanup queries
    op.create_index(
        'idx_user_sessions_expires_at',
        'user_sessions',
        ['expires_at']
    )

    # Comment on table
    op.execute("""
        COMMENT ON TABLE user_sessions IS
        'Database-backed session storage for recovery across Cloud Run deployments. '
        'Provides fallback when Redis is unavailable due to connection resets or service restarts.'
    """)


def downgrade():
    """
    Drop user_sessions table.
    """
    op.drop_index('idx_user_sessions_expires_at', table_name='user_sessions')
    op.drop_index('idx_user_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')
