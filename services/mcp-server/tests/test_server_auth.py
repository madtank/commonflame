"""Resource-server OAuth discovery, audience and fail-closed regression checks."""
import asyncio
import importlib
import json
import os
import sys
import unittest
from unittest.mock import patch

import httpx2 as httpx
from starlette.testclient import TestClient


def _import_server_module():
    sys.modules.pop("fastmcp_server.server", None)
    sys.modules.pop("fastmcp_server.config", None)
    return importlib.import_module("fastmcp_server.server")


class CreateAuthTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "AX_AUTH_SERVER_INTERNAL_URL": "http://backend:8080",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
    })
    def test_remote_auth_mode_uses_ax_as_authorization_server(self):
        """Remote mode makes MCP a resource server and advertises the backend as AS."""
        from fastmcp.server.auth import RemoteAuthProvider
        server_mod = _import_server_module()

        auth = server_mod.create_auth()

        self.assertIsInstance(auth, RemoteAuthProvider)
        self.assertEqual(
            [str(server).rstrip("/") for server in auth.authorization_servers],
            ["http://localhost:3000"],
        )
        self.assertEqual(
            auth.token_verifier.jwks_uri,
            "http://localhost:3000/.well-known/jwks.json",
        )
        self.assertEqual(auth.token_verifier.issuer, "http://localhost:3000")
        self.assertEqual(auth.token_verifier.audience, "http://localhost:3000/mcp")
        self.assertEqual(auth.auth_server_internal_url, "http://backend:8080")

    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
    })
    def test_remote_auth_routes_include_prm_and_ax_as_metadata_forwarder(self):
        """Clients can discover protected-resource metadata and AS metadata."""
        server_mod = _import_server_module()

        auth = server_mod.create_auth()
        paths = {route.path for route in auth.get_routes(mcp_path="/mcp")}

        self.assertIn("/.well-known/oauth-protected-resource/mcp", paths)
        self.assertIn("/.well-known/oauth-authorization-server", paths)

    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "AX_AUTH_SERVER_INTERNAL_URL": "http://backend:8080",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
    })
    def test_protected_resource_metadata_is_not_publicly_cached(self):
        """Browsers must not use stale MCP OAuth metadata after deploys."""
        server_mod = _import_server_module()
        client = TestClient(server_mod.create_app())

        response = client.get(
            "/.well-known/oauth-protected-resource/mcp/agents/connect_probe"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("ax-api/mcp:write", response.json()["scopes_supported"])

    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "AX_AUTH_SERVER_INTERNAL_URL": "http://backend:8080",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
        "GLAMA_MAINTAINER_EMAILS": "owner@example.com, ops@example.com",
    })
    def test_glama_ownership_file_is_served_at_well_known_path(self):
        """Glama verifies connector ownership through the apex well-known file."""
        server_mod = _import_server_module()
        client = TestClient(server_mod.create_app())

        response = client.get("/.well-known/glama.json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(
            response.json(),
            {
                "$schema": "https://glama.ai/mcp/schemas/connector.json",
                "maintainers": [
                    {"email": "owner@example.com"},
                    {"email": "ops@example.com"},
                ],
            },
        )

    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "AX_AUTH_SERVER_INTERNAL_URL": "http://backend:8080",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
    })
    def test_remote_auth_metadata_forwarder_returns_503_when_backend_unavailable(self):
        """AS metadata forwarding should fail as structured OAuth JSON."""
        server_mod = _import_server_module()
        auth = server_mod.create_auth()
        route = next(
            route
            for route in auth.get_routes(mcp_path="/mcp")
            if route.path == "/.well-known/oauth-authorization-server"
        )

        class FailingClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, _url):
                raise httpx.ConnectError("backend unavailable")

        with patch("fastmcp_server.ax_remote_auth.httpx.AsyncClient", FailingClient):
            response = asyncio.run(route.endpoint(None))

        self.assertEqual(response.status_code, 503)

    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "AX_AUTH_SERVER_INTERNAL_URL": "http://backend:8080",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
    })
    def test_remote_auth_metadata_forwarder_passes_through_valid_json(self):
        """AS metadata forwarding should return backend JSON unchanged."""
        server_mod = _import_server_module()
        auth = server_mod.create_auth()
        route = next(
            route
            for route in auth.get_routes(mcp_path="/mcp")
            if route.path == "/.well-known/oauth-authorization-server"
        )

        class MetadataClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, _url):
                return httpx.Response(
                    200,
                    json={"issuer": "http://localhost:3000"},
                    request=httpx.Request("GET", _url),
                )

        with patch("fastmcp_server.ax_remote_auth.httpx.AsyncClient", MetadataClient):
            response = asyncio.run(route.endpoint(None))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"issuer": "http://localhost:3000"})

    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "AX_AUTH_SERVER_INTERNAL_URL": "http://backend:8080",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
    })
    def test_remote_auth_metadata_forwarder_returns_503_for_non_json_body(self):
        """AS metadata forwarding should not leak non-JSON upstream failures."""
        server_mod = _import_server_module()
        auth = server_mod.create_auth()
        route = next(
            route
            for route in auth.get_routes(mcp_path="/mcp")
            if route.path == "/.well-known/oauth-authorization-server"
        )

        class NonJsonClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, _url):
                return httpx.Response(
                    200,
                    content=b"<html>not json</html>",
                    request=httpx.Request("GET", _url),
                )

        with patch("fastmcp_server.ax_remote_auth.httpx.AsyncClient", NonJsonClient):
            response = asyncio.run(route.endpoint(None))

        self.assertEqual(response.status_code, 503)

    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
    })
    def test_remote_auth_requires_backend_jwks_uri(self):
        """Remote mode must know where to verify Waystation-issued access tokens."""
        server_mod = _import_server_module()

        with patch.object(server_mod, "BACKEND_JWKS_URI", ""):
            with self.assertRaisesRegex(RuntimeError, "BACKEND_JWKS_URI is required"):
                server_mod.create_auth()

    @patch.dict(os.environ, {
        "AX_AUTH_MODE": "remote",
        "AX_AUTH_SERVER_URL": "http://localhost:3000",
        "MCP_SERVER_URL": "http://localhost:3000",
        "BACKEND_JWKS_URI": "http://localhost:3000/.well-known/jwks.json",
        "BACKEND_ISSUER": "http://localhost:3000",
    })
    def test_remote_auth_requires_backend_issuer(self):
        """Remote mode must know which issuer to trust for backend access tokens."""
        server_mod = _import_server_module()

        with patch.object(server_mod, "BACKEND_ISSUER", ""):
            with self.assertRaisesRegex(RuntimeError, "BACKEND_ISSUER is required"):
                server_mod.create_auth()
    def test_unknown_auth_mode_fails_closed(self):
        with patch.dict(os.environ, {"AX_AUTH_MODE": "typo"}):
            # create_server() runs at module load and refuses unauthenticated startup.
            with self.assertRaisesRegex(RuntimeError, "requires AX_AUTH_MODE=remote"):
                _import_server_module()

    def test_glama_ownership_has_no_personal_default(self):
        with patch.dict(os.environ, {"AX_AUTH_MODE": "remote"}):
            os.environ.pop("GLAMA_MAINTAINER_EMAILS", None)
            server = _import_server_module()
            with TestClient(server.create_app()) as client:
                response = client.get("/.well-known/glama.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["maintainers"], [])
