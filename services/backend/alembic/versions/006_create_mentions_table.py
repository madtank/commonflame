"""create mentions table

Revision ID: 006_create_mentions
Revises: 3f77e5a32f22
Create Date: 2025-11-22 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '006_create_mentions'
down_revision = '3f77e5a32f22'
branch_labels = None
depends_on = None


def upgrade():
    """Create mentions table for MCP push notifications"""
    op.create_table(
        'mentions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('message_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mentioned_agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mentioning_agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Create indexes for performance
    op.create_index(
        'idx_mentions_agent_unread',
        'mentions',
        ['mentioned_agent_id', 'read_at']
    )

    op.create_index(
        'idx_mentions_agent_created',
        'mentions',
        ['mentioned_agent_id', 'created_at']
    )

    op.create_index(
        'idx_mentions_org_agent',
        'mentions',
        ['org_id', 'mentioned_agent_id']
    )


def downgrade():
    """Drop mentions table"""
    op.drop_index('idx_mentions_org_agent', table_name='mentions')
    op.drop_index('idx_mentions_agent_created', table_name='mentions')
    op.drop_index('idx_mentions_agent_unread', table_name='mentions')
    op.drop_table('mentions')
