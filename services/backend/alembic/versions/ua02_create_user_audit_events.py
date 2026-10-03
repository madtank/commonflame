"""create user audit events

Revision ID: ua02_user_audit_events
Revises: ag01_agent_groups
Create Date: 2026-06-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ua02_user_audit_events"
down_revision: Union[str, None] = "ag01_agent_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(length=120), nullable=True),
        sa.Column("resource_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(length=80), server_default="backend", nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["actor_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_audit_events_space_created", "user_audit_events", ["space_id", "created_at"])
    op.create_index("idx_user_audit_events_tool_created", "user_audit_events", ["tool_name", "created_at"])
    op.create_index("idx_user_audit_events_type_created", "user_audit_events", ["event_type", "created_at"])
    op.create_index("idx_user_audit_events_user_created", "user_audit_events", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_user_audit_events_user_created", table_name="user_audit_events")
    op.drop_index("idx_user_audit_events_type_created", table_name="user_audit_events")
    op.drop_index("idx_user_audit_events_tool_created", table_name="user_audit_events")
    op.drop_index("idx_user_audit_events_space_created", table_name="user_audit_events")
    op.drop_table("user_audit_events")
