"""Add task_number column and get_next_task_number function

Revision ID: 010_add_task_number_and_function
Revises: 009_guardrail_tables, 009_add_user_violations_count
Create Date: 2025-07-29 09:15:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '010_add_task_number_and_function'
down_revision = ('009_guardrail_tables', '009_add_user_violations_count')  # Merge both heads
branch_labels = None
depends_on = None


def upgrade():
    # Add task_number column to tasks table
    op.add_column('tasks', sa.Column('task_number', sa.Integer(), nullable=True))

    # Update existing tasks with sequential numbers per space
    op.execute("""
        UPDATE tasks
        SET task_number = row_number() OVER (PARTITION BY space_id ORDER BY created_at)
        WHERE task_number IS NULL
    """)

    # Create the get_next_task_number function
    op.execute("""
        CREATE OR REPLACE FUNCTION public.get_next_task_number(space_uuid uuid)
        RETURNS integer
        LANGUAGE plpgsql
        AS $function$
        DECLARE
            next_num INTEGER;
        BEGIN
            -- Get the highest task number for this space and increment by 1
            SELECT COALESCE(MAX(task_number), 0) + 1
            INTO next_num
            FROM tasks
            WHERE space_id = space_uuid;

            RETURN next_num;
        END;
        $function$
    """)


def downgrade():
    # Drop the function
    op.execute("DROP FUNCTION IF EXISTS public.get_next_task_number(uuid)")

    # Remove task_number column
    op.drop_column('tasks', 'task_number')
