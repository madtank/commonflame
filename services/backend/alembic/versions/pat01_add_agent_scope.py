"""Add agent_scope column to credentials table.

Revision ID: pat01_add_agent_scope
Revises: at01_set_agent_types, bac01_bound_agent_credential
Create Date: 2026-03-13

Adds explicit agent_scope enum ('all', 'user', 'agents') to credentials.
Backfills existing rows based on allowed_agent_ids presence.
"""
from alembic import op
import sqlalchemy as sa

revision: str = "pat01_add_agent_scope"
down_revision = ("at01_set_agent_types", "bac01_bound_agent_cred")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add column with default
    op.add_column(
        "credentials",
        sa.Column("agent_scope", sa.String(10), nullable=False, server_default="all"),
    )

    # Backfill: if allowed_agent_ids is a non-empty JSON array, scope is 'agents'
    # Use text cast and pattern match to avoid jsonb_typeof on non-jsonb column
    op.execute(sa.text("""
        UPDATE credentials
        SET agent_scope = 'agents'
        WHERE allowed_agent_ids IS NOT NULL
          AND allowed_agent_ids::text LIKE '[%'
          AND allowed_agent_ids::text != '[]'
    """))

    # Drop the server default after backfill (app sets it explicitly)
    op.alter_column("credentials", "agent_scope", server_default=None)


def downgrade() -> None:
    op.drop_column("credentials", "agent_scope")
