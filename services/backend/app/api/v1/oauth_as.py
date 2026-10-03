"""Provider-neutral aX OAuth authorization-server contract.

The MCP server is the protected resource. aX is the authorization server clients
discover for DCR, browser OAuth, headless device authorization, and refresh.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...core.ax_jwt import mint_ax_jwt, mint_exchange_jwt
from ...core.agent_constraints import validate_agent_name
from ...core.agent_space import grant_space_access
from ...core.database import get_db_session
from ...core.rls import get_secure_session
from ...core.security import SECRET_KEY, hash_token, verify_password
from ...models.agent import Agent
from ...models.agent_key import AgentKey
from ...models.oauth_as import (
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthDeviceCode,
    OAuthRefreshToken,
)
from ...models.user import User


router = APIRouter(tags=["oauth-as"])

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
GRANT_TYPES_SUPPORTED = {
    "authorization_code",
    "refresh_token",
    DEVICE_CODE_GRANT,
    "client_credentials",
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
DEFAULT_COGNITO_LOGIN_SCOPE = "openid email profile"
COGNITO_LOGIN_STATE_TTL_SECONDS = 600
OAUTH_REDIRECT_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
LEGACY_OAUTH_LOGIN_PATHS = {"/auth/login"}
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


def _public_base_url(request: Request) -> str:
    configured = os.getenv("AX_AUTH_SERVER_URL") or os.getenv("BASE_URL")
    if configured:
        return configured.rstrip("/")

    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host"
    )
    if forwarded_host:
        if "," in forwarded_host:
            forwarded_host = forwarded_host.split(",", 1)[0].strip()
        scheme = forwarded_proto or request.url.scheme
        return f"{scheme}://{forwarded_host}".rstrip("/")

    return str(request.base_url).rstrip("/")


def _default_resource_url() -> str:
    configured_resource = os.getenv("AX_MCP_RESOURCE_URL")
    if configured_resource:
        return configured_resource.rstrip("/")

    public_mcp_server = os.getenv("MCP_PUBLIC_SERVER_URL") or os.getenv(
        "PUBLIC_MCP_SERVER_URL"
    )
    if public_mcp_server:
        return f"{public_mcp_server.rstrip('/')}/mcp"

    mcp_server_url = os.getenv("MCP_SERVER_URL")
    if mcp_server_url:
        parsed = urlparse(mcp_server_url)
        if parsed.hostname not in {"ax-mcp-server", "mcp", "mcp-server"}:
            return f"{mcp_server_url.rstrip('/')}/mcp"

    return "http://localhost:8002/mcp"


def _device_verification_uri(request: Request) -> str:
    configured = os.getenv("AX_DEVICE_VERIFICATION_URL")
    if configured:
        return configured.rstrip("/")
    return f"{_public_base_url(request)}/device"


def _mcp_connection_metadata(resource_url: str) -> dict[str, Any]:
    mcp_resource = resource_url.rstrip("/")
    return {
        "resource": mcp_resource,
        "agent_url_template": f"{mcp_resource}/agents/{{agent_name}}",
        "agent_binding_methods": [
            {
                "type": "url_path",
                "template": "/mcp/agents/{agent_name}",
                "description": "Recommended for MCP clients that can use a per-agent URL.",
            },
            {
                "type": "header",
                "header": "X-Agent-Name",
                "endpoint": mcp_resource,
                "description": "Alternative for clients that prefer a stable MCP URL.",
            },
            {
                "type": "header",
                "header": "X-Agent-Id",
                "endpoint": mcp_resource,
                "description": "Stable-id alternative when the agent id is already known.",
            },
        ],
    }


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
    resource_url = resource or _default_resource_url()
    _validate_agent_resource(resource_url)
    return resource_url


def _device_resource_type(resource: str) -> str:
    if _agent_name_from_mcp_resource(resource):
        return "mcp_agent"
    if urlparse(resource).path.rstrip("/").endswith("/mcp"):
        return "mcp"
    return "resource"


def _agent_scoped_device_error() -> dict[str, str]:
    return {
        "error": "invalid_target",
        "error_description": (
            "MCP device-code agent registration must use an agent-scoped resource "
            "such as https://paxai.app/mcp/agents/{agent_name}. Reconnect using the "
            "agent URL from auth.md."
        ),
    }


def _agent_scoped_device_error_for(resource: str) -> dict[str, str] | None:
    if _device_resource_type(resource) == "mcp":
        return _agent_scoped_device_error()
    return None


def _validate_device_resource_url(resource: str | None) -> str:
    resource_url = _validated_resource_url(resource)
    if error := _agent_scoped_device_error_for(resource_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )
    return resource_url


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


def _local_device_approval_user_id() -> str | None:
    environment = os.getenv("ENVIRONMENT", "").lower()
    if environment not in {"development", "local", "test"}:
        return None
    if os.getenv("ENABLE_LOCAL_TESTING", "").lower() != "true":
        return None
    configured = os.getenv("DEVICE_CODE_TEST_USER_ID")
    if not configured:
        return None
    try:
        return str(uuid.UUID(configured))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "server_error",
                "error_description": "DEVICE_CODE_TEST_USER_ID is not a UUID",
            },
        ) from exc


async def get_device_approval_principal(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> DeviceApprovalPrincipal:
    params = await _request_params(request)
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        session = await get_secure_session(request=request, token=token, db=db)
        return DeviceApprovalPrincipal(user_id=str(session.user.id))

    jwt_token = params.get("jwt_token", "").strip()
    if jwt_token:
        session = await get_secure_session(request=request, token=jwt_token, db=db)
        return DeviceApprovalPrincipal(user_id=str(session.user.id))

    local_user_id = _local_device_approval_user_id()
    if local_user_id:
        return DeviceApprovalPrincipal(user_id=local_user_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": "login_required",
            "error_description": "Sign in before approving this device code.",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _find_device_by_user_code(
    db: AsyncSession,
    user_code: str,
) -> OAuthDeviceCode | None:
    normalized = _normalize_user_code(user_code)
    if not normalized:
        return None
    result = await db.execute(
        select(OAuthDeviceCode).where(
            OAuthDeviceCode.user_code_hash == hash_token(normalized),
            OAuthDeviceCode.consumed_at.is_(None),
        )
    )
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


def _is_legacy_oauth_login_url(login_url: str) -> bool:
    parsed = urlparse(login_url)
    path = (parsed.path or login_url).rstrip("/") or "/"
    return path in LEGACY_OAUTH_LOGIN_PATHS


def _configured_oauth_login_url() -> str | None:
    login_url = os.getenv("AX_OAUTH_LOGIN_URL", "").strip()
    if not login_url or _is_legacy_oauth_login_url(login_url):
        return None
    return login_url


def _cognito_domain_url(request: Request) -> str | None:
    domain = _first_env(
        "AX_COGNITO_DOMAIN",
        "COGNITO_HOSTED_UI_DOMAIN",
        "COGNITO_DOMAIN",
        "FRONTEND_COGNITO_DOMAIN",
        "VITE_COGNITO_DOMAIN",
    )
    if not domain:
        return None
    if "://" not in domain:
        domain = f"https://{domain}"
    return domain.rstrip("/")


def _inferred_cognito_domain_url(request: Request) -> str | None:
    region = os.getenv("AWS_REGION", "us-west-2")
    candidates = [
        os.getenv("BASE_URL"),
        os.getenv("FRONTEND_URL"),
        _public_base_url(request),
        str(request.base_url),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
        host = parsed.hostname
        # Dev and prod currently share this Cognito Hosted UI domain/user pool.
        # The production frontend deploy config uses the ax-dev domain with the
        # production paxai.app callback and frontend client.
        if host == "paxai.app":
            return f"https://ax-dev.auth.{region}.amazoncognito.com"
        if host in {"dev.paxai.app", "localhost", "127.0.0.1"}:
            return f"https://ax-dev.auth.{region}.amazoncognito.com"
    return None


def _cognito_callback_url(request: Request) -> str:
    configured = _first_env(
        "AX_COGNITO_CALLBACK_URI",
        "COGNITO_CALLBACK_URI",
    )
    if configured and not _is_spa_cognito_callback_url(configured):
        return configured
    return f"{_public_base_url(request).rstrip('/')}/auth/callback"


def _is_spa_cognito_callback_url(value: str) -> bool:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return parsed.path.rstrip("/") == "/login/callback"


SPA_LOGIN_CALLBACK_PATH = "/login/callback"
# Fernet tokens (version byte 0x80) always base64-encode to this prefix; the
# SPA uses the same prefix to forward backend states from /login/callback back
# to /auth/callback, so anything else is an SPA-owned state.
BACKEND_LOGIN_STATE_PREFIX = "gAAAA"


def _is_backend_login_state(state: str) -> bool:
    return state.startswith(BACKEND_LOGIN_STATE_PREFIX)


def _state_secret() -> str:
    return _first_env("AX_OAUTH_STATE_SECRET") or SECRET_KEY


def _state_cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(_state_secret().encode()).digest())
    return Fernet(key)


def _authorize_params_from_return_to(return_to: str) -> dict[str, str]:
    parsed = urlparse(return_to)
    values = parse_qs(parsed.query)
    allowed = {
        "response_type",
        "client_id",
        "redirect_uri",
        "scope",
        "state",
        "resource",
        "code_challenge",
        "code_challenge_method",
    }
    return {key: value[0] for key, value in values.items() if key in allowed and value}


def _encode_cognito_login_state(return_to: str, verifier: str) -> str:
    now = int(time.time())
    payload = {
        "v": 1,
        "iat": now,
        "exp": now + COGNITO_LOGIN_STATE_TTL_SECONDS,
        "return_to": return_to,
        "code_verifier": verifier,
        "authorize": _authorize_params_from_return_to(return_to),
    }
    return (
        _state_cipher()
        .encrypt(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        .decode("ascii")
    )


def _decode_cognito_login_state(value: str) -> dict[str, Any]:
    try:
        raw = _state_cipher().decrypt(
            value.encode("ascii"),
            ttl=COGNITO_LOGIN_STATE_TTL_SECONDS,
        )
        payload = json.loads(raw)
    except (InvalidToken, UnicodeEncodeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid state parameter") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=400, detail="Expired state parameter")
    if payload.get("v") != 1 or not isinstance(payload.get("authorize"), dict):
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    if not payload.get("code_verifier"):
        raise HTTPException(status_code=400, detail="Invalid state parameter")
    return payload


def _cognito_hosted_ui_redirect_url(request: Request, return_to: str) -> str | None:
    domain = _cognito_domain_url(request) or _inferred_cognito_domain_url(request)
    client_id = _first_env(
        "AX_COGNITO_CLIENT_ID",
        "COGNITO_FRONTEND_CLIENT_ID",
        "FRONTEND_COGNITO_CLIENT_ID",
        "AWS_COGNITO_FRONTEND_CLIENT_ID",
        "VITE_COGNITO_CLIENT_ID",
    )
    if not domain or not client_id:
        return None
    verifier = secrets.token_urlsafe(48)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _cognito_callback_url(request),
        "scope": os.getenv("AX_COGNITO_LOGIN_SCOPE", DEFAULT_COGNITO_LOGIN_SCOPE),
        "state": _encode_cognito_login_state(return_to, verifier),
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{domain}/oauth2/authorize?{urlencode(params)}"


def _oauth_login_redirect_url(request: Request) -> str:
    public_base = _public_base_url(request).rstrip("/")
    return_to = f"{public_base}{request.url.path}"
    if request.url.query:
        return_to = f"{return_to}?{request.url.query}"

    login_url = _configured_oauth_login_url()
    if login_url:
        separator = "&" if "?" in login_url else "?"
        return f"{login_url}{separator}{urlencode({'return_to': return_to})}"

    cognito_login_url = _cognito_hosted_ui_redirect_url(request, return_to)
    if cognito_login_url:
        return cognito_login_url

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="OAuth login is not configured",
    )


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


def _mint_user_access_token(
    *,
    owner_user_id: str,
    scope: str,
    audience: str,
    src_credential_id: str,
):
    return mint_exchange_jwt(
        sub=f"user:{owner_user_id}",
        token_class="user_access",
        audience=audience,
        scope=scope,
        ttl_seconds=900,
        src_credential_id=src_credential_id,
        owner_user_id=owner_user_id,
    )


def _mint_agent_access_token(
    *,
    owner_user_id: str,
    agent_id: str,
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
        authorized_space_id=authorized_space_id,
    )


async def _resolve_or_create_oauth_agent(
    db: AsyncSession,
    *,
    owner_user_id: str,
    resource: str,
    bound_agent_id: str | None = None,
) -> Agent | None:
    agent_name = _validate_agent_resource(resource)
    if not agent_name:
        return None

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
    if user is None or not user.active:
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
        if agent.name != agent_name:
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
        await grant_space_access(
            db, agent.id, uuid.UUID(str(agent.space_id)), is_default=True
        )
        return agent

    target_space_id = user.current_space_id or user.space_id
    if not target_space_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_target",
                "error_description": "sponsoring user has no active space",
            },
        )

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
    agent_id: str | None = None,
    rotated_from_id: uuid.UUID | None = None,
) -> None:
    db.add(
        OAuthRefreshToken(
            refresh_token_hash=hash_token(refresh_token),
            client_id=client_id,
            owner_user_id=uuid.UUID(str(owner_user_id)),
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
    bound_agent_id: str | None = None,
    rotated_from_id: uuid.UUID | None = None,
):
    agent = await _resolve_or_create_oauth_agent(
        db,
        owner_user_id=owner_user_id,
        resource=audience,
        bound_agent_id=bound_agent_id,
    )
    if agent is not None:
        token = _mint_agent_access_token(
            owner_user_id=owner_user_id,
            agent_id=str(agent.id),
            scope=scope,
            audience=audience,
            src_credential_id=src_credential_id,
            authorized_space_id=str(agent.space_id),
        )
    else:
        token = _mint_user_access_token(
            owner_user_id=owner_user_id,
            scope=scope,
            audience=audience,
            src_credential_id=src_credential_id,
        )
    refresh_token = f"rfr_{secrets.token_urlsafe(48)}"
    _add_refresh_token(
        db,
        refresh_token=refresh_token,
        owner_user_id=owner_user_id,
        client_id=client_id,
        scope=scope,
        resource=audience,
        agent_id=str(agent.id) if agent is not None else None,
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
    resource_url = resource or _default_resource_url()
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
    if os.getenv("AX_AUTH_SERVER_METADATA_MODE", "ax").strip().lower() != "ax":
        return {
            "issuer": auth_base,
            "authorization_endpoint": f"{auth_base}/authorize",
            "token_endpoint": f"{auth_base}/token",
            "registration_endpoint": f"{auth_base}/register",
            "revocation_endpoint": f"{auth_base}/revoke",
            "service_documentation": f"{auth_base}/auth.md",
            "mcp": _mcp_connection_metadata(resource_url),
            "scopes_supported": ["openid"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
                "none",
            ],
            "code_challenge_methods_supported": ["S256"],
        }

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
    record = OAuthAuthorizationCode(
        code_hash=hash_token(code),
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=requested_scope,
        resource=resource_url,
        owner_user_id=uuid.UUID(str(session.user.id)),
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
async def authorize_client(
    request: Request,
    response_type: str | None = None,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    state: str | None = None,
    resource: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    # Browser entry with no params and no session: send the human to login
    # instead of leaking FastAPI's Bearer-auth 401 JSON.
    has_authorize_params = any(
        value is not None
        for value in (
            response_type,
            client_id,
            redirect_uri,
            scope,
            state,
            resource,
            code_challenge,
            code_challenge_method,
        )
    )
    token = _oauth_authorize_session_token(request)
    if not has_authorize_params and not token:
        return _oauth_redirect_response(_oauth_login_redirect_url(request))

    await _validated_authorization_request(
        db=db,
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        resource=resource,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )

    if not token:
        if os.getenv("AUTH_MODE", "local").lower() == "local":
            from .local_consent import consent_page
            client = await db.get(OAuthClient, client_id)
            return consent_page(request, client.client_name if client else None)
        return _oauth_redirect_response(_oauth_login_redirect_url(request))
    try:
        session = await get_secure_session(request=request, token=token, db=db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return _oauth_redirect_response(_oauth_login_redirect_url(request))
        raise

    return await _issue_authorization_code_redirect(
        db=db,
        session=session,
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        resource=resource,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )


@router.post("/oauth/authorize")
async def approve_authorization(request: Request, db: AsyncSession = Depends(get_db_session)):
    from .local_auth import _check_origin
    _check_origin(request)
    token = _oauth_authorize_session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in before approving")
    params = await request.json()
    session = await get_secure_session(request=request, token=token, db=db)
    response = await _issue_authorization_code_redirect(
        db=db, session=session,
        **{key: params.get(key) for key in (
            "response_type", "client_id", "redirect_uri", "scope", "state", "resource",
            "code_challenge", "code_challenge_method",
        )},
    )
    return JSONResponse({"redirect_uri": response.headers["location"]}, headers={"Cache-Control": "no-store"})


async def _exchange_cognito_authorization_code(
    request: Request,
    *,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    domain = _cognito_domain_url(request) or _inferred_cognito_domain_url(request)
    client_id = _first_env(
        "AX_COGNITO_CLIENT_ID",
        "COGNITO_FRONTEND_CLIENT_ID",
        "FRONTEND_COGNITO_CLIENT_ID",
        "AWS_COGNITO_FRONTEND_CLIENT_ID",
        "VITE_COGNITO_CLIENT_ID",
    )
    if not domain or not client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth login is not configured",
        )
    body = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": _cognito_callback_url(request),
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{domain}/oauth2/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        try:
            error = response.json()
        except ValueError:
            error = {"error": response.text}
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "cognito_token_exchange_failed",
                "cognito_error": error,
            },
        )
    return response.json()


@router.get("/auth/callback")
async def cognito_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    if state and not _is_backend_login_state(state):
        # SPA-initiated Cognito logins share this redirect URI but carry an
        # opaque nonce only the SPA can validate. Hand the callback (including
        # error callbacks) to the SPA route, which CloudFront serves from the
        # frontend origin.
        return _oauth_redirect_response(
            f"{SPA_LOGIN_CALLBACK_PATH}?{request.url.query}"
        )
    if error:
        if not state:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": error, "error_description": error_description},
            )
        payload = _decode_cognito_login_state(state)
        return await _authorization_error_redirect(
            db=db,
            authorize=payload["authorize"],
            error=error,
            error_description=error_description,
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing Cognito callback state")

    payload = _decode_cognito_login_state(state)
    token_response = await _exchange_cognito_authorization_code(
        request,
        code=code,
        code_verifier=payload["code_verifier"],
    )
    access_token = token_response.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cognito token response did not include access_token",
        )
    session = await get_secure_session(request=request, token=access_token, db=db)
    authorize = payload["authorize"]
    return await _issue_authorization_code_redirect(
        db=db,
        session=session,
        response_type=authorize.get("response_type"),
        client_id=authorize.get("client_id"),
        redirect_uri=authorize.get("redirect_uri"),
        scope=authorize.get("scope"),
        state=authorize.get("state"),
        resource=authorize.get("resource"),
        code_challenge=authorize.get("code_challenge"),
        code_challenge_method=authorize.get("code_challenge_method"),
    )


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
    safe_value = escape(value, quote=True)
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>aX MCP Device Approval</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #090a0f;
      --panel: #151316;
      --panel-strong: #211b20;
      --line: #3d3439;
      --text: #f6f4ee;
      --muted: #a7adbb;
      --accent: #60e6c7;
      --accent-strong: #ff755f;
      --danger: #ff6b7a;
      --ok: #8ff5bc;
    }

    * {
      box-sizing: border-box;
    }

    body {
      min-height: 100vh;
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background-color: var(--bg);
      background-image:
        linear-gradient(rgba(246, 244, 238, 0.045) 1px, transparent 1px),
        linear-gradient(90deg, rgba(246, 244, 238, 0.035) 1px, transparent 1px);
      background-size: 44px 44px;
      color: var(--text);
    }

    main {
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 2rem;
    }

    .approval-shell {
      width: min(100%, 720px);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(20, 23, 32, 0.94);
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.42);
      overflow: hidden;
    }

    .approval-header {
      padding: 2rem;
      border-bottom: 1px solid var(--line);
      background: #1d171d;
    }

    .eyebrow {
      margin: 0 0 0.75rem;
      color: var(--accent);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    h1 {
      margin: 0;
      font-size: clamp(2rem, 7vw, 3.35rem);
      line-height: 1.02;
      letter-spacing: 0;
    }

    .subtitle {
      margin: 1rem 0 0;
      color: var(--muted);
      font-size: 1rem;
      line-height: 1.55;
    }

    .approval-body {
      padding: 1.5rem 2rem 2rem;
      display: grid;
      gap: 1rem;
    }

    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.75rem;
    }

    .detail {
      min-width: 0;
      padding: 1rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
    }

    .detail.wide {
      grid-column: 1 / -1;
    }

    .label {
      display: block;
      margin-bottom: 0.45rem;
      color: var(--muted);
      font-size: 0.76rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    .value {
      display: block;
      overflow-wrap: anywhere;
      font-size: 1rem;
      line-height: 1.4;
    }

    .code-input {
      width: 100%;
      height: 3.25rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 1rem;
      background: #090b12;
      color: var(--text);
      font: 700 1.1rem ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    .scope-list {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
      min-height: 2rem;
      margin: 0;
      padding: 0;
      list-style: none;
    }

    .scope-list li {
      border: 1px solid rgba(96, 230, 199, 0.34);
      border-radius: 999px;
      padding: 0.34rem 0.58rem;
      color: var(--accent);
      background: rgba(96, 230, 199, 0.08);
      font-size: 0.84rem;
      line-height: 1;
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
      padding-top: 0.25rem;
    }

    button {
      min-height: 3rem;
      border: 0;
      border-radius: 8px;
      padding: 0 1.25rem;
      background: var(--accent);
      color: #05100d;
      font-weight: 800;
      cursor: pointer;
    }

    button.secondary {
      border: 1px solid var(--line);
      background: transparent;
      color: var(--text);
    }

    button:disabled {
      cursor: wait;
      opacity: 0.62;
    }

    #status {
      min-height: 1.5rem;
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
    }

    #status[data-tone="error"] {
      color: var(--danger);
    }

    #status[data-tone="success"] {
      color: var(--ok);
    }

    @media (max-width: 640px) {
      main {
        padding: 1rem;
        place-items: stretch;
      }

      .approval-shell {
        align-self: center;
      }

      .approval-header,
      .approval-body {
        padding: 1.25rem;
      }

      .detail-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main>
    <section class="approval-shell" aria-labelledby="approval-title">
      <header class="approval-header">
        <p class="eyebrow">aX remote MCP</p>
        <h1 id="approval-title">aX MCP Device Approval</h1>
        <p class="subtitle">Review the agent connection before granting this browser session as sponsor.</p>
      </header>

      <form class="approval-body" method="post" action="/oauth/device/approve">
        <div class="detail-grid">
          <label class="detail" for="user_code">
            <span class="label">Device code</span>
            <input
              class="code-input"
              id="user_code"
              name="user_code"
              value="__USER_CODE__"
              autocomplete="one-time-code"
              inputmode="text"
            >
          </label>

          <div class="detail">
            <span class="label">Agent</span>
            <strong class="value" data-device-agent-name>Checking...</strong>
          </div>

          <div class="detail">
            <span class="label">Client</span>
            <strong class="value" data-device-client-name>Checking...</strong>
          </div>

          <div class="detail">
            <span class="label">Sponsor</span>
            <strong class="value" data-device-sponsor>Signed-in Waystation session</strong>
          </div>

          <div class="detail wide">
            <span class="label">Resource</span>
            <span class="value" data-device-resource>Checking...</span>
          </div>

          <div class="detail wide">
            <span class="label">Requested access</span>
            <ul class="scope-list" data-device-scopes></ul>
          </div>
        </div>

        <input id="jwt_token" name="jwt_token" type="hidden">
        <div class="actions">
          <button type="submit">Approve Connection</button>
          <button class="secondary" type="button" data-refresh-details>Refresh Details</button>
        </div>
        <p id="status" role="status"></p>
      </form>
    </section>
  </main>
  <script>
    (function () {
      const form = document.querySelector("form");
      const status = document.getElementById("status");
      const tokenInput = document.getElementById("jwt_token");
      const codeInput = document.getElementById("user_code");
      const refreshButton = document.querySelector("[data-refresh-details]");
      const approveButton = form.querySelector("button[type='submit']");
      const agentName = document.querySelector("[data-device-agent-name]");
      const clientName = document.querySelector("[data-device-client-name]");
      const sponsor = document.querySelector("[data-device-sponsor]");
      const resource = document.querySelector("[data-device-resource]");
      const scopes = document.querySelector("[data-device-scopes]");
      let approvalBlocked = false;

      function jwtMatches(value) {
        if (!value) return [];
        const text = String(value);
        const jwtPattern = /eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+/g;
        return text.match(jwtPattern) || [];
      }

      function storageEntries(storage) {
        const entries = [];
        try {
          for (let index = 0; index < storage.length; index += 1) {
            const key = storage.key(index);
            if (!key) continue;
            entries.push([key, storage.getItem(key)]);
          }
        } catch (_error) {
          return [];
        }
        return entries;
      }

      function scoreKey(key) {
        if (/accessToken/i.test(key)) return 0;
        if (/idToken/i.test(key)) return 1;
        if (/token/i.test(key)) return 2;
        return 3;
      }

      function findBrowserToken() {
        const candidates = [];
        [localStorage, sessionStorage].forEach((storage) => {
          storageEntries(storage).forEach(([key, value]) => {
            jwtMatches(value).forEach((token) => {
              candidates.push({ key, token, score: scoreKey(key) });
            });
          });
        });
        candidates.sort((left, right) => left.score - right.score);
        return candidates[0] ? candidates[0].token : "";
      }

      function parseJwtPayload(token) {
        try {
          const payload = token.split(".")[1];
          const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
          const padded = normalized.padEnd(
            normalized.length + ((4 - (normalized.length % 4)) % 4),
            "="
          );
          const json = decodeURIComponent(
            atob(padded)
              .split("")
              .map((char) => "%" + ("00" + char.charCodeAt(0).toString(16)).slice(-2))
              .join("")
          );
          return JSON.parse(json);
        } catch (_error) {
          return {};
        }
      }

      function setStatus(message, tone) {
        status.textContent = message;
        if (tone) {
          status.dataset.tone = tone;
        } else {
          delete status.dataset.tone;
        }
      }

      async function hydrateToken() {
        tokenInput.value = findBrowserToken();
        if (!tokenInput.value) {
          try {
            const response = await fetch("/auth/local/refresh", { method: "POST", credentials: "same-origin" });
            if (response.ok) tokenInput.value = (await response.json()).access_token || "";
          } catch (_error) { /* Login link remains available when no session exists. */ }
        }
        const claims = parseJwtPayload(tokenInput.value);
        sponsor.textContent = claims.email || claims.username || claims.preferred_username || claims.name || "Signed-in Waystation session";
        return tokenInput.value;
      }

      function renderScopes(values) {
        scopes.replaceChildren();
        if (!values || values.length === 0) {
          const item = document.createElement("li");
          item.textContent = "No scopes advertised";
          scopes.appendChild(item);
          return;
        }
        values.forEach((value) => {
          const item = document.createElement("li");
          item.textContent = value;
          scopes.appendChild(item);
        });
      }

      async function refreshDetails() {
        const code = codeInput.value.replace(/[-\\s]/g, "").toUpperCase();
        if (!code) {
          setStatus("Enter the device code to load connection details.", "error");
          return;
        }
        approvalBlocked = true;
        approveButton.disabled = true;
        setStatus("Loading connection details...");
        try {
          const response = await fetch("/oauth/device/verify?" + new URLSearchParams({ user_code: code }));
          const data = await response.json();
          if (!response.ok) {
            const description = data.error_description
              || data.detail?.error_description
              || "Unable to load device details.";
            setStatus(description, "error");
            return;
          }
          agentName.textContent = data.agent_name || "User MCP session";
          clientName.textContent = data.client_name || data.client_id || "Registered MCP client";
          resource.textContent = data.resource || "Default Waystation MCP resource";
          renderScopes(data.scopes || []);
          approvalBlocked = Boolean(data.approval_blocked);
          approveButton.disabled = approvalBlocked;
          if (approvalBlocked) {
            agentName.textContent = data.agent_name || "Agent target missing";
            setStatus(data.error_description || "This device code is not agent-scoped.", "error");
            return;
          }
          setStatus(data.resource_type === "mcp_agent"
            ? "Ready to approve this agent connection."
            : "Ready to approve this MCP connection.");
        } catch (_error) {
          setStatus("Unable to load device details. Check the code and try again.", "error");
        }
      }

      hydrateToken();
      refreshDetails();
      refreshButton.addEventListener("click", refreshDetails);
      codeInput.addEventListener("change", refreshDetails);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const token = await hydrateToken();
        if (!token) {
          setStatus("Sign in to Waystation in this browser, then reload this page.", "error");
          return;
        }
        if (approvalBlocked) {
          setStatus("This device code is not agent-scoped. Reconnect using /mcp/agents/{agent_name}.", "error");
          return;
        }

        approveButton.disabled = true;
        setStatus("Approving...");
        try {
          const response = await fetch("/oauth/device/approve", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams(new FormData(form)),
          });
          const data = await response.json();
          if (!response.ok) {
            const description = data.error_description
              || data.detail?.error_description
              || "Approval failed.";
            setStatus(description, "error");
            approveButton.disabled = approvalBlocked;
            return;
          }
          setStatus(
            data.status === "approved"
              ? "Approved. Return to your terminal."
              : "Denied.",
            data.status === "approved" ? "success" : "error"
          );
        } catch (_error) {
          setStatus("Approval failed. Reload this page and try again.", "error");
          approveButton.disabled = approvalBlocked;
        }
      });
    }());
  </script>
</body>
</html>"""
    return HTMLResponse(html.replace("__USER_CODE__", safe_value))


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
    target_error = _agent_scoped_device_error_for(resource)
    payload = {
        "status": record.status,
        "user_code": _normalize_user_code(user_code),
        "client_id": record.client_id,
        "client_name": client.client_name if client else record.client_id,
        "resource": resource,
        "resource_type": _device_resource_type(resource),
        "agent_name": agent_name,
        "scope": scope,
        "scopes": scope.split(),
        "expires_at": record.expires_at.isoformat(),
        "approval_blocked": target_error is not None,
    }
    if target_error:
        payload.update(target_error)
    return payload


@router.post("/oauth/device/approve")
async def approve_device_code(
    request: Request,
    principal: DeviceApprovalPrincipal = Depends(get_device_approval_principal),
    db: AsyncSession = Depends(get_db_session),
):
    params = await _request_params(request)
    user_code = params.get("user_code", "")
    record = await _find_device_by_user_code(db, user_code)
    if record is None:
        return JSONResponse(
            {
                "error": "invalid_user_code",
                "error_description": "user_code is invalid or expired",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    approved = params.get("approved", "true").lower() not in {
        "false",
        "0",
        "no",
        "deny",
        "denied",
    }
    if not approved:
        record.status = "denied"
        record.owner_user_id = uuid.UUID(principal.user_id)
        record.approved_at = _now_utc()
        await db.commit()
        return {"status": "denied", "client_id": record.client_id}

    resource = record.resource or _default_resource_url()
    if error := _agent_scoped_device_error_for(resource):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    record.status = "approved"
    record.owner_user_id = uuid.UUID(principal.user_id)
    record.approved_at = _now_utc()
    await db.commit()
    return {"status": "approved", "client_id": record.client_id}


def _client_credentials_error(
    error: str, description: str, status_code: int = 400
) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status_code,
    )


async def _handle_client_credentials(params: dict[str, str], db: AsyncSession):
    client_id = params.get("client_id")
    client_secret = params.get("client_secret")
    if not client_id or not client_secret:
        return _client_credentials_error(
            "invalid_request",
            "client_id and client_secret are required",
        )

    result = await db.execute(
        select(AgentKey)
        .options(selectinload(AgentKey.agent))
        .where(AgentKey.client_id == client_id, AgentKey.is_active.is_(True))
    )
    key = result.scalar_one_or_none()
    if key is None or not verify_password(client_secret, key.client_secret_hash):
        return _client_credentials_error(
            "invalid_client", "client credentials are invalid", 401
        )

    agent = key.agent
    if agent is None:
        return _client_credentials_error(
            "invalid_client",
            "agent key is not bound to an agent",
            401,
        )

    requested_scope = (
        params.get("scope") or key.scopes or "messages.read messages.write"
    )
    token = mint_ax_jwt(
        agent_id=str(key.agent_id),
        agent_name=agent.name,
        space_id=str(agent.space_id),
        tools_allowed=DEFAULT_AGENT_TOOLS,
        extra_claims={"scope": requested_scope},
    )
    key.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 900,
        "scope": requested_scope,
    }


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

    record.consumed_at = _now_utc()
    response = await _issue_oauth_token_response(
        db,
        client_id=record.client_id,
        owner_user_id=str(record.owner_user_id),
        scope=record.scope,
        audience=record.resource,
        src_credential_id=f"authorization_code:{record.id}",
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
    if params.get("client_id") and params["client_id"] != record.client_id:
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

    record.revoked_at = _now_utc()
    response = await _issue_oauth_token_response(
        db,
        client_id=record.client_id,
        owner_user_id=str(record.owner_user_id),
        scope=record.scope,
        audience=record.resource,
        src_credential_id=f"refresh:{secrets.token_urlsafe(12)}",
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
            )
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
        if params.get("client_id") and params["client_id"] != record.client_id:
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
        record.consumed_at = _now_utc()
        response = await _issue_oauth_token_response(
            db,
            client_id=record.client_id,
            owner_user_id=owner_user_id,
            scope=scope,
            audience=audience,
            src_credential_id=f"device:{record.id}",
        )
        await db.commit()
        return response

    if grant_type == "client_credentials":
        return await _handle_client_credentials(params, db)

    return JSONResponse(
        {"error": "unsupported_grant_type"},
        status_code=status.HTTP_400_BAD_REQUEST,
    )
