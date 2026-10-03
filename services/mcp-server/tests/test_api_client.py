"""Tests for api_client.py — agent context extraction and token forwarding.

Validates the auth contract from AX-AGENT-MGMT-001 §22.1:
- Backend-issued agent JWTs are forwarded as-is
- Agent identity comes from token claims first, header fallback second
- User JWTs stay user principals even when routed through a named MCP agent URL
- User principals use explicit request space over stale token space claims
- Missing agent identity warning only applies to agent principals
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp_server.api_client import (
    _configured_mcp_audiences,
    _normalize_mcp_resource,
    extract_agent_context,
    api_request,
    wait_for_reply,
)


class DummyProgress:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def set_message(self, message: str) -> None:
        self.messages.append(message)


class ExtractAgentContextTests(unittest.TestCase):
    """Test extract_agent_context() resolves identity correctly."""

    def setUp(self):
        # Several tests patch MCP audience env vars; keep the startup cache isolated.
        _configured_mcp_audiences.cache_clear()

    def tearDown(self):
        # Future env-patching tests in this class rely on cache isolation too.
        _configured_mcp_audiences.cache_clear()

    def test_backend_jwt_returns_agent_jwt_as_auth_token(self):
        """Backend-issued agent JWT must be forwarded — never substituted."""
        agent_jwt = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImF4LWJhY2tlbmQtMSJ9.agent-payload"
        token = SimpleNamespace(
            token=agent_jwt,
            claims={
                "agent_name": "Waystation",
                "agent_id": "5e33fedf-659f-4762-acb0-4166c2ac4c12",
                "space_id": "a632f74e-6c61-4222-821f-06ad3b02de34",
                "iss": "ax-backend",
            },
        )
        request = SimpleNamespace(headers={})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["jwt"], agent_jwt)
        self.assertEqual(ctx["agent_name"], "Waystation")
        self.assertEqual(ctx["space_id"], "a632f74e-6c61-4222-821f-06ad3b02de34")

    def test_local_browser_token_remains_user_on_named_agent_route(self):
        token = SimpleNamespace(
            token="local-browser-token",
            claims={
                "typ": "local-user", "token_class": "user_access",
                "sub": "user-1", "user_id": "user-1",
                "aud": ["ax-api", "http://localhost:3000/mcp"],
                "space_id": "home-space",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "worker", "x-space-id": "selected-space"})
        with patch("fastmcp_server.api_client._FRONTEND_CLIENT_IDS", frozenset()):
            ctx = extract_agent_context(token, request)
        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])
        self.assertEqual(ctx["user_id"], "user-1")
        self.assertEqual(ctx["space_id"], "selected-space")

    def test_normalize_mcp_resource_handles_empty_root_and_nested_paths(self):
        """Audience normalization should always collapse to the MCP resource."""
        self.assertEqual(_normalize_mcp_resource(""), "")
        self.assertEqual(
            _normalize_mcp_resource("https://example.com/api/mcp"),
            "https://example.com/api/mcp",
        )
        self.assertEqual(
            _normalize_mcp_resource("https://example.com/mcp/agents/orion/"),
            "https://example.com/mcp",
        )
        self.assertEqual(
            _normalize_mcp_resource("https://example.com/mcp/extra/path"),
            "https://example.com/mcp",
        )

    def test_user_jwt_with_route_header_stays_user_session(self):
        """User quick-launch tokens must not inherit the route's X-Agent-Name."""
        cognito_jwt = "eyJhbGciOiJSUzI1NiJ9.cognito-user-payload"
        token = SimpleNamespace(
            token=cognito_jwt,
            claims={
                "sub": "user-12345",
                "username": "GitHub_48119320",
                "scope": "openid profile",
                "client_id": "frontend-client",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "protocol_sage_738"})

        with patch(
            "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
            frozenset({"frontend-client"}),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["jwt"], cognito_jwt)
        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])
        self.assertEqual(ctx["route_agent_name"], "protocol_sage_738")
        self.assertEqual(ctx["user_id"], "user-12345")

    def test_user_jwt_with_route_header_and_no_frontend_config_stays_user_session(self):
        """Missing frontend client config must fail closed, not promote users."""
        token = SimpleNamespace(
            token="cognito-user-jwt",
            claims={
                "sub": "user-12345",
                "username": "GitHub_48119320",
                "scope": "openid profile",
                "client_id": "frontend-client",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "protocol_sage_738"})

        with (
            patch("fastmcp_server.api_client._FRONTEND_CLIENT_IDS", frozenset()),
            patch(
                "fastmcp_server.api_client._MCP_CLIENT_IDS",
                frozenset({"mcp-interactive-client", "mcp-m2m-client"}),
            ),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])
        self.assertEqual(ctx["route_agent_name"], "protocol_sage_738")
        self.assertEqual(ctx["user_id"], "user-12345")

    def test_mcp_oauth_proxy_token_with_route_header_resolves_agent_session(self):
        """MCP Inspector/OAuth sessions use /mcp/agents/{name} as the binding."""
        token = SimpleNamespace(
            token="fastmcp-proxy-jwt",
            claims={
                "sub": "oauth-user-subject",
                "client_id": "mcp-interactive-client",
                "scope": "openid",
                "username": "GitHub_48119320",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "agentx2"})

        with (
            patch(
                "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
                frozenset({"frontend-client"}),
            ),
            patch(
                "fastmcp_server.api_client._MCP_CLIENT_IDS",
                frozenset({"mcp-interactive-client"}),
            ),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "agent")
        self.assertEqual(ctx["agent_name"], "agentx2")
        self.assertEqual(ctx["route_agent_name"], "agentx2")
        self.assertEqual(ctx["user_id"], "oauth-user-subject")

    def test_pat_exchanged_mcp_token_with_route_header_resolves_agent_session(self):
        """PAT-exchanged headless MCP tokens use the route as the agent binding."""
        token = SimpleNamespace(
            token="pat-exchanged-mcp-jwt",
            claims={
                "sub": "user-12345",
                "user_id": "user-12345",
                "username": "GitHub_48119320",
                "token_class": "user_access",
                "audience": "ax-mcp",
                "scope": "messages tasks context agents spaces search",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "agentx2"})

        with (
            patch(
                "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
                frozenset({"frontend-client"}),
            ),
            patch("fastmcp_server.api_client._MCP_CLIENT_IDS", frozenset()),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "agent")
        self.assertEqual(ctx["agent_name"], "agentx2")
        self.assertEqual(ctx["route_agent_name"], "agentx2")
        self.assertEqual(ctx["user_id"], "user-12345")

    def test_ax_as_resource_audience_token_with_route_header_resolves_agent_session(self):
        """Waystation AS remote OAuth tokens use the protected resource URL as audience."""
        token = SimpleNamespace(
            token="ax-as-device-jwt",
            claims={
                "sub": "user-12345",
                "owner_user_id": "user-12345",
                "username": "GitHub_48119320",
                "token_class": "user_access",
                "aud": "http://localhost:3000/mcp",
                "scope": "messages.read messages.write",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "lantern"})

        with (
            patch(
                "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
                frozenset({"frontend-client"}),
            ),
            patch("fastmcp_server.api_client._MCP_CLIENT_IDS", frozenset()),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "agent")
        self.assertEqual(ctx["agent_name"], "lantern")
        self.assertEqual(ctx["route_agent_name"], "lantern")
        self.assertEqual(ctx["user_id"], "user-12345")

    def test_full_agent_url_config_still_matches_mcp_resource_audience(self):
        """Pasted /mcp/agents/{name} config should normalize to the MCP resource."""
        token = SimpleNamespace(
            token="ax-as-device-jwt",
            claims={
                "sub": "user-12345",
                "owner_user_id": "user-12345",
                "username": "GitHub_48119320",
                "token_class": "user_access",
                "aud": "http://localhost:3000/mcp",
                "scope": "messages.read messages.write",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "lantern"})

        with (
            patch(
                "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
                frozenset({"frontend-client"}),
            ),
            patch("fastmcp_server.api_client._MCP_CLIENT_IDS", frozenset()),
            patch.dict(
                "os.environ",
                {"MCP_SERVER_URL": "http://localhost:3000/mcp/agents/lantern"},
            ),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "agent")
        self.assertEqual(ctx["agent_name"], "lantern")
        self.assertEqual(ctx["route_agent_name"], "lantern")

    def test_ax_as_token_for_other_mcp_resource_stays_user_session(self):
        """Remote OAuth audience matching must not accept another MCP resource."""
        token = SimpleNamespace(
            token="other-mcp-resource-jwt",
            claims={
                "sub": "user-12345",
                "owner_user_id": "user-12345",
                "username": "GitHub_48119320",
                "token_class": "user_access",
                "aud": "https://other-service.example/mcp",
                "scope": "messages.read messages.write",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "lantern"})

        with (
            patch(
                "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
                frozenset({"frontend-client"}),
            ),
            patch("fastmcp_server.api_client._MCP_CLIENT_IDS", frozenset()),
            patch.dict("os.environ", {"MCP_SERVER_URL": "http://localhost:3000"}),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])
        self.assertEqual(ctx["route_agent_name"], "lantern")
        self.assertEqual(ctx["user_id"], "user-12345")

    def test_pat_exchanged_non_mcp_token_with_route_header_stays_user_session(self):
        """PAT exchanges for non-MCP audiences cannot route-bind as agents."""
        token = SimpleNamespace(
            token="pat-exchanged-api-jwt",
            claims={
                "sub": "user-12345",
                "user_id": "user-12345",
                "username": "GitHub_48119320",
                "token_class": "user_access",
                "audience": "ax-api",
                "scope": "messages tasks context agents spaces search",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "agentx2"})

        with patch("fastmcp_server.api_client._MCP_CLIENT_IDS", frozenset()):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])
        self.assertEqual(ctx["route_agent_name"], "agentx2")
        self.assertEqual(ctx["user_id"], "user-12345")

    def test_pat_exchanged_admin_token_with_route_header_stays_user_session(self):
        """Admin PAT exchange tokens are bootstrap credentials, not runtime agents."""
        token = SimpleNamespace(
            token="pat-exchanged-admin-jwt",
            claims={
                "sub": "admin-user-123",
                "user_id": "admin-user-123",
                "username": "GitHub_admin",
                "token_class": "user_admin",
                "audience": "ax-mcp",
                "scope": "messages tasks context agents spaces search",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "agentx2"})

        with (
            patch(
                "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
                frozenset({"frontend-client"}),
            ),
            patch("fastmcp_server.api_client._MCP_CLIENT_IDS", frozenset()),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])
        self.assertEqual(ctx["route_agent_name"], "agentx2")
        self.assertEqual(ctx["user_id"], "admin-user-123")

    def test_legacy_mcp_scoped_token_with_route_header_resolves_agent_session(self):
        """Legacy MCP OAuth tokens can route-bind when only the resource scope is present."""
        token = SimpleNamespace(
            token="legacy-mcp-oauth-jwt",
            claims={
                "sub": "legacy-mcp-subject",
                "client_id": "legacy-mcp-client",
                "scope": "openid ax-api/mcp:read",
                "username": "GitHub_48119320",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "agentx2"})

        with (
            patch(
                "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
                frozenset({"frontend-client"}),
            ),
            patch(
                "fastmcp_server.api_client._MCP_CLIENT_IDS",
                frozenset({"known-mcp-client"}),
            ),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "agent")
        self.assertEqual(ctx["agent_name"], "agentx2")
        self.assertEqual(ctx["route_agent_name"], "agentx2")
        self.assertEqual(ctx["user_id"], "legacy-mcp-subject")


class WaitForReplyProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_reply_emits_uniform_still_waiting_status(self) -> None:
        clock = {"now": 0.0}
        progress = DummyProgress()

        async def fake_sleep(seconds: float) -> None:
            clock["now"] += seconds

        async def fake_api_request(*args, **kwargs):
            return {"messages": []}

        with (
            patch("fastmcp_server.api_client.asyncio.sleep", new=fake_sleep),
            patch("fastmcp_server.api_client.time.monotonic", new=lambda: clock["now"]),
            patch("fastmcp_server.api_client.api_request", new=fake_api_request),
        ):
            result = await wait_for_reply(
                {
                    "jwt": "jwt",
                    "agent_name": "sender",
                    "space_id": "space-1",
                },
                {
                    "id": "msg-1",
                    "conversation_id": "msg-1",
                    "created_at": "2026-05-22T08:00:00+00:00",
                },
                "msg-1",
                max_wait=16,
                poll_interval=2,
                progress=progress,
            )

        self.assertEqual(result["status"], "timeout")
        self.assertIn(
            "Still waiting for reply... 10s elapsed (max 16s)",
            progress.messages,
        )

    def test_cognito_m2m_token_with_route_header_resolves_agent_session(self):
        """MCP M2M tokens use the route/header as the reviewed agent binding."""
        cognito_m2m_jwt = "eyJhbGciOiJSUzI1NiJ9.cognito-m2m-payload"
        token = SimpleNamespace(
            token=cognito_m2m_jwt,
            claims={
                "sub": "m2m-client-subject",
                "token_use": "access",
                "client_id": "mcp-m2m-client",
                "scope": "ax-api/mcp:read",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "protocol_sage_738"})

        with patch(
            "fastmcp_server.api_client._MCP_CLIENT_IDS",
            frozenset({"mcp-m2m-client"}),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["jwt"], cognito_m2m_jwt)
        self.assertEqual(ctx["principal_type"], "agent")
        self.assertEqual(ctx["agent_name"], "protocol_sage_738")
        self.assertEqual(ctx["route_agent_name"], "protocol_sage_738")
        self.assertEqual(ctx["user_id"], "m2m-client-subject")

    def test_cognito_m2m_token_without_route_header_does_not_guess_agent(self):
        """MCP M2M needs an explicit route/header binding to pick an agent."""
        token = SimpleNamespace(
            token="cognito-m2m-jwt",
            claims={
                "sub": "m2m-client-subject",
                "token_use": "access",
                "client_id": "mcp-m2m-client",
                "scope": "ax-api/mcp:read",
            },
        )
        request = SimpleNamespace(headers={})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])

    def test_user_jwt_without_header_is_clean_user_session(self):
        """A user JWT without agent claims is the normal quick-launch path."""
        token = SimpleNamespace(
            token="cognito-jwt",
            claims={"sub": "user-12345"},
        )
        request = SimpleNamespace(headers={})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])

    def test_user_jwt_space_header_overrides_stale_space_claim(self):
        """Viewer-token widget calls use the current UI space, not stale claims."""
        token = SimpleNamespace(
            token="cognito-jwt",
            claims={
                "sub": "user-12345",
                "username": "GitHub_48119320",
                "scope": "openid profile",
                "client_id": "frontend-client",
                "space_id": "stale-session-space",
            },
        )
        request = SimpleNamespace(
            headers={
                "x-agent-name": "Waystation",
                "x-space-id": "current-panel-space",
            }
        )

        with patch(
            "fastmcp_server.api_client._FRONTEND_CLIENT_IDS",
            frozenset({"frontend-client"}),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])
        self.assertEqual(ctx["space_id"], "current-panel-space")

    def test_token_claims_take_precedence_over_header(self):
        """Agent identity in token claims beats X-Agent-Name header."""
        token = SimpleNamespace(
            token="agent-jwt",
            claims={"agent_name": "Waystation", "space_id": "space-from-claims"},
        )
        request = SimpleNamespace(
            headers={"x-agent-name": "wrong_agent", "x-space-id": "space-from-header"}
        )

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["agent_name"], "Waystation")
        self.assertEqual(ctx["principal_type"], "agent")
        self.assertEqual(ctx["space_id"], "space-from-claims")

    def test_home_space_delegation_claims_are_extracted(self):
        """Delegation claims are surfaced in agent context unchanged."""
        token = SimpleNamespace(
            token="agent-jwt",
            claims={
                "agent_name": "Waystation",
                "agent_id": "5e33fedf-659f-4762-acb0-4166c2ac4c12",
                "space_id": "space-1",
                "delegation_mode": "home_space",
                "delegated_for": ["space-2", "space-3"],
            },
        )
        request = SimpleNamespace(headers={})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["delegation_mode"], "home_space")
        self.assertEqual(ctx["delegated_for"], ["space-2", "space-3"])

    def test_trusted_agent_id_on_behalf_header_is_extracted_for_concierge_delegation(self):
        """Space-agent MCP calls can carry the human requester for HITL drafts."""
        token = SimpleNamespace(
            token="agent-jwt",
            claims={
                "agent_name": "Waystation",
                "agent_id": "5e33fedf-659f-4762-acb0-4166c2ac4c12",
                "space_id": "team-space",
            },
        )
        request = SimpleNamespace(headers={"x-on-behalf-of": "user-123"})

        with patch(
            "fastmcp_server.api_client._TRUSTED_CONCIERGE_AGENT_IDS",
            frozenset({"5e33fedf-659f-4762-acb0-4166c2ac4c12"}),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "agent")
        self.assertEqual(ctx["delegation_mode"], "concierge_delegated")
        self.assertEqual(ctx["delegated_for"], "user-123")

    def test_untrusted_agent_on_behalf_header_is_ignored(self):
        """Arbitrary agents cannot turn a mutable header into delegated identity."""
        token = SimpleNamespace(
            token="agent-jwt",
            claims={
                "agent_name": "worker_bot",
                "agent_id": "untrusted-agent-id",
                "space_id": "team-space",
            },
        )
        request = SimpleNamespace(headers={"x-on-behalf-of": "user-123"})

        with patch(
            "fastmcp_server.api_client._TRUSTED_CONCIERGE_AGENT_IDS",
            frozenset({"trusted-concierge-id"}),
        ):
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "agent")
        self.assertIsNone(ctx["delegation_mode"])
        self.assertIsNone(ctx["delegated_for"])

    def test_space_id_from_header_when_not_in_claims(self):
        """Space ID falls back to X-Space-Id header when not in claims."""
        token = SimpleNamespace(
            token="jwt",
            claims={"agent_name": "test_agent"},
        )
        request = SimpleNamespace(headers={"x-space-id": "header-space-id"})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["space_id"], "header-space-id")


class ApiRequestTokenForwardingTests(unittest.IsolatedAsyncioTestCase):
    """Test that api_request() forwards the correct token as Bearer."""

    async def test_forwards_agent_jwt_as_bearer(self):
        """The JWT passed to api_request must appear as Authorization: Bearer."""
        agent_jwt = "eyJ-agent-rs256-jwt"
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request("GET", "/api/v1/agents/me", agent_jwt, agent_name="Waystation")

        self.assertEqual(
            captured_headers.get("Authorization"),
            f"Bearer {agent_jwt}",
        )
        self.assertEqual(captured_headers.get("X-Agent-Name"), "Waystation")

    async def test_does_not_leak_cognito_token_for_agent_calls(self):
        """When agent_name is set, the forwarded token must NOT be a different token.

        This is the exact bug Stack found — the MCP server was forwarding a
        Cognito passthrough token instead of the agent's own JWT.
        """
        agent_jwt = "eyJ-agent-rs256-jwt"
        cognito_jwt = "eyJ-cognito-user-jwt"
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request("GET", "/api/v1/messages", agent_jwt, agent_name="Waystation")

        auth_header = captured_headers.get("Authorization", "")
        self.assertIn(agent_jwt, auth_header)
        self.assertNotIn(cognito_jwt, auth_header)

    async def test_home_space_delegation_forwarded_as_headers(self):
        """Delegation headers are forwarded for home_space mode."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "GET",
                "/api/v1/spaces",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for=["space-2", "space-3"],
            )

        self.assertEqual(captured_headers.get("X-Delegation-Mode"), "home_space")
        self.assertEqual(captured_headers.get("X-Delegated-For"), '["space-2", "space-3"]')
        self.assertNotIn("X-On-Behalf-Of", captured_headers)

    async def test_home_space_draft_delegation_forwards_on_behalf_of(self):
        """Draft endpoints need X-On-Behalf-Of for concierge HITL creation."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "POST",
                "/api/v1/drafts/agents",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for="user-123",
                json_data={"agent": {"name": "draft_bot"}},
            )

        self.assertEqual(captured_headers.get("X-Delegation-Mode"), "home_space")
        self.assertEqual(captured_headers.get("X-Delegated-For"), "user-123")
        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_concierge_delegation_forwards_on_behalf_of(self):
        """Team-space Waystation drafts also need X-On-Behalf-Of, not full manage rights."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "POST",
                "/api/v1/drafts/agents",
                "jwt",
                agent_name="Waystation",
                space_id="team-space",
                delegation_mode="concierge_delegated",
                delegated_for="user-123",
                json_data={"agent": {"name": "draft_bot"}},
            )

        self.assertEqual(captured_headers.get("X-Delegation-Mode"), "concierge_delegated")
        self.assertEqual(captured_headers.get("X-Delegated-For"), "user-123")
        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_home_space_draft_delegation_extracts_user_id_from_dict(self):
        """Some delegated tokens carry a structured delegated_for claim."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "POST",
                "/api/v1/drafts/spaces",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for={"user_id": "user-456", "space_id": "space-1"},
                json_data={"space": {"name": "draft_space"}},
            )

        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-456")

    async def test_home_space_agent_management_forwards_on_behalf_of(self):
        """Private-space Waystation management writes must carry the human owner."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "PATCH",
                "/api/v1/agents/agent-123",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for="user-123",
                json_data={"description": "Updated by Waystation for review"},
            )

        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_home_space_agent_control_forwards_on_behalf_of(self):
        """Disable/re-enable routes need the same delegated owner context."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "PATCH",
                "/auth/agents/agent-123/control",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for="user-123",
                json_data={"disabled": True, "reason": "User requested pause"},
            )

        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_home_space_task_writes_forward_on_behalf_of(self):
        """Delegated task writes need the human owner for RLS/background workers."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "POST",
                "/api/v1/tasks",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for={"user_id": "user-123", "space_id": "space-home"},
                json_data={"title": "Fix widget warning"},
            )

        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_home_space_task_field_update_forwards_on_behalf_of(self):
        """PUT /api/v1/tasks/{id} (field updates) must forward delegated identity."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "PUT",
                "/api/v1/tasks/task-abc",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for={"user_id": "user-123", "space_id": "space-home"},
                json_data={"title": "Updated"},
            )

        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_home_space_task_nudge_forwards_on_behalf_of(self):
        """POST /api/v1/tasks/{id}/nudge must forward delegated identity (Codex P2 on #293)."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "POST",
                "/api/v1/tasks/task-abc/nudge",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for={"user_id": "user-123", "space_id": "space-home"},
            )

        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_home_space_task_reminder_pause_forwards_on_behalf_of(self):
        """PUT /api/v1/tasks/reminders/pause must preserve delegated user audit context."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "PUT",
                "/api/v1/tasks/reminders/pause",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for={"user_id": "user-123", "space_id": "space-home"},
                json_data={"global": {"paused": True, "reason": "User requested mute"}},
            )

        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_home_space_task_status_write_forwards_on_behalf_of(self):
        """PUT /api/v1/tasks/{id}/status (lifecycle mutations) must forward delegated identity."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "PUT",
                "/api/v1/tasks/task-abc/status",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for={"user_id": "user-123", "space_id": "space-home"},
                json_data={"status": "completed"},
            )

        self.assertEqual(captured_headers.get("X-On-Behalf-Of"), "user-123")

    async def test_legacy_task_path_does_not_forward_on_behalf_of(self):
        """Legacy /api/tasks paths were removed from the allowlist after the v1 migration."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "POST",
                "/api/tasks",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for={"user_id": "user-123", "space_id": "space-home"},
                json_data={"title": "via legacy path"},
            )

        self.assertNotIn("X-On-Behalf-Of", captured_headers)

    async def test_home_space_agent_put_does_not_forward_on_behalf_of(self):
        """Delegation forwarding is opt-in by reviewed method/path pairs."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "PUT",
                "/api/v1/agents/agent-123",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for="user-123",
                json_data={"description": "not reviewed"},
            )

        self.assertNotIn("X-On-Behalf-Of", captured_headers)

    async def test_home_space_delete_does_not_forward_on_behalf_of_by_default(self):
        """Future destructive routes must be explicitly reviewed before delegation."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "DELETE",
                "/api/v1/agents/agent-123",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for="user-123",
            )

        self.assertNotIn("X-On-Behalf-Of", captured_headers)

    async def test_home_space_read_requests_do_not_forward_on_behalf_of(self):
        """Read-only calls keep delegation metadata but not user impersonation headers."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "GET",
                "/api/v1/agents",
                "jwt",
                agent_name="Waystation",
                space_id="space-1",
                delegation_mode="home_space",
                delegated_for="user-123",
            )

        self.assertNotIn("X-On-Behalf-Of", captured_headers)

    async def test_space_id_forwarded_as_header(self):
        """Space ID must be forwarded as X-Space-Id header."""
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "POST", "/api/v1/messages", "jwt",
                agent_name="Waystation",
                space_id="a632f74e-6c61-4222-821f-06ad3b02de34",
            )

        self.assertEqual(
            captured_headers.get("X-Space-Id"),
            "a632f74e-6c61-4222-821f-06ad3b02de34",
        )


class EndToEndTokenFlowTests(unittest.IsolatedAsyncioTestCase):
    """Integration test: extract_agent_context → api_request token chain.

    Validates that the JWT from extract_agent_context is the same JWT
    that api_request sends to the backend — no token substitution.
    """

    async def test_agent_jwt_flows_from_context_to_backend_call(self):
        """Full chain: token auth → extract context → api_request → backend."""
        agent_jwt = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImF4LWJhY2tlbmQtMSJ9.agent"
        token = SimpleNamespace(
            token=agent_jwt,
            claims={
                "agent_name": "Waystation",
                "agent_id": "5e33fedf-659f-4762-acb0-4166c2ac4c12",
                "space_id": "a632f74e-6c61-4222-821f-06ad3b02de34",
                "iss": "ax-backend",
            },
        )
        request = SimpleNamespace(headers={})

        ctx = extract_agent_context(token, request)
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "POST", "/api/v1/messages",
                ctx["jwt"],
                agent_name=ctx["agent_name"],
                space_id=ctx["space_id"],
            )

        self.assertEqual(
            captured_headers["Authorization"],
            f"Bearer {agent_jwt}",
            "Agent JWT must be forwarded to backend — not Cognito passthrough",
        )
        self.assertEqual(captured_headers["X-Agent-Name"], "Waystation")
        self.assertEqual(
            captured_headers["X-Space-Id"],
            "a632f74e-6c61-4222-821f-06ad3b02de34",
        )

    async def test_delegation_claims_flow_from_context_to_backend_call(self):
        """Delegation claims extracted from JWT are forwarded to the backend."""
        token = SimpleNamespace(
            token="delegated-jwt",
            claims={
                "agent_name": "Waystation",
                "agent_id": "5e33fedf-659f-4762-acb0-4166c2ac4c12",
                "space_id": "space-home",
                "delegation_mode": "home_space",
                "delegated_for": {"user_id": "user-123", "space_id": "space-home"},
            },
        )
        request = SimpleNamespace(headers={})

        ctx = extract_agent_context(token, request)
        captured_headers = {}

        async def capture_request(self, method, url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            response = SimpleNamespace(
                status_code=200,
                content=b'{"ok": true}',
                json=lambda: {"ok": True},
            )
            return response

        with patch("httpx.AsyncClient.request", new=capture_request):
            await api_request(
                "GET",
                "/api/v1/messages",
                ctx["jwt"],
                agent_name=ctx["agent_name"],
                agent_id=ctx["agent_id"],
                space_id=ctx["space_id"],
                delegation_mode=ctx["delegation_mode"],
                delegated_for=ctx["delegated_for"],
            )

        self.assertEqual(captured_headers.get("X-Delegation-Mode"), "home_space")
        self.assertEqual(
            captured_headers.get("X-Delegated-For"),
            '{"user_id": "user-123", "space_id": "space-home"}',
        )


if __name__ == "__main__":
    unittest.main()
