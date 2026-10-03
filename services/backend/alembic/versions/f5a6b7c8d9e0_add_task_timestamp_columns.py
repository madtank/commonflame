"""Add assigned_at and claimed_at columns to tasks table

Revision ID: f5a6b7c8d9e0
Revises: b2c3d4e5f6a7
Create Date: 2025-12-17

Fixes production error: column t.assigned_at does not exist
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5a6b7c8d9e0'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add timestamp columns that MCP tools/tasks.py expects
    op.add_column('tasks', sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tasks', sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('tasks', 'claimed_at')
    op.drop_column('tasks', 'assigned_at')
