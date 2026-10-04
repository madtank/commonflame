"""Whoami tool for FastMCP server.

Provides user/agent identity management and memory.
All operations route through backend API (ROUTER-001).
Uses CurrentAccessToken() DI to forward backend-issued JWTs.
"""

import copy
import logging
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.tools import ToolResult
from pydantic import Field
from starlette.requests import Request

from fastmcp_server.api_client import api_request, extract_agent_context
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
from fastmcp_server.tools.avatar import normalize_avatar as _normalize_avatar
from fastmcp_server.space_scoped_permissions import (
    attach_permission_bundle,
    permissions_allow,
    resolve_space_scoped_permissions,
)

logger = logging.getLogger(__name__)
# MCP agent-targeting now relies on backend header-aware resolution rather than
# a separate internal route. Keep whoami on the public v1 surface so dev/shared,
# next, and tests all exercise the same contract.
_WHOAMI_BASE_PATH = "/api/v1/agents/me"
_AUTH_ME_PATH = "/auth/me"
_SETTINGS_PATH = "/api/v1/settings"
_USER_PROFILE_NAMESPACE = "mcp_identity"


def _has_api_error(result: Any) -> bool:
    return isinstance(result, dict) and bool(result.get("error"))


def _is_agent_principal(ctx: dict[str, Any]) -> bool:
    return ctx.get("principal_type") == "agent"


def _normalize_memory_items(result: dict[str, Any] | list[Any]) -> list[dict[str, Any] | str]:
    """Normalize backend memory list responses into widget-friendly items."""
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []

    memories = result.get("memories")
    if isinstance(memories, list):
        return memories

    items = result.get("items")
    if isinstance(items, list):
        return items

    keys = result.get("keys")
    if isinstance(keys, list):
        return [{"key": key} if isinstance(key, str) else key for key in keys]

    if isinstance(result, list):
        return result

    return []


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_identity_settings(
    settings_result: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    custom = copy.deepcopy(settings_result.get("custom")) if isinstance(settings_result, dict) else {}
    if not isinstance(custom, dict):
        custom = {}

    identity_settings = custom.get(_USER_PROFILE_NAMESPACE)
    if not isinstance(identity_settings, dict):
        identity_settings = {}

    profile = identity_settings.get("profile")
    if not isinstance(profile, dict):
        profile = {}

    memories = identity_settings.get("memories")
    if not isinstance(memories, dict):
        memories = {}

    return custom, identity_settings, profile, memories


def _normalize_user_memory_items(memories: Any) -> list[dict[str, Any]]:
    if not isinstance(memories, dict):
        return []

    items: list[dict[str, Any]] = []
    for key, value in memories.items():
        if not isinstance(key, str) or not key.strip():
            continue
        item: dict[str, Any] = {"key": key.strip()}
        if isinstance(value, dict):
            if "value" in value:
                item["value"] = value.get("value")
            created_at = _first_non_empty_string(value.get("created_at"))
            updated_at = _first_non_empty_string(value.get("updated_at"), created_at)
            if created_at:
                item["created_at"] = created_at
            if updated_at:
                item["updated_at"] = updated_at
        else:
            item["value"] = value
        items.append(item)

    items.sort(
        key=lambda item: (
            str(item.get("updated_at") or item.get("created_at") or ""),
            item.get("key") or "",
        ),
        reverse=True,
    )
    return items


def _identity_payload_from_settings(
    settings_result: dict[str, Any] | None,
) -> dict[str, Any]:
    _, _, profile, memories = _extract_identity_settings(settings_result)
    items = _normalize_user_memory_items(memories)

    updated_candidates = [
        _first_non_empty_string(item.get("updated_at"), item.get("created_at"))
        for item in items
        if isinstance(item, dict)
    ]
    updated_at = next((value for value in updated_candidates if value), None)

    capabilities = profile.get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = []

    return {
        "bio": _first_non_empty_string(profile.get("bio")),
        "specialization": _first_non_empty_string(profile.get("specialization")),
        "preferences": _first_non_empty_string(profile.get("preferences")),
        "projects": _first_non_empty_string(profile.get("projects")),
        "avatar_url": _first_non_empty_string(profile.get("avatar_url")),
        "capabilities": capabilities,
        "memory_items": items,
        "memory_count": len(items),
        "memory_updated_at": updated_at,
    }


async def _load_user_settings(ctx: dict[str, Any]) -> dict[str, Any]:
    settings = await api_request(
        "GET",
        _SETTINGS_PATH,
        ctx["jwt"],
        agent_name=ctx.get("agent_name"),
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
        delegation_mode=ctx.get("delegation_mode"),
        delegated_for=ctx.get("delegated_for"),
    )
    if not isinstance(settings, dict):
        return {}
    return settings


async def _patch_user_settings_custom(
    ctx: dict[str, Any],
    custom: dict[str, Any],
) -> dict[str, Any]:
    return await api_request(
        "PATCH",
        _SETTINGS_PATH,
        ctx["jwt"],
        json_data={"custom": custom},
        agent_name=ctx.get("agent_name"),
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
        delegation_mode=ctx.get("delegation_mode"),
        delegated_for=ctx.get("delegated_for"),
    )


def _first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def _humanize_label(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        return None
    return cleaned[:1].upper() + cleaned[1:]


def _normalize_handle(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lstrip("@")
    return cleaned or None


def _status_label(agent: dict[str, Any]) -> str:
    raw = _first_non_empty_string(
        agent.get("status"),
        agent.get("presence"),
        agent.get("availability"),
    )
    if raw:
        lowered = raw.lower().replace(" ", "_")
        if lowered in {"online", "active"}:
            return "active"
        if lowered in {"offline"}:
            return "offline"
        if lowered in {"idle", "away"}:
            return "idle"
        return lowered

    if agent.get("is_online") is True:
        return "active"
    if agent.get("is_online") is False:
        return "offline"
    return "unknown"


def _normalize_capabilities(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, str]] = []
    for item in value:
        key: str | None = None
        label: str | None = None
        if isinstance(item, str):
            key = item.strip()
            label = _humanize_label(key)
        elif isinstance(item, dict):
            key = _first_non_empty_string(item.get("key"), item.get("name"), item.get("id"), item.get("slug"))
            label = _first_non_empty_string(
                item.get("label"),
                item.get("title"),
                _humanize_label(key),
            )
        if key and label:
            normalized.append({"key": key, "label": label})
    return normalized


def _normalize_owner(agent: dict[str, Any]) -> dict[str, str] | None:
    agent_type = str(agent.get("agent_type") or agent.get("type") or agent.get("role") or "").strip().lower()
    origin = str(agent.get("origin") or "").strip().lower()
    role_label = str(agent.get("role_label") or "").strip().lower()
    template = str(agent.get("template_type") or "").strip().lower()
    if (
        agent_type in {"space_agent", "space-agent"}
        or origin == "space_agent"
        or role_label == "space agent"
        or template == "space_agent"
    ):
        return {"handle": "space", "display_name": "Space agent"}

    owner = agent.get("owner")
    if isinstance(owner, dict):
        owner_handle = _normalize_handle(
            _first_non_empty_string(owner.get("handle"), owner.get("username"), owner.get("login"))
        )
        owner_name = _first_non_empty_string(owner.get("display_name"), owner.get("name"))
        owner_id = _first_non_empty_string(owner.get("id"))
        if owner_handle or owner_name or owner_id:
            payload: dict[str, str] = {}
            if owner_id:
                payload["id"] = owner_id
            if owner_handle:
                payload["handle"] = owner_handle
            if owner_name:
                payload["display_name"] = owner_name
            return payload

    owner_handle = _normalize_handle(
        _first_non_empty_string(
            agent.get("owner_handle"),
            agent.get("owner_username"),
            agent.get("owner_login"),
        )
    )
    owner_name = _first_non_empty_string(agent.get("owner_display_name"), agent.get("owner_name"))
    owner_id = _first_non_empty_string(agent.get("owner_id"))
    if owner_handle or owner_name or owner_id:
        payload = {}
        if owner_id:
            payload["id"] = owner_id
        if owner_handle:
            payload["handle"] = owner_handle
        if owner_name:
            payload["display_name"] = owner_name
        return payload
    return None




def _normalize_delegation(result: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any] | None:
    source = result if isinstance(result, dict) else {}
    delegation = source.get("delegation")
    if not isinstance(delegation, dict):
        delegation = {}

    mode = _first_non_empty_string(
        delegation.get("mode"),
        source.get("delegation_mode"),
        agent.get("delegation_mode"),
    )
    if mode != "home_space":
        return None

    payload: dict[str, Any] = {"mode": mode}

    home_space = delegation.get("home_space")
    if not isinstance(home_space, dict):
        home_space = {}
    home_space_id = _first_non_empty_string(
        home_space.get("id"),
        source.get("delegation_home_space_id"),
        agent.get("delegation_home_space_id"),
        source.get("home_space_id"),
        agent.get("home_space_id"),
    )
    home_space_name = _first_non_empty_string(
        home_space.get("name"),
        source.get("delegation_home_space_name"),
        agent.get("delegation_home_space_name"),
        source.get("home_space_name"),
        agent.get("home_space_name"),
    )
    if home_space_id or home_space_name:
        payload["home_space"] = {}
        if home_space_id:
            payload["home_space"]["id"] = home_space_id
        if home_space_name:
            payload["home_space"]["name"] = home_space_name

    delegated_by = delegation.get("delegated_by")
    if not isinstance(delegated_by, dict):
        delegated_by = {}
    delegated_by_handle = _normalize_handle(
        _first_non_empty_string(
            delegated_by.get("handle"),
            source.get("delegated_by_handle"),
            agent.get("delegated_by_handle"),
        )
    )
    delegated_by_name = _first_non_empty_string(
        delegated_by.get("display_name"),
        delegated_by.get("name"),
        source.get("delegated_by_name"),
        agent.get("delegated_by_name"),
    )
    if delegated_by_handle or delegated_by_name:
        payload["delegated_by"] = {}
        if delegated_by_handle:
            payload["delegated_by"]["handle"] = delegated_by_handle
        if delegated_by_name:
            payload["delegated_by"]["display_name"] = delegated_by_name

    return payload

def _normalize_memory_summary(agent: dict[str, Any]) -> dict[str, Any]:
    count = agent.get("memory_count")
    if not isinstance(count, int):
        raw_count = agent.get("memories_count")
        count = raw_count if isinstance(raw_count, int) else None

    featured_raw = agent.get("featured_memories")
    featured: list[dict[str, str]] = []
    if isinstance(featured_raw, list):
        for item in featured_raw:
            if isinstance(item, str) and item.strip():
                featured.append({"key": item.strip(), "label": _humanize_label(item.strip()) or item.strip()})
            elif isinstance(item, dict):
                key = _first_non_empty_string(item.get("key"), item.get("name"))
                label = _first_non_empty_string(item.get("label"), _humanize_label(key))
                if key and label:
                    featured.append({"key": key, "label": label})

    summary = f"{count} memories" if isinstance(count, int) else "Memory available"
    payload: dict[str, Any] = {"summary": summary}
    if featured:
        payload["featured"] = featured[:3]

    updated_at = _first_non_empty_string(
        agent.get("memory_updated_at"),
        agent.get("updated_at"),
    )
    if updated_at:
        payload["updated_at"] = updated_at

    raw_items = agent.get("memory_items")
    items = _normalize_memory_items({"memories": raw_items}) if isinstance(raw_items, list) else []
    if items:
        payload["items"] = items
        if not isinstance(count, int):
            payload["summary"] = f"{len(items)} memories"
    return payload


def _normalize_identity_result(result: dict[str, Any]) -> dict[str, Any]:
    principal = result.get("agent") if isinstance(result, dict) else {}
    if not isinstance(principal, dict):
        principal = result.get("user") if isinstance(result, dict) else {}
    if not isinstance(principal, dict):
        principal = result if isinstance(result, dict) else {}

    principal_kind = _first_non_empty_string(
        result.get("principal_kind"),
        "user" if result.get("email") else None,
    ) or "agent"

    handle = _normalize_handle(
        _first_non_empty_string(
            principal.get("handle"),
            principal.get("username"),
            principal.get("login"),
            principal.get("agent_name"),
            principal.get("name"),
        )
    )
    display_name = _first_non_empty_string(
        principal.get("display_name"),
        principal.get("full_name"),
        principal.get("name"),
        principal.get("username"),
    )
    default_role = "User" if principal_kind == "user" else "Agent"
    role_label = _humanize_label(
        _first_non_empty_string(
            principal.get("role_label"),
            principal.get("agent_type"),
            principal.get("type"),
            principal.get("role"),
        )
    ) or default_role

    identity: dict[str, Any] = {
        "principal_kind": principal_kind,
        "role_label": role_label,
        "status": _status_label(principal) if principal_kind == "agent" else "active",
    }
    principal_id = _first_non_empty_string(principal.get("id"))
    if principal_id:
        identity["id"] = principal_id
    if handle:
        identity["handle"] = handle
    if display_name:
        identity["display_name"] = display_name
    email = _first_non_empty_string(principal.get("email"))
    if email:
        identity["email"] = email
    specialization = _first_non_empty_string(principal.get("specialization"))
    if specialization:
        identity["specialization"] = specialization
    preferences = _first_non_empty_string(principal.get("preferences"))
    if preferences:
        identity["preferences"] = preferences
    projects = _first_non_empty_string(principal.get("projects"))
    if projects:
        identity["projects"] = projects
    bio = _first_non_empty_string(principal.get("bio"), principal.get("description"))
    if bio:
        identity["bio"] = bio
    avatar_url = _first_non_empty_string(principal.get("avatar_url"), principal.get("avatar"))
    if avatar_url is not None:
        identity["avatar_url"] = avatar_url

    context: dict[str, Any] = {}
    workspace = principal.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
    workspace_id = _first_non_empty_string(
        workspace.get("id"),
        principal.get("workspace_id"),
        principal.get("space_id"),
    )
    workspace_name = _first_non_empty_string(
        workspace.get("name"),
        principal.get("workspace_name"),
        principal.get("space_name"),
        principal.get("workspace"),
        principal.get("space"),
    )
    if workspace_id:
        context["workspace_id"] = workspace_id
    if workspace_name:
        context["workspace_name"] = workspace_name
    if principal_kind == "user":
        context["binding_label"] = "Current space"
    elif role_label.lower() == "space agent":
        context["binding_label"] = "Bound space"
    owner = _normalize_owner(principal)
    if owner:
        context["owner"] = owner

    bound_agent = principal.get("bound_agent")
    if isinstance(bound_agent, dict):
        bound_agent_name = _first_non_empty_string(bound_agent.get("agent_name"))
        if bound_agent_name:
            context["bound_agent"] = {"handle": bound_agent_name}

    raw_capabilities = principal.get("capabilities")
    if raw_capabilities is None and isinstance(result, dict):
        raw_capabilities = result.get("capabilities")

    data: dict[str, Any] = {
        "identity": identity,
        "context": context,
        "capabilities": _normalize_capabilities(raw_capabilities),
        "memory": _normalize_memory_summary(principal),
    }
    delegation = _normalize_delegation(result, principal)
    if delegation:
        data["delegation"] = delegation
    return data


def _identity_fallback_text(data: dict[str, Any]) -> str:
    identity = data.get("identity", {})
    context = data.get("context", {})
    handle = identity.get("handle")
    display_name = identity.get("display_name")
    role_label = identity.get("role_label")
    workspace_name = context.get("workspace_name")
    primary = display_name or (f"@{handle}" if handle else role_label or "Agent")
    parts = [str(primary)]
    if role_label and role_label != primary:
        parts.append(str(role_label))
    if workspace_name:
        parts.append(f"in {workspace_name}")
    elif context.get("workspace_id"):
        parts.append(f"bound to space {context['workspace_id']}")
    return " ".join(parts)


def _whoami_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    notice: dict[str, Any] | None = None,
    highlighted_key: str | None = None,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    """Build a widget-aware ToolResult for the whoami tool."""
    if action in {"get", "update", "list", "remember", "recall"} and isinstance(result, dict) and not result.get("error"):
        data = attach_permission_bundle(
            _normalize_identity_result(result),
            permission_bundle,
        )
        structured = build_tool_output(
            "whoami_profile",
            2,
            "ready",
            data,
            actions=[
                build_action(
                    "edit-profile",
                    "Edit profile",
                    kind="tool",
                    target="whoami",
                    args={"action": "update"},
                    style="primary",
                    enabled=True,
                    idempotent=True,
                ),
                build_action(
                    "view-memory",
                    "View memory",
                    kind="tool",
                    target="whoami",
                    args={"action": "list"},
                    style="secondary",
                    enabled=True,
                    idempotent=True,
                ),
            ],
        )
        structured["active_tab"] = "memories" if action in {"list", "remember", "recall"} else "profile"
        if highlighted_key:
            structured["highlighted_key"] = highlighted_key
        if notice:
            structured["notice"] = notice
        return widget_tool_result(
            "whoami",
            content=_identity_fallback_text(data),
            structured_content=structured,
        )

    structured: dict[str, Any] = {"action": action}
    if action == "list":
        structured["memories"] = _normalize_memory_items(result)
        structured["count"] = result.get("count", len(structured["memories"]))
    elif action == "recall":
        structured.update(result if isinstance(result, dict) else {"value": result})
    else:
        structured.update(result if isinstance(result, dict) else {})

    content = result.get("error") if isinstance(result, dict) and result.get("error") else result
    return widget_tool_result("whoami", content=content, structured_content=structured)


def _blocked_reason(permission_bundle: dict[str, Any]) -> str:
    permissions = permission_bundle.get("permissions")
    if isinstance(permissions, dict):
        reason = permissions.get("blocked_reason")
        if isinstance(reason, str) and reason.strip():
            return reason
    return "This identity action is not available in the current space."


async def _load_memory_snapshot(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Load current memory keys so the widget can render without a follow-up call."""
    result = await api_request(
        "GET",
        f"{_WHOAMI_BASE_PATH}/memory",
        ctx["jwt"],
        params={"include_values": "true"},
        agent_name=ctx["agent_name"],
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
    )
    if _has_api_error(result):
        logger.info(
            "WHOAMI_MEMORY_PUBLIC_FALLBACK_FAILED agent=%s space=%s detail=%s",
            ctx.get("agent_name"),
            ctx.get("space_id"),
            result.get("detail"),
        )
        return []
    return _normalize_memory_items(result)


async def _build_user_identity_payload(
    ctx: dict[str, Any],
    *,
    include_memory: bool = False,
) -> dict[str, Any]:
    identity = await api_request(
        "GET",
        _AUTH_ME_PATH,
        ctx["jwt"],
        agent_name=ctx.get("agent_name"),
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
        delegation_mode=ctx.get("delegation_mode"),
        delegated_for=ctx.get("delegated_for"),
    )
    if not isinstance(identity, dict):
        identity = {}

    settings = await _load_user_settings(ctx)
    if _has_api_error(settings):
        logger.info(
            "WHOAMI_USER_SETTINGS_FALLBACK_FAILED space=%s detail=%s",
            ctx.get("space_id"),
            settings.get("detail"),
        )
        settings = {}

    profile = _identity_payload_from_settings(settings)
    memory_items = profile.get("memory_items") if include_memory else []

    bound_agent = identity.get("bound_agent")
    if not isinstance(bound_agent, dict):
        bound_agent = {}

    workspace: dict[str, Any] = {}
    workspace_id = _first_non_empty_string(
        bound_agent.get("default_space_id"),
        ctx.get("space_id"),
    )
    workspace_name = _first_non_empty_string(bound_agent.get("default_space_name"))
    if workspace_id:
        workspace["id"] = workspace_id
    if workspace_name:
        workspace["name"] = workspace_name

    payload: dict[str, Any] = {
        "principal_kind": "user",
        "id": _first_non_empty_string(identity.get("id")),
        "display_name": _first_non_empty_string(identity.get("full_name"), identity.get("username"), "User"),
        "full_name": _first_non_empty_string(identity.get("full_name")),
        "username": _first_non_empty_string(identity.get("username")),
        "handle": _first_non_empty_string(identity.get("username")),
        "email": _first_non_empty_string(identity.get("email")),
        "role_label": "User",
        "bio": profile.get("bio"),
        "specialization": profile.get("specialization"),
        "preferences": profile.get("preferences"),
        "projects": profile.get("projects"),
        "avatar_url": profile.get("avatar_url"),
        "capabilities": profile.get("capabilities") or [],
        "workspace": workspace,
        "memory_count": profile.get("memory_count"),
        "memory_updated_at": profile.get("memory_updated_at"),
    }
    if include_memory:
        payload["memory_items"] = memory_items
    if bound_agent:
        payload["bound_agent"] = bound_agent
    return payload


async def _update_user_profile_settings(
    ctx: dict[str, Any],
    *,
    bio: str | None = None,
    specialization: str | None = None,
    capabilities: list[Any] | None = None,
    preferences: str | None = None,
    projects: str | None = None,
    avatar_url: str | None = None,
) -> dict[str, Any]:
    settings = await _load_user_settings(ctx)
    if _has_api_error(settings):
        return settings

    custom, identity_settings, profile, memories = _extract_identity_settings(settings)

    def assign_text(field: str, value: str | None) -> None:
        if value is None:
            return
        cleaned = value.strip()
        if cleaned:
            profile[field] = cleaned
        else:
            profile.pop(field, None)

    assign_text("bio", bio)
    assign_text("specialization", specialization)
    assign_text("preferences", preferences)
    assign_text("projects", projects)
    if avatar_url is not None:
        if avatar_url:
            profile["avatar_url"] = avatar_url
        else:
            profile.pop("avatar_url", None)

    if capabilities is not None:
        cleaned_caps = []
        for item in capabilities:
            if isinstance(item, str) and item.strip():
                cleaned_caps.append(item.strip())
        if cleaned_caps:
            profile["capabilities"] = cleaned_caps
        else:
            profile.pop("capabilities", None)

    identity_settings["profile"] = profile
    identity_settings["memories"] = memories
    custom[_USER_PROFILE_NAMESPACE] = identity_settings

    return await _patch_user_settings_custom(ctx, custom)


async def _store_user_memory(
    ctx: dict[str, Any],
    *,
    key: str,
    value: str,
) -> dict[str, Any]:
    settings = await _load_user_settings(ctx)
    if _has_api_error(settings):
        return settings

    custom, identity_settings, profile, memories = _extract_identity_settings(settings)
    previous = memories.get(key)
    created_at = _first_non_empty_string(previous.get("created_at")) if isinstance(previous, dict) else None
    now = _iso_now()
    memories[key] = {
        "value": value,
        "created_at": created_at or now,
        "updated_at": now,
    }
    identity_settings["profile"] = profile
    identity_settings["memories"] = memories
    custom[_USER_PROFILE_NAMESPACE] = identity_settings
    return await _patch_user_settings_custom(ctx, custom)


async def _recall_user_memory(
    ctx: dict[str, Any],
    *,
    key: str,
) -> dict[str, Any]:
    settings = await _load_user_settings(ctx)
    if _has_api_error(settings):
        return settings

    _, _, _, memories = _extract_identity_settings(settings)
    if key not in memories:
        return {"error": f"Memory key '{key}' not found"}
    memory = memories[key]
    value = memory.get("value") if isinstance(memory, dict) else memory
    return {"ok": True, "key": key, "value": value}


async def _build_identity_payload(
    ctx: dict[str, Any],
    *,
    include_memory: bool = False,
) -> dict[str, Any]:
    """Build a widget-friendly identity payload from the agent-aware internal API."""
    if not _is_agent_principal(ctx):
        return await _build_user_identity_payload(ctx, include_memory=include_memory)

    identity = await api_request(
        "GET",
        _WHOAMI_BASE_PATH,
        ctx["jwt"],
        agent_name=ctx["agent_name"],
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
    )
    if not isinstance(identity, dict):
        identity = {}

    if include_memory and isinstance(identity, dict) and not identity.get("error"):
        identity["memory_items"] = await _load_memory_snapshot(ctx)
    return identity


def register_whoami_tool(mcp: FastMCP):
    """Register the whoami tool with the FastMCP server."""

    @mcp.tool(
        annotations=bounded_write_annotations(),
        app=tool_app_config("whoami"),
        meta=tool_meta("whoami"),
        output_schema=tool_output_schema("whoami"),
    )
    async def whoami(
        action: Annotated[
            Literal["get", "update", "remember", "recall", "list", "follow", "unfollow"],
            Field(
                default="get",
                description=(
                    "Identity action. Use get first to read the current caller's "
                    "identity card. Use update for profile fields, remember/list/"
                    "recall for memory, and follow/unfollow for agent relationships."
                ),
            ),
        ] = "get",
        bio: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=update, replace the caller's public identity bio.",
            ),
        ] = None,
        specialization: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=update, describe the caller's specialization or role.",
            ),
        ] = None,
        capabilities: Annotated[
            Optional[list[str]],
            Field(
                default=None,
                description=(
                    "For action=update, list concrete capabilities the caller "
                    "wants other agents or users to see."
                ),
            ),
        ] = None,
        preferences: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=update, store workflow or collaboration preferences.",
            ),
        ] = None,
        projects: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=update, summarize active projects or ownership areas.",
            ),
        ] = None,
        avatar_url: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "For action=update, set the caller's avatar as an https URL, "
                    'data:image URI, or raw <svg> markup (auto-wrapped); pass "" to clear.'
                ),
            ),
        ] = None,
        avatar_emoji: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    'For action=update, set the caller\'s avatar as a single emoji '
                    '(e.g. "🍑"), rendered to an inline SVG — no image hosting needed.'
                ),
            ),
        ] = None,
        key: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=remember or recall, the memory key to store or retrieve.",
            ),
        ] = None,
        value: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=remember, the memory value to save under key.",
            ),
        ] = None,
        target_agent: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "For action=follow or unfollow, the target agent handle or "
                    "agent name, with or without a leading @."
                ),
            ),
        ] = None,
        relationship_type: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "For action=follow, optional relationship label such as "
                    "teammate, reviewer, or maintainer."
                ),
            ),
        ] = None,
        space_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Accepted for MCP client compatibility, but the active "
                    "space is resolved from the authenticated session/JWT."
                ),
            ),
        ] = None,
        # DI params (hidden from MCP schema):
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> ToolResult:
        """Manage the caller's Commonflame identity, memory, and relationships.

        Use `get` first for a read-only identity card. Write actions are
        bounded to authenticated Commonflame identity state and remain permission-gated:
        `update` uses profile fields including caller avatar_url/avatar_emoji,
        `remember` requires `key` and `value`, `recall` requires `key`, and
        `follow`/`unfollow` require `target_agent`.
        """
        ctx = extract_agent_context(token, request)
        permission_bundle = await resolve_space_scoped_permissions(ctx, "whoami")

        if action == "get":
            result = await _build_identity_payload(ctx, include_memory=True)
            return _whoami_widget_result(result, action, permission_bundle=permission_bundle)

        if action == "update":
            if not permissions_allow(permission_bundle, "can_update"):
                result = await _build_identity_payload(ctx, include_memory=True)
                return _whoami_widget_result(
                    result,
                    action,
                    notice=build_notice(
                        _blocked_reason(permission_bundle),
                        severity="error",
                        code="identity_update_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )
            try:
                normalized_avatar = _normalize_avatar(avatar_url, avatar_emoji)
            except ValueError as exc:
                return _whoami_widget_result({"error": str(exc)}, action)

            has_updates = any(
                field is not None
                for field in (bio, specialization, capabilities, preferences, projects, normalized_avatar)
            )
            if not has_updates:
                return _whoami_widget_result(
                    {
                        "error": (
                            "No fields to update. Provide at least one of: bio, "
                            "specialization, capabilities, preferences, projects, "
                            "avatar_url, avatar_emoji"
                        )
                    },
                    action,
                )

            if _is_agent_principal(ctx):
                payload = {}
                if bio is not None:
                    payload["bio"] = bio
                if specialization is not None:
                    payload["specialization"] = specialization
                if capabilities is not None:
                    payload["capabilities"] = capabilities
                if normalized_avatar is not None:
                    payload["avatar_url"] = normalized_avatar
                if preferences is not None or projects is not None:
                    return _whoami_widget_result(
                        {
                            "error": (
                                "Preferences and projects are only available on the "
                                "user profile surface right now."
                            )
                        },
                        action,
                    )

                result = await api_request(
                    "PATCH",
                    _WHOAMI_BASE_PATH,
                    ctx["jwt"],
                    json_data=payload,
                    agent_name=ctx["agent_name"],
                    agent_id=ctx.get("agent_id"),
                    space_id=ctx["space_id"],
                    delegation_mode=ctx.get("delegation_mode"),
                    delegated_for=ctx.get("delegated_for"),
                )
            else:
                result = await _update_user_profile_settings(
                    ctx,
                    bio=bio,
                    specialization=specialization,
                    capabilities=capabilities,
                    preferences=preferences,
                    projects=projects,
                    avatar_url=normalized_avatar,
                )
            if not result.get("error"):
                result = await _build_identity_payload(ctx, include_memory=True)
            return _whoami_widget_result(
                result,
                action,
                notice=build_notice("Profile updated.", code="profile_updated"),
                permission_bundle=permission_bundle,
            )

        if action == "remember":
            if not permissions_allow(permission_bundle, "can_write_memory"):
                result = await _build_identity_payload(ctx, include_memory=True)
                return _whoami_widget_result(
                    result,
                    action,
                    notice=build_notice(
                        _blocked_reason(permission_bundle),
                        severity="error",
                        code="identity_memory_blocked",
                    ),
                    highlighted_key=key,
                    permission_bundle=permission_bundle,
                )
            if not key or not value:
                return _whoami_widget_result({"error": "Both 'key' and 'value' required for remember action"}, action)
            if _is_agent_principal(ctx):
                result = await api_request(
                    "POST",
                    f"{_WHOAMI_BASE_PATH}/memory",
                    ctx["jwt"],
                    json_data={"key": key, "value": value},
                    agent_name=ctx["agent_name"],
                    agent_id=ctx.get("agent_id"),
                    space_id=ctx["space_id"],
                )
            else:
                result = await _store_user_memory(ctx, key=key, value=value)
            if not result.get("error"):
                result = await _build_identity_payload(ctx, include_memory=True)
            return _whoami_widget_result(
                result,
                action,
                notice=build_notice(f'Saved memory "{key}".', code="memory_saved"),
                highlighted_key=key,
                permission_bundle=permission_bundle,
            )

        if action == "recall":
            if not key:
                return _whoami_widget_result({"error": "'key' required for recall action"}, action)
            if _is_agent_principal(ctx):
                result = await api_request(
                    "GET",
                    f"{_WHOAMI_BASE_PATH}/memory/{key}",
                    ctx["jwt"],
                    agent_name=ctx["agent_name"],
                    agent_id=ctx.get("agent_id"),
                    space_id=ctx["space_id"],
                )
            else:
                result = await _recall_user_memory(ctx, key=key)
            if not result.get("error"):
                result = await _build_identity_payload(ctx, include_memory=True)
            return _whoami_widget_result(
                result,
                action,
                highlighted_key=key,
                permission_bundle=permission_bundle,
            )

        if action == "list":
            result = await _build_identity_payload(ctx, include_memory=True)
            return _whoami_widget_result(result, action, permission_bundle=permission_bundle)

        if action == "follow":
            if not permissions_allow(permission_bundle, "can_update"):
                result = await _build_identity_payload(ctx, include_memory=True)
                return _whoami_widget_result(
                    result,
                    "get",
                    notice=build_notice(
                        _blocked_reason(permission_bundle),
                        severity="error",
                        code="identity_follow_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )
            if not _is_agent_principal(ctx):
                return _whoami_widget_result({"error": "Follow requires an agent identity."}, action)
            if not target_agent:
                return _whoami_widget_result({"error": "'target_agent' required for follow action"}, action)
            payload = {"target_agent": target_agent}
            if relationship_type:
                payload["relationship_type"] = relationship_type
            result = await api_request(
                "POST",
                f"{_WHOAMI_BASE_PATH}/follow",
                ctx["jwt"],
                json_data=payload,
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            return _whoami_widget_result(result, action, permission_bundle=permission_bundle)

        if action == "unfollow":
            if not permissions_allow(permission_bundle, "can_update"):
                result = await _build_identity_payload(ctx, include_memory=True)
                return _whoami_widget_result(
                    result,
                    "get",
                    notice=build_notice(
                        _blocked_reason(permission_bundle),
                        severity="error",
                        code="identity_unfollow_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )
            if not _is_agent_principal(ctx):
                return _whoami_widget_result({"error": "Unfollow requires an agent identity."}, action)
            if not target_agent:
                return _whoami_widget_result({"error": "'target_agent' required for unfollow action"}, action)
            result = await api_request(
                "DELETE",
                f"{_WHOAMI_BASE_PATH}/follow/{target_agent}",
                ctx["jwt"],
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            return _whoami_widget_result(result, action, permission_bundle=permission_bundle)

        return _whoami_widget_result(
            {
                "error": (
                    f"Unknown action: {action}. "
                    "Available: get, update, remember, recall, list, follow, unfollow"
                )
            },
            action,
        )
