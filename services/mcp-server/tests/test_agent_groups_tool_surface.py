"""Regression tests for folding agent groups into the agents MCP tool."""

import asyncio

from fastmcp import FastMCP

from fastmcp_server.tools import register_all_tools
from fastmcp_server.tools.agents import register_agents_tool


def test_register_all_tools_does_not_expose_standalone_agent_groups_tool():
    mcp = FastMCP("test")
    register_all_tools(mcp)

    names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert "agents" in names
    assert "agent_groups" not in names


def test_agents_tool_schema_contains_group_actions_and_parameters():
    mcp = FastMCP("test")
    register_agents_tool(mcp)
    tool = asyncio.run(mcp.get_tool("agents"))
    assert tool is not None
    params = tool.parameters["properties"]

    assert "group_id" in params
    assert "member_agent_ids" in params
    assert "group_member_agent_id" in params
    action_enum = params["action"]["enum"]
    assert "group_list" in action_enum
    assert "group_create" in action_enum
    assert "group_send" in action_enum
