"""Add user_settings table

Revision ID: 7155801152f0
Revises: b2c3d4e5f6g7
Create Date: 2026-02-10

Per-user, per-org preferences for notification, AI, and display settings.
Auto-created on first access with sensible defaults.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision: str = '7155801152f0'
down_revision: Union[str, None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_settings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('org_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True),

        # Notification preferences
        sa.Column('email_notifications', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('mention_notifications', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('task_notifications', sa.Boolean(), nullable=False, server_default=sa.text('true')),

        # AI preferences
        sa.Column('ai_suggestions_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('ai_auto_summarize', sa.Boolean(), nullable=False, server_default=sa.text('false')),

        # Display preferences
        sa.Column('theme', sa.String(20), nullable=False, server_default='system'),
        sa.Column('compact_mode', sa.Boolean(), nullable=False, server_default=sa.text('false')),

        # Extension point
        sa.Column('custom', JSONB, nullable=False, server_default='{}'),

        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),

        sa.UniqueConstraint('user_id', 'org_id', name='uq_user_settings_user_org'),
    )


def downgrade() -> None:
    op.drop_table('user_settings')
