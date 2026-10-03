"""add_web_browsing_enabled

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2025-12-31 12:00:00.000000

Adds web_browsing_enabled boolean field to agents table.
This enables Brave browser access for cloud agents (admin/plus users only).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "h2i3j4k5l6m7"
down_revision = "g1h2i3j4k5l6"
branch_labels = None
depends_on = None


def upgrade():
    # Add web_browsing_enabled column to agents table
    op.add_column(
        "agents",
        sa.Column("web_browsing_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    # Remove web_browsing_enabled column
    op.drop_column("agents", "web_browsing_enabled")
