"""Repair task number function to use space_id instead of org_id

Revision ID: tk01_fix_task_number_function_space_id
Revises: dft01_expand_agent_mgmt_enums
Create Date: 2026-03-20 14:30:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "tk01_fix_task_number_function_space_id"
down_revision = "dft01_expand_agent_mgmt_enums"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP FUNCTION IF EXISTS public.get_next_task_number(uuid)")
    op.execute(
        """
        CREATE FUNCTION public.get_next_task_number(space_uuid uuid)
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


def downgrade():
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.get_next_task_number(org_uuid uuid)
        RETURNS integer
        LANGUAGE plpgsql
        AS $function$
        DECLARE
            next_num INTEGER;
        BEGIN
            SELECT COALESCE(MAX(task_number), 0) + 1
            INTO next_num
            FROM tasks
            WHERE org_id = org_uuid;

            RETURN next_num;
        END;
        $function$
        """
    )
