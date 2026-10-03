"""Spaces tool for FastMCP server.

Provides space listing, details, and member listing. Read-only.
All operations route through backend API (ROUTER-001).
Uses CurrentAccessToken() DI to forward backend-issued JWTs.
"""

import logging
from typing import Annotated, Any, Literal, Optional

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.server.auth import AccessToken
from fastmcp.tools.tool import ToolResult
from starlette.requests import Request

from fastmcp_server.api_client import (
    api_request_with_context as _api_request_with_ctx,
    extract_agent_context,
)
from fastmcp_server.backend_routes import (
    SPACES_COLLECTION_PATH,
    SPACES_SWITCH_PATH,
    space_item_path,
    space_members_path,
)
from fastmcp_server.mcp_ui import (
    bounded_write_annotations,
    build_action,
    build_notice,
    build_tool_output,
    tool_app_config,
    tool_meta,
    tool_output_schema,
    widget_tool_result,
)
from fastmcp_server.space_scoped_permissions import (
    attach_permission_bundle,
    blocked_reason,
    permissions_allow,
    resolve_space_scoped_permissions,
)

logger = logging.getLogger(__name__)

_SPACE_DRAFT_ACTIONS = {
    "create_draft",
    "get_draft",
    "edit_draft",
    "approve_draft",
    "reject_draft",
    "cancel_draft",
}
_SPACE_DRAFT_REQUIRED_FIELDS = ["name"]
_SPACE_JOIN_ACTIONS = {"join_public", "join_invite"}


def _extract_space_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = result.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    results = result.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    spaces = result.get("spaces")
    if isinstance(spaces, list):
        return [item for item in spaces if isinstance(item, dict)]
    if isinstance(result.get("id"), str) and "name" in result:
        return [result]
    return []


def _is_missing_route_error(result: dict[str, Any]) -> bool:
    """Detect legacy deployments that have not mounted the /api/spaces route."""
    if not isinstance(result, dict) or result.get("error") != "API error 404":
        return False
    detail = str(result.get("detail") or "")
    return '"Not Found"' in detail or detail.strip() == "Not Found"


def _find_space_by_slug(result: dict[str, Any], slug: str) -> dict[str, Any] | None:
    normalized = slug.strip().lower()
    if not normalized:
        return None
    for item in _extract_space_items(result):
        candidate = str(item.get("slug") or "").strip().lower()
        if candidate == normalized:
            return item
    return None


def _extract_member_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    members = result.get("members")
    if isinstance(members, list):
        return [_normalize_member_item(m) for m in members if isinstance(m, dict)]
    items = result.get("items")
    if isinstance(items, list):
        return [_normalize_member_item(m) for m in items if isinstance(m, dict)]
    results = result.get("results")
    if isinstance(results, list):
        return [_normalize_member_item(m) for m in results if isinstance(m, dict)]
    return []


def _normalize_member_item(member: dict[str, Any]) -> dict[str, Any]:
    """Normalize member display fields across backend member-list contracts."""
    normalized = dict(member)
    full_name = next(
        (
            str(value).strip()
            for value in (
                member.get("full_name"),
                member.get("name"),
            )
            if value is not None and str(value).strip()
        ),
        "",
    )
    display_name = next(
        (
            str(value).strip()
            for value in (
                member.get("display_name"),
                full_name,
                member.get("username"),
                member.get("email"),
                member.get("id"),
            )
            if value is not None and str(value).strip()
        ),
        "",
    )
    if display_name:
        normalized.setdefault("display_name", display_name)
    if full_name:
        normalized.setdefault("full_name", full_name)
    return normalized


def _space_mode_from_kind(kind: Any) -> str:
    if not isinstance(kind, str):
        return ""
    if kind.endswith(".personal"):
        return "personal"
    if kind.endswith(".team"):
        return "team"
    if kind.endswith(".community"):
        return "community"
    return ""


def _space_mode_from_visibility(
    visibility: str | None,
    requested_mode: str | None = None,
) -> str:
    if requested_mode in {"personal", "team", "community"}:
        return requested_mode
    normalized_visibility = (visibility or "").strip().lower()
    if normalized_visibility == "public":
        return "community"
    if normalized_visibility == "invite_only":
        return "team"
    return "personal"


def _space_draft_to_widget_draft(result: dict[str, Any]) -> dict[str, Any]:
    space = result.get("space") if isinstance(result.get("space"), dict) else {}
    kind = result.get("kind")
    return {
        "draft_id": result.get("draft_id"),
        "version": result.get("version"),
        "status": result.get("status"),
        "kind": kind,
        "card": result.get("card"),
        "space_mode": _space_mode_from_kind(kind) or _space_mode_from_visibility(space.get("visibility")),
        "name": space.get("name") or "",
        "description": space.get("description") or "",
        "visibility": space.get("visibility") or "private",
        "join_policy": space.get("join_policy"),
        "team": result.get("team") or {},
        "join": result.get("join") or {},
        "editable_fields": result.get("editable_fields") or [],
        "approval_required": result.get("approval_required", True),
        "risk_class": result.get("risk_class"),
        "backend": result,
    }


def _created_space_from_draft_result(result: dict[str, Any]) -> dict[str, Any] | None:
    execution = result.get("execution_result")
    if not isinstance(execution, dict):
        return None
    created = execution.get("space")
    if isinstance(created, dict):
        return created
    return None


def _space_invite_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    if result.get("error"):
        message = str(result.get("detail") or result.get("error"))
        structured = build_tool_output(
            "space_detail",
            2,
            "error",
            attach_permission_bundle(
                {"scope": "invite_error", "hint": message, "error": result.get("error")},
                permission_bundle,
            ),
        )
        structured["notice"] = build_notice(message, severity="error", code="space_invite_error")
        return widget_tool_result("spaces", action=action, content=result, structured_content=structured)

    structured = build_tool_output(
        "space_detail",
        2,
        "ready",
        attach_permission_bundle(
            {
                "scope": "invite_created" if action == "create_invite" else "invite_details",
                "invite": result,
                "hint": "Invite code ready.",
            },
            permission_bundle,
        ),
    )
    structured["notice"] = build_notice("Invite code ready.", code="space_invite_created")
    return widget_tool_result("spaces", action=action, content=result, structured_content=structured)


def _space_discovery_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    spaces = _extract_space_items(result)
    structured = build_tool_output(
        "space_collection",
        2,
        "empty" if not spaces else "ready",
        attach_permission_bundle(
            {
                "scope": "discover",
                "items": spaces,
                "count": result.get("count", len(spaces)),
                "hint": "Browse public spaces you can join.",
            },
            permission_bundle,
        ),
    )
    return widget_tool_result("spaces", action=action, content=result, structured_content=structured)


def _space_join_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    if result.get("error"):
        message = str(result.get("detail") or result.get("error"))
        structured = build_tool_output(
            "space_collection",
            2,
            "error",
            attach_permission_bundle(
                {"scope": "join_error", "hint": message, "error": result.get("error")},
                permission_bundle,
            ),
        )
        structured["notice"] = build_notice(message, severity="error", code="space_join_error")
        return widget_tool_result("spaces", action=action, content=result, structured_content=structured)

    space_name = result.get("space_name") or result.get("org_name") or result.get("name") or "space"
    structured = build_tool_output(
        "space_collection",
        2,
        "ready",
        attach_permission_bundle(
            {
                "scope": "join_result",
                "joined": result,
                "space_name": space_name,
                "hint": f"Joined {space_name}.",
            },
            permission_bundle,
        ),
    )
    structured["notice"] = build_notice(f"Joined {space_name}.", code="space_joined")
    return widget_tool_result("spaces", action=action, content=result, structured_content=structured)


def _space_name(result: dict[str, Any]) -> str | None:
    for value in (
        result.get("space_name"),
        result.get("name"),
        result.get("org_name"),
        result.get("workspace_name"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    space = result.get("space")
    if isinstance(space, dict):
        return _space_name(space)
    workspace = result.get("workspace")
    if isinstance(workspace, dict):
        return _space_name(workspace)
    return None


def _space_id(result: dict[str, Any], fallback: str | None = None) -> str | None:
    for value in (
        result.get("space_id"),
        result.get("id"),
        result.get("org_id"),
        result.get("workspace_id"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    space = result.get("space")
    if isinstance(space, dict):
        return _space_id(space, fallback)
    workspace = result.get("workspace")
    if isinstance(workspace, dict):
        return _space_id(workspace, fallback)
    return fallback


def _space_from_result(result: dict[str, Any], fallback_space_id: str | None = None) -> dict[str, Any]:
    spaces = _extract_space_items(result)
    if spaces:
        return spaces[0]
    space = result.get("space")
    if isinstance(space, dict):
        return space
    workspace = result.get("workspace")
    if isinstance(workspace, dict):
        return workspace
    space_id = _space_id(result, fallback_space_id)
    space_name = _space_name(result)
    current_space: dict[str, Any] = {}
    if space_id:
        current_space["id"] = space_id
    if space_name:
        current_space["name"] = space_name
    return current_space


def _space_current_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    fallback_space_id: str | None = None,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    if result.get("error"):
        message = str(result.get("detail") or result.get("error"))
        structured = build_tool_output(
            "space_detail",
            2,
            "error",
            attach_permission_bundle(
                {
                    "scope": "current_error",
                    "hint": message,
                    "error": result.get("error"),
                },
                permission_bundle,
            ),
        )
        structured["notice"] = build_notice(message, severity="error", code="spaces_current_error")
        return widget_tool_result("spaces", action=action, content=result, structured_content=structured)

    current_space = _space_from_result(result, fallback_space_id)
    data = {
        "scope": "current",
        "current_space": current_space,
        "space": current_space,
        "items": [current_space] if current_space else [],
        "count": 1 if current_space else 0,
    }
    structured = build_tool_output(
        "space_detail",
        2,
        "ready" if current_space else "empty",
        attach_permission_bundle(data, permission_bundle),
        actions=_spaces_actions("current", current_space.get("id") if current_space else None),
    )
    if current_space:
        structured["notice"] = build_notice(
            f"Currently in {current_space.get('name') or 'this space'}.",
            code="space_current",
        )
    return widget_tool_result("spaces", action=action, content=result, structured_content=structured)


def _space_switch_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    target_space_id: str,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    if result.get("error"):
        message = str(result.get("detail") or result.get("error"))
        structured = build_tool_output(
            "space_detail",
            2,
            "error",
            attach_permission_bundle(
                {
                    "scope": "switch_error",
                    "target_space_id": target_space_id,
                    "hint": message,
                    "error": result.get("error"),
                },
                permission_bundle,
            ),
        )
        structured["notice"] = build_notice(message, severity="error", code="spaces_switch_error")
        return widget_tool_result("spaces", action=action, content=result, structured_content=structured)

    current_space = _space_from_result(result, target_space_id)
    if "id" not in current_space:
        current_space["id"] = target_space_id
    available_agents = result.get("available_agents")
    agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
    if not agent and (result.get("agent_id") or result.get("agent_name")):
        agent = {
            "id": result.get("agent_id"),
            "name": result.get("agent_name"),
        }
        agent = {key: value for key, value in agent.items() if value is not None}

    data: dict[str, Any] = {
        "scope": "switch_result",
        "target_space_id": target_space_id,
        "current_space": current_space,
        "space": current_space,
        "items": [current_space],
        "count": 1,
    }
    if isinstance(available_agents, list):
        data["available_agents"] = available_agents
    if agent:
        data["agent"] = agent

    structured = build_tool_output(
        "space_detail",
        2,
        "ready",
        attach_permission_bundle(data, permission_bundle),
        actions=_spaces_actions("current", current_space.get("id")),
    )
    structured["notice"] = build_notice(
        str(result.get("message") or f"Switched to {current_space.get('name') or 'space'}."),
        code="space_switched",
    )
    return widget_tool_result("spaces", action=action, content=result, structured_content=structured)


def _active_space_id(ctx: dict[str, Any], permission_bundle: dict[str, Any] | None) -> str | None:
    if isinstance(ctx.get("space_id"), str) and ctx["space_id"].strip():
        return ctx["space_id"].strip()
    space_context = (
        permission_bundle.get("space_context")
        if isinstance(permission_bundle, dict) and isinstance(permission_bundle.get("space_context"), dict)
        else {}
    )
    return _space_id(space_context)


async def _resolve_switch_space_id(ctx: dict[str, Any], space_id: str | None, slug: str | None) -> str | None:
    normalized_space_id = (space_id or "").strip()
    if normalized_space_id:
        return normalized_space_id
    normalized_slug = (slug or "").strip()
    if not normalized_slug:
        return None
    spaces_result = await _api_request_with_ctx(ctx, "GET", SPACES_COLLECTION_PATH)
    if isinstance(spaces_result, dict) and not spaces_result.get("error"):
        space = _find_space_by_slug(spaces_result, normalized_slug)
        if space:
            return _space_id(space)
    return None


def _agent_pin_state(agent: dict[str, Any]) -> bool:
    """Return the backend-reported placement lock state for an agent."""
    for field in ("pinned", "is_pinned", "space_locked", "placement_locked"):
        value = agent.get(field)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}:
            return True
    for field in ("pinned_to_space", "pinned_space_id"):
        if str(agent.get(field) or "").strip():
            return True
    return False


async def _switch_route_bound_agent(ctx: dict[str, Any], target_space_id: str) -> dict[str, Any]:
    agent = await _api_request_with_ctx(ctx, "GET", "/api/v1/agents/me")
    if not isinstance(agent, dict) or agent.get("error"):
        return agent if isinstance(agent, dict) else {"error": "Agent identity unavailable"}

    agent_id = str(agent.get("id") or agent.get("agent_id") or "").strip()
    if not agent_id:
        return {
            "error": "Agent identity unavailable",
            "detail": "Backend did not return an agent id for /api/v1/agents/me.",
        }

    result = await _api_request_with_ctx(
        ctx,
        "POST",
        f"/api/v1/agents/{agent_id}/placement",
        # Preserve pin/space-lock state. Switching spaces should not silently
        # unlock a route-bound agent that was already pinned by the backend.
        json_data={"space_id": target_space_id, "pinned": _agent_pin_state(agent)},
    )
    if isinstance(result, dict):
        enriched = dict(result)
        enriched.setdefault("space_id", target_space_id)
        enriched.setdefault(
            "agent",
            {
                "id": agent_id,
                "name": agent.get("name") or agent.get("agent_name"),
            },
        )
        return enriched
    return {"error": "Invalid backend response", "detail": "Agent placement response was not an object."}


def _spaces_actions(action: str, space_id: str | None = None) -> list[dict[str, Any]]:
    actions = [
        build_action(
            "refresh-spaces",
            "Refresh",
            kind="tool",
            target="spaces",
            args={"action": action, **({"space_id": space_id} if space_id else {})},
            style="secondary",
            enabled=True,
            idempotent=True,
        )
    ]
    if action in {"get", "members", "current"} and space_id:
        actions.append(
            build_action(
                "view-members",
                "View members",
                kind="tool",
                target="spaces",
                args={"action": "members", "space_id": space_id},
                style="primary",
                enabled=True,
                idempotent=True,
            )
        )
        actions.append(
            build_action(
                "refresh-space-detail",
                "Refresh details",
                kind="tool",
                target="spaces",
                args={"action": "get", "space_id": space_id},
                style="secondary",
                enabled=True,
                idempotent=True,
            )
        )
        actions.append(
            build_action(
                "switch-space",
                "Switch",
                kind="tool",
                target="spaces",
                args={"action": "switch", "space_id": space_id},
                style="primary",
                enabled=True,
                idempotent=False,
            )
        )
    return actions


def _spaces_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    if result.get("error"):
        message = str(result.get("detail") or result.get("error"))
        kind = (
            "space_detail"
            if action == "get"
            else "space_members" if action == "members" else "space_collection"
        )
        structured = build_tool_output(
            kind,
            2,
            "error",
            attach_permission_bundle(
                {
                    "scope": f"{action}_error",
                    "hint": message,
                    "error": result.get("error"),
                },
                permission_bundle,
            ),
        )
        structured["notice"] = build_notice(
            message,
            severity="error",
            code=f"spaces_{action}_error",
        )
        return widget_tool_result(
            "spaces",
            action=action,
            content=result,
            structured_content=structured,
        )

    if action == "members":
        members = _extract_member_items(result)
        space_id = result.get("space_id")
        structured = build_tool_output(
            "space_members",
            2,
            "empty" if not members else "ready",
            attach_permission_bundle({
                "items": members,
                "count": result.get("count", result.get("total_members", len(members))),
                "space_name": result.get("space_name") or result.get("name"),
                "space_id": space_id,
            }, permission_bundle),
            actions=_spaces_actions(action, space_id),
        )
    else:
        spaces = _extract_space_items(result)
        state = "empty" if not spaces else "ready"
        kind = "space_detail" if action == "get" else "space_collection"
        data: dict[str, Any] = attach_permission_bundle({
            "items": spaces,
            "count": result.get("count", len(spaces)),
        }, permission_bundle)
        if action == "get" and spaces:
            data["space"] = spaces[0]
        structured = build_tool_output(
            kind,
            2,
            state,
            data,
            actions=_spaces_actions(action, spaces[0].get("id") if spaces else None),
        )
    return widget_tool_result("spaces", action=action, content=result, structured_content=structured)


def _space_draft_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    if result.get("error"):
        message = str(result.get("detail") or result.get("error"))
        structured = build_tool_output(
            "space_collection",
            2,
            "error",
            attach_permission_bundle(
                {
                    "scope": "create_error",
                    "hint": message,
                    "error": result.get("error"),
                },
                permission_bundle,
            ),
        )
        structured["notice"] = build_notice(
            message,
            severity="error",
            code="space_draft_error",
        )
        return widget_tool_result(
            "spaces",
            action=action,
            content=result,
            structured_content=structured,
        )

    draft = _space_draft_to_widget_draft(result)
    created = _created_space_from_draft_result(result)
    if created:
        data = {
            "scope": "created",
            "created": created,
            "space": created,
            "items": [created],
            "count": 1,
            "draft": draft,
            "hint": "Space created.",
        }
        kind = "space_detail"
    elif action in {"reject_draft", "cancel_draft"} or result.get("status") in {"rejected", "cancelled"}:
        data = {
            "scope": "draft_dismissed",
            "draft": draft,
            "hint": "Draft dismissed.",
        }
        kind = "space_collection"
    else:
        data = {
            "scope": "create",
            "draft": draft,
            "required_fields": _SPACE_DRAFT_REQUIRED_FIELDS,
            "hint": "Review this draft before creating the space.",
        }
        kind = "space_collection"

    structured = build_tool_output(
        kind,
        2,
        "ready",
        attach_permission_bundle(data, permission_bundle),
    )
    return widget_tool_result(
        "spaces",
        action=action,
        content=result,
        structured_content=structured,
    )


def _spaces_blocked_result(
    action: str,
    *,
    permission_bundle: dict[str, Any],
    message: str,
) -> ToolResult:
    kind = "space_detail" if action == "update" else "space_collection"
    structured = build_tool_output(
        kind,
        2,
        "ready",
        attach_permission_bundle(
            {
                "items": [],
                "count": 0,
                "scope": f"{action}_blocked",
            },
            permission_bundle,
        ),
    )
    structured["notice"] = build_notice(
        message,
        severity="error",
        code=f"spaces_{action}_blocked",
    )
    return widget_tool_result(
        "spaces",
        action=action,
        content={"error": message},
        structured_content=structured,
    )


def register_spaces_tool(mcp: FastMCP):

    @mcp.tool(
        annotations=bounded_write_annotations(destructive=False),
        app=tool_app_config("spaces"),
        meta=tool_meta("spaces"),
        output_schema=tool_output_schema("spaces"),
    )
    async def spaces(
        action: Annotated[
            Literal[
                "list",
                "current",
                "switch",
                "get",
                "members",
                "create",
                "update",
                "create_draft",
                "get_draft",
                "edit_draft",
                "approve_draft",
                "reject_draft",
                "cancel_draft",
                "discover",
                "list_public",
                "join_public",
                "join_invite",
                "create_invite",
                "invite_details",
            ],
            Field(
                default="list",
                description=(
                    "Space action. Use list/current/get/members to read, "
                    "switch to change the active space, create/update or the "
                    "*_draft flow to manage spaces, and discover/join_public/"
                    "join_invite/create_invite/invite_details for membership."
                ),
            ),
        ] = "list",
        space_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Target space ID. Required for switch, members, update, "
                    "join_public, and create_invite; get accepts space_id or slug."
                ),
            ),
        ] = None,
        slug: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Space slug; alternative to space_id for action=get.",
            ),
        ] = None,
        name: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Space display name. Required for create/create_draft.",
            ),
        ] = None,
        description: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Space description shown to members and in discovery.",
            ),
        ] = None,
        visibility: Annotated[
            Optional[str],
            Field(
                default=None,
                description='Space visibility, e.g. "private" or "public".',
            ),
        ] = None,
        is_archived: Annotated[
            Optional[bool],
            Field(
                default=None,
                description="For action=update, archive (true) or unarchive (false) the space.",
            ),
        ] = None,
        space_mode: Annotated[
            Optional[Literal["personal", "team", "community"]],
            Field(
                default=None,
                description=(
                    "For create_draft: team for shared internal workspaces, "
                    "personal for a single-user workspace, community for public."
                ),
            ),
        ] = None,
        draft_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Draft ID. Required for get_draft, edit_draft, approve_draft, "
                    "reject_draft, and cancel_draft."
                ),
            ),
        ] = None,
        version: Annotated[
            Optional[int],
            Field(
                default=None,
                description="Draft version for optimistic-concurrency checks on edit_draft.",
            ),
        ] = None,
        changes: Annotated[
            Optional[dict[str, Any]],
            Field(
                default=None,
                description="For edit_draft, the draft fields to change as a partial object.",
            ),
        ] = None,
        idempotency_key: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Client-supplied key to make draft approval safe to retry.",
            ),
        ] = None,
        invite_code: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Invite code. Required for join_invite and invite_details.",
            ),
        ] = None,
        expires_hours: Annotated[
            Optional[int],
            Field(
                default=None,
                description="For create_invite, hours until the invite expires.",
            ),
        ] = None,
        max_uses: Annotated[
            Optional[int],
            Field(
                default=None,
                description="For create_invite, maximum number of times the invite can be used.",
            ),
        ] = None,
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> ToolResult | dict:
        """Navigate spaces, view members, and create or review space drafts.

        Actions:
        - list: List all spaces you belong to
        - current: Get the active space for this route/session
        - switch: Switch the active user or route-bound agent to a space (requires space_id)
        - get: Get space details (requires space_id or slug)
        - members: List members of a space (requires space_id)
        - create: Create a new space (requires name, optional description and visibility)
        - update: Update a space (requires space_id plus name, description, or is_archived)
        - create_draft: Create a reviewable space draft for HITL approval. Use
          space_mode=team for internal shared workspaces, personal only for a
          single-user private workspace, and community for public spaces.
        - get_draft: Refresh a persisted draft by id
        - edit_draft: Update editable draft fields before approval
        - approve_draft: Approve and execute a draft with the user's JWT
        - reject_draft/cancel_draft: Dismiss a draft without creating a space
        - discover/list_public: List public spaces the user can join
        - join_public: Join a public space (requires space_id)
        - join_invite: Join an invite-only/private team space by invite_code
        - create_invite: Generate an invite_code for a space (requires space_id)
        - invite_details: Preview invite_code details
        """
        ctx = extract_agent_context(token, request)
        permission_bundle = await resolve_space_scoped_permissions(ctx, "spaces")

        if action == "list":
            result = await _api_request_with_ctx(ctx, "GET", SPACES_COLLECTION_PATH)
            return _spaces_widget_result(result, action, permission_bundle=permission_bundle)

        if action == "current":
            current_space_id = _active_space_id(ctx, permission_bundle)
            if not current_space_id:
                return _space_current_widget_result(
                    {
                        "error": "No active space context is available for action=current",
                        "detail": "No active space context is available for action=current",
                    },
                    action,
                    permission_bundle=permission_bundle,
                )
            current_ctx = {**ctx, "space_id": current_space_id}
            result = await _api_request_with_ctx(current_ctx, "GET", space_item_path(current_space_id))
            return _space_current_widget_result(
                result,
                action,
                fallback_space_id=current_space_id,
                permission_bundle=permission_bundle,
            )

        if action == "switch":
            target_space_id = await _resolve_switch_space_id(ctx, space_id, slug)
            if not target_space_id:
                message = (
                    f"No space found with slug '{slug.strip()}'."
                    if slug and slug.strip()
                    else "space_id or slug is required for action=switch"
                )
                return _space_switch_widget_result(
                    {"error": message, "detail": message},
                    action,
                    target_space_id="",
                    permission_bundle=permission_bundle,
                )
            if ctx.get("principal_type") == "agent":
                result = await _switch_route_bound_agent(ctx, target_space_id)
            else:
                result = await _api_request_with_ctx(
                    ctx,
                    "POST",
                    SPACES_SWITCH_PATH,
                    json_data={"space_id": target_space_id},
                )
                if _is_missing_route_error(result):
                    logger.debug(
                        "Primary space switch route missing; falling back to legacy organization switch"
                    )
                    result = await _api_request_with_ctx(
                        ctx,
                        "POST",
                        "/api/organizations/switch",
                        json_data={"org_id": target_space_id},
                    )
            return _space_switch_widget_result(
                result,
                action,
                target_space_id=target_space_id,
                permission_bundle=permission_bundle,
            )

        if action == "get":
            if slug:
                spaces_result = await _api_request_with_ctx(ctx, "GET", SPACES_COLLECTION_PATH)
                if isinstance(spaces_result, dict) and spaces_result.get("error"):
                    result = spaces_result
                else:
                    result = _find_space_by_slug(spaces_result, slug) or {
                        "error": "Space not found",
                        "detail": f"No visible space found for slug '{slug}'",
                    }
            elif space_id:
                result = await _api_request_with_ctx(ctx, "GET", space_item_path(space_id))
            else:
                return {"error": "'space_id' or 'slug' required for get action"}
            return _spaces_widget_result(result, action, permission_bundle=permission_bundle)

        if action == "members":
            if not space_id:
                space_id = ctx.get("space_id")
            if not space_id:
                return {"error": "'space_id' required for members action"}
            result = await _api_request_with_ctx(ctx, "GET", space_members_path(space_id))
            if isinstance(result, dict) and "space_id" not in result:
                result = {**result, "space_id": space_id}
            return _spaces_widget_result(result, action, permission_bundle=permission_bundle)

        if action in {"discover", "list_public"}:
            result = await _api_request_with_ctx(ctx, "GET", "/api/spaces/public")
            return _space_discovery_widget_result(result, action, permission_bundle=permission_bundle)

        if action == "invite_details":
            if not invite_code:
                return {"error": "invite_code is required for action=invite_details"}
            result = await _api_request_with_ctx(ctx, "GET", f"/api/spaces/invites/{invite_code}")
            return _space_invite_widget_result(result, action, permission_bundle=permission_bundle)

        if action in _SPACE_JOIN_ACTIONS:
            if not permissions_allow(permission_bundle, "can_create"):
                return _spaces_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=blocked_reason(permission_bundle),
                )
            if action == "join_public":
                if not space_id:
                    return {"error": "space_id is required for action=join_public"}
                result = await _api_request_with_ctx(
                    ctx,
                    "POST",
                    "/api/spaces/join-public",
                    json_data={"space_id": space_id},
                )
                if _is_missing_route_error(result):
                    result = await _api_request_with_ctx(
                        ctx,
                        "POST",
                        "/api/organizations/join-public",
                        json_data={"org_id": space_id},
                    )
            else:
                if not invite_code:
                    return {"error": "invite_code is required for action=join_invite"}
                result = await _api_request_with_ctx(
                    ctx,
                    "POST",
                    "/api/spaces/join",
                    json_data={"invite_code": invite_code},
                )
            return _space_join_widget_result(result, action, permission_bundle=permission_bundle)

        if action == "create_invite":
            if not permissions_allow(permission_bundle, "can_update"):
                return _spaces_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=blocked_reason(permission_bundle),
                )
            if not space_id:
                return {"error": "space_id is required for action=create_invite"}
            body: dict[str, Any] = {}
            if expires_hours is not None:
                body["expires_hours"] = expires_hours
            if max_uses is not None:
                body["max_uses"] = max_uses
            result = await _api_request_with_ctx(
                ctx,
                "POST",
                f"/api/spaces/{space_id}/invites",
                json_data=body,
            )
            return _space_invite_widget_result(result, action, permission_bundle=permission_bundle)

        if action in _SPACE_DRAFT_ACTIONS:
            if not permissions_allow(permission_bundle, "can_use_hitl_approval"):
                return _spaces_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=blocked_reason(permission_bundle),
                )

            if action == "create_draft":
                space_name = (name or "").strip()
                if not space_name:
                    return {"error": "name is required for action=create_draft"}
                normalized_visibility = (visibility or "").strip().lower() or None
                mode = _space_mode_from_visibility(normalized_visibility, space_mode)
                if not normalized_visibility:
                    normalized_visibility = {
                        "personal": "private",
                        "team": "invite_only",
                        "community": "public",
                    }[mode]
                payload: dict[str, Any] = {
                    "space_mode": mode,
                    "space": {
                        "name": space_name,
                        "visibility": normalized_visibility,
                    },
                    "idempotency_key": idempotency_key,
                }
                if description is not None:
                    payload["space"]["description"] = description
                payload = {key: value for key, value in payload.items() if value is not None}
                result = await _api_request_with_ctx(
                    ctx,
                    "POST",
                    "/api/v1/drafts/spaces",
                    json_data=payload,
                )
                return _space_draft_widget_result(
                    result,
                    action,
                    permission_bundle=permission_bundle,
                )

            if not draft_id:
                return {"error": f"draft_id is required for action={action}"}

            if action == "get_draft":
                result = await _api_request_with_ctx(
                    ctx,
                    "GET",
                    f"/api/v1/drafts/{draft_id}",
                )
                return _space_draft_widget_result(
                    result,
                    action,
                    permission_bundle=permission_bundle,
                )

            if version is None:
                return {"error": f"version is required for action={action}"}

            if action == "edit_draft":
                patch_changes = changes if isinstance(changes, dict) else {}
                if not patch_changes:
                    return {"error": "changes is required for action=edit_draft"}
                result = await _api_request_with_ctx(
                    ctx,
                    "PATCH",
                    f"/api/v1/drafts/{draft_id}",
                    json_data={"version": version, "changes": patch_changes},
                )
                return _space_draft_widget_result(
                    result,
                    action,
                    permission_bundle=permission_bundle,
                )

            decision_path = {
                "approve_draft": "approve",
                "reject_draft": "reject",
                "cancel_draft": "cancel",
            }[action]
            result = await _api_request_with_ctx(
                ctx,
                "POST",
                f"/api/v1/drafts/{draft_id}/{decision_path}",
                json_data={"version": version},
            )
            return _space_draft_widget_result(
                result,
                action,
                permission_bundle=permission_bundle,
            )

        if action == "create":
            if not permissions_allow(permission_bundle, "can_create"):
                return _spaces_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=blocked_reason(permission_bundle),
                )
            if not name:
                return {"error": "'name' required for create action"}
            body: dict[str, Any] = {"name": name}
            if description:
                body["description"] = description
            if visibility:
                body["visibility"] = visibility
            result = await _api_request_with_ctx(
                ctx,
                "POST",
                "/api/spaces/create",
                json_data=body,
            )
            if isinstance(result, dict) and result.get("error"):
                return _spaces_widget_result(result, action)
            # Return the new space in the widget with a success notice
            spaces_list = _extract_space_items(result)
            structured = build_tool_output(
                "space_detail",
                2,
                "ready",
                attach_permission_bundle(
                    {"items": spaces_list, "count": 1, "space": spaces_list[0] if spaces_list else result},
                    permission_bundle,
                ),
                actions=_spaces_actions("get", spaces_list[0].get("id") if spaces_list else None),
            )
            structured["notice"] = build_notice(
                f'Space "{name}" created successfully.',
                code="space_created",
            )
            return widget_tool_result("spaces", action=action, content=result, structured_content=structured)

        if action == "update":
            if not permissions_allow(permission_bundle, "can_update"):
                return _spaces_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=blocked_reason(permission_bundle),
                )
            if not space_id:
                return {"error": "'space_id' required for update action"}

            body: dict[str, Any] = {}
            if name is not None:
                body["name"] = name
            if description is not None:
                body["description"] = description
            if is_archived is not None:
                body["is_archived"] = is_archived

            if not body:
                return {"error": "At least one of 'name', 'description', or 'is_archived' is required for update action"}

            result = await _api_request_with_ctx(
                ctx,
                "PUT",
                f"/api/spaces/{space_id}",
                json_data=body,
            )
            if isinstance(result, dict) and result.get("error"):
                return _spaces_widget_result(result, action)

            spaces_list = _extract_space_items(result)
            updated_name = name or (spaces_list[0].get("name") if spaces_list else None) or "Space"
            structured = build_tool_output(
                "space_detail",
                2,
                "ready",
                attach_permission_bundle(
                    {"items": spaces_list, "count": 1, "space": spaces_list[0] if spaces_list else result},
                    permission_bundle,
                ),
                actions=_spaces_actions("get", spaces_list[0].get("id") if spaces_list else space_id),
            )
            structured["notice"] = build_notice(
                f'Space "{updated_name}" saved.',
                code="space_updated",
            )
            return widget_tool_result("spaces", action=action, content=result, structured_content=structured)

        return {
            "error": (
                "Unknown action: "
                f"{action}. Available: list, current, switch, get, members, create, update, "
                "create_draft, get_draft, edit_draft, approve_draft, reject_draft, cancel_draft, "
                "discover, list_public, join_public, join_invite, create_invite, invite_details"
            )
        }
