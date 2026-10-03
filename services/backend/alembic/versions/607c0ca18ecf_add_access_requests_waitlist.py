"""add_access_requests_waitlist

Invite-only waitlist gate (IP-protection lockdown Phase 2).
Creates the access_requests table and seeds the four always-approved accounts.

Design: docs/plans/2026-05-28-invite-only-waitlist-gate-design.md

Revision ID: 607c0ca18ecf
Revises: ctxcat_v1_20260525
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "607c0ca18ecf"
down_revision: Union[str, None] = "ctxcat_v1_20260525"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SEED_APPROVED = ()  # Deployments choose their own access policy; no inherited identities.


def upgrade() -> None:
    op.create_table(
        "access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("github_username", sa.String(), nullable=True),
        sa.Column("github_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("email", name="uq_access_requests_email"),
        sa.CheckConstraint(
            "status IN ('pending','approved','denied')",
            name="ck_access_requests_status",
        ),
    )

    # Seed the always-approved accounts. Idempotent on re-run.
    for email in _SEED_APPROVED:
        op.execute(
            sa.text(
                "INSERT INTO access_requests (id, email, status, decided_by, decided_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :email, 'approved', 'seed', now(), now(), now()) "
                "ON CONFLICT (email) DO NOTHING"
            ).bindparams(email=email)
        )


def downgrade() -> None:
    op.drop_table("access_requests")
