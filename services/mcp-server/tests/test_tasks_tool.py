import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from fastmcp_server.tools.tasks import register_tasks_tool
from fastmcp_server.tools import register_all_tools


class TasksToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mcp = FastMCP("test")
        register_tasks_tool(self.mcp)
        self.tool = await self.mcp.get_tool("tasks")
        self.token = SimpleNamespace(
            token="jwt",
            claims={"agent_name": "protocol", "space_id": "space-1", "agent_id": "agent-test"},
        )
        self.request = SimpleNamespace(headers={})

    async def _call_tool(self, **kwargs):
        return await self.tool.fn(
            token=self.token,
            request=self.request,
            **kwargs,
        )

    async def test_list_returns_v2_envelope_and_uses_summary(self) -> None:
        with patch(
            "fastmcp_server.tools.tasks.api_request_with_context",
            new=AsyncMock(
                return_value={
                    "tasks": [
                        {
                            "id": "task-1",
                            "title": "Urgent task",
                            "status": "open",
                            "priority": "urgent",
                            "summary": "Backend-provided summary",
                            "ai_summary": "Model-generated summary",
                            "task_ref": "task_000471",
                            "task_deep_link": "/ax/tasks/task_000471?space_id=space-1",
                            "task_reference": {
                                "copyable_ref": "task_000471",
                                "deep_link": "/ax/tasks/task_000471?space_id=space-1",
                            },
                            "task_display_id": "task_000471",
                            "space_id": "space-1",
                            "updated_at": "2026-03-21T12:00:00+00:00",
                        }
                    ],
                    "total": 1,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        structured = result.structured_content
        self.assertEqual(set(structured.keys()), {"kind", "version", "state", "data", "actions"})
        self.assertEqual(structured["kind"], "task_collection")
        item = structured["data"]["items"][0]
        self.assertEqual(item["priority"], "urgent")
        self.assertEqual(item["summary"], "Backend-provided summary")
        self.assertEqual(item["task_ref"], "task_000471")
        self.assertEqual(item["task_deep_link"], "/ax/tasks/task_000471?space_id=space-1")
        self.assertEqual(item["task_reference"]["copyable_ref"], "task_000471")
        self.assertEqual(
            item["task_reference"]["deep_link"], "/ax/tasks/task_000471?space_id=space-1"
        )
        self.assertEqual(item["task_display_id"], "task_000471")
        self.assertEqual(item["space_id"], "space-1")
        self.assertNotIn("ai_summary", item)

    async def test_agent_list_uses_context_header_without_space_query_override(self) -> None:
        with patch(
            "fastmcp_server.tools.tasks.api_request_with_context",
            new=AsyncMock(return_value={"tasks": [], "total": 0}),
        ) as api_request_with_context:
            await self._call_tool(action="list")

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["space_id"], "space-1")
        self.assertEqual(first_call.args[1:], ("GET", "/api/v1/tasks"))
        self.assertEqual(first_call.kwargs["params"], {"limit": 50, "offset": 0})
        self.assertNotIn("space_id", first_call.kwargs["params"])

    async def test_user_list_adds_explicit_space_query_override(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "frontend-client",
            "space_id": "stale-space",
            "typ": "local-user",
        }
        self.request = SimpleNamespace(
            headers={
                "x-agent-name": "Commonflame",
                "x-space-id": "current-ui-space",
            }
        )
        with (
            patch(
                "fastmcp_server.tools.tasks.api_request_with_context",
                new=AsyncMock(return_value={"tasks": [], "total": 0}),
            ) as api_request_with_context,
        ):
            await self._call_tool(action="list")

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["principal_type"], "user")
        self.assertEqual(first_call.args[0]["space_id"], "current-ui-space")
        self.assertEqual(
            first_call.kwargs["params"],
            {"limit": 50, "offset": 0, "space_id": "current-ui-space"},
        )

    async def test_detail_returns_v2_envelope(self) -> None:
        with patch(
            "fastmcp_server.tools.tasks.api_request_with_context",
            new=AsyncMock(
                return_value={
                    "id": "task-1",
                    "title": "Urgent task",
                    "status": "open",
                    "priority": "urgent",
                    "summary": "Backend-provided summary",
                    "requirements": {"deliverable": "docs/spec.md"},
                    "task_ref": "task_000471",
                    "task_deep_link": "/ax/tasks/task_000471?space_id=space-1",
                    "task_reference": {
                        "copyable_ref": "task_000471",
                        "deep_link": "/ax/tasks/task_000471?space_id=space-1",
                    },
                    "task_display_id": "task_000471",
                    "space_id": "space-1",
                    "reminder_policy": "daily_until_done",
                    "next_reminder_at": "2026-04-30T16:00:00+00:00",
                    "reminder_state": "snoozed",
                    "snoozed_until": "2099-12-31T18:00:00+00:00",
                    "last_reminded_at": "2026-04-29T16:00:00+00:00",
                    "reminder_interval_minutes": 60,
                    "reminder_max_count": 4,
                    "reminder_until_done": True,
                    "updated_at": "2026-03-21T12:00:00+00:00",
                }
            ),
        ) as api_request_with_context:
            result = await self._call_tool(action="get", task_id="task-1")

        structured = result.structured_content
        self.assertEqual(set(structured.keys()), {"kind", "version", "state", "data", "actions"})
        self.assertEqual(structured["kind"], "task_detail")
        self.assertEqual(structured["data"]["task"]["summary"], "Backend-provided summary")
        self.assertEqual(structured["data"]["task"]["requirements"]["deliverable"], "docs/spec.md")
        self.assertEqual(structured["data"]["task"]["task_ref"], "task_000471")
        self.assertEqual(
            structured["data"]["task"]["task_deep_link"],
            "/ax/tasks/task_000471?space_id=space-1",
        )
        self.assertEqual(
            structured["data"]["task"]["task_reference"]["copyable_ref"], "task_000471"
        )
        self.assertEqual(
            structured["data"]["task"]["task_reference"]["deep_link"],
            "/ax/tasks/task_000471?space_id=space-1",
        )
        self.assertEqual(structured["data"]["task"]["task_display_id"], "task_000471")
        self.assertEqual(structured["data"]["task"]["space_id"], "space-1")
        self.assertEqual(structured["data"]["task"]["reminder"]["policy"], "daily_until_done")
        self.assertEqual(structured["data"]["task"]["reminder"]["next_fire_at"], "2026-04-30T16:00:00+00:00")
        self.assertEqual(structured["data"]["task"]["reminder"]["state"], "snoozed")
        self.assertEqual(structured["data"]["task"]["reminder"]["snoozed_until"], "2099-12-31T18:00:00+00:00")
        self.assertEqual(structured["data"]["task"]["reminder"]["last_reminded_at"], "2026-04-29T16:00:00+00:00")
        self.assertEqual(structured["data"]["task"]["reminder"]["cadence_minutes"], 60)
        self.assertEqual(structured["data"]["task"]["reminder"]["max_count"], 4)
        self.assertIs(structured["data"]["task"]["reminder"]["until_done"], True)
        show_all = next(
            action for action in structured["actions"] if action["id"] == "show-all-tasks"
        )
        self.assertEqual(show_all["target"], "tasks")
        self.assertEqual(show_all["args"], {"action": "list"})
        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[1:], ("GET", "/api/v1/tasks/task-1"))

    async def test_tasks_schema_advertises_canonical_reminder_fields_only(self) -> None:
        schema = self.tool.parameters
        properties = schema["properties"]

        for canonical in ("reminder_action", "reminder_cadence_minutes", "next_fire_at"):
            self.assertIn(canonical, properties)
        for alias in ("reminder_interval_minutes", "next_reminder_at", "cancel_reminder"):
            self.assertNotIn(alias, properties)

    async def test_update_accepts_widget_reminder_schedule_contract(self) -> None:
        api = AsyncMock(return_value={"id": "task-1", "title": "Reminder", "status": "open"})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            await self._call_tool(
                action="update",
                task_id="task-1",
                reminder_action="schedule",
                reminder_cadence_minutes=1,
                reminder_max_count=5,
                reminder_until_done=False,
                next_fire_at="2026-05-02T23:30:00+00:00",
            )

        update_call = api.await_args_list[0]
        self.assertEqual(update_call.args[1:], ("PUT", "/api/v1/tasks/task-1"))
        self.assertEqual(
            update_call.kwargs["json_data"],
            {
                "reminder": {
                    "action": "schedule",
                    "cadence_minutes": 1,
                    "max_count": 5,
                    "until_done": False,
                    "next_fire_at": "2026-05-02T23:30:00+00:00",
                },
            },
        )

    async def test_update_accepts_widget_reminder_cancel_contract(self) -> None:
        api = AsyncMock(return_value={"id": "task-1", "title": "Reminder", "status": "open"})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            await self._call_tool(
                action="update",
                task_id="task-1",
                reminder_action="cancel",
            )

        update_call = api.await_args_list[0]
        self.assertEqual(update_call.args[1:], ("PUT", "/api/v1/tasks/task-1"))
        self.assertEqual(
            update_call.kwargs["json_data"],
            {
                "reminder": {"action": "cancel"},
            },
        )

    async def test_update_accepts_widget_reminder_snooze_contract(self) -> None:
        api = AsyncMock(return_value={"id": "task-1", "title": "Reminder", "status": "open"})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            await self._call_tool(
                action="update",
                task_id="task-1",
                reminder_action="snooze",
                snoozed_until="2099-12-31T23:40:00+00:00",
            )

        update_call = api.await_args_list[0]
        self.assertEqual(update_call.args[1:], ("PUT", "/api/v1/tasks/task-1"))
        self.assertEqual(
            update_call.kwargs["json_data"],
            {
                "reminder": {
                    "action": "snooze",
                    "snoozed_until": "2099-12-31T23:40:00+00:00",
                },
            },
        )

    async def test_update_accepts_widget_reminder_snoozed_until_only(self) -> None:
        api = AsyncMock(return_value={"id": "task-1", "title": "Reminder", "status": "open"})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            await self._call_tool(
                action="update",
                task_id="task-1",
                snoozed_until="2099-12-31T23:40:00+00:00",
            )

        update_call = api.await_args_list[0]
        self.assertEqual(update_call.args[1:], ("PUT", "/api/v1/tasks/task-1"))
        self.assertEqual(
            update_call.kwargs["json_data"],
            {"snoozed_until": "2099-12-31T23:40:00+00:00"},
        )

    async def test_update_keeps_schedule_and_snooze_fields_unambiguous(self) -> None:
        api = AsyncMock(return_value={"id": "task-1", "title": "Reminder", "status": "open"})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            await self._call_tool(
                action="update",
                task_id="task-1",
                reminder_action="schedule",
                reminder_cadence_minutes=10,
                snoozed_until="2099-12-31T23:40:00+00:00",
            )

        update_call = api.await_args_list[0]
        self.assertEqual(
            update_call.kwargs["json_data"],
            {
                "reminder": {"action": "schedule", "cadence_minutes": 10},
            },
        )

    async def test_update_cancel_ignores_snooze_field(self) -> None:
        api = AsyncMock(return_value={"id": "task-1", "title": "Reminder", "status": "open"})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            await self._call_tool(
                action="update",
                task_id="task-1",
                reminder_action="cancel",
                snoozed_until="2099-12-31T23:40:00+00:00",
            )

        update_call = api.await_args_list[0]
        self.assertEqual(update_call.kwargs["json_data"], {"reminder": {"action": "cancel"}})

    async def test_reminder_pause_sets_space_mute_contract(self) -> None:
        api = AsyncMock(return_value={"global": {"paused": True, "reason": "stand down"}})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            result = await self._call_tool(
                action="reminder_pause",
                reminder_paused=True,
                reminder_pause_reason="stand down",
            )

        pause_call = api.await_args_list[0]
        self.assertEqual(pause_call.args[1:], ("PUT", "/api/v1/tasks/reminders/pause"))
        self.assertEqual(
            pause_call.kwargs["json_data"],
            {"global": {"paused": True, "reason": "stand down"}},
        )
        self.assertEqual(result.structured_content["kind"], "task_reminder_pause")
        self.assertEqual(result.structured_content["data"]["global"]["paused"], True)

    async def test_reminder_pause_reads_space_mute_state(self) -> None:
        api = AsyncMock(return_value={"global": {"paused": False}})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            result = await self._call_tool(action="reminder_pause")

        read_call = api.await_args_list[0]
        self.assertEqual(read_call.args[1:], ("GET", "/api/v1/tasks/reminders/pause"))
        self.assertEqual(result.structured_content["kind"], "task_reminder_pause")

    async def test_nudge_posts_to_manual_nudge_endpoint_and_returns_detail(self) -> None:
        api = AsyncMock(
            side_effect=[
                {"success": True, "nudged": True, "task_id": "task-1", "message_id": "m-1"},
                {"id": "task-1", "title": "Reminder", "status": "open"},
            ]
        )
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            result = await self._call_tool(action="nudge", task_id="task-1")

        nudge_call = api.await_args_list[0]
        self.assertEqual(nudge_call.args[1:], ("POST", "/api/v1/tasks/task-1/nudge"))
        refresh_call = api.await_args_list[1]
        self.assertEqual(refresh_call.args[1], "GET")
        structured = result.structured_content
        self.assertEqual(structured["kind"], "task_detail")
        self.assertEqual(structured["notice"]["code"], "task_reminder_nudged")

    async def test_nudge_requires_task_id(self) -> None:
        result = await self._call_tool(action="nudge")
        self.assertEqual(result, {"error": "'task_id' required for nudge action"})

    async def test_nudge_surfaces_backend_error(self) -> None:
        api = AsyncMock(return_value={"error": "Task has no assignee to nudge"})
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            result = await self._call_tool(action="nudge", task_id="task-1")

        self.assertEqual(len(api.await_args_list), 1)
        self.assertIn("Task has no assignee to nudge", result.content[0].text)

    async def test_user_detail_adds_explicit_space_query_override(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "frontend-client",
            "space_id": "stale-space",
            "typ": "local-user",
        }
        self.request = SimpleNamespace(
            headers={
                "x-agent-name": "Commonflame",
                "x-space-id": "current-ui-space",
            }
        )
        with (
            patch(
                "fastmcp_server.tools.tasks.api_request_with_context",
                new=AsyncMock(
                    return_value={
                        "id": "task-1",
                        "title": "Urgent task",
                        "status": "open",
                        "space_id": "current-ui-space",
                    }
                ),
            ) as api_request_with_context,
        ):
            result = await self._call_tool(action="get", task_id="task-1")

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["principal_type"], "user")
        self.assertEqual(first_call.args[0]["space_id"], "current-ui-space")
        self.assertEqual(first_call.args[1:], ("GET", "/api/v1/tasks/task-1"))
        self.assertEqual(first_call.kwargs["params"], {"space_id": "current-ui-space"})
        self.assertEqual(
            result.structured_content["data"]["scope"]["space_id"],
            "current-ui-space",
        )

    async def test_detail_prefers_work_status_over_legacy_status(self) -> None:
        with patch(
            "fastmcp_server.tools.tasks.api_request_with_context",
            new=AsyncMock(
                return_value={
                    "id": "task-1",
                    "title": "Closed task",
                    "status": "open",
                    "work_status": "completed",
                    "priority": "high",
                    "updated_at": "2026-03-21T12:00:00+00:00",
                }
            ),
        ):
            result = await self._call_tool(action="get", task_id="task-1")

        task = result.structured_content["data"]["task"]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["status_label"], "Completed")

    async def test_list_prefers_work_status_over_legacy_status(self) -> None:
        with patch(
            "fastmcp_server.tools.tasks.api_request_with_context",
            new=AsyncMock(
                return_value={
                    "tasks": [
                        {
                            "id": "task-1",
                            "title": "Closed task",
                            "status": "open",
                            "work_status": "completed",
                            "updated_at": "2026-03-21T12:00:00+00:00",
                        }
                    ],
                    "total": 1,
                }
            ),
        ):
            result = await self._call_tool(action="list")

        item = result.structured_content["data"]["items"][0]
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["status_label"], "Completed")

    async def test_update_status_refresh_prefers_work_status_over_legacy_status(self) -> None:
        api = AsyncMock(
            side_effect=[
                {
                    "id": "task-1",
                    "title": "Closed task",
                    "status": "open",
                    "work_status": "completed",
                    "updated_at": "2026-03-21T12:00:00+00:00",
                },
                {
                    "id": "task-1",
                    "title": "Closed task",
                    "status": "open",
                    "work_status": "completed",
                    "updated_at": "2026-03-21T12:00:00+00:00",
                },
            ]
        )
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            result = await self._call_tool(
                action="update", task_id="task-1", status="completed"
            )

        self.assertEqual(api.await_args_list[0].args[1:], ("PUT", "/api/v1/tasks/task-1/status"))
        self.assertEqual(api.await_args_list[0].kwargs["json_data"], {"status": "completed"})
        self.assertEqual(api.await_args_list[1].args[1:], ("GET", "/api/v1/tasks/task-1"))
        task = result.structured_content["data"]["task"]
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["status_label"], "Completed")

    async def test_create_forwards_delegation_context_via_helper(self) -> None:
        self.token.claims = {
            "agent_name": "Commonflame",
            "agent_id": "agent-1",
            "space_id": "space-home",
            "delegation_mode": "home_space",
            "delegated_for": {"user_id": "user-123", "space_id": "space-home"},
        }
        with patch(
            "fastmcp_server.tools.tasks.api_request_with_context",
            new=AsyncMock(return_value={"id": "task-1", "title": "Fix warning", "status": "open"}),
        ) as api_request_with_context:
            await self._call_tool(action="create", title="Fix warning")

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["delegation_mode"], "home_space")
        self.assertEqual(first_call.args[0]["delegated_for"], {"user_id": "user-123", "space_id": "space-home"})
        self.assertEqual(first_call.args[1:], ("POST", "/api/v1/tasks"))

    async def test_user_create_adds_explicit_space_body_override(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "frontend-client",
            "space_id": "stale-space",
            "typ": "local-user",
        }
        self.request = SimpleNamespace(
            headers={
                "x-agent-name": "Commonflame",
                "x-space-id": "current-ui-space",
            }
        )
        with (
            patch(
                "fastmcp_server.tools.tasks.api_request_with_context",
                new=AsyncMock(return_value={"id": "task-1", "title": "Fix warning", "status": "open"}),
            ) as api_request_with_context,
        ):
            await self._call_tool(action="create", title="Fix warning")

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["principal_type"], "user")
        self.assertEqual(first_call.args[0]["space_id"], "current-ui-space")
        self.assertEqual(
            first_call.kwargs["json_data"],
            {"title": "Fix warning", "space_id": "current-ui-space"},
        )

    async def test_user_list_honors_explicit_requested_space_id(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "frontend-client",
            "space_id": "stale-space",
            "typ": "local-user",
        }
        self.request = SimpleNamespace(
            headers={
                "x-agent-name": "Commonflame",
                "x-space-id": "current-ui-space",
            }
        )
        with (
            patch(
                "fastmcp_server.tools.tasks.api_request_with_context",
                new=AsyncMock(return_value={"tasks": [], "total": 0}),
            ) as api_request_with_context,
        ):
            result = await self._call_tool(action="list", space_id="requested-space")

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["principal_type"], "user")
        self.assertEqual(first_call.args[0]["space_id"], "requested-space")
        self.assertEqual(
            first_call.kwargs["params"],
            {"limit": 50, "offset": 0, "space_id": "requested-space"},
        )
        self.assertEqual(
            result.structured_content["data"]["scope"]["space_id"],
            "requested-space",
        )

    async def test_user_create_honors_explicit_requested_space_id(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "frontend-client",
            "space_id": "stale-space",
            "typ": "local-user",
        }
        self.request = SimpleNamespace(
            headers={
                "x-agent-name": "Commonflame",
                "x-space-id": "current-ui-space",
            }
        )
        with (
            patch(
                "fastmcp_server.tools.tasks.api_request_with_context",
                new=AsyncMock(return_value={"id": "task-1", "title": "Fix warning", "status": "open"}),
            ) as api_request_with_context,
        ):
            result = await self._call_tool(
                action="create", title="Fix warning", space_id="requested-space"
            )

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["principal_type"], "user")
        self.assertEqual(first_call.args[0]["space_id"], "requested-space")
        self.assertEqual(
            first_call.kwargs["json_data"],
            {"title": "Fix warning", "space_id": "requested-space"},
        )
        self.assertEqual(
            result.structured_content["data"]["scope"]["space_id"],
            "requested-space",
        )

    async def test_create_accepts_typed_user_assignee_fields(self) -> None:
        with patch(
            "fastmcp_server.tools.tasks.api_request_with_context",
            new=AsyncMock(
                return_value={
                    "id": "task-1",
                    "title": "Fix assignment",
                    "status": "open",
                    "assignee_type": "user",
                    "assignee_id": "user-1",
                }
            ),
        ) as api_request_with_context:
            result = await self._call_tool(
                action="create",
                title="Fix assignment",
                assignee_type="user",
                assignee_id="user-1",
            )

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[1:], ("POST", "/api/v1/tasks"))
        self.assertEqual(
            first_call.kwargs["json_data"],
            {
                "title": "Fix assignment",
                "assignee_type": "user",
                "assignee_id": "user-1",
            },
        )
        self.assertEqual(result.structured_content["data"]["task"]["assignee"]["type"], "user")
        self.assertEqual(result.structured_content["data"]["task"]["assignee"]["id"], "user-1")

    async def test_agent_requested_space_id_cannot_override_claim_bound_space(self) -> None:
        with patch(
            "fastmcp_server.tools.tasks.api_request_with_context",
            new=AsyncMock(return_value={"tasks": [], "total": 0}),
        ) as api_request_with_context:
            await self._call_tool(action="list", space_id="spoofed-space")

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["principal_type"], "agent")
        self.assertEqual(first_call.args[0]["space_id"], "space-1")
        self.assertEqual(first_call.kwargs["params"], {"limit": 50, "offset": 0})


    async def test_update_status_uses_explicit_status_endpoint_and_verifies_refresh(self) -> None:
        api = AsyncMock(
            side_effect=[
                {"id": "task-1", "title": "Fix warning", "status": "open", "priority": "urgent"},
                {"id": "task-1", "title": "Fix warning", "status": "completed", "priority": "urgent"},
                {"id": "task-1", "title": "Fix warning", "status": "completed", "priority": "urgent"},
            ]
        )
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            result = await self._call_tool(
                action="update",
                task_id="task-1",
                priority="urgent",
                status="completed",
            )

        calls = api.await_args_list
        self.assertEqual(calls[0].args[1:], ("PUT", "/api/v1/tasks/task-1"))
        self.assertEqual(calls[0].kwargs["json_data"], {"priority": "urgent"})
        self.assertEqual(calls[1].args[1:], ("PUT", "/api/v1/tasks/task-1/status"))
        self.assertEqual(calls[1].kwargs["json_data"], {"status": "completed"})
        self.assertEqual(calls[2].args[1:], ("GET", "/api/v1/tasks/task-1"))
        self.assertEqual(result.structured_content["notice"]["code"], "task_updated")
        self.assertEqual(result.structured_content["data"]["task"]["status"], "completed")

    async def test_update_accepts_typed_assignee_fields(self) -> None:
        api = AsyncMock(
            side_effect=[
                {
                    "id": "task-1",
                    "title": "Fix warning",
                    "status": "open",
                    "assignee_type": "user",
                    "assignee_id": "user-1",
                },
                {
                    "id": "task-1",
                    "title": "Fix warning",
                    "status": "open",
                    "assignee_type": "user",
                    "assignee_id": "user-1",
                },
            ]
        )
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            result = await self._call_tool(
                action="update",
                task_id="task-1",
                assignee_type="user",
                assignee_id="user-1",
            )

        self.assertEqual(api.await_args_list[0].args[1:], ("PUT", "/api/v1/tasks/task-1"))
        self.assertEqual(
            api.await_args_list[0].kwargs["json_data"],
            {"assignee_type": "user", "assignee_id": "user-1"},
        )
        self.assertEqual(api.await_args_list[1].args[1:], ("GET", "/api/v1/tasks/task-1"))
        self.assertEqual(result.structured_content["data"]["task"]["assignee"]["type"], "user")
        self.assertEqual(result.structured_content["data"]["task"]["assignee"]["id"], "user-1")

    async def test_update_status_fails_clearly_when_backend_returns_stale_status(self) -> None:
        api = AsyncMock(
            side_effect=[
                {"id": "task-1", "title": "Fix warning", "status": "open", "priority": "urgent"},
                {"id": "task-1", "title": "Fix warning", "status": "open", "priority": "urgent"},
            ]
        )
        with patch("fastmcp_server.tools.tasks.api_request_with_context", new=api):
            result = await self._call_tool(
                action="update",
                task_id="task-1",
                priority="urgent",
                status="completed",
            )

        self.assertEqual(api.await_count, 2)
        self.assertEqual(result.structured_content["code"], "task_status_not_persisted")
        self.assertEqual(result.structured_content["requested_status"], "completed")
        self.assertEqual(result.structured_content["actual_status"], "open")
        self.assertIn("did not persist", result.content[0].text)

    async def test_user_update_honors_explicit_requested_space_header(self) -> None:
        self.token.claims = {
            "sub": "user-1",
            "client_id": "frontend-client",
            "space_id": "stale-space",
            "typ": "local-user",
        }
        self.request = SimpleNamespace(
            headers={
                "x-agent-name": "Commonflame",
                "x-space-id": "current-ui-space",
            }
        )
        with (
            patch(
                "fastmcp_server.tools.tasks.api_request_with_context",
                new=AsyncMock(return_value={"id": "task-1", "title": "Fix warning", "status": "open"}),
            ) as api_request_with_context,
        ):
            await self._call_tool(
                action="update",
                task_id="task-1",
                priority="high",
                space_id="requested-space",
            )

        first_call = api_request_with_context.await_args_list[0]
        self.assertEqual(first_call.args[0]["principal_type"], "user")
        self.assertEqual(first_call.args[0]["space_id"], "requested-space")
        self.assertEqual(first_call.args[1:], ("PUT", "/api/v1/tasks/task-1"))
        refresh_call = api_request_with_context.await_args_list[1]
        self.assertEqual(refresh_call.args[1:], ("GET", "/api/v1/tasks/task-1"))
        self.assertEqual(refresh_call.kwargs["params"], {"space_id": "requested-space"})



class ToolRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_docs_tool_not_registered_in_main_server(self) -> None:
        mcp = FastMCP("test")
        register_all_tools(mcp)
        tool_names = [tool.name for tool in await mcp.list_tools(run_middleware=False)]
        self.assertNotIn("docs", tool_names)
        self.assertEqual(
            sorted(tool_names),
            sorted(["whoami", "messages", "tasks", "agents", "spaces", "context", "search"]),
        )
        # CTX-ARTIFACTS-002: catalog folded into context, no longer a separate tool.
        self.assertNotIn("context_catalog", tool_names)

    async def test_games_tool_registers_when_experimental_flag_enabled(self) -> None:
        mcp = FastMCP("test")
        with patch.dict("os.environ", {"AX_ENABLE_EXPERIMENTAL_GAMES": "true"}, clear=False):
            register_all_tools(mcp)
        tool_names = [tool.name for tool in await mcp.list_tools(run_middleware=False)]
        self.assertEqual(
            sorted(tool_names),
            sorted(["whoami", "messages", "tasks", "agents", "spaces", "context", "search", "games"]),
        )


if __name__ == "__main__":
    unittest.main()
