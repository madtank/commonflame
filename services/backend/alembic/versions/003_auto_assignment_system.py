"""Add auto-assignment system with dual-state task management

Revision ID: 003_auto_assignment
Revises: 002_seed_mcp_data
Create Date: 2025-07-23 15:30:00.000000

This migration implements the auto-assignment system that reduces cognitive load
by automatically assigning tasks when agents view task details, with single-task
constraints and dual-state tracking (assignment vs work status).

Key Changes:
- agents.current_assigned_task_id: Enforces single-task focus
- tasks.assignment_status: Who owns the task (unassigned|assigned|locked)
- tasks.work_status: Work phase (not_started|in_progress|blocked|completed|cancelled)

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003_auto_assignment'
down_revision = '002_mcp_seed'
branch_labels = None
depends_on = None


def _table_exists(inspector, table_name: str) -> bool:
    return inspector.has_table(table_name)


def upgrade() -> None:
    """Add auto-assignment system columns"""

    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Guard: skip column/constraint/index ops when core tables don't exist yet
    # (fresh RDS — tables created by Base.metadata.create_all at app startup)
    agents_exists = _table_exists(inspector, 'agents')
    tasks_exists = _table_exists(inspector, 'tasks')

    if agents_exists:
        # Check if current_assigned_task_id column already exists (production hotfix compatibility)
        result = connection.execute(sa.text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'agents' AND column_name = 'current_assigned_task_id'
        """))

        if not result.fetchone():
            op.add_column('agents',
                sa.Column('current_assigned_task_id',
                          postgresql.UUID(as_uuid=True),
                          nullable=True,
                          comment='Currently assigned task - enforces single-task focus'))

        # Add foreign key constraint to ensure referential integrity (check if not exists)
        if tasks_exists:
            result = connection.execute(sa.text("""
                SELECT constraint_name FROM information_schema.table_constraints
                WHERE table_name = 'agents' AND constraint_name = 'fk_agents_current_assigned_task'
            """))

            if not result.fetchone():
                op.create_foreign_key(
                    'fk_agents_current_assigned_task',
                    'agents', 'tasks',
                    ['current_assigned_task_id'], ['id'],
                    ondelete='SET NULL'
                )

        # Index on agents
        connection.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_agents_current_assigned_task ON agents(current_assigned_task_id)
        """))

    if tasks_exists:
        # Add assignment_status to tasks table (who owns the task) - check if not exists
        result = connection.execute(sa.text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'tasks' AND column_name = 'assignment_status'
        """))

        if not result.fetchone():
            op.add_column('tasks',
                sa.Column('assignment_status',
                          sa.String(20),
                          nullable=False,
                          default='unassigned',
                          comment='Assignment ownership: unassigned|assigned|locked'))

        # Add work_status to tasks table (work phase) - check if not exists
        result = connection.execute(sa.text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'tasks' AND column_name = 'work_status'
        """))

        if not result.fetchone():
            op.add_column('tasks',
                sa.Column('work_status',
                          sa.String(20),
                          nullable=False,
                          default='not_started',
                          comment='Work phase: not_started|in_progress|blocked|completed|cancelled'))

        # Create indexes only if they don't exist
        connection.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_tasks_assignment_status ON tasks(assignment_status)
        """))

        connection.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS idx_tasks_work_status ON tasks(work_status)
        """))

        # Add check constraints to ensure valid status values (check if not exists)
        result = connection.execute(sa.text("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'tasks' AND constraint_name = 'ck_tasks_assignment_status_valid'
        """))

        if not result.fetchone():
            op.create_check_constraint(
                'ck_tasks_assignment_status_valid',
                'tasks',
                "assignment_status IN ('unassigned', 'assigned', 'locked')"
            )

        result = connection.execute(sa.text("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'tasks' AND constraint_name = 'ck_tasks_work_status_valid'
        """))

        if not result.fetchone():
            op.create_check_constraint(
                'ck_tasks_work_status_valid',
                'tasks',
                "work_status IN ('not_started', 'in_progress', 'blocked', 'completed', 'cancelled')"
            )

        # Migrate existing data to new system
        # Set assignment_status based on existing assigned_agent_id
        op.execute("""
            UPDATE tasks
            SET assignment_status = CASE
                WHEN assigned_agent_id IS NOT NULL THEN 'assigned'
                ELSE 'unassigned'
            END
        """)

        # Set work_status based on existing status
        op.execute("""
            UPDATE tasks
            SET work_status = CASE
                WHEN status = 'open' THEN 'not_started'
                WHEN status = 'assigned' THEN 'not_started'
                WHEN status = 'in_progress' THEN 'in_progress'
                WHEN status = 'completed' THEN 'completed'
                WHEN status = 'cancelled' THEN 'cancelled'
                ELSE 'not_started'
            END
        """)

    # Update agents.current_assigned_task_id based on existing assignments
    if agents_exists and tasks_exists:
        op.execute("""
            UPDATE agents
            SET current_assigned_task_id = (
                SELECT id FROM tasks
                WHERE assigned_agent_id = agents.id
                  AND assignment_status = 'assigned'
                  AND work_status NOT IN ('completed', 'cancelled')
                LIMIT 1
            )
        """)


def downgrade() -> None:
    """Remove auto-assignment system columns"""

    # Drop constraints first
    op.drop_constraint('ck_tasks_work_status_valid', 'tasks')
    op.drop_constraint('ck_tasks_assignment_status_valid', 'tasks')

    # Drop indexes
    op.drop_index('idx_agents_current_assigned_task')
    op.drop_index('idx_tasks_work_status')
    op.drop_index('idx_tasks_assignment_status')

    # Drop foreign key constraint
    op.drop_constraint('fk_agents_current_assigned_task', 'agents')

    # Drop columns
    op.drop_column('agents', 'current_assigned_task_id')
    op.drop_column('tasks', 'work_status')
    op.drop_column('tasks', 'assignment_status')
