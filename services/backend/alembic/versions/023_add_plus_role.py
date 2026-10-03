"""add plus role support

Revision ID: 023_add_plus_role
Revises: 51306b34beb0
Create Date: 2025-12-12 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "023_add_plus_role"
down_revision = "51306b34beb0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow 'plus' as a valid user role."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'users_role_check'
                AND table_name = 'users'
            ) THEN
                ALTER TABLE users DROP CONSTRAINT users_role_check;
            END IF;

            ALTER TABLE users ADD CONSTRAINT users_role_check
            CHECK (role IN ('user', 'agent_manager', 'admin', 'super_admin', 'plus'));
        END $$;
        """
    )


def downgrade() -> None:
    """Remove 'plus' role support (demote to 'user')."""
    op.execute(
        """
        UPDATE users
        SET role = 'user'
        WHERE role = 'plus';
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'users_role_check'
                AND table_name = 'users'
            ) THEN
                ALTER TABLE users DROP CONSTRAINT users_role_check;
            END IF;

            ALTER TABLE users ADD CONSTRAINT users_role_check
            CHECK (role IN ('user', 'agent_manager', 'admin', 'super_admin'));
        END $$;
        """
    )
