"""Tool registration for FastMCP server.

7 tools: whoami, messages, tasks, agents, spaces, context, search
(+ experimental `games` when enabled). The governed Context Catalog is folded
into the `context` tool as additional actions (CTX-ARTIFACTS-002), not a
separate tool.
All tools return ToolResult with widget metadata for MCP Apps rendering.
"""

from fastmcp import FastMCP

from fastmcp_server.mcp_ui import experimental_games_enabled


def register_all_tools(mcp: FastMCP):
    """Register all MCP tools."""
    from fastmcp_server.tools.whoami import register_whoami_tool
    from fastmcp_server.tools.messages import register_messages_tool
    from fastmcp_server.tools.tasks import register_tasks_tool
    from fastmcp_server.tools.agents import register_agents_tool
    from fastmcp_server.tools.spaces import register_spaces_tool
    from fastmcp_server.tools.context import register_context_tool
    from fastmcp_server.tools.search import register_search_tool

    register_whoami_tool(mcp)
    register_messages_tool(mcp)
    register_tasks_tool(mcp)
    register_agents_tool(mcp)
    register_spaces_tool(mcp)
    register_context_tool(mcp)
    register_search_tool(mcp)
    if experimental_games_enabled():
        from fastmcp_server.tools.games import register_games_tool

        register_games_tool(mcp)
