"""Serialize task display-id allocation and enforce uniqueness.

Revision ID: td01_lock_task_number_allocation
Revises: llr02_release_lockdown
Create Date: 2026-06-17
"""

from typing import Sequence, Union

from alembic import op


revision: str = "td01_lock_task_number_allocation"
down_revision: Union[str, None] = "llr02_release_lockdown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Repair historical duplicates before adding the uniqueness gate. Keep the
    # earliest row for each (space_id, task_number); assign later duplicates new
    # numbers above the current per-space max so no tasks are deleted or merged.
    op.execute(
        """
        WITH duplicate_tasks AS (
            SELECT
                id,
                space_id,
                task_number,
                created_at,
                ROW_NUMBER() OVER (
                    PARTITION BY space_id, task_number
                    ORDER BY created_at ASC NULLS LAST, id ASC
                ) AS duplicate_rank
            FROM tasks
            WHERE task_number IS NOT NULL
        ),
        space_max AS (
            SELECT space_id, COALESCE(MAX(task_number), 0) AS max_task_number
            FROM tasks
            GROUP BY space_id
        ),
        renumber AS (
            SELECT
                duplicate_tasks.id,
                space_max.max_task_number + ROW_NUMBER() OVER (
                    PARTITION BY duplicate_tasks.space_id
                    ORDER BY duplicate_tasks.task_number ASC, duplicate_tasks.created_at ASC NULLS LAST, duplicate_tasks.id ASC
                ) AS new_task_number
            FROM duplicate_tasks
            JOIN space_max ON space_max.space_id = duplicate_tasks.space_id
            WHERE duplicate_tasks.duplicate_rank > 1
        )
        UPDATE tasks
        SET task_number = renumber.new_task_number
        FROM renumber
        WHERE tasks.id = renumber.id
        """
    )

    op.create_unique_constraint(
        "uq_tasks_space_task_number",
        "tasks",
        ["space_id", "task_number"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.get_next_task_number(space_uuid uuid)
        RETURNS integer
        LANGUAGE plpgsql
        AS $function$
        DECLARE
            next_num INTEGER;
        BEGIN
            -- Serialize allocation per space. Without this transaction-scoped
            -- advisory lock, concurrent creates can both read the same
            -- MAX(task_number) and emit duplicate task_000NNN display IDs.
            PERFORM pg_advisory_xact_lock(hashtextextended(space_uuid::text, 0));

            SELECT COALESCE(MAX(task_number), 0) + 1
            INTO next_num
            FROM tasks
            WHERE space_id = space_uuid;

            RETURN next_num;
        END;
        $function$
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_tasks_space_task_number", "tasks", type_="unique")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.get_next_task_number(space_uuid uuid)
        RETURNS integer
        LANGUAGE plpgsql
        AS $function$
        DECLARE
            next_num INTEGER;
        BEGIN
            SELECT COALESCE(MAX(task_number), 0) + 1
            INTO next_num
            FROM tasks
            WHERE space_id = space_uuid;

            RETURN next_num;
        END;
        $function$
        """
    )
