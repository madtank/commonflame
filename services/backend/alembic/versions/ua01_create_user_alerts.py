"""Create user_alerts table (ALERTS-001).

Durable user attention alerts — distinct from mentions, task notifications,
and transient toasts.  Only aX (space agent) may create alerts in v1.

Revision ID: ua01_user_alerts
Revises: tk01_fix_task_number_function_space_id
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON

revision: str = "ua01_user_alerts"
down_revision: str = "tk01_fix_task_number_function_space_id"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_alerts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "source_agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Classification
        sa.Column("alert_type", sa.String(50), nullable=False),
        sa.Column(
            "severity",
            sa.String(20),
            nullable=False,
            server_default="attention",
        ),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="active"
        ),
        # Content
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("body_markdown", sa.Text, nullable=True),
        # Primary action / CTA
        sa.Column("primary_action_label", sa.String(80), nullable=False),
        sa.Column("primary_action_kind", sa.String(40), nullable=False),
        sa.Column("primary_target_url", sa.Text, nullable=True),
        sa.Column(
            "primary_target_message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "primary_target_task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        # Deduplication & extensibility
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column(
            "metadata",
            JSON,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Lifecycle timestamps
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Composite index: user + status + created_at DESC
    op.create_index(
        "ix_user_alerts_user_status_created",
        "user_alerts",
        ["user_id", "status", sa.text("created_at DESC")],
    )

    # Partial unique index: one active alert per dedupe_key per user
    op.execute(
        """
        CREATE UNIQUE INDEX uq_user_alerts_active_dedupe
            ON user_alerts (user_id, dedupe_key)
            WHERE dedupe_key IS NOT NULL AND status = 'active';
        """
    )


def downgrade():
    op.drop_index("uq_user_alerts_active_dedupe", table_name="user_alerts")
    op.drop_index(
        "ix_user_alerts_user_status_created", table_name="user_alerts"
    )
    op.drop_table("user_alerts")
