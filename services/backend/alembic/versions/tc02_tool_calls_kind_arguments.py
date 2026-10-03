"""Add kind and arguments columns to tool_calls for context-specific widget rendering

Revision ID: tc02_kind_args
Revises: tc01_tool_calls
Create Date: 2026-03-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "tc02_kind_args"
down_revision: Union[str, None] = "tc01_tool_calls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tool_calls", sa.Column("kind", sa.String(), nullable=True))
    op.add_column("tool_calls", sa.Column("arguments", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("tool_calls", "arguments")
    op.drop_column("tool_calls", "kind")
