"""Create MCP compatibility tables

Revision ID: 001_mcp_tables
Revises:
Create Date: 2025-07-21 23:55:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_mcp_tables'
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return inspector.has_table(table_name)


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    if not _table_exists(inspector, table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Create MCP-compatible tables for infrastructure-as-code deployment"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create agent_profiles table (maps to existing agents table)
    if not _table_exists(inspector, 'agent_profiles'):
        op.create_table('agent_profiles',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('username', sa.String(255), nullable=False),
            sa.Column('agent_type', sa.String(100), nullable=True, default='general'),
            sa.Column('bio', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True, default=sa.func.current_timestamp()),
            sa.Column('user_id', sa.String(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
            sa.Column('last_active', sa.DateTime(), nullable=True, default=sa.func.current_timestamp()),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('username')
        )

    # Create text_posts table (maps to existing messages table)
    if not _table_exists(inspector, 'text_posts'):
        op.create_table('text_posts',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('agent_id', sa.Integer(), nullable=True),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('uploaded_at', sa.DateTime(), nullable=True, default=sa.func.current_timestamp()),
            sa.Column('channel', sa.String(100), nullable=True, default='main'),
            sa.Column('response_to', sa.Integer(), nullable=True),
            sa.Column('user_id', sa.String(), nullable=True),
            sa.Column('waiting_for_response', sa.Boolean(), nullable=True, default=False),
            sa.Column('waiting_since', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['agent_id'], ['agent_profiles.id'], ),
            sa.ForeignKeyConstraint(['response_to'], ['text_posts.id'], )
        )

    # Create read_status table for message tracking
    if not _table_exists(inspector, 'read_status'):
        op.create_table('read_status',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('post_id', sa.Integer(), nullable=True),
            sa.Column('agent_name', sa.String(255), nullable=True),
            sa.Column('read_at', sa.DateTime(), nullable=True, default=sa.func.current_timestamp()),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['post_id'], ['text_posts.id'], ),
            sa.UniqueConstraint('post_id', 'agent_name')
        )

    # Extend existing tasks table with MCP-expected columns if they don't exist
    # Skip entirely on fresh DB where tasks table hasn't been created yet
    if _table_exists(inspector, 'tasks'):
        task_columns = _column_names(inspector, 'tasks')
        if 'created_by' not in task_columns:
            op.add_column('tasks', sa.Column('created_by', sa.String(255), nullable=True))
        if 'assigned_to' not in task_columns:
            op.add_column('tasks', sa.Column('assigned_to', sa.String(255), nullable=True))
        if 'claimed_by' not in task_columns:
            op.add_column('tasks', sa.Column('claimed_by', sa.String(255), nullable=True))
        if 'skills' not in task_columns:
            op.add_column('tasks', sa.Column('skills', postgresql.ARRAY(sa.String()), nullable=True))


def downgrade() -> None:
    """Remove MCP compatibility tables"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, 'read_status'):
        op.drop_table('read_status')
    if _table_exists(inspector, 'text_posts'):
        op.drop_table('text_posts')
    if _table_exists(inspector, 'agent_profiles'):
        op.drop_table('agent_profiles')

    # Remove added columns from tasks table
    task_columns = _column_names(inspector, 'tasks')
    if 'skills' in task_columns:
        op.drop_column('tasks', 'skills')
    if 'claimed_by' in task_columns:
        op.drop_column('tasks', 'claimed_by')
    if 'assigned_to' in task_columns:
        op.drop_column('tasks', 'assigned_to')
    if 'created_by' in task_columns:
        op.drop_column('tasks', 'created_by')
