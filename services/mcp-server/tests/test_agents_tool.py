
"""Tests for agents tool availability and shared control-state semantics."""

from __future__ import annotations

import asyncio
import base64
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from fastmcp_server.tools.agents import (
    _build_control_payload,
    _enrich_widget_agent_contract,
    _enrich_with_availability,
    _extract_agent_items,
    _fetch_availability_map,
    _fetch_visible_spaces,
    _infer_connection_type,
    _merge_agent_items,
    _messageability_score,
    _normalize_avatar,
    register_agents_tool,
)


def _token() -> SimpleNamespace:
    return SimpleNamespace(
        token="***",
        claims={
            "agent_name": "protocol",
            "space_id": "space-1",
            "agent_id": "caller",
            "delegation_mode": "home_space",
            "delegated_for": {"user_id": "user-9"},
        },
    )


class TestNormalizeAvatar(unittest.TestCase):
    def test_avatar_emoji_escapes_svg_text_with_html_escape(self):
        uri = _normalize_avatar(None, "<>&")

        assert uri is not None
        prefix = "data:image/svg+xml;base64,"
        self.assertTrue(uri.startswith(prefix))
        svg = base64.b64decode(uri[len(prefix):]).decode("utf-8")
        self.assertIn("&lt;&gt;&amp;", svg)
        self.assertNotIn("<>&", svg)


class TestEnrichWithAvailability(unittest.TestCase):

    def test_connected_maps_to_high(self):
        items = [{"name": "agent_a", "presence": "connected"}]
        result = _enrich_with_availability(items)
        self.assertEqual(result[0]["availability_confidence"], "high")
        self.assertEqual(result[0]["control"]["state"], "active")
        self.assertTrue(result[0]["control"]["active"])
        self.assertFalse(result[0]["control"]["disabled"])
        self.assertEqual(result[0]["availability"]["state"], "available")
        self.assertEqual(result[0]["availability"]["reason"], "connected")
        self.assertEqual(result[0]["setup"]["state"], "ready")
        self.assertFalse(result[0]["setup_required"])

    def test_unknown_presence_defaults_to_offline(self):
        items = [{"name": "agent_e", "presence": "unknown_state"}]
        result = _enrich_with_availability(items)
        self.assertEqual(result[0]["availability_confidence"], "offline")

    def test_normalizes_temporary_break_from_backend_state(self):
        items = [{
            "id": "agent-1",
            "name": "Commonflame",
            "control_state": {
                "kind": "temporary_break",
                "reason": "cooldown",
                "until": "2026-04-08T23:00:00Z",
            },
        }]
        result = _enrich_with_availability(items)
        control = result[0]["control"]
        self.assertEqual(control["state"], "break")
        self.assertEqual(control["label"], "Break")
        self.assertEqual(control["break_until_ts"], "2026-04-08T23:00:00Z")
        self.assertTrue(control["disabled"])

    def test_normalizes_frontend_popover_control_shape(self):
        items = [{
            "id": "agent-1",
            "name": "Commonflame",
            "control": {
                "is_disabled": True,
                "disabled_reason": "Temporarily disabled by owner",
                "disabled_until": "2026-04-08T23:15:00Z",
                "disabled_by": ["agent"],
            },
        }]
        result = _enrich_with_availability(items)
        control = result[0]["control"]
        self.assertEqual(control["state"], "break")
        self.assertEqual(control["break_until_ts"], "2026-04-08T23:15:00Z")
        self.assertEqual(control["disabled_by"], ["agent"])
        self.assertTrue(control["disabled"])

    def test_user_control_surface_can_disable_current_space_agent(self):
        items = [{
            "id": "space-agent-1",
            "name": "Commonflame",
            "agent_type": "space_agent",
            "origin": "space_agent",
            "space_id": "team-space",
            "space_name": "Team Space",
            "is_own": False,
            "can_control": False,
            "can_update": False,
        }]
        permission_bundle = {
            "viewer": {"user_id": "user-1"},
            "space_context": {"id": "team-space", "name": "Team Space"},
            "permissions": {
                "mode": "user_session_controls",
                "can_control": True,
            },
        }

        result = _enrich_widget_agent_contract(items, permission_bundle)

        self.assertTrue(result[0]["can_control"])
        self.assertFalse(result[0]["can_update"])
        self.assertEqual(result[0]["relationship_to_viewer"], "space_agent")
        self.assertTrue(result[0]["action_capabilities"]["can_control"])
        self.assertTrue(result[0]["action_capabilities"]["can_disable"])
        self.assertTrue(result[0]["action_capabilities"]["can_enable"])
        self.assertTrue(result[0]["action_capabilities"]["can_reenable"])

    def test_user_control_surface_overrides_stale_denial_for_current_space_agent(self):
        items = [{
            "id": "space-agent-1",
            "name": "Commonflame",
            "agent_type": "space_agent",
            "origin": "space_agent",
            "space_id": "team-space",
            "space_name": "Team Space",
            "is_own": False,
            "can_control": False,
            "can_update": False,
            "action_capabilities": {
                "can_control": False,
                "can_disable": False,
                "can_enable": False,
                "can_reenable": False,
            },
        }]
        permission_bundle = {
            "viewer": {"user_id": "user-1"},
            "space_context": {"id": "team-space", "name": "Team Space"},
            "permissions": {
                "mode": "user_session_controls",
                "can_control": True,
            },
        }

        result = _enrich_widget_agent_contract(items, permission_bundle)

        self.assertTrue(result[0]["can_control"])
        self.assertTrue(result[0]["action_capabilities"]["can_control"])
        self.assertTrue(result[0]["action_capabilities"]["can_disable"])
        self.assertTrue(result[0]["action_capabilities"]["can_enable"])
        self.assertTrue(result[0]["action_capabilities"]["can_reenable"])

    def test_user_control_surface_denies_current_space_agent_without_permission(self):
        items = [{
            "id": "space-agent-1",
            "name": "Commonflame",
            "agent_type": "space_agent",
            "origin": "space_agent",
            "space_id": "team-space",
            "space_name": "Team Space",
            "is_own": False,
            "can_control": False,
            "can_update": False,
        }]
        permission_bundle = {
            "viewer": {"user_id": "user-1"},
            "space_context": {"id": "team-space", "name": "Team Space"},
            "permissions": {
                "mode": "user_session_controls",
                "can_control": False,
            },
        }

        result = _enrich_widget_agent_contract(items, permission_bundle)

        self.assertFalse(result[0]["can_control"])
        self.assertFalse(result[0]["action_capabilities"]["can_control"])
        self.assertFalse(result[0]["action_capabilities"].get("can_disable", False))
        self.assertFalse(result[0]["action_capabilities"].get("can_enable", False))

    def test_user_control_surface_denies_other_space_agent_even_with_permission(self):
        items = [{
            "id": "space-agent-2",
            "name": "Commonflame",
            "agent_type": "space_agent",
            "origin": "space_agent",
            "space_id": "other-space",
            "space_name": "Other Space",
            "is_own": False,
            "can_control": False,
            "can_update": False,
        }]
        permission_bundle = {
            "viewer": {"user_id": "user-1"},
            "space_context": {"id": "team-space", "name": "Team Space"},
            "permissions": {
                "mode": "user_session_controls",
                "can_control": True,
            },
        }

        result = _enrich_widget_agent_contract(items, permission_bundle)

        self.assertFalse(result[0]["can_control"])
        self.assertFalse(result[0]["action_capabilities"]["can_control"])
        self.assertFalse(result[0]["action_capabilities"].get("can_disable", False))
        self.assertFalse(result[0]["action_capabilities"].get("can_enable", False))

    def test_user_control_surface_does_not_promote_regular_agent_from_space_permission(self):
        items = [{
            "id": "regular-agent-1",
            "name": "worker",
            "agent_type": "local",
            "origin": "local",
            "space_id": "team-space",
            "space_name": "Team Space",
            "is_own": False,
            "can_control": False,
            "can_update": False,
        }]
        permission_bundle = {
            "viewer": {"user_id": "user-1"},
            "space_context": {"id": "team-space", "name": "Team Space"},
            "permissions": {
                "mode": "user_session_controls",
                "can_control": True,
            },
        }

        result = _enrich_widget_agent_contract(items, permission_bundle)

        self.assertFalse(result[0]["can_control"])
        self.assertFalse(result[0]["action_capabilities"]["can_control"])
        self.assertFalse(result[0]["action_capabilities"].get("can_disable", False))
        self.assertFalse(result[0]["action_capabilities"].get("can_enable", False))

    def test_normalizes_no_reply_control_shape_from_message_disable(self):
        items = [{
            "id": "agent-2",
            "name": "Commonflame",
            "control": {
                "no_reply": True,
                "no_reply_reason": "Paused from message thread",
                "no_reply_until": "2026-04-08T23:15:00Z",
            },
        }]
        result = _enrich_with_availability(items)
        control = result[0]["control"]
        self.assertEqual(control["state"], "break")
        self.assertEqual(control["reason"], "Paused from message thread")
        self.assertEqual(control["break_until_ts"], "2026-04-08T23:15:00Z")
        self.assertTrue(control["disabled"])
        self.assertEqual(result[0]["availability"]["reason"], "on_break")

    def test_normalizes_indefinite_no_reply_as_disabled(self):
        items = [{
            "id": "agent-3",
            "name": "Commonflame",
            "control_state": {
                "kind": "no_reply",
                "no_reply": True,
                "no_reply_reason": "Owner paused replies",
            },
        }]
        result = _enrich_with_availability(items)
        control = result[0]["control"]
        self.assertEqual(control["state"], "disabled")
        self.assertEqual(control["reason"], "Owner paused replies")
        self.assertTrue(control["disabled"])
        self.assertEqual(result[0]["availability"]["reason"], "disabled")

    def test_normalizes_timed_no_reply_as_break(self):
        items = [{
            "id": "agent-4",
            "name": "Commonflame",
            "control_state": {
                "kind": "no_reply",
                "no_reply": True,
                "no_reply_reason": "Owner paused replies",
                "no_reply_until": "2026-05-08T23:15:00Z",
            },
        }]
        result = _enrich_with_availability(items)
        control = result[0]["control"]
        self.assertEqual(control["state"], "break")
        self.assertEqual(control["reason"], "Owner paused replies")
        self.assertEqual(control["break_until_ts"], "2026-05-08T23:15:00Z")
        self.assertTrue(control["disabled"])
        self.assertEqual(result[0]["availability"]["reason"], "on_break")

    def test_normalizes_legacy_timed_no_reply_fallback_as_break(self):
        items = [{
            "id": "agent-5",
            "name": "Commonflame",
            "control_state": {
                "status": "legacy_no_reply_mode",
                "no_reply": True,
                "no_reply_reason": "Owner paused replies",
                "no_reply_until": "2026-05-08T23:15:00Z",
            },
        }]
        result = _enrich_with_availability(items)
        control = result[0]["control"]
        self.assertEqual(control["state"], "break")
        self.assertEqual(control["reason"], "Owner paused replies")
        self.assertEqual(control["break_until_ts"], "2026-05-08T23:15:00Z")
        self.assertTrue(control["disabled"])
        self.assertEqual(result[0]["availability"]["reason"], "on_break")

    def test_normalizes_legacy_timed_disabled_fallback_as_break(self):
        items = [{
            "id": "agent-6",
            "name": "Commonflame",
            "control_state": {
                "status": "legacy_disabled_mode",
                "is_disabled": True,
                "disabled_reason": "Owner paused replies",
                "until": "2026-05-08T23:15:00Z",
            },
        }]
        result = _enrich_with_availability(items)
        control = result[0]["control"]
        self.assertEqual(control["state"], "break")
        self.assertEqual(control["reason"], "Owner paused replies")
        self.assertEqual(control["break_until_ts"], "2026-05-08T23:15:00Z")
        self.assertTrue(control["disabled"])
        self.assertEqual(result[0]["availability"]["reason"], "on_break")

    def test_normalizes_top_level_no_reply_as_disabled(self):
        items = [{
            "id": "agent-7",
            "name": "Commonflame",
            "state": "unknown_state",
            "no_reply": True,
            "no_reply_reason": "Owner paused replies",
        }]
        result = _enrich_with_availability(items)
        control = result[0]["control"]
        self.assertEqual(control["state"], "disabled")
        self.assertEqual(control["reason"], "Owner paused replies")
        self.assertTrue(control["disabled"])
        self.assertEqual(result[0]["availability"]["reason"], "disabled")


class TestMessageabilityScore(unittest.TestCase):
    def test_disabled_agent_is_unavailable(self):
        score, reasons, bucket = _messageability_score({
            "control": {"disabled": True},
            "availability": {"state": "available"},
        })

        self.assertEqual(score, 0)
        self.assertEqual(reasons, ["control_disabled"])
        self.assertEqual(bucket, "unavailable")

    def test_setup_required_agent_is_not_messageable(self):
        score, reasons, bucket = _messageability_score({
            "setup": {"required": True, "reason": "missing_gateway"},
            "availability": {"state": "available"},
        })

        self.assertEqual(score, 5)
        self.assertEqual(reasons, ["missing_gateway"])
        self.assertEqual(bucket, "needs_setup")

    # AGENT-LIFECYCLE-UI
    def test_dormant_unreachable_agent_is_soft_demoted(self):
        item = {"availability": {"state": "warming", "expected_response": "immediate"}}
        base_score, base_reasons, _ = _messageability_score(dict(item))
        score, reasons, bucket = _messageability_score(
            {**item, "lifecycle_state": "dormant"}
        )
        self.assertIn("lifecycle_dormant", reasons)
        self.assertNotIn("lifecycle_dormant", base_reasons)
        self.assertLess(score, base_score)

    def test_dormant_does_not_force_hide_reachable_agent(self):
        # A still-routable / gateway-connected agent labeled dormant must stay
        # at or above the "likely" fold — shadow-mode signal is uncalibrated.
        score, reasons, bucket = _messageability_score({
            "availability": {
                "state": "available",
                "messages_routable": True,
                "gateway_connected": True,
            },
            "lifecycle_state": "dormant",
        })
        self.assertIn("lifecycle_dormant", reasons)
        self.assertGreaterEqual(score, 45)
        self.assertIn(bucket, {"send_now", "likely"})

    def test_non_dormant_lifecycle_is_noop(self):
        item = {"availability": {"state": "available", "messages_routable": True}}
        base_score, _, base_bucket = _messageability_score(dict(item))
        score, reasons, bucket = _messageability_score(
            {**item, "lifecycle_state": "active"}
        )
        self.assertEqual(score, base_score)
        self.assertEqual(bucket, base_bucket)
        self.assertNotIn("lifecycle_dormant", reasons)


class TestEnrichWithAvailabilityMap(unittest.TestCase):
    def test_uses_availability_map_when_provided(self):
        items = [{"id": "uuid-1", "name": "agent_a", "presence": "offline"}]
        avail_map = {
            "uuid-1": {
                "availability": "high",
                "sse_connected": True,
                "operational_status": "active",
                "last_message_age_seconds": 10.0,
            }
        }
        result = _enrich_with_availability(items, avail_map)
        self.assertEqual(result[0]["availability_confidence"], "high")
        self.assertTrue(result[0]["sse_connected"])
        self.assertEqual(result[0]["operational_status"], "active")

    def test_uses_agent_id_fallback_from_map(self):
        items = [{"agent_id": "uuid-4", "name": "agent_d", "presence": "offline"}]
        avail_map = {"uuid-4": {"availability_confidence": "medium", "sse_connected": False}}
        result = _enrich_with_availability(items, avail_map)
        self.assertEqual(result[0]["availability_confidence"], "medium")

    def test_offline_owned_agent_marks_setup_required(self):
        items = [{
            "id": "agent-setup",
            "name": "builder",
            "presence": "offline",
            "can_control": True,
        }]
        result = _enrich_with_availability(items)
        self.assertEqual(result[0]["availability"]["state"], "unavailable")
        self.assertEqual(result[0]["availability"]["reason"], "offline")
        self.assertEqual(result[0]["setup"]["state"], "needs_setup")
        self.assertEqual(result[0]["setup"]["reason"], "runtime_not_connected")
        self.assertTrue(result[0]["setup_required"])


class TestWidgetAgentContract(unittest.TestCase):
    def test_collapses_legacy_space_fields_to_single_space_contract(self):
        items = [{
            "id": "agent-1",
            "name": "chatgpt",
            "space_id": "personal-space",
            "space_name": "Personal Workspace",
            "default_space_id": "personal-space",
            "home_space_id": "personal-space",
            "current_space_id": "team-hub",
            "effective_space_id": "team-hub",
            "space_access": [{"space_id": "personal-space", "name": "Personal Workspace"}],
            "pinned": True,
        }]
        result = _enrich_widget_agent_contract(
            items,
            {"space_context": {"role": "member"}},
            source_space={"id": "team-hub", "name": "Team Hub"},
        )

        item = result[0]
        self.assertEqual(item["space_id"], "team-hub")
        self.assertEqual(item["space_name"], "Team Hub")
        self.assertFalse(item["is_own"])
        self.assertFalse(item["can_control"])
        self.assertFalse(item["can_update"])
        self.assertTrue(item["space_locked"])
        self.assertNotIn("default_space_id", item)
        self.assertNotIn("home_space_id", item)
        self.assertNotIn("current_space_id", item)
        self.assertNotIn("effective_space_id", item)
        self.assertEqual(
            {entry["space_id"] for entry in item["space_access"]},
            {"personal-space", "team-hub"},
        )

    def test_current_placement_beats_roster_source_space(self):
        items = [{
            "id": "agent-moved",
            "name": "moved_agent",
            "space_id": "old-space",
            "space_name": "Old Space",
            "current_space_id": "new-space",
            "current_space_name": "New Space",
            "effective_space_id": "new-space",
            "effective_space_name": "New Space",
            "space_access": [{"space_id": "old-space", "name": "Old Space"}],
        }]
        result = _enrich_widget_agent_contract(
            items,
            {"space_context": {"id": "old-space", "name": "Old Space", "role": "admin"}},
            source_space={"id": "old-space", "name": "Old Space"},
        )

        item = result[0]
        self.assertEqual(item["space_id"], "new-space")
        self.assertEqual(item["space_name"], "New Space")

    def test_roster_space_id_does_not_pair_with_unrelated_current_space_name(self):
        items = [{
            "id": "agent-old",
            "name": "old_agent",
            "space_id": "old-space",
            "current_space_name": "New Space",
        }]
        result = _enrich_widget_agent_contract(
            items,
            {"space_context": {"id": "old-space", "name": "Old Space", "role": "admin"}},
            source_space={"id": "old-space", "name": "Old Space"},
            spaces_by_id={"old-space": {"id": "old-space", "name": "Old Space"}},
        )

        item = result[0]
        self.assertEqual(item["space_id"], "old-space")
        self.assertEqual(item["space_name"], "Old Space")

    def test_merge_does_not_treat_space_locked_as_placement_field(self):
        merged = _merge_agent_items(
            [{
                "id": "agent-locked",
                "name": "locked",
                "space_id": "old-space",
                "space_locked": True,
                "_placement_rank": 0,
            }],
            [{
                "id": "agent-locked",
                "space_id": "new-space",
                "space_name": "New Space",
                "space_locked": False,
                "_placement_rank": 3,
            }],
        )

        self.assertEqual(merged[0]["space_id"], "new-space")
        self.assertEqual(merged[0]["space_name"], "New Space")
        self.assertTrue(merged[0]["space_locked"])

    def test_ownership_flags_default_to_is_own_when_backend_omits_them(self):
        items = [{
            "id": "agent-2",
            "name": "reviewer",
            "space_id": "personal-space",
            "space_name": "Personal Workspace",
            "user_id": "user-9",
            "owner_username": "madtank",
        }]
        result = _enrich_widget_agent_contract(
            items,
            {"viewer": {"user_id": "user-9"}},
        )

        item = result[0]
        self.assertTrue(item["is_own"])
        self.assertTrue(item["can_control"])
        self.assertTrue(item["can_update"])
        self.assertEqual(item["owner_username"], "madtank")

    def test_blocked_setup_state_for_unowned_offline_agent(self):
        items = _enrich_with_availability([{"id": "agent-3", "name": "helper", "presence": "offline"}])
        result = _enrich_widget_agent_contract(items, {"viewer": {"user_id": "user-9"}})

        item = result[0]
        self.assertEqual(item["setup"]["state"], "blocked")
        self.assertEqual(item["setup"]["reason"], "owner_action_required")
        self.assertTrue(item["setup_required"])


class TestFetchAvailabilityMap(unittest.IsolatedAsyncioTestCase):
    async def test_returns_map_on_success(self):
        mock_response = {
            "agents": [
                {"agent_id": "uuid-1", "name": "a", "availability": "high", "sse_connected": True},
                {"id": "uuid-2", "name": "b", "availability": "offline", "sse_connected": False},
            ],
            "count": 2,
        }
        ctx = {
            "jwt": "jwt",
            "agent_name": "agent",
            "agent_id": "id",
            "space_id": "space",
            "delegation_mode": "home_space",
            "delegated_for": {"user_id": "user-1"},
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=mock_response)) as mock_api:
            result = await _fetch_availability_map(ctx)
        self.assertEqual(len(result), 2)
        self.assertEqual(result["uuid-1"]["availability"], "high")
        self.assertFalse(result["uuid-2"]["sse_connected"])
        self.assertEqual(mock_api.await_args.kwargs["delegation_mode"], "home_space")

    async def test_returns_none_on_failure(self):
        ctx = {"jwt": "jwt", "agent_name": "agent", "agent_id": "id", "space_id": "space"}
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(side_effect=Exception("404"))):
            result = await _fetch_availability_map(ctx)
        self.assertIsNone(result)


class TestFetchVisibleSpaces(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_visible_spaces_from_v1_endpoint(self):
        ctx = {"jwt": "jwt", "space_id": "team-hub", "principal_type": "user"}
        response = {
            "spaces": [
                {"id": "personal-space", "name": "Personal"},
                {"id": "team-hub", "name": "Team Hub"},
            ]
        }
        with patch(
            "fastmcp_server.tools.agents._api_request_with_ctx",
            new=AsyncMock(return_value=response),
        ) as mock_api:
            result = await _fetch_visible_spaces(ctx)

        self.assertEqual([space["id"] for space in result], ["personal-space", "team-hub"])
        self.assertEqual(mock_api.await_args.args[:3], (ctx, "GET", "/api/v1/spaces"))

    async def test_returns_empty_when_v1_spaces_endpoint_fails(self):
        ctx = {"jwt": "jwt", "space_id": "team-hub", "principal_type": "user"}
        with patch(
            "fastmcp_server.tools.agents._api_request_with_ctx",
            new=AsyncMock(return_value={"error": "not_found", "detail": "Not Found"}),
        ) as mock_api:
            result = await _fetch_visible_spaces(ctx)

        self.assertEqual(result, [])
        self.assertEqual(mock_api.await_args.args[:3], (ctx, "GET", "/api/v1/spaces"))


class TestInferConnectionType(unittest.TestCase):
    def test_connected_presence_returns_cli(self):
        self.assertEqual(_infer_connection_type({"presence": "connected"}), "cli")

    def test_concierge_type_returns_always_on(self):
        self.assertEqual(_infer_connection_type({"agent_type": "concierge", "presence": "offline"}), "always_on")


class TestExtractAgentItems(unittest.TestCase):
    def test_extracts_from_agents_key(self):
        result = {"agents": [{"name": "a"}, {"name": "b"}], "count": 2}
        items = _extract_agent_items(result)
        self.assertEqual(len(items), 2)


class TestControlPayloadCompatibility(unittest.TestCase):
    def test_build_break_payload_matches_frontend_control_service_shape(self):
        payload = _build_control_payload("break", 5, "Temporarily disabled by owner")
        self.assertEqual(payload["scope"], "agent")
        self.assertTrue(payload["disabled"])
        self.assertEqual(payload["reason"], "Temporarily disabled by owner")
        self.assertIsNotNone(payload["disabled_until"])
        parsed = datetime.fromisoformat(payload["disabled_until"].replace("Z", "+00:00"))
        self.assertGreater(parsed, datetime.now(timezone.utc))

    def test_build_break_payload_rejects_missing_duration(self):
        with self.assertRaises(ValueError):
            _build_control_payload("break", None, "Missing duration")

    def test_build_active_payload_uses_minimal_reenable_shape(self):
        payload = _build_control_payload("active", None, None)
        self.assertEqual(payload, {
            "scope": "agent",
            "disabled": False,
            "disabled_until": None,
            "reason": None,
        })


class AgentsToolEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mcp = FastMCP("test")
        register_agents_tool(self.mcp)
        self.tool = await self.mcp.get_tool("agents")
        self.token = _token()
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
                "can_archive": False,
                "can_control": True,
                "can_write_memory": False,
                "can_use_hitl_approval": True,
            },
        }

    async def _call_tool(self, **kwargs):
        with patch(
            "fastmcp_server.tools.agents.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=self.private_permissions),
        ):
            return await self.tool.fn(token=self.token, request=self.request, **kwargs)

    async def test_list_returns_v2_envelope_with_kill_switch_state_fields(self) -> None:
        list_result = {
            "items": [
                {
                    "id": "agent-1",
                    "name": "backend_sentinel",
                    "availability": "high",
                    "presence": "connected",
                    "control": {
                        "is_disabled": True,
                        "disabled_reason": "manual pause",
                        "disabled_until": "2026-04-08T23:20:00Z",
                        "disabled_by": ["agent"],
                    },
                }
            ],
            "count": 1,
            "scope": "workspace",
        }
        availability = {"agent-1": {"agent_id": "agent-1", "availability": "high", "sse_connected": True}}
        with patch("fastmcp_server.tools.agents._parallel_fetch", new=AsyncMock(return_value=(list_result, availability))):
            result = await self._call_tool(action="list")

        structured = result.structured_content
        item = structured["data"]["items"][0]
        control = item["control"]
        self.assertEqual(set(structured.keys()), {"kind", "version", "state", "data", "actions"})
        self.assertEqual(control["state"], "break")
        self.assertFalse(control["active"])
        self.assertTrue(control["disabled"])
        self.assertEqual(control["break_until_ts"], "2026-04-08T23:20:00Z")
        self.assertEqual(item["availability"]["state"], "unavailable")
        self.assertEqual(item["availability"]["reason"], "on_break")
        self.assertEqual(item["setup"]["state"], "ready")
        self.assertFalse(item["setup_required"])

    async def test_list_ranks_default_messageable_agents_from_gateway_truth(self) -> None:
        list_result = {
            "items": [
                {"id": "offline", "name": "old_agent", "presence": "offline"},
                {"id": "gateway", "name": "backend_sentinel", "presence": "connected"},
                {"id": "queued", "name": "batch_worker", "presence": "recent"},
            ],
            "count": 3,
            "scope": "workspace",
        }
        availability = {
            "offline": {
                "agent_id": "offline",
                "availability": "offline",
                "sse_connected": False,
                "messages_routable": False,
                "expected_response": "unavailable",
            },
            "gateway": {
                "agent_id": "gateway",
                "availability": "high",
                "sse_connected": True,
                "gateway_connected": True,
                "source_of_truth": "gateway",
                "messages_routable": True,
                "expected_response": "immediate",
            },
            "queued": {
                "agent_id": "queued",
                "availability": "medium",
                "sse_connected": False,
                "messages_routable": True,
                "expected_response": "queued",
            },
        }
        with patch("fastmcp_server.tools.agents._parallel_fetch", new=AsyncMock(return_value=(list_result, availability))):
            result = await self._call_tool(action="list")

        data = result.structured_content["data"]
        items = data["items"]
        self.assertEqual(items[0]["name"], "backend_sentinel")
        self.assertEqual(items[0]["messageability"]["bucket"], "send_now")
        self.assertTrue(items[0]["messageability"]["gateway_connected"])
        self.assertTrue(items[0]["messageability"]["default_visible"])
        self.assertEqual(data["default_filter"]["id"], "most_likely_messageable")
        self.assertEqual(data["default_filter"]["count"], 1)
        self.assertIn("gateway_connected", {flt["id"] for flt in data["filters"]})
        self.assertFalse(items[-1]["messageability"]["default_visible"])

    async def test_get_returns_single_agent_with_availability_setup_contract(self) -> None:
        detail_result = {
            "agent": {
                "id": "agent-2",
                "name": "frontend_sentinel",
                "availability": "offline",
                "presence": "offline",
                "owner_user_id": "user-1",
                "owned_by_viewer": True,
                "space_id": "space-1",
            }
        }
        availability = {
            "agent-2": {
                "agent_id": "agent-2",
                "availability": "offline",
                "sse_connected": False,
                "operational_status": "inactive",
            }
        }
        spaces = [{"id": "space-1", "name": "Personal workspace"}]
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value=detail_result),
        ) as mock_api, patch(
            "fastmcp_server.tools.agents._fetch_availability_map",
            new=AsyncMock(return_value=availability),
        ), patch(
            "fastmcp_server.tools.agents._fetch_visible_spaces",
            new=AsyncMock(return_value=spaces),
        ):
            result = await self._call_tool(action="get", agent_id="agent-2")

        mock_api.assert_awaited_once()
        self.assertEqual(mock_api.await_args.args[:2], ("GET", "/api/v1/agents/agent-2"))
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "get")
        self.assertEqual(structured["data"]["count"], 1)
        item = structured["data"]["items"][0]
        self.assertEqual(item["availability"]["state"], "unavailable")
        self.assertEqual(item["setup"]["state"], "needs_setup")
        self.assertTrue(item["setup_required"])
        self.assertEqual(item["space_name"], "Personal workspace")

    async def test_get_returns_backend_error_without_empty_ready_widget(self) -> None:
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"error": "not_found", "detail": "Agent not found"}),
        ), patch(
            "fastmcp_server.tools.agents._fetch_availability_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "fastmcp_server.tools.agents._fetch_visible_spaces",
            new=AsyncMock(return_value=[]),
        ):
            result = await self._call_tool(action="get", agent_id="missing-agent")

        self.assertEqual(result, {"error": "Agent not found"})

    async def test_get_cancels_background_fetches_before_returning_error(self) -> None:
        avail_cancelled = asyncio.Event()
        spaces_cancelled = asyncio.Event()

        async def _pending_fetch(cancelled: asyncio.Event):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def _availability_fetch(*_args, **_kwargs):
            return await _pending_fetch(avail_cancelled)

        async def _spaces_fetch(*_args, **_kwargs):
            return await _pending_fetch(spaces_cancelled)

        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value={"error": "not_found", "detail": "Agent not found"}),
        ), patch(
            "fastmcp_server.tools.agents._fetch_availability_map",
            new=AsyncMock(side_effect=_availability_fetch),
        ), patch(
            "fastmcp_server.tools.agents._fetch_visible_spaces",
            new=AsyncMock(side_effect=_spaces_fetch),
        ):
            result = await self._call_tool(action="get", agent_id="missing-agent")

        self.assertEqual(result, {"error": "Agent not found"})
        self.assertTrue(avail_cancelled.is_set())
        self.assertTrue(spaces_cancelled.is_set())

    async def test_set_control_without_confirmation_returns_control_review(self) -> None:
        result = await self._call_tool(
            action="set_control",
            agent_id="agent-1",
            state="break",
            duration_minutes=5,
        )
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "control_review")
        self.assertEqual(structured["data"]["control"]["action"], "set_control")
        self.assertEqual(structured["data"]["control"]["state"], "break")
        self.assertEqual(structured["data"]["control"]["duration_minutes"], 5)

    async def test_set_control_break_confirmed_uses_shared_patch_contract(self) -> None:
        fake_response = {
            "scope": "control_applied",
            "agent": {
                "id": "agent-1",
                "name": "Commonflame",
                "control": {
                    "is_disabled": True,
                    "disabled_reason": "Temporarily disabled by owner",
                    "disabled_until": "2026-04-08T23:25:00Z",
                    "disabled_by": ["agent"],
                },
            },
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="set_control",
                agent_id="agent-1",
                state="break",
                duration_minutes=5,
                reason="Temporarily disabled by owner",
                confirmed=True,
            )

        self.assertEqual(mock_api.await_args.args[:2], ("PATCH", "/auth/agents/agent-1/control"))
        payload = mock_api.await_args.kwargs["json_data"]
        self.assertEqual(payload["scope"], "agent")
        self.assertEqual(result.structured_content["data"]["agent"]["control"]["state"], "break")

    async def test_control_is_blocked_in_shared_space(self) -> None:
        shared_permissions = {
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
        with patch(
            "fastmcp_server.tools.agents.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=shared_permissions),
        ):
            result = await self.tool.fn(
                action="set_control",
                agent_id="agent-1",
                state="break",
                duration_minutes=5,
                token=self.token,
                request=self.request,
            )

        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "control_blocked")
        self.assertEqual(structured["notice"]["code"], "agents_set_control_blocked")
        self.assertIsNone(structured["data"].get("control_state_requested"))
        self.assertEqual(
            structured["data"]["hint"],
            "Agent-authored actions require user approval. Use a user quick action to make changes.",
        )

    async def test_set_control_active_confirmed_calls_minimal_reenable_shape(self) -> None:
        fake_response = {
            "scope": "control_applied",
            "agent": {
                "id": "agent-1",
                "name": "Commonflame",
                "control": {"is_disabled": False, "disabled_by": []},
            },
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="set_control",
                target="agent-1",
                state="active",
                confirmed=True,
            )
        self.assertEqual(mock_api.await_args.kwargs["json_data"], {
            "scope": "agent",
            "disabled": False,
            "disabled_until": None,
            "reason": None,
        })
        self.assertEqual(result.structured_content["data"]["agent"]["control"]["state"], "active")

    async def test_toggle_remains_backward_compatible_alias(self) -> None:
        fake_response = {
            "scope": "control_applied",
            "agent": {
                "id": "agent-1",
                "name": "Commonflame",
                "control": {"is_disabled": True, "disabled_by": ["agent"]},
            },
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            await self._call_tool(
                action="toggle",
                agent_id="agent-1",
                state="disabled",
                confirmed=True,
            )
        self.assertEqual(mock_api.await_args.args[:2], ("PATCH", "/auth/agents/agent-1/control"))
        self.assertEqual(mock_api.await_args.kwargs["json_data"], {
            "scope": "agent",
            "disabled": True,
            "reason": None,
            "disabled_until": None,
        })

    async def test_control_applied_without_agent_still_returns_normalized_control(self) -> None:
        fake_response = {
            "is_disabled": True,
            "disabled_reason": "manual pause",
            "disabled_until": "2026-04-08T23:25:00Z",
            "disabled_by": ["agent"],
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)):
            result = await self._call_tool(
                action="disable",
                target="agent-1",
                confirmed=True,
            )

        structured = result.structured_content["data"]
        self.assertEqual(structured["scope"], "control_applied")
        self.assertEqual(structured["agent_id"], "agent-1")
        self.assertEqual(structured["control"]["state"], "break")
        self.assertTrue(structured["control"]["disabled"])

    async def test_set_placement_posts_canonical_endpoint_and_refreshes_agent(self) -> None:
        placement_response = {
            "status": "ok",
            "agent_id": "agent-1",
            "agent_name": "Commonflame",
            "space_id": "space-2",
            "pinned": True,
        }
        detail_response = {
            "agent": {
                "id": "agent-1",
                "name": "Commonflame",
                "space_id": "space-2",
                "space_name": "Team Hub",
                "space_locked": True,
            }
        }
        with patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(side_effect=[placement_response, detail_response]),
        ) as mock_api:
            result = await self._call_tool(
                action="set_placement",
                agent_id="agent-1",
                space_id="space-2",
                pinned=True,
            )

        first_call = mock_api.await_args_list[0]
        self.assertEqual(first_call.args[:2], ("POST", "/api/v1/agents/agent-1/placement"))
        self.assertEqual(first_call.kwargs["json_data"], {"space_id": "space-2", "pinned": True})
        second_call = mock_api.await_args_list[1]
        self.assertEqual(second_call.args[:2], ("GET", "/api/v1/agents/agent-1"))
        item = result.structured_content["data"]["items"][0]
        self.assertEqual(item["space_id"], "space-2")
        self.assertEqual(item["space_name"], "Team Hub")
        self.assertNotIn("default_space_id", item)
        self.assertNotIn("home_space_id", item)
        self.assertTrue(item["space_locked"])

    async def test_set_placement_requires_pinned_flag(self) -> None:
        result = await self._call_tool(
            action="set_placement",
            agent_id="agent-1",
            space_id="space-2",
        )
        self.assertEqual(result, {"error": "pinned is required for action=set_placement"})

    async def test_create_draft_posts_backend_draft_contract(self) -> None:
        fake_response = {
            "draft_id": "draft-1",
            "kind": "agents.create.sandbox",
            "status": "under_review",
            "version": 1,
            "target_space_id": "space-1",
            "editable_fields": ["agent.name", "agent.description"],
            "agent": {
                "name": "review_bot",
                "description": "Reviews changes",
                "system_prompt": "Be precise.",
            },
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="create_draft",
                name="review_bot",
                description="Reviews changes",
                system_prompt="Be precise.",
                agent_mode="sandbox",
            )

        self.assertEqual(mock_api.await_args.args[:2], ("POST", "/api/v1/drafts/agents"))
        payload = mock_api.await_args.kwargs["json_data"]
        self.assertEqual(payload["agent_mode"], "sandbox")
        self.assertEqual(payload["agent"]["name"], "review_bot")
        self.assertEqual(payload["agent"]["description"], "Reviews changes")
        structured = result.structured_content["data"]
        self.assertEqual(structured["scope"], "create")
        self.assertEqual(structured["draft"]["draft_id"], "draft-1")
        self.assertEqual(structured["draft"]["name"], "review_bot")

    async def test_create_is_not_a_draft_alias(self) -> None:
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock()) as mock_api:
            result = await self._call_tool(
                action="create",
                name="review_bot",
            )

        mock_api.assert_not_awaited()
        self.assertIn("Unknown action", result["error"])
        self.assertIn("create_draft", result["error"])

    async def test_edit_draft_patches_backend_changes(self) -> None:
        fake_response = {
            "draft_id": "draft-1",
            "kind": "agents.create.sandbox",
            "status": "under_review",
            "version": 2,
            "agent": {
                "name": "review_bot_v2",
                "description": "Reviews changes",
            },
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="edit_draft",
                draft_id="draft-1",
                version=1,
                changes={"agent.name": "review_bot_v2"},
            )

        self.assertEqual(mock_api.await_args.args[:2], ("PATCH", "/api/v1/drafts/draft-1"))
        self.assertEqual(mock_api.await_args.kwargs["json_data"], {
            "version": 1,
            "changes": {"agent.name": "review_bot_v2"},
        })
        structured = result.structured_content["data"]
        self.assertEqual(structured["scope"], "create")
        self.assertEqual(structured["draft"]["version"], 2)

    async def test_get_draft_refreshes_executed_draft(self) -> None:
        fake_response = {
            "draft_id": "draft-1",
            "kind": "agents.create.sandbox",
            "status": "executed",
            "version": 3,
            "agent": {
                "name": "review_bot",
                "description": "Reviews changes",
            },
            "execution_result": {
                "agent": {
                    "id": "agent-created",
                    "name": "review_bot",
                    "description": "Reviews changes",
                    "status": "active",
                }
            },
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="get_draft",
                draft_id="draft-1",
            )

        self.assertEqual(mock_api.await_args.args[:2], ("GET", "/api/v1/drafts/draft-1"))
        structured = result.structured_content["data"]
        self.assertEqual(structured["scope"], "created")
        self.assertEqual(structured["created"]["id"], "agent-created")

    async def test_approve_draft_returns_created_agent_scope(self) -> None:
        fake_response = {
            "draft_id": "draft-1",
            "kind": "agents.create.sandbox",
            "status": "executed",
            "version": 3,
            "agent": {
                "name": "review_bot",
                "description": "Reviews changes",
            },
            "execution_result": {
                "agent": {
                    "id": "agent-created",
                    "name": "review_bot",
                    "description": "Reviews changes",
                    "status": "active",
                }
            },
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="approve_draft",
                draft_id="draft-1",
                version=2,
            )

        self.assertEqual(mock_api.await_args.args[:2], ("POST", "/api/v1/drafts/draft-1/approve"))
        self.assertEqual(mock_api.await_args.kwargs["json_data"], {"version": 2})
        structured = result.structured_content["data"]
        self.assertEqual(structured["scope"], "created")
        self.assertEqual(structured["created"]["id"], "agent-created")

    async def test_reject_draft_returns_dismissed_scope(self) -> None:
        fake_response = {
            "draft_id": "draft-1",
            "kind": "agents.create.sandbox",
            "status": "rejected",
            "version": 2,
            "agent": {
                "name": "review_bot",
                "description": "Reviews changes",
            },
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="reject_draft",
                draft_id="draft-1",
                version=1,
            )

        self.assertEqual(mock_api.await_args.args[:2], ("POST", "/api/v1/drafts/draft-1/reject"))
        structured = result.structured_content["data"]
        self.assertEqual(structured["scope"], "draft_dismissed")

    async def test_agent_authored_create_draft_allows_hitl_in_shared_space(self) -> None:
        shared_permissions = {
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
                "can_use_hitl_approval": True,
            },
        }
        fake_response = {
            "draft_id": "draft-shared-1",
            "kind": "agents.create.sandbox",
            "status": "under_review",
            "version": 1,
            "target_space_id": "shared-space",
            "agent": {
                "name": "review_bot",
                "description": "Shared-space draft",
            },
        }
        with patch(
            "fastmcp_server.tools.agents.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=shared_permissions),
        ), patch(
            "fastmcp_server.api_client.api_request",
            new=AsyncMock(return_value=fake_response),
        ) as mock_api:
            result = await self.tool.fn(
                action="create_draft",
                name="review_bot",
                description="Shared-space draft",
                token=self.token,
                request=self.request,
            )

        self.assertEqual(mock_api.await_args.args[:2], ("POST", "/api/v1/drafts/agents"))
        payload = mock_api.await_args.kwargs["json_data"]
        self.assertEqual(payload["target_space_id"], "space-1")
        structured = result.structured_content["data"]
        self.assertEqual(structured["scope"], "create")
        self.assertEqual(structured["draft"]["draft_id"], "draft-shared-1")

    async def test_user_all_scope_uses_management_list_with_authoritative_current_space(self) -> None:
        user_permissions = {
            "space_context": {
                "id": "team-hub",
                "name": "Team Hub",
                "visibility": "private",
                "role": "member",
                "is_personal": False,
                "scope": "shared_space",
            },
            "viewer": {"user_id": "user-9"},
            "permissions": {
                "mode": "user_session_controls",
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": False,
                "can_archive": False,
                "can_control": True,
                "can_write_memory": False,
                "can_use_hitl_approval": True,
            },
        }
        management_result = {
            "agents": [
                {
                    "id": "team-agent",
                    "agent_name": "team_agent",
                    "space_id": "team-hub",
                    "current_space_name": "Team Hub",
                    "user_id": "user-10",
                },
                {
                    "id": "owned-1",
                    "agent_name": "owned_one",
                    "space_id": "personal-space",
                    "current_space_name": "Personal",
                    "user_id": "user-9",
                },
                {
                    "id": "owned-2",
                    "agent_name": "owned_two",
                    "space_id": "team-hub",
                    "current_space_name": "Team Hub",
                    "user_id": "user-9",
                },
            ],
            "total_count": 3,
            "limit": 200,
            "offset": 0,
            "has_more": False,
        }
        visible_spaces = [
            {"id": "personal-space", "name": "Personal"},
            {"id": "team-hub", "name": "Team Hub"},
        ]
        api_calls: list[tuple[str, str, dict | None]] = []

        async def api_side_effect(ctx, method, path, params=None, **_kwargs):
            api_calls.append((method, path, params))
            if path == "/auth/agents":
                return management_result
            if path == "/api/v1/agents/availability":
                return {"agents": []}
            self.fail(f"Unexpected API call: {method} {path} {params}")

        with patch(
            "fastmcp_server.tools.agents.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=user_permissions),
        ), patch(
            "fastmcp_server.tools.agents._fetch_visible_spaces",
            new=AsyncMock(return_value=visible_spaces),
        ), patch(
            "fastmcp_server.tools.agents._api_request_with_ctx",
            new=AsyncMock(side_effect=api_side_effect),
        ):
            result = await self.tool.fn(
                action="list",
                view_scope="all",
                token=SimpleNamespace(
                    token="***",
                    claims={"sub": "user-9", "space_id": "team-hub"},
                ),
                request=self.request,
            )

        data = result.structured_content["data"]
        self.assertTrue(data["cross_space_loaded"])
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["total"], 3)
        self.assertEqual(
            {item["id"] for item in data["items"]},
            {"team-agent", "owned-1", "owned-2"},
        )
        self.assertEqual(
            {item["id"]: item["space_name"] for item in data["items"]},
            {"team-agent": "Team Hub", "owned-1": "Personal", "owned-2": "Team Hub"},
        )
        self.assertIn(("GET", "/auth/agents", {"limit": 200, "offset": 0}), api_calls)

    async def test_my_scope_uses_agent_current_space_not_active_ui_space_for_badge(self) -> None:
        user_permissions = {
            "space_context": {
                "id": "madtank-space",
                "name": "madtank's Workspace",
                "visibility": "private",
                "role": "admin",
                "is_personal": True,
                "scope": "private_workspace",
            },
            "viewer": {"user_id": "user-9"},
            "permissions": {
                "mode": "user_session_controls",
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": False,
                "can_archive": False,
                "can_control": True,
                "can_write_memory": False,
                "can_use_hitl_approval": True,
            },
        }
        management_result = {
            "agents": [
                {
                    "id": "pulse-agent",
                    "agent_name": "pulse",
                    "space_id": "nexus-space",
                    "current_space_name": "The Nexus",
                    "user_id": "user-9",
                }
            ],
            "total_count": 1,
            "limit": 200,
            "offset": 0,
            "has_more": False,
        }
        visible_spaces = [
            {"id": "madtank-space", "name": "madtank's Workspace"},
            {"id": "nexus-space", "name": "The Nexus"},
        ]
        api_calls: list[tuple[str, str, dict | None]] = []

        async def api_side_effect(ctx, method, path, params=None, **_kwargs):
            api_calls.append((method, path, params))
            if path == "/auth/agents":
                return management_result
            if path == "/api/v1/agents/availability":
                return {"agents": []}
            self.fail(f"Unexpected API call: {method} {path} {params}")

        with patch(
            "fastmcp_server.tools.agents.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=user_permissions),
        ), patch(
            "fastmcp_server.tools.agents._fetch_visible_spaces",
            new=AsyncMock(return_value=visible_spaces),
        ), patch(
            "fastmcp_server.tools.agents._api_request_with_ctx",
            new=AsyncMock(side_effect=api_side_effect),
        ):
            result = await self.tool.fn(
                action="list",
                view_scope="mine",
                token=SimpleNamespace(
                    token="***",
                    claims={"sub": "user-9", "space_id": "madtank-space"},
                ),
                request=self.request,
            )

        data = result.structured_content["data"]
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["id"], "pulse-agent")
        self.assertEqual(data["items"][0]["name"], "pulse")
        self.assertEqual(data["items"][0]["space_id"], "nexus-space")
        self.assertEqual(data["items"][0]["space_name"], "The Nexus")
        self.assertNotIn("_placement_rank", data["items"][0])
        self.assertIn(("GET", "/auth/agents", {"limit": 200, "offset": 0, "owner": "me"}), api_calls)

    async def test_private_full_access_my_scope_uses_management_current_space_column(self) -> None:
        private_permissions = {
            "space_context": {
                "id": "active-space",
                "name": "Active Workspace",
                "visibility": "private",
                "role": "admin",
                "is_personal": True,
                "scope": "private_workspace",
            },
            "viewer": {"user_id": "user-9"},
            "permissions": {
                "mode": "private_full_access",
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": False,
                "can_archive": False,
                "can_control": True,
                "can_write_memory": False,
                "can_use_hitl_approval": True,
            },
        }
        management_result = {
            "agents": [
                {
                    "id": "test-uat-agent",
                    "agent_name": "test_uat",
                    "space_id": "codex-uat-space",
                    "current_space_name": "codex_uat's Workspace",
                    "user_id": "user-9",
                }
            ],
            "total_count": 1,
            "limit": 200,
            "offset": 0,
            "has_more": False,
        }
        api_calls: list[tuple[str, str, dict | None]] = []

        async def api_side_effect(ctx, method, path, params=None, **_kwargs):
            api_calls.append((method, path, params))
            if path == "/auth/agents":
                return management_result
            if path == "/api/v1/agents/availability":
                return {"agents": []}
            self.fail(f"Unexpected API call: {method} {path} {params}")

        with patch(
            "fastmcp_server.tools.agents.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=private_permissions),
        ), patch(
            "fastmcp_server.tools.agents._fetch_visible_spaces",
            new=AsyncMock(return_value=[{"id": "codex-uat-space", "name": "codex_uat's Workspace"}]),
        ), patch(
            "fastmcp_server.tools.agents._api_request_with_ctx",
            new=AsyncMock(side_effect=api_side_effect),
        ):
            result = await self.tool.fn(
                action="list",
                view_scope="mine",
                token=SimpleNamespace(
                    token="***",
                    claims={"sub": "user-9", "space_id": "active-space"},
                ),
                request=self.request,
            )

        data = result.structured_content["data"]
        self.assertTrue(data["cross_space_loaded"])
        self.assertEqual(data["items"][0]["space_id"], "codex-uat-space")
        self.assertEqual(data["items"][0]["space_name"], "codex_uat's Workspace")
        self.assertIn(("GET", "/auth/agents", {"limit": 200, "offset": 0, "owner": "me"}), api_calls)

    async def test_agent_session_all_scope_uses_management_roster(self) -> None:
        management_result = {
            "agents": [
                {
                    "id": "agent-session-visible",
                    "agent_name": "visible_to_agent_session",
                    "space_id": "space-2",
                    "current_space_name": "Team Hub",
                }
            ],
            "total_count": 1,
            "limit": 200,
            "offset": 0,
            "has_more": False,
        }
        api_calls: list[tuple[str, str, dict | None]] = []

        async def api_side_effect(ctx, method, path, params=None, **_kwargs):
            api_calls.append((method, path, params))
            if path == "/auth/agents":
                return management_result
            if path == "/api/v1/agents/availability":
                return {"agents": []}
            self.fail(f"Unexpected API call: {method} {path} {params}")

        with patch(
            "fastmcp_server.tools.agents._fetch_visible_spaces",
            new=AsyncMock(return_value=[{"id": "space-2", "name": "Team Hub"}]),
        ), patch(
            "fastmcp_server.tools.agents._api_request_with_ctx",
            new=AsyncMock(side_effect=api_side_effect),
        ):
            result = await self._call_tool(action="list", view_scope="all")

        data = result.structured_content["data"]
        self.assertTrue(data["cross_space_loaded"])
        self.assertEqual(data["items"][0]["space_id"], "space-2")
        self.assertEqual(data["items"][0]["space_name"], "Team Hub")
        self.assertIn(("GET", "/auth/agents", {"limit": 200, "offset": 0}), api_calls)

    async def test_management_scope_backend_error_returns_widget_error(self) -> None:
        async def api_side_effect(ctx, method, path, params=None, **_kwargs):
            if path == "/auth/agents":
                return {"error": "API error 403", "detail": "Forbidden"}
            if path == "/api/v1/agents/availability":
                return {"agents": []}
            self.fail(f"Unexpected API call: {method} {path} {params}")

        with patch(
            "fastmcp_server.tools.agents._fetch_visible_spaces",
            new=AsyncMock(return_value=[]),
        ), patch(
            "fastmcp_server.tools.agents._api_request_with_ctx",
            new=AsyncMock(side_effect=api_side_effect),
        ):
            result = await self._call_tool(action="list", view_scope="all")

        structured = result.structured_content
        data = structured["data"]
        self.assertEqual(structured["state"], "error")
        self.assertEqual(structured["notice"]["code"], "agents_list_error")
        self.assertEqual(data["items"], [])
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["total"], 0)

    async def test_space_scope_excludes_agents_currently_moved_elsewhere(self) -> None:
        user_permissions = {
            "space_context": {
                "id": "old-space",
                "name": "Old Space",
                "visibility": "invite_only",
                "role": "admin",
                "is_personal": False,
                "scope": "shared_space",
            },
            "viewer": {"user_id": "user-9"},
            "permissions": {
                "mode": "user_session_controls",
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": False,
                "can_archive": False,
                "can_control": True,
                "can_write_memory": False,
                "can_use_hitl_approval": True,
            },
        }
        list_result = {
            "items": [
                {
                    "id": "moved-agent",
                    "name": "moved_agent",
                    "space_id": "old-space",
                    "current_space_id": "new-space",
                    "effective_space_id": "new-space",
                    "space_access": [{"space_id": "old-space", "name": "Old Space"}],
                }
            ],
            "total": 1,
            "has_more": True,
        }

        with patch(
            "fastmcp_server.tools.agents.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=user_permissions),
        ), patch(
            "fastmcp_server.tools.agents._parallel_fetch",
            new=AsyncMock(return_value=(list_result, None)),
        ):
            result = await self.tool.fn(
                action="list",
                view_scope="space",
                token=SimpleNamespace(
                    token="***",
                    claims={"sub": "user-9", "space_id": "old-space"},
                ),
                request=self.request,
            )

        data = result.structured_content["data"]
        self.assertEqual(data["count"], 0)
        self.assertFalse(data["has_more"])
        self.assertEqual(data["items"], [])


class TestAgentsUpdateValidation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mcp = FastMCP("test")
        register_agents_tool(self.mcp)
        self.tool = await self.mcp.get_tool("agents")
        self.token = _token()
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
                "can_archive": False,
                "can_control": True,
                "can_write_memory": False,
                "can_use_hitl_approval": True,
            },
        }

    async def _call_tool(self, **kwargs):
        with patch(
            "fastmcp_server.tools.agents.resolve_space_scoped_permissions",
            new=AsyncMock(return_value=self.private_permissions),
        ):
            return await self.tool.fn(token=self.token, request=self.request, **kwargs)

    async def test_update_requires_agent_id(self):
        result = await self._call_tool(action="update", bio="New bio")
        self.assertEqual(result["error"], "agent_id is required for action=update")

    async def test_update_requires_mutable_fields(self):
        result = await self._call_tool(action="update", agent_id="agent-1")
        self.assertIn("At least one editable field is required", result["error"])

    async def test_update_patches_backend_and_returns_widget_result(self):
        fake_response = {
            "agent": {
                "id": "agent-1",
                "display_name": "Agent One",
                "bio": "Updated bio",
                "specialization": "Routing",
                "declared_capabilities": ["triage"],
            }
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="update",
                agent_id="agent-1",
                bio="Updated bio",
                specialization="Routing",
                declared_capabilities=["triage"],
            )
        self.assertEqual(mock_api.await_args.kwargs["json_data"], {
            "bio": "Updated bio",
            "specialization": "Routing",
            "capabilities": ["triage"],
        })
        self.assertEqual(mock_api.await_args.args[0], "PATCH")
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "update")
        self.assertEqual(structured["data"]["items"][0]["bio"], "Updated bio")

    async def test_update_with_status_and_tools_uses_profile_route(self):
        fake_response = {
            "id": "agent-1",
            "name": "agent_one",
            "status": "inactive",
            "enabled_tools": {"ax_mcp": True, "web_fetch": False},
        }
        with patch("fastmcp_server.api_client.api_request", new=AsyncMock(return_value=fake_response)) as mock_api:
            result = await self._call_tool(
                action="update",
                agent_id="agent-1",
                status="inactive",
                enabled_tools={"ax_mcp": True, "web_fetch": False},
            )

        self.assertEqual(mock_api.await_args.args[0], "PATCH")
        self.assertEqual(mock_api.await_args.args[1], "/api/v1/agents/agent-1")
        self.assertEqual(mock_api.await_args.kwargs["json_data"], {
            "status": "inactive",
            "enabled_tools": {"ax_mcp": True, "web_fetch": False},
        })
        structured = result.structured_content
        self.assertEqual(structured["data"]["scope"], "update")
        self.assertEqual(structured["data"]["items"][0]["status"], "inactive")


if __name__ == "__main__":
    unittest.main()
