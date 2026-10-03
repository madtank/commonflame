"""add_messages_org_created_index_for_smart_window

Revision ID: 7e35fa9982f1
Revises: 020_oauth_clients
Create Date: 2025-11-16 00:02:32.187920

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e35fa9982f1'
down_revision: Union[str, None] = '020_oauth_clients'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add composite index on messages (org_id, created_at DESC) for smart window selection.

    PRODUCTION NOTE: For large tables, create index CONCURRENTLY manually before running migration:
        CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_org_created_at
        ON messages (org_id, created_at DESC);

    This migration will skip index creation if it already exists.
    """
    # Check if index exists
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = inspector.get_indexes('messages')
    index_exists = any(idx['name'] == 'idx_messages_org_created_at' for idx in indexes)

    if not index_exists:
        # Create index normally (brief lock, acceptable for dev/small tables)
        # For production with large tables, index should be created manually with CONCURRENTLY
        op.create_index(
            'idx_messages_org_created_at',
            'messages',
            ['org_id', sa.text('created_at DESC')],
            unique=False
        )


def downgrade() -> None:
    """Remove the messages org_id + created_at index."""
    op.execute("DROP INDEX IF EXISTS idx_messages_org_created_at")
