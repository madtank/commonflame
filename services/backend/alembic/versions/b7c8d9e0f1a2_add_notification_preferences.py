"""Add notification_preferences table for email notifications

Revision ID: a1b2c3d4e5f6
Revises: z9y8x7w6v5u4
Create Date: 2026-02-08

Adds per-user notification preferences for email delivery
when users are @mentioned by agents.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'b7c8d9e0f1a2'
down_revision: str = 'z9y8x7w6v5u4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notification_preferences',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('email_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('email_on_mention', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('email_on_agent_error', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('email_on_task_complete', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('digest_mode', sa.String(20), nullable=False, server_default='immediate'),
        sa.Column('digest_window_minutes', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('quiet_hours_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('quiet_hours_start', sa.Time(), nullable=True, server_default=sa.text("'23:00'")),
        sa.Column('quiet_hours_end', sa.Time(), nullable=True, server_default=sa.text("'08:00'")),
        sa.Column('timezone', sa.String(50), nullable=True, server_default='America/Los_Angeles'),
        sa.Column('max_emails_per_hour', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('emails_sent_this_hour', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('hour_reset_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_notification_preferences_user_id', 'notification_preferences', ['user_id'])


def downgrade() -> None:
    op.drop_index('idx_notification_preferences_user_id')
    op.drop_table('notification_preferences')
