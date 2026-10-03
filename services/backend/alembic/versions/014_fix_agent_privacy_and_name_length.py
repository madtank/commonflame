"""Fix agent privacy and increase name length

Revision ID: 014_fix_agent_privacy_and_name_length
Revises: 013_fix_organization_invites_schema
Create Date: 2025-08-05 12:45:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '014_fix_agent_privacy_and_name_length'
down_revision = '013_fix_organization_invites_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    1. Increase agent name length from 100 to 255
    2. Update default visibility to private
    3. Update existing org_visible agents to private for security
    """

    # Increase name column length
    op.alter_column('agents', 'name',
                    type_=sa.String(255),
                    existing_type=sa.String(100),
                    existing_nullable=False)

    # Update existing org_visible agents to private (security fix)
    # This is a breaking change but necessary for privacy
    op.execute("""
        UPDATE agents
        SET visibility_level = 'private'
        WHERE visibility_level = 'org_visible'
    """)

    # Add comment documenting the privacy change
    op.execute("""
        COMMENT ON TABLE agents IS
        'Agent registry - privacy update 2025-08-05: agents now private by default, org sharing removed'
    """)


def downgrade() -> None:
    """Revert changes (not recommended due to privacy implications)"""

    # Reduce name column length back to 100
    # WARNING: This may truncate existing long names
    op.alter_column('agents', 'name',
                    type_=sa.String(100),
                    existing_type=sa.String(255),
                    existing_nullable=False)

    # Revert agents back to org_visible (not recommended)
    op.execute("""
        UPDATE agents
        SET visibility_level = 'org_visible'
        WHERE visibility_level = 'private'
    """)
