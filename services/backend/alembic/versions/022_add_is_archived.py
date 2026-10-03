"""add is_archived to organizations

Revision ID: 022_add_is_archived
Revises: 021_add_org_tier
Create Date: 2025-12-06
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "022_add_is_archived"
down_revision = "021_add_org_tier"
branch_labels = None
depends_on = None


def upgrade():
    # Add is_archived column with default False
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("organizations")]
    if "is_archived" not in columns:
        op.add_column("organizations", sa.Column("is_archived", sa.Boolean(), server_default="false", nullable=False))


def downgrade():
    op.drop_column("organizations", "is_archived")
