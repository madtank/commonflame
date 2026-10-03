"""Add audience column to credentials for PAT audience selector.

Revision ID: authspec02
Revises: authspec01
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "authspec02"
down_revision = "authspec01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column(
            "audience",
            sa.String(length=20),
            nullable=False,
            server_default="cli",
        ),
    )
    # Existing PATs default to 'cli' (API-only)
    op.alter_column("credentials", "audience", server_default=None)


def downgrade() -> None:
    op.drop_column("credentials", "audience")
