"""Make messages.user_id nullable for agent-authored messages

SECURITY FIX: Agent-authored messages must have user_id=None to prevent
user impersonation. The internal agent-reply path already sets user_id=None,
but the column was marked NOT NULL in the model. This migration ensures
the DB schema matches production reality.

Revision ID: sec01_msg_uid_null
Revises: f4a2c8e7d9b1
Create Date: 2026-03-15

"""
from alembic import op
import sqlalchemy as sa

revision = 'sec01_msg_uid_null'
down_revision = 'tc03_correlation_idx'
branch_labels = None
depends_on = None


def upgrade():
    # Make user_id nullable — agent messages have user_id=NULL
    op.alter_column(
        'messages',
        'user_id',
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade():
    # Revert to NOT NULL (requires backfilling agent messages first)
    op.alter_column(
        'messages',
        'user_id',
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
