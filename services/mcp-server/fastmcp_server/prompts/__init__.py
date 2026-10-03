"""Prompt registration for FastMCP server."""
from fastmcp import FastMCP


def register_all_prompts(mcp: FastMCP):
    """Register all MCP prompts."""
    from fastmcp_server.prompts.startup import register_startup_prompt

    register_startup_prompt(mcp)
