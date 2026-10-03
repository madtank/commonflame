"""Agent-group actions folded into the FastMCP agents tool.

Create and manage named groups of agents, then message a whole group at once.
All operations route through the backend API (/api/v1/agent-groups), forwarding
the active JWT + agent/space context (ROUTER-001 / AGENT-TOKEN-001).

This module is intentionally *not* registered as a standalone MCP tool. The
public MCP surface remains the long-standing seven tools; callers reach these
operations through the existing ``agents`` tool actions. Sending expands to
member mentions server-side via metadata.mentioned_group_ids — no new delivery
path.
"""

import logging
from typing import Any, Literal, Optional

from fastmcp.tools.tool import ToolResult

from fastmcp_server.api_client import (
    api_request_with_context as _api_request_with_ctx,
)
from fastmcp_server.mcp_ui import (
    build_notice,
    build_tool_output,
    widget_tool_result,
)

logger = logging.getLogger(__name__)

_GROUPS_PATH = "/api/v1/agent-groups"
_MESSAGES_PATH = "/api/v1/messages"


def _err_message(result: Any, fallback: str) -> str:
    if isinstance(result, dict):
        err = result.get("error") or result.get("detail")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("detail") or fallback)
        if err:
            return str(err)
    return fallback


def _groups_result(
    *,
    action: str,
    groups: list[dict] | None = None,
    selected_group: dict | None = None,
    space_id: str | None = None,
    notice: dict | None = None,
    state: str = "ready",
    raw: Any = None,
) -> ToolResult:
    data: dict[str, Any] = {
        "groups": groups if groups is not None else [],
        "selected_group": selected_group,
        "space_id": space_id,
    }
    structured = build_tool_output("agent_groups", 1, state, data)
    if notice:
        structured["notice"] = notice
    return widget_tool_result(
        "agents",
        action=action,
        content=raw if raw is not None else (selected_group or {"groups": data["groups"]}),
        structured_content=structured,
    )


def _error_result(action: str, message: str, *, code: str = "agent_groups_error", space_id: str | None = None) -> ToolResult:
    return _groups_result(
        action=action,
        groups=[],
        space_id=space_id,
        state="error",
        notice=build_notice(message, severity="error", code=code),
        raw={"error": message},
    )


async def agent_groups_action(
    *,
    action: Literal[
        "list",
        "get",
        "create",
        "update",
        "delete",
        "add_members",
        "remove_member",
        "send",
    ] = "list",
    group_id: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    member_agent_ids: Optional[list[str]] = None,
    agent_id: Optional[str] = None,
    visibility: Optional[Literal["space", "private"]] = None,
    is_archived: Optional[bool] = None,
    is_dynamic: Optional[bool] = None,
    dynamic_rules: Optional[dict[str, Any]] = None,
    include_archived: bool = False,
    content: Optional[str] = None,
    ctx: dict[str, Any],
) -> ToolResult | dict:
    """Shared agent-group action implementation folded into the agents tool."""
    space_id = str(ctx.get("space_id") or "").strip() or None

    async def _refresh_list() -> list[dict]:
        params = {"include_archived": "true"} if include_archived else None
        res = await _api_request_with_ctx(ctx, "GET", _GROUPS_PATH, params=params)
        if isinstance(res, list):
            return res
        if isinstance(res, dict) and isinstance(res.get("items"), list):
            return res["items"]
        return []

    try:
        if action == "list":
            groups = await _refresh_list()
            return _groups_result(action=action, groups=groups, space_id=space_id, raw=groups)

        if action == "get":
            if not group_id:
                return _error_result(action, "group_id is required for action=get", space_id=space_id)
            res = await _api_request_with_ctx(ctx, "GET", f"{_GROUPS_PATH}/{group_id}")
            if isinstance(res, dict) and res.get("error"):
                return _error_result(action, _err_message(res, "Group not found"), space_id=space_id)
            return _groups_result(action=action, selected_group=res, space_id=space_id, raw=res)

        if action == "create":
            if not name or not name.strip():
                return _error_result(action, "name is required for action=create", space_id=space_id)
            payload: dict[str, Any] = {"name": name.strip()}
            if description is not None:
                payload["description"] = description
            if member_agent_ids:
                payload["member_agent_ids"] = member_agent_ids
            if visibility is not None:
                payload["visibility"] = visibility
            if is_dynamic is not None:
                payload["is_dynamic"] = is_dynamic
            if dynamic_rules is not None:
                payload["dynamic_rules"] = dynamic_rules
            res = await _api_request_with_ctx(ctx, "POST", _GROUPS_PATH, json_data=payload)
            if isinstance(res, dict) and res.get("error"):
                return _error_result(action, _err_message(res, "Could not create group"), space_id=space_id)
            groups = await _refresh_list()
            return _groups_result(
                action=action,
                groups=groups,
                selected_group=res,
                space_id=space_id,
                notice=build_notice(f"Created group “{res.get('name', name)}”.", severity="info"),
                raw=res,
            )

        if action == "update":
            if not group_id:
                return _error_result(action, "group_id is required for action=update", space_id=space_id)
            payload = {}
            if name is not None:
                payload["name"] = name
            if description is not None:
                payload["description"] = description
            if visibility is not None:
                payload["visibility"] = visibility
            if is_archived is not None:
                payload["is_archived"] = is_archived
            if dynamic_rules is not None:
                payload["dynamic_rules"] = dynamic_rules
            if not payload:
                return _error_result(action, "Nothing to update", space_id=space_id)
            res = await _api_request_with_ctx(ctx, "PATCH", f"{_GROUPS_PATH}/{group_id}", json_data=payload)
            if isinstance(res, dict) and res.get("error"):
                return _error_result(action, _err_message(res, "Could not update group"), space_id=space_id)
            groups = await _refresh_list()
            return _groups_result(action=action, groups=groups, selected_group=res, space_id=space_id, raw=res)

        if action == "delete":
            if not group_id:
                return _error_result(action, "group_id is required for action=delete", space_id=space_id)
            res = await _api_request_with_ctx(ctx, "DELETE", f"{_GROUPS_PATH}/{group_id}")
            if isinstance(res, dict) and res.get("error"):
                return _error_result(action, _err_message(res, "Could not delete group"), space_id=space_id)
            groups = await _refresh_list()
            return _groups_result(
                action=action,
                groups=groups,
                space_id=space_id,
                notice=build_notice("Group deleted.", severity="info"),
                raw={"deleted": group_id},
            )

        if action == "add_members":
            if not group_id or not member_agent_ids:
                return _error_result(action, "group_id and member_agent_ids are required", space_id=space_id)
            res = await _api_request_with_ctx(
                ctx, "POST", f"{_GROUPS_PATH}/{group_id}/members", json_data={"agent_ids": member_agent_ids}
            )
            if isinstance(res, dict) and res.get("error"):
                return _error_result(action, _err_message(res, "Could not add members"), space_id=space_id)
            return _groups_result(action=action, selected_group=res, space_id=space_id, raw=res)

        if action == "remove_member":
            if not group_id or not agent_id:
                return _error_result(action, "group_id and agent_id are required", space_id=space_id)
            res = await _api_request_with_ctx(
                ctx, "DELETE", f"{_GROUPS_PATH}/{group_id}/members/{agent_id}"
            )
            if isinstance(res, dict) and res.get("error"):
                return _error_result(action, _err_message(res, "Could not remove member"), space_id=space_id)
            return _groups_result(action=action, selected_group=res, space_id=space_id, raw=res)

        if action == "send":
            if not group_id:
                return _error_result(action, "group_id is required for action=send", space_id=space_id)
            if not content or not content.strip():
                return _error_result(action, "content is required for action=send", space_id=space_id)
            payload = {
                "content": content,
                "metadata": {"mentioned_group_ids": [group_id]},
            }
            res = await _api_request_with_ctx(ctx, "POST", _MESSAGES_PATH, json_data=payload)
            if isinstance(res, dict) and res.get("error"):
                return _error_result(action, _err_message(res, "Could not send message"), space_id=space_id)
            groups = await _refresh_list()
            return _groups_result(
                action=action,
                groups=groups,
                space_id=space_id,
                notice=build_notice("Message sent to the group.", severity="info"),
                raw=res,
            )

        return _error_result(action, f"Unknown action: {action}", space_id=space_id)

    except Exception as exc:  # noqa: BLE001 - surface a friendly notice, never crash the tool
        logger.exception("agent group action=%s failed", action)
        return _error_result(action, f"Agent group request failed: {exc}", space_id=space_id)
