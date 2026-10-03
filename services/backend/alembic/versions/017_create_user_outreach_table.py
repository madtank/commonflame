"""create user outreach table

Revision ID: b92188f8199a
Revises: 016_github_sso
Create Date: 2025-11-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'b92188f8199a'
down_revision = '016_github_sso'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create user_outreach table for tracking admin contact with users"""

    op.create_table(
        'user_outreach',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('target_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contacted_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contact_method', sa.String(50), nullable=False, comment='email, phone, in-app, etc.'),
        sa.Column('notes', sa.Text, nullable=True, comment='Optional notes about the outreach'),
        sa.Column('campaign', sa.String(100), nullable=True, comment='Campaign or reason for outreach'),
        sa.Column('contacted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()'), onupdate=sa.text('now()')),
        comment='Tracks admin outreach to users to prevent duplicate contacts'
    )

    # Create indexes for efficient queries
    op.create_index('ix_user_outreach_target_user_id', 'user_outreach', ['target_user_id'])
    op.create_index('ix_user_outreach_contacted_by_user_id', 'user_outreach', ['contacted_by_user_id'])
    op.create_index('ix_user_outreach_contacted_at', 'user_outreach', ['contacted_at'])
    op.create_index('ix_user_outreach_campaign', 'user_outreach', ['campaign'])

    # Create composite index for finding last contact per user
    op.create_index('ix_user_outreach_target_contacted_at', 'user_outreach', ['target_user_id', 'contacted_at'])


def downgrade() -> None:
    """Drop user_outreach table"""

    op.drop_index('ix_user_outreach_target_contacted_at', table_name='user_outreach')
    op.drop_index('ix_user_outreach_campaign', table_name='user_outreach')
    op.drop_index('ix_user_outreach_contacted_at', table_name='user_outreach')
    op.drop_index('ix_user_outreach_contacted_by_user_id', table_name='user_outreach')
    op.drop_index('ix_user_outreach_target_user_id', table_name='user_outreach')
    op.drop_table('user_outreach')
