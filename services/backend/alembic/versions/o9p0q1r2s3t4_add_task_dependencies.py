"""Add task dependency columns for Phase 2

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-01-07 05:00:00.000000

Adds parent_task_id and blocked_by_ids columns to support task dependencies.
Requested by @wise_nexus_921 for Phase 2: Dependency Mapping.

Schema:
- parent_task_id: Single parent task (UUID FK)
- blocked_by_ids: List of blocking task IDs (JSONB array)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision: str = 'o9p0q1r2s3t4'
down_revision: Union[str, None] = 'n8o9p0q1r2s3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add task dependency columns.

    Note: These columns may already exist from manual ALTER in dev.
    Using IF NOT EXISTS for idempotency.
    """
    # Add parent_task_id - single parent reference
    op.execute("""
        ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS parent_task_id UUID REFERENCES tasks(id) ON DELETE SET NULL
    """)

    # Add blocked_by_ids - JSONB array of task UUIDs
    op.execute("""
        ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS blocked_by_ids JSONB DEFAULT '[]'::jsonb
    """)

    # Index for parent lookups (find children of a task)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id
        ON tasks (parent_task_id)
        WHERE parent_task_id IS NOT NULL
    """)

    # GIN index for blocked_by_ids lookups (find tasks blocked by a specific task)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_blocked_by_ids
        ON tasks USING gin (blocked_by_ids)
    """)


def downgrade() -> None:
    """Remove task dependency columns."""
    op.execute("DROP INDEX IF EXISTS idx_tasks_blocked_by_ids")
    op.execute("DROP INDEX IF EXISTS idx_tasks_parent_task_id")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS blocked_by_ids")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS parent_task_id")
