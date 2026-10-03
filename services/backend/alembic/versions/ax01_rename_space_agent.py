"""Rename Space Agent to aX in agents table

Revision ID: ax01_rename_ax
Revises: fb01_feedback
Create Date: 2026-03-09

"""
from alembic import op
import sqlalchemy as sa

revision = "ax01_rename_ax"
down_revision = "fb01_feedback"
branch_labels = None
depends_on = None


def upgrade():
    # Rename all Space Agent instances to "aX"
    # origin='space_agent' stays unchanged (it's a dispatch routing key, not a display name)
    op.execute(
        sa.text(
            "UPDATE agents SET name = 'aX' WHERE origin = 'space_agent' AND name = 'Space Agent'"
        )
    )


def downgrade():
    op.execute(
        sa.text(
            "UPDATE agents SET name = 'Space Agent' WHERE origin = 'space_agent' AND name = 'aX'"
        )
    )
