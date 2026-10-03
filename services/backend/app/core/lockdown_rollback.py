"""One-shot release of users locked out by the invite-only gate.

Reactivates app-level-disabled users (except those explicitly denied) and
approves pending access requests. Idempotent: a second run finds nothing to
flip and returns zeros. Invoked from the admin endpoint; the llr02 alembic
migration applies the same SQL at deploy time.

Design: docs/plans/2026-06-10-lockdown-rollback-design.md
"""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.access_request import AccessRequest
from ..models.user import User
from ..services.user_audit import record_user_audit_event

logger = logging.getLogger(__name__)


async def run_lockdown_rollback(
    db: AsyncSession,
    *,
    decided_by: str = "lockdown-rollback",
    actor_user_id: uuid.UUID | None = None,
) -> dict:
    """Reactivate non-denied disabled users and approve pending requests.

    Mirrors ``set_request_status`` conventions (lower-cased email matching,
    decided_by/decided_at on transition) but in bulk: that helper commits
    per-row, so it isn't called here. This function only flushes — the
    caller (the admin endpoint) owns the commit. Each reactivated user gets
    a ``user.reactivated`` audit event in the same transaction: the reason
    is the constant ``lockdown-rollback`` (it is a why, not a who) and the
    acting admin, if any, is attributed via ``actor_user_id``.
    """
    # Disabled users NOT matching a denied request's email (case-insensitive).
    # AccessRequest.email is NOT NULL, so the subquery can't yield NULL rows
    # (which would make NOT IN never-true and wrongly skip everyone).
    denied_emails = select(func.lower(AccessRequest.email)).where(
        AccessRequest.status == "denied"
    )
    result = await db.execute(
        select(User).where(
            User.active.is_(False),
            func.lower(User.email).not_in(denied_emails),
        )
    )
    disabled_users = result.scalars().all()
    for user in disabled_users:
        user.active = True
        await record_user_audit_event(
            db,
            user_id=user.id,
            event_type="user.reactivated",
            resource_type="user",
            resource_id=user.id,
            actor_user_id=actor_user_id,
            metadata={"reason": "lockdown-rollback"},
        )

    # Approve everything still pending; denied rows stay denied.
    result = await db.execute(
        select(AccessRequest).where(AccessRequest.status == "pending")
    )
    pending_requests = result.scalars().all()
    now = datetime.now(timezone.utc)
    for req in pending_requests:
        req.status = "approved"
        req.decided_by = decided_by
        req.decided_at = now

    await db.flush()  # caller commits

    counts = {
        "users_reactivated": len(disabled_users),
        "requests_approved": len(pending_requests),
    }
    logger.info(
        "LOCKDOWN_ROLLBACK users_reactivated=%s requests_approved=%s by=%s",
        counts["users_reactivated"],
        counts["requests_approved"],
        decided_by,
    )
    return counts
