"""add web_fetch and brave_search toggles

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-01-22

Splits web_browsing_enabled into two granular toggles:
- web_fetch_enabled: Fetch and parse web pages
- brave_search_enabled: Web search via Brave Search API

The existing web_browsing_enabled column is preserved for backwards compatibility.
"""
from alembic import op
import sqlalchemy as sa

revision = 'w7x8y9z0a1b2'
down_revision = 'v6w7x8y9z0a1'
branch_labels = None
depends_on = None


def upgrade():
    # Add web_fetch_enabled column
    op.add_column(
        'agents',
        sa.Column('web_fetch_enabled', sa.Boolean(), nullable=False, server_default='false')
    )

    # Add brave_search_enabled column
    op.add_column(
        'agents',
        sa.Column('brave_search_enabled', sa.Boolean(), nullable=False, server_default='false')
    )

    # Migrate existing web_browsing_enabled values to new columns
    # If web_browsing was enabled, enable both new toggles
    op.execute("""
        UPDATE agents
        SET web_fetch_enabled = web_browsing_enabled,
            brave_search_enabled = web_browsing_enabled
        WHERE web_browsing_enabled = true
    """)


def downgrade():
    op.drop_column('agents', 'brave_search_enabled')
    op.drop_column('agents', 'web_fetch_enabled')
