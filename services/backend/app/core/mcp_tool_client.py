# DEPRECATED (2026-03-14): This module was used by dispatch_executor.py
# to re-fetch structuredContent from the MCP server after agent responses.
# That flow has been removed — messages now carry thin widget pointers and
# the frontend fetches data directly from the MCP server.
#
# This module may still be useful for backend-initiated MCP tool calls
# in the future. Keeping it but marking as unused.
"""
MCP Tool Client — direct JSON-RPC calls to the MCP server.

DEPRECATED: No active callers. See deprecation notice above.
"""

import json
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _parse_sse_response(text: str) -> dict | None:
    """Parse a JSON-RPC result from an SSE text/event-stream response.

    FastMCP returns SSE format:
        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}

    We extract the last 'data:' line that contains valid JSON.
    """
    for line in reversed(text.strip().splitlines()):
        if line.startswith("data: "):
            try:
                return json.loads(line[6:])
            except json.JSONDecodeError:
                continue
    return None

# Default arguments for MCP tool actions (used when agent already called the
# tool and we just need the structured data for the widget)
_DEFAULT_ARGS: dict[str, dict] = {
    "agents": {"action": "list"},
    "tasks": {"action": "list"},
    "messages": {"action": "check", "limit": 10},
    "search": {"action": "search", "query": "recent"},
    "context": {"action": "list"},
    "spaces": {"action": "list"},
}

# Timeout for MCP tool calls — widget data fetch should be fast
_MCP_CALL_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


async def call_mcp_tool(
    *,
    tool_name: str,
    space_id: str,
    agent_id: str,
    agent_name: str,
    tool_args: dict[str, Any] | None = None,
) -> dict | None:
    """Call an MCP tool directly and return the result.

    Returns the full result dict (with structuredContent, meta, content)
    or None if the call fails for any reason.

    This is used after dispatch to fetch structured widget data when
    the Space Agent doesn't expose raw MCP tool results in its stream.
    """
    settings = get_settings()
    mcp_url = f"{settings.mcp_server_url}/mcp"
    args = tool_args or _DEFAULT_ARGS.get(tool_name, {})

    # Mint a short-lived JWT for this MCP call
    try:
        from app.core.ax_jwt import mint_ax_jwt

        token = mint_ax_jwt(
            agent_id=agent_id,
            agent_name=agent_name,
            space_id=space_id,
            tools_allowed=[tool_name],
            ttl_seconds=60,
        )
    except Exception as e:
        logger.error("MCP_TOOL_CLIENT_JWT_ERROR tool=%s error=%s", tool_name, e)
        return None

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": args,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=_MCP_CALL_TIMEOUT) as client:
            resp = await client.post(
                mcp_url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "X-Agent-Name": agent_name,
                },
            )

        if resp.status_code != 200:
            logger.warning(
                "MCP_TOOL_CLIENT_HTTP_ERROR tool=%s status=%s",
                tool_name, resp.status_code,
            )
            return None

        # FastMCP returns SSE (text/event-stream) — parse the JSON from
        # the "event: message\ndata: {json}" envelope
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            data = _parse_sse_response(resp.text)
        else:
            data = resp.json()

        if not data:
            logger.warning("MCP_TOOL_CLIENT_NO_DATA tool=%s", tool_name)
            return None

        result = data.get("result")
        if not result:
            logger.warning("MCP_TOOL_CLIENT_NO_RESULT tool=%s", tool_name)
            return None

        # Normalize _meta → meta (FastMCP uses _meta, our code expects meta)
        if "_meta" in result and "meta" not in result:
            result["meta"] = result.pop("_meta")

        if result.get("isError"):
            logger.warning(
                "MCP_TOOL_CLIENT_TOOL_ERROR tool=%s content=%s",
                tool_name, result.get("content", "")[:200],
            )
            return None

        logger.info(
            "MCP_TOOL_CLIENT_OK tool=%s has_structured=%s",
            tool_name, "structuredContent" in result,
        )
        return result

    except httpx.TimeoutException:
        logger.warning("MCP_TOOL_CLIENT_TIMEOUT tool=%s", tool_name)
        return None
    except Exception as e:
        logger.error("MCP_TOOL_CLIENT_ERROR tool=%s error=%s", tool_name, e)
        return None
