"""Merge heads before space_id rename

Revision ID: merge_before_space_rename
Revises: ax02_deactivate, cc01_conv_cards
Create Date: 2026-03-11
"""

from alembic import op

revision = "merge_before_space_rename"
down_revision = ("ax02_deactivate", "cc01_conv_cards")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
