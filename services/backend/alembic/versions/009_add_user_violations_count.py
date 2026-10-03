"""Add violations_count to users table

Revision ID: 009_add_user_violations_count
Revises: 008_add_performance_indexes
Create Date: 2025-01-28 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '009_add_user_violations_count'
down_revision = '008_add_performance_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add violations_count column to users table
    op.add_column('users', sa.Column('violations_count', sa.Integer(), nullable=False, server_default='0'))

    # Update existing users with current violation counts from guardrail_violations table
    op.execute("""
        UPDATE users
        SET violations_count = (
            SELECT COUNT(*)
            FROM guardrail_violations
            WHERE guardrail_violations.user_id = users.id::text
        )
    """)


def downgrade() -> None:
    # Remove violations_count column
    op.drop_column('users', 'violations_count')
