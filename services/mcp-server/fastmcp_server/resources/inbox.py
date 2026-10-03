"""Inbox resource for FastMCP server.

Returns unread @mentions for the authenticated agent.
"""

import html
import logging

from fastmcp import FastMCP
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.server.auth import AccessToken
from starlette.requests import Request

from fastmcp_server.api_client import api_request

logger = logging.getLogger(__name__)

MARKDOWN_META_CHARS = "\\`*_{}[]()#+-.!|>"


def escape_markdown_text(value: object) -> str:
    """Render untrusted backend text as literal Markdown text.

    Inbox resources are consumed by MCP clients that may render Markdown as
    HTML. Treat agent names, message sender names, message bodies, timestamps,
    and backend error text as data so a compromised or malicious sender cannot
    inject raw HTML or active Markdown links into the rendered inbox resource.
    """
    escaped_html = html.escape(str(value), quote=False)
    return "".join(
        f"\\{char}" if char in MARKDOWN_META_CHARS else char
        for char in escaped_html
    )


def register_inbox(mcp: FastMCP):
    async def _render_inbox(
        agent_name: str,
        token: AccessToken,
        request: Request,
    ) -> str:
        jwt = token.token
        agent_id = token.claims.get("agent_id")
        resolved_agent = request.headers.get("x-agent-name") or agent_name

        result = await api_request(
            "GET", "/api/v1/messages", jwt,
            params={"filter": "mentions", "limit": 20},
            agent_name=resolved_agent,
            agent_id=agent_id,
        )

        if result.get("error"):
            # Backend error text is escaped as literal Markdown before rendering.
            return "Error fetching inbox: " + escape_markdown_text(result["error"])  # nosemgrep: python.flask.security.audit.directly-returned-format-string.directly-returned-format-string

        messages = result.get("messages", [])
        safe_agent = escape_markdown_text(resolved_agent)
        if not messages:
            # The agent name is escaped as literal Markdown before rendering.
            return "# Inbox for @" + safe_agent + "\n\nNo unread mentions."  # nosemgrep: python.flask.security.audit.directly-returned-format-string.directly-returned-format-string

        lines = ["# Inbox for @" + safe_agent + "\n"]
        for msg in messages:
            sender = escape_markdown_text(msg.get("sender_name", "unknown"))
            content = escape_markdown_text(str(msg.get("content", ""))[:200])
            ts = escape_markdown_text(msg.get("created_at", ""))
            lines.append("**@" + sender + "** (" + ts + "):\n" + content + "\n")

        return "\n".join(lines)

    @mcp.resource("ax://inbox/me")
    async def inbox_me(
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> str:
        """Unread @mentions for the connected agent."""
        resolved_agent = request.headers.get("x-agent-name") or token.claims.get(
            "agent_name"
        )
        if not resolved_agent:
            return "# Inbox\n\nAgent identity is required to read ax://inbox/me."
        return await _render_inbox(resolved_agent, token, request)

    @mcp.resource("ax://inbox/{agent_name}")
    async def inbox(
        agent_name: str,
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> str:
        """Unread @mentions for an agent."""
        return await _render_inbox(agent_name, token, request)
