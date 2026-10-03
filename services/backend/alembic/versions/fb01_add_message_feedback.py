"""Add message_feedback table and agent feedback columns

Revision ID: fb01_feedback
Revises: sa01_backfill
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "fb01_feedback"
down_revision = "sa01_backfill"
branch_labels = None
depends_on = None


def upgrade():
    # Create message_feedback table
    op.create_table(
        "message_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=False),
        sa.Column("vote", sa.SmallInteger, nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
        sa.CheckConstraint("vote IN (-1, 1)", name="ck_feedback_vote_range"),
        sa.Index("idx_feedback_agent_org", "agent_id", "org_id"),
    )

    # Add feedback columns to agents table
    op.add_column("agents", sa.Column("feedback_score", sa.DECIMAL(5, 4), nullable=True))
    op.add_column("agents", sa.Column("feedback_count", sa.Integer, server_default="0", nullable=False))


def downgrade():
    op.drop_column("agents", "feedback_count")
    op.drop_column("agents", "feedback_score")
    op.drop_table("message_feedback")
