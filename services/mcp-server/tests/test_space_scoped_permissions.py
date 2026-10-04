from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastmcp_server.space_scoped_permissions import (
    attach_permission_bundle,
    blocked_reason,
    permissions_allow,
    resolve_space_scoped_permissions,
)


class SpaceScopedPermissionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_space_from_spaces_list_grants_full_access(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "space-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "spaces": [
                {
                    "id": "space-1",
                    "name": "madtank's Workspace",
                    "description": "Personal workspace for madtank",
                    "is_personal": True,
                    "visibility": "private",
                    "viewer_role": "admin",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ) as mock_api:
            bundle = await resolve_space_scoped_permissions(ctx, "agents")

        self.assertEqual(bundle["space_context"]["id"], "space-1")
        self.assertEqual(bundle["space_context"]["visibility"], "private")
        self.assertEqual(bundle["space_context"]["scope"], "private_workspace")
        self.assertTrue(bundle["permissions"]["can_control"])
        self.assertTrue(bundle["permissions"]["can_use_hitl_approval"])
        self.assertFalse(bundle["permissions"]["read_only"])
        self.assertEqual(mock_api.await_args.args[:2], ("GET", "/api/v1/spaces"))

    async def test_falls_back_to_v1_space_detail_when_list_lookup_misses(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "space-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {"spaces": [], "count": 0}
        fallback_response = {
            "id": "space-1",
            "name": "Jane's Workspace",
            "description": "Personal workspace for Jane Doe",
            "space_mode": "personal",
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(side_effect=[list_response, fallback_response]),
        ) as mock_api:
            bundle = await resolve_space_scoped_permissions(ctx, "whoami")

        self.assertEqual(bundle["space_context"]["scope"], "private_workspace")
        self.assertTrue(bundle["permissions"]["can_update"])
        self.assertEqual(mock_api.await_args_list[0].args[:2], ("GET", "/api/v1/spaces"))
        self.assertEqual(
            mock_api.await_args_list[1].args[:2],
            ("GET", "/api/v1/spaces/space-1"),
        )

    async def test_falls_back_to_detail_when_list_item_lacks_scope_fields(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "space-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "spaces": [{"id": "space-1", "name": "Jane's Workspace"}],
            "count": 1,
        }
        detail_response = {
            "id": "space-1",
            "name": "Jane's Workspace",
            "space_mode": "personal",
            "visibility": "private",
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(side_effect=[list_response, detail_response]),
        ) as mock_api:
            bundle = await resolve_space_scoped_permissions(ctx, "whoami")

        self.assertEqual(bundle["space_context"]["scope"], "private_workspace")
        self.assertTrue(bundle["space_context"]["is_personal"])
        self.assertEqual(len(mock_api.await_args_list), 2)
        self.assertEqual(
            mock_api.await_args_list[1].args[:2],
            ("GET", "/api/v1/spaces/space-1"),
        )

    async def test_unknown_scope_fails_closed_when_detail_lookup_fails(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "space-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "spaces": [{"id": "space-1", "name": "Jane's Workspace"}],
            "count": 1,
        }
        detail_error = {"error": "API error 503", "detail": "spaces unavailable"}

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(side_effect=[list_response, detail_error]),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "agents")

        self.assertEqual(bundle["space_context"]["scope"], "unscoped")
        self.assertFalse(bundle["permissions"]["can_use_hitl_approval"])
        self.assertEqual(
            bundle["permissions"]["blocked_reason"],
            "This action requires an active workspace context.",
        )

    async def test_agent_authored_shared_space_requires_user_approval_for_agents(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "shared-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "results": [
                {
                    "id": "shared-1",
                    "name": "Test Space",
                    "description": "Team collaboration space",
                    "space_mode": "team",
                    "visibility": "invite_only",
                    "viewer_role": "member",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "agents")

        self.assertEqual(bundle["space_context"]["scope"], "shared_space")
        self.assertEqual(bundle["permissions"]["mode"], "agent_authored_limited")
        self.assertTrue(bundle["permissions"]["read_only"])
        self.assertFalse(bundle["permissions"]["can_control"])
        self.assertTrue(bundle["permissions"]["can_use_hitl_approval"])
        self.assertEqual(
            bundle["permissions"]["blocked_reason"],
            "Agent-authored actions require user approval. Use a user quick action to make changes.",
        )

    async def test_agent_authored_non_personal_private_space_requires_user_approval(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "private-team-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "results": [
                {
                    "id": "private-team-1",
                    "name": "Direct Test Space",
                    "description": "Private shared test space",
                    "space_mode": "team",
                    "visibility": "private",
                    "viewer_role": "member",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "spaces")

        self.assertEqual(bundle["space_context"]["scope"], "shared_space")
        self.assertTrue(bundle["permissions"]["read_only"])
        self.assertFalse(bundle["permissions"]["can_create"])
        self.assertTrue(bundle["permissions"]["can_use_hitl_approval"])

    async def test_agent_authored_shared_space_allows_context_collaboration(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "shared-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "results": [
                {
                    "id": "shared-1",
                    "name": "Team Hub",
                    "space_mode": "team",
                    "visibility": "invite_only",
                    "viewer_role": "member",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "context")

        self.assertEqual(bundle["space_context"]["scope"], "shared_space")
        self.assertEqual(bundle["permissions"]["mode"], "shared_collaborative_space")
        self.assertFalse(bundle["permissions"]["read_only"])
        self.assertTrue(bundle["permissions"]["can_create"])
        self.assertTrue(bundle["permissions"]["can_update"])
        self.assertTrue(bundle["permissions"]["can_delete"])
        self.assertIsNone(bundle["permissions"]["blocked_reason"])

    async def test_agent_authored_non_personal_private_space_allows_context_collaboration(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "private-team-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "results": [
                {
                    "id": "private-team-1",
                    "name": "Direct Project Space",
                    "space_mode": "team",
                    "visibility": "private",
                    "viewer_role": "member",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "context")

        self.assertEqual(bundle["space_context"]["scope"], "shared_space")
        self.assertEqual(bundle["permissions"]["mode"], "shared_collaborative_space")
        self.assertFalse(bundle["permissions"]["read_only"])
        self.assertTrue(bundle["permissions"]["can_create"])
        self.assertTrue(bundle["permissions"]["can_update"])

    async def test_agent_authored_shared_space_allows_message_collaboration(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "shared-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "results": [
                {
                    "id": "shared-1",
                    "name": "Team Hub",
                    "space_mode": "team",
                    "visibility": "invite_only",
                    "viewer_role": "member",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "messages")

        self.assertEqual(bundle["permissions"]["mode"], "shared_collaborative_space")
        self.assertFalse(bundle["permissions"]["read_only"])
        self.assertTrue(bundle["permissions"]["can_create"])
        self.assertTrue(bundle["permissions"]["can_update"])

    async def test_description_heuristic_fails_closed_without_backend_personal_flag(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": "spoofed-space",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "results": [
                {
                    "id": "spoofed-space",
                    "name": "Spoofed Team Space",
                    "description": "Personal workspace for Mallory",
                    "space_mode": "team",
                    "visibility": "private",
                    "viewer_role": "admin",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "agents")

        self.assertEqual(bundle["space_context"]["scope"], "shared_space")
        self.assertFalse(bundle["permissions"]["can_create"])
        self.assertFalse(bundle["permissions"]["can_control"])
        self.assertTrue(bundle["permissions"]["can_use_hitl_approval"])

    async def test_signed_delegated_space_owner_claim_marks_personal_scope(self):
        ctx = {
            "jwt": "***",
            "agent_name": "Commonflame",
            "agent_id": "space-agent",
            "space_id": "personal-1",
            "delegation_mode": "home_space",
            "delegated_for": "user-1",
            "delegated_space_owner": True,
        }
        list_response = {
            "results": [
                {
                    "id": "personal-1",
                    "name": "madtank's Workspace",
                    "description": "Personal workspace for Jacob Taunton",
                    "visibility": "private",
                    "viewer_role": "admin",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "agents")

        self.assertEqual(bundle["space_context"]["scope"], "private_workspace")
        self.assertTrue(bundle["space_context"]["is_personal"])
        self.assertTrue(bundle["permissions"]["can_control"])
        self.assertTrue(bundle["permissions"]["can_use_hitl_approval"])

    async def test_user_session_controls_allow_quick_action_writes_any_space(self):
        ctx = {
            "jwt": "***",
            "principal_type": "user",
            "user_id": "user-1",
            "space_id": "shared-1",
            "delegation_mode": None,
            "delegated_for": None,
        }
        list_response = {
            "results": [
                {
                    "id": "shared-1",
                    "name": "Team Hub",
                    "space_mode": "team",
                    "visibility": "invite_only",
                    "viewer_role": "member",
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value=list_response),
        ):
            bundle = await resolve_space_scoped_permissions(ctx, "agents")

        self.assertEqual(bundle["permissions"]["mode"], "user_session_controls")
        self.assertTrue(bundle["permissions"]["can_update"])
        self.assertTrue(bundle["permissions"]["can_control"])
        self.assertTrue(bundle["permissions"]["can_use_hitl_approval"])

    async def test_unscoped_context_fails_closed_for_privileged_writes(self):
        ctx = {
            "jwt": "***",
            "agent_name": "protocol",
            "agent_id": "caller",
            "space_id": None,
            "delegation_mode": None,
            "delegated_for": None,
        }

        bundle = await resolve_space_scoped_permissions(ctx, "context")

        self.assertEqual(bundle["space_context"]["scope"], "unscoped")
        self.assertTrue(bundle["permissions"]["read_only"])
        self.assertFalse(bundle["permissions"]["can_create"])
        self.assertEqual(
            bundle["permissions"]["blocked_reason"],
            "This action requires an active workspace context.",
        )

    async def test_route_bound_agent_resolves_space_before_context_permissions(self):
        ctx = {
            "jwt": "***",
            "principal_type": "agent",
            "agent_name": "claimed-agent",
            "route_agent_name": "cipher",
            "agent_id": "claimed-agent-id",
            "space_id": None,
            "delegation_mode": None,
            "delegated_for": None,
        }
        agent_me_response = {
            "id": "agent-1",
            "name": "cipher",
            "space_id": "space-1",
            "workspace": {
                "id": "space-1",
                "name": "madtank's Workspace",
            },
        }
        list_response = {
            "spaces": [
                {
                    "id": "space-1",
                    "name": "madtank's Workspace",
                    "space_mode": "personal",
                    "visibility": "private",
                    "viewer_role": "admin",
                    "is_personal": True,
                }
            ],
            "count": 1,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(side_effect=[agent_me_response, list_response]),
        ) as mock_api:
            bundle = await resolve_space_scoped_permissions(ctx, "context")

        self.assertEqual(bundle["space_context"]["id"], "space-1")
        self.assertEqual(bundle["space_context"]["scope"], "private_workspace")
        self.assertEqual(bundle["permissions"]["mode"], "private_full_access")
        self.assertTrue(bundle["permissions"]["can_create"])
        self.assertEqual(mock_api.await_args_list[0].args[:2], ("GET", "/api/v1/agents/me"))
        self.assertEqual(mock_api.await_args_list[0].kwargs["agent_name"], "cipher")
        self.assertNotIn("agent_id", mock_api.await_args_list[0].kwargs)
        self.assertEqual(mock_api.await_args_list[1].args[:2], ("GET", "/api/v1/spaces"))
        self.assertEqual(mock_api.await_args_list[1].kwargs["space_id"], "space-1")

    async def test_route_bound_agent_space_resolution_failure_falls_back_to_unscoped(self):
        ctx = {
            "jwt": "***",
            "principal_type": "agent",
            "agent_name": "cipher",
            "route_agent_name": "cipher",
            "agent_id": None,
            "space_id": None,
            "delegation_mode": None,
            "delegated_for": None,
        }

        with patch(
            "fastmcp_server.space_scoped_permissions.api_request",
            new=AsyncMock(return_value={"error": "not_found"}),
        ) as mock_api:
            bundle = await resolve_space_scoped_permissions(ctx, "context")

        self.assertEqual(mock_api.await_count, 1)
        self.assertEqual(bundle["space_context"]["scope"], "unscoped")
        self.assertEqual(bundle["permissions"]["mode"], "unscoped_read_only")
        self.assertFalse(bundle["permissions"]["can_create"])

    def test_permission_helpers_are_null_safe(self):
        self.assertFalse(permissions_allow(None, "can_update"))
        self.assertEqual(blocked_reason(None), "Action not available in this context")

        bundle = {
            "space_context": {"id": "space-1"},
            "viewer": {"user_id": "user-1"},
            "permissions": {"can_update": True, "blocked_reason": None},
        }
        attached = attach_permission_bundle({"items": []}, bundle)
        self.assertEqual(attached["space_context"]["id"], "space-1")
        self.assertTrue(permissions_allow(bundle, "can_update"))
        self.assertEqual(blocked_reason(bundle, "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
