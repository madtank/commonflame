"""add_space_awareness_indexes_and_tracking

Revision ID: 951dc48445c5
Revises: d3f09750ed4a
Create Date: 2025-08-16 01:31:45.520827

Adds indexes and tracking columns for space-aware agent behavior.
Ensures optimal performance for multi-organization queries and
tracks agent space switching for debugging.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '951dc48445c5'
down_revision: Union[str, None] = 'd3f09750ed4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add tracking columns for better observability
    op.add_column('agents',
        sa.Column('last_space_switch_at', sa.DateTime(timezone=True), nullable=True,
                  comment='Timestamp of last organization context switch'))

    # Optional: Add default public org for users (future feature)
    op.add_column('users',
        sa.Column('default_public_org_id', postgresql.UUID(as_uuid=True), nullable=True,
                  comment='Default organization for public/community interaction'))

    # Add foreign key for default_public_org_id
    op.create_foreign_key(
        'fk_users_default_public_org',
        'users', 'organizations',
        ['default_public_org_id'], ['id'],
        ondelete='SET NULL'
    )

    # Create indexes for space-aware queries

    # Messages - most common query pattern
    op.create_index(
        'idx_messages_org_created',
        'messages',
        ['org_id', 'created_at'],
        postgresql_using='btree'
    )

    # Tasks - filtered by org and status
    op.create_index(
        'idx_tasks_org_status_created',
        'tasks',
        ['org_id', 'work_status', 'created_at'],
        postgresql_using='btree'
    )

    # Message read status - unique constraint and lookup index
    op.create_index(
        'idx_message_read_status_unique',
        'message_read_status',
        ['message_id', 'agent_id'],
        unique=True,
        postgresql_using='btree'
    )

    op.create_index(
        'idx_message_read_status_agent_read',
        'message_read_status',
        ['agent_id', 'read_at'],
        postgresql_using='btree'
    )

    # Agents - lookups by user and pinned org
    op.create_index(
        'idx_agents_user',
        'agents',
        ['user_id'],
        postgresql_using='btree'
    )

    op.create_index(
        'idx_agents_pinned_org',
        'agents',
        ['pinned_to_org'],
        postgresql_where=sa.text('pinned_to_org IS NOT NULL'),
        postgresql_using='btree'
    )

    op.create_index(
        'idx_agents_org',
        'agents',
        ['org_id'],
        postgresql_using='btree'
    )

    # Organizations - slug lookup (should be unique)
    op.create_index(
        'idx_organizations_slug',
        'organizations',
        ['slug'],
        unique=True,
        postgresql_using='btree'
    )

    # Organization memberships - ensure uniqueness
    op.create_index(
        'idx_org_memberships_unique',
        'organization_memberships',
        ['org_id', 'user_id'],
        unique=True,
        postgresql_using='btree'
    )

    # Add CHECK constraint to ensure pinned agents stay in their org
    # This enforces at DB level that pinned agents cannot have their org_id changed
    op.execute("""
        ALTER TABLE agents
        ADD CONSTRAINT check_pinned_org_immutable
        CHECK (pinned_to_org IS NULL OR org_id = pinned_to_org)
    """)

    # Add comment to clarify the resolution order
    op.execute("""
        COMMENT ON COLUMN agents.org_id IS
        'Current organization context (sticky). Resolution: pinned_to_org > target_space > org_id > user.current_org_id > user.org_id'
    """)

    op.execute("""
        COMMENT ON COLUMN agents.pinned_to_org IS
        'If set, agent is locked to this organization and cannot switch spaces'
    """)


def downgrade() -> None:
    # Drop CHECK constraint
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS check_pinned_org_immutable")

    # Drop all indexes
    op.drop_index('idx_org_memberships_unique', 'organization_memberships')
    op.drop_index('idx_organizations_slug', 'organizations')
    op.drop_index('idx_agents_org', 'agents')
    op.drop_index('idx_agents_pinned_org', 'agents')
    op.drop_index('idx_agents_user', 'agents')
    op.drop_index('idx_message_read_status_agent_read', 'message_read_status')
    op.drop_index('idx_message_read_status_unique', 'message_read_status')
    op.drop_index('idx_tasks_org_status_created', 'tasks')
    op.drop_index('idx_messages_org_created', 'messages')

    # Drop foreign key and columns
    op.drop_constraint('fk_users_default_public_org', 'users', type_='foreignkey')
    op.drop_column('users', 'default_public_org_id')
    op.drop_column('agents', 'last_space_switch_at')
