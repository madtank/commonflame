"""Approve-link signing for the invite-only waitlist gate.

A short HS256 JWT binds an email + expiry, signed with ``approve_link_secret``.
The signed token IS the auth for ``GET /auth/access/approve`` (clicked from
an email link in a browser), so no X-Internal-Key is required there.

Also hosts ``set_request_status`` — the single place that mutates an
AccessRequest's status — shared by the email-link endpoint and the admin
console so the approve/deny/reset logic (and the approve-also-reenables-user
unification) lives in exactly one place.

Design: docs/plans/2026-05-28-invite-only-waitlist-gate-design.md
       docs/plans/2026-05-29-access-requests-admin-console-design.md
"""
import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote

import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings

logger = logging.getLogger(__name__)

VALID_REQUEST_STATUSES = ("pending", "approved", "denied")

_ALGORITHM = "HS256"
_DEFAULT_TTL_SECONDS = 14 * 24 * 60 * 60  # 14 days
APPROVE_ROUTE_PATH = "/auth/access/approve"


def mint_approve_token(email: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
    """Mint an HS256 token binding the (lower-cased) email with an expiry."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "email": (email or "").strip().lower(),
        "iat": now,
        "exp": now + ttl_seconds,
        "purpose": "access_approve",
    }
    return jwt.encode(payload, settings.approve_link_secret, algorithm=_ALGORITHM)


def verify_approve_token(token: str) -> str | None:
    """Return the email if the token is valid+unexpired, else None."""
    if not token:
        return None
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.approve_link_secret, algorithms=[_ALGORITHM])
    except Exception as exc:  # noqa: BLE001 — invalid/expired/garbage all → None
        logger.info("APPROVE_TOKEN_INVALID %s: %s", type(exc).__name__, exc)
        return None
    if claims.get("purpose") != "access_approve":
        return None
    email = claims.get("email")
    return email or None


def build_approve_url(email: str) -> str:
    """Build the full approve URL (base + route + signed token) for an email."""
    settings = get_settings()
    token = mint_approve_token(email)
    base = settings.approve_link_base_url.rstrip("/")
    return f"{base}{APPROVE_ROUTE_PATH}?token={quote(token)}"


async def set_request_status(
    db: AsyncSession,
    req,
    status: str,
    *,
    decided_by: str,
    commit: bool = True,
) -> bool:
    """Transition an AccessRequest to ``status`` and (on approve) re-enable a match.

    The single mutation point for AccessRequest.status, shared by the email-link
    endpoint and the admin console so the rules stay in one place:

    - sets status / decided_at / decided_by (decided_at cleared on a reset to
      ``pending`` so the row reads as freshly re-opened);
    - when approving, finds the matching ``User`` (lower-cased email, else
      github_id) and flips ``active=True`` if it was disabled — unifying the
      front-door allowlist with existing-account enable/disable so one click is
      enough.

    Returns whether a matching disabled user was reactivated. Idempotent: a
    no-op transition (already in ``status``) still refreshes decided_by/at.
    """
    from ..models.user import User  # local import avoids import-time cycles

    norm = (status or "").strip().lower()
    if norm not in VALID_REQUEST_STATUSES:
        raise ValueError(f"invalid access-request status: {status!r}")

    req.status = norm
    if norm == "pending":
        req.decided_at = None
        req.decided_by = None
    else:
        req.decided_at = datetime.now(timezone.utc)
        req.decided_by = decided_by

    reactivated = False
    if norm == "approved":
        email = (getattr(req, "email", None) or "").strip().lower()
        github_id = getattr(req, "github_id", None)
        # Match email FIRST (case-insensitive — stored emails may be mixed-case),
        # then fall back to github_id only if no email match. Avoids an ambiguous
        # OR where the email belongs to user A but the github_id to user B.
        user = None
        if email:
            result = await db.execute(
                select(User).where(func.lower(User.email) == email).limit(1)
            )
            user = result.scalar_one_or_none()
        if user is None and github_id:
            result = await db.execute(
                select(User).where(User.github_id == str(github_id)).limit(1)
            )
            user = result.scalar_one_or_none()
        if user is not None and not user.active:
            user.active = True
            reactivated = True
            logger.info(
                "ACCESS_APPROVE_REENABLED_USER email=%s user_id=%s by=%s",
                email,
                user.id,
                decided_by,
            )

    if commit:
        await db.commit()
    return reactivated
