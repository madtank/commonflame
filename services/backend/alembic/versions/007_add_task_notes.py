"""Add task notes table

Revision ID: 007_add_task_notes
Revises: 006_fix_refresh_token_duplicates
Create Date: 2025-07-28 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '007_add_task_notes'
down_revision = '006_fix_refresh_token_duplicates'
branch_labels = None
depends_on = None


def upgrade():
    # Create task_notes table
    op.create_table('task_notes',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('author_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('author_agent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('note_type', sa.String(20), nullable=False, server_default='general'),
        sa.Column('visibility', sa.String(20), nullable=False, server_default='public'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('(author_user_id IS NOT NULL) OR (author_agent_id IS NOT NULL)', name='author_check')
    )

    # Create indexes for better query performance
    op.create_index('ix_task_notes_task_id', 'task_notes', ['task_id'])
    op.create_index('ix_task_notes_org_id', 'task_notes', ['org_id'])
    op.create_index('ix_task_notes_created_at', 'task_notes', ['created_at'])


def downgrade():
    # Drop indexes
    op.drop_index('ix_task_notes_created_at', table_name='task_notes')
    op.drop_index('ix_task_notes_org_id', table_name='task_notes')
    op.drop_index('ix_task_notes_task_id', table_name='task_notes')

    # Drop table
    op.drop_table('task_notes')
