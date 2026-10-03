"""Add typed task assignee fields

Revision ID: ta01_typed_task_assignee
Revises: tq02_queue_reminders
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op


revision: str = "ta01_typed_task_assignee"
down_revision: Union[str, None] = "tq02_queue_reminders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS assignee_type VARCHAR(10),
        ADD COLUMN IF NOT EXISTS assignee_id UUID,
        ADD COLUMN IF NOT EXISTS assigned_by_type VARCHAR(10),
        ADD COLUMN IF NOT EXISTS assigned_by_id UUID
    """)

    op.execute("""
        UPDATE tasks
        SET assignee_type = 'agent',
            assignee_id = assigned_agent_id
        WHERE assigned_agent_id IS NOT NULL
          AND assignee_id IS NULL
    """)

    op.execute("""
        ALTER TABLE tasks
        DROP CONSTRAINT IF EXISTS ck_tasks_assignee_pair,
        ADD CONSTRAINT ck_tasks_assignee_pair CHECK (
            (assignee_type IS NULL AND assignee_id IS NULL)
            OR
            (assignee_type IN ('user', 'agent') AND assignee_id IS NOT NULL)
        )
    """)
    op.execute("""
        ALTER TABLE tasks
        DROP CONSTRAINT IF EXISTS ck_tasks_assigned_by_type,
        ADD CONSTRAINT ck_tasks_assigned_by_type CHECK (
            assigned_by_type IS NULL OR assigned_by_type IN ('user', 'agent')
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_assignee
        ON tasks (space_id, assignee_type, assignee_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_assigned_agent_compat
        ON tasks (space_id, assigned_agent_id)
        WHERE assigned_agent_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tasks_assigned_agent_compat")
    op.execute("DROP INDEX IF EXISTS idx_tasks_assignee")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_assigned_by_type")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_assignee_pair")
    op.execute("""
        ALTER TABLE tasks
        DROP COLUMN IF EXISTS assigned_by_id,
        DROP COLUMN IF EXISTS assigned_by_type,
        DROP COLUMN IF EXISTS assignee_id,
        DROP COLUMN IF EXISTS assignee_type
    """)
