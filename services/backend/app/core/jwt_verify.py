"""Cognito JWT verification and FastAPI auth dependencies (AUTH-001).

Drop-in replacements for get_current_user_from_token and get_user_from_jwt_or_mcp
so route files can swap imports without rewriting signatures.
"""
import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.access_approval import build_approve_url
from ..core.auth_config import ALLOWED_AUDIENCES, COGNITO_ISSUER, COGNITO_JWKS_URL, FRONTEND_AUDIENCE
from ..core.authorization import verify_space_membership
from ..core.config import get_settings
from ..core.database import get_db_session
from ..core.redis_client import redis_client
from ..core.ses_client import send_waitlist_notification
from ..models.access_request import AccessRequest
from ..models.agent import Agent
from ..models.space import Space
from ..models.space_membership import SpaceMembership
from ..models.user import User
from ..services.agent_control_service import AgentControlService
from ..services.user_audit import email_domain, record_user_audit_event, send_signup_alert, touch_last_login

logger = logging.getLogger(__name__)
agent_control_service = AgentControlService(redis_client)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

# JWKS cache
_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL_SECONDS = 300  # 5 minutes


def _derive_cognito_fallback_identity(
    *,
    email: str | None,
    preferred_username: str | None,
    cognito_sub: str | None,
) -> tuple[str, str]:
    """Return the deterministic username/email used for Cognito auto-provisioning."""
    # Never use preferred/display username claims as an authentication fallback.
    # They are mutable/provider-controlled and can collide with an unrelated app
    # user. Deterministic synthetic fallback identities must come from Cognito sub.
    username = f"user_{cognito_sub[:8]}" if cognito_sub else f"user_{uuid.uuid4().hex[:8]}"
    user_email = email or f"{username}@github.local"
    return username, user_email


def _username_with_cognito_suffix(username: str, cognito_sub: str | None) -> str:
    suffix = (cognito_sub or uuid.uuid4().hex)[:8]
    base = username[: max(1, 50 - len(suffix) - 1)].strip("-.") or "user"
    return f"{base}-{suffix}"[:50]


async def _ensure_unique_provision_username(
    db: AsyncSession,
    username: str,
    *,
    cognito_sub: str | None,
) -> str:
    """Return a non-conflicting app username for a new Cognito user.

    Username claims are useful display/provisioning hints, not identity bindings.
    If a hinted username already belongs to another account, suffix it with the
    Cognito subject fingerprint rather than authenticating as the colliding user.
    """
    result = await db.execute(select(User).where(User.username == username).limit(1))
    if result.scalar_one_or_none() is None:
        return username

    candidate = _username_with_cognito_suffix(username, cognito_sub)
    result = await db.execute(select(User).where(User.username == candidate).limit(1))
    if result.scalar_one_or_none() is None:
        return candidate

    return _username_with_cognito_suffix(username, uuid.uuid4().hex)


async def _get_jwks() -> dict[str, Any]:
    """Fetch and cache JWKS from Cognito (or cognito-local)."""
    global _jwks_cache, _jwks_fetched_at
    if _jwks_cache and (time.time() - _jwks_fetched_at) < _JWKS_TTL_SECONDS:
        return _jwks_cache

    async with httpx.AsyncClient() as client:
        resp = await client.get(COGNITO_JWKS_URL, timeout=5.0)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = time.time()
    return _jwks_cache


def _find_key(token: str, jwks: dict) -> dict:
    """Find the matching JWK for a token's kid."""
    headers = jwt.get_unverified_headers(token)
    kid = headers.get("kid")
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return key
    raise HTTPException(status_code=401, detail="Token key not found in JWKS")


def _looks_like_jwt(token: str | None) -> bool:
    return bool(token) and token.count(".") == 2


def _claim_values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item is not None}
    return {str(value)}


def _claims_audiences(claims: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    values.update(_claim_values(claims.get("aud")))
    values.update(_claim_values(claims.get("audience")))
    values.update(_claim_values(claims.get("resource")))
    return values


def _is_mcp_audience(value: str) -> bool:
    return value == "ax-mcp" or value.rstrip("/").endswith("/mcp")


def _is_exchange_mcp_route_agent_session(
    *,
    claims: dict[str, Any],
    request: Request | None,
    targeted_agent_id: str | None,
) -> bool:
    """Return whether an exchange/aX-AS MCP JWT may author as the route agent."""
    if not targeted_agent_id or request is None:
        return False
    if not (request.headers.get("x-agent-id") or request.headers.get("x-agent-name")):
        return False
    return bool(
        claims.get("token_class") in {"user_access", "user_admin"}
        and any(_is_mcp_audience(value) for value in _claims_audiences(claims))
    )


def _mark_user_as_route_agent_principal(user: User) -> None:
    agent_id = getattr(user, "_agent_id", None)
    if not agent_id:
        return
    user._principal_type = "agent"
    user._principal_id = str(agent_id)
    user._principal_agent_id = str(agent_id)
    user._principal_user_id = None


def _extract_cognito_username(claims: dict[str, Any]) -> str | None:
    return claims.get("username") or claims.get("cognito:username")


def _sanitize_username_candidate(
    value: Any,
    *,
    allow_email_local_part: bool = False,
) -> str | None:
    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if not candidate:
        return None

    if allow_email_local_part and "@" in candidate and " " not in candidate:
        candidate = candidate.split("@", 1)[0]

    if candidate.startswith("@"):
        candidate = candidate[1:]

    if candidate.startswith("GitHub_"):
        return None

    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate).strip("-.")
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    if not sanitized:
        return None
    return sanitized[:50]


def _extract_claim_username(claims: dict[str, Any]) -> str | None:
    for key in (
        "preferred_username",
        "custom:github_username",
        "github_username",
        "nickname",
        "cognito:username",
        "username",
    ):
        candidate = _sanitize_username_candidate(claims.get(key))
        if candidate:
            return candidate
    return None


def _derive_provision_username(
    claims: dict[str, Any],
    *,
    email: str | None,
    cognito_sub: str | None,
) -> str:
    explicit_username = _extract_claim_username(claims)
    if explicit_username:
        return explicit_username

    email_username = _sanitize_username_candidate(email, allow_email_local_part=True)
    if email_username:
        return email_username

    name_username = _sanitize_username_candidate(claims.get("name"))
    if name_username:
        return name_username

    if cognito_sub:
        return f"user_{cognito_sub[:8]}"
    return f"user_{uuid.uuid4().hex[:8]}"


def _extract_github_id(claims: dict[str, Any]) -> str | None:
    cognito_username = _extract_cognito_username(claims)
    if cognito_username and cognito_username.startswith("GitHub_"):
        return cognito_username.removeprefix("GitHub_")

    identities = claims.get("identities")
    if isinstance(identities, str):
        try:
            identities = json.loads(identities)
        except json.JSONDecodeError:
            identities = None

    if isinstance(identities, list):
        for identity in identities:
            if not isinstance(identity, dict):
                continue
            provider_name = (identity.get("providerName") or identity.get("providerType") or "").lower()
            if provider_name != "github":
                continue
            provider_user_id = identity.get("userId") or identity.get("issuerUserId")
            if provider_user_id:
                return str(provider_user_id)

    return None


async def _decode_token(token: str) -> dict:
    """Validate and decode a Cognito JWT, returning claims.

    Handles both access tokens (client_id, no aud) and ID tokens (aud).
    """
    jwks = await _get_jwks()
    key = _find_key(token, jwks)
    try:
        # Cognito access tokens don't have 'aud', only 'client_id'.
        # Decode without aud check first, then verify client_id/aud manually.
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=COGNITO_ISSUER,
            options={"verify_aud": False, "verify_at_hash": False},
        )
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    # Verify audience: check 'aud' (ID token) or 'client_id' (access token)
    if ALLOWED_AUDIENCES:
        token_aud = claims.get("aud") or claims.get("client_id")
        # aud can be a string or list
        token_auds = token_aud if isinstance(token_aud, list) else [token_aud] if token_aud else []
        if not any(a in ALLOWED_AUDIENCES for a in token_auds):
            raise HTTPException(status_code=401, detail="Token audience not allowed")

    return claims


_cognito_idp_client = None
_cognito_idp_client_lock = threading.Lock()


def _get_cognito_idp_client():
    """Lazy singleton boto3 cognito-idp client (mirrors ses_client pattern)."""
    global _cognito_idp_client
    if _cognito_idp_client is not None:
        return _cognito_idp_client
    with _cognito_idp_client_lock:
        if _cognito_idp_client is not None:
            return _cognito_idp_client
        import boto3

        region = get_settings().ses_region
        _cognito_idp_client = boto3.client("cognito-idp", region_name=region)
        logger.info("Cognito IDP client initialised (region=%s)", region)
    return _cognito_idp_client


async def resolve_cognito_email(access_token: str) -> str | None:
    """Best-effort fetch of the user's email from a Cognito ACCESS token.

    Cognito access tokens carry no `email` claim, so brand-new users can't be
    allowlisted/notified by the waitlist gate. This calls `cognito-idp.get_user`
    to read the verified email attribute. Returns the lower-cased email, or None
    on any failure (the boto3 call is sync, so it runs off the event loop).

    Best-effort: a Cognito API failure degrades to None — the gate then falls
    back to its empty-email 403 behaviour rather than 500-ing.
    """
    if not access_token:
        return None
    try:
        client = _get_cognito_idp_client()
        resp = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.get_user(AccessToken=access_token)
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise (ClientError, EndpointConnectionError, ...)
        logger.warning(
            "WAITLIST_COGNITO_GET_USER_FAILED error=%s: %s", type(exc).__name__, exc
        )
        return None

    for attr in resp.get("UserAttributes", []) or []:
        if attr.get("Name") == "email":
            value = attr.get("Value")
            if value:
                return value.strip().lower()
            return None
    logger.info("WAITLIST_COGNITO_NO_EMAIL_ATTR")
    return None


_WAITLIST_403_DETAIL = {
    "error": "pending_approval",
    "reason": "pending_approval",
    "message": (
        "Access to aX is invite-only. You've been added to the waitlist and the "
        "admin has been notified."
    ),
}
# HTTP 423 Locked, not 403: the CloudFront distribution rewrites 403 (and 404)
# responses to /index.html (200) for SPA deep-linking, which masks the gate's
# JSON body from the frontend. 423 passes through untouched so the SPA can read
# reason=pending_approval and show the request-access page. (Band-aid until the
# CDK error-responses are scoped off the /api & /auth behaviors.)
_WAITLIST_STATUS = 423


def _parse_always_approve_floor() -> set[str]:
    """Parse settings.access_always_approve into a lower-cased set.

    The floor contains BOTH emails and github-ids (as strings). Comma-split,
    strip blanks, lower-case. A bad/missing seed degrades to an empty set rather
    than crashing the gate.
    """
    raw = getattr(get_settings(), "access_always_approve", "") or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


async def _access_is_approved(
    db: AsyncSession, *, email: str | None, github_id: str | None
) -> bool:
    """Return whether this principal is allowed through the access gate.

    Fast path (no DB hit): owner floor — email OR github_id present in the
    always-approve set. Otherwise query AccessRequest for an ``approved`` row
    matching the email OR the github_id.
    """
    norm_email = (email or "").strip().lower()
    floor = _parse_always_approve_floor()
    if norm_email and norm_email in floor:
        return True
    if github_id is not None and str(github_id).strip().lower() in floor:
        return True

    conditions = []
    if norm_email:
        conditions.append(AccessRequest.email == norm_email)
    if github_id is not None:
        conditions.append(
            (AccessRequest.github_id.isnot(None))
            & (AccessRequest.github_id == str(github_id))
        )
    if not conditions:
        return False

    from sqlalchemy import or_

    result = await db.execute(
        select(AccessRequest)
        .where(AccessRequest.status == "approved")
        .where(or_(*conditions))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    # Re-check status explicitly: the SQL filters by status, but this also makes
    # the helper robust and keeps the "approved" decision unambiguous.
    return row is not None and getattr(row, "status", None) == "approved"


async def enforce_access(
    db: AsyncSession,
    *,
    email: str | None,
    github_id: str | None = None,
    full_name: str | None = None,
    github_username: str | None = None,
    sub: str | None = None,
) -> None:
    """Invite-only access gate (IP-protection lockdown Phase 2).

    Default-denies ALL unapproved human Cognito users (existing AND new). Agent
    RS256 / M2M-exchange / PAT tokens resolve via different functions and are
    never gated.

    - waitlist off                 -> no-op
    - approved (floor or DB row)    -> return (allow)
    - pending/denied existing row   -> 403 pending_approval (NO re-email)
    - unknown email                 -> insert pending row, best-effort SES notify
                                       ONCE, then 403
    - no email AND no existing row  -> 403 (cannot allowlist; fail closed)

    Design: docs/plans/2026-05-28-invite-only-waitlist-gate-design.md
    """
    settings = get_settings()
    if not settings.waitlist_enabled:
        return

    norm_email = (email or "").strip().lower()
    floor = _parse_always_approve_floor()

    # Owner floor — fast path, no DB hit.
    if norm_email and norm_email in floor:
        return
    if github_id is not None and str(github_id).strip().lower() in floor:
        return

    if not norm_email:
        # github_id present but not in floor — check for an approved row by id,
        # otherwise fail closed (cannot allowlist/notify without an email).
        if github_id is not None and await _access_is_approved(
            db, email=None, github_id=github_id
        ):
            return
        logger.info("ACCESS_BLOCK reason=empty_email sub=%s", sub)
        raise HTTPException(status_code=_WAITLIST_STATUS, detail=_WAITLIST_403_DETAIL)

    # Approved by email OR github_id -> allow. Checking this up front (rather than
    # only after the email-row lookup) honors a stable github-id approval even when
    # the current email has a stale pending/denied row (P2).
    if await _access_is_approved(db, email=norm_email, github_id=github_id):
        return

    # Not approved. Decide record/notify from the email's existing row.
    result = await db.execute(
        select(AccessRequest).where(AccessRequest.email == norm_email)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        # pending/denied (and not approved by github_id above) -> blocked, no re-email.
        logger.info("ACCESS_BLOCK reason=%s email=%s", existing.status, norm_email)
        raise HTTPException(status_code=_WAITLIST_STATUS, detail=_WAITLIST_403_DETAIL)

    # Unknown email: record a pending request, then best-effort notify the admin.
    req = AccessRequest(
        email=norm_email,
        full_name=full_name,
        github_username=github_username,
        github_id=github_id,
        status="pending",
    )
    db.add(req)
    try:
        await db.commit()
    except IntegrityError:
        # FINDING 4: a concurrent sign-in inserted the same email between our
        # SELECT and INSERT. Roll back, re-read the now-existing row, and decide
        # from its current status. Never re-send the notification email (the
        # winning request already did, or will).
        await db.rollback()
        logger.info("ACCESS_RACE_CONFLICT email=%s", norm_email)
        result = await db.execute(
            select(AccessRequest).where(AccessRequest.email == norm_email)
        )
        existing = result.scalar_one_or_none()
        if existing is not None and existing.status == "approved":
            return
        raise HTTPException(status_code=_WAITLIST_STATUS, detail=_WAITLIST_403_DETAIL)

    logger.info("ACCESS_NEW_REQUEST email=%s", norm_email)

    approve_url = build_approve_url(norm_email)
    # FINDING 5: send_waitlist_notification is sync boto3 — run it off the event
    # loop so the blocking SES call doesn't stall the async worker. The patched
    # name (app.core.jwt_verify.send_waitlist_notification) is resolved inside
    # the lambda so test patches still apply.
    sent = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: send_waitlist_notification(
            requester_email=norm_email,
            full_name=full_name,
            github_username=github_username,
            approve_url=approve_url,
        ),
    )
    if sent:
        req.emailed_at = datetime.now(timezone.utc)
        await db.commit()
    else:
        logger.warning("ACCESS_EMAIL_NOT_SENT email=%s (request still recorded)", norm_email)

    raise HTTPException(status_code=_WAITLIST_STATUS, detail=_WAITLIST_403_DETAIL)


async def enforce_waitlist(claims: dict, db: AsyncSession) -> None:
    """Thin wrapper over enforce_access for the new-user provisioning path.

    Extracts the relevant fields from Cognito claims and delegates to
    enforce_access. Kept for the brand-new-user branch of _resolve_user; behaves
    identically to the previous implementation for that path.
    """
    await enforce_access(
        db,
        email=claims.get("email"),
        github_id=_extract_github_id(claims),
        full_name=claims.get("name") or claims.get("full_name"),
        github_username=_extract_claim_username(claims),
        sub=claims.get("sub"),
    )


async def _block_if_disabled_account(
    db: AsyncSession,
    *,
    email: str | None,
    github_id: str | None,
    preferred_username: str | None,
) -> None:
    """Raise 423 if a matching account exists but is DISABLED (active=False).

    The user lookups in _resolve_user filter ``.where(User.active)``, so a
    disabled existing account resolves to None and would otherwise fall through
    to JIT provisioning — which then collides on the unique email and 500s.
    This makes app-level disable (admin console / DB) behave as a clean block
    that routes the SPA to the waiting-list page. The owner floor is honored so
    the owner can never self-lock. Called only on the new-user branch (after the
    active lookups already returned None).

    Independent of ``waitlist_enabled``: an account disabled at the app level
    must stay blocked even when the invite-only gate is turned off, otherwise a
    disabled account would fall through to provisioning (duplicate-key, or a
    second active account). App-level disable is the source of truth.
    """
    norm_email = (email or "").strip().lower()
    floor = _parse_always_approve_floor()
    if norm_email and norm_email in floor:
        return
    if github_id is not None and str(github_id).strip().lower() in floor:
        return

    from sqlalchemy import func, or_

    conditions = []
    if norm_email:
        conditions.append(func.lower(User.email) == norm_email)
    if github_id:
        conditions.append(User.github_id == str(github_id))
    if preferred_username:
        conditions.append(User.username == preferred_username)
        conditions.append(User.github_username == preferred_username)
    if not conditions:
        return

    # Only consider DISABLED rows. An ACTIVE match would already have been found
    # by the .where(User.active) lookups in _resolve_user, so we'd never reach
    # here — filtering active=False prevents a non-unique github_username from
    # returning an active row and masking a disabled one (which would otherwise
    # fall through to provisioning and 500 on the unique email).
    result = await db.execute(
        select(User).where(User.active.is_(False)).where(or_(*conditions)).limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        logger.info(
            "ACCESS_BLOCK reason=disabled_account email=%s user_id=%s",
            norm_email,
            existing.id,
        )
        raise HTTPException(status_code=_WAITLIST_STATUS, detail=_WAITLIST_403_DETAIL)


async def _resolve_user(
    claims: dict, db: AsyncSession, access_token: str | None = None
) -> User:
    """Look up (or auto-provision) a user from Cognito claims and set runtime attrs.

    Cognito sub is a new UUID not matching existing DB user IDs.
    Handles both token types:
      - ID tokens: have email, preferred_username
      - Access tokens: have username (e.g. "GitHub_12345"), client_id

    Account linking: updates existing user's GitHub fields on match.
    Auto-provision: creates new user + personal workspace if no match.
    """
    email = claims.get("email")
    preferred_username = _extract_claim_username(claims)
    cognito_sub = claims.get("sub")

    # Deterministic Cognito -> DB mapping:
    # 1. Immutable provider user ID (GitHub) when present
    # 2. Exact email
    # Username/display claims are not identity bindings and must not authenticate
    # an existing application user.
    github_id = _extract_github_id(claims)

    user = None

    # Try github_id first (most reliable for GitHub-federated users)
    if github_id:
        result = await db.execute(
            select(User).where(User.github_id == github_id).where(User.active)
        )
        user = result.scalar_one_or_none()

    # Try email (unique in DB)
    if user is None and email:
        result = await db.execute(select(User).where(User.email == email).where(User.active))
        user = result.scalar_one_or_none()

    _fallback_username, fallback_email = _derive_cognito_fallback_identity(
        email=email,
        preferred_username=preferred_username,
        cognito_sub=cognito_sub,
    )

    # Thin Cognito tokens (notably client_credentials) may carry only `sub`
    # and `client_id`. Reuse the same deterministic synthetic account instead
    # of repeatedly provisioning user_<sub> on every request.
    if user is None and fallback_email:
        result = await db.execute(
            select(User).where(User.email == fallback_email).where(User.active)
        )
        user = result.scalar_one_or_none()

    if user is not None:
        # Invite-only access gate FIRST — before any account-link mutation — so an
        # unapproved existing user's request persists nothing (P2). Existing users
        # are default-denied unless approved (floor or an approved AccessRequest);
        # their data is untouched, only the request is blocked. Use the resolved
        # USER's DB fields (reliable), NOT the thin token claims.
        await enforce_access(
            db,
            email=(user.email or "").strip().lower(),
            github_id=user.github_id,
            full_name=user.full_name,
            github_username=user.github_username,
        )
        # Account linking: update GitHub fields and fix workspace name if needed
        changed = False
        if github_id and user.github_id != github_id:
            user.github_id = github_id
            changed = True
        if preferred_username and user.github_username != preferred_username:
            user.github_username = preferred_username
            changed = True
        if preferred_username and user.username != preferred_username and user.username.startswith("user_"):
            # Fix username that was set from fallback (user_<sub>) — now we have the real one
            user.username = preferred_username
            changed = True
        if email and user.email != email and email != f"{user.username}@github.local":
            user.email = email
            changed = True

        # Fix home workspace name if it still has the fallback pattern
        if preferred_username and changed:
            home_space = await db.execute(
                select(Space).where(Space.id == user.space_id)
            )
            home = home_space.scalar_one_or_none()
            if home and home.name.startswith("user_") and home.name.endswith("'s Workspace"):
                correct_name = f"{preferred_username}'s Workspace"
                logger.info(f"Fixing workspace name: {home.name!r} → {correct_name!r}")
                home.name = correct_name

        touched = await touch_last_login(db, user)
        if changed or touched:
            user_id_for_log = user.id  # rollback() expires ORM attributes; capture first
            try:
                await db.commit()
                await db.refresh(user)
            except Exception:
                if changed:
                    # Account-linking failures must still surface as before.
                    raise
                # A failed login-touch commit must never fail auth. rollback()
                # expires all loaded attributes, so re-load them for the
                # downstream return path. (If this refresh itself raises, the
                # DB is genuinely down and auth cannot proceed anyway.)
                await db.rollback()
                await db.refresh(user)
                logger.warning("LOGIN_TOUCH_COMMIT_FAILED user_id=%s", user_id_for_log, exc_info=True)
            else:
                if changed:
                    logger.info(f"Cognito account linked: user={user.id}, github_id={github_id}, username={user.username}")
    else:
        # Public/community-first signup path. Brand-new users are allowed through
        # by default; setting WAITLIST_ENABLED=true restores the old invite-only
        # gate. Existing disabled app-level accounts are still blocked below.
        #
        # Cognito ACCESS tokens carry no `email` claim, so a brand-new user would
        # hit the gate with an empty email and could never be recorded, notified,
        # or approved. Best-effort fetch the verified email via cognito-idp so
        # BOTH the gate and provisioning use the real address. A Cognito failure
        # degrades to the existing empty-email 403 behaviour.
        if not claims.get("email") and access_token:
            fetched = await resolve_cognito_email(access_token)
            if fetched:
                claims = {**claims, "email": fetched}
                email = fetched
        # A "not found" result above can mean the account is DISABLED rather than
        # brand-new (the lookups filter .where(User.active)). Catch that first so
        # admin/app-level disable cleanly blocks (423 → waiting-list page) instead
        # of falling through to provisioning and colliding on the unique email.
        await _block_if_disabled_account(
            db,
            email=claims.get("email"),
            github_id=github_id,
            preferred_username=preferred_username,
        )
        await enforce_waitlist(claims, db)
        # Auto-provision new user with personal workspace
        user = await _provision_new_user(
            claims,
            db,
            claims.get("email"),
            preferred_username,
            github_id,
            cognito_sub,
        )

    # Set runtime attributes matching existing codebase expectations
    user._effective_space_id = str(user.current_space_id or user.space_id)
    user._agent_id = claims.get("custom:agent_id")
    user._agent_name = claims.get("custom:agent_name")
    await _enforce_agent_runtime_access(
        db,
        agent_id=user._agent_id,
        space_id=user._effective_space_id,
    )
    return user


async def _apply_owned_agent_target(
    user: User,
    db: AsyncSession,
    request: Request | None,
) -> User:
    """Adopt an owned agent target for interactive user principals.

    Cognito user JWTs authenticate the human principal. When the caller also
    supplies `X-Agent-Id` or `X-Agent-Name`, we resolve the user's owned agent
    and switch the effective space to that agent's bound space. This preserves
    the user principal while letting MCP/REST target the correct user-owned
    agent surface.

    Space agents remain out of scope here. They must authenticate via the
    backend-issued RS256 JWT path, not by reusing a user session token.
    """
    if request is None:
        return user

    # Space-agent and backend-issued agent tokens already carry canonical
    # agent identity in claims. Never override that with transport hints.
    if getattr(user, "_principal_type", None) == "agent":
        return user

    header_agent_id = request.headers.get("x-agent-id")
    header_agent_name = request.headers.get("x-agent-name")

    if isinstance(header_agent_name, str):
        header_agent_name = header_agent_name.strip()
    if isinstance(header_agent_id, str):
        header_agent_id = header_agent_id.strip()

    if not header_agent_id and not header_agent_name:
        return user

    query = select(Agent).where(Agent.user_id == user.id)

    if header_agent_id:
        try:
            agent_uuid = uuid.UUID(header_agent_id)
        except (TypeError, ValueError):
            logger.warning(
                "USER_AGENT_TARGET_INVALID_ID user=%s raw_agent_id=%s",
                user.id,
                header_agent_id,
            )
            return user
        query = query.where(Agent.id == agent_uuid)
    else:
        query = query.where(Agent.name == header_agent_name)

    result = await db.execute(query)
    try:
        agent = result.scalar_one_or_none()
    except MultipleResultsFound as exc:
        logger.warning(
            "USER_AGENT_TARGET_AMBIGUOUS user=%s agent_name=%s",
            user.id,
            header_agent_name,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "agent_target_ambiguous",
                "message": "Multiple owned agents match X-Agent-Name. Use X-Agent-Id.",
            },
        ) from exc
    if not agent:
        logger.info(
            "USER_AGENT_TARGET_MISS user=%s agent_id=%s agent_name=%s",
            user.id,
            header_agent_id,
            header_agent_name,
        )
        return user

    await verify_space_membership(db, user.id, agent.space_id)
    await _enforce_agent_runtime_access(
        db,
        agent_id=agent.id,
        space_id=agent.space_id,
    )

    user._agent_id = str(agent.id)
    user._agent_name = agent.name
    user._effective_space_id = str(agent.space_id)
    logger.info(
        "USER_AGENT_TARGET_RESOLVED user=%s agent=%s space=%s",
        user.id,
        agent.id,
        agent.space_id,
    )
    return user


async def _resolve_runtime_agent_space(
    db: AsyncSession,
    *,
    agent_id: str | None,
    fallback_space_id: str | None,
) -> str | None:
    """Resolve the current runtime space for an agent token.

    Agent sessions must follow live placement changes instead of staying pinned to
    whatever space_id was embedded when the token was minted. The token still
    authenticates the agent identity; the current space is reloaded from DB on
    each request so read/write paths stay in sync after a move.
    """
    if not agent_id:
        return fallback_space_id

    try:
        agent_uuid = uuid.UUID(str(agent_id))
    except (TypeError, ValueError):
        return fallback_space_id

    result = await db.execute(select(Agent).where(Agent.id == agent_uuid))
    agent = result.scalar_one_or_none()
    if agent is None:
        return fallback_space_id

    live_space_id = getattr(agent, "space_id", None) or fallback_space_id
    return str(live_space_id) if live_space_id else None


async def _resolve_legacy_user(
    token: str,
    db: AsyncSession,
    *,
    allow_agent_tokens: bool,
) -> User:
    from ..core.security import verify_token

    token_payload = verify_token(token)
    if token_payload is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if token_payload.type not in ("access", "agent"):
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if token_payload.type == "agent" and not allow_agent_tokens:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    user_id = token_payload.sub
    try:
        user_id = uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        pass

    result = await db.execute(
        select(User).where(User.id == user_id).where(User.active)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    if token_payload.type == "access" and user.token_version != token_payload.token_version:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    token_space_id = getattr(token_payload, "space_id", None)
    fallback_space_id = (
        str(token_space_id)
        if token_space_id and token_space_id != "00000000-0000-0000-0000-000000000000"
        else str(user.current_space_id or user.space_id)
    )

    user._agent_id = getattr(token_payload, "agent_id", None)
    effective_space_id = await _resolve_runtime_agent_space(
        db,
        agent_id=user._agent_id,
        fallback_space_id=fallback_space_id,
    ) or fallback_space_id
    user._effective_space_id = effective_space_id
    user._agent_name = getattr(token_payload, "agent_name", None)
    await _enforce_agent_runtime_access(
        db,
        agent_id=user._agent_id,
        space_id=effective_space_id,
    )
    return user


async def _resolve_cached_mcp_user(token: str, db: AsyncSession) -> User:
    from ..core.mcp_token_cache import validate_token

    token_meta = await validate_token(token)
    if not token_meta:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    user_id = token_meta.get("user_id") or token_meta.get("owner_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    try:
        user_id = uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        pass

    result = await db.execute(
        select(User).where(User.id == user_id).where(User.active)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    fallback_space_id = (
        token_meta.get("space_id") or token_meta.get("org_id")
        or token_meta.get("pinned_to_space") or token_meta.get("pinned_to_org")
        or str(user.current_space_id or user.space_id)
    )
    user._agent_id = token_meta.get("agent_id")
    effective_space_id = await _resolve_runtime_agent_space(
        db,
        agent_id=user._agent_id,
        fallback_space_id=str(fallback_space_id) if fallback_space_id else None,
    ) or str(fallback_space_id)
    user._effective_space_id = str(effective_space_id)
    user._agent_name = token_meta.get("agent_name")
    await _enforce_agent_runtime_access(
        db,
        agent_id=user._agent_id,
        space_id=effective_space_id,
    )
    return user


async def _enforce_agent_runtime_access(
    db: AsyncSession,
    *,
    agent_id: str | uuid.UUID | None,
    space_id: str | uuid.UUID | None = None,
) -> None:
    """Fail closed when an authenticated agent is inactive or kill-switched."""
    if not agent_id:
        return

    try:
        agent_uuid = uuid.UUID(str(agent_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid agent identity")

    result = await db.execute(select(Agent).where(Agent.id == agent_uuid))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=401, detail="Agent not found")
    if (agent.status or "").lower() != "active":
        raise HTTPException(status_code=403, detail="Agent is not active")

    effective_space_uuid = agent.space_id
    if space_id:
        try:
            effective_space_uuid = uuid.UUID(str(space_id))
        except (TypeError, ValueError):
            pass

    control_state = await agent_control_service.get_control_state(
        agent_id=agent.id,
        space_id=effective_space_uuid,
        agent_slug=(agent.name or "").strip().lower() or None,
    )
    if control_state.is_disabled:
        raise HTTPException(
            status_code=403,
            detail=control_state.disabled_reason or "Agent is disabled",
        )


async def _resolve_dev_rs256_user(claims: dict, db: AsyncSession) -> User:
    """Resolve a dev-login RS256 token to its user.

    Dev-login tokens carry user_id and space_id in claims (no agent_id).
    Same RS256 signing as agent tokens so they work with the MCP server.

    Security: enforces User.active, token_version, and space membership
    to match the same invariants as production auth paths.
    """
    from sqlalchemy import text

    if claims.get("typ") == "local-user":
        if os.getenv("AUTH_MODE", "local").lower() != "local":
            raise HTTPException(status_code=401, detail="Local authentication is disabled")
        if "ax-api" not in _claim_values(claims.get("aud")):
            raise HTTPException(status_code=401, detail="Invalid local session audience")
    elif os.getenv("ENABLE_LOCAL_TESTING", "false").lower() != "true":
        raise HTTPException(status_code=401, detail="Developer sessions are disabled")

    user_id = claims.get("user_id") or claims.get("sub")
    space_id = claims.get("space_id")

    if not user_id:
        raise HTTPException(status_code=401, detail="Missing user_id in token")

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user_id in token")

    # Load user — must be active (Finding 1: missing .active filter)
    result = await db.execute(
        select(User).where(User.id == user_uuid).where(User.active)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Validate token_version — reject revoked tokens (Finding 2)
    token_version = claims.get("token_version")
    if token_version is not None and user.token_version != token_version:
        raise HTTPException(status_code=401, detail="Token has been revoked")

    if space_id and str(space_id) != str(user.space_id):
        await verify_space_membership(db, user.id, uuid.UUID(str(space_id)))

    # Set RLS context after user validation (Finding 3: validate first)
    if space_id:
        await db.execute(
            text("SELECT set_config('app.current_space_id', :space_id, true)"),
            {"space_id": str(space_id)},
        )
        try:
            user.current_space_id = uuid.UUID(str(space_id))
        except (TypeError, ValueError):
            user.current_space_id = space_id

    logger.info("DEV_RS256_AUTH user_id=%s space_id=%s", user_id, space_id)
    return user


async def _resolve_ax_agent_user(token: str, claims: dict, db: AsyncSession) -> User:
    """Resolve a backend-minted aX agent token to the agent's owner user.

    Backend-minted RS256 tokens carry agent_id and space_id in claims.
    We look up the agent, find its owner, and set runtime attributes
    so RLS scopes to the correct space.

    SECURITY INVARIANTS:
    - JWT space_id MUST match agent's actual space_id (prevents cross-space access)
    - Agent identity is preserved via _agent_id/_agent_name on the user object
    - Admin fallback (when agent has no owner) is logged as security event
    """
    from sqlalchemy import text
    from ..models.agent import Agent

    # Use agent_id claim only — sub is now canonical format agent:<uuid> (AUTH-SPEC-001 §5)
    agent_id = claims.get("agent_id")
    space_id = claims.get("space_id")

    if not agent_id or not space_id:
        raise HTTPException(status_code=401, detail="Incomplete agent token claims")

    try:
        agent_uuid = uuid.UUID(str(agent_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid agent_id in token")

    try:
        space_uuid = uuid.UUID(str(space_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid space_id in token")

    # Set RLS context from JWT claims so agent lookup isn't blocked
    await db.execute(
        text("SELECT set_config('app.current_space_id', :space_id, true)"),
        {"space_id": str(space_uuid)},
    )

    result = await db.execute(
        select(Agent).where(Agent.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        logger.warning("AX_AUTH_AGENT_NOT_FOUND agent_id=%s space_id=%s", agent_id, space_id)
        raise HTTPException(status_code=401, detail="Agent not found")

    # SECURITY: Validate JWT space_id matches agent's actual space_id
    # This prevents a token minted for space A from being used in space B
    if str(agent.space_id) != str(space_uuid):
        logger.error(
            "AX_AUTH_SPACE_MISMATCH agent_id=%s token_space=%s agent_space=%s "
            "SECURITY: JWT space_id does not match agent's space_id",
            agent_id, space_uuid, agent.space_id,
        )
        raise HTTPException(status_code=403, detail="Agent space mismatch")

    await _enforce_agent_runtime_access(db, agent_id=agent.id, space_id=space_id)

    # Resolve owner user — if agent has no owner, use admin fallback with security logging
    if agent.user_id:
        user_result = await db.execute(
            select(User).where(User.id == agent.user_id).where(User.active)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            logger.error(
                "AX_AUTH_OWNER_NOT_FOUND agent_id=%s owner_user_id=%s space_id=%s "
                "SECURITY: Agent owner user does not exist or is inactive",
                agent_id, agent.user_id, space_id,
            )
            raise HTTPException(status_code=401, detail="Agent owner not found")
    else:
        # System agent (no owner) — look up any admin in the space
        # SECURITY WARNING: This is a fallback path. The admin user's identity
        # is used for DB session only — the agent's identity is preserved in
        # _agent_id and _agent_name attributes. Code must ALWAYS use session.agent_id
        # (not session.user.id) for agent attribution.
        logger.warning(
            "AX_AUTH_ADMIN_FALLBACK agent_id=%s space_id=%s "
            "SECURITY: Agent has no owner, falling back to space admin for DB session. "
            "Ensure all attribution uses session.agent_id, not session.user.id",
            agent_id, space_id,
        )
        admin_result = await db.execute(
            select(User)
            .join(SpaceMembership, SpaceMembership.user_id == User.id)
            .where(SpaceMembership.space_id == space_uuid)
            .where(SpaceMembership.role == "admin")
            .where(User.active)
            .limit(1)
        )
        user = admin_result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="No admin user in space")

    user._effective_space_id = str(space_uuid)
    user._agent_id = str(agent_uuid)
    user._agent_name = claims.get("agent_name")
    user._token_issuer = "ax-backend"
    user._principal_type = "agent"
    user._principal_id = str(agent_uuid)
    user._principal_agent_id = str(agent_uuid)
    user._principal_user_id = None
    logger.info(
        "AX_AUTH_RESOLVED user_id=%s agent_id=%s space_id=%s agent_has_owner=%s",
        user.id, agent_uuid, space_uuid, bool(agent.user_id),
    )
    return user


async def _resolve_exchange_jwt_user(claims: dict, db: AsyncSession) -> User:
    """Resolve user from an exchange-issued JWT (AUTH-SPEC-001).

    Exchange JWTs carry:
      - sub: user:<id> or agent:<id>
      - token_class: user_access | user_admin | agent_access
      - owner_user_id: the PAT owner
      - scope: space-separated scope string
      - src_credential_id: originating PAT
    """
    sub = claims.get("sub", "")
    token_class = claims.get("token_class")
    owner_user_id = claims.get("owner_user_id")

    # Validate canonical sub format (AUTH-SPEC-001 §5)
    if not sub.startswith(("user:", "agent:")):
        raise HTTPException(status_code=401, detail="Invalid sub format in exchange JWT")

    if not owner_user_id:
        raise HTTPException(status_code=401, detail="Exchange JWT missing owner_user_id")

    # Resolve the PAT owner
    try:
        user_uuid = uuid.UUID(owner_user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid owner_user_id in exchange JWT")

    result = await db.execute(select(User).where(User.id == user_uuid, User.active))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Exchange JWT owner not found")

    # Set runtime attributes
    user._effective_space_id = str(user.current_space_id or user.space_id)
    user._exchange_token_class = token_class
    user._exchange_scope = claims.get("scope", "")
    user._exchange_sub = sub
    user._token_issuer = "ax-backend"

    # For agent_access: set agent context — fail closed if agent missing/inactive.
    # The effective space must be the space that authorized the PAT exchange, not
    # necessarily the agent's home space: an agent may be shared into the PAT
    # owner's space through agent_space_access.
    if token_class == "agent_access":
        agent_id_str = claims.get("agent_id")
        if not agent_id_str:
            raise HTTPException(status_code=401, detail="Exchange JWT missing agent_id for agent_access")
        try:
            agent_uuid = uuid.UUID(agent_id_str)
        except (ValueError, TypeError):
            raise HTTPException(status_code=401, detail="Invalid agent_id in exchange JWT")

        authorized_space_id = claims.get("authorized_space_id")
        if authorized_space_id is None:
            # Backward-compatible fallback for exchange JWTs minted before this
            # claim existed. The source credential's space is the authorization
            # boundary that should have been embedded in the token.
            from app.models.credential import Credential

            src_credential_id = claims.get("src_credential_id")
            try:
                src_credential_uuid = uuid.UUID(str(src_credential_id))
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid source credential in exchange JWT",
                )

            credential_result = await db.execute(
                select(Credential.space_id).where(Credential.id == src_credential_uuid)
            )
            authorized_space_id = credential_result.scalar_one_or_none()
            if authorized_space_id is None:
                raise HTTPException(status_code=401, detail="Exchange JWT source credential not found")

        try:
            authorized_space_uuid = uuid.UUID(str(authorized_space_id))
        except (ValueError, TypeError):
            raise HTTPException(status_code=401, detail="Invalid authorized_space_id in exchange JWT")

        agent_result = await db.execute(
            select(Agent).where(Agent.id == agent_uuid)
        )
        agent = agent_result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=401, detail="Agent not found — may have been deleted after token was issued")
        if (getattr(agent, "status", "") or "").lower() not in ("active", ""):
            raise HTTPException(status_code=401, detail="Agent is inactive")

        from app.core.agent_space import agent_has_space_access

        if not await agent_has_space_access(db, agent_uuid, authorized_space_uuid):
            raise HTTPException(
                status_code=401,
                detail="Agent is not authorized for exchange JWT space",
            )

        user._agent_id = agent_id_str
        user._bound_agent_id = agent_id_str
        user._principal_type = "agent"
        user._agent_name = agent.name
        user._effective_space_id = str(authorized_space_uuid)

    logger.info(
        "EXCHANGE_JWT_RESOLVED sub=%s token_class=%s owner=%s",
        sub, token_class, owner_user_id,
    )
    return user


async def _resolve_user_from_bearer_token(
    token: str,
    db: AsyncSession,
    *,
    allow_agent_tokens: bool,
    request: Request | None = None,
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    # AUTH-SPEC-001 §3.1: PATs accepted ONLY at /auth/exchange.
    if token.startswith("axp_"):
        import os
        if os.environ.get("AX_ENFORCE_EXCHANGE", "true").lower() == "true":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "pat_not_allowed",
                    "message": "PATs cannot be used on business routes. Exchange for a JWT first via POST /auth/exchange.",
                },
            )
        # Legacy fallback (AX_ENFORCE_EXCHANGE=false) — will be removed
        from .credential_service import authenticate_credential
        from .agent_context import AgentTargetError, resolve_agent_target

        principal = await authenticate_credential(token, db)
        user = principal.user
        principal_space_id = principal.space_id
        user._effective_space_id = str(principal_space_id)
        user._credential_agent_scope = principal.agent_scope
        user._credential_allowed_agent_ids = principal.allowed_agent_ids
        user._bound_agent_id = str(principal.bound_agent_id) if principal.bound_agent_id else None

        if request is not None:
            try:
                user_home_space_id = getattr(principal.user, "space_id", None) or principal_space_id
                target_space_id = principal_space_id
                agent_target = await resolve_agent_target(
                    db=db,
                    agent_id_header=request.headers.get("x-agent-id"),
                    agent_name_header=request.headers.get("x-agent-name"),
                    user_id=principal.user.id,
                    space_id=target_space_id,
                    user_home_space_id=user_home_space_id,
                    agent_scope=principal.agent_scope,
                    allowed_agent_ids=principal.allowed_agent_ids,
                    credential_id=principal.credential_id,
                )
            except AgentTargetError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

            if agent_target:
                target_agent_id, target_agent_name, target_space_id = agent_target
                user._agent_id = str(target_agent_id)
                user._agent_name = target_agent_name
                user._effective_space_id = str(target_space_id)
                if principal.agent_scope == "unbound":
                    user._bound_agent_id = str(target_agent_id)
                    user._credential_agent_scope = "agents"
                    user._credential_allowed_agent_ids = [target_agent_id]
            elif principal.bound_agent_id:
                user._agent_id = str(principal.bound_agent_id)

        await _enforce_agent_runtime_access(
            db,
            agent_id=getattr(user, "_agent_id", None) or principal.bound_agent_id,
            space_id=user._effective_space_id,
        )
        return user

    if _looks_like_jwt(token):
        try:
            if os.getenv("AUTH_MODE", "local").lower() == "local":
                raise HTTPException(status_code=401, detail="Invalid token for Cognito")
            claims = await _decode_token(token)
            user = await _resolve_user(claims, db, access_token=token)
            return await _apply_owned_agent_target(user, db, request)
        except HTTPException as exc:
            if "Invalid token" not in str(exc.detail) and "Token key not found" not in str(exc.detail):
                logger.info("AX_AUTH_COGNITO_REJECT detail=%s (not falling through)", exc.detail)
                raise
            logger.info("AX_AUTH_COGNITO_MISS detail=%s (falling through to backend JWT)", exc.detail)
        except Exception as exc:
            logger.info("AX_AUTH_COGNITO_ERROR %s: %s (falling through)", type(exc).__name__, exc)

        # Try backend-minted RS256 token (aX agent auth)
        try:
            from .ax_jwt import get_jwks
            unverified = jwt.get_unverified_claims(token)
            token_iss = unverified.get("iss")
            logger.info("AX_AUTH_FALLBACK iss=%s sub=%s", token_iss, unverified.get("sub", "?")[:20])
            if token_iss == "ax-backend":
                jwks = get_jwks()
                headers = jwt.get_unverified_headers(token)
                kid = headers.get("kid")
                jwks_kids = [k.get("kid") for k in jwks.get("keys", [])]
                logger.info("AX_AUTH_KEY_MATCH token_kid=%s jwks_kids=%s", kid, jwks_kids)
                key = None
                for k in jwks.get("keys", []):
                    if k.get("kid") == kid:
                        key = k
                        break
                if key:
                    claims = jwt.decode(
                        token, key, algorithms=["RS256"],
                        issuer="ax-backend",
                        options={"verify_aud": False},
                    )
                    logger.info("AX_AUTH_DECODED agent_id=%s space_id=%s user_id=%s", claims.get("agent_id", claims.get("sub", "?"))[:20], claims.get("space_id", "?"), claims.get("user_id", "?")[:20] if claims.get("user_id") else "none")
                    # Dev-login RS256 tokens carry user_id (not agent_id)
                    if claims.get("user_id") and not claims.get("agent_id"):
                        return await _resolve_dev_rs256_user(claims, db)
                    # Exchange-issued JWTs carry src_credential_id + token_class (AUTH-SPEC-001)
                    if claims.get("src_credential_id") and claims.get("token_class") in ("user_access", "user_admin", "agent_access"):
                        user = await _resolve_exchange_jwt_user(claims, db)
                        if claims.get("token_class") in ("user_access", "user_admin"):
                            user = await _apply_owned_agent_target(user, db, request)
                            if allow_agent_tokens and _is_exchange_mcp_route_agent_session(
                                claims=claims,
                                request=request,
                                targeted_agent_id=getattr(user, "_agent_id", None),
                            ):
                                _mark_user_as_route_agent_principal(user)
                        return user
                    return await _resolve_ax_agent_user(token, claims, db)
                else:
                    logger.warning("AX_AUTH_NO_KEY_MATCH token_kid=%s available=%s", kid, jwks_kids)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("AX_AUTH_BACKEND_JWT_FAILED %s: %s", type(exc).__name__, exc)

    if token.startswith("axat_"):
        return await _resolve_cached_mcp_user(token, db)

    return await _resolve_legacy_user(token, db, allow_agent_tokens=allow_agent_tokens)


def _claims_match_frontend_user_session(claims: dict[str, Any]) -> bool:
    """Return True when Cognito claims represent an interactive frontend user."""
    token_aud = claims.get("aud") or claims.get("client_id")
    token_auds = token_aud if isinstance(token_aud, list) else [token_aud] if token_aud else []
    if FRONTEND_AUDIENCE:
        return FRONTEND_AUDIENCE in token_auds

    # Fallback for tests/dev when frontend audience is not configured:
    # require human-identifying claims so thin M2M tokens fail closed.
    return bool(claims.get("email") or _extract_claim_username(claims))


async def _resolve_admin_human_user_from_bearer_token(
    token: str,
    db: AsyncSession,
) -> User:
    """Resolve only human-admin session token types for admin endpoints.

    Accepted:
      - Cognito frontend user JWTs
      - backend-minted dev RS256 user JWTs (user_id, no agent_id)
      - legacy user access tokens

    Rejected:
      - PAT/service credentials (`axp_*`)
      - cached MCP tokens (`axat_*`)
      - backend-issued agent JWTs
    """
    if not token:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    if token.startswith(("axp_", "axat_")):
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    if _looks_like_jwt(token):
        try:
            claims = await _decode_token(token)
            if not _claims_match_frontend_user_session(claims):
                raise HTTPException(status_code=401, detail="Could not validate credentials")
            return await _resolve_user(claims, db, access_token=token)
        except HTTPException as exc:
            if "Invalid token" not in str(exc.detail) and "Token key not found" not in str(exc.detail):
                logger.info("AX_ADMIN_AUTH_COGNITO_REJECT detail=%s (not falling through)", exc.detail)
                raise
            logger.info("AX_ADMIN_AUTH_COGNITO_MISS detail=%s (falling through to backend JWT)", exc.detail)
        except Exception as exc:
            logger.info("AX_ADMIN_AUTH_COGNITO_ERROR %s: %s (falling through)", type(exc).__name__, exc)

        try:
            from .ax_jwt import get_jwks

            unverified = jwt.get_unverified_claims(token)
            if unverified.get("iss") == "ax-backend":
                jwks = get_jwks()
                headers = jwt.get_unverified_headers(token)
                kid = headers.get("kid")
                key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
                if not key:
                    raise HTTPException(status_code=401, detail="Could not validate credentials")

                claims = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256"],
                    issuer="ax-backend",
                    options={"verify_aud": False},
                )
                if claims.get("user_id") and not claims.get("agent_id"):
                    return await _resolve_dev_rs256_user(claims, db)
                raise HTTPException(status_code=401, detail="Could not validate credentials")
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("AX_ADMIN_AUTH_BACKEND_JWT_FAILED %s: %s", type(exc).__name__, exc)

    return await _resolve_legacy_user(token, db, allow_agent_tokens=False)


async def _provision_new_user(
    claims: dict, db: AsyncSession,
    email: str | None, preferred_username: str | None,
    github_id: str | None, cognito_sub: str | None,
) -> User:
    """Create a new user with a personal workspace (JIT provisioning from Cognito)."""
    # Debug: log all claim keys so we can see what Cognito sends
    claim_keys = sorted(claims.keys()) if claims else []
    logger.debug(f"PROVISION_DEBUG claims_keys={claim_keys}")
    for key in ("preferred_username", "custom:github_username", "nickname", "cognito:username", "username", "email", "name", "sub"):
        logger.debug(f"PROVISION_DEBUG claim[{key}]={claims.get(key)!r}")

    username = await _ensure_unique_provision_username(
        db,
        _derive_provision_username(claims, email=email, cognito_sub=cognito_sub),
        cognito_sub=cognito_sub,
    )
    # Keep the lookup email deterministic for username-only Cognito tokens.
    # The resolver searches the synthetic sub-based fallback email when Cognito
    # omits email, so provisioning must store that same value even when the
    # display username comes from preferred_username/cognito:username claims.
    _fallback_username, fallback_email = _derive_cognito_fallback_identity(
        email=email,
        preferred_username=preferred_username,
        cognito_sub=cognito_sub,
    )
    user_email = fallback_email
    full_name = claims.get("name") or username
    avatar = claims.get("picture") or ""

    logger.info(f"Auto-provisioning Cognito user: username={username}, email={user_email}, derived_from_claims=True")

    # Create personal workspace
    personal_org_name = f"{username}'s Workspace"
    personal_org_slug = f"{username.lower()}-workspace"

    # Ensure slug is unique
    slug_check = await db.execute(
        select(Space).where(Space.slug == personal_org_slug)
    )
    if slug_check.scalar_one_or_none():
        personal_org_slug = f"{username.lower()}-{uuid.uuid4().hex[:8]}"

    personal_org = Space(
        id=uuid.uuid4(),
        name=personal_org_name,
        slug=personal_org_slug,
        description=f"Personal workspace for {full_name}",
        visibility="private",
        created_by=None,
    )
    db.add(personal_org)
    await db.flush()

    # Create user
    user = User(
        id=uuid.uuid4(),
        space_id=personal_org.id,
        current_space_id=personal_org.id,
        email=user_email,
        password_hash="",
        full_name=full_name,
        username=username,
        role="user",
        active=True,
        token_version=0,
        github_id=github_id,
        github_username=preferred_username or username,
        github_avatar_url=avatar,
        auth_provider="github",
    )
    db.add(user)
    await db.flush()

    # Link org to creator
    personal_org.created_by = user.id

    # Create space membership (admin of own workspace)
    membership = SpaceMembership(
        user_id=user.id,
        space_id=personal_org.id,
        role="admin",
    )
    db.add(membership)

    await record_user_audit_event(
        db,
        user_id=user.id,
        event_type="user.created",
        space_id=personal_org.id,
        resource_type="user",
        resource_id=user.id,
        metadata={
            "username": username,
            "email": user_email,
            "email_domain": email_domain(user_email),
            "github_username": user.github_username,
            "auth_provider": user.auth_provider,
        },
    )
    await record_user_audit_event(
        db,
        user_id=user.id,
        event_type="space.joined",
        space_id=personal_org.id,
        resource_type="space",
        resource_id=personal_org.id,
        metadata={"role": "admin", "reason": "personal_workspace_created"},
    )

    if get_settings().auto_join_nexus_on_signup:
        # Optional public-lobby onboarding. Keep disabled by default so fresh
        # users land in their own private workspace first.
        DEFAULT_PUBLIC_ORG_ID = uuid.UUID("00000000-0000-0000-0000-314159265359")
        public_org_result = await db.execute(
            select(Space).where(Space.id == DEFAULT_PUBLIC_ORG_ID)
        )
        if public_org_result.scalar_one_or_none():
            public_membership = SpaceMembership(
                user_id=user.id,
                space_id=DEFAULT_PUBLIC_ORG_ID,
                role="member",
            )
            db.add(public_membership)
            await record_user_audit_event(
                db,
                user_id=user.id,
                event_type="space.joined",
                space_id=DEFAULT_PUBLIC_ORG_ID,
                resource_type="space",
                resource_id=DEFAULT_PUBLIC_ORG_ID,
                metadata={"role": "member", "reason": "auto_join_nexus_on_signup"},
            )
            logger.info(f"Added {username} to The Nexus public org")

    await db.commit()
    await db.refresh(user)
    logger.info(f"Provisioned new user: id={user.id}, username={username}, workspace={personal_org.id}")
    await send_signup_alert(user)
    return user


async def get_current_user_from_token(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Get current user from Cognito JWT or legacy access token."""
    return await _resolve_user_from_bearer_token(
        token,
        db,
        allow_agent_tokens=False,
        request=request,
    )


async def get_admin_user_from_token(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Get current admin user from human-only session token types."""
    return await _resolve_admin_human_user_from_bearer_token(token, db)


async def get_user_from_jwt_or_mcp(
    request: Request,
    token: str | None = Depends(oauth2_scheme_optional),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Get user from Cognito JWT, cached MCP token, or legacy JWT."""
    return await _resolve_user_from_bearer_token(
        token,
        db,
        allow_agent_tokens=True,
        request=request,
    )
