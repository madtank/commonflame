"""Remote OAuth auth provider for Waystation-issued MCP tokens.

In this mode the MCP server is only a resource server. It validates
backend-issued Waystation JWTs and advertises the backend as the Authorization Server.
"""

from __future__ import annotations

import logging

import httpx
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Route


logger = logging.getLogger(__name__)

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


class AxRemoteAuthProvider(RemoteAuthProvider):
    """FastMCP RemoteAuthProvider configured for Waystation as the AS."""

    def __init__(
        self,
        *,
        auth_server_url: str,
        auth_server_internal_url: str | None = None,
        backend_jwks_uri: str,
        backend_issuer: str,
        mcp_server_url: str,
    ):
        self.auth_server_url = auth_server_url.rstrip("/")
        self.auth_server_internal_url = (
            auth_server_internal_url.rstrip("/")
            if auth_server_internal_url
            else self.auth_server_url
        )
        mcp_base_url = mcp_server_url.rstrip("/")
        resource_audience = (
            mcp_base_url if mcp_base_url.endswith("/mcp") else f"{mcp_base_url}/mcp"
        )
        token_verifier = JWTVerifier(
            jwks_uri=backend_jwks_uri,
            issuer=backend_issuer,
            audience=resource_audience,
            required_scopes=[],
        )
        self.resource_audience = resource_audience
        super().__init__(
            token_verifier=token_verifier,
            authorization_servers=[AnyHttpUrl(self.auth_server_url)],
            base_url=AnyHttpUrl(mcp_base_url),
            scopes_supported=SCOPES_SUPPORTED,
            resource_name="Waystation MCP",
            # The Waystation backend AS serves /auth.md as the agent-readable integration doc.
            resource_documentation=AnyHttpUrl(f"{self.auth_server_url}/auth.md"),
        )

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)

        async def authorization_server_metadata(_request):
            # TODO(REMOTE-AUTH-001): add a short-lived cache if discovery volume grows.
            async with httpx.AsyncClient(timeout=5.0) as client:
                try:
                    response = await client.get(
                        f"{self.auth_server_internal_url}/.well-known/oauth-authorization-server"
                    )
                    response.raise_for_status()
                    try:
                        metadata = response.json()
                    except ValueError:
                        logger.warning(
                            "Waystation AS metadata returned non-JSON body from %s",
                            self.auth_server_internal_url,
                        )
                        return JSONResponse(
                            {"error": "temporarily_unavailable"},
                            status_code=503,
                        )
                    return JSONResponse(metadata)
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "Waystation AS metadata returned upstream status %s from %s",
                        exc.response.status_code,
                        self.auth_server_internal_url,
                    )
                    return JSONResponse(
                        {"error": "temporarily_unavailable"},
                        status_code=503,
                    )
                except httpx.HTTPError:
                    logger.warning(
                        "Waystation AS metadata request failed for %s",
                        self.auth_server_internal_url,
                        exc_info=True,
                    )
                    return JSONResponse(
                        {"error": "temporarily_unavailable"},
                        status_code=503,
                    )

        routes.append(
            Route(
                "/.well-known/oauth-authorization-server",
                endpoint=authorization_server_metadata,
                methods=["GET"],
            )
        )
        return routes
