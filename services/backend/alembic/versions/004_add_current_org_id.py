"""Add current_org_id to users table for multi-org context switching

Revision ID: 004_add_current_org_id
Revises: 003_auto_assignment_system
Create Date: 2025-07-27 01:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '004_add_current_org_id'
down_revision = '003_auto_assignment'
branch_labels = None
depends_on = None


def upgrade():
    """Add current_org_id column to users table"""

    # Add current_org_id column to users table
    op.add_column('users', sa.Column(
        'current_org_id',
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey('organizations.id', ondelete='SET NULL'),
        nullable=True
    ))

    # Set current_org_id to match org_id for existing users (they stay in their home org)
    op.execute("""
        UPDATE users SET current_org_id = org_id WHERE current_org_id IS NULL
    """)


def downgrade():
    """Remove current_org_id column from users table"""

    # Drop the current_org_id column
    op.drop_column('users', 'current_org_id')
