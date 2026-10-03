"""Tests for ToolCallNotificationMiddleware identity guard.

Verifies that tool-call notifications are skipped when no agent identity
is present, preventing guaranteed 403s from the backend's /api/v1/tool-calls
endpoint which requires authenticated agent context.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp_server.tool_call_notifier import (
    ToolCallNotificationMiddleware,
    _extract_initial_data,
    _extract_result_kind,
    _extract_task_activity_context,
    _resolve_resource_uri,
)


class _BrokenStructuredContentResult:
    @property
    def structured_content(self):
        raise RuntimeError("broken structured content")


class ToolResultExtractionTests(unittest.TestCase):
    """Malformed tool results are ignored but no longer silently swallowed."""

    def test_extract_result_kind_logs_and_returns_none(self):
        with self.assertLogs("fastmcp_server.tool_call_notifier", level="DEBUG") as logs:
            self.assertIsNone(_extract_result_kind(_BrokenStructuredContentResult()))
        self.assertIn("Unable to extract tool result kind", "\n".join(logs.output))

    def test_extract_initial_data_logs_and_returns_none(self):
        with self.assertLogs("fastmcp_server.tool_call_notifier", level="DEBUG") as logs:
            self.assertIsNone(_extract_initial_data(_BrokenStructuredContentResult()))
        self.assertIn("Unable to extract initial tool result data", "\n".join(logs.output))


class TaskActivityContextTests(unittest.TestCase):
    """Task result notifications carry target context for activity cards."""

    def test_extracts_task_and_assignee_context(self):
        context = _extract_task_activity_context({
            "kind": "task_detail",
            "data": {
                "task": {
                    "id": "task-1",
                    "title": "lifecycle proof disposable",
                    "assignee": {
                        "id": "agent-1",
                        "handle": "widget_hermes_local",
                        "display_name": "Widget Hermes",
                    },
                }
            },
        })
        self.assertEqual(context["task_id"], "task-1")
        self.assertEqual(context["task_title"], "lifecycle proof disposable")
        self.assertEqual(context["target_agent_id"], "agent-1")
        self.assertEqual(context["assigned_to"], "agent-1")
        self.assertEqual(context["target_agent_handle"], "widget_hermes_local")
        self.assertEqual(context["target_label"], "Widget Hermes")

    def test_ignores_missing_task_payload(self):
        self.assertEqual(_extract_task_activity_context({"kind": "task_collection"}), {})


class ResourceUriResolutionTests(unittest.TestCase):
    """Tool-call notifications route folded group actions to the agents widget."""

    def test_agents_group_actions_use_agents_widget(self):
        self.assertEqual(
            _resolve_resource_uri("agents", "group_list"),
            "ui://agents/dashboard",
        )
        self.assertEqual(
            _resolve_resource_uri("agents", "group_remove_member"),
            "ui://agents/dashboard",
        )

    def test_regular_agents_actions_keep_agent_dashboard(self):
        self.assertEqual(_resolve_resource_uri("agents", "list"), "ui://agents/dashboard")


class PostNotificationIdentityGuardTests(unittest.IsolatedAsyncioTestCase):
    """_post_notification must skip POST when agent identity is missing."""

    async def test_skips_when_no_jwt(self):
        """Existing guard: skip when JWT is None."""
        with patch(
            "fastmcp_server.tool_call_notifier.api_request",
            new=AsyncMock(),
        ) as mock_api:
            await ToolCallNotificationMiddleware._post_notification(
                {"tool_name": "context", "agent_name": None, "agent_id": None},
                jwt=None,
            )
        mock_api.assert_not_called()

    async def test_skips_when_no_agent_name_and_no_agent_id(self):
        """New guard: skip when both agent_name and agent_id are None.

        This happens when a browser widget client authenticates via Cognito
        user session token without connecting through /mcp/agents/{name}.
        The backend returns 403 on POST /api/v1/tool-calls because it
        requires authenticated agent context.
        """
        with patch(
            "fastmcp_server.tool_call_notifier.api_request",
            new=AsyncMock(),
        ) as mock_api:
            await ToolCallNotificationMiddleware._post_notification(
                {
                    "tool_name": "context",
                    "tool_call_id": "test-id",
                    "agent_name": None,
                    "agent_id": None,
                    "space_id": "space-1",
                },
                jwt="valid-jwt-token",
            )
        mock_api.assert_not_called()

    async def test_sends_when_agent_name_present(self):
        """Normal case: notification fires when agent identity exists."""
        with patch(
            "fastmcp_server.tool_call_notifier.api_request",
            new=AsyncMock(return_value={}),
        ) as mock_api:
            await ToolCallNotificationMiddleware._post_notification(
                {
                    "tool_name": "context",
                    "tool_call_id": "test-id",
                    "agent_name": "relay",
                    "agent_id": None,
                    "space_id": "space-1",
                    "duration_ms": 50,
                },
                jwt="valid-jwt-token",
            )
        mock_api.assert_called_once()

    async def test_sends_when_agent_id_present(self):
        """Notification fires when agent_id exists even without agent_name."""
        with patch(
            "fastmcp_server.tool_call_notifier.api_request",
            new=AsyncMock(return_value={}),
        ) as mock_api:
            await ToolCallNotificationMiddleware._post_notification(
                {
                    "tool_name": "messages",
                    "tool_call_id": "test-id",
                    "agent_name": None,
                    "agent_id": "uuid-123",
                    "space_id": "space-1",
                    "duration_ms": 30,
                },
                jwt="valid-jwt-token",
            )
        mock_api.assert_called_once()


class ViewerPrincipalAuditTests(unittest.IsolatedAsyncioTestCase):
    """MCP-DISCOVERY-001: shared audit fires only for token-asserted agents.

    Per ATTR-001 only a token can bind agent identity. A logged-in user
    (viewer) whose ``x-agent-name`` is supplied by the /mcp/agents/{name}
    route header — but whose token asserts no agent — must render
    viewer-private: no shared-audit POST, no broadcast, no provoked 403.
    """

    def _fire(self, *, claims, header_agent_name=None, has_token=True):
        token = SimpleNamespace(token="jwt", claims=claims) if has_token else None
        headers = {}
        if header_agent_name is not None:
            headers["x-agent-name"] = header_agent_name
        request = SimpleNamespace(headers=headers)
        mw = ToolCallNotificationMiddleware()
        # _fire_notification is sync; it dispatches via
        # asyncio.create_task(self._post_notification(...)). Python evaluates
        # the _post_notification(...) call eagerly to build the coroutine
        # BEFORE create_task receives it, so the AsyncMock records the call
        # synchronously inside this patch block — assert_called* works directly.
        with patch(
            "fastmcp_server.tool_call_notifier.get_access_token", return_value=token
        ), patch(
            "fastmcp_server.tool_call_notifier.get_http_request", return_value=request
        ), patch.object(
            ToolCallNotificationMiddleware, "_post_notification", new=AsyncMock()
        ) as mock_post:
            mw._fire_notification(
                tool_name="search",
                tool_call_id="call-1",
                arguments={"action": "list"},
                status="success",
                duration_ms=5,
            )
        return mock_post

    async def test_viewer_with_header_agent_name_does_not_audit(self):
        # User/viewer token: no agent claims. Header carries agent_name.
        mock_post = self._fire(claims={}, header_agent_name="protocol")
        mock_post.assert_not_called()

    async def test_agent_token_audits(self):
        # Token asserts an agent principal via claims.
        mock_post = self._fire(claims={"agent_id": "uuid-123"})
        mock_post.assert_called_once()

    async def test_agent_name_without_signed_id_does_not_audit(self):
        # Symmetric to agent_id: an agent_name claim also asserts a principal.
        mock_post = self._fire(claims={"agent_name": "relay"})
        mock_post.assert_not_called()

    async def test_no_token_does_not_audit(self):
        # Auth disabled or token extraction failed — treat as viewer-private
        # even when the route header carries an agent name.
        mock_post = self._fire(claims={}, header_agent_name="protocol", has_token=False)
        mock_post.assert_not_called()

    async def test_no_token_and_no_header_does_not_audit(self):
        # Fully unauthenticated call (no token, no x-agent-name) → viewer-private.
        mock_post = self._fire(claims={}, has_token=False)
        mock_post.assert_not_called()

    async def test_route_label_without_signed_agent_id_does_not_audit(self):
        # Headless/MCPJam: a PAT-exchanged MCP token carries no agent_id/
        # agent_name claim but is a legitimate agent via the named route
        # (token_class + ax-mcp audience + x-agent-name). It MUST still audit —
        # classified the same way api_client resolves the principal.
        mock_post = self._fire(
            claims={"token_class": "user_access", "audience": "ax-mcp"},
            header_agent_name="protocol",
        )
        mock_post.assert_not_called()
