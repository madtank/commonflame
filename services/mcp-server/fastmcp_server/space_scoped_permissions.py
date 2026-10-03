"""Shared MCP widget permission policy based on the active space context."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp_server.api_client import api_request
from fastmcp_server.backend_routes import SPACES_COLLECTION_PATH, space_item_path

logger = logging.getLogger(__name__)

_PRIVATE_SCOPE = "private_workspace"
_SHARED_SCOPE = "shared_space"
_UNSCOPED_SCOPE = "unscoped"
_AGENT_AUTHORED_HITL_TOOLS = frozenset({"agents", "spaces"})
_AGENT_AUTHORED_COLLABORATION_TOOLS = frozenset({"context", "messages", "tasks"})


def _first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def is_personal_space(space: dict[str, Any] | None) -> bool:
    if not isinstance(space, dict):
        return False
    if space.get("is_personal") is True:
        return True
    if _first_non_empty_string(space.get("space_mode")) == "personal":
        return True
    description = _first_non_empty_string(space.get("description"))
    if description and description.startswith("Personal workspace for"):
        logger.warning(
            "Ignoring legacy personal-space description heuristic for space_id=%s; "
            "backend must provide is_personal=true or space_mode=personal",
            space.get("id"),
        )
    return False


def _has_space_scope_fields(space: dict[str, Any]) -> bool:
    """Return true when a list/detail row can classify personal vs shared."""
    if isinstance(space.get("is_personal"), bool):
        return True
    return _first_non_empty_string(space.get("space_mode")) is not None


def blocked_reason(
    permission_bundle: dict[str, Any] | None,
    default: str = "Action not available in this context",
) -> str:
    permissions = (
        permission_bundle.get("permissions")
        if isinstance(permission_bundle, dict)
        else None
    )
    if isinstance(permissions, dict):
        reason = permissions.get("blocked_reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return default


def _extract_space_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("spaces", "items", "results"):
        value = result.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    data = result.get("data")
    if isinstance(data, dict):
        return _extract_space_items(data)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _normalize_space_context(
    space_id: str | None,
    space: dict[str, Any] | None,
    *,
    assume_personal: bool = False,
) -> dict[str, Any]:
    if not isinstance(space, dict):
        return {
            "id": space_id,
            "name": None,
            "visibility": None,
            "role": None,
            "is_personal": assume_personal,
            "scope": _PRIVATE_SCOPE if assume_personal else _UNSCOPED_SCOPE,
        }

    personal = assume_personal or is_personal_space(space)
    visibility = _first_non_empty_string(space.get("visibility")) or "private"
    scope = _PRIVATE_SCOPE if personal else _SHARED_SCOPE
    return {
        "id": _first_non_empty_string(space.get("id"), space_id),
        "name": _first_non_empty_string(space.get("name"), space.get("workspace_name")),
        "visibility": visibility,
        "role": _first_non_empty_string(
            space.get("viewer_role"),
            space.get("role"),
            space.get("member_role"),
        ),
        "is_personal": personal,
        "scope": scope,
    }


def _extract_agent_space_id(result: dict[str, Any]) -> str | None:
    workspace = result.get("workspace")
    return _first_non_empty_string(
        result.get("space_id"),
        workspace.get("id") if isinstance(workspace, dict) else None,
    )


async def _resolve_route_bound_agent_space_id(ctx: dict[str, Any]) -> str | None:
    space_id = _first_non_empty_string(ctx.get("space_id"))
    if space_id:
        return space_id
    route_agent_name = _first_non_empty_string(ctx.get("route_agent_name"))
    if not route_agent_name:
        return None

    result = await api_request(
        "GET",
        "/api/v1/agents/me",
        ctx["jwt"],
        agent_name=route_agent_name,
        delegation_mode=ctx.get("delegation_mode"),
        delegated_for=ctx.get("delegated_for"),
    )
    if not isinstance(result, dict) or result.get("error"):
        return None
    return _extract_agent_space_id(result)


async def _fetch_current_space(
    ctx: dict[str, Any],
    space_id: str | None = None,
) -> dict[str, Any] | None:
    space_id = _first_non_empty_string(space_id, ctx.get("space_id"))
    if not space_id:
        return None

    # Prefer the canonical v1 list response. It is the route mounted in dev/prod
    # and prevents the old `/api/spaces/` compatibility path from turning every
    # permission lookup into a 404.
    result = await api_request(
        "GET",
        SPACES_COLLECTION_PATH,
        ctx["jwt"],
        agent_name=ctx.get("agent_name"),
        agent_id=ctx.get("agent_id"),
        space_id=space_id,
        delegation_mode=ctx.get("delegation_mode"),
        delegated_for=ctx.get("delegated_for"),
    )
    matched_list_item: dict[str, Any] | None = None
    if isinstance(result, dict) and not result.get("error"):
        for item in _extract_space_items(result):
            if _first_non_empty_string(item.get("id")) == space_id:
                if _has_space_scope_fields(item):
                    return item
                matched_list_item = item
                break

    # Fallback to by-id detail when the list shape changes, omits the target, or
    # returns a lightweight row without personal/shared classification fields.
    fallback = await api_request(
        "GET",
        space_item_path(space_id),
        ctx["jwt"],
        agent_name=ctx.get("agent_name"),
        agent_id=ctx.get("agent_id"),
        space_id=space_id,
        delegation_mode=ctx.get("delegation_mode"),
        delegated_for=ctx.get("delegated_for"),
    )
    if not isinstance(fallback, dict) or fallback.get("error"):
        return None
    if not _has_space_scope_fields(fallback):
        if matched_list_item and _has_space_scope_fields(matched_list_item):
            return matched_list_item
        return None
    return fallback


def _tool_permissions(
    tool_name: str,
    scope: str,
    *,
    user_session: bool = False,
) -> dict[str, Any]:
    tool = (tool_name or "").strip().lower()
    if user_session:
        permissions: dict[str, Any] = {
            "mode": "user_session_controls",
            "read_only": False,
            "blocked_reason": None,
            "can_create": False,
            "can_update": False,
            "can_delete": False,
            "can_archive": False,
            "can_control": False,
            "can_write_memory": False,
            "can_use_hitl_approval": True,
        }
        if tool == "tasks":
            permissions.update({"can_create": True, "can_update": True})
        elif tool == "agents":
            permissions.update({"can_create": True, "can_update": True, "can_control": True})
        elif tool == "spaces":
            permissions.update({"can_create": True, "can_update": True, "can_archive": True})
        elif tool == "whoami":
            permissions.update({"can_update": True, "can_write_memory": True})
        elif tool == "context":
            permissions.update({"can_create": True, "can_update": True, "can_delete": True})
        return permissions

    # User-initiated quick actions are handled above. Agent-authored calls are
    # collaborative inside a bound private/team space for messages, tasks, and
    # context, while agent/space administration remains approval-gated.
    collaborative_space = scope in {_PRIVATE_SCOPE, _SHARED_SCOPE}
    collaborative_tool = tool in _AGENT_AUTHORED_COLLABORATION_TOOLS
    read_only = not (
        scope == _PRIVATE_SCOPE
        or (scope == _SHARED_SCOPE and collaborative_tool)
    )
    # blocked_reason only applies to privileged write actions — reads (list,
    # get) always work. The widget uses this to gate write buttons, not to
    # block the entire widget.
    write_blocked_reason = None
    if scope == _SHARED_SCOPE and read_only:
        write_blocked_reason = "Agent-authored actions require user approval. Use a user quick action to make changes."
    elif scope == _UNSCOPED_SCOPE and read_only:
        write_blocked_reason = "This action requires an active workspace context."

    permissions: dict[str, Any] = {
        "mode": (
            "private_full_access"
            if scope == _PRIVATE_SCOPE
            else "shared_collaborative_space"
            if scope == _SHARED_SCOPE and collaborative_tool
            else "agent_authored_limited"
            if scope == _SHARED_SCOPE
            else "unscoped_read_only"
        ),
        "read_only": read_only,
        "blocked_reason": write_blocked_reason,
        "can_create": False,
        "can_update": False,
        "can_delete": False,
        "can_archive": False,
        "can_control": False,
        "can_write_memory": False,
        # Space agents may draft privileged agent/space changes for a human to
        # approve, but they still cannot execute those writes directly.
        "can_use_hitl_approval": scope == _PRIVATE_SCOPE
        or (scope == _SHARED_SCOPE and tool in _AGENT_AUTHORED_HITL_TOOLS),
    }

    if collaborative_space and tool == "tasks":
        permissions.update(
            {
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": False,
                "can_archive": False,
                "can_control": False,
                "can_write_memory": False,
            }
        )
        return permissions

    if collaborative_space and tool == "messages":
        permissions.update(
            {
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": True,
                "can_control": False,
                "can_write_memory": False,
            }
        )
        return permissions

    if collaborative_space and tool == "context":
        permissions.update(
            {
                "read_only": False,
                "blocked_reason": None,
                "can_create": True,
                "can_update": True,
                "can_delete": True,
                "can_control": False,
                "can_write_memory": False,
            }
        )
        return permissions

    if scope != _PRIVATE_SCOPE:
        return permissions

    if tool == "agents":
        permissions.update(
            {
                "can_create": True,
                "can_update": True,
                "can_control": True,
            }
        )
    elif tool == "spaces":
        permissions.update(
            {
                "can_create": True,
                "can_update": True,
                "can_archive": True,
            }
        )
    elif tool == "whoami":
        permissions.update(
            {
                "can_update": True,
                "can_write_memory": True,
            }
        )
    elif tool == "context":
        permissions.update(
            {
                "can_create": True,
                "can_update": True,
                "can_delete": True,
            }
        )

    return permissions


async def resolve_space_scoped_permissions(
    ctx: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    space_id = await _resolve_route_bound_agent_space_id(ctx)
    current_space = await _fetch_current_space(ctx, space_id)
    # For concierge/HITL dispatch, the backend mints this signed claim only
    # after validating that the sender owns the personal workspace. This avoids
    # using mutable description text as a permission boundary.
    delegated_space_owner = ctx.get("delegated_space_owner") is True
    space_context = _normalize_space_context(
        space_id,
        current_space,
        assume_personal=delegated_space_owner,
    )
    return {
        "space_context": space_context,
        "viewer": {
            "user_id": ctx.get("user_id"),
            "username": ctx.get("username"),
            "email": ctx.get("email"),
            "agent_id": ctx.get("agent_id"),
            "agent_name": ctx.get("agent_name"),
        },
        "permissions": _tool_permissions(
            tool_name,
            space_context["scope"],
            user_session=ctx.get("principal_type") == "user",
        ),
    }


def attach_permission_bundle(
    data: dict[str, Any],
    permission_bundle: dict[str, Any] | None,
) -> dict[str, Any]:
    if not permission_bundle:
        return data
    return {
        **data,
        "space_context": permission_bundle.get("space_context"),
        "viewer": permission_bundle.get("viewer"),
        "permissions": permission_bundle.get("permissions"),
    }


def permissions_allow(
    permission_bundle: dict[str, Any] | None,
    capability: str,
) -> bool:
    if not permission_bundle:
        return False
    permissions = permission_bundle.get("permissions")
    return bool(isinstance(permissions, dict) and permissions.get(capability))
