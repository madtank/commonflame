"""Merge all migration heads into single head

Revision ID: merge_2026_03_08
Revises: ac01, mrs01_create_message_read_status, b7c8d9e0f1a2, 7155801152f0
Create Date: 2026-03-08
"""

from alembic import op

revision = 'merge_2026_03_08'
down_revision = (
    'ac01',
    'mrs01_create_message_read_status',
    'b7c8d9e0f1a2',
    '7155801152f0',
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
