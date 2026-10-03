"""Helpers for signup alerts and user audit events."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any
import uuid

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import User
from app.models.user_audit_event import UserAuditEvent

logger = logging.getLogger(__name__)


def email_domain(email: str | None) -> str | None:
    """Return a lower-cased email domain, or None for synthetic/missing emails."""
    if not email:
        return None
    _display, parsed = parseaddr(email.strip())
    candidate = parsed or email.strip()
    if "@" not in candidate:
        return None
    domain = candidate.rsplit("@", 1)[1].strip().lower()
    if not domain or domain == "github.local":
        return None
    return domain


async def send_signup_alert(user: User) -> bool:
    """Best-effort SES email to the admin for a newly-created account."""
    settings = get_settings()
    if not getattr(settings, "signup_alerts_enabled", True):
        return False
    from app.core import ses_client  # local import: boto3 stays lazy

    try:
        return await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: ses_client.send_signup_alert_email(
                username=getattr(user, "username", None),
                email=getattr(user, "email", None),
                github_username=getattr(user, "github_username", None),
                auth_provider=getattr(user, "auth_provider", None),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - alert failures must not block signup
        logger.warning(
            "SIGNUP_ALERT_FAILED user_id=%s error=%s: %s",
            getattr(user, "id", None), type(exc).__name__, exc,
        )
        return False


async def record_user_audit_event(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    event_type: str,
    space_id: uuid.UUID | None = None,
    tool_name: str | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
    source: str = "backend",
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> UserAuditEvent:
    """Append a user audit event inside the caller's transaction."""
    event = UserAuditEvent(
        user_id=user_id,
        event_type=event_type,
        space_id=space_id,
        tool_name=tool_name,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
        source=source,
        metadata_json=metadata or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(event)
    return event


LOGIN_TOUCH_THROTTLE = timedelta(hours=1)


async def touch_last_login(db: AsyncSession, user: User) -> bool:
    """Throttled last_login_at update + user.login audit + dormant-return alert.

    Called on every authenticated request, so it no-ops unless the stored
    timestamp is missing or > LOGIN_TOUCH_THROTTLE stale. Returns True when it
    mutated the user (caller is responsible for committing). Must never raise:
    a failed touch logs and returns False rather than breaking auth.
    """
    now = datetime.now(timezone.utc)
    previous = getattr(user, "last_login_at", None)
    if previous is not None and previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    if previous is not None and now - previous < LOGIN_TOUCH_THROTTLE:
        return False

    user_id = user.id  # rollback() expires ORM attributes; capture before the try
    try:
        # Atomic claim: under concurrent requests at the throttle boundary, only
        # the one whose guarded UPDATE matches records the audit event and alert.
        result = await db.execute(
            update(User)
            .where(User.id == user.id)
            .where(
                or_(
                    User.last_login_at.is_(None),
                    User.last_login_at < now - LOGIN_TOUCH_THROTTLE,
                )
            )
            .values(last_login_at=now)
        )
        if getattr(result, "rowcount", 0) != 1:
            return False
        user.last_login_at = now

        settings = get_settings()
        await record_user_audit_event(
            db,
            user_id=user.id,
            event_type="user.login",
            resource_type="user",
            resource_id=user.id,
            metadata={
                "previous_login": previous.isoformat() if previous else None,
                "email_domain": email_domain(getattr(user, "email", None)),
            },
        )

        dormant_days = getattr(settings, "dormant_alert_days", 14)
        is_dormant_return = (
            previous is not None
            and now - previous >= timedelta(days=dormant_days)
            and email_domain(getattr(user, "email", None)) is not None
            and getattr(settings, "signup_alerts_enabled", True)
        )
        if is_dormant_return:
            from app.core import ses_client  # lazy: keeps boto3 out of hot path

            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ses_client.send_dormant_return_email(
                        username=getattr(user, "username", None),
                        email=getattr(user, "email", None),
                        last_seen=previous.isoformat(),
                        dormant_days=dormant_days,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - alerts must not block auth
                logger.warning("DORMANT_ALERT_FAILED user_id=%s: %s", user.id, exc)
        return True
    except Exception as exc:  # noqa: BLE001 - login touch must never break auth
        logger.warning(
            "LOGIN_TOUCH_FAILED user_id=%s error=%s: %s",
            user_id, type(exc).__name__, exc,
        )
        # A failed statement aborts the transaction on Postgres; roll back so
        # the request's shared session is usable again. rollback() expires
        # loaded attributes, so re-load the user for downstream readers. (If
        # the refresh itself raises, the DB is genuinely unusable — propagate.)
        await db.rollback()
        await db.refresh(user)
        return False
