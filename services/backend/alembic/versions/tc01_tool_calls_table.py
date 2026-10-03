"""Create tool_calls audit table

Revision ID: tc01_tool_calls
Revises: asa01_agent_space_access
Create Date: 2026-03-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "tc01_tool_calls"
down_revision: Union[str, None] = "am01_can_manage_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tool_call_id", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("tool_action", sa.String(), nullable=True),
        sa.Column("resource_uri", sa.String(), nullable=True),
        sa.Column("arguments_hash", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="success"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("agent_name", sa.String(), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    # Unique index on tool_call_id for idempotent inserts
    op.create_index("ix_tool_calls_tool_call_id", "tool_calls", ["tool_call_id"], unique=True)

    # Composite indexes for common query patterns
    op.create_index("idx_tool_calls_space_created", "tool_calls", ["space_id", "created_at"])
    op.create_index("idx_tool_calls_agent", "tool_calls", ["agent_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_tool_calls_agent", table_name="tool_calls")
    op.drop_index("idx_tool_calls_space_created", table_name="tool_calls")
    op.drop_index("ix_tool_calls_tool_call_id", table_name="tool_calls")
    op.drop_table("tool_calls")
