"""Add task queue, reminder, and stale lifecycle fields

Revision ID: tq02_queue_reminders
Revises: merge_heads_2026_04_11
Create Date: 2026-04-27
"""
from typing import Sequence, Union

from alembic import op


revision: str = "tq02_queue_reminders"
down_revision: Union[str, None] = "merge_heads_2026_04_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE tasks
        ADD COLUMN IF NOT EXISTS queue_state VARCHAR(20),
        ADD COLUMN IF NOT EXISTS queue_rank NUMERIC(20, 10),
        ADD COLUMN IF NOT EXISTS reminder_policy JSONB,
        ADD COLUMN IF NOT EXISTS next_reminder_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS last_reminded_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS reminder_count INTEGER,
        ADD COLUMN IF NOT EXISTS snoozed_until TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS stale_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS stale_reason VARCHAR(120),
        ADD COLUMN IF NOT EXISTS cancelled_reason VARCHAR(120)
    """)

    op.execute("""
        ALTER TABLE tasks
        ALTER COLUMN queue_rank TYPE NUMERIC(20, 10)
        USING queue_rank::numeric
    """)

    op.execute("""
        UPDATE tasks
        SET queue_state = CASE
            WHEN work_status = 'in_progress' THEN 'active'
            WHEN work_status = 'blocked' THEN 'blocked'
            WHEN work_status IN ('completed', 'cancelled') THEN 'inactive'
            ELSE 'queued'
        END
        WHERE queue_state IS NULL
    """)

    op.execute("""
        UPDATE tasks
        SET reminder_count = 0
        WHERE reminder_count IS NULL
    """)

    op.execute("ALTER TABLE tasks ALTER COLUMN queue_state SET DEFAULT 'queued'")
    op.execute("ALTER TABLE tasks ALTER COLUMN reminder_count SET DEFAULT 0")
    op.execute("ALTER TABLE tasks ALTER COLUMN reminder_count SET NOT NULL")

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_queue_state
        ON tasks (space_id, queue_state)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_assignee_queue
        ON tasks (space_id, assigned_agent_id, queue_state, queue_rank)
        WHERE assigned_agent_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_next_reminder_ready
        ON tasks (space_id, next_reminder_at)
        WHERE next_reminder_at IS NOT NULL AND stale_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_stale_at
        ON tasks (space_id, stale_at)
        WHERE stale_at IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tasks_stale_at")
    op.execute("DROP INDEX IF EXISTS idx_tasks_next_reminder_ready")
    op.execute("DROP INDEX IF EXISTS idx_tasks_assignee_queue")
    op.execute("DROP INDEX IF EXISTS idx_tasks_queue_state")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS cancelled_reason")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS stale_reason")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS stale_at")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS snoozed_until")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS reminder_count")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS last_reminded_at")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS next_reminder_at")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS reminder_policy")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS queue_rank")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS queue_state")
