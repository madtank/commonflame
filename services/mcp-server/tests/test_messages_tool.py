import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastmcp import FastMCP

from fastmcp_server.tools.messages import register_messages_tool


class DummyProgress:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def set_message(self, message: str) -> None:
        self.messages.append(message)


class MessagesToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.mcp = FastMCP("test")
        register_messages_tool(self.mcp)
        self.tool = await self.mcp.get_tool("messages")
        self.token = SimpleNamespace(
            token="jwt",
            claims={"agent_name": "sender", "space_id": "space-1"},
        )
        self.request = SimpleNamespace(headers={})
        self.progress = DummyProgress()

    async def _call_tool(self, **kwargs):
        return await self.tool.fn(
            token=self.token,
            request=self.request,
            progress=self.progress,
            **kwargs,
        )

    async def test_schema_marks_action_required_with_enum(self) -> None:
        self.assertIn("action", self.tool.parameters["required"])
        self.assertEqual(
            self.tool.parameters["properties"]["action"]["enum"],
            ["check", "send", "ask_ax", "draft", "react", "edit", "delete"],
        )

    async def test_messages_task_poll_interval_matches_reply_polling_cadence(self) -> None:
        self.assertEqual(self.tool.task_config.poll_interval, timedelta(seconds=2))

    async def test_send_payload_contains_raw_content(self) -> None:
        """Backend router handles Waystation routing — MCP sends raw content."""
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "message": {
                        "id": "msg-1",
                        "content": "@pong_agent PING 123",
                        "created_at": "2026-03-12T18:00:00+00:00",
                    }
                }
            ),
        ) as api_request:
            result = await self._call_tool(
                action="send",
                content="@pong_agent PING 123",
                wait=False,
            )

        payload = api_request.await_args.kwargs["json_data"]
        self.assertEqual(payload["content"], "@pong_agent PING 123")
        self.assertEqual(result.structured_content["data"]["status"], "sent")
        self.assertEqual(result.structured_content["data"]["delivery"]["state"], "confirmed")
        self.assertEqual(result.structured_content["data"]["delivery"]["evidence"], "message_receipt")
        self.assertTrue(result.meta["ax_supervision"])

    async def test_send_notifies_mentioned_inbox_subscribers(self) -> None:
        notify = AsyncMock(return_value=1)
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "message": {
                        "id": "msg-notify-1",
                        "content": "@pong_agent PING 123",
                        "created_at": "2026-03-12T18:00:00+00:00",
                    }
                }
            ),
        ), patch(
            "fastmcp_server.tools.messages.notify_message_inboxes",
            new=notify,
        ):
            await self._call_tool(
                action="send",
                content="@pong_agent PING 123",
                wait=False,
            )

        notify.assert_awaited_once()
        args, kwargs = notify.await_args
        self.assertEqual(args[0]["content"], "@pong_agent PING 123")
        self.assertEqual(kwargs["fallback_content"], "@pong_agent PING 123")

    async def test_send_notifies_from_backend_returned_content(self) -> None:
        notify = AsyncMock(return_value=1)
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "message": {
                        "id": "msg-notify-2",
                        "content": "@canonical_agent PING 123",
                        "created_at": "2026-03-12T18:00:00+00:00",
                    }
                }
            ),
        ), patch(
            "fastmcp_server.tools.messages.notify_message_inboxes",
            new=notify,
        ):
            await self._call_tool(
                action="send",
                content="@display_agent PING 123",
                wait=False,
            )

        notify.assert_awaited_once()
        args, kwargs = notify.await_args
        self.assertEqual(args[0]["content"], "@canonical_agent PING 123")
        self.assertEqual(kwargs["fallback_content"], "@canonical_agent PING 123")

    async def test_send_notifies_from_backend_returned_text(self) -> None:
        notify = AsyncMock(return_value=1)
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "message": {
                        "id": "msg-notify-3",
                        "text": "@text_agent PING 123",
                        "created_at": "2026-03-12T18:00:00+00:00",
                    }
                }
            ),
        ), patch(
            "fastmcp_server.tools.messages.notify_message_inboxes",
            new=notify,
        ):
            await self._call_tool(
                action="send",
                content="@display_agent PING 123",
                wait=False,
            )

        notify.assert_awaited_once()
        args, kwargs = notify.await_args
        self.assertEqual(args[0]["text"], "@text_agent PING 123")
        self.assertEqual(kwargs["fallback_content"], "@text_agent PING 123")

    async def test_send_notifies_with_empty_backend_content_as_canonical(self) -> None:
        notify = AsyncMock(return_value=0)
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "message": {
                        "id": "msg-notify-empty",
                        "content": "",
                        "text": "@text_agent PING 123",
                        "created_at": "2026-03-12T18:00:00+00:00",
                    }
                }
            ),
        ), patch(
            "fastmcp_server.tools.messages.notify_message_inboxes",
            new=notify,
        ):
            await self._call_tool(
                action="send",
                content="@raw_agent PING 123",
                wait=False,
            )

        notify.assert_awaited_once()
        args, kwargs = notify.await_args
        self.assertEqual(args[0]["content"], "")
        self.assertEqual(kwargs["fallback_content"], "")

    async def test_send_includes_sanitized_inbox_summary_metadata(self) -> None:
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "message": {
                        "id": "msg-4",
                        "content": "@pong_agent PING 456",
                        "created_at": "2026-03-12T18:05:00+00:00",
                    },
                    "inbox_summary": {
                        "unread_count": "3",
                        "has_pending_mentions": "true",
                        "pending_mentions_count": 2,
                        "oldest_unread_at": "2026-03-12T18:00:00+00:00",
                        "latest_unread_at": "2026-03-12T18:04:00+00:00",
                        "messages": [
                            {"content": "secret unread inbox message"},
                        ],
                        "preview": "secret unread inbox message",
                        "addressed_to_others": "7",
                        "addressed_to_me": [
                            {
                                "message_id": "direct-1",
                                "sender": {"name": "orion", "display_name": "Orion"},
                                "summary": "Needs review of the migration plan",
                                "kind": "mention",
                                "reason": "direct_mention",
                                "content": "FULL BODY token=super-secret must not leak",
                            },
                        ]
                        + [
                            {
                                "id": f"direct-{index}",
                                "sender_name": "relay",
                                "summary": f"Follow-up {index}",
                                "kind": "reply",
                                "reason": "thread_reply",
                            }
                            for index in range(2, 8)
                        ],
                    },
                }
            ),
        ):
            result = await self._call_tool(
                action="send",
                content="@pong_agent PING 456",
                wait=False,
            )

        data = result.structured_content["data"]
        self.assertEqual(data["unread_count"], 3)
        self.assertEqual(
            data["inbox_summary"],
            {
                "unread_count": 3,
                "has_pending_mentions": True,
                "oldest_unread_at": "2026-03-12T18:00:00+00:00",
                "addressed_to_me": [
                    {
                        "message_id": "direct-1",
                        "sender": "orion",
                        "display_name": "Orion",
                        "summary": "Needs review of the migration plan",
                        "kind": "mention",
                        "reason": "direct_mention",
                    },
                    {
                        "message_id": "direct-2",
                        "sender": "relay",
                        "display_name": "relay",
                        "summary": "Follow-up 2",
                        "kind": "reply",
                        "reason": "thread_reply",
                    },
                    {
                        "message_id": "direct-3",
                        "sender": "relay",
                        "display_name": "relay",
                        "summary": "Follow-up 3",
                        "kind": "reply",
                        "reason": "thread_reply",
                    },
                    {
                        "message_id": "direct-4",
                        "sender": "relay",
                        "display_name": "relay",
                        "summary": "Follow-up 4",
                        "kind": "reply",
                        "reason": "thread_reply",
                    },
                    {
                        "message_id": "direct-5",
                        "sender": "relay",
                        "display_name": "relay",
                        "summary": "Follow-up 5",
                        "kind": "reply",
                        "reason": "thread_reply",
                    },
                ],
                "addressed_to_others": 7,
                "pending_mentions_count": 2,
                "latest_unread_at": "2026-03-12T18:04:00+00:00",
            },
        )
        self.assertNotIn("messages", data["inbox_summary"])
        self.assertNotIn("preview", data["inbox_summary"])
        self.assertLessEqual(len(data["inbox_summary"]["addressed_to_me"]), 5)
        self.assertNotIn("content", data["inbox_summary"]["addressed_to_me"][0])
        self.assertNotIn(
            "secret", str(data["inbox_summary"]["addressed_to_me"]).lower()
        )

    async def test_send_rejects_scoped_agent_without_space_id(self) -> None:
        self.token.claims = {"agent_name": "sender", "tools_allowed": ["messages"]}
        result = await self._call_tool(
            action="send",
            content="@pong_agent PING 123",
            bypass=True,
            wait=False,
        )

        self.assertIn("missing space_id", result.structured_content["data"]["error"])

    async def test_send_bypass_preserves_direct_message_content(self) -> None:
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "message": {
                        "id": "msg-2",
                        "content": "@pong_agent direct ping",
                        "created_at": "2026-03-12T18:01:00+00:00",
                    }
                }
            ),
        ) as api_request:
            result = await self._call_tool(
                action="send",
                content="@pong_agent direct ping",
                bypass=True,
                wait=False,
            )

        self.assertEqual(
            api_request.await_args.kwargs["json_data"],
            {"content": "@pong_agent direct ping"},
        )
        self.assertEqual(result.structured_content["data"]["status"], "sent")
        self.assertEqual(
            result.structured_content["data"]["messages"][0]["content"],
            "@pong_agent direct ping",
        )

    async def test_send_transport_ack_without_receipt_stays_unconfirmed(self) -> None:
        """Transport ack alone must not be rendered as confirmed listener delivery."""
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={"status": "ok", "code": 200},
            ),
        ):
            result = await self._call_tool(
                action="send",
                content="@pong_agent direct ping",
                wait=False,
            )

        data = result.structured_content["data"]
        self.assertEqual(data["status"], "accepted_unconfirmed")
        self.assertIsNone(data["sent"])
        self.assertEqual(data["outbound"]["content"], "@pong_agent direct ping")
        self.assertEqual(data["delivery"]["state"], "unconfirmed")
        self.assertEqual(data["delivery"]["evidence"], "transport_ack_only")
        self.assertTrue(data["delivery"]["can_escalate"])

    async def test_send_waits_for_reply_without_bypass(self) -> None:
        api = AsyncMock(
            return_value={
                "message": {
                    "id": "msg-wait-1",
                    "content": "@pong_agent wait for me",
                    "created_at": "2026-03-12T18:01:00+00:00",
                    "conversation_id": "msg-wait-1",
                }
            }
        )
        wait = AsyncMock(
            return_value={
                "reply": {
                    "id": "reply-1",
                    "content": "I am here",
                    "created_at": "2026-03-12T18:01:08+00:00",
                    "parent_id": "msg-wait-1",
                    "conversation_id": "msg-wait-1",
                    "sender_name": "pong_agent",
                },
                "status": "reply_received",
                "reply_ms": 8123,
                "reply_match": "direct_reply",
            }
        )

        with patch("fastmcp_server.tools.messages.api_request", new=api), patch(
            "fastmcp_server.tools.messages.wait_for_reply",
            new=wait,
        ):
            result = await self._call_tool(
                action="send",
                content="@pong_agent wait for me",
                wait=True,
            )

        wait.assert_awaited_once()
        self.assertEqual(wait.await_args.kwargs["max_wait"], 60)
        data = result.structured_content["data"]
        self.assertEqual(data["status"], "reply_received")
        self.assertEqual(data["reply"]["content"], "I am here")
        self.assertEqual(data["reply_wait"]["state"], "reply_received")
        self.assertEqual(data["reply_wait"]["max_wait_seconds"], 60)
        self.assertEqual(data["delivery"]["state"], "working")
        self.assertIn("waiting up to 60s", self.progress.messages[-1])

    async def test_send_wait_timeout_is_explicit_without_bypass(self) -> None:
        api = AsyncMock(
            return_value={
                "message": {
                    "id": "msg-wait-2",
                    "content": "@pong_agent wait for me",
                    "created_at": "2026-03-12T18:01:00+00:00",
                    "conversation_id": "msg-wait-2",
                }
            }
        )
        wait = AsyncMock(
            return_value={
                "reply": None,
                "status": "timeout",
                "waited_seconds": 12,
            }
        )

        with patch("fastmcp_server.tools.messages.api_request", new=api), patch(
            "fastmcp_server.tools.messages.wait_for_reply",
            new=wait,
        ):
            result = await self._call_tool(
                action="send",
                content="@pong_agent wait for me",
                wait=True,
                max_wait=12,
            )

        wait.assert_awaited_once()
        data = result.structured_content["data"]
        self.assertEqual(data["status"], "timed_out")
        self.assertEqual(data["waited_seconds"], 12)
        self.assertEqual(data["reply_wait"]["state"], "timed_out")
        self.assertEqual(data["reply_wait"]["max_wait_seconds"], 12)
        self.assertIn("No reply yet after 12 seconds", data["hint"])
        self.assertEqual(
            result.structured_content["notice"]["code"],
            "message_reply_wait_timed_out",
        )

    async def test_send_wait_without_reply_always_reports_timed_out(self) -> None:
        api = AsyncMock(
            return_value={
                "message": {
                    "id": "msg-wait-unknown",
                    "content": "@pong_agent wait for me",
                    "created_at": "2026-03-12T18:01:00+00:00",
                    "conversation_id": "msg-wait-unknown",
                }
            }
        )
        wait = AsyncMock(
            return_value={
                "reply": None,
                "status": "no_reply",
                "waited_seconds": 9,
            }
        )

        with patch("fastmcp_server.tools.messages.api_request", new=api), patch(
            "fastmcp_server.tools.messages.wait_for_reply",
            new=wait,
        ):
            result = await self._call_tool(
                action="send",
                content="@pong_agent wait for me",
                wait=True,
                max_wait=9,
            )

        data = result.structured_content["data"]
        self.assertEqual(data["status"], "timed_out")
        self.assertEqual(data["reply_wait"]["state"], "timed_out")
        self.assertEqual(data["delivery"]["state"], "confirmed")
        self.assertIn("No reply yet after 9 seconds", data["hint"])

    async def test_send_maps_reply_to_to_parent_id(self) -> None:
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "message": {
                        "id": "msg-3",
                        "content": "Reply body",
                        "created_at": "2026-03-12T18:02:00+00:00",
                        "parent_id": "root-123",
                        "conversation_id": "root-123",
                    }
                }
            ),
        ) as api_request:
            result = await self._call_tool(
                action="send",
                content="Reply body",
                reply_to="root-123",
                wait=False,
            )

        self.assertEqual(
            api_request.await_args.kwargs["json_data"]["parent_id"],
            "root-123",
        )
        self.assertNotIn("reply_to", api_request.await_args.kwargs["json_data"])
        self.assertEqual(
            result.structured_content["data"]["thread"]["thread_root_id"],
            "root-123",
        )

    async def test_check_defaults_to_widget_refresh_when_no_reason(self) -> None:
        """Check without reason defaults to 'widget refresh' instead of error."""
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "messages": [],
                    "count": 0,
                    "unread_count": 0,
                },
            ),
        ):
            result = await self._call_tool(action="check", curate=False)

        self.assertEqual(result.structured_content["data"]["reason"], "widget refresh")

    async def test_awareness_deduplicates_repeated_looking_for_summaries(self) -> None:
        with patch(
            "fastmcp_server.tools.messages.api_request",
            new=AsyncMock(
                return_value={
                    "messages": [
                        {
                            "id": "msg-1",
                            "content": "@aX looking for OAuth wiring help",
                            "created_at": "2026-03-12T18:03:00+00:00",
                            "sender": {"name": "relay"},
                        },
                        {
                            "id": "msg-2",
                            "content": "@aX looking for OAuth wiring help",
                            "created_at": "2026-03-12T18:04:00+00:00",
                            "sender": {"name": "relay"},
                        },
                    ],
                    "count": 2,
                    "unread_count": 2,
                }
            ),
        ):
            result = await self._call_tool(action="check", curate=False)

        awareness = result.structured_content["data"]["awareness"]
        self.assertEqual(len(awareness["looking_for"]), 1)
        self.assertEqual(awareness["looking_for"][0]["summary"], "OAuth wiring help")
        self.assertEqual(awareness["headline"], "Looking for OAuth wiring help")

    async def test_check_default_curate_false_returns_no_briefing(self) -> None:
        api = AsyncMock(
            return_value={
                "messages": [],
                "count": 0,
                "unread_count": 0,
            },
        )
        with patch("fastmcp_server.tools.messages.api_request", new=api), patch(
            "fastmcp_server.tools.messages.wait_for_reply",
            new=AsyncMock(),
        ) as wait_for_reply:
            result = await self._call_tool(
                action="check",
                reason="routine check",
            )

        api.assert_awaited_once()
        wait_for_reply.assert_not_awaited()
        self.assertIsNone(result.structured_content["data"].get("briefing"))

    async def test_check_private_briefing_includes_reason_without_posting_message(self) -> None:
        api = AsyncMock(
            return_value={
                "messages": [
                    {
                        "id": "msg-inbox-1",
                        "content": "@stack_forge_482 I am working on auth cleanup",
                        "created_at": "2026-03-12T18:03:00+00:00",
                    }
                ],
                "count": 1,
                "unread_count": 1,
            },
        )
        with patch("fastmcp_server.tools.messages.api_request", new=api), patch(
            "fastmcp_server.tools.messages.wait_for_reply",
            new=AsyncMock(),
        ) as wait_for_reply:
            result = await self._call_tool(
                action="check",
                reason=(
                    "I am working on OAuth token persistence, blocked on auth "
                    "wiring, and looking for help or related assignments."
                ),
                curate=True,
            )

        api.assert_awaited_once()
        self.assertEqual(api.await_args.args[0], "GET")
        self.assertEqual(api.await_args.args[1], "/api/v1/messages")
        wait_for_reply.assert_not_awaited()
        briefing = result.structured_content["data"]["briefing"]
        self.assertEqual(briefing["status"], "private_briefing")
        self.assertTrue(briefing["private"])
        self.assertIn("blocked on auth wiring", briefing["content"])
        self.assertIn("looking for help", briefing["content"])
        self.assertNotIn("note", briefing)
        self.assertEqual(
            result.structured_content["data"]["reason"],
            (
                "I am working on OAuth token persistence, blocked on auth "
                "wiring, and looking for help or related assignments."
            ),
        )

    async def test_user_principal_check_curate_does_not_post_briefing_request(self) -> None:
        self.token.claims = {
            "user_id": "user-1",
            "username": "madtank",
            "space_id": "space-1",
        }
        api = AsyncMock(
            return_value={
                "messages": [],
                "count": 0,
                "unread_count": 0,
            }
        )
        with patch("fastmcp_server.tools.messages.api_request", new=api), patch(
            "fastmcp_server.tools.messages.wait_for_reply",
            new=AsyncMock(),
        ) as wait_for_reply:
            result = await self._call_tool(
                action="check",
                reason="Review production attribution issue",
                curate=True,
            )

        api.assert_awaited_once()
        wait_for_reply.assert_not_awaited()
        briefing = result.structured_content["data"]["briefing"]
        self.assertEqual(briefing["status"], "private_briefing")
        self.assertTrue(briefing["private"])
        self.assertIn("Review production attribution issue", briefing["content"])
        self.assertNotIn("note", briefing)

    async def test_agent_id_only_check_curate_uses_agent_id_fallback_label_privately(self) -> None:
        self.token.claims = {
            "agent_id": "agent-123",
            "space_id": "space-1",
        }
        api = AsyncMock(
            return_value={
                "messages": [],
                "count": 0,
                "unread_count": 0,
            },
        )
        with patch("fastmcp_server.tools.messages.api_request", new=api), patch(
            "fastmcp_server.tools.messages.wait_for_reply",
            new=AsyncMock(),
        ) as wait_for_reply:
            result = await self._call_tool(
                action="check",
                reason="Review attribution issue",
                curate=True,
            )

        api.assert_awaited_once()
        wait_for_reply.assert_not_awaited()
        briefing = result.structured_content["data"]["briefing"]
        self.assertEqual(briefing["status"], "private_briefing")
        self.assertIn("for @agent-123", briefing["content"])


if __name__ == "__main__":
    unittest.main()
