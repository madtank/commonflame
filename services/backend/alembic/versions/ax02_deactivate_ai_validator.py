"""Deactivate __ai_validator__ system agent

Intelligence consolidated into aX — the ai_validator agent is no longer needed.
Sets status='inactive' so it stops reacting to messages.

Revision ID: ax02_deactivate
Revises: fb01_feedback
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa

revision = "ax02_deactivate"
down_revision = "ax01_rename_ax"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text(
            "UPDATE agents SET status = 'inactive' "
            "WHERE is_internal = true AND internal_type = 'ai_validator'"
        )
    )


def downgrade():
    op.execute(
        sa.text(
            "UPDATE agents SET status = 'active' "
            "WHERE is_internal = true AND internal_type = 'ai_validator'"
        )
    )
