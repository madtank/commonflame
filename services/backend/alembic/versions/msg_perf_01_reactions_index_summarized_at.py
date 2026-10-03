"""Add messages performance indexes and summarized_at column

- Composite index on (parent_id, message_type) for reaction aggregation queries
- Index on (agent_id, created_at) for agent stats queries
- Index on (ai_summary) partial for finding unsummarized messages
- Add summarized_at timestamp column

Revision ID: msg_perf_01
Revises: 4383aa19f7fa
Create Date: 2026-02-16
"""

from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "msg_perf_01"
down_revision: Union[str, None] = "ak01_agent_keys"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Reaction aggregation: covers WHERE parent_id IN (...) AND message_type = 'reaction'
    op.create_index(
        "ix_messages_parent_id_message_type",
        "messages",
        ["parent_id", "message_type"],
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )

    # Agent stats: covers WHERE agent_id = X AND created_at >= Y
    op.create_index(
        "ix_messages_agent_id_created_at",
        "messages",
        ["agent_id", "created_at"],
    )

    # Summarization backfill: find messages without summaries
    op.create_index(
        "ix_messages_unsummarized",
        "messages",
        ["created_at"],
        postgresql_where=sa.text("ai_summary IS NULL AND message_type != 'reaction'"),
    )

    # Add summarized_at timestamp
    op.add_column("messages", sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "summarized_at")
    op.drop_index("ix_messages_unsummarized", table_name="messages")
    op.drop_index("ix_messages_agent_id_created_at", table_name="messages")
    op.drop_index("ix_messages_parent_id_message_type", table_name="messages")
