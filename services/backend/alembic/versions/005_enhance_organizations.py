"""Enhance organizations with description and creator

Revision ID: 005_enhance_organizations
Revises: 004_add_current_org_id
Create Date: 2025-01-27 11:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005_enhance_organizations'
down_revision = '004_add_current_org_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add description column to organizations
    op.add_column('organizations', sa.Column('description', sa.String(length=500), nullable=True))

    # Add created_by column to organizations
    op.add_column('organizations', sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True))

    # Add foreign key constraint for created_by
    op.create_foreign_key(
        'fk_organizations_created_by_users',
        'organizations',
        'users',
        ['created_by'],
        ['id']
    )


def downgrade() -> None:
    # Drop foreign key constraint
    op.drop_constraint('fk_organizations_created_by_users', 'organizations', type_='foreignkey')

    # Drop columns
    op.drop_column('organizations', 'created_by')
    op.drop_column('organizations', 'description')
