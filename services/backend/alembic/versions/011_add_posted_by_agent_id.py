"""Add posted_by_agent_id column to tasks table

Revision ID: 011_add_posted_by_agent_id
Revises: 010_add_task_number_and_function
Create Date: 2025-07-30 12:00:00.000000

This migration adds the missing posted_by_agent_id column to the tasks table.
This column allows tracking when tasks are created by agents (vs users), supporting
agent-to-agent task delegation and collaboration workflows.

Key Changes:
- tasks.posted_by_agent_id: UUID foreign key to agents table, nullable
- Foreign key constraint with SET NULL on delete
- Index for performance on agent task queries

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '011_add_posted_by_agent_id'
down_revision = '010_add_task_number_and_function'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add posted_by_agent_id column to tasks table"""

    # Add posted_by_agent_id column to tasks table
    op.add_column('tasks',
        sa.Column('posted_by_agent_id',
                  postgresql.UUID(as_uuid=True),
                  nullable=True,
                  comment='Agent that posted this task (for agent-created tasks)'))

    # Add foreign key constraint to agents table
    op.create_foreign_key(
        'fk_tasks_posted_by_agent',
        'tasks', 'agents',
        ['posted_by_agent_id'], ['id'],
        ondelete='SET NULL'
    )

    # Create index for performance on agent task queries
    op.create_index('idx_tasks_posted_by_agent_id', 'tasks', ['posted_by_agent_id'])


def downgrade() -> None:
    """Remove posted_by_agent_id column from tasks table"""

    # Drop index first
    op.drop_index('idx_tasks_posted_by_agent_id', 'tasks')

    # Drop foreign key constraint
    op.drop_constraint('fk_tasks_posted_by_agent', 'tasks')

    # Drop column
    op.drop_column('tasks', 'posted_by_agent_id')
