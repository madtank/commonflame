"""Unified credential service for principal-based auth.

Handles user PATs (axp_u_*) and future system account secrets (axp_s_*).
Credentials authenticate a principal, NOT an agent — agents are resources.
"""

import logging
import secrets
import string
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select, text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..models.access_request import AccessRequest
from ..models.credential import Credential
from ..models.user import User

logger = logging.getLogger(__name__)

# Argon2id hasher — separate from bcrypt in security.py
_ph = PasswordHasher()

# Token prefix → (principal_type, credential_type)
_TOKEN_TYPE_MAP = {
    "u": ("user", "pat"),
    "a": ("user", "pat"),          # agent-bound PAT (same principal_type, bound via bound_agent_id)
    "s": ("system_account", "service_secret"),
}

_BASE62 = string.ascii_letters + string.digits
_BASE62_SET = frozenset(_BASE62)
_KEY_ID_MIN = 6
_KEY_ID_MAX = 16

# Valid scopes for Phase 1 — allowlist
VALID_SCOPES = frozenset(["api:read", "api:write"])

# --- AUTH-SPEC-001 v3 Exchange Constants ---

# PAT class → allowed token classes (§4.1)
# System account PATs (axp_s_) excluded from exchange in Phase 1.
PAT_CLASS_EXCHANGE_MATRIX = {
    "u": {"user_access", "user_admin"},    # User bootstrap PAT
    "a": {"agent_access"},                  # Agent-bound PAT
}

# Token class → max TTL in seconds (§9.2)
TOKEN_CLASS_MAX_TTL = {
    "user_access": 900,     # 15 minutes
    "user_admin": 300,      # 5 minutes
    "agent_access": 900,    # 15 minutes
}

# Token class → allowed scopes (§6). No cross-contamination.
# Phase 3: read/write split. Unsplit scopes (e.g. "messages") accepted as read+write for backward compat.
TOKEN_CLASS_SCOPE_ALLOWLIST = {
    "user_access": frozenset({
        "messages", "messages:read", "messages:write",
        "tasks", "tasks:read", "tasks:write",
        "context", "context:read", "context:write",
        "agents", "agents:read", "agents:write",
        "spaces", "spaces:read",
        "search",
    }),
    "user_admin": frozenset({
        "agents.create", "agents.bind",
        "credentials.issue.agent", "credentials.revoke",
        "delegations.manage",
    }),
    "agent_access": frozenset({
        "messages", "messages:read", "messages:write",
        "tasks", "tasks:read", "tasks:write",
        "context", "context:read", "context:write",
        "agents", "agents:read", "agents:write",
        "spaces", "spaces:read",
        "search",
    }),
}

# Scope expansion: unsplit scope → read+write (backward compat)
SCOPE_EXPANSION = {
    "messages": {"messages:read", "messages:write"},
    "tasks": {"tasks:read", "tasks:write"},
    "context": {"context:read", "context:write"},
    "agents": {"agents:read", "agents:write"},
    "spaces": {"spaces:read"},
    "search": {"search"},
}

# Scope → MCP tools_allowed mapping (for scope-only exchange JWTs)
SCOPE_TO_TOOLS = {
    "messages:read": ["messages"],
    "messages:write": ["messages"],
    "tasks:read": ["tasks"],
    "tasks:write": ["tasks"],
    "context:read": ["context"],
    "context:write": ["context"],
    "agents:read": ["agents"],
    "agents:write": ["agents"],
    "spaces:read": ["spaces"],
    "search": ["search"],
    # Unsplit scopes map to full tool access
    "messages": ["messages"],
    "tasks": ["tasks"],
    "context": ["context"],
    "agents": ["agents"],
    "spaces": ["spaces"],
}


def _generate_key_id(length: int = 10) -> str:
    """Generate a 10-12 char base62 key_id for public lookup."""
    return "".join(secrets.choice(_BASE62) for _ in range(length))


def _generate_secret() -> str:
    """Generate 48 bytes, base64url encoded."""
    return urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")


def hash_credential_secret(secret: str) -> str:
    return _ph.hash(secret)


def verify_credential_secret(secret: str, hash: str) -> bool:
    try:
        return _ph.verify(hash, secret)
    except VerifyMismatchError:
        return False


def parse_token(token: str) -> tuple[str, str, str] | None:
    """Parse axp_<type>_<key_id>.<secret> → (type_char, key_id, secret) or None.

    Strict validation: exactly one accepted format, no best-effort parsing.
    """
    if not token.startswith("axp_"):
        return None
    try:
        # axp_u_KEY_ID.SECRET
        rest = token[4:]  # u_KEY_ID.SECRET
        if len(rest) < 4:  # minimum: type + _ + 1 char key + . + 1 char secret
            return None
        type_char = rest[0]
        if rest[1] != "_":
            return None
        if type_char not in _TOKEN_TYPE_MAP:
            return None
        key_and_secret = rest[2:]  # KEY_ID.SECRET
        dot_idx = key_and_secret.index(".")
        key_id = key_and_secret[:dot_idx]
        secret = key_and_secret[dot_idx + 1:]
        # Reject empty key_id or secret
        if not key_id or not secret:
            return None
        # Validate key_id: base62 only, bounded length
        if not (_KEY_ID_MIN <= len(key_id) <= _KEY_ID_MAX):
            return None
        if not all(c in _BASE62_SET for c in key_id):
            return None
        return (type_char, key_id, secret)
    except (IndexError, ValueError):
        return None


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Normalized auth context from credential authentication.

    User owns the token. Agent scope limits where it can be used.

    An agent-bound PAT is the agent's credential. The user creates and manages
    it, but when used with the X-Agent-Id header, the effective identity IS
    the agent. Everything done with an agent-bound PAT is done as the agent.
    """
    principal_type: str          # 'user' | 'system_account'
    principal_id: UUID
    space_id: UUID
    credential_id: UUID
    scopes: list[str]
    agent_scope: str             # 'all' | 'user' | 'agents'
    allowed_agent_ids: list[UUID] | None  # which agents this PAT is bound to (not "become")
    bound_agent_id: UUID | None  # agent policy object, resolved at auth time
    user: User                   # resolved user principal (the authenticated identity)
    audience: str = "cli"        # cli | mcp | both — PAT's intended resource target


VALID_AGENT_SCOPES = frozenset(["all", "user", "agents", "unbound"])


def _parse_access_always_approve_floor() -> set[str]:
    """Parse settings.access_always_approve into lower-cased emails/github IDs."""
    raw = getattr(get_settings(), "access_always_approve", "") or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


async def _credential_principal_is_access_approved(db: AsyncSession, user: User) -> bool:
    """Return True if the PAT owner's human account is allowed through the gate.

    PAT authentication must not auto-create waitlist rows or send approval emails;
    it should only honor the same approved/floor decisions that let a user login.
    """
    settings = get_settings()
    if not getattr(settings, "waitlist_enabled", False):
        return True

    norm_email = (getattr(user, "email", None) or "").strip().lower()
    github_id = getattr(user, "github_id", None)
    floor = _parse_access_always_approve_floor()
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

    result = await db.execute(
        select(AccessRequest)
        .where(AccessRequest.status == "approved")
        .where(or_(*conditions))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row is not None and getattr(row, "status", None) == "approved"


async def create_credential(
    db: AsyncSession,
    *,
    space_id: UUID,
    principal_type: str,
    principal_id: UUID,
    credential_type: str,
    name: str | None = None,
    scopes: list[str] | None = None,
    agent_scope: str = "all",
    allowed_agent_ids: list[str] | None = None,
    bound_agent_id: UUID | None = None,
    created_by_principal_type: str | None = None,
    created_by_principal_id: UUID | None = None,
    expires_at: datetime | None = None,
    audience: str = "cli",
) -> tuple[str, Credential]:
    """Create a new credential. Returns (plaintext_token, Credential).

    The plaintext token is shown ONCE and never stored.
    """
    # Validate agent_scope
    if agent_scope not in VALID_AGENT_SCOPES:
        raise ValueError(f"Invalid agent_scope: {agent_scope}. Allowed: {VALID_AGENT_SCOPES}")
    if agent_scope == "agents" and not allowed_agent_ids:
        raise ValueError("agent_scope='agents' requires non-empty allowed_agent_ids")
    if agent_scope == "unbound" and allowed_agent_ids:
        raise ValueError("agent_scope='unbound' cannot have allowed_agent_ids — it binds on first use")
    if agent_scope not in ("agents", "unbound") and allowed_agent_ids:
        raise ValueError(f"allowed_agent_ids only valid with agent_scope='agents', got '{agent_scope}'")

    # Validate scopes against allowlist
    effective_scopes = scopes or ["api:read", "api:write"]
    invalid_scopes = set(effective_scopes) - VALID_SCOPES
    if invalid_scopes:
        raise ValueError(f"Invalid scopes: {invalid_scopes}. Allowed: {VALID_SCOPES}")

    # Use axp_a_ prefix for agent-targeting PATs, axp_u_ for user-only PATs.
    # Exchange validation treats agent_scope=agents/unbound as agent PAT class;
    # the visible prefix must match that class so clients choose agent_access.
    if bound_agent_id is not None or agent_scope in ("agents", "unbound"):
        type_char = "a"
    elif principal_type == "user":
        type_char = "u"
    else:
        type_char = "s"
    key_id = _generate_key_id()
    secret = _generate_secret()
    secret_hash = hash_credential_secret(secret)
    full_token = f"axp_{type_char}_{key_id}.{secret}"

    cred = Credential(
        space_id=space_id,
        principal_type=principal_type,
        principal_id=principal_id,
        credential_type=credential_type,
        key_id=key_id,
        secret_hash=secret_hash,
        name=name,
        scopes=effective_scopes,
        agent_scope=agent_scope,
        allowed_agent_ids=allowed_agent_ids,
        bound_agent_id=bound_agent_id,
        created_by_principal_type=created_by_principal_type,
        created_by_principal_id=created_by_principal_id,
        expires_at=expires_at,
        audience=audience,
    )
    db.add(cred)
    await db.flush()
    logger.info("CRED_CREATED key_id=%s principal=%s/%s space=%s", key_id, principal_type, principal_id, space_id)
    return (full_token, cred)


async def authenticate_credential(
    token: str,
    db: AsyncSession,
) -> AuthenticatedPrincipal:
    """Authenticate an axp_* credential token.

    Two-phase DB access:
      Phase 1 (privileged): Look up credential by key_id, verify secret
      Phase 2 (scoped): Resolve user from principal_id after dropping privilege

    Raises HTTPException(401) on any failure.
    """
    from fastapi import HTTPException

    parsed = parse_token(token)
    if parsed is None:
        raise HTTPException(status_code=401, detail="Invalid credential format")

    type_char, key_id, secret = parsed

    # Phase 1: Privileged lookup — credential table is RLS-protected
    await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
    try:
        result = await db.execute(
            select(Credential).where(
                Credential.key_id == key_id,
                Credential.revoked_at.is_(None),
            )
        )
        cred = result.scalar_one_or_none()
    finally:
        # Drop privilege immediately
        await db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))

    if cred is None:
        logger.info("CRED_AUTH_FAIL key_id=%s reason=not_found_or_revoked", key_id)
        raise HTTPException(status_code=401, detail="Invalid or revoked credential")

    # Check expiration
    if cred.expires_at and cred.expires_at < datetime.now(timezone.utc):
        logger.info("CRED_AUTH_FAIL key_id=%s reason=expired", key_id)
        raise HTTPException(status_code=401, detail="Credential expired")

    # Verify secret (Argon2id, constant-time)
    if not verify_credential_secret(secret, cred.secret_hash):
        logger.info("CRED_AUTH_FAIL key_id=%s reason=bad_secret", key_id)
        raise HTTPException(status_code=401, detail="Invalid credential")

    # Phase 2: Resolve user (normal session, no privilege)
    user_result = await db.execute(
        select(User).where(User.id == cred.principal_id, User.active)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        logger.info("CRED_AUTH_FAIL key_id=%s reason=user_not_found principal_id=%s", key_id, cred.principal_id)
        raise HTTPException(status_code=401, detail="Principal not found")

    if not await _credential_principal_is_access_approved(db, user):
        logger.info(
            "CRED_AUTH_FAIL key_id=%s reason=user_not_approved principal_id=%s email=%s",
            key_id,
            cred.principal_id,
            getattr(user, "email", None),
        )
        raise HTTPException(status_code=401, detail="Principal is not approved for access")

    # Resolve bound agent — fail closed if agent is gone or inactive
    bound_agent_id = None
    if cred.bound_agent_id:
        from ..models.agent import Agent
        from ..core.redis_client import redis_client
        from ..services.agent_control_service import AgentControlService

        agent_result = await db.execute(
            select(Agent).where(Agent.id == cred.bound_agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            logger.info("CRED_AUTH_FAIL key_id=%s reason=bound_agent_not_found agent_id=%s", key_id, cred.bound_agent_id)
            raise HTTPException(status_code=401, detail="Bound agent not found or deleted")
        if (getattr(agent, "status", "") or "").lower() != "active":
            logger.info("CRED_AUTH_FAIL key_id=%s reason=bound_agent_inactive agent_id=%s status=%s", key_id, cred.bound_agent_id, getattr(agent, "status", None))
            raise HTTPException(status_code=401, detail="Bound agent is inactive")

        control_state = await AgentControlService(redis_client).get_control_state(
            agent_id=agent.id,
            space_id=agent.space_id,
            agent_slug=(agent.name or "").strip().lower() or None,
        )
        if control_state.is_disabled:
            logger.info("CRED_AUTH_FAIL key_id=%s reason=bound_agent_disabled agent_id=%s scopes=%s", key_id, cred.bound_agent_id, control_state.disabled_by)
            raise HTTPException(status_code=401, detail=control_state.disabled_reason or "Bound agent is disabled")
        bound_agent_id = cred.bound_agent_id

    # Fire-and-forget: update last_used_at
    try:
        cred.last_used_at = datetime.now(timezone.utc)
        await db.flush()
    except Exception:
        pass  # Non-critical

    allowed = None
    if cred.allowed_agent_ids:
        allowed = [UUID(str(aid)) for aid in cred.allowed_agent_ids]

    logger.info("CRED_AUTH_OK key_id=%s principal=%s/%s space=%s bound_agent=%s scope=%s", key_id, cred.principal_type, cred.principal_id, cred.space_id, bound_agent_id, cred.agent_scope)
    return AuthenticatedPrincipal(
        principal_type=cred.principal_type,
        principal_id=cred.principal_id,
        space_id=cred.space_id,
        credential_id=cred.id,
        scopes=cred.scopes or [],
        agent_scope=cred.agent_scope or "all",
        allowed_agent_ids=allowed,
        bound_agent_id=bound_agent_id,
        user=user,
        audience=getattr(cred, "audience", None) or "cli",
    )


async def revoke_credential(
    db: AsyncSession,
    credential_id: UUID,
    *,
    principal_id: UUID | None = None,
) -> bool:
    """Soft-revoke a credential by setting revoked_at. Returns True if found.

    When principal_id is provided, enforces ownership at the application level
    (defense-in-depth, not relying solely on RLS).
    """
    query = select(Credential).where(Credential.id == credential_id)
    if principal_id is not None:
        query = query.where(Credential.principal_id == principal_id)
    result = await db.execute(query)
    cred = result.scalar_one_or_none()
    if cred is None:
        return False
    cred.revoked_at = datetime.now(timezone.utc)
    await db.flush()
    logger.info("CRED_REVOKED credential_id=%s key_id=%s", credential_id, cred.key_id)
    return True


async def list_credentials(
    db: AsyncSession,
    principal_type: str,
    principal_id: UUID,
) -> list[Credential]:
    """List credentials for a principal (metadata only, no secrets)."""
    result = await db.execute(
        select(Credential).where(
            Credential.principal_type == principal_type,
            Credential.principal_id == principal_id,
        ).order_by(Credential.created_at.desc())
    )
    return list(result.scalars().all())
