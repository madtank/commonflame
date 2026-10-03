"""
Row-Level Security (RLS) dependencies for FastAPI

Provides dependencies that combine authentication with PostgreSQL session
context initialization for proper RLS policy enforcement.

RLS filters by space_id, which enforces space-level isolation.

Usage (RECOMMENDED - secure by default):
    from app.core.rls import get_secure_session, SecureSession

    @router.get("/messages")
    async def get_messages(session: SecureSession):
        # session.db has RLS context set, session.user is authenticated
        # All queries automatically filtered to user's space
        result = await session.db.execute(select(Message))
        ...

Legacy Usage (manual RLS - avoid in new code):
    from app.core.rls import init_rls_for_user

    @router.get("/messages")
    async def get_messages(
        current_user: User = Depends(get_current_user_from_token),
        db: AsyncSession = Depends(get_db_session),
    ):
        rls = await init_rls_for_user(db, current_user)
        ...
"""

import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from .database import get_db_session, set_rls_context
from ..models.user import User

logger = logging.getLogger(__name__)

# OAuth2 scheme for token extraction
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def _claim_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, list):
        return {str(item) for item in value if item}
    return set()


def _configured_client_ids(raw: str | None) -> set[str]:
    return {
        client_id.strip()
        for client_id in (raw or "").split(",")
        if client_id.strip()
    }


def _claims_client_ids(claims: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    values.update(_claim_values(claims.get("client_id")))
    values.update(_claim_values(claims.get("azp")))
    values.update(_claim_values(claims.get("aud")))
    return values


def _is_mcp_route_agent_session(
    *,
    claims: dict[str, Any],
    request: Request | None,
    targeted_agent_id: str | None,
) -> bool:
    """Return whether a Cognito session should author as the route agent.

    The browser SPA and MCP OAuth both use Cognito user tokens, but they are
    different security paths:
    - frontend quick-action widgets are viewer-private and remain user-authored;
    - `/mcp/agents/{agent}` OAuth sessions are explicit agent-route sessions
      after `_apply_owned_agent_target` verifies the user can target that agent.
    """
    if not targeted_agent_id or request is None:
        return False
    if not (request.headers.get("x-agent-id") or request.headers.get("x-agent-name")):
        return False

    from ..core.auth_config import FRONTEND_AUDIENCE, MCP_AUDIENCE, MCP_M2M_AUDIENCE

    client_ids = _claims_client_ids(claims)
    frontend_ids = _configured_client_ids(FRONTEND_AUDIENCE)
    mcp_ids = _configured_client_ids(MCP_AUDIENCE) | _configured_client_ids(
        MCP_M2M_AUDIENCE
    )

    if client_ids & frontend_ids:
        return False
    return bool(client_ids & mcp_ids)


@dataclass
class RLSSession:
    """
    Container for RLS-enabled database session with user context.

    Attributes:
        db: AsyncSession with RLS context already set
        user: The authenticated user
        space_id: The effective space ID for this request
    """
    db: AsyncSession
    user: User
    space_id: str


async def get_rls_session(
    db: AsyncSession = Depends(get_db_session),
) -> AsyncGenerator[RLSSession, None]:
    """
    Dependency that provides an RLS-enabled database session.

    This dependency:
    1. Gets the authenticated user from the request
    2. Sets PostgreSQL session variables for RLS
    3. Returns a combined session+user object

    Note: This dependency requires the user to be injected separately
    since we can't have circular imports with auth.py.

    Usage in endpoints:
        from app.core.jwt_verify import get_current_user_from_token

        @router.get("/secure-data")
        async def get_data(
            current_user: User = Depends(get_current_user_from_token),
            db: AsyncSession = Depends(get_db_session),
        ):
            # Manually set RLS context
            await set_rls_context(
                db,
                user_id=str(current_user.id),
                space_id=str(current_user._effective_space_id)
            )
            ...
    """
    # This is a base dependency - actual user injection happens in routes
    yield db


async def init_rls_for_user(
    db: AsyncSession,
    user: User,
) -> RLSSession:
    """
    Initialize RLS context for a user and return an RLSSession.

    Call this at the start of any endpoint that needs RLS enforcement.

    Args:
        db: Database session from get_db_session dependency
        user: Authenticated user from get_current_user_from_token

    Returns:
        RLSSession with configured context

    Example:
        @router.get("/messages")
        async def get_messages(
            current_user: User = Depends(get_current_user_from_token),
            db: AsyncSession = Depends(get_db_session),
        ):
            rls = await init_rls_for_user(db, current_user)
            # Now all queries through rls.db are RLS-filtered
    """
    space_id = str(getattr(user, '_effective_space_id', None) or user.current_space_id or user.space_id)
    user_id = str(user.id)

    await set_rls_context(db, user_id=user_id, space_id=space_id)

    logger.debug(f"RLS context initialized: user={user_id}, space={space_id}")

    return RLSSession(db=db, user=user, space_id=space_id)


async def init_rls_for_agent(
    db: AsyncSession,
    agent_id: str,
    space_id: str,
    actual_agent_id: str | None = None,
) -> None:
    """
    Initialize RLS context for an agent (service-to-service calls).

    Used when an agent is making database queries on behalf of itself,
    not through a user session.

    Args:
        db: Database session
        agent_id: The owner user_id (used as user_id for RLS DB lookups)
        space_id: The agent's space UUID
        actual_agent_id: The real agent UUID for app.current_agent_id.
            Needed for guest RLS policies that check per-agent channel
            visibility via space_channel_settings. When None, falls back
            to agent_id (owner user_id) — sufficient for non-guest flows.
    """
    # Set app.current_agent_id to the real agent UUID so guest RLS policies can enforce
    # per-agent channel visibility (space_channel_settings.guest_accessible).
    await set_rls_context(db, user_id=agent_id, space_id=space_id, agent_id=actual_agent_id or agent_id)
    logger.debug(f"RLS context initialized for agent: agent={actual_agent_id or agent_id}, space={space_id}")


# =============================================================================
# SECURE SESSION - The "Sovereign Shield" Combined Dependency
# =============================================================================

@dataclass
class SecureSession:
    """
    Combined authenticated session with RLS context already set.

    This is the RECOMMENDED way to access the database in endpoints.
    RLS is automatically configured - queries only return data from
    the user's current space (space_id).

    Identity model:
        User owns the token. Agent scope limits where it can be used.

        PATs (axp_u_*) with no agent header: USER principal. is_agent=False.
        PATs with a valid agent header: AGENT principal at the API layer.
        The user still owns the credential, but the effective actor is the
        targeted owned agent and the effective space is the agent's bound space.

        Backend-issued RS256 JWTs (mint_space_agent_token) also authenticate
        as AGENT principal. is_agent=True. Used by dispatched agents and aX.

    Attributes:
        db: AsyncSession with RLS context set (space-isolated)
        user: The authenticated user (or synthetic user for agent tokens)
        space_id: The effective space ID
        agent_id: Agent UUID — for agent JWTs: the principal; for PATs: the bound agent
        agent_name: Agent name — same distinction as agent_id
        is_agent: True for agent-acting PATs and backend-issued agent JWTs
        principal_type: "user" for user-acting sessions, "agent" when the
            effective actor is an agent
        principal_id: UUID of the authenticated principal
    """
    db: AsyncSession
    user: User
    space_id: str
    agent_id: str = None
    agent_name: str = None
    is_agent: bool = False
    principal_type: str = "user"
    principal_id: str | None = None


async def get_secure_session(
    request: Request,
    token: str = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> SecureSession:
    """
    Dependency that provides an authenticated, RLS-protected database session.

    This is the "Sovereign Shield" - a single dependency that:
    1. Validates the JWT token
    2. Loads the user from the database
    3. Sets the RLS context for space isolation
    4. Returns everything in one convenient object

    Security:
    - Without this dependency, endpoints must manually set RLS context
    - With this dependency, space isolation is AUTOMATIC
    - Queries through session.db only return data from user's space

    Usage:
        from app.core.rls import get_secure_session, SecureSession

        @router.get("/tasks")
        async def get_tasks(session: SecureSession):
            # Automatically filtered to user's space - no manual filtering needed!
            result = await session.db.execute(select(Task))
            return result.scalars().all()

    Raises:
        HTTPException 401: If token is invalid or user not found
    """
    from ..core.security import verify_token
    from fastapi import HTTPException, status

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # AUTH-SPEC-001 §3.1: PATs accepted ONLY at /auth/exchange.
    # Reject PATs on business routes — clients must exchange for JWT first.
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
        from .agent_context import resolve_agent_target, AgentTargetError

        principal = await authenticate_credential(token, db)
        effective_space_id = str(principal.space_id)

        # Resolve optional agent targeting via X-Agent-Id or X-Agent-Name
        agent_id_header = request.headers.get("x-agent-id")
        agent_name_header = request.headers.get("x-agent-name")
        agent_id = None
        agent_name = None
        try:
            user_home_space_id = getattr(principal.user, "space_id", None) or principal.space_id
            agent_target = await resolve_agent_target(
                db=db,
                agent_id_header=agent_id_header,
                agent_name_header=agent_name_header,
                user_id=principal.user.id,
                space_id=principal.space_id,
                user_home_space_id=user_home_space_id,
                agent_scope=principal.agent_scope,
                allowed_agent_ids=principal.allowed_agent_ids,
                credential_id=principal.credential_id,
            )
            if agent_target:
                agent_id, agent_name, target_space_id = agent_target
                effective_space_id = str(target_space_id)
                logger.info(
                    "PAT_AGENT_TARGET principal=user/%s target_agent=%s/%s space=%s",
                    principal.user.id, agent_id, agent_name, effective_space_id,
                )
        except AgentTargetError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail)

        await set_rls_context(
            db,
            user_id=str(principal.user.id),
            space_id=effective_space_id,
        )

        return SecureSession(
            db=db, user=principal.user, space_id=effective_space_id,
            # Legacy PAT targeting can select an agent resource, but it must
            # not change authorship identity. Business routes should normally
            # never reach this path because PATs are exchange-only.
            agent_id=None,
            agent_name=None,
            is_agent=False,
            principal_type="user",
            principal_id=str(principal.user.id),
        )

    # Try Cognito JWT first (AUTH-001)
    from ..core.auth_config import COGNITO_USER_POOL_ID
    if COGNITO_USER_POOL_ID:
        try:
            from app.core.jwt_verify import (
                _apply_owned_agent_target,
                _decode_token,
                _resolve_user,
            )
            claims = await _decode_token(token)
            # Valid Cognito token — resolve user (pass the raw access token so the
            # waitlist gate can fetch the email for brand-new users; access tokens
            # carry no `email` claim).
            user = await _resolve_user(claims, db, access_token=token)
            user = await _apply_owned_agent_target(user, db, request)
            space_id = str(
                getattr(user, "_effective_space_id", None)
                or user.current_space_id
                or user.space_id
            )
            targeted_agent_id = getattr(user, "_agent_id", None)
            targeted_agent_name = getattr(user, "_agent_name", None)
            route_agent_session = _is_mcp_route_agent_session(
                claims=claims,
                request=request,
                targeted_agent_id=targeted_agent_id,
            )
            await set_rls_context(
                db,
                user_id=str(user.id),
                space_id=space_id,
                agent_id=targeted_agent_id,
            )
            logger.debug(
                "SecureSession (Cognito) initialized: user=%s targeted_agent=%s space=%s principal=%s",
                user.id,
                targeted_agent_id,
                space_id,
                "agent" if route_agent_session else "user",
            )
            return SecureSession(
                db=db,
                user=user,
                space_id=space_id,
                # Frontend widget user JWTs may target an agent for UI flows
                # but remain user-authored. MCP OAuth sessions entered through
                # /mcp/agents/{name} are route-bound agent sessions after the
                # owned-agent target is verified above.
                agent_id=targeted_agent_id if route_agent_session else None,
                agent_name=targeted_agent_name if route_agent_session else None,
                is_agent=route_agent_session,
                principal_type="agent" if route_agent_session else "user",
                principal_id=targeted_agent_id if route_agent_session else str(user.id),
            )
        except HTTPException as e:
            if "Invalid token" in str(e.detail) or "Token key not found" in str(e.detail):
                logger.debug(f"Not a Cognito token, trying legacy: {e.detail}")
            else:
                raise
        except Exception as e:
            logger.debug(f"Cognito JWT check failed, trying legacy: {e}")

    # Backend-minted RS256 token path (aX agent auth)
    try:
        from jose import jwt as jose_jwt
        unverified = jose_jwt.get_unverified_claims(token)
        if unverified.get("iss") == "ax-backend":
            from app.core.jwt_verify import _resolve_ax_agent_user
            from app.core.ax_jwt import get_jwks
            jwks = get_jwks()
            headers = jose_jwt.get_unverified_headers(token)
            kid = headers.get("kid")
            key = None
            for k in jwks.get("keys", []):
                if k.get("kid") == kid:
                    key = k
                    break
            if key:
                claims = jose_jwt.decode(
                    token, key, algorithms=["RS256"],
                    issuer="ax-backend",
                    options={"verify_aud": False},
                )
                # Dev-login RS256 tokens carry user_id (not agent_id)
                if claims.get("user_id") and not claims.get("agent_id"):
                    from app.core.jwt_verify import _resolve_dev_rs256_user
                    user = await _resolve_dev_rs256_user(claims, db)
                    space_id = str(user.current_space_id or getattr(user, 'space_id', ''))
                    await set_rls_context(db, user_id=str(user.id), space_id=space_id)
                    logger.info("SecureSession (dev-rs256) initialized: user=%s space=%s", user.id, space_id)
                    return SecureSession(
                        db=db,
                        user=user,
                        space_id=space_id,
                        principal_type="user",
                        principal_id=str(user.id),
                    )
                # Exchange-issued JWTs carry src_credential_id + token_class (AUTH-SPEC-001)
                if claims.get("src_credential_id") and claims.get("token_class") in ("user_access", "user_admin", "agent_access"):
                    from app.core.jwt_verify import (
                        _apply_owned_agent_target,
                        _is_exchange_mcp_route_agent_session,
                        _resolve_exchange_jwt_user,
                    )
                    user = await _resolve_exchange_jwt_user(claims, db)
                    if claims.get("token_class") in ("user_access", "user_admin"):
                        user = await _apply_owned_agent_target(user, db, request)
                    space_id = str(getattr(user, "_effective_space_id", None) or user.current_space_id or user.space_id)
                    route_agent_session = _is_exchange_mcp_route_agent_session(
                        claims=claims,
                        request=request,
                        targeted_agent_id=getattr(user, "_agent_id", None),
                    )
                    is_agent = claims.get("token_class") == "agent_access" or route_agent_session
                    agent_id = getattr(user, "_agent_id", None) if is_agent else None
                    agent_name = getattr(user, "_agent_name", None) if is_agent else None
                    await set_rls_context(db, user_id=str(user.id), space_id=space_id, agent_id=agent_id)
                    logger.info(
                        "SecureSession (exchange-jwt) initialized: user=%s token_class=%s space=%s agent=%s principal=%s",
                        user.id,
                        claims.get("token_class"),
                        space_id,
                        agent_id,
                        "agent" if is_agent else "user",
                    )
                    return SecureSession(
                        db=db, user=user, space_id=space_id,
                        agent_id=agent_id, agent_name=agent_name,
                        is_agent=is_agent,
                        principal_type="agent" if is_agent else "user",
                        principal_id=agent_id if is_agent else str(user.id),
                    )
                user = await _resolve_ax_agent_user(token, claims, db)
                space_id = user._effective_space_id
                await init_rls_for_agent(
                    db,
                    agent_id=str(user.id),
                    space_id=space_id,
                    actual_agent_id=getattr(user, "_agent_id", None),
                )
                logger.info("SecureSession (ax-backend) initialized: user=%s agent=%s space=%s", user.id, getattr(user, '_agent_name', None), space_id)
                return SecureSession(
                    db=db, user=user, space_id=space_id,
                    agent_id=getattr(user, '_agent_id', None),
                    agent_name=getattr(user, '_agent_name', None),
                    is_agent=True,
                    principal_type="agent",
                    principal_id=getattr(user, "_agent_id", None) or str(user.id),
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.debug("Backend JWT check in SecureSession failed: %s", e)

    # Legacy token path
    token_payload = verify_token(token)
    if token_payload is None or token_payload.type not in ("access", "agent"):
        raise credentials_exception

    user_id = token_payload.sub

    if token_payload.type == "agent" or getattr(token_payload, 'grant_type', None) == "client_credentials":
        # Agent token (client_credentials grant) — skip token_version check
        # sub was rewritten to user_id by verify_token
        result = await db.execute(
            select(User).where(User.id == user_id).where(User.active)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise credentials_exception

        space_id = str(
            token_payload.space_id
            if token_payload.space_id != "00000000-0000-0000-0000-000000000000"
            else (user.current_space_id or user.space_id)
        )

        # Use agent RLS context — extract real agent UUID first so it's available for RLS
        agent_id = getattr(token_payload, 'agent_id', None)
        agent_name = getattr(token_payload, 'agent_name', None)
        await init_rls_for_agent(db, agent_id=user_id, space_id=space_id, actual_agent_id=agent_id)
        logger.debug(f"SecureSession (agent) initialized: user={user.id}, agent={agent_name}, space={space_id}")
        return SecureSession(
            db=db, user=user, space_id=space_id,
            agent_id=agent_id, agent_name=agent_name, is_agent=True,
            principal_type="agent",
            principal_id=str(agent_id or user.id),
        )

    # Standard user token path
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .where(User.token_version == token_payload.token_version)
        .where(User.active)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    # Determine effective space_id
    space_id = str(
        getattr(user, '_effective_space_id', None) or
        user.current_space_id or
        user.space_id
    )

    # Set RLS context
    await set_rls_context(db, user_id=str(user.id), space_id=space_id)
    logger.debug(f"SecureSession initialized: user={user.id}, space={space_id}")

    return SecureSession(
        db=db,
        user=user,
        space_id=space_id,
        principal_type="user",
        principal_id=str(user.id),
    )


# Type alias for cleaner endpoint signatures
SecureSessionDep = Annotated[SecureSession, Depends(get_secure_session)]


# =============================================================================
# SYSTEM SESSION - Privileged bypass for auth/OAuth bootstrap endpoints
# =============================================================================

@dataclass
class SystemSession:
    """
    Privileged database session for endpoints that run before user auth.

    Used by: login, register, OAuth callbacks, token refresh.
    These endpoints need global table access because no user session exists yet.

    Sets app.is_privileged=true so RLS policies can grant access:
        USING ( current_setting('app.is_privileged', true) = 'true' OR space_id = ... )

    Security:
        - No space_id or user_id context set (global access)
        - Only use in auth bootstrap endpoints
        - All access is auditable via the is_privileged flag
    """
    db: AsyncSession


async def get_system_session(
    db: AsyncSession = Depends(get_db_session),
) -> SystemSession:
    """
    Dependency for auth/OAuth endpoints that need pre-auth database access.

    Sets is_privileged flag but no org/user context.
    RLS policies must check this flag to allow access.
    """
    from sqlalchemy import text

    await db.execute(
        text("SELECT set_config('app.is_privileged', 'true', true)")
    )
    logger.debug("SystemSession initialized (privileged, no user context)")

    return SystemSession(db=db)


# Type alias
SystemSessionDep = Annotated[SystemSession, Depends(get_system_session)]


# =============================================================================
# ADMIN SESSION - Auditable cross-org access for admin endpoints
# =============================================================================

@dataclass
class AdminSession:
    """
    Admin-scoped database session for cross-org operations.

    Used by: admin.py, admin_analytics.py, admin_outreach.py, etc.
    Requires authenticated user with admin/superuser role.

    Sets app.current_role=admin and app.current_user_id for audit trail,
    but space_id=SYSTEM to signal cross-space scope.

    Security:
        - User must be authenticated AND have admin role
        - All queries auditable (user_id + role=admin logged)
        - Explicit "God mode" — never silent escalation
    """
    db: AsyncSession
    user: User


async def get_admin_session(
    request: Request,
    token: str = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> AdminSession:
    """
    Dependency for admin endpoints requiring cross-org database access.

    Validates admin role, sets RLS context with role=admin and space_id=SYSTEM.

    Raises:
        HTTPException 401: If token is invalid or user not found
        HTTPException 403: If user is not an admin
    """
    from fastapi import HTTPException, status
    from sqlalchemy import text
    from .jwt_verify import _resolve_admin_human_user_from_bearer_token

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        user = await _resolve_admin_human_user_from_bearer_token(token, db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise
        raise credentials_exception from exc

    # Verify admin role
    user_role = getattr(user, "role", "user") or "user"
    if user_role.lower() not in ("admin", "superuser", "super_admin", "agent_manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    # Set RLS context: user identified, role=admin, org=SYSTEM (cross-org)
    await db.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": str(user.id)}
    )
    await db.execute(
        text("SELECT set_config('app.current_role', 'admin', true)")
    )
    await db.execute(
        text("SELECT set_config('app.current_space_id', 'SYSTEM', true)")
    )
    await db.execute(
        text("SELECT set_config('app.is_privileged', 'true', true)")
    )

    logger.debug(f"AdminSession initialized: user={user.id}, role=admin, org=SYSTEM")

    return AdminSession(db=db, user=user)


# Type alias
AdminSessionDep = Annotated[AdminSession, Depends(get_admin_session)]


# =============================================================================
# CONTEXT MANAGERS - For inline usage (not dependency injection)
# =============================================================================
# Use these when you can't use Depends() — e.g., inside SSE generators,
# background tasks, or inline OAuth flows.

from contextlib import asynccontextmanager


@asynccontextmanager
async def system_session_context():
    """
    Context manager for SystemSession (privileged, no user context).

    Usage:
        async with system_session_context() as system:
            result = await system.db.execute(select(User).where(...))
    """
    from sqlalchemy import text

    async for db in get_db_session():
        await db.execute(
            text("SELECT set_config('app.is_privileged', 'true', true)")
        )
        logger.debug("SystemSession context manager initialized (privileged)")
        yield SystemSession(db=db)
        break  # Only need one iteration from the generator


@asynccontextmanager
async def admin_session_context(user: User):
    """
    Context manager for AdminSession (auditable cross-org access).

    Requires a pre-authenticated admin user. Use when you already have
    the user from a different auth path (e.g., SSE with token validation).

    Usage:
        async with admin_session_context(current_user) as admin:
            result = await admin.db.execute(select(...))

    Args:
        user: Pre-authenticated user with admin role (caller must verify)
    """
    from sqlalchemy import text

    async for db in get_db_session():
        await db.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user.id)}
        )
        await db.execute(
            text("SELECT set_config('app.current_role', 'admin', true)")
        )
        await db.execute(
            text("SELECT set_config('app.current_space_id', 'SYSTEM', true)")
        )
        await db.execute(
            text("SELECT set_config('app.is_privileged', 'true', true)")
        )
        logger.debug(f"AdminSession context manager initialized: user={user.id}")
        yield AdminSession(db=db, user=user)
        break
