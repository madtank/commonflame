"""Tests for agent identity resolution from token claims — AGENT-TOKEN-001.

Validates that agent_name is resolved from validated token claims first,
then URL path, then X-Agent-Name header. Agent claims are highest trust.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp_server.api_client import extract_agent_context


class AgentIdentityFromClaimsTests(unittest.TestCase):
    """Agent identity resolved from token claims first."""

    def test_agent_name_from_claims_takes_precedence(self):
        """Token claims agent_name wins over X-Agent-Name header."""
        token = SimpleNamespace(
            token="agent-jwt",
            claims={"agent_name": "Commonflame", "agent_id": "5e33fedf", "space_id": "space-1"},
        )
        request = SimpleNamespace(headers={"x-agent-name": "wrong_agent"})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["agent_name"], "Commonflame")

    def test_agent_id_extracted_from_claims(self):
        """agent_id from token claims must be available in context."""
        token = SimpleNamespace(
            token="agent-jwt",
            claims={
                "agent_id": "5e33fedf-659f-4762-acb0-4166c2ac4c12",
                "agent_name": "Commonflame",
                "space_id": "space-1",
            },
        )
        request = SimpleNamespace(headers={})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["agent_id"], "5e33fedf-659f-4762-acb0-4166c2ac4c12")

    def test_agent_space_id_from_claims_wins_over_header(self):
        """Agent claim space_id wins over X-Space-Id header."""
        token = SimpleNamespace(
            token="agent-jwt",
            claims={"agent_name": "Commonflame", "space_id": "claims-space", "agent_id": "agent-test"},
        )
        request = SimpleNamespace(headers={"x-space-id": "header-space"})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["space_id"], "claims-space")

    def test_space_id_mismatch_logged_as_warning(self):
        """When agent claims and header space_id differ, log a warning."""
        token = SimpleNamespace(
            token="agent-jwt",
            claims={"agent_name": "Commonflame", "space_id": "claims-space", "agent_id": "agent-test"},
        )
        request = SimpleNamespace(headers={"x-space-id": "different-space"})

        with self.assertLogs("fastmcp_server.api_client", level="WARNING") as logs:
            ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["space_id"], "claims-space")
        self.assertTrue(
            any("mismatch" in msg.lower() for msg in logs.output),
            f"Expected space_id mismatch warning, got: {logs.output}",
        )

    def test_user_space_header_wins_over_stale_claim(self):
        """User/browser sessions use the explicit UI-selected space."""
        token = SimpleNamespace(
            token="cognito-jwt",
            claims={
                "sub": "user-123",
                "username": "GitHub_48119320",
                "scope": "openid profile",
                "client_id": "frontend-client",
                "space_id": "stale-claims-space",
                "typ": "local-user",
            },
        )
        request = SimpleNamespace(
            headers={
                "x-agent-name": "Commonflame",
                "x-space-id": "current-ui-space",
            }
        )

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertEqual(ctx["space_id"], "current-ui-space")

    def test_user_token_header_does_not_create_agent_principal(self):
        """X-Agent-Name is only routing context for user/browser tokens."""
        token = SimpleNamespace(
            token="cognito-jwt",
            claims={
                "sub": "user-123",
                "username": "GitHub_48119320",
                "scope": "openid profile",
                "client_id": "frontend-client",
                "typ": "local-user",
            },
        )
        request = SimpleNamespace(headers={"x-agent-name": "protocol_sage_738"})

        ctx = extract_agent_context(token, request)

        self.assertEqual(ctx["principal_type"], "user")
        self.assertIsNone(ctx["agent_name"])
        self.assertEqual(ctx["route_agent_name"], "protocol_sage_738")

    def test_cognito_m2m_header_cannot_promote_user_principal(self):
        """Route labels and OAuth metadata cannot create an unsigned agent identity."""
        token = SimpleNamespace(token='cognito-m2m-jwt', claims={'sub': 'm2m-client-subject', 'token_use': 'access', 'client_id': 'mcp-m2m-client', 'scope': 'ax-api/mcp:read'})
        request = SimpleNamespace(headers={'x-agent-name': 'protocol_sage_738'})
        ctx = extract_agent_context(token, request)
        self.assertEqual(ctx['principal_type'], 'user')
        self.assertIsNone(ctx['agent_name'])
    def test_pat_exchanged_mcp_token_header_cannot_promote_user_principal(self):
        """Route labels and OAuth metadata cannot create an unsigned agent identity."""
        token = SimpleNamespace(token='pat-exchanged-mcp-jwt', claims={'sub': 'user-123', 'user_id': 'user-123', 'token_class': 'user_access', 'audience': 'ax-mcp', 'scope': 'messages tasks context agents spaces search'})
        request = SimpleNamespace(headers={'x-agent-name': 'agentx2'})
        ctx = extract_agent_context(token, request)
        self.assertEqual(ctx['principal_type'], 'user')
        self.assertIsNone(ctx['agent_name'])
        self.assertEqual(ctx['user_id'], 'user-123')

    def test_no_agent_id_for_user_tokens(self):
        """User tokens don't have agent_id — should be None."""
        token = SimpleNamespace(
            token="cognito-jwt",
            claims={"sub": "user-123"},
        )
        request = SimpleNamespace(headers={"x-agent-name": "protocol_sage_738"})

        ctx = extract_agent_context(token, request)

        self.assertIsNone(ctx.get("agent_id"))


if __name__ == "__main__":
    unittest.main()
