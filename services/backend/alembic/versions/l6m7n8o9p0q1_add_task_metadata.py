"""add metadata column to tasks

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-01-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = 'l6m7n8o9p0q1'
down_revision = 'k5l6m7n8o9p0'
branch_labels = None
depends_on = None


def upgrade():
    # Add metadata column for flexible task metadata (completion notes, etc.)
    op.add_column('tasks', sa.Column('metadata', JSONB, nullable=True))


def downgrade():
    op.drop_column('tasks', 'metadata')
