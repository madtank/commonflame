"""Add index on correlation_id to tool_calls for audit-based widget lookup

Revision ID: tc03_correlation_idx
Revises: tc02_kind_args
Create Date: 2026-03-14

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "tc03_correlation_idx"
down_revision: Union[str, None] = "tc02_kind_args"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tool_calls_correlation "
            "ON tool_calls (correlation_id)"
        ))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_tool_calls_correlation"
        ))
