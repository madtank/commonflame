"""create_message_intelligence_table

Revision ID: 41b2e2d93412
Revises: 39f0ca8875a4
Create Date: 2025-12-13 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "41b2e2d93412"
down_revision = "39f0ca8875a4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "message_intelligence",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("spam_score", sa.Float(), nullable=True),
        sa.Column("toxicity_score", sa.Float(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        # Security detection - simple columns instead of JSON for reliability
        sa.Column("security_risk", sa.Float(), nullable=True),  # 0.0-1.0 overall risk score
        sa.Column("security_type", sa.String(50), nullable=True),  # prompt_injection, social_engineering, etc.
        sa.Column("security_reason", sa.String(200), nullable=True),  # Brief explanation (~20 words)
        sa.Column("provider_metadata", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id"),
    )
    # Explicit index on FK for query performance (also serves as PK index)
    op.create_index("ix_message_intelligence_message_id", "message_intelligence", ["message_id"])


def downgrade():
    op.drop_index("ix_message_intelligence_message_id", table_name="message_intelligence")
    op.drop_table("message_intelligence")
