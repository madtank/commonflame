"""Tests for the hard deadline around MCP tool dispatch.

Regression coverage for the 2026-06-10 prod incident: one stuck POST /mcp
pinned the single uvicorn worker until ELB health checks recycled the task.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from fastmcp_server.tool_timeouts import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    MAX_TOOL_TIMEOUT_SECONDS,
    ToolCallTimeoutMiddleware,
    tool_call_deadline_seconds,
)


class DeadlinePolicyTests(unittest.TestCase):
    def test_default_deadline_applies_to_plain_tools(self) -> None:
        self.assertEqual(
            tool_call_deadline_seconds("tasks", {}), DEFAULT_TOOL_TIMEOUT_SECONDS
        )

    def test_messages_wait_extends_deadline_by_max_wait_plus_margin(self) -> None:
        deadline = tool_call_deadline_seconds(
            "messages", {"action": "send", "wait": True, "max_wait": 300}
        )
        self.assertEqual(deadline, 360)

    def test_messages_wait_deadline_is_capped(self) -> None:
        deadline = tool_call_deadline_seconds(
            "messages", {"action": "send", "wait": True, "max_wait": 999999}
        )
        self.assertEqual(deadline, MAX_TOOL_TIMEOUT_SECONDS)

    def test_messages_without_wait_uses_default(self) -> None:
        deadline = tool_call_deadline_seconds(
            "messages", {"action": "send", "max_wait": 3000}
        )
        self.assertEqual(deadline, DEFAULT_TOOL_TIMEOUT_SECONDS)

    def test_curated_check_keeps_default_deadline(self) -> None:
        deadline = tool_call_deadline_seconds(
            "messages", {"action": "check", "curate": True, "max_wait": 3600}
        )
        self.assertEqual(deadline, DEFAULT_TOOL_TIMEOUT_SECONDS)

    def test_non_wait_capable_tools_ignore_max_wait(self) -> None:
        deadline = tool_call_deadline_seconds(
            "tasks", {"wait": True, "max_wait": 3000}
        )
        self.assertEqual(deadline, DEFAULT_TOOL_TIMEOUT_SECONDS)

    def test_garbage_max_wait_falls_back_to_default_wait_budget(self) -> None:
        deadline = tool_call_deadline_seconds(
            "messages", {"wait": True, "max_wait": "not-a-number"}
        )
        self.assertEqual(deadline, 120)


class TimeoutMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def _context(self, name: str = "tasks", arguments: dict | None = None):
        return SimpleNamespace(
            message=SimpleNamespace(name=name, arguments=arguments or {})
        )

    async def test_hung_tool_call_raises_structured_tool_error(self) -> None:
        middleware = ToolCallTimeoutMiddleware()

        async def hung_call(_context):
            await asyncio.sleep(3600)

        with patch.dict("os.environ", {"AX_MCP_TOOL_TIMEOUT_SECONDS": "0.05"}):
            with self.assertRaises(ToolError) as caught:
                await middleware.on_call_tool(self._context(), hung_call)

        self.assertIn("timed out", str(caught.exception))
        self.assertIn("tasks", str(caught.exception))

    async def test_fast_tool_call_passes_through_unchanged(self) -> None:
        middleware = ToolCallTimeoutMiddleware()
        sentinel = object()

        async def fast_call(_context):
            return sentinel

        result = await middleware.on_call_tool(self._context(), fast_call)
        self.assertIs(result, sentinel)

    async def test_timeout_cancels_the_hung_dispatch(self) -> None:
        middleware = ToolCallTimeoutMiddleware()
        cancelled = asyncio.Event()

        async def hung_call(_context):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with patch.dict("os.environ", {"AX_MCP_TOOL_TIMEOUT_SECONDS": "0.05"}):
            with self.assertRaises(ToolError):
                await middleware.on_call_tool(self._context(), hung_call)

        await asyncio.wait_for(cancelled.wait(), timeout=1)


if __name__ == "__main__":
    unittest.main()
