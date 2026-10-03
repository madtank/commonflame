"""Increase agent name length only (preserve org visibility)

Revision ID: 015_increase_agent_name_length_only
Revises: 014_fix_agent_privacy_and_name_length
Create Date: 2025-08-05 13:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '015_increase_agent_name_length_only'
down_revision = '014_fix_agent_privacy_and_name_length'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    1. Increase agent name length from 100 to 255 (if not already done)
    2. Restore org_visible as default for team collaboration
    """

    # Increase name column length (safe to run again if already 255)
    op.alter_column('agents', 'name',
                    type_=sa.String(255),
                    existing_type=sa.String(100),
                    existing_nullable=False)

    # Restore org_visible agents for team collaboration
    # This reverses the privacy change from migration 014
    op.execute("""
        UPDATE agents
        SET visibility_level = 'org_visible'
        WHERE visibility_level = 'private'
    """)

    # Update comment to reflect collaborative approach
    op.execute("""
        COMMENT ON TABLE agents IS
        'Agent registry - collaborative workspace: agents visible within organizations for team collaboration'
    """)


def downgrade() -> None:
    """Revert to private visibility (not recommended for collaboration)"""

    # Change agents back to private
    op.execute("""
        UPDATE agents
        SET visibility_level = 'private'
        WHERE visibility_level = 'org_visible'
    """)

    # Note: Not reverting name length as it would truncate data
