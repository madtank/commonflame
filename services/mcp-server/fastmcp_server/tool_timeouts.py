"""Hard deadline around MCP tool dispatch.

A hung tool call must fail with a structured tool error instead of holding the
request open indefinitely. 2026-06-10 prod incident: one stuck POST /mcp pinned
the single uvicorn worker, /health stopped answering, and ECS recycled the task
on ELB health-check timeouts (exit 137) until a worker-count mitigation landed.
This middleware removes the unbounded-hang class regardless of worker count.
"""

import asyncio
import logging
import os

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.middleware import Middleware

logger = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT_SECONDS = 60
# messages send may legitimately hold the request while waiting on a reply
# (`wait=True`, `max_wait` up to 3600s per the tool schema). Honor the
# caller's own bound plus a margin — bounded is the property that matters.
# Only `wait` qualifies: curate/check never use max_wait for a bounded hold,
# so they keep the default deadline (Codex P2 on #296).
WAIT_CAPABLE_TOOLS = frozenset({"messages"})
WAIT_MARGIN_SECONDS = 60
MAX_TOOL_TIMEOUT_SECONDS = 3660


def _default_timeout_seconds() -> float:
    raw = os.getenv("AX_MCP_TOOL_TIMEOUT_SECONDS", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(DEFAULT_TOOL_TIMEOUT_SECONDS)
    return value if value > 0 else float(DEFAULT_TOOL_TIMEOUT_SECONDS)


def tool_call_deadline_seconds(tool_name, arguments) -> float:
    deadline = _default_timeout_seconds()
    if tool_name in WAIT_CAPABLE_TOOLS and isinstance(arguments, dict):
        if arguments.get("wait"):
            try:
                max_wait = float(arguments.get("max_wait", DEFAULT_TOOL_TIMEOUT_SECONDS))
            except (TypeError, ValueError):
                max_wait = float(DEFAULT_TOOL_TIMEOUT_SECONDS)
            deadline = max(
                deadline,
                min(max_wait + WAIT_MARGIN_SECONDS, MAX_TOOL_TIMEOUT_SECONDS),
            )
    return deadline


class ToolCallTimeoutMiddleware(Middleware):
    """Bound every tools/call so one stuck tool cannot pin the worker."""

    async def on_call_tool(self, context, call_next):
        params = getattr(context, "message", None)
        tool_name = getattr(params, "name", None) or "unknown"
        arguments = getattr(params, "arguments", None)
        deadline = tool_call_deadline_seconds(tool_name, arguments)
        try:
            return await asyncio.wait_for(call_next(context), timeout=deadline)
        except asyncio.TimeoutError:
            logger.error(
                "MCP tool call exceeded hard deadline and was cancelled",
                extra={"mcp_tool_name": tool_name, "deadline_seconds": deadline},
            )
            raise ToolError(
                f"Tool '{tool_name}' timed out after {int(deadline)}s and was "
                "cancelled. The platform may be degraded or the call may be "
                "waiting on something that will never respond (e.g. a disabled "
                "agent); retry, or retry with a smaller scope."
            ) from None
