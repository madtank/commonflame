"""Add index on tool_calls.message_id for scoped count queries

Revision ID: tc04_message_id_idx
Revises: tc03_correlation_idx
Create Date: 2026-04-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "tc04_message_id_idx"
down_revision: Union[str, None] = "tc03_correlation_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tool_calls_message_id "
            "ON tool_calls (message_id)"
        ))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_tool_calls_message_id"
        ))
