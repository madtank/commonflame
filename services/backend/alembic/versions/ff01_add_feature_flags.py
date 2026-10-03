"""Add feature_flags table

Revision ID: ff01
Revises: router01
Create Date: 2026-02-17
"""
from typing import Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'ff01'
down_revision: Union[str, None] = 'router01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'feature_flags',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('flag_name', sa.String(100), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('flag_name', 'org_id', 'user_id', name='uq_flag_org_user'),
    )
    op.create_index('ix_feature_flags_flag_name', 'feature_flags', ['flag_name'])
    op.create_index('ix_feature_flags_org_id', 'feature_flags', ['org_id'])
    op.create_index('ix_feature_flags_user_id', 'feature_flags', ['user_id'])


def downgrade() -> None:
    op.drop_table('feature_flags')
