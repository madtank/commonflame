"""Regression tests for whoami tool compatibility behavior."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from fastmcp_server.mcp_ui import build_notice
from fastmcp_server.tools.whoami import (
    _build_identity_payload,
    _recall_user_memory,
    _whoami_widget_result,
    register_whoami_tool,
)


class WhoamiFallbackTests(unittest.IsolatedAsyncioTestCase):
    """whoami must remain compatible with ordinary MCP principals."""

    async def test_identity_payload_uses_v1_agent_route(self):
        ctx = {
            "jwt": "token",
            "principal_type": "agent",
            "agent_name": "test_agent",
            "space_id": "space-123",
        }
        public_identity = {
            "id": "agent-1",
            "name": "Test Agent",
            "handle": "@test_agent",
            "workspace": {"id": "space-123", "name": "Example Space"},
        }
        api_request = AsyncMock(return_value=public_identity)

        with patch("fastmcp_server.tools.whoami.api_request", new=api_request):
            payload = await _build_identity_payload(ctx, include_memory=False)

        self.assertEqual(payload["id"], "agent-1")
        self.assertEqual(payload["workspace"]["name"], "Example Space")
        api_request.assert_awaited_once_with(
            "GET",
            "/api/v1/agents/me",
            "token",
            agent_name="test_agent",
            agent_id=None,
            space_id="space-123",
        )

    async def test_memory_snapshot_falls_back_to_empty_list_on_public_error(self):
        ctx = {
            "jwt": "token",
            "principal_type": "agent",
            "agent_name": "test_agent",
            "space_id": "space-123",
        }
        public_identity = {
            "id": "agent-1",
            "name": "Test Agent",
            "workspace": {"id": "space-123", "name": "Example Space"},
        }
        memory_failure = {"error": "API error 404", "detail": "Not Found"}
        api_request = AsyncMock(side_effect=[public_identity, memory_failure])

        with patch("fastmcp_server.tools.whoami.api_request", new=api_request):
            payload = await _build_identity_payload(ctx, include_memory=True)

        self.assertEqual(payload["id"], "agent-1")
        self.assertEqual(payload["memory_items"], [])
        self.assertEqual(api_request.await_count, 2)
        first_call = api_request.await_args_list[0]
        second_call = api_request.await_args_list[1]
        self.assertEqual(first_call.args[:2], ("GET", "/api/v1/agents/me"))
        self.assertEqual(second_call.args[:2], ("GET", "/api/v1/agents/me/memory"))

    async def test_identity_payload_uses_auth_me_and_settings_for_user_principal(self):
        ctx = {
            "jwt": "token",
            "agent_name": None,
            "space_id": "space-123",
        }
        auth_identity = {
            "id": "user-1",
            "email": "jacob@example.com",
            "full_name": "Jacob",
            "username": "madtank",
        }
        settings = {
            "custom": {
                "mcp_identity": {
                    "profile": {
                        "bio": "Building Commonflame.",
                        "preferences": "Direct, concise communication.",
                        "projects": "MCP apps, widget validation",
                        "specialization": "Platform engineering",
                        "capabilities": ["delivery", "product"],
                    },
                    "memories": {
                        "widget_focus": {
                            "value": "Stability first.",
                            "created_at": "2026-04-10T00:00:00+00:00",
                            "updated_at": "2026-04-10T00:00:00+00:00",
                        }
                    },
                }
            }
        }
        api_request = AsyncMock(side_effect=[auth_identity, settings])

        with patch("fastmcp_server.tools.whoami.api_request", new=api_request):
            payload = await _build_identity_payload(ctx, include_memory=True)

        self.assertEqual(payload["principal_kind"], "user")
        self.assertEqual(payload["display_name"], "Jacob")
        self.assertEqual(payload["handle"], "madtank")
        self.assertEqual(payload["preferences"], "Direct, concise communication.")
        self.assertEqual(payload["projects"], "MCP apps, widget validation")
        self.assertEqual(payload["memory_items"][0]["key"], "widget_focus")
        self.assertEqual(api_request.await_args_list[0].args[:2], ("GET", "/auth/me"))
        self.assertEqual(api_request.await_args_list[1].args[:2], ("GET", "/api/v1/settings"))

    async def test_user_memory_recall_returns_stored_value(self):
        ctx = {
            "jwt": "token",
            "agent_name": None,
            "space_id": "space-123",
        }
        settings = {
            "custom": {
                "mcp_identity": {
                    "memories": {
                        "widget_focus": {
                            "value": "Stability first.",
                            "created_at": "2026-04-10T00:00:00+00:00",
                            "updated_at": "2026-04-10T00:00:00+00:00",
                        }
                    },
                }
            }
        }

        with patch("fastmcp_server.tools.whoami.api_request", new=AsyncMock(return_value=settings)):
            result = await _recall_user_memory(ctx, key="widget_focus")

        self.assertEqual(result["key"], "widget_focus")
        self.assertEqual(result["value"], "Stability first.")

    def test_remember_widget_returns_enriched_profile_with_memory_tab_active(self):
        identity = {
            "id": "agent-1",
            "name": "Test Agent",
            "handle": "@test_agent",
            "workspace": {"id": "space-123", "name": "Example Space"},
            "memory_items": [{"key": "favorite_color", "value": "green"}],
        }

        result = _whoami_widget_result(
            identity,
            "remember",
            notice=build_notice('Saved memory "favorite_color".', code="memory_saved"),
        )

        structured = result.structured_content
        self.assertEqual(structured["kind"], "whoami_profile")
        self.assertEqual(structured["active_tab"], "memories")
        self.assertEqual(structured["notice"]["code"], "memory_saved")
        self.assertEqual(
            structured["data"]["memory"]["items"][0]["key"],
            "favorite_color",
        )

    def test_user_widget_result_surfaces_profile_sections(self):
        identity = {
            "principal_kind": "user",
            "id": "user-1",
            "display_name": "Jacob",
            "handle": "madtank",
            "email": "jacob@example.com",
            "avatar_url": "https://cdn.example.com/jacob.png",
            "bio": "Building Commonflame.",
            "preferences": "Direct communication.",
            "projects": "MCP widgets",
            "specialization": "Platform engineering",
            "capabilities": ["delivery", "product"],
            "memory_items": [{"key": "widget_focus", "value": "Stability first."}],
            "memory_count": 1,
        }

        result = _whoami_widget_result(identity, "get")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "whoami_profile")
        self.assertEqual(structured["data"]["identity"]["role_label"], "User")
        self.assertEqual(structured["data"]["identity"]["avatar_url"], "https://cdn.example.com/jacob.png")
        self.assertEqual(structured["data"]["identity"]["preferences"], "Direct communication.")
        self.assertEqual(structured["data"]["identity"]["projects"], "MCP widgets")
        self.assertEqual(structured["data"]["memory"]["items"][0]["key"], "widget_focus")


class WhoamiPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mcp = FastMCP("test")
        register_whoami_tool(self.mcp)
        self.tool = await self.mcp.get_tool("whoami")
        self.token = SimpleNamespace(token="jwt", claims={"space_id": "shared-space"})
        self.request = SimpleNamespace(headers={})
        self.shared_permissions = {
            "space_context": {
                "id": "shared-space",
                "name": "Shared Space",
                "visibility": "private",
                "role": "member",
                "is_personal": False,
                "scope": "shared_space",
            },
            "permissions": {
                "mode": "agent_authored_limited",
                "read_only": True,
                "blocked_reason": "Agent-authored actions require user approval. Use a user quick action to make changes.",
                "can_create": False,
                "can_update": False,
                "can_delete": False,
                "can_archive": False,
                "can_control": False,
                "can_write_memory": False,
                "can_use_hitl_approval": False,
            },
        }
        self.update_permissions = {
            **self.shared_permissions,
            "permissions": {
                **self.shared_permissions["permissions"],
                "mode": "agent_personal",
                "read_only": False,
                "blocked_reason": None,
                "can_update": True,
            },
        }

    async def test_update_schema_advertises_self_avatar_fields(self):
        properties = self.tool.parameters["properties"]
        self.assertIn("avatar_url", properties)
        self.assertIn("avatar_emoji", properties)
        self.assertIn("caller's avatar", properties["avatar_url"]["description"])
        self.assertIn("caller's avatar", properties["avatar_emoji"]["description"])

    async def test_agent_self_avatar_update_routes_through_whoami(self):
        token = SimpleNamespace(
            token="jwt",
            claims={"agent_id": "agent-1", "agent_name": "daimon", "space_id": "shared-space"},
        )
        identity = {
            "id": "agent-1",
            "name": "Daimon",
            "handle": "@daimon",
            "avatar_url": "data:image/svg+xml;base64,updated",
            "workspace": {"id": "shared-space", "name": "Shared Space"},
        }
        api_request = AsyncMock(return_value={"ok": True})

        with (
            patch(
                "fastmcp_server.tools.whoami.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.update_permissions),
            ),
            patch("fastmcp_server.tools.whoami.api_request", new=api_request),
            patch(
                "fastmcp_server.tools.whoami._build_identity_payload",
                new=AsyncMock(return_value=identity),
            ),
        ):
            result = await self.tool.fn(
                action="update",
                avatar_emoji="🍑",
                token=token,
                request=self.request,
            )

        api_request.assert_awaited_once()
        call = api_request.await_args
        self.assertEqual(call.args[:3], ("PATCH", "/api/v1/agents/me", "jwt"))
        self.assertIn("avatar_url", call.kwargs["json_data"])
        self.assertTrue(call.kwargs["json_data"]["avatar_url"].startswith("data:image/svg+xml;base64,"))
        self.assertEqual(result.structured_content["notice"]["code"], "profile_updated")

    async def test_remember_returns_profile_with_block_notice_in_shared_space(self):
        identity = {
            "principal_kind": "user",
            "id": "user-1",
            "display_name": "Jacob",
            "handle": "madtank",
            "memory_items": [],
            "memory_count": 0,
        }

        with (
            patch(
                "fastmcp_server.tools.whoami.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=self.shared_permissions),
            ),
            patch(
                "fastmcp_server.tools.whoami._build_identity_payload",
                new=AsyncMock(return_value=identity),
            ),
        ):
            result = await self.tool.fn(
                action="remember",
                key="favorite_workflow",
                value="Ship directly to dev/staging",
                token=self.token,
                request=self.request,
            )

        structured = result.structured_content
        self.assertEqual(structured["kind"], "whoami_profile")
        self.assertEqual(structured["notice"]["code"], "identity_memory_blocked")
        self.assertTrue(structured["data"]["permissions"]["read_only"])

    async def test_user_update_avatar_url_is_saved_in_identity_settings(self):
        user_token = SimpleNamespace(token="jwt", claims={"space_id": "shared-space"})
        writable_permissions = {
            "space_context": {"id": "shared-space", "name": "Shared Space"},
            "permissions": {"can_update": True, "can_write_memory": True},
        }
        settings = {"custom": {"mcp_identity": {"profile": {}, "memories": {}}}}
        auth_identity = {"id": "user-1", "email": "jacob@example.com", "full_name": "Jacob"}
        api_request = AsyncMock(side_effect=[settings, {"ok": True}, auth_identity, settings])

        with (
            patch(
                "fastmcp_server.tools.whoami.resolve_space_scoped_permissions",
                new=AsyncMock(return_value=writable_permissions),
            ),
            patch("fastmcp_server.tools.whoami.api_request", new=api_request),
        ):
            await self.tool.fn(
                action="update",
                avatar_url="https://cdn.example.com/jacob.png",
                token=user_token,
                request=self.request,
            )

        settings_patch = api_request.await_args_list[1]
        self.assertEqual(settings_patch.args[:2], ("PATCH", "/api/v1/settings"))
        profile = settings_patch.kwargs["json_data"]["custom"]["mcp_identity"]["profile"]
        self.assertEqual(profile["avatar_url"], "https://cdn.example.com/jacob.png")


    def test_identity_payload_includes_home_space_delegation_context(self):
        identity = {
            "id": "agent-1",
            "name": "Test Agent",
            "workspace": {"id": "space-123", "name": "Current Space"},
            "delegation_mode": "home_space",
            "delegation_home_space_id": "space-home",
            "delegation_home_space_name": "Home Space",
            "delegated_by_handle": "owner_agent",
            "delegated_by_name": "Owner Agent",
        }

        result = _whoami_widget_result(identity, "get")

        structured = result.structured_content
        self.assertEqual(structured["data"]["delegation"]["mode"], "home_space")
        self.assertEqual(structured["data"]["delegation"]["home_space"]["id"], "space-home")
        self.assertEqual(structured["data"]["delegation"]["home_space"]["name"], "Home Space")
        self.assertEqual(structured["data"]["delegation"]["delegated_by"]["handle"], "owner_agent")
        self.assertEqual(structured["data"]["delegation"]["delegated_by"]["display_name"], "Owner Agent")


if __name__ == "__main__":
    unittest.main()
