"""Add lifecycle_state to credentials for enrollment/active/revoked/expired tracking.

Revision ID: authspec01
Revises: cf01_create_credential_fingerprints
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa


revision = "authspec01"
down_revision = "cf01_create_credential_fingerprints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column(
            "lifecycle_state",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )
    # Backfill existing rows based on current state
    op.execute("""
        UPDATE credentials
        SET lifecycle_state = CASE
            WHEN revoked_at IS NOT NULL THEN 'revoked'
            WHEN expires_at IS NOT NULL AND expires_at <= NOW() THEN 'expired'
            ELSE 'active'
        END
    """)
    # Remove server_default after backfill so app code controls the value
    op.alter_column("credentials", "lifecycle_state", server_default=None)


def downgrade() -> None:
    op.drop_column("credentials", "lifecycle_state")
