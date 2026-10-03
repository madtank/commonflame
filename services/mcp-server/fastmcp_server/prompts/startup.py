"""Startup prompt for FastMCP server.

Guides agents to load their Mission Briefing at the start of any session.
"""

from fastmcp import FastMCP
from fastmcp.prompts import Message


def register_startup_prompt(mcp: FastMCP):

    @mcp.prompt(name="startup")
    async def startup() -> list[Message]:
        """Load your Mission Briefing. Run this first in any session."""
        return [
            Message(content="Load my Mission Briefing and summarize what I need to know.", role="user"),
            Message(
                content=(
                    "I'll load your Mission Briefing now. This includes your identity, "
                    "recent inbox messages, and assigned tasks. "
                    "Reading resource: ax://mission-briefing"
                ),
                role="assistant",
            ),
        ]
