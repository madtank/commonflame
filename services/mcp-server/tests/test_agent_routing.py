"""Regression tests for /mcp/agents/{name} path rewriting."""

import unittest
from urllib.parse import parse_qs

from starlette.testclient import TestClient

from fastmcp_server.server import create_app, normalize_authorize_resource_query, rewrite_agent_route


class AgentRouteRewriteTests(unittest.TestCase):
    """Agent-path aliases must preserve both MCP and OAuth routes."""

    def test_agent_root_rewrites_to_mcp(self):
        agent_name, path = rewrite_agent_route("/mcp/agents/agentx")
        self.assertEqual(agent_name, "agentx")
        self.assertEqual(path, "/mcp")

    def test_mcp_subpath_stays_under_mcp(self):
        agent_name, path = rewrite_agent_route("/mcp/agents/agentx/messages")
        self.assertEqual(agent_name, "agentx")
        self.assertEqual(path, "/mcp/messages")

    def test_authorization_server_alias_rewrites_to_root(self):
        agent_name, path = rewrite_agent_route(
            "/mcp/agents/agentx/.well-known/oauth-authorization-server"
        )
        self.assertEqual(agent_name, "agentx")
        self.assertEqual(path, "/.well-known/oauth-authorization-server")

    def test_protected_resource_alias_rewrites_to_root(self):
        agent_name, path = rewrite_agent_route(
            "/mcp/agents/agentx/.well-known/oauth-protected-resource/mcp"
        )
        self.assertEqual(agent_name, "agentx")
        self.assertEqual(path, "/.well-known/oauth-protected-resource/mcp")

    def test_token_endpoint_alias_rewrites_to_root(self):
        agent_name, path = rewrite_agent_route("/mcp/agents/agentx/token")
        self.assertEqual(agent_name, "agentx")
        self.assertEqual(path, "/token")

    def test_non_agent_paths_pass_through_unchanged(self):
        agent_name, path = rewrite_agent_route("/health")
        self.assertIsNone(agent_name)
        self.assertEqual(path, "/health")

    def test_authorize_resource_query_normalizes_agent_alias(self):
        query = (
            b"response_type=code&"
            b"resource=https%3A%2F%2Fpaxai.app%2Fmcp%2Fagents%2Fmcpjam&"
            b"state=abc"
        )

        normalized = normalize_authorize_resource_query(query, "https://paxai.app")

        params = parse_qs(normalized.decode("utf-8"))
        self.assertEqual(params["resource"], ["https://paxai.app/mcp"])
        self.assertEqual(params["state"], ["abc"])

    def test_authorize_resource_query_leaves_canonical_resource(self):
        query = b"resource=https%3A%2F%2Fpaxai.app%2Fmcp&state=abc"

        normalized = normalize_authorize_resource_query(query, "https://paxai.app")

        self.assertEqual(normalized, query)

    def test_scoped_health_path_is_cheap_under_mcp_namespace(self):
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.get("/mcp/health")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ok")
        self.assertNotIn("www-authenticate", response.headers)

    def test_agent_scoped_health_alias_reroutes_to_mcp_health(self):
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.get("/mcp/agents/glama/health")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ok")
        self.assertNotIn("www-authenticate", response.headers)


if __name__ == "__main__":
    unittest.main()
