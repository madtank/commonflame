"""Bind OAuth refresh tokens to MCP agents.

Revision ID: oauth_as03_refresh_agent_binding
Revises: oauth_as02_reconcile_as_tables
Create Date: 2026-05-24
"""

from typing import Sequence, Union

from alembic import op


revision: str = "oauth_as03_refresh_agent_binding"
down_revision: Union[str, None] = "oauth_as02_reconcile_as_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE oauth_refresh_tokens ADD COLUMN IF NOT EXISTS agent_id UUID")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_agent_id
        ON oauth_refresh_tokens (agent_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_oauth_refresh_tokens_agent_id")
    op.execute("ALTER TABLE oauth_refresh_tokens DROP COLUMN IF EXISTS agent_id")
