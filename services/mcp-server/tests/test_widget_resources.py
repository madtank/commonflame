"""Regression tests for MCP widget resource compatibility.

These tests protect the standard MCP Apps contract during the URI migration:
- canonical namespaced ui:// resources stay registered
- legacy ui:// aliases remain first-class resources
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastmcp_server.mcp_ui import (
    EXPERIMENTAL_GAMES_FLAG,
    WIDGET_RESOURCE_DOMAINS,
    WIDGET_STATIC_DIR,
    get_widget_manifest,
    get_widget_specs,
    get_widget_resource_uri,
    resource_meta,
    tool_meta,
    widget_tool_result,
)
from fastmcp_server.server import create_server


class WidgetResourceCompatibilityTests(unittest.TestCase):
    """Protect both legacy and canonical widget resource URIs."""

    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(EXPERIMENTAL_GAMES_FLAG, None)
            server = create_server()
        provider = server.local_provider
        cls.components = getattr(provider, "_components", {})

    def test_whoami_registers_canonical_and_legacy_resources(self):
        self.assertIn("resource:ui://whoami/identity@", self.components)
        self.assertIn("resource:ui://agent-identity@", self.components)

    def test_core_widgets_register_canonical_and_legacy_resources(self):
        expected_pairs = [
            ("resource:ui://messages/timeline@", "resource:ui://message-timeline@"),
            ("resource:ui://tasks/board@", "resource:ui://task-board@"),
            ("resource:ui://agents/dashboard@", "resource:ui://agent-dashboard@"),
            ("resource:ui://spaces/navigator@", "resource:ui://space-navigator@"),
            ("resource:ui://search/results@", "resource:ui://search-results@"),
            ("resource:ui://context/explorer@", "resource:ui://context-explorer@"),
        ]
        for canonical, legacy in expected_pairs:
            with self.subTest(canonical=canonical, legacy=legacy):
                self.assertIn(canonical, self.components)
                self.assertIn(legacy, self.components)

    def test_context_graph_resource_registers(self):
        self.assertIn("resource:ui://context/graph@", self.components)

    def test_agent_groups_is_not_a_standalone_widget_surface(self):
        """Groups belong under the agents tool, not a separate quick-action app."""
        self.assertNotIn("agent_groups", get_widget_specs())
        self.assertNotIn("resource:ui://agent-groups/manager@", self.components)
        self.assertNotIn("agent_groups", {entry["tool_name"] for entry in get_widget_manifest()})

    def test_games_resource_is_hidden_by_default(self):
        self.assertNotIn("resource:ui://games/tic-tac-toe@", self.components)

    def test_games_resource_registers_when_flag_is_enabled(self):
        with patch.dict(os.environ, {EXPERIMENTAL_GAMES_FLAG: "true"}, clear=False):
            server = create_server()
        components = getattr(server.local_provider, "_components", {})
        self.assertIn("resource:ui://games/tic-tac-toe@", components)

    def test_task_actions_route_to_board_or_detail_resources(self):
        self.assertIn("resource:ui://tasks/detail@", self.components)
        self.assertEqual(get_widget_resource_uri("tasks", action="list"), "ui://tasks/board")
        for action in ("get", "create", "update"):
            with self.subTest(action=action):
                self.assertEqual(
                    get_widget_resource_uri("tasks", action=action),
                    "ui://tasks/detail",
                )
                result = widget_tool_result(
                    "tasks",
                    action=action,
                    content={},
                    structured_content={},
                )
                self.assertEqual(result.meta["ui"]["resourceUri"], "ui://tasks/detail")
                self.assertEqual(result.meta["ui/resourceUri"], "ui://tasks/detail")

    def test_task_widget_agent_load_failure_does_not_spin_forever(self):
        widget_html = (
            WIDGET_STATIC_DIR / "task-board.html"
        ).read_text(encoding="utf-8")
        self.assertIn("agentsLoadError", widget_html)
        self.assertIn("callServerToolWithTimeout", widget_html)
        self.assertIn("state.agentsLoaded = true;", widget_html)
        self.assertIn("Space member list did not respond in time.", widget_html)

    def test_task_widget_reminder_v1_controls_and_filters_exist(self):
        widget_html = (
            WIDGET_STATIC_DIR / "task-board.html"
        ).read_text(encoding="utf-8")
        for expected in (
            "tasks-reminder-filter-row",
            "tasks-reminder-filter-armed",
            "tasks-reminder-filter-overdue",
            "tasks-reminder-filter-snoozed",
            "tasks-assignee-filter",
            "tasks-mute-reminders",
            "tasks-unmute-reminders",
            "task-reminder-strip-complete",
            "task-reminder-strip-nudge",
            "task-reminder-strip-snooze-1h",
            "task-reminder-strip-snooze-4h",
            "task-reminder-strip-snooze-tomorrow",
            "task-reminder-strip-snooze-3d",
            "task-reminder-strip-snooze-1w",
            "task-reminder-strip-snooze-indefinite",
            "task-reminder-strip-cancel",
            "task-reminder-strip-terminal",
            "matchesReminderFilter",
            "setReminderPause",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, widget_html)

    def test_agent_dashboard_renders_profile_avatar_before_generated_glyph(self):
        widget_html = (
            WIDGET_STATIC_DIR / "agent-dashboard.html"
        ).read_text(encoding="utf-8")
        self.assertIn("function agentAvatarUrl(agent)", widget_html)
        self.assertIn("agent?.avatar_url", widget_html)
        self.assertIn("agent?.emoji || agent?.icon", widget_html)
        self.assertIn("className: \"avatar-image\"", widget_html)
        self.assertIn("avatarUrl ? \"has-image\"", widget_html)
        self.assertIn("AVATAR_RESOURCE_ORIGINS", widget_html)
        self.assertIn("AVATAR_UPLOAD_ORIGINS", widget_html)
        self.assertIn("/api/v1/uploads/files/", widget_html)
        self.assertIn("window.location.origin", widget_html)
        self.assertNotIn("paxai.app", widget_html)
        self.assertIn("https://avatars.githubusercontent.com", widget_html)
        self.assertIn("data:image/", widget_html)

    def test_agent_dashboard_avatar_resource_origins_are_csp_allowlisted(self):
        meta = resource_meta("agents")
        resource_domains = meta["openai/widgetCSP"]["resource_domains"]
        self.assertIs(resource_domains, WIDGET_RESOURCE_DOMAINS)
        self.assertIn("https://avatars.githubusercontent.com", resource_domains)
        self.assertIn("https://raw.githubusercontent.com", resource_domains)
        self.assertNotIn("https://paxai.app", resource_domains)

    def test_context_upload_fetch_only_sends_bearer_to_same_origin(self):
        widget_html = (
            WIDGET_STATIC_DIR / "context-explorer.html"
        ).read_text(encoding="utf-8")
        self.assertIn("function shouldIncludeUploadAuthorization(url)", widget_html)
        self.assertIn(
            'const token = shouldIncludeUploadAuthorization(url) ? getStoredUserToken() : "";',
            widget_html,
        )
        self.assertIn("if (target.origin === window.location.origin) return true;", widget_html)
        self.assertNotIn("paxai.app", widget_html)
        self.assertIn('target.pathname.startsWith("/api/v1/uploads/")', widget_html)
        self.assertIn("targetIsLocalUploadApi", widget_html)
        self.assertNotIn(
            'const token = isInlineUploadUrl(url) ? "" : getStoredUserToken();',
            widget_html,
        )

    def test_widget_manifest_describes_product_surfaces(self):
        manifest = get_widget_manifest()
        specs = get_widget_specs()
        by_name = {entry["name"]: entry for entry in manifest}

        self.assertEqual(set(by_name), set(specs))
        for name, entry in by_name.items():
            with self.subTest(name=name):
                self.assertIn(entry["primitive"], {"agents", "context", "identity", "messages", "search", "spaces", "tasks"})
                self.assertTrue(entry["surface"])
                self.assertTrue(entry["title"])
                self.assertTrue(entry["resource_uri"].startswith("ui://"))
                self.assertTrue((WIDGET_STATIC_DIR / entry["filename"]).is_file())
                self.assertIsInstance(entry["actions"], list)
                self.assertGreater(len(entry["actions"]), 0)

        self.assertEqual(by_name["tasks"]["primitive"], "tasks")
        self.assertEqual(by_name["tasks/detail"]["surface"], "detail")
        self.assertIn("remind", by_name["tasks"]["actions"])
        self.assertIn("complete", by_name["tasks/detail"]["actions"])
        self.assertIn("close", by_name["tasks/detail"]["actions"])

    def test_widget_registry_has_no_duplicate_resource_uris(self):
        manifest = get_widget_manifest()
        resource_uris = [entry["resource_uri"] for entry in manifest]
        self.assertEqual(
            len(resource_uris),
            len(set(resource_uris)),
            "Every product widget surface needs a unique ui:// resource URI.",
        )

    def test_core_tool_action_forms_are_published_with_canonical_names(self):
        """Expose action-specific forms under the canonical host contract keys."""
        manifest = get_widget_manifest()
        apps_by_tool = {entry["tool_name"]: entry for entry in manifest if entry.get("tool_name")}
        for tool_name in ("whoami", "messages", "tasks", "agents", "spaces", "context", "search"):
            with self.subTest(tool_name=tool_name):
                self.assertIn(tool_name, apps_by_tool)
                self.assertIn("action_forms", apps_by_tool[tool_name])
                self.assertNotIn("action_parameter_contract", apps_by_tool[tool_name])

                meta = tool_meta(tool_name)
                self.assertIn("ax/actionForms", meta)
                self.assertNotIn("ax/actionParameterContract", meta)
                self.assertIn("actions", meta["ax/actionForms"])

    def test_tasks_update_action_form_uses_canonical_fields(self):
        forms = tool_meta("tasks")["ax/actionForms"]
        update = forms["actions"]["update"]
        fields = update["parameters"]

        self.assertIn("task_id", update["required"])
        self.assertIn("reminder_cadence_minutes", fields)
        self.assertIn("next_fire_at", fields)
        self.assertNotIn("reminder_interval_minutes", fields)
        self.assertNotIn("next_reminder_at", fields)
        self.assertNotIn("cancel_reminder", fields)


if __name__ == "__main__":
    unittest.main()
