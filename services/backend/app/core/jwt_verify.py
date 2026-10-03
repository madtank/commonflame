"""Waystation JWT verification and authenticated principal dependencies."""
import logging
import os
import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth_config import builtin_auth_enabled
from .authorization import verify_space_membership
from .database import get_db_session
from .redis_client import redis_client
from ..models.agent import Agent
from ..models.space_membership import SpaceMembership
from ..models.user import User
from ..services.agent_control_service import AgentControlService

logger = logging.getLogger(__name__)
agent_control_service = AgentControlService(redis_client)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/local/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="auth/local/login", auto_error=False)


def _decode_backend_token(token: str) -> dict:
    from .ax_jwt import get_jwks, get_issuer
    try:
        header = jwt.get_unverified_headers(token)
        key = next((key for key in get_jwks()["keys"] if key.get("kid") == header.get("kid")), None)
        if key is None:
            raise HTTPException(status_code=401, detail="Could not validate credentials")
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=get_issuer(),
                            options={"verify_aud": False, "require_exp": True, "require_sub": True})
        audiences = _claim_values(claims.get("aud"))
        public_mcp = os.getenv("AX_MCP_RESOURCE_URL", "http://localhost:3000/mcp")
        if not audiences.intersection({"ax-api", "ax-mcp", public_mcp}):
            raise HTTPException(status_code=401, detail="Invalid token audience")
        return claims
    except (JWTError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Could not validate credentials") from exc


def _enforce_oauth_request_scope(claims: dict, request: Request | None):
    if request is None or claims.get("token_class") != "agent_access":
        return
    source = str(claims.get("src_credential_id", ""))
    if not source.startswith(("authorization_code:", "device:", "refresh:")):
        return
    scopes = set(str(claims.get("scope", "")).split())
    action = "read" if request.method in {"GET", "HEAD", "OPTIONS"} else "write"
    if f"ax-api/mcp:{action}" in scopes:
        return
    segments = request.url.path.strip("/").split("/")
    category = next((part for part in segments if part in {"messages", "tasks", "context", "spaces", "agents"}), None)
    if category and f"{category}.{action}" in scopes:
        return
    if request.url.path == "/auth/me" and action == "read" and scopes:
        return
    raise HTTPException(status_code=403, detail={"error": "insufficient_scope"},
                        headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'})


async def _resolve_user_from_bearer_token(token: str, db: AsyncSession, *,
                                           allow_agent_tokens: bool, request: Request | None = None) -> User:
    if not token or token.startswith("axp_"):
        raise HTTPException(status_code=401, detail="Exchange credentials for a scoped access token first")
    if _looks_like_jwt(token):
        claims = _decode_backend_token(token)
        _enforce_oauth_request_scope(claims, request)
        if claims.get("user_id") and not claims.get("agent_id"):
            return await _resolve_dev_rs256_user(claims, db)
        if claims.get("src_credential_id") and claims.get("token_class") in {"user_access", "user_admin", "agent_access"}:
            if claims.get("token_class") == "agent_access" and not allow_agent_tokens:
                raise HTTPException(status_code=401, detail="Human sign-in required")
            return await _resolve_exchange_jwt_user(claims, db)
        if not allow_agent_tokens:
            raise HTTPException(status_code=401, detail="Human sign-in required")
        return await _resolve_ax_agent_user(token, claims, db)
    if token.startswith("axat_") and allow_agent_tokens:
        return await _resolve_cached_mcp_user(token, db)
    # Legacy HMAC tokens remain available only through the explicit compatibility flag.
    if os.getenv("ENABLE_LEGACY_JWT", "false").lower() == "true":
        return await _resolve_legacy_user(token, db, allow_agent_tokens=allow_agent_tokens)
    raise HTTPException(status_code=401, detail="Could not validate credentials")


async def _resolve_admin_human_user_from_bearer_token(token: str, db: AsyncSession) -> User:
    return await _resolve_user_from_bearer_token(token, db, allow_agent_tokens=False)


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
        if not builtin_auth_enabled():
            raise HTTPException(status_code=401, detail="Built-in authentication is disabled")
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

    if space_id:
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
    from .ax_jwt import get_issuer
    user._token_issuer = get_issuer()
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
    from .ax_jwt import get_issuer
    user._token_issuer = get_issuer()

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
        if str(claims.get("src_credential_id", "")).startswith(("authorization_code:", "device:", "refresh:")):
            if str(agent.user_id) != str(user.id):
                raise HTTPException(status_code=401, detail="Agent sponsor binding changed")
            await verify_space_membership(db, user.id, authorized_space_uuid)
        await _enforce_agent_runtime_access(db, agent_id=agent.id, space_id=authorized_space_uuid)

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



async def get_current_user_from_token(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Get current user from Waystation access token."""
    return await _resolve_user_from_bearer_token(
        token,
        db,
        allow_agent_tokens=True,
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
    """Get user from Waystation JWT or explicitly enabled legacy token."""
    return await _resolve_user_from_bearer_token(
        token,
        db,
        allow_agent_tokens=True,
        request=request,
    )
