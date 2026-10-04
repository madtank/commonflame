"""Commonflame MCP resource server.

Backend-issued JWTs are validated through JWKS. The backend owns OAuth
authorization and every data mutation; MCP provides stateless Streamable HTTP.
"""
import asyncio
import json
import logging
import os
import re
from urllib.parse import parse_qsl, urlencode

from fastmcp import FastMCP
from fastmcp.server.auth import RemoteAuthProvider

from fastmcp_server.config import (
    MCP_SERVER_NAME,
    FASTMCP_PORT,
    AX_AUTH_MODE,
    AX_AUTH_SERVER_URL,
    AX_AUTH_SERVER_INTERNAL_URL,
    MCP_SERVER_URL,
    MCP_STATELESS_HTTP,
    BACKEND_JWKS_URI,
    BACKEND_ISSUER,
)

logger = logging.getLogger(__name__)

# Max bytes the anonymous-discovery middleware will buffer from an
# unauthenticated POST /mcp before rejecting with 413. Kept independent from
# auth_checks.MAX_MCP_AUTH_CHALLENGE_BODY_BYTES so the two limits can evolve
# separately (a discovery request is tiny; an auth-challenge body may not be).
MAX_ANONYMOUS_DISCOVERY_BODY_BYTES = 64 * 1024

DEFAULT_GLAMA_MAINTAINER_EMAIL = ""

# Real MCP JSON-RPC methods that an unauthenticated client may legitimately
# call and that must still reach the authed stack so the client receives the
# OAuth 401 challenge (auth discovery). Anything outside this set AND not
# handled cheaply by the discovery middleware (ping/tools-list/
# notifications/initialized/logging-setLevel) is vendor/garbage traffic (e.g.
# `ai.smithery/events/list`) and is rejected with a cheap -32601 before it can
# reach expensive Pydantic ClientRequest validation. See HEALTH-RESILIENCE-001.
PROTECTED_MCP_METHODS_NEEDING_AUTH_CHALLENGE = frozenset(
    {
        "initialize",
        "tools/call",
        "resources/list",
        "resources/read",
        "resources/templates/list",
        "resources/subscribe",
        "resources/unsubscribe",
        "prompts/list",
        "prompts/get",
        "completion/complete",
        "server/discover",
        "tasks/get",
        "tasks/update",
        "tasks/cancel",
        "subscriptions/listen",
    }
)


def _origin_allowed(origin: str, cors_list: list[str]) -> bool:
    """Whether ``origin`` is permitted by the configured CORS origins.

    Single source of truth for the allow-origin decision shared by the
    anonymous-discovery CORS path (the main Starlette CORSMiddleware owns its
    own equivalent check on the authenticated path).
    """
    return "*" in cors_list or origin in cors_list

AUTH_ROOT_PREFIXES = (
    "/.well-known/",
    "/authorize",
    "/token",
    "/register",
    "/revoke",
    "/auth/",
)


def rewrite_agent_route(path: str) -> tuple[str | None, str]:
    """Normalize /mcp/agents/{name} paths to the canonical FastMCP routes.

    Agent-path URLs are a transport convenience. MCP protocol requests should
    land on `/mcp`, while OAuth discovery/token routes must remain at the app
    root so standards-compliant clients can complete discovery from either
    entrypoint.

    Also handles RFC 9728 protected resource metadata discovery paths:
    /.well-known/oauth-protected-resource/mcp/agents/{name}
    → rewrites to /.well-known/oauth-protected-resource/mcp
    """
    # Standard agent-path URLs: /mcp/agents/{name}/...
    match = re.match(r"^/mcp/agents/([^/]+)(.*)", path)
    if match:
        agent_name = match.group(1)
        remainder = match.group(2) or ""

        if remainder and remainder.startswith(AUTH_ROOT_PREFIXES):
            return agent_name, remainder
        return agent_name, f"/mcp{remainder}"

    # RFC 9728: clients construct protected resource metadata URLs by inserting
    # /.well-known/oauth-protected-resource before the resource path. For agent
    # URLs, this produces:
    #   /.well-known/oauth-protected-resource/mcp/agents/{name}
    # We strip the agent segment so it matches the registered route:
    #   /.well-known/oauth-protected-resource/mcp
    rfc9728_match = re.match(
        r"^(/\.well-known/oauth-protected-resource)/mcp/agents/([^/]+)(/.*)?$",
        path,
    )
    if rfc9728_match:
        agent_name = rfc9728_match.group(2)
        return agent_name, f"{rfc9728_match.group(1)}/mcp"

    return None, path


def normalize_authorize_resource_query(query_string: bytes, base_url: str) -> bytes:
    """Normalize agent-path OAuth resource indicators to the canonical MCP resource.

    Public agent aliases are transport routes, not separate OAuth resources.
    Older clients visiting the MCP-side authorize alias use the canonical
    resource. Native backend authorization retains requested agent bindings.
    """
    if not query_string:
        return query_string

    canonical_resource = f"{base_url.rstrip('/')}/mcp"
    agent_resource_prefix = f"{canonical_resource}/agents/"
    params = parse_qsl(
        query_string.decode("utf-8", errors="ignore"),
        keep_blank_values=True,
    )

    changed = False
    normalized_params: list[tuple[str, str]] = []
    for key, value in params:
        if key == "resource" and value.startswith(agent_resource_prefix):
            normalized_params.append((key, canonical_resource))
            changed = True
            continue
        normalized_params.append((key, value))

    if not changed:
        return query_string
    return urlencode(normalized_params).encode("utf-8")


def create_auth() -> RemoteAuthProvider:
    """Trust the backend authorization server; configuration errors fail closed."""
    if AX_AUTH_MODE != "remote":
        raise RuntimeError("Commonflame requires AX_AUTH_MODE=remote")
    for name, value in (
        ("AX_AUTH_SERVER_URL", AX_AUTH_SERVER_URL),
        ("BACKEND_JWKS_URI", BACKEND_JWKS_URI),
        ("BACKEND_ISSUER", BACKEND_ISSUER),
    ):
        if not value:
            raise RuntimeError(f"{name} is required when AX_AUTH_MODE=remote")

    from fastmcp_server.ax_remote_auth import AxRemoteAuthProvider

    return AxRemoteAuthProvider(
        auth_server_url=AX_AUTH_SERVER_URL,
        auth_server_internal_url=AX_AUTH_SERVER_INTERNAL_URL,
        backend_jwks_uri=BACKEND_JWKS_URI,
        backend_issuer=BACKEND_ISSUER,
        mcp_server_url=MCP_SERVER_URL,
    )


def create_server() -> FastMCP:
    """Create and configure the FastMCP server."""
    from fastmcp.server.middleware.authorization import AuthMiddleware
    from fastmcp_server.auth_checks import (
        RequireTokenForToolCallsMiddleware,
        ax_tool_filter,
    )

    auth = create_auth()

    kwargs = {"name": MCP_SERVER_NAME}
    if auth is not None:
        kwargs["auth"] = auth

    mcp = FastMCP(**kwargs)
    # MCP 2026 background tasks are a negotiated extension. Legacy clients
    # keep synchronous messages.check; capable clients may run it in the task
    # engine. Redis shares task state across stateless Uvicorn workers.
    from fastmcp_tasks import TasksExtension
    mcp.add_extension(TasksExtension(url=os.getenv("REDIS_URL") or None))

    # Tool filtering: backend-issued tokens carry tools_allowed claim.
    # AuthMiddleware filters tools/list and rejects tools/call accordingly.
    # In mixed-auth mode, unauthenticated tools/list should still work, so
    # ax_tool_filter passes through when there is no token.
    if auth is not None:
        mcp.add_middleware(RequireTokenForToolCallsMiddleware())
        mcp.add_middleware(AuthMiddleware(auth=ax_tool_filter()))

    # Tool call notification: fire-and-forget POST to backend after every
    # tools/call. Provides audit trail + SSE broadcast for frontend widgets.
    from fastmcp_server.tool_call_notifier import ToolCallNotificationMiddleware
    mcp.add_middleware(ToolCallNotificationMiddleware())

    # Hard deadline on tool dispatch: a hung tool call is cancelled and returns
    # a structured error instead of pinning the worker until ELB health checks
    # recycle the task (2026-06-10 prod incident).
    from fastmcp_server.tool_timeouts import ToolCallTimeoutMiddleware
    mcp.add_middleware(ToolCallTimeoutMiddleware())

    # Register tools, resources, and prompts
    from fastmcp_server.tools import register_all_tools
    from fastmcp_server.resources import register_all_resources
    from fastmcp_server.prompts import register_all_prompts

    register_all_tools(mcp)
    register_all_resources(
        mcp,
        enable_inbox_notifications=not MCP_STATELESS_HTTP,
    )
    register_all_prompts(mcp)

    return mcp


# Create the server instance
server = create_server()


def create_app():
    """Serve /mcp, widget resources, health and OAuth resource discovery.

    The backend owns /oauth/authorize, /oauth/token and /oauth/register.
    MCP advertises the backend AS via /.well-known/oauth-protected-resource/mcp.
    """
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount
    from starlette.requests import Request

    from fastmcp_server.apps_endpoint import apps_bridge, apps_d3, apps_get, apps_list
    from fastmcp_server.mcp_ui import D3_ASSET_PATH, MCP_APPS_BRIDGE_PATH
    from fastmcp_server.auth_checks import (
        EarlyBearerAuthValidationMiddleware,
        MCPProtectedMethodAuthChallengeMiddleware,
        OAuthProtectedResourceNoStoreMiddleware,
        build_auth_diagnostics,
    )

    async def health(request: Request):
        """Health check for Docker/load balancer."""
        return JSONResponse({"status": "ok", "server": "Commonflame MCP", "stateless": MCP_STATELESS_HTTP})

    async def auth_diagnostics(request: Request):
        """Expose a lightweight auth/session probe without invoking a tool call."""
        diagnostics = await build_auth_diagnostics(
            auth,
            request.scope.get("headers", []),
        )
        status_code = 200 if diagnostics["token_valid"] or not diagnostics["token_present"] else 401
        response = JSONResponse(diagnostics, status_code=status_code)
        if diagnostics["token_present"] and not diagnostics["token_valid"]:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

    async def glama_ownership(request: Request):
        """Serve Glama's public connector verification metadata."""
        emails = [
            email.strip()
            for email in (os.getenv("GLAMA_MAINTAINER_EMAILS") or DEFAULT_GLAMA_MAINTAINER_EMAIL).split(",")
            if email.strip()
        ]
        return JSONResponse(
            {
                "$schema": "https://glama.ai/mcp/schemas/connector.json",
                "maintainers": [{"email": email} for email in emails],
            }
        )

    def _jsonrpc_response(request_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _jsonrpc_error(request_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def _discovery_payload(method: str, request_id, params: dict | None) -> dict | None:
        """Return a side-effect-free anonymous MCP discovery response.

        We intentionally do not forward anonymous tools/list to
        FastMCP's Streamable HTTP session manager: abandoned scanner sessions can
        wedge stateful single-worker deployments. Initialize remains protected
        and flows to the OAuth challenge path; cheap public catalog discovery is
        limited to tools/list plus liveness/notification probes.
        """
        if method == "ping":
            # Catalogs such as Glama probe Streamable HTTP connectors without an
            # OAuth session. Let anonymous liveness pings complete without
            # entering FastMCP's stateful session manager; otherwise a public
            # crawler can wait until its timeout even though /health and
            # tools/list are healthy.
            return _jsonrpc_response(request_id, {})
        if method == "logging/setLevel":
            # MCPJam sets log level after initialization. Answer it without
            # entering the stateful MCP app so anonymous catalog scans cannot
            # wedge single-worker deployments.
            return _jsonrpc_error(request_id, -32601, "Method not found")
        return None

    # HEALTH-RESILIENCE-001: the registered toolset is fixed for the life of
    # the process, so the serialized anonymous tools/list array is invariant.
    # Build it once and reuse it; otherwise every scanner tools/list call pays
    # a full Pydantic model_dump of all tools (large now that every tool carries
    # an outputSchema), which is the main CPU sink that wedges /health under
    # registry-scan load. The lock collapses concurrent first hits into one
    # build; later hits are a dict construction only.
    _tools_list_cache: dict[str, list] = {}
    _tools_list_lock = asyncio.Lock()

    async def _build_anonymous_tools_array() -> list:
        return [
            tool.to_mcp_tool().model_dump(by_alias=True, exclude_none=True)
            for tool in await server.list_tools()
        ]

    async def _tools_list_payload(request_id, params: dict | None) -> dict:
        tools = _tools_list_cache.get("tools")
        if tools is None:
            async with _tools_list_lock:
                tools = _tools_list_cache.get("tools")
                if tools is None:
                    tools = await _build_anonymous_tools_array()
                    _tools_list_cache["tools"] = tools
        result: dict = {"tools": tools}
        if params and params.get("cursor"):
            result["nextCursor"] = None
        return _jsonrpc_response(request_id, result)

    def _wants_sse(headers: list[tuple[bytes, bytes]]) -> bool:
        for key, value in headers:
            if key.lower() == b"accept" and b"text/event-stream" in value.lower():
                return True
        return False

    def _discovery_cors_headers(scope) -> list[tuple[bytes, bytes]]:
        """CORS headers for the short-circuit discovery response.

        This middleware answers before the inner CORSMiddleware can run, so a
        cross-origin browser discovery request would otherwise get a response
        with no Access-Control-Allow-Origin and be rejected. Mirror the
        configured CORS policy (CORS_ORIGINS) here. NOTE: this intentionally
        duplicates the allow-origin logic of the main CORSMiddleware
        configured below — keep the two in sync if the CORS policy changes.
        """
        origin = None
        for key, value in scope.get("headers", []):
            if key.lower() == b"origin":
                origin = value
                break
        if origin is None:
            return []
        allowed = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
        if not _origin_allowed(origin.decode("latin-1"), allowed):
            return []
        if "*" in allowed:
            return [(b"access-control-allow-origin", b"*"), (b"vary", b"Origin")]
        if origin.decode("latin-1") in allowed:
            return [
                (b"access-control-allow-origin", origin),
                (b"vary", b"Origin"),
                (b"access-control-allow-credentials", b"true"),
            ]
        return []

    async def _send_status(
        send,
        status: int,
        message: bytes,
        extra_headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        headers = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(message)).encode("ascii")),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": message, "more_body": False})

    async def _send_discovery_response(scope, send, response_payload) -> None:
        body = json.dumps(response_payload, separators=(",", ":")).encode("utf-8")
        if _wants_sse(scope.get("headers", [])):
            body = b"event: message\ndata: " + body + b"\n\n"
            headers = [(b"content-type", b"text/event-stream")]
        else:
            headers = [(b"content-type", b"application/json")]
        headers.append((b"content-length", str(len(body)).encode("ascii")))
        headers.extend(_discovery_cors_headers(scope))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    class AnonymousMCPDiscoveryMiddleware:
        """Expose cheap anonymous discovery without opening MCP sessions."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if (
                scope["type"] != "http"
                or scope.get("method") != "POST"
                or scope.get("path") != "/mcp"
            ):
                await self.app(scope, receive, send)
                return

            # Authenticated requests should flow through the normal MCP stack so
            # clients keep full session semantics after OAuth.
            for key, value in scope.get("headers", []):
                if key.lower() == b"authorization" and value.strip():
                    await self.app(scope, receive, send)
                    return

            # Sessionless-era requests must reach SDK v2 so it validates
            # Mcp-Method/Mcp-Name and serves server/discover correctly. The
            # inner auth/body-size gate still protects all data operations.
            if any(
                key.lower() == b"mcp-protocol-version" and value >= b"2026-07-28"
                for key, value in scope.get("headers", [])
            ):
                await self.app(scope, receive, send)
                return

            chunks: list[bytes] = []
            buffered = 0
            more_body = True
            while more_body:
                message = await receive()
                if message["type"] == "http.disconnect":
                    # The client disconnected while the middleware was reading
                    # the one-shot ASGI body stream. We may already have
                    # consumed part of the body, so forwarding to downstream
                    # would present a truncated request; there is no response to
                    # send to a disconnected client anyway.
                    return
                if message["type"] != "http.request":
                    continue
                chunk = message.get("body", b"")
                buffered += len(chunk)
                if buffered > MAX_ANONYMOUS_DISCOVERY_BODY_BYTES:
                    # Bound the buffer: an unauthenticated POST /mcp is fully
                    # read here before the inner 64 KiB limiter can run, so cap
                    # it ourselves. A discovery request (tools/list)
                    # is tiny; anything larger is rejected cleanly instead of
                    # being buffered into unbounded memory.
                    await _send_status(
                        send,
                        413,
                        b"Payload Too Large",
                        _discovery_cors_headers(scope),
                    )
                    return
                chunks.append(chunk)
                more_body = message.get("more_body", False)

            body = b"".join(chunks)
            discovery_response = None
            method = None
            request_id = None
            try:
                payload = json.loads(body or b"{}")
                if isinstance(payload, dict):
                    method = payload.get("method")
                    request_id = payload.get("id")
                    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                    if method == "notifications/initialized":
                        await _send_status(send, 200, b"", _discovery_cors_headers(scope))
                        return
                    if method == "tools/list":
                        discovery_response = await _tools_list_payload(request_id, params)
                    elif isinstance(method, str):
                        discovery_response = _discovery_payload(method, request_id, params)
            except (TypeError, json.JSONDecodeError, UnicodeDecodeError):
                discovery_response = None
                method = None

            if discovery_response is not None:
                await _send_discovery_response(scope, send, discovery_response)
                return

            # HEALTH-RESILIENCE-001: fast-reject non-MCP methods on the anonymous
            # path. A real protected method (tools/call, resources/*, prompts/*)
            # must still replay so the client gets the OAuth 401 challenge, and
            # notifications stay fire-and-forget through the normal stack. But
            # vendor/garbage methods like `ai.smithery/events/list` would
            # otherwise replay into full Pydantic ClientRequest validation
            # (observed: "28 validation errors") — answer those with a cheap
            # -32601 before they reach that path.
            if (
                isinstance(method, str)
                and method
                and not method.startswith("notifications/")
                and method not in PROTECTED_MCP_METHODS_NEEDING_AUTH_CHALLENGE
            ):
                await _send_discovery_response(
                    scope, send, _jsonrpc_error(request_id, -32601, "Method not found")
                )
                return

            replayed = False

            async def replay_receive():
                nonlocal replayed
                if replayed:
                    return {"type": "http.request", "body": b"", "more_body": False}
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}

            await self.app(scope, replay_receive, send)

    # Build a mixed-auth Streamable HTTP app. OAuth-aware clients discover auth
    # through a 401 challenge on initialize/protected calls, while anonymous
    # tools/list is answered by AnonymousMCPDiscoveryMiddleware without opening
    # stateful FastMCP sessions.
    # FastMCP's default auth wiring protects the entire /mcp route, so we attach
    # auth middleware + OAuth routes manually without wrapping /mcp in
    # RequireAuthMiddleware.
    from fastmcp.server.http import create_streamable_http_app

    auth = server.auth
    auth_middleware = auth.get_middleware() if auth is not None else None
    auth_routes = auth.get_routes(mcp_path="/mcp") if auth is not None else None
    mcp_app = create_streamable_http_app(
        server=server,
        streamable_http_path="/mcp",
        auth=None,
        middleware=auth_middleware,
        routes=auth_routes,
        stateless_http=MCP_STATELESS_HTTP,
    )

    # ASGI middleware that resolves agent name from URL path or header.
    # In stateless mode every request is independent; in stateful mode this
    # same header injection gives initialize, tools/call, and GET/SSE streams
    # the connected agent identity.
    class AgentRouteMiddleware:
        """Resolves agent name from URL path or header and injects X-Agent-Name.

        Clients connect via /mcp/agents/{name} and the middleware strips the
        prefix, injecting the agent name as a header for tools and subscription
        handlers to read.
        """

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                path = scope["path"]
                headers = list(scope.get("headers", []))

                agent_name = None

                # Source 1: URL path /mcp/agents/{name}
                agent_name, scope["path"] = rewrite_agent_route(path)

                # Source 2: X-Agent-Name header
                if not agent_name:
                    for k, v in headers:
                        if k == b"x-agent-name":
                            agent_name = v.decode()
                            break

                # Inject header so tools always see it
                if agent_name:
                    headers = [(k, v) for k, v in headers if k != b"x-agent-name"]
                    headers.append((b"x-agent-name", agent_name.encode()))
                    scope["headers"] = headers

                if scope["path"] == "/authorize":
                    scope["query_string"] = normalize_authorize_resource_query(
                        scope.get("query_string", b""),
                        MCP_SERVER_URL,
                    )

            await self.app(scope, receive, send)

    routes = [
        Route("/health", health),
        # Scoped MCP connector health: public catalogs/operators can probe the
        # MCP namespace (or /mcp/agents/{name}/health via AgentRouteMiddleware)
        # without opening a Streamable HTTP session or touching auth/tool paths.
        Route("/mcp/health", health),
        Route("/auth/diagnostics", auth_diagnostics),
        Route("/.well-known/glama.json", glama_ownership),
        Route(MCP_APPS_BRIDGE_PATH, apps_bridge),
        Route(D3_ASSET_PATH, apps_d3),
        # MCP Apps: REST endpoints for frontend widget HTML serving.
        # No auth — HTML is static. Widgets call tools via /mcp (authenticated).
        Route("/apps", apps_list),
        Route("/apps/{name:path}", apps_get),
        # MCP protocol + OAuth — FastMCP's http_app() handles everything
        Mount("/", app=mcp_app),
    ]

    # CORS for browser-hosted MCP UI. Frontend widgets resolve resources via
    # cross-origin POST /mcp resources/read and invoke tools via POST /mcp
    # tools/call, GET /mcp hosts stateful SSE, and DELETE /mcp tears down
    # stateful Streamable HTTP sessions.
    cors_origins = os.getenv("CORS_ORIGINS", "*")
    cors_list = [o.strip() for o in cors_origins.split(",") if o.strip()]
    allow_credentials = "*" not in cors_list
    cors_middleware = [
        Middleware(OAuthProtectedResourceNoStoreMiddleware),
        Middleware(AnonymousMCPDiscoveryMiddleware),
        Middleware(MCPProtectedMethodAuthChallengeMiddleware),
        Middleware(
            EarlyBearerAuthValidationMiddleware,
            auth_provider=auth,
        ),
        Middleware(
            CORSMiddleware,
            allow_origins=cors_list,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
            allow_credentials=allow_credentials,
        ),
    ]

    app = Starlette(routes=routes, lifespan=mcp_app.lifespan, middleware=cors_middleware)
    app = AgentRouteMiddleware(app)
    return app


def uvicorn_bind_config() -> dict[str, int | str]:
    """Return the direct `python -m fastmcp_server.server` bind config.

    Default to loopback for local direct execution. Container deployments that
    need to accept traffic from Docker/Kubernetes networking must opt in with
    FASTMCP_HOST=0.0.0.0 rather than inheriting a wildcard bind implicitly.
    """
    return {
        "host": os.getenv("FASTMCP_HOST", "127.0.0.1"),
        "port": FASTMCP_PORT,
    }


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    # Enable DEBUG on JWT verifier to diagnose auth chain failures
    logging.getLogger("fastmcp.server.auth").setLevel(logging.DEBUG)
    app = create_app()
    uvicorn.run(app, **uvicorn_bind_config())
