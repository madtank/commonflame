"""Provider-neutral aX OAuth authorization-server contract.

The MCP server is the protected resource. aX is the authorization server clients
discover for DCR, browser OAuth, headless device authorization, and refresh.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.ax_jwt import mint_exchange_jwt
from ...core.agent_constraints import validate_agent_name
from ...core.agent_space import grant_space_access
from ...core.database import get_db_session
from ...core.rls import get_secure_session
from ...core.security import hash_token
from ...models.agent import Agent
from ...models.oauth_as import (
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthDeviceCode,
    OAuthRefreshToken,
)
from ...models.user import User
from ...models.space_membership import SpaceMembership
from ...core.authorization import verify_space_membership


router = APIRouter(tags=["oauth-as"])

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
GRANT_TYPES_SUPPORTED = {
    "authorization_code",
    "refresh_token",
    DEVICE_CODE_GRANT,
}
TOKEN_AUTH_METHODS_SUPPORTED = {
    "none",
    "client_secret_post",
    "client_secret_basic",
}

SCOPES_SUPPORTED = [
    "agents.read",
    "agents.write",
    "messages.read",
    "messages.write",
    "tasks.read",
    "tasks.write",
    "context.read",
    "context.write",
    "spaces.read",
    "spaces.write",
    "ax-api/mcp:read",
    "ax-api/mcp:write",
    "openid",
    "offline_access",
]

# Scopes granted to public dynamically-registered MCP clients (e.g. Claude Code
# via `claude mcp add`). They must be able to obtain a refresh token
# (offline_access) and call MCP tools (ax-api/mcp:*); otherwise they register
# too narrowly (legacy discovery advertises only "openid") and then get
# 400 invalid_scope at /oauth/authorize when they request offline_access.
MCP_CLIENT_DEFAULT_SCOPES = [
    "openid",
    "offline_access",
    "ax-api/mcp:read",
    "ax-api/mcp:write",
]

DEFAULT_AGENT_TOOLS = [
    "agents",
    "messages",
    "tasks",
    "context",
    "spaces",
    "whoami",
    "search",
]
DEFAULT_USER_SCOPE = "messages.read tasks.read context.read spaces.read agents.read"
OAUTH_REDIRECT_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
REFRESH_TOKEN_TTL_SECONDS = int(
    os.getenv("AX_REFRESH_TOKEN_TTL_SECONDS", str(30 * 24 * 60 * 60))
)


class ClientRegistrationRequest(BaseModel):
    client_id: str | None = None
    client_name: str | None = None
    redirect_uris: list[str] = Field(default_factory=list)
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])
    token_endpoint_auth_method: str = "none"
    scope: str | None = None
    client_uri: str | None = None
    logo_uri: str | None = None
    contacts: list[str] | None = None


@dataclass(frozen=True)
class DeviceApprovalPrincipal:
    user_id: str
    space_id: str


async def _require_human_sponsor(session) -> uuid.UUID:
    if (getattr(session, "is_agent", False) or getattr(session, "principal_type", "user") != "user"
            or not session.user.active or session.user.auth_provider not in {"builtin", "local"}):
        raise HTTPException(status_code=403, detail={"error": "human_sponsor_required"})
    try:
        space_id = uuid.UUID(str(session.space_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=403, detail={"error": "invalid_workspace"}) from exc
    await verify_space_membership(session.db, session.user.id, space_id)
    return space_id


def _public_base_url(request: Request) -> str:
    # Public metadata must never follow arbitrary Host / forwarded headers.
    return (os.getenv("AX_AUTH_PUBLIC_BASE_URL") or os.getenv("PUBLIC_URL")
            or os.getenv("AX_AUTH_SERVER_URL") or os.getenv("FRONTEND_URL")
            or "http://localhost:3000").rstrip("/")


def _default_resource_url() -> str:
    configured = (os.getenv("AX_MCP_RESOURCE_URL") or os.getenv("PUBLIC_MCP_SERVER_URL")
                  or os.getenv("MCP_PUBLIC_SERVER_URL"))
    if configured:
        configured = configured.rstrip("/")
        return configured if configured.endswith("/mcp") else configured + "/mcp"
    origin = (os.getenv("AX_AUTH_PUBLIC_BASE_URL") or os.getenv("PUBLIC_URL")
              or os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip("/")
    return origin + "/mcp"


def _device_verification_uri(request: Request) -> str:
    configured = os.getenv("AX_DEVICE_VERIFICATION_URL")
    if configured:
        return configured.rstrip("/")
    return f"{_public_base_url(request)}/device"


def _mcp_connection_metadata(resource_url: str) -> dict[str, Any]:
    return {"resource": resource_url, "agent_identity": "signed_agent_id",
            "onboarding": "human_sponsored_oauth",
            "agent_url_template": resource_url + "/agents/{agent_name}"}


def _is_loopback_host(hostname: str | None) -> bool:
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _validate_redirect_uri(uri: str) -> None:
    parsed = urlparse(uri)
    if "*" in uri:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_redirect_uri",
                "error_description": "wildcard redirect_uris are not allowed",
            },
        )
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and _is_loopback_host(parsed.hostname):
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": "invalid_redirect_uri",
            "error_description": "redirect_uris must be https or http loopback",
        },
    )


async def _form_params(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


async def _request_params(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        return {key: str(value) for key, value in body.items() if value is not None}
    return await _form_params(request)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _expires_in(seconds: int) -> datetime:
    return _now_utc() + timedelta(seconds=seconds)


def _is_expired(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < _now_utc()


def _normalize_user_code(user_code: str) -> str:
    return user_code.replace("-", "").replace(" ", "").upper()


def _validate_requested_scope(scope: str | None) -> str | None:
    if not scope:
        return None
    requested = [item for item in scope.split() if item]
    unsupported = sorted(set(requested) - set(SCOPES_SUPPORTED))
    if unsupported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_scope",
                "error_description": f"unsupported scopes: {' '.join(unsupported)}",
            },
        )
    return " ".join(dict.fromkeys(requested))


def _validate_scope_subset(
    requested_scope: str | None, registered_scope: str | None
) -> None:
    if not requested_scope or not registered_scope:
        return
    requested = set(requested_scope.split())
    registered = set(registered_scope.split())
    unsupported = sorted(requested - registered)
    if unsupported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_scope",
                "error_description": (
                    f"scope was not registered for this client: {' '.join(unsupported)}"
                ),
            },
        )


def _agent_name_from_mcp_resource(resource: str | None) -> str | None:
    if not resource:
        return None

    parsed = urlparse(resource)
    path = parsed.path
    for marker in ("/mcp/agents/", "/mcp/agent/"):
        if marker not in path:
            continue
        agent_name = path.split(marker, 1)[1].split("/", 1)[0]
        return unquote(agent_name).strip()
    return None


def _is_empty_named_agent_resource(resource: str) -> bool:
    path = urlparse(resource).path.rstrip("/")
    return path.endswith("/mcp/agents") or path.endswith("/mcp/agent")


def _validate_agent_resource(resource: str) -> str | None:
    agent_name = _agent_name_from_mcp_resource(resource)
    if agent_name is None:
        if _is_empty_named_agent_resource(resource):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_target",
                    "error_description": "MCP agent resource must include an agent name",
                },
            )
        return None

    if not agent_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_target",
                "error_description": "MCP agent resource must include an agent name",
            },
        )

    is_valid, error_msg = validate_agent_name(agent_name)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_target",
                "error_description": error_msg,
            },
        )
    return agent_name


def _validated_resource_url(resource: str | None) -> str:
    canonical = _default_resource_url()
    value = resource or canonical
    if value == canonical:
        return value
    parsed = urlparse(value)
    base = urlparse(canonical)
    # Named routes are compatibility aliases within this exact resource server.
    prefix = base.path.rstrip("/") + "/agents/"
    if (parsed.scheme != base.scheme or parsed.netloc != base.netloc
            or parsed.query or parsed.fragment or not parsed.path.startswith(prefix)):
        raise HTTPException(status_code=400, detail={"error": "invalid_target",
                            "error_description": "resource must identify this Waystation MCP server"})
    suffix = parsed.path[len(prefix):]
    if "/" in suffix or not suffix:
        raise HTTPException(status_code=400, detail={"error": "invalid_target"})
    _validate_agent_resource(value)
    return value


def _device_resource_type(resource: str) -> str:
    if _agent_name_from_mcp_resource(resource):
        return "mcp_agent"
    if urlparse(resource).path.rstrip("/").endswith("/mcp"):
        return "mcp"
    return "resource"






def _validate_device_resource_url(resource: str | None) -> str:
    return _validated_resource_url(resource)


def _validate_client_metadata(body: ClientRegistrationRequest) -> None:
    unsupported_grants = sorted(set(body.grant_types) - GRANT_TYPES_SUPPORTED)
    if unsupported_grants:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_client_metadata",
                "error_description": f"unsupported grants: {' '.join(unsupported_grants)}",
            },
        )
    unsupported_responses = sorted(set(body.response_types) - {"code"})
    if unsupported_responses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_client_metadata",
                "error_description": (
                    f"unsupported response types: {' '.join(unsupported_responses)}"
                ),
            },
        )
    if body.token_endpoint_auth_method not in TOKEN_AUTH_METHODS_SUPPORTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_client_metadata",
                "error_description": "unsupported token_endpoint_auth_method",
            },
        )




async def get_device_approval_principal(request: Request, db: AsyncSession = Depends(get_db_session)) -> DeviceApprovalPrincipal:
    token = _oauth_authorize_session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail={"error": "login_required"}, headers={"WWW-Authenticate": "Bearer"})
    session = await get_secure_session(request=request, token=token, db=db)
    space_id = await _require_human_sponsor(session)
    return DeviceApprovalPrincipal(user_id=str(session.user.id), space_id=str(space_id))


async def _find_device_by_user_code(
    db: AsyncSession,
    user_code: str,
    *,
    for_update: bool = False,
) -> OAuthDeviceCode | None:
    normalized = _normalize_user_code(user_code)
    if not normalized:
        return None
    query = select(OAuthDeviceCode).where(
            OAuthDeviceCode.user_code_hash == hash_token(normalized),
            OAuthDeviceCode.consumed_at.is_(None),
        )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    record = result.scalar_one_or_none()
    if record is None or _is_expired(record.expires_at):
        return None
    return record


def _client_redirect_allowed(client: OAuthClient, redirect_uri: str) -> bool:
    return redirect_uri in (client.redirect_uris or [])


def _oauth_authorize_session_token(request: Request) -> str | None:
    """Resolve an explicit authorize credential without forcing Bearer auth.

    The authorization endpoint is a public browser entrypoint. FastAPI security
    dependencies with auto_error=True turn missing credentials into the default
    `401 {"detail":"Not authenticated"}` before OAuth request validation can run,
    creating a chicken-and-egg deadlock for new MCP clients. Keep token lookup
    optional here and redirect unauthenticated humans to login/consent instead.

    Deliberately do not accept ambient cookies on this GET endpoint: a
    top-level cross-site navigation can carry SameSite=Lax cookies, so minting an
    authorization code from cookies alone would bypass consent/CSRF protections.
    The login/consent UI can submit an explicit Bearer token after the user acts.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None






























def _oauth_login_redirect_url(request: Request) -> str:
    path = request.url.path
    if request.url.query:
        path += "?" + request.url.query
    return "/login?" + urlencode({"next": path})


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _oauth_redirect_response(
    url: str,
    status_code: int = status.HTTP_302_FOUND,
) -> RedirectResponse:
    return RedirectResponse(
        url=url,
        status_code=status_code,
        headers=OAUTH_REDIRECT_HEADERS,
    )


def _apply_basic_client_auth(
    request: Request, params: dict[str, str]
) -> dict[str, str]:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("basic "):
        return params
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        client_id, client_secret = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return params
    params.setdefault("client_id", client_id)
    params.setdefault("client_secret", client_secret)
    return params


def _client_metadata(body: ClientRegistrationRequest) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if body.client_uri:
        metadata["client_uri"] = body.client_uri
    if body.logo_uri:
        metadata["logo_uri"] = body.logo_uri
    if body.contacts:
        metadata["contacts"] = body.contacts
    return metadata


def _client_registration_response(
    client: OAuthClient,
    *,
    issued_at: int,
    plaintext_secret: str | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "client_id": client.client_id,
        "client_id_issued_at": issued_at,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris or [],
        "grant_types": client.grant_types or [],
        "response_types": client.response_types or [],
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
        # DCR clients validate this metadata as a string; keep stored NULL
        # semantics while avoiding JSON null on the wire.
        "scope": client.scope or "",
    }
    metadata = client.client_metadata or {}
    response.update(metadata)
    if plaintext_secret is not None:
        response["client_secret"] = plaintext_secret
        response["client_secret_expires_at"] = 0
    return response


def _client_secret_matches(client: OAuthClient, supplied_secret: str | None) -> bool:
    if client.token_endpoint_auth_method == "none":
        return True
    if not supplied_secret or not client.client_secret:
        return False
    return (
        hash_token(supplied_secret) == client.client_secret
        or supplied_secret == client.client_secret
    )




def _mint_agent_access_token(
    *,
    owner_user_id: str,
    agent_id: str,
    agent_name: str,
    scope: str,
    audience: str,
    src_credential_id: str,
    authorized_space_id: str,
):
    return mint_exchange_jwt(
        sub=f"agent:{agent_id}",
        token_class="agent_access",
        audience=_default_resource_url() if audience.startswith(_default_resource_url() + "/agents/") else audience,
        scope=scope,
        ttl_seconds=900,
        src_credential_id=src_credential_id,
        owner_user_id=owner_user_id,
        agent_id=agent_id,
        agent_name=agent_name,
        authorized_space_id=authorized_space_id,
    )


async def _resolve_or_create_oauth_agent(
    db: AsyncSession,
    *,
    owner_user_id: str,
    resource: str,
    client_id: str,
    authorized_space_id: str | None,
    bound_agent_id: str | None = None,
) -> Agent:
    _validated_resource_url(resource)
    agent_name = _validate_agent_resource(resource)

    try:
        owner_uuid = uuid.UUID(str(owner_user_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_grant",
                "error_description": "sponsoring user id is invalid",
            },
        ) from exc

    user = await db.get(User, owner_uuid)
    if user is None or not user.active or user.auth_provider not in {"builtin", "local"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "invalid_grant",
                "error_description": "sponsoring user is not active",
            },
        )

    if bound_agent_id:
        try:
            agent_uuid = uuid.UUID(str(bound_agent_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_grant",
                    "error_description": "refresh token agent binding is invalid",
                },
            ) from exc

        agent = await db.get(Agent, agent_uuid)
        if agent is None or str(agent.user_id) != str(owner_uuid):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_grant",
                    "error_description": "refresh token agent binding is no longer valid",
                },
            )
        if agent_name and agent.name != agent_name:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_grant",
                    "error_description": "refresh token agent binding does not match resource",
                },
            )
        if (agent.status or "").lower() != "active":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_grant",
                    "error_description": "refresh token agent binding is inactive",
                },
            )
        if authorized_space_id and str(agent.space_id) != str(authorized_space_id):
            raise HTTPException(status_code=401, detail={"error": "invalid_grant", "error_description": "agent workspace binding changed"})
        await verify_space_membership(db, user.id, agent.space_id)
        return agent

    target_space_id = uuid.UUID(authorized_space_id) if authorized_space_id else None
    if not target_space_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_target",
                "error_description": "human approval has no workspace binding",
            },
        )
    await verify_space_membership(db, user.id, target_space_id)
    if not agent_name:
        client = await db.get(OAuthClient, client_id)
        label = re.sub(r"[^a-z0-9]+", "-", (client.client_name if client else "mcp-client").lower()).strip("-")[:32] or "mcp-client"
        binding = f"{client_id}:{owner_uuid}:{target_space_id}"
        agent_name = f"{label}-{hashlib.sha256(binding.encode()).hexdigest()[:12]}"
    # Concurrent approved grants for one client must resolve the same identity.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:binding))"),
                     {"binding": f"oauth:{target_space_id}:{agent_name}"})

    async def load_existing() -> Agent | None:
        result = await db.execute(
            select(Agent).where(
                Agent.space_id == target_space_id,
                Agent.name == agent_name,
            )
        )
        return result.scalar_one_or_none()

    async def validate_existing(existing_agent: Agent) -> Agent:
        if str(existing_agent.user_id) != str(owner_uuid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "invalid_target",
                    "error_description": "agent name is already used by another owner in this space",
                },
            )
        if (existing_agent.status or "").lower() != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "invalid_target",
                    "error_description": "agent is not active",
                },
            )
        await grant_space_access(
            db,
            existing_agent.id,
            uuid.UUID(str(target_space_id)),
            is_default=True,
        )
        return existing_agent

    existing = await load_existing()
    if existing is not None:
        return await validate_existing(existing)

    agent = Agent(
        id=uuid.uuid4(),
        user_id=owner_uuid,
        owner_user_id=owner_uuid,
        owner_space_id=target_space_id,
        home_space_id=target_space_id,
        space_id=target_space_id,
        name=agent_name,
        description="MCP OAuth agent",
        agent_type="mcp",
        origin="mcp",
        status="active",
        visibility_level="org_visible",
        enabled_tools={"ax_mcp": True},
    )
    try:
        async with db.begin_nested():
            db.add(agent)
            await db.flush()
    except IntegrityError:
        existing = await load_existing()
        if existing is not None:
            return await validate_existing(existing)
        raise
    await grant_space_access(
        db, agent.id, uuid.UUID(str(target_space_id)), is_default=True
    )
    return agent


def _add_refresh_token(
    db: AsyncSession,
    *,
    refresh_token: str,
    owner_user_id: str,
    client_id: str,
    scope: str,
    resource: str,
    authorized_space_id: str,
    agent_id: str | None = None,
    rotated_from_id: uuid.UUID | None = None,
) -> None:
    db.add(
        OAuthRefreshToken(
            refresh_token_hash=hash_token(refresh_token),
            client_id=client_id,
            owner_user_id=uuid.UUID(str(owner_user_id)),
            authorized_space_id=uuid.UUID(str(authorized_space_id)),
            agent_id=uuid.UUID(str(agent_id)) if agent_id else None,
            scope=scope,
            resource=resource,
            expires_at=_expires_in(REFRESH_TOKEN_TTL_SECONDS),
            rotated_from_id=rotated_from_id,
        )
    )


async def _issue_oauth_token_response(
    db: AsyncSession,
    *,
    client_id: str,
    owner_user_id: str,
    scope: str,
    audience: str,
    src_credential_id: str,
    authorized_space_id: str | None,
    bound_agent_id: str | None = None,
    rotated_from_id: uuid.UUID | None = None,
):
    agent = await _resolve_or_create_oauth_agent(
        db,
        owner_user_id=owner_user_id,
        resource=audience,
        client_id=client_id,
        authorized_space_id=authorized_space_id,
        bound_agent_id=bound_agent_id,
    )
    token = _mint_agent_access_token(
            owner_user_id=owner_user_id,
            agent_id=str(agent.id),
            agent_name=agent.name,
            scope=scope,
            audience=audience,
            src_credential_id=src_credential_id,
            authorized_space_id=str(agent.space_id),
        )
    refresh_token = f"rfr_{secrets.token_urlsafe(48)}"
    _add_refresh_token(
        db,
        refresh_token=refresh_token,
        owner_user_id=owner_user_id,
        client_id=client_id,
        scope=scope,
        resource=audience,
        authorized_space_id=str(agent.space_id),
        agent_id=str(agent.id),
        rotated_from_id=rotated_from_id,
    )
    return {
        "access_token": token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": 900,
        "scope": scope,
    }


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request, resource: str | None = None):
    auth_base = _public_base_url(request)
    resource_url = _default_resource_url()
    if resource:
        _validated_resource_url(resource)
    return {
        "resource": resource_url,
        "resource_name": "Waystation MCP",
        "authorization_servers": [auth_base],
        "scopes_supported": SCOPES_SUPPORTED,
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{auth_base}/auth.md",
        "mcp": _mcp_connection_metadata(resource_url),
    }


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request):
    auth_base = _public_base_url(request)
    resource_url = _default_resource_url()


    return {
        "issuer": auth_base,
        "authorization_endpoint": f"{auth_base}/oauth/authorize",
        "token_endpoint": f"{auth_base}/oauth/token",
        "registration_endpoint": f"{auth_base}/oauth/register",
        "device_authorization_endpoint": f"{auth_base}/oauth/device/code",
        "jwks_uri": f"{auth_base}/.well-known/jwks.json",
        "service_documentation": f"{auth_base}/auth.md",
        "mcp": _mcp_connection_metadata(resource_url),
        "scopes_supported": SCOPES_SUPPORTED,
        "response_types_supported": ["code"],
        "grant_types_supported": sorted(GRANT_TYPES_SUPPORTED),
        "token_endpoint_auth_methods_supported": sorted(TOKEN_AUTH_METHODS_SUPPORTED),
        "code_challenge_methods_supported": ["S256"],
    }


@router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
async def register_client(
    body: ClientRegistrationRequest,
    db: AsyncSession = Depends(get_db_session),
):
    _validate_client_metadata(body)
    if not body.redirect_uris and "authorization_code" in body.grant_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_client_metadata",
                "error_description": "redirect_uris are required for authorization_code clients",
            },
        )
    for redirect_uri in body.redirect_uris:
        _validate_redirect_uri(redirect_uri)
    requested_scope = _validate_requested_scope(body.scope)
    # Public dynamically-registered MCP clients (e.g. Claude Code `claude mcp add`,
    # token_endpoint_auth_method="none") register with a narrow scope (the legacy
    # discovery advertises only "openid"), so they lack offline_access and then
    # fail at /oauth/authorize with 400 invalid_scope when they request it. When
    # such a client registers WITH an explicit scope, expand it to the MCP floor
    # (offline_access + ax-api/mcp:*), unioned with whatever it requested.
    # NB: a scope-LESS public registration is left untouched (client.scope stays
    # None) so it preserves authorization-time scope selection — narrowing it to
    # the floor here would regress clients that legitimately request other
    # supported scopes at /authorize.
    if body.token_endpoint_auth_method == "none" and requested_scope:
        granted = list(MCP_CLIENT_DEFAULT_SCOPES)
        for s in requested_scope.split():
            if s not in granted:
                granted.append(s)
        requested_scope = " ".join(granted)

    client_id = body.client_id or f"ax_dcr_{secrets.token_urlsafe(18)}"
    existing = await db.get(OAuthClient, client_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "invalid_client_metadata",
                "error_description": "client_id is already registered",
            },
        )

    issued_at = int(time.time())
    plaintext_secret: str | None = None
    stored_secret: str | None = None
    if body.token_endpoint_auth_method != "none":
        plaintext_secret = secrets.token_urlsafe(32)
        stored_secret = hash_token(plaintext_secret)

    client = OAuthClient(
        client_id=client_id,
        client_secret=stored_secret,
        client_name=body.client_name or "MCP Client",
        redirect_uris=body.redirect_uris,
        grant_types=body.grant_types,
        response_types=body.response_types,
        token_endpoint_auth_method=body.token_endpoint_auth_method,
        scope=requested_scope,
        client_metadata=_client_metadata(body),
        is_active=True,
    )
    db.add(client)
    await db.commit()

    return _client_registration_response(
        client,
        issued_at=issued_at,
        plaintext_secret=plaintext_secret,
    )


async def _validated_authorization_request(
    *,
    db: AsyncSession,
    response_type: str | None,
    client_id: str | None,
    redirect_uri: str | None,
    scope: str | None,
    resource: str | None,
    code_challenge: str | None,
    code_challenge_method: str | None,
) -> tuple[str, str]:
    if response_type != "code":
        raise HTTPException(
            status_code=400, detail={"error": "unsupported_response_type"}
        )
    if not client_id:
        raise HTTPException(status_code=400, detail={"error": "invalid_client"})
    if not redirect_uri:
        raise HTTPException(status_code=400, detail={"error": "invalid_redirect_uri"})

    client = await db.get(OAuthClient, client_id)
    if client is None or not client.is_active:
        raise HTTPException(status_code=400, detail={"error": "invalid_client"})
    if not _client_redirect_allowed(client, redirect_uri):
        raise HTTPException(status_code=400, detail={"error": "invalid_redirect_uri"})
    if code_challenge_method and code_challenge_method != "S256":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "Only S256 PKCE is supported",
            },
        )
    if not code_challenge:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "code_challenge is required",
            },
        )
    requested_scope = (
        _validate_requested_scope(scope) or client.scope or DEFAULT_USER_SCOPE
    )
    _validate_scope_subset(requested_scope, client.scope)
    resource_url = _validated_resource_url(resource)
    return requested_scope, resource_url


async def _issue_authorization_code_redirect(
    *,
    db: AsyncSession,
    session,
    response_type: str | None,
    client_id: str | None,
    redirect_uri: str | None,
    scope: str | None,
    state: str | None,
    resource: str | None,
    code_challenge: str | None,
    code_challenge_method: str | None,
) -> RedirectResponse:
    requested_scope, resource_url = await _validated_authorization_request(
        db=db,
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        resource=resource,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )

    code = f"code_{secrets.token_urlsafe(32)}"
    space_id = await _require_human_sponsor(session)
    record = OAuthAuthorizationCode(
        code_hash=hash_token(code),
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=requested_scope,
        resource=resource_url,
        owner_user_id=uuid.UUID(str(session.user.id)),
        authorized_space_id=space_id,
        code_challenge=code_challenge,
        expires_at=_expires_in(300),
    )
    db.add(record)
    await db.commit()

    query = {"code": code}
    if state is not None:
        query["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return _oauth_redirect_response(f"{redirect_uri}{separator}{urlencode(query)}")


async def _authorization_error_redirect(
    *,
    db: AsyncSession,
    authorize: dict[str, str],
    error: str,
    error_description: str | None,
) -> RedirectResponse:
    await _validated_authorization_request(
        db=db,
        response_type=authorize.get("response_type"),
        client_id=authorize.get("client_id"),
        redirect_uri=authorize.get("redirect_uri"),
        scope=authorize.get("scope"),
        resource=authorize.get("resource"),
        code_challenge=authorize.get("code_challenge"),
        code_challenge_method=authorize.get("code_challenge_method"),
    )
    redirect_uri = authorize["redirect_uri"]
    query = {"error": error}
    if error_description:
        query["error_description"] = error_description
    if authorize.get("state") is not None:
        query["state"] = authorize["state"]
    separator = "&" if "?" in redirect_uri else "?"
    return _oauth_redirect_response(f"{redirect_uri}{separator}{urlencode(query)}")


@router.get("/oauth/authorize")
async def authorize_client(request: Request, response_type: str | None = None,
    client_id: str | None = None, redirect_uri: str | None = None, scope: str | None = None,
    state: str | None = None, resource: str | None = None, code_challenge: str | None = None,
    code_challenge_method: str | None = None, db: AsyncSession = Depends(get_db_session)):
    if not any((response_type, client_id, redirect_uri, code_challenge)):
        return _oauth_redirect_response("/login")
    requested_scope, resource_url = await _validated_authorization_request(db=db, response_type=response_type,
        client_id=client_id, redirect_uri=redirect_uri, scope=scope, resource=resource,
        code_challenge=code_challenge, code_challenge_method=code_challenge_method)
    from .local_consent import consent_page
    client = await db.get(OAuthClient, client_id)
    return consent_page(request, client.client_name if client else None,
                        resolved_scope=requested_scope, resolved_resource=resource_url)


@router.post("/oauth/authorize")
async def approve_authorization(request: Request, db: AsyncSession = Depends(get_db_session)):
    from .local_auth import _check_origin
    _check_origin(request)
    token = _oauth_authorize_session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in before approving")
    params = await request.json()
    if not isinstance(params.get("approved"), bool):
        raise HTTPException(status_code=400, detail={"error": "invalid_request", "error_description": "Explicit approval or denial required"})
    session = await get_secure_session(request=request, token=token, db=db)
    await _require_human_sponsor(session)
    authorize = {key: params.get(key) for key in ("response_type", "client_id", "redirect_uri", "scope", "state", "resource", "code_challenge", "code_challenge_method")}
    if not params["approved"]:
        response = await _authorization_error_redirect(db=db, authorize=authorize,
                            error="access_denied", error_description="The sponsor declined this connection")
    else:
        response = await _issue_authorization_code_redirect(db=db, session=session, **authorize)
    return JSONResponse({"redirect_uri": response.headers["location"]}, headers={"Cache-Control": "no-store"})






@router.post("/oauth/device/code")
async def device_authorization(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    params = await _form_params(request)
    client_id = params.get("client_id")
    if not client_id:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "client_id is required"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    client = await db.get(OAuthClient, client_id)
    if (
        client is None
        or not client.is_active
        or DEVICE_CODE_GRANT not in client.grant_types
    ):
        return JSONResponse(
            {
                "error": "invalid_client",
                "error_description": "client is not registered for device authorization",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    device_code = f"dev_{secrets.token_urlsafe(32)}"
    user_code = secrets.token_hex(4).upper()
    expires_in = 600
    requested_scope = _validate_requested_scope(params.get("scope") or client.scope)
    _validate_scope_subset(requested_scope, client.scope)
    resource = _validate_device_resource_url(params.get("resource"))
    db.add(
        OAuthDeviceCode(
            device_code_hash=hash_token(device_code),
            user_code_hash=hash_token(user_code),
            client_id=client_id,
            scope=requested_scope,
            resource=resource,
            status="pending",
            expires_at=_expires_in(expires_in),
        )
    )
    await db.commit()

    verification_uri = _device_verification_uri(request)
    verification_query = urlencode({"user_code": user_code})
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "verification_uri_complete": f"{verification_uri}?{verification_query}",
        "expires_in": expires_in,
        "interval": 5,
    }


@router.get("/device")
async def device_approval_page(user_code: str | None = None):
    value = _normalize_user_code(user_code or "")
    next_path = "/device?" + urlencode({"user_code": value})
    login = escape("/login?" + urlencode({"next": next_path}), quote=True)
    signup = escape("/signup?" + urlencode({"next": next_path}), quote=True)
    html = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Approve an agent · Waystation</title><style>body{font:16px system-ui;background:#171d24;color:#edf2f5;margin:0;padding:5vh 20px}main{max-width:560px;margin:auto;padding:32px;border:1px solid #394451;border-radius:20px}p{color:#b9c5d2;line-height:1.6}button,input{font:inherit;padding:12px;border-radius:8px}button{background:#d09c68;border:0;font-weight:600;cursor:pointer}button:disabled{opacity:.45}input{width:90%;background:#222c38;color:white;border:1px solid #536274}a{color:#dcb48b}code{overflow-wrap:anywhere}.actions{display:flex;gap:12px;flex-wrap:wrap}</style>
<main><small>WAYSTATION</small><h1>Approve an agent connection</h1>
<label>Device code<input id="code" value="__CODE__" autocomplete="off"></label>
<p id="identity">Checking your session…</p><p id="signin" hidden><a href="__LOGIN__">Sign in</a> · <a href="__SIGNUP__">Set up an invited account</a></p>
<p>Client: <strong id="client"></strong></p><p>Agent: <span id="agent"></span></p><p>Resource: <code id="resource"></code></p><p>Requested permissions: <code id="scope"></code></p>
<p>This client receives its own agent identity in your current workspace.</p>
<div class="actions"><button id="approve" disabled>Approve connection</button><button id="deny" disabled>Deny</button><button id="reload">Refresh details</button></div><p id="status" role="status"></p><p><a href="/ax">Return to Waystation</a></p></main>
<script>
let token='',pending=false;const approve=document.getElementById('approve'),deny=document.getElementById('deny'),status=document.getElementById('status'),code=document.getElementById('code');
function enable(){approve.disabled=deny.disabled=!(token&&pending);}
async function session(){try{const refreshSession=()=>fetch('/auth/local/refresh',{method:'POST',credentials:'same-origin'});const r=await(navigator.locks?.request ? navigator.locks.request('waystation-refresh-cookie',refreshSession) : refreshSession());if(!r.ok)throw Error();const d=await r.json();token=d.access_token;document.getElementById('identity').textContent='Sponsor: '+d.user.username+' · Workspace: '+d.space_id;}catch(e){document.getElementById('identity').textContent='Sign in before deciding.';document.getElementById('signin').hidden=false;}enable();}
function updateLoginLinks(){const next='/device?'+new URLSearchParams({user_code:code.value});document.querySelector('#signin a').href='/login?'+new URLSearchParams({next});document.querySelectorAll('#signin a')[1].href='/signup?'+new URLSearchParams({next});}async function details(){updateLoginLinks();pending=false;enable();try{const r=await fetch('/oauth/device/verify?'+new URLSearchParams({user_code:code.value}));const d=await r.json();if(!r.ok)throw Error('Device code is invalid or expired');document.getElementById('client').textContent=d.client_name;document.getElementById('agent').textContent=d.agent_name;document.getElementById('resource').textContent=d.resource;document.getElementById('scope').textContent=d.scope;pending=d.status==='pending';status.textContent=pending?'Review this request before deciding.':'This request is already '+d.status;}catch(e){status.textContent=e.message;}enable();}
async function decide(approved){approve.disabled=deny.disabled=true;try{const r=await fetch('/oauth/device/approve',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({user_code:code.value,approved})});const d=await r.json();if(!r.ok)throw Error('Could not complete this decision. Sign in or refresh details.');pending=false;status.textContent=d.status==='approved'?'Approved. Return to your client.':'Denied. No credential was issued.';}catch(e){status.textContent=e.message;}enable();}
approve.addEventListener('click',()=>decide(true));deny.addEventListener('click',()=>decide(false));document.getElementById('reload').addEventListener('click',details);code.addEventListener('change',details);session();details();
</script></html>"""
    html = html.replace("__CODE__", escape(value, quote=True)).replace("__LOGIN__", login).replace("__SIGNUP__", signup)
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


@router.get("/oauth/device/approve")
async def device_approve_browser_redirect(user_code: str | None = None):
    value = _normalize_user_code(user_code or "")
    query = f"?{urlencode({'user_code': value})}" if value else ""
    return _oauth_redirect_response(
        url=f"/device{query}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.get("/oauth/device/verify")
async def verify_device_code(
    user_code: str,
    db: AsyncSession = Depends(get_db_session),
):
    record = await _find_device_by_user_code(db, user_code)
    if record is None or _is_expired(record.expires_at):
        return JSONResponse(
            {
                "error": "invalid_user_code",
                "error_description": "user_code is invalid or expired",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    client = await db.get(OAuthClient, record.client_id)
    scope = record.scope or (client.scope if client else None) or DEFAULT_USER_SCOPE
    resource = record.resource or _default_resource_url()
    agent_name = _agent_name_from_mcp_resource(resource)
    _validated_resource_url(resource)
    payload = {
        "status": record.status,
        "user_code": _normalize_user_code(user_code),
        "client_id": record.client_id,
        "client_name": client.client_name if client else record.client_id,
        "resource": resource,
        "resource_type": _device_resource_type(resource),
        "agent_name": agent_name or f"New agent for {client.client_name if client else 'this client'}",
        "scope": scope,
        "scopes": scope.split(),
        "expires_at": record.expires_at.isoformat(),
        "approval_blocked": record.status != "pending",
    }
    return payload


@router.post("/oauth/device/approve")
async def approve_device_code(
    request: Request,
    principal: DeviceApprovalPrincipal = Depends(get_device_approval_principal),
    db: AsyncSession = Depends(get_db_session),
):
    from .local_auth import _check_origin
    _check_origin(request)
    params = await _request_params(request)
    user_code = params.get("user_code", "")
    record = await _find_device_by_user_code(db, user_code, for_update=True)
    if record is None or _is_expired(record.expires_at):
        return JSONResponse(
            {
                "error": "invalid_user_code",
                "error_description": "user_code is invalid or expired",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if record.status != "pending":
        raise HTTPException(status_code=409, detail={"error": "already_decided"})
    if params.get("approved", "").lower() not in {"true", "false"}:
        raise HTTPException(status_code=400, detail={"error": "invalid_request", "error_description": "Explicit approval or denial required"})
    approved = params["approved"].lower() == "true"
    if not approved:
        record.status = "denied"
        record.owner_user_id = uuid.UUID(principal.user_id)
        record.approved_at = _now_utc()
        await db.commit()
        return {"status": "denied", "client_id": record.client_id}

    resource = record.resource or _default_resource_url()
    _validated_resource_url(resource)

    record.status = "approved"
    record.owner_user_id = uuid.UUID(principal.user_id)
    record.authorized_space_id = uuid.UUID(principal.space_id)
    record.approved_at = _now_utc()
    await db.commit()
    return {"status": "approved", "client_id": record.client_id}


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )


async def _handle_authorization_code(params: dict[str, str], db: AsyncSession):
    code = params.get("code")
    result = await db.execute(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code_hash == hash_token(code or ""),
            OAuthAuthorizationCode.consumed_at.is_(None),
        ).with_for_update()
    )
    record = result.scalar_one_or_none()
    if record is None or _is_expired(record.expires_at):
        return _oauth_error("invalid_grant", "authorization code is invalid or expired")
    client_id = params.get("client_id")
    if client_id != record.client_id:
        return _oauth_error(
            "invalid_client", "client_id does not match authorization code", 401
        )
    client = await db.get(OAuthClient, record.client_id)
    if (
        client is None
        or not client.is_active
        or not _client_secret_matches(client, params.get("client_secret"))
    ):
        return _oauth_error("invalid_client", "client authentication failed", 401)
    if params.get("redirect_uri") != record.redirect_uri:
        return _oauth_error(
            "invalid_grant", "redirect_uri does not match authorization code"
        )

    verifier = params.get("code_verifier")
    if not verifier or _pkce_challenge(verifier) != record.code_challenge:
        return _oauth_error("invalid_grant", "PKCE verification failed")
    if params.get("resource") and params["resource"] not in {record.resource, _default_resource_url()}:
        return _oauth_error("invalid_target", "resource does not match human approval")

    record.consumed_at = _now_utc()
    response = await _issue_oauth_token_response(
        db,
        client_id=record.client_id,
        owner_user_id=str(record.owner_user_id),
        scope=record.scope,
        audience=record.resource,
        src_credential_id=f"authorization_code:{record.id}",
        authorized_space_id=str(record.authorized_space_id) if record.authorized_space_id else None,
    )
    await db.commit()
    return response


async def _handle_refresh_token(params: dict[str, str], db: AsyncSession):
    refresh_token = params.get("refresh_token")
    result = await db.execute(
        select(OAuthRefreshToken).where(
            OAuthRefreshToken.refresh_token_hash == hash_token(refresh_token or ""),
            OAuthRefreshToken.revoked_at.is_(None),
        ).with_for_update()
    )
    record = result.scalar_one_or_none()
    if record is None or _is_expired(record.expires_at):
        return _oauth_error("invalid_grant", "refresh token is invalid or expired")
    if params.get("client_id") != record.client_id:
        return _oauth_error(
            "invalid_client", "client_id does not match refresh token", 401
        )
    client = await db.get(OAuthClient, record.client_id)
    if (
        client is None
        or not client.is_active
        or not _client_secret_matches(client, params.get("client_secret"))
    ):
        return _oauth_error("invalid_client", "client authentication failed", 401)

    if params.get("resource") and params["resource"] not in {record.resource, _default_resource_url()}:
        return _oauth_error("invalid_target", "resource does not match human approval")
    refresh_scope = _validate_requested_scope(params.get("scope")) or record.scope
    _validate_scope_subset(refresh_scope, record.scope)

    record.revoked_at = _now_utc()
    response = await _issue_oauth_token_response(
        db,
        client_id=record.client_id,
        owner_user_id=str(record.owner_user_id),
        scope=refresh_scope,
        audience=record.resource,
        src_credential_id=f"refresh:{secrets.token_urlsafe(12)}",
        authorized_space_id=str(record.authorized_space_id) if record.authorized_space_id else None,
        bound_agent_id=str(record.agent_id) if record.agent_id else None,
        rotated_from_id=record.id,
    )
    await db.commit()
    return response


@router.post("/oauth/token")
async def token_endpoint(request: Request, db: AsyncSession = Depends(get_db_session)):
    params = _apply_basic_client_auth(request, await _form_params(request))
    grant_type = params.get("grant_type")

    if grant_type == "authorization_code":
        return await _handle_authorization_code(params, db)

    if grant_type == "refresh_token":
        return await _handle_refresh_token(params, db)

    if grant_type == DEVICE_CODE_GRANT:
        device_code = params.get("device_code")
        result = await db.execute(
            select(OAuthDeviceCode).where(
                OAuthDeviceCode.device_code_hash == hash_token(device_code or ""),
                OAuthDeviceCode.consumed_at.is_(None),
            ).with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None or _is_expired(record.expires_at):
            return JSONResponse(
                {
                    "error": "expired_token",
                    "error_description": "device_code is invalid or expired",
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if record.status == "denied":
            return JSONResponse(
                {"error": "access_denied"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if record.status != "approved":
            return JSONResponse(
                {"error": "authorization_pending"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if params.get("client_id") != record.client_id:
            return JSONResponse(
                {
                    "error": "invalid_client",
                    "error_description": "client_id does not match device_code",
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        client = await db.get(OAuthClient, record.client_id)
        if (
            client is None
            or not client.is_active
            or DEVICE_CODE_GRANT not in client.grant_types
            or not _client_secret_matches(client, params.get("client_secret"))
        ):
            return JSONResponse(
                {
                    "error": "invalid_client",
                    "error_description": "client authentication failed",
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        owner_user_id = str(record.owner_user_id) if record.owner_user_id else None
        if not owner_user_id:
            return JSONResponse(
                {"error": "authorization_pending"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        scope = record.scope or DEFAULT_USER_SCOPE
        audience = _validated_resource_url(record.resource)
        if params.get("resource") and params["resource"] not in {record.resource, _default_resource_url()}:
            return _oauth_error("invalid_target", "resource does not match human approval")
        record.consumed_at = _now_utc()
        response = await _issue_oauth_token_response(
            db,
            client_id=record.client_id,
            owner_user_id=owner_user_id,
            scope=scope,
            audience=audience,
            src_credential_id=f"device:{record.id}",
            authorized_space_id=str(record.authorized_space_id) if record.authorized_space_id else None,
        )
        await db.commit()
        return response

    return JSONResponse(
        {"error": "unsupported_grant_type"},
        status_code=status.HTTP_400_BAD_REQUEST,
    )
