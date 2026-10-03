"""Add RLS policy for organizations table

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-01-07 05:15:00.000000

Security: Users should only see organizations (spaces) they are members of.
This prevents enumeration of all organization names in the platform.

SOVEREIGN NOTE: "organizations" table = frontend "spaces".
The organization_memberships table tracks space membership.

Note: This requires app.current_user_id to be set (in addition to org_id).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'q1r2s3t4u5v6'
down_revision: Union[str, None] = 'p0q1r2s3t4u5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS on organizations table.

    Policy: Users can only see organizations (spaces) they are members of.
    This is checked via organization_memberships table.
    """
    # Enable RLS
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")

    # Create policy: user can see orgs they're a member of
    # OR the org matches their current context (for the active workspace)
    op.execute("""
        CREATE POLICY organizations_membership ON organizations
        FOR ALL
        USING (
            -- User is viewing their current workspace (space)
            id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            OR
            -- User is a member of this organization (space)
            id IN (
                SELECT org_id FROM organization_memberships
                WHERE user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
        )
    """)

    # Index for membership lookups (composite index on user_id, org_id)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_org_memberships_user_org
        ON organization_memberships (user_id, org_id)
    """)


def downgrade() -> None:
    """Remove RLS from organizations table."""
    op.execute("DROP INDEX IF EXISTS idx_org_memberships_user_org")
    op.execute("DROP POLICY IF EXISTS organizations_membership ON organizations")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")
