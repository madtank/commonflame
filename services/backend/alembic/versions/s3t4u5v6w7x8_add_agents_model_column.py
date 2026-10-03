"""add agents model column for model selection

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-01-09

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 's3t4u5v6w7x8'
down_revision = 'r2s3t4u5v6w7'
branch_labels = None
depends_on = None


def upgrade():
    # Add model column to agents table for cloud agent model selection
    # Default to gemini-2.5-flash (free tier, good balance of speed/quality)
    op.add_column(
        'agents',
        sa.Column('model', sa.String(100), nullable=False, server_default='gemini-2.5-flash')
    )
    # Remove server default after backfill - application handles defaults going forward
    # This prevents the DB schema from overriding application-level defaults
    op.alter_column('agents', 'model', server_default=None)


def downgrade():
    op.drop_column('agents', 'model')
