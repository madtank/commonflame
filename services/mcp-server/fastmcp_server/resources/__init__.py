"""Resource registration for FastMCP server."""

from fastmcp import FastMCP


def register_all_resources(
    mcp: FastMCP,
    *,
    enable_inbox_notifications: bool = True,
) -> None:
    """Register all MCP resources."""
    from fastmcp_server.resources.mission_briefing import register_mission_briefing
    from fastmcp_server.resources.inbox import register_inbox
    from fastmcp_server.resources.inbox_notifications import (
        register_inbox_notifications,
    )
    from fastmcp_server.resources.widget_resources import register_widget_resources

    register_mission_briefing(mcp)
    register_inbox(mcp)
    register_inbox_notifications(mcp, enabled=enable_inbox_notifications)
    register_widget_resources(mcp)
