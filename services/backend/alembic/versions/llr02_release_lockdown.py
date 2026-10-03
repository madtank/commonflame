"""Release users locked out by the invite-only gate (lockdown rollback).

Bulk deploy-time fallback for `run_lockdown_rollback` (app/core/lockdown_rollback.py):
reactivates disabled users except those whose email matches a denied
access request (case-insensitive), and approves all pending requests.
Raw SQL writes no user audit rows — the audited path is the admin
endpoint POST /api/admin/access-requests/rollback-lockdown. Idempotent.

`access_requests.email` is NOT NULL, so the NOT IN subquery cannot yield
NULL rows (which would make NOT IN never-true and skip every user).

Revision ID: llr02_release_lockdown
Revises: llr01_last_login
"""
from typing import Sequence, Union

from alembic import op

revision: str = "llr02_release_lockdown"
down_revision: Union[str, None] = "llr01_last_login"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users SET active = true, updated_at = now()
        WHERE active = false
          AND lower(email) NOT IN (
            SELECT lower(email) FROM access_requests WHERE status = 'denied'
          )
        """
    )
    op.execute(
        """
        UPDATE access_requests
        SET status = 'approved', decided_by = 'lockdown-rollback',
            decided_at = now(), updated_at = now()
        WHERE status = 'pending'
        """
    )


def downgrade() -> None:
    pass  # one-way data migration; re-arm via WAITLIST_ENABLED=true instead
