"""Auth checks and middleware for Waystation Agent tool access.

Backend-issued tokens carry a tools_allowed claim listing which tools
the agent may use. User access tokens without this claim are checked by backend permissions.

This module also contains a small auth diagnostics helper and an early
request validator so presented bearer tokens fail fast before the first
real tools/call. The Streamable HTTP MCP resource is protected: unauthenticated
first contact on initialize or sensitive methods returns an OAuth challenge so
clients can discover protected-resource metadata before retrying with a token.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable
from urllib.parse import quote

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.auth import AuthCheck, AuthContext
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from starlette.responses import JSONResponse
import mcp.types as mt

logger = logging.getLogger(__name__)

MAX_MCP_AUTH_CHALLENGE_BODY_BYTES = 64 * 1024

PROTECTED_MCP_METHODS = frozenset(
    {
        # `initialize` MUST stay protected. Anonymous initialize is dispatched
        # through FastMCP's StreamableHTTP session manager, which (in stateful
        # single-worker mode) allocates a long-lived transport/session per call.
        # Unauthenticated scanners (e.g. Glama) fire-and-abandon these, saturating
        # the session manager until every POST hangs. Glama domain-ownership is
        # served separately by the /.well-known/glama.json route, so anonymous
        # initialize is not needed for discovery. See specs/MCP-INIT-AUTH-001/spec.md.
        "initialize",
        "tools/call",
        "resources/read",
        "prompts/get",
        "tasks/get",
        "tasks/update",
        "tasks/cancel",
        "subscriptions/listen",
    }
)


def extract_bearer_token(headers: Iterable[tuple[bytes, bytes]]) -> str | None:
    """Return the presented bearer token if one exists."""
    for key, value in headers:
        if key.lower() != b"authorization":
            continue
        raw = value.decode("utf-8", errors="ignore").strip()
        if not raw.lower().startswith("bearer "):
            return None
        token = raw[7:].strip()
        return token or None
    return None


def _principal_hint(claims: dict[str, Any]) -> str:
    if claims.get("agent_id") or claims.get("space_id"):
        return "space_agent"
    if (
        claims.get("token_use") == "access"
        and not claims.get("username")
        and (claims.get("client_id") or claims.get("aud"))
    ):
        return "m2m_client"
    if claims.get("client_id") or claims.get("aud"):
        return "user_session"
    return "unknown"


async def build_auth_diagnostics(
    auth_provider: Any | None,
    headers: Iterable[tuple[bytes, bytes]],
) -> dict[str, Any]:
    """Summarize whether a presented bearer token is usable.

    The caller can use this for a public diagnostics endpoint or for an
    early request gate on `/mcp` requests that already present a token.
    Missing tokens are reported as discovery-compatible, not as errors.
    """
    token = extract_bearer_token(headers)
    if token is None:
        return dict(
            status="unauthenticated",
            token_present=False,
            token_valid=False,
            discovery_compatible=True,
            message="No bearer token presented.",
        )

    if auth_provider is None:
        return dict(
            status="unavailable",
            token_present=True,
            token_valid=False,
            discovery_compatible=False,
            message="Authentication provider is not configured.",
        )

    try:
        access = await auth_provider.verify_token(token)
    except Exception:
        return dict(
            status="invalid",
            token_present=True,
            token_valid=False,
            discovery_compatible=False,
            message="Presented bearer token could not be validated.",
        )
    if access is None:
        return dict(
            status="invalid",
            token_present=True,
            token_valid=False,
            discovery_compatible=False,
            message="Presented bearer token was rejected.",
        )

    claims = getattr(access, "claims", {}) or {}
    scope_value = claims.get("scope")
    scopes: list[str] = []
    if isinstance(scope_value, str):
        scopes = [scope for scope in scope_value.split() if scope]
    elif isinstance(scope_value, list):
        scopes = [str(scope) for scope in scope_value if scope]

    return dict(
        status="ok",
        token_present=True,
        token_valid=True,
        discovery_compatible=True,
        principal_hint=_principal_hint(claims),
        subject=claims.get("sub"),
        agent_id=claims.get("agent_id"),
        agent_name=claims.get("agent_name"),
        space_id=claims.get("space_id"),
        client_id=claims.get("client_id") or claims.get("aud"),
        scopes=scopes,
        tools_allowed=claims.get("tools_allowed") is not None,
    )


class EarlyBearerAuthValidationMiddleware:
    """Reject invalid presented bearer tokens before the MCP stack runs.

    Discovery remains compatible because requests without a bearer token are
    passed through untouched. This makes stale sessions fail fast on the early
    initialize path without blocking unauthenticated MCP discovery.
    """

    def __init__(self, app, auth_provider: Any | None):
        self.app = app
        self.auth_provider = auth_provider

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        token = extract_bearer_token(scope.get("headers", []))
        if token is None:
            await self.app(scope, receive, send)
            return

        diagnostics = await build_auth_diagnostics(
            self.auth_provider,
            scope.get("headers", []),
        )
        if diagnostics["token_valid"]:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(diagnostics, status_code=401)
        response.headers["WWW-Authenticate"] = _oauth_challenge_header(
            scope.get("headers", []),
            error="invalid_token",
            error_description="The access token expired or is invalid",
        )
        await response(scope, receive, send)


def _header_value(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> str | None:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value.decode("utf-8", errors="ignore")
    return None


def _mcp_request_requires_auth(payload: Any) -> bool:
    """Return whether a JSON-RPC request contains a protected MCP method."""
    if isinstance(payload, list):
        return any(_mcp_request_requires_auth(item) for item in payload)
    if not isinstance(payload, dict):
        return False
    return payload.get("method") in PROTECTED_MCP_METHODS


def _agent_resource_urls(headers: Iterable[tuple[bytes, bytes]]) -> tuple[str, str]:
    base_url = os.getenv("MCP_SERVER_URL", "http://localhost:3000").rstrip("/")
    agent_name = _header_value(headers, b"x-agent-name")
    if agent_name:
        encoded_agent = quote(agent_name, safe="")
        resource_path = f"/mcp/agents/{encoded_agent}"
    else:
        resource_path = "/mcp"

    resource = f"{base_url}{resource_path}"
    resource_metadata = f"{base_url}/.well-known/oauth-protected-resource{resource_path}"
    return resource, resource_metadata


def _oauth_challenge_scope() -> str:
    if os.getenv("AX_AUTH_MODE", "remote").strip().lower() == "remote":
        return "openid offline_access ax-api/mcp:read ax-api/mcp:write"
    return "openid"


def _oauth_challenge_header(
    headers: Iterable[tuple[bytes, bytes]],
    *,
    error: str | None = None,
    error_description: str | None = None,
) -> str:
    resource, resource_metadata = _agent_resource_urls(headers)
    parts = [
        'Bearer realm="mcp"',
        f'resource_metadata="{resource_metadata}"',
        f'resource="{resource}"',
        f'scope="{_oauth_challenge_scope()}"',
    ]
    if error:
        parts.insert(1, f'error="{error}"')
    if error_description:
        insert_at = 2 if error else 1
        parts.insert(insert_at, f'error_description="{error_description}"')
    return ", ".join(parts)


class MCPProtectedMethodAuthChallengeMiddleware:
    """Return a real OAuth challenge for protected MCP methods without a token.

    OAuth-aware MCP clients expect a transport-level 401 for initialize and
    protected JSON-RPC methods so they can discover the protected-resource
    metadata URL before opening an authenticated session.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/mcp"
            or extract_bearer_token(scope.get("headers", [])) is not None
        ):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        more_body = True
        body_size = 0
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            chunks.append(chunk)
            body_size += len(chunk)
            if body_size > MAX_MCP_AUTH_CHALLENGE_BODY_BYTES:
                response = JSONResponse(
                    {
                        "error": "invalid_request",
                        "error_description": "MCP request body is too large.",
                    },
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            more_body = message.get("more_body", False)

        body = b"".join(chunks)
        requires_auth = False
        try:
            payload = json.loads(body or b"{}")
            requires_auth = _mcp_request_requires_auth(payload)
        except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
            requires_auth = False

        if requires_auth:
            response = JSONResponse(
                {
                    "error": "authorization_required",
                    "error_description": (
                        "Authentication required for this MCP method. "
                        "Fetch protected-resource metadata and connect with OAuth."
                    ),
                },
                status_code=401,
            )
            response.headers["WWW-Authenticate"] = _oauth_challenge_header(scope.get("headers", []))
            await response(scope, receive, send)
            return

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)


class OAuthProtectedResourceNoStoreMiddleware:
    """Prevent stale public caches for OAuth protected-resource metadata."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "GET"
            or not scope.get("path", "").startswith("/.well-known/oauth-protected-resource")
        ):
            await self.app(scope, receive, send)
            return

        async def no_store_send(message):
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in {b"cache-control", b"expires"}
                ]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, no_store_send)


class RequireTokenForToolCallsMiddleware(Middleware):
    """Allow unauthenticated discovery, but require a bearer token for tool calls."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        if get_access_token() is None:
            params = getattr(context, "message", None)
            logger.warning(
                "Rejecting unauthenticated MCP tools/call",
                extra={"mcp_tool_name": getattr(params, "name", None)},
            )
            raise AuthorizationError(
                "Authentication required for tools/call. "
                "MCP initialize and tools/list may be unauthenticated for discovery; "
                "tool execution requires a valid bearer token."
            )
        return await call_next(context)


def ax_tool_filter() -> AuthCheck:
    """Filter tools based on tools_allowed JWT claim.

    - No tools_allowed claim: backend API permissions still apply
    - tools_allowed present: only listed tools accessible (FR-004/FR-005)
    """

    def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return True
        tools_allowed = ctx.token.claims.get("tools_allowed")
        if tools_allowed is None:
            return True  # Backend API authorizes the requested operation.
        tool = ctx.component
        if tool is None:
            return True  # resources/prompts pass through
        if tool.name not in tools_allowed:
            raise AuthorizationError(
                f"Tool '{tool.name}' not permitted. Allowed: {tools_allowed}"
            )
        return True

    return check
