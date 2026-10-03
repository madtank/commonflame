import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from fastmcp_server.tools.spaces import _normalize_member_item, register_spaces_tool


class SpacesToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mcp = FastMCP("test")
        register_spaces_tool(self.mcp)
        self.tool = await self.mcp.get_tool("spaces")
        self.token = SimpleNamespace(
            token="jwt",
            claims={"agent_name": "protocol", "space_id": "space-1", "agent_id": "agent-test"},
        )
        self.request = SimpleNamespace(headers={})
        self.private_permissions = {
            "space_context": {
                "id": "space-1",
                "name": "Personal workspace",
                "visibility": "private",
                "role": "admin",
                "is_personal": True,
                "scope": "private_workspace",
            },
            "permissions": {
                "mode": "private_full_access",
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": False,
                "can_archive": True,
                "can_control": False,
                "can_write_memory": False,
                "can_use_hitl_approval": True,
            },
        }

    async def _call_tool(self, **kwargs):
        with patch(
            "fastmcp_server.tools.spaces.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=self.private_permissions),
        ):
            return await self.tool.fn(
                token=self.token,
                request=self.request,
                **kwargs,
            )

    async def test_list_returns_v2_envelope_without_members_array(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"spaces": [{"id": "space-1", "name": "Alpha"}], "count": 1}),
        ) as api_mock:
            result = await self._call_tool(action="list")

        args, _ = api_mock.await_args
        self.assertEqual(args[:3], ("GET", "/api/v1/spaces", "jwt"))
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_collection")
        self.assertEqual(structured["version"], 2)
        self.assertEqual(structured["state"], "ready")
        self.assertIn("data", structured)
        self.assertIn("actions", structured)
        self.assertIn("items", structured["data"])
        self.assertNotIn("members", structured["data"])

    async def test_members_returns_v2_envelope_without_spaces_array(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(
                return_value={
                    "members": [{"id": "user-1", "display_name": "Pat", "full_name": "Pat"}],
                    "count": 1,
                }
            ),
        ) as api_mock:
            result = await self._call_tool(action="members", space_id="space-1")

        args, _ = api_mock.await_args
        self.assertEqual(args[:3], ("GET", "/api/v1/spaces/space-1/members", "jwt"))
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_members")
        self.assertEqual(structured["version"], 2)
        self.assertIn("items", structured["data"])
        self.assertNotIn("spaces", structured["data"])
        self.assertEqual(structured["data"]["space_id"], "space-1")
        self.assertEqual(structured["data"]["items"][0]["full_name"], "Pat")
        self.assertEqual(structured["data"]["items"][0]["display_name"], "Pat")

    async def test_members_falls_back_to_token_space_context(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"members": [], "count": 0}),
        ) as api_mock:
            result = await self._call_tool(action="members")

        args, _ = api_mock.await_args
        self.assertEqual(args[:3], ("GET", "/api/v1/spaces/space-1/members", "jwt"))
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_members")
        self.assertEqual(structured["data"]["space_id"], "space-1")

    async def test_current_returns_active_space_from_context(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(
                return_value={
                    "id": "space-1",
                    "name": "Personal workspace",
                    "is_personal": True,
                    "space_mode": "personal",
                }
            ),
        ) as api_mock:
            result = await self._call_tool(action="current")

        args, _ = api_mock.await_args
        self.assertEqual(args[:3], ("GET", "/api/v1/spaces/space-1", "jwt"))
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["data"]["scope"], "current")
        self.assertEqual(structured["data"]["current_space"]["id"], "space-1")

    async def test_current_without_space_context_returns_widget_error(self) -> None:
        token = SimpleNamespace(token="jwt", claims={"sub": "user-1"})
        permissions = {
            "permissions": {
                "mode": "private_full_access",
                "read_only": False,
            },
        }

        with patch(
            "fastmcp_server.tools.spaces.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=permissions),
        ):
            result = await self.tool.fn(
                action="current",
                token=token,
                request=SimpleNamespace(headers={}),
            )

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["data"]["scope"], "current_error")
        self.assertEqual(structured["notice"]["code"], "spaces_current_error")

    async def test_switch_user_session_posts_spaces_switch_endpoint(self) -> None:
        user_token = SimpleNamespace(token="jwt", claims={"sub": "user-1"})
        user_request = SimpleNamespace(headers={"x-space-id": "space-1"})
        with patch(
            "fastmcp_server.tools.spaces.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=self.private_permissions),
        ), patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(
                return_value={
                    "message": "Switched to Team Hub",
                    "space_name": "Team Hub",
                    "available_agents": [{"name": "protocol"}],
                }
            ),
        ) as api_mock:
            result = await self.tool.fn(
                action="switch",
                space_id="space-2",
                token=user_token,
                request=user_request,
            )

        args, kwargs = api_mock.await_args
        self.assertEqual(args[:3], ("POST", "/api/v1/spaces/switch", "jwt"))
        self.assertEqual(kwargs["json_data"], {"space_id": "space-2"})
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["data"]["scope"], "switch_result")
        self.assertEqual(structured["data"]["current_space"]["id"], "space-2")
        self.assertEqual(structured["data"]["current_space"]["name"], "Team Hub")
        self.assertEqual(structured["notice"]["code"], "space_switched")

    async def test_switch_user_session_resolves_slug_before_switching(self) -> None:
        user_token = SimpleNamespace(token="jwt", claims={"sub": "user-1"})
        user_request = SimpleNamespace(headers={"x-space-id": "space-1"})
        api_mock = AsyncMock(
            side_effect=[
                {
                    "spaces": [
                        {"id": "space-1", "slug": "personal", "name": "Personal"},
                        {"id": "space-2", "slug": "team-hub", "name": "Team Hub"},
                    ],
                    "count": 2,
                },
                {"message": "Switched to Team Hub", "space_name": "Team Hub"},
            ]
        )
        with patch(
            "fastmcp_server.tools.spaces.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=self.private_permissions),
        ), patch("fastmcp_server.api_client.api_request", new=api_mock):
            result = await self.tool.fn(
                action="switch",
                slug="team-hub",
                token=user_token,
                request=user_request,
            )

        list_call, switch_call = api_mock.await_args_list
        self.assertEqual(list_call.args[:3], ("GET", "/api/v1/spaces", "jwt"))
        self.assertEqual(switch_call.args[:3], ("POST", "/api/v1/spaces/switch", "jwt"))
        self.assertEqual(switch_call.kwargs["json_data"], {"space_id": "space-2"})
        structured = result.structured_content
        self.assertEqual(structured["data"]["current_space"]["id"], "space-2")

    async def test_switch_slug_not_found_returns_specific_widget_error(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"spaces": [{"id": "space-1", "slug": "personal"}]}),
        ):
            result = await self._call_tool(action="switch", slug="missing")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["data"]["scope"], "switch_error")
        self.assertIn("missing", structured["notice"]["message"])

    async def test_switch_user_session_falls_back_to_legacy_org_switch(self) -> None:
        user_token = SimpleNamespace(token="jwt", claims={"sub": "user-1"})
        user_request = SimpleNamespace(headers={"x-space-id": "space-1"})
        api_mock = AsyncMock(
            side_effect=[
                {"error": "API error 404", "detail": '{"detail":"Not Found"}'},
                {"message": "Switched to Team Hub", "org_name": "Team Hub"},
            ]
        )
        with patch(
            "fastmcp_server.tools.spaces.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=self.private_permissions),
        ), patch("fastmcp_server.api_client.api_request", new=api_mock):
            result = await self.tool.fn(
                action="switch",
                space_id="space-2",
                token=user_token,
                request=user_request,
            )

        primary_call, fallback_call = api_mock.await_args_list
        self.assertEqual(primary_call.args[:3], ("POST", "/api/v1/spaces/switch", "jwt"))
        self.assertEqual(primary_call.kwargs["json_data"], {"space_id": "space-2"})
        self.assertEqual(fallback_call.args[:3], ("POST", "/api/organizations/switch", "jwt"))
        self.assertEqual(fallback_call.kwargs["json_data"], {"org_id": "space-2"})
        structured = result.structured_content
        self.assertEqual(structured["notice"]["code"], "space_switched")

    async def test_switch_user_session_does_not_fall_back_on_forbidden(self) -> None:
        user_token = SimpleNamespace(token="jwt", claims={"sub": "user-1"})
        user_request = SimpleNamespace(headers={"x-space-id": "space-1"})
        api_mock = AsyncMock(return_value={"error": "API error 403", "detail": "Forbidden"})
        with patch(
            "fastmcp_server.tools.spaces.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=self.private_permissions),
        ), patch("fastmcp_server.api_client.api_request", new=api_mock):
            result = await self.tool.fn(
                action="switch",
                space_id="space-2",
                token=user_token,
                request=user_request,
            )

        self.assertEqual(api_mock.await_count, 1)
        structured = result.structured_content
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["notice"]["code"], "spaces_switch_error")

    async def test_switch_route_bound_agent_posts_agent_placement_endpoint(self) -> None:
        api_mock = AsyncMock(
            side_effect=[
                {"id": "agent-1", "name": "protocol", "space_id": "space-1"},
                {
                    "agent_id": "agent-1",
                    "agent_name": "protocol",
                    "space_id": "space-2",
                    "space_name": "Team Hub",
                    "pinned": False,
                },
            ]
        )
        with patch("fastmcp_server.api_client.api_request", new=api_mock):
            result = await self._call_tool(action="switch", space_id="space-2")

        me_call, switch_call = api_mock.await_args_list
        self.assertEqual(me_call.args[:3], ("GET", "/api/v1/agents/me", "jwt"))
        self.assertEqual(switch_call.args[:3], ("POST", "/api/v1/agents/agent-1/placement", "jwt"))
        self.assertEqual(switch_call.kwargs["json_data"], {"space_id": "space-2", "pinned": False})
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "switch_result")
        self.assertEqual(structured["data"]["current_space"]["id"], "space-2")
        self.assertEqual(structured["data"]["agent"]["id"], "agent-1")

    async def test_switch_route_bound_agent_preserves_pin_state(self) -> None:
        api_mock = AsyncMock(
            side_effect=[
                {
                    "id": "agent-1",
                    "name": "protocol",
                    "space_id": "space-1",
                    "space_locked": True,
                },
                {
                    "agent_id": "agent-1",
                    "agent_name": "protocol",
                    "space_id": "space-2",
                    "space_name": "Team Hub",
                    "pinned": True,
                },
            ]
        )
        with patch("fastmcp_server.api_client.api_request", new=api_mock):
            await self._call_tool(action="switch", space_id="space-2")

        switch_call = api_mock.await_args_list[1]
        self.assertEqual(switch_call.kwargs["json_data"], {"space_id": "space-2", "pinned": True})

    async def test_switch_route_bound_agent_unrecognized_pin_string_defaults_to_false(self) -> None:
        api_mock = AsyncMock(
            side_effect=[
                {
                    "id": "agent-1",
                    "name": "protocol",
                    "space_id": "space-1",
                    "space_locked": "locked",
                },
                {
                    "agent_id": "agent-1",
                    "agent_name": "protocol",
                    "space_id": "space-2",
                    "space_name": "Team Hub",
                    "pinned": False,
                },
            ]
        )
        with patch("fastmcp_server.api_client.api_request", new=api_mock):
            await self._call_tool(action="switch", space_id="space-2")

        switch_call = api_mock.await_args_list[1]
        self.assertEqual(switch_call.kwargs["json_data"], {"space_id": "space-2", "pinned": False})

    async def test_switch_route_bound_agent_missing_identity_returns_widget_error(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"name": "protocol"}),
        ):
            result = await self._call_tool(action="switch", space_id="space-2")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["data"]["scope"], "switch_error")
        self.assertEqual(structured["notice"]["code"], "spaces_switch_error")

    async def test_switch_requires_space_id(self) -> None:
        result = await self._call_tool(action="switch")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["data"]["scope"], "switch_error")
        self.assertIn("space_id or slug", structured["notice"]["message"])

    def test_normalize_member_item_does_not_backfill_full_name_from_email(self) -> None:
        normalized = _normalize_member_item(
            {
                "id": "member-1",
                "email": "member@example.com",
            }
        )

        self.assertEqual(normalized["display_name"], "member@example.com")
        self.assertNotIn("full_name", normalized)

    async def test_list_accepts_array_results_shape(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"results": [{"id": "space-1", "name": "Alpha"}], "count": 1}),
        ):
            result = await self._call_tool(action="list")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_collection")
        self.assertEqual(structured["data"]["items"][0]["name"], "Alpha")
        self.assertEqual(structured["data"]["count"], 1)

    async def test_list_error_returns_error_envelope(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"error": "API error 503", "detail": "spaces unavailable"}),
        ):
            result = await self._call_tool(action="list")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_collection")
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["data"]["error"], "API error 503")
        self.assertEqual(structured["notice"]["severity"], "error")

    async def test_get_by_slug_uses_visible_spaces_list(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(
                return_value={
                    "spaces": [
                        {"id": "space-1", "slug": "alpha", "name": "Alpha"},
                        {"id": "space-2", "slug": "beta", "name": "Beta"},
                    ],
                    "count": 2,
                }
            ),
        ) as api_mock:
            result = await self._call_tool(action="get", slug="beta")

        args, _ = api_mock.await_args
        self.assertEqual(args[:3], ("GET", "/api/v1/spaces", "jwt"))
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["data"]["space"]["id"], "space-2")

    async def test_get_by_slug_preserves_list_errors(self) -> None:
        upstream_error = {"error": "API error 503", "detail": "spaces backend unavailable"}
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value=upstream_error),
        ):
            result = await self._call_tool(action="get", slug="beta")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["data"]["error"], "API error 503")
        self.assertEqual(structured["notice"]["severity"], "error")

    async def test_get_by_slug_returns_not_found_when_list_succeeds_without_match(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"spaces": [{"id": "space-1", "slug": "alpha"}]}),
        ):
            result = await self._call_tool(action="get", slug="beta")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["data"]["error"], "Space not found")

    async def test_update_returns_detail_with_notice(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"id": "space-1", "name": "Alpha", "description": "Updated"}),
        ):
            result = await self._call_tool(action="update", space_id="space-1", name="Alpha", description="Updated")

        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["data"]["space"]["description"], "Updated")
        self.assertEqual(structured["notice"]["code"], "space_updated")

    async def test_update_is_blocked_in_shared_space(self) -> None:
        shared_permissions = {
            "space_context": {
                "id": "space-2",
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
        with patch(
            "fastmcp_server.tools.spaces.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=shared_permissions),
        ):
            result = await self.tool.fn(
                action="update",
                space_id="space-2",
                name="Blocked",
                token=self.token,
                request=self.request,
            )

        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "update_blocked")
        self.assertEqual(structured["notice"]["code"], "spaces_update_blocked")

    async def test_create_draft_maps_public_visibility_to_community_draft(self) -> None:
        draft_response = {
            "draft_id": "draft-1",
            "version": 1,
            "status": "under_review",
            "kind": "spaces.create.community",
            "space": {
                "name": "The Thunderdome",
                "description": "Public coordination arena",
                "visibility": "public",
            },
            "editable_fields": ["space.name", "space.description", "space.visibility"],
        }
        api_mock = AsyncMock(return_value=draft_response)
        with patch("fastmcp_server.api_client.api_request", new=api_mock):
            result = await self._call_tool(
                action="create_draft",
                name="The Thunderdome",
                description="Public coordination arena",
                visibility="public",
            )

        api_mock.assert_awaited_once()
        args, kwargs = api_mock.await_args
        self.assertEqual(args[:3], ("POST", "/api/v1/drafts/spaces", "jwt"))
        self.assertEqual(kwargs["json_data"]["space_mode"], "community")
        self.assertEqual(kwargs["json_data"]["space"]["visibility"], "public")
        self.assertEqual(kwargs["delegation_mode"], None)
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_collection")
        self.assertEqual(structured["data"]["scope"], "create")
        self.assertEqual(structured["data"]["draft"]["space_mode"], "community")
        self.assertEqual(structured["data"]["draft"]["name"], "The Thunderdome")

    async def test_approve_draft_returns_created_space_detail(self) -> None:
        draft_response = {
            "draft_id": "draft-1",
            "version": 2,
            "status": "executed",
            "kind": "spaces.create.community",
            "space": {"name": "The Thunderdome", "visibility": "public"},
            "execution_result": {
                "space": {
                    "id": "space-new",
                    "name": "The Thunderdome",
                    "slug": "the-thunderdome",
                    "visibility": "public",
                }
            },
        }
        api_mock = AsyncMock(return_value=draft_response)
        with patch("fastmcp_server.api_client.api_request", new=api_mock):
            result = await self._call_tool(action="approve_draft", draft_id="draft-1", version=2)

        args, kwargs = api_mock.await_args
        self.assertEqual(args[:3], ("POST", "/api/v1/drafts/draft-1/approve", "jwt"))
        self.assertEqual(kwargs["json_data"], {"version": 2})
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_detail")
        self.assertEqual(structured["data"]["scope"], "created")
        self.assertEqual(structured["data"]["created"]["id"], "space-new")

    async def test_reject_draft_returns_dismissed_scope(self) -> None:
        draft_response = {
            "draft_id": "draft-1",
            "version": 2,
            "status": "rejected",
            "kind": "spaces.create.team",
            "space": {"name": "Team Space", "visibility": "invite_only"},
        }
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value=draft_response),
        ):
            result = await self._call_tool(action="reject_draft", draft_id="draft-1", version=2)

        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "draft_dismissed")
        self.assertEqual(structured["data"]["draft"]["space_mode"], "team")

    async def test_create_draft_allows_hitl_in_shared_space(self) -> None:
        shared_permissions = {
            "space_context": {
                "id": "space-2",
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
                "can_use_hitl_approval": True,
            },
        }
        draft_response = {
            "draft_id": "draft-shared-space-1",
            "version": 1,
            "status": "under_review",
            "kind": "spaces.create.team",
            "space": {
                "name": "Team Draft",
                "visibility": "invite_only",
            },
        }
        with patch(
            "fastmcp_server.tools.spaces.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=shared_permissions),
        ), patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value=draft_response),
        ) as api_mock:
            result = await self.tool.fn(
                action="create_draft",
                name="Team Draft",
                token=self.token,
                request=self.request,
            )

        args, kwargs = api_mock.await_args
        self.assertEqual(args[:3], ("POST", "/api/v1/drafts/spaces", "jwt"))
        self.assertEqual(kwargs["json_data"]["space"]["name"], "Team Draft")
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "create")
        self.assertEqual(structured["data"]["draft"]["draft_id"], "draft-shared-space-1")

    async def test_discover_lists_public_spaces(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"results": [{"id": "public-1", "name": "Town Square"}], "count": 1}),
        ) as api_mock:
            result = await self._call_tool(action="discover")

        args, _kwargs = api_mock.await_args
        self.assertEqual(args[:3], ("GET", "/api/spaces/public", "jwt"))
        structured = result.structured_content
        self.assertEqual(structured["kind"], "space_collection")
        self.assertEqual(structured["data"]["scope"], "discover")
        self.assertEqual(structured["data"]["items"][0]["name"], "Town Square")

    async def test_join_invite_posts_invite_code(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"message": "Successfully joined Team!", "space_name": "Team"}),
        ) as api_mock:
            result = await self._call_tool(action="join_invite", invite_code="ABC123")

        args, kwargs = api_mock.await_args
        self.assertEqual(args[:3], ("POST", "/api/spaces/join", "jwt"))
        self.assertEqual(kwargs["json_data"], {"invite_code": "ABC123"})
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "join_result")
        self.assertEqual(structured["notice"]["code"], "space_joined")

    async def test_join_public_posts_space_id(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"message": "Successfully joined Town Square!", "space_name": "Town Square"}),
        ) as api_mock:
            result = await self._call_tool(action="join_public", space_id="public-1")

        args, kwargs = api_mock.await_args
        self.assertEqual(args[:3], ("POST", "/api/spaces/join-public", "jwt"))
        self.assertEqual(kwargs["json_data"], {"space_id": "public-1"})
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "join_result")
        self.assertEqual(structured["data"]["space_name"], "Town Square")

    async def test_join_public_falls_back_to_legacy_organization_route(self) -> None:
        api_mock = AsyncMock(
            side_effect=[
                {"error": "API error 404", "detail": '{"detail":"Not Found"}'},
                {"message": "Successfully joined Town Square!", "org_name": "Town Square"},
            ]
        )
        with patch("fastmcp_server.api_client.api_request", new=api_mock):
            result = await self._call_tool(action="join_public", space_id="public-1")

        first_args, first_kwargs = api_mock.await_args_list[0]
        second_args, second_kwargs = api_mock.await_args_list[1]
        self.assertEqual(first_args[:3], ("POST", "/api/spaces/join-public", "jwt"))
        self.assertEqual(first_kwargs["json_data"], {"space_id": "public-1"})
        self.assertEqual(second_args[:3], ("POST", "/api/organizations/join-public", "jwt"))
        self.assertEqual(second_kwargs["json_data"], {"org_id": "public-1"})
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "join_result")
        self.assertEqual(structured["data"]["space_name"], "Town Square")

    async def test_create_invite_posts_space_invite_request(self) -> None:
        invite = {
            "id": "invite-1",
            "invite_code": "ABC123",
            "space_id": "space-1",
            "space_name": "Team",
            "current_uses": 0,
            "active": True,
        }
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value=invite),
        ) as api_mock:
            result = await self._call_tool(action="create_invite", space_id="space-1", expires_hours=168, max_uses=5)

        args, kwargs = api_mock.await_args
        self.assertEqual(args[:3], ("POST", "/api/spaces/space-1/invites", "jwt"))
        self.assertEqual(kwargs["json_data"], {"expires_hours": 168, "max_uses": 5})
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "invite_created")
        self.assertEqual(structured["data"]["invite"]["invite_code"], "ABC123")


if __name__ == "__main__":
    unittest.main()
