"""Add security_score and security_category columns to message_intelligence

Revision ID: b2c3d4e5f6a7
Revises: d1e2f3a4b5c6
Create Date: 2025-12-16

Note: These columns were manually added to production on 2025-12-16 to fix
the message intelligence security review feature. This migration ensures
the schema is consistent across environments.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    # Add security_score and security_category columns
    # Using IF NOT EXISTS pattern for safety (already applied to prod manually)
    op.execute("""
        ALTER TABLE message_intelligence
        ADD COLUMN IF NOT EXISTS security_score DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS security_category VARCHAR(50);
    """)


def downgrade():
    op.drop_column("message_intelligence", "security_category")
    op.drop_column("message_intelligence", "security_score")
