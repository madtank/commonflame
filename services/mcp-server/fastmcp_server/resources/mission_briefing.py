"""Mission Briefing resource for FastMCP server.

Composes identity, recent messages, and assigned tasks into a
single markdown briefing that agents load at session start.
"""

import logging

from fastmcp import FastMCP
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.server.auth import AccessToken
from starlette.requests import Request

from fastmcp_server.api_client import api_request

logger = logging.getLogger(__name__)


def register_mission_briefing(mcp: FastMCP):

    @mcp.resource("ax://mission-briefing")
    async def mission_briefing(
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> str:
        """Your Mission Briefing — identity, inbox, and tasks at a glance."""
        jwt = token.token
        agent_id = token.claims.get("agent_id")
        agent_name = request.headers.get("x-agent-name")

        # Fetch identity, messages, and tasks in parallel would be ideal,
        # but we do sequential calls for simplicity (3 fast API calls)
        identity = await api_request("GET", "/api/v1/agents/me", jwt, agent_name=agent_name, agent_id=agent_id)
        messages = await api_request("GET", "/api/v1/messages", jwt, params={"limit": 5, "mark_read": False}, agent_name=agent_name, agent_id=agent_id)
        my_tasks = await api_request("GET", "/api/v1/tasks", jwt, params={"filter": "my_tasks", "limit": 10}, agent_name=agent_name, agent_id=agent_id)

        # Build markdown briefing
        lines = ["# Mission Briefing\n"]

        # Identity section
        if not identity.get("error"):
            name = identity.get("name", "unknown")
            handle = identity.get("handle", f"@{name}")
            bio = identity.get("bio", "")
            lines.append("## Your Identity")
            lines.append(f"- **Handle:** {handle}")
            if bio:
                lines.append(f"- **Bio:** {bio}")
            spec = identity.get("specialization", "")
            if spec:
                lines.append(f"- **Specialization:** {spec}")
            lines.append("")

        # Inbox section
        if not messages.get("error"):
            msg_list = messages.get("messages", [])
            unread = messages.get("unread_count", 0)
            lines.append(f"## Inbox ({unread} unread)")
            if msg_list:
                for msg in msg_list[:5]:
                    sender = msg.get("sender_name", "unknown")
                    content = msg.get("content", "")[:100]
                    lines.append(f"- **@{sender}**: {content}")
            else:
                lines.append("- No recent messages")
            lines.append("")

        # Tasks section
        if not my_tasks.get("error"):
            task_list = my_tasks.get("tasks", [])
            total = my_tasks.get("total", 0)
            lines.append(f"## Your Tasks ({total} assigned)")
            if task_list:
                for t in task_list[:10]:
                    status_icon = {"not_started": "[ ]", "in_progress": "[~]", "completed": "[x]"}.get(t.get("status", ""), "[ ]")
                    priority = t.get("priority", "")
                    prio_tag = f" [{priority}]" if priority else ""
                    lines.append(f"- {status_icon}{prio_tag} {t.get('title', 'Untitled')}")
            else:
                lines.append("- No tasks assigned")
            lines.append("")

        return "\n".join(lines)
