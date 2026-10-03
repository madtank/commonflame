"""add_ai_summary_to_messages

Revision ID: 39f0ca8875a4
Revises: f4a2c8e7d9b1
Create Date: 2025-12-13 08:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "39f0ca8875a4"
down_revision = "023_add_plus_role"
branch_labels = None
depends_on = None


def upgrade():
    """
    Add ai_summary column to messages table for persistent AI-generated summaries.

    Feature: AI Summary Persistence (Task #974df7)
    - Stores AI-generated summaries so they're only generated once
    - Summaries are generated on-demand when user clicks "Generate AI Summary"
    - Rate limited to prevent abuse (10 summaries/min per user)
    """
    # Add ai_summary column (nullable, TEXT type for long summaries)
    op.add_column("messages", sa.Column("ai_summary", sa.Text(), nullable=True))

    # Create partial index for performance (only index messages with summaries)
    # CONCURRENTLY requires running outside transaction
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_ai_summary
            ON messages(id)
            WHERE ai_summary IS NOT NULL
        """)


def downgrade():
    """Remove ai_summary column and index."""
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_ai_summary")
    op.drop_column("messages", "ai_summary")
