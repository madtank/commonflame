"""add_security_fields_to_intelligence

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b3
Create Date: 2025-12-15 12:00:00.000000

Adds security analysis fields to message_intelligence table:
- security_score: 0-1 threat level
- security_category: type of security concern
- security_reason: explanation
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade():
    # Add security analysis columns to message_intelligence
    op.add_column(
        "message_intelligence",
        sa.Column("security_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "message_intelligence",
        sa.Column("security_category", sa.String(50), nullable=True),
    )
    op.add_column(
        "message_intelligence",
        sa.Column("security_reason", sa.String(500), nullable=True),
    )

    # Create index for security analysis queries
    op.create_index(
        "idx_message_intelligence_security",
        "message_intelligence",
        ["security_score", "security_category"],
        postgresql_where=sa.text("security_score > 0.5"),
    )


def downgrade():
    op.drop_index("idx_message_intelligence_security", table_name="message_intelligence")
    op.drop_column("message_intelligence", "security_reason")
    op.drop_column("message_intelligence", "security_category")
    op.drop_column("message_intelligence", "security_score")
