"""add super_admin role support

Revision ID: 564fe0a0864a
Revises: 017_create_user_outreach_table
Create Date: 2025-11-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '564fe0a0864a'
down_revision = 'b92188f8199a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add super_admin role support for AI-powered features

    Role hierarchy:
    - user: Regular platform user
    - agent_manager: Can manage agents and basic admin tasks
    - admin: Full admin access to platform management
    - super_admin: HIGHEST privilege - AI features, system-level control

    NOTE: Migration does NOT automatically promote any users to super_admin.
    This must be done manually for security reasons.
    """

    # No schema change needed - role is VARCHAR(20) and can accommodate super_admin
    # This migration serves as documentation and validation checkpoint

    # Add constraint to validate role values (PostgreSQL)
    op.execute("""
        DO $$
        BEGIN
            -- Drop constraint if it exists
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'users_role_check'
                AND table_name = 'users'
            ) THEN
                ALTER TABLE users DROP CONSTRAINT users_role_check;
            END IF;

            -- Add new constraint with super_admin
            ALTER TABLE users ADD CONSTRAINT users_role_check
            CHECK (role IN ('user', 'agent_manager', 'admin', 'super_admin'));
        END $$;
    """)


def downgrade() -> None:
    """Remove super_admin role support"""

    # First, demote any super_admins to regular admin
    op.execute("""
        UPDATE users
        SET role = 'admin'
        WHERE role = 'super_admin';
    """)

    # Restore old constraint
    op.execute("""
        DO $$
        BEGIN
            -- Drop current constraint
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'users_role_check'
                AND table_name = 'users'
            ) THEN
                ALTER TABLE users DROP CONSTRAINT users_role_check;
            END IF;

            -- Add old constraint without super_admin
            ALTER TABLE users ADD CONSTRAINT users_role_check
            CHECK (role IN ('user', 'agent_manager', 'admin'));
        END $$;
    """)
