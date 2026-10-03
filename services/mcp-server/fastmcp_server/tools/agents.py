"""Agents tool for FastMCP server.

Provides agent discovery and bounded profile updates.
All operations route through backend API (ROUTER-001).
Uses CurrentAccessToken() DI to forward backend-issued JWTs.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Optional

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.tools.tool import ToolResult
from starlette.requests import Request

from fastmcp_server.api_client import (
    api_request_with_context as _api_request_with_ctx,
    extract_agent_context,
)
from fastmcp_server.backend_routes import SPACES_COLLECTION_PATH
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
from fastmcp_server.tools.agent_groups import agent_groups_action
from fastmcp_server.space_scoped_permissions import (
    attach_permission_bundle,
    blocked_reason,
    permissions_allow,
    resolve_space_scoped_permissions,
)

logger = logging.getLogger(__name__)

# Phase 1 fallback: maps backend's 3-state presence to availability confidence.
# Used when GET /agents/availability is unavailable.
_PRESENCE_TO_CONFIDENCE = {
    "connected": "high",
    "recent": "medium",
    "offline": "offline",
}

_CONTROL_LABELS = {
    "active": "Active",
    "break": "Break",
    "disabled": "Disabled",
}

_CONTROL_REASONS = {
    "break": "Take a timed break so the agent will not receive or answer messages until the timer expires.",
    "disabled": "Disable the agent until someone explicitly re-enables it.",
    "active": "The agent is available to receive and respond to messages.",
}

_CONTROL_ACTIONS = {"disable", "enable", "set_control"}
_PLACEMENT_ACTIONS = {"set_placement"}
_GROUP_ACTION_MAP = {
    "group_list": "list",
    "group_get": "get",
    "group_create": "create",
    "group_update": "update",
    "group_delete": "delete",
    "group_add_members": "add_members",
    "group_remove_member": "remove_member",
    "group_send": "send",
}
_CONTROL_STATES = {"active", "break", "disabled"}
_DRAFT_ACTIONS = {"create_draft", "get_draft", "edit_draft", "approve_draft", "reject_draft", "cancel_draft"}
_DRAFT_REQUIRED_FIELDS = ["name", "description"]
_AGENTS_ROSTER_PATH = "/api/v1/agents"
# Management roster returns {"agents": [...], "total_count": ...}; widget
# helpers normalize it alongside /api/v1/agents' {"items": [...], "total": ...}.
_AGENTS_MANAGEMENT_PATH = "/auth/agents"
_PLACEMENT_RANK_NONE = 0
_PLACEMENT_RANK_HOME_DEFAULT = 1
_PLACEMENT_RANK_ROSTER_SPACE_ID = 2
_PLACEMENT_RANK_EXPLICIT = 3


def _infer_connection_type(item: dict[str, Any]) -> str:
    """Heuristic: derive connection type from presence + agent_type signals."""
    if item.get("sse_connected") or item.get("presence") == "connected":
        return "cli"
    if item.get("agent_type") == "concierge":
        return "always_on"
    return "on_demand"


def _availability_confidence_from_item(item: dict[str, Any]) -> str:
    for key in ("availability", "availability_confidence"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    presence = item.get("presence", "offline")
    return _PRESENCE_TO_CONFIDENCE.get(str(presence).lower(), "offline")


def _stringish(value: Any) -> str:
    return str(value or "").strip().lower()


# AGENT-LIFECYCLE-UI: heartbeat freshness thresholds (seconds). Backend writes
# ax:presence:{id}.last_heartbeat on SSE connect + every ~15s (TTL 30s), now
# surfaced on the roster via serialize_agent. A fresh heartbeat is the single
# most reliable "this agent is live right now" signal we have.
_HEARTBEAT_ONLINE_SECONDS = 60
_HEARTBEAT_IDLE_SECONDS = 300


def _heartbeat_age_seconds(item: dict[str, Any]) -> Optional[float]:
    """Best-effort age (seconds) of the agent's last SSE/platform heartbeat.

    Prefers a backend-computed ``presence_age_seconds``; otherwise parses
    ``last_heartbeat`` (top-level or nested under ``presence``). Returns None
    when no heartbeat signal is available.
    """
    age = item.get("presence_age_seconds")
    if isinstance(age, (int, float)):
        return float(age)

    presence = item.get("presence")
    presence_dict = presence if isinstance(presence, dict) else {}
    # last_heartbeat is the true freshness signal. connected_at (session-established
    # time) is a weak last-resort proxy only — a long-lived-but-live session reads
    # older than it is; accepted because it only ever downgrades online→idle/dormant,
    # never falsely promotes.
    raw = (
        item.get("last_heartbeat")
        or presence_dict.get("last_heartbeat")
        or presence_dict.get("connected_at")
    )
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        hb = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if hb.tzinfo is None:
        hb = hb.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - hb).total_seconds()


def _derive_availability_state(item: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    confidence = _availability_confidence_from_item(item)
    presence = _stringish(item.get("presence"))
    operational_status = _stringish(item.get("operational_status") or item.get("status"))
    sse_connected = bool(item.get("sse_connected"))
    last_message_age_seconds = item.get("last_message_age_seconds")
    heartbeat_age = _heartbeat_age_seconds(item)

    if control.get("state") == "break":
        state = "unavailable"
        reason = "on_break"
    elif control.get("state") == "disabled":
        state = "unavailable"
        reason = "disabled"
    # Heartbeat freshness wins over every soft signal: a real SSE/platform
    # heartbeat in the last minute means the agent is genuinely online.
    elif heartbeat_age is not None and heartbeat_age < _HEARTBEAT_ONLINE_SECONDS:
        state = "available"
        reason = "online"
    elif heartbeat_age is not None and heartbeat_age < _HEARTBEAT_IDLE_SECONDS:
        state = "degraded"
        reason = "idle"
    elif confidence == "high" or sse_connected or presence == "connected":
        state = "available"
        reason = "connected"
    elif confidence == "medium" or presence == "recent":
        state = "degraded"
        reason = "recently_seen"
    elif operational_status in {"active", "available", "ready"}:
        state = "degraded"
        reason = "warming"
    else:
        state = "unavailable"
        reason = "offline"

    return {
        "state": state,
        "reason": reason,
        "confidence": confidence,
        "connection_type": item.get("connection_type") or _infer_connection_type(item),
        "sse_connected": sse_connected,
        "operational_status": item.get("operational_status"),
        "last_message_age_seconds": last_message_age_seconds,
        "heartbeat_age_seconds": heartbeat_age,
    }


# AGENT-LIFECYCLE-UI: one-word availability label for the widget. Priority is
# deliberate so the most decision-relevant fact wins: a disabled/break agent is
# "disabled" regardless of any stale heartbeat; an agent that needs setup is
# "needs_setup"; a genuinely-live heartbeat (<60s) is "online"; recent activity
# or lifecycle idle is "idle"; lifecycle dormant is "dormant"; everything else
# maps from the derived availability state.
# Ordered so summary counts render online→idle→…; also the single source for the
# summary seed (keeps every label present even when a category is empty).
_AVAILABILITY_LABELS = ("online", "idle", "dormant", "needs_setup", "disabled")
# Sort priority: live agents (online/idle) first. needs_setup ranks above dormant
# because it's actionable AND dormant agents are folded into the collapsed
# "Inactive" group by the lifecycle widget, so they're not buried in the main list.
_LABEL_PRIORITY = {"online": 0, "idle": 1, "needs_setup": 2, "dormant": 3, "disabled": 4}


def _derive_availability_label(item: dict[str, Any], score: int, control: dict[str, Any]) -> str:
    """Collapse availability/lifecycle/control signals into a single widget word."""
    setup = item.get("setup") if isinstance(item.get("setup"), dict) else {}
    availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
    lifecycle_state = str(item.get("lifecycle_state") or "").lower()

    if control.get("disabled") or control.get("state") in {"break", "disabled"}:
        return "disabled"
    if setup.get("required"):
        return "needs_setup"

    heartbeat_age = _heartbeat_age_seconds(item)
    if isinstance(heartbeat_age, (int, float)):
        if heartbeat_age < _HEARTBEAT_ONLINE_SECONDS:
            return "online"
        if heartbeat_age < _HEARTBEAT_IDLE_SECONDS:
            return "idle"

    if lifecycle_state == "idle":
        return "idle"
    if lifecycle_state == "dormant":
        return "dormant"

    state = str(availability.get("state") or "").lower()
    if state == "available":
        return "online"
    if state == "degraded":
        return "idle"
    # unavailable / unknown -> dormant unless flagged for setup above
    return "dormant"


def _derive_setup_state(item: dict[str, Any], availability: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    can_fix = bool(item.get("can_control")) or bool(item.get("can_update")) or bool(item.get("owned_by_viewer"))
    connection_type = str(availability.get("connection_type") or item.get("connection_type") or "").strip()

    if control.get("state") in {"break", "disabled"}:
        state = "ready"
        reason = None
        action_required = False
    elif availability.get("state") in {"available", "degraded"}:
        state = "ready"
        reason = None
        action_required = False
    elif connection_type == "always_on":
        state = "ready"
        reason = None
        action_required = False
    elif can_fix:
        state = "needs_setup"
        reason = "runtime_not_connected"
        action_required = True
    else:
        state = "blocked"
        reason = "owner_action_required"
        action_required = True

    return {
        "state": state,
        "required": action_required,
        "reason": reason,
        "can_fix": can_fix,
    }


def _normalize_control_state(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize backend/UI kill-switch payloads to a stable MCP shape."""
    raw = item.get("control") or item.get("control_state")
    if not isinstance(raw, dict):
        raw = {}

    state = raw.get("state") or raw.get("status") or raw.get("kind")
    raw_no_reply = raw.get("no_reply")
    item_no_reply = item.get("no_reply")
    raw_is_disabled = raw.get("is_disabled")
    item_is_disabled = item.get("is_disabled") or item.get("disabled")
    until_hint = (
        raw.get("until")
        or raw.get("disabled_until")
        or raw.get("no_reply_until")
        or item.get("pause_expires_at")
        or item.get("no_reply_until")
    )

    if isinstance(state, str):
        state_value = state.strip().lower()
        if state_value in {"taking_a_break", "temporary_break"}:
            normalized = "break"
        elif state_value == "no_reply":
            normalized = "break" if until_hint else "disabled"
        else:
            normalized = state_value
    elif item.get("paused"):
        normalized = "break"
    else:
        if raw_is_disabled or item_is_disabled or raw_no_reply or item_no_reply:
            normalized = "break" if until_hint else "disabled"
        else:
            normalized = "active"

    if normalized not in _CONTROL_LABELS:
        if until_hint and (raw_is_disabled or item_is_disabled or raw_no_reply or item_no_reply):
            normalized = "break"
        elif raw_is_disabled or item_is_disabled or raw_no_reply or item_no_reply:
            normalized = "disabled"
        else:
            normalized = "active"

    reason = (
        raw.get("reason")
        or raw.get("disabled_reason")
        or raw.get("no_reply_reason")
        or item.get("disabled_reason")
        or item.get("no_reply_reason")
    )
    until = until_hint
    disabled_by = raw.get("disabled_by") or item.get("disabled_by")
    if not isinstance(disabled_by, list):
        disabled_by = []

    disabled_flag = normalized in {"break", "disabled"}
    return {
        "state": normalized,
        "label": _CONTROL_LABELS[normalized],
        "reason": reason,
        "until": until,
        "break_until_ts": until if normalized == "break" else None,
        "disabled_until": until,
        "active": normalized == "active",
        "disabled": disabled_flag,
        "disabled_by": disabled_by,
        "is_disabled": disabled_flag,
    }


def _enrich_with_availability(
    items: list[dict[str, Any]],
    availability_map: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Add availability and kill-switch context to each agent item."""
    for item in items:
        agent_id = item.get("id") or item.get("agent_id")
        avail = availability_map.get(str(agent_id)) if availability_map and agent_id else None

        if avail:
            item["availability_confidence"] = _availability_confidence_from_item(avail)
            item["sse_connected"] = bool(avail.get("sse_connected"))
            if avail.get("operational_status") is not None:
                item["operational_status"] = avail.get("operational_status")
            if avail.get("last_message_age_seconds") is not None:
                item["last_message_age_seconds"] = avail.get("last_message_age_seconds")
            for field in (
                "messages_routable",
                "expected_response",
                "source_of_truth",
                "presence_source",
                "gateway_connected",
                "connected_through_gateway",
                "gateway_session_active",
                "connection_mode",
                "gateway_label",
            ):
                if avail.get(field) is not None:
                    item[field] = avail.get(field)
        else:
            item["availability_confidence"] = _availability_confidence_from_item(item)

        item["connection_type"] = _infer_connection_type(item)
        item["control"] = _normalize_control_state(item)
        item["availability"] = _derive_availability_state(item, item["control"])
        item["setup"] = _derive_setup_state(item, item["availability"], item["control"])
        item["setup_required"] = bool(item["setup"].get("required"))

    return items


async def _fetch_availability_map(
    ctx: dict[str, Any],
) -> Optional[dict[str, dict[str, Any]]]:
    """Fetch availability from backend. Returns None on failure."""
    try:
        result = await _api_request_with_ctx(ctx, "GET", "/api/v1/agents/availability")
        agents = result.get("agents", [])
        if not isinstance(agents, list):
            return {}
        availability_map: dict[str, dict[str, Any]] = {}
        for item in agents:
            if not isinstance(item, dict):
                continue
            item_id = item.get("agent_id") or item.get("id")
            if item_id:
                availability_map[str(item_id)] = item
        return availability_map
    except Exception:
        logger.debug("GET /agents/availability unavailable, falling back to list payload", exc_info=True)
        return None


def _extract_agent_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = result.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]

    agents = result.get("agents")
    if isinstance(agents, list):
        return [item for item in agents if isinstance(item, dict)]

    agent = result.get("agent")
    if isinstance(agent, dict):
        return [agent]

    if isinstance(result.get("name"), str) or isinstance(result.get("handle"), str):
        return [result]

    return []


def _extract_space_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "results", "spaces"):
        items = result.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _space_id(space: dict[str, Any]) -> str:
    return str(space.get("id") or space.get("space_id") or "").strip()


def _space_name(space: dict[str, Any]) -> str:
    return str(space.get("name") or space.get("space_name") or space.get("workspace_name") or "").strip()


def _nested_space(source: Any) -> dict[str, Any]:
    return source if isinstance(source, dict) else {}


def _agent_placement_space_id(item: dict[str, Any]) -> str:
    """Return the agent's current placement, not every space it can access."""
    placement = _nested_space(item.get("placement"))
    current_space = _nested_space(item.get("current_space"))
    effective_space = _nested_space(item.get("effective_space"))
    assigned_space = _nested_space(item.get("assigned_space"))
    home_space = _nested_space(item.get("home_space"))
    default_space = _nested_space(item.get("default_space"))
    for value in (
        item.get("effective_space_id"),
        _space_id(effective_space),
        item.get("current_space_id"),
        _space_id(current_space),
        item.get("placement_space_id"),
        _space_id(placement),
        item.get("assigned_space_id"),
        _space_id(assigned_space),
        item.get("space_id"),
        item.get("home_space_id"),
        _space_id(home_space),
        item.get("default_space_id"),
        _space_id(default_space),
        item.get("pinned_space_id"),
    ):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _agent_placement_rank(item: dict[str, Any]) -> int:
    """Rank placement confidence: explicit current/effective beats roster space."""
    placement = _nested_space(item.get("placement"))
    current_space = _nested_space(item.get("current_space"))
    effective_space = _nested_space(item.get("effective_space"))
    assigned_space = _nested_space(item.get("assigned_space"))
    for value in (
        item.get("effective_space_id"),
        _space_id(effective_space),
        item.get("current_space_id"),
        _space_id(current_space),
        item.get("placement_space_id"),
        _space_id(placement),
        item.get("assigned_space_id"),
        _space_id(assigned_space),
    ):
        if str(value or "").strip():
            return _PLACEMENT_RANK_EXPLICIT
    if str(item.get("space_id") or "").strip():
        return _PLACEMENT_RANK_ROSTER_SPACE_ID
    home_space = _nested_space(item.get("home_space"))
    default_space = _nested_space(item.get("default_space"))
    for value in (
        item.get("home_space_id"),
        _space_id(home_space),
        item.get("default_space_id"),
        _space_id(default_space),
        item.get("pinned_space_id"),
    ):
        if str(value or "").strip():
            return _PLACEMENT_RANK_HOME_DEFAULT
    return _PLACEMENT_RANK_NONE


def _agent_placement_space_name(item: dict[str, Any], space_id: str, spaces_by_id: dict[str, dict[str, Any]]) -> str:
    placement = _nested_space(item.get("placement"))
    current_space = _nested_space(item.get("current_space"))
    effective_space = _nested_space(item.get("effective_space"))
    assigned_space = _nested_space(item.get("assigned_space"))
    home_space = _nested_space(item.get("home_space"))
    default_space = _nested_space(item.get("default_space"))

    requested_space_id = str(space_id or "").strip()
    # Match the placement id priority: effective/current/placement/assigned,
    # then roster space, then home/default/pinned fallbacks.
    paired_sources = (
        (item.get("effective_space_id"), item.get("effective_space_name")),
        (_space_id(effective_space), _space_name(effective_space)),
        (item.get("current_space_id"), item.get("current_space_name")),
        (_space_id(current_space), _space_name(current_space)),
        (item.get("placement_space_id"), item.get("placement_space_name")),
        (_space_id(placement), _space_name(placement)),
        (item.get("assigned_space_id"), item.get("assigned_space_name")),
        (_space_id(assigned_space), _space_name(assigned_space)),
        (item.get("space_id"), item.get("space_name")),
        (item.get("home_space_id"), item.get("home_space_name")),
        (_space_id(home_space), _space_name(home_space)),
        (item.get("default_space_id"), item.get("default_space_name")),
        (_space_id(default_space), _space_name(default_space)),
        (item.get("pinned_space_id"), item.get("pinned_space_name")),
    )

    if requested_space_id:
        for candidate_id, candidate_name in paired_sources:
            if str(candidate_id or "").strip() != requested_space_id:
                continue
            normalized = str(candidate_name or "").strip()
            if normalized:
                return normalized

    mapped_name = _space_name(spaces_by_id.get(requested_space_id, {}))
    if mapped_name:
        return mapped_name

    if not requested_space_id:
        for _, candidate_name in paired_sources:
            normalized = str(candidate_name or "").strip()
            if normalized:
                return normalized
    return ""


def _filter_agents_currently_in_space(items: list[dict[str, Any]], space_id: str | None) -> list[dict[str, Any]]:
    active_space_id = str(space_id or "").strip()
    if not active_space_id:
        return items
    filtered: list[dict[str, Any]] = []
    for item in items:
        placement_space_id = _agent_placement_space_id(item)
        if not placement_space_id or placement_space_id == active_space_id:
            filtered.append(item)
    return filtered


async def _fetch_visible_spaces(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        result = await _api_request_with_ctx(ctx, "GET", SPACES_COLLECTION_PATH)
        return _extract_space_items(result)
    except Exception:
        logger.debug("Failed to fetch visible spaces for agents widget", exc_info=True)
        return []


def _space_lookup(spaces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        sid: space
        for space in spaces
        if (sid := _space_id(space))
    }


def _agents_actions() -> list[dict[str, Any]]:
    return [
        build_action(
            "refresh-agents",
            "Refresh",
            kind="tool",
            target="agents",
            args={"action": "list"},
            style="secondary",
            enabled=True,
            idempotent=True,
        )
    ]


def _agent_action_capabilities(item: dict[str, Any]) -> dict[str, Any]:
    sources = [
        item.get("action_capabilities"),
        item.get("action_permissions"),
        item.get("permissions"),
        item.get("controls"),
    ]
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        sources.append(capabilities)
    for source in sources:
        if isinstance(source, dict):
            return source
    return {}


def _explicit_bool(source: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool):
            return value
    return None


def _agent_item_allows(item: dict[str, Any], capability: str) -> bool:
    caps = _agent_action_capabilities(item)
    if capability == "can_control":
        explicit = _explicit_bool(caps, "can_control", "control", "can_manage")
        if explicit is not None:
            return explicit
    elif capability == "can_update":
        explicit = _explicit_bool(caps, "can_update", "update", "can_manage")
        if explicit is not None:
            return explicit

    direct = _explicit_bool(item, capability, "is_own", "owned_by_viewer")
    if direct is not None:
        return direct

    relationship = str(
        item.get("relationship_to_viewer")
        or item.get("viewer_relationship")
        or item.get("ownership")
        or ""
    ).strip().lower()
    return relationship in {"owned", "owner", "self", "mine", "owned_by_viewer"}


def _is_space_agent_item(item: dict[str, Any]) -> bool:
    agent_type = str(item.get("agent_type") or item.get("type") or item.get("role") or item.get("kind") or "").strip().lower()
    origin = str(item.get("origin") or "").strip().lower()
    template = str(item.get("template_type") or "").strip().lower()
    return agent_type in {"space_agent", "space-agent"} or origin == "space_agent" or template == "space_agent"


def _enrich_widget_agent_contract(
    items: list[dict[str, Any]],
    permission_bundle: dict[str, Any] | None = None,
    *,
    source_space: dict[str, Any] | None = None,
    spaces_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Normalize ownership and the single user-facing agent Space field.

    The widget contract intentionally exposes one placement concept:
    `space_id` / `space_name` is the agent's assigned operating space for this
    row. Older backend fields such as home/default/current/effective space are
    compatibility inputs only; they should not leak into widget UX as competing
    concepts.
    """
    bundle = permission_bundle or {}
    viewer = bundle.get("viewer") if isinstance(bundle.get("viewer"), dict) else {}
    space_context = (
        bundle.get("space_context")
        if isinstance(bundle.get("space_context"), dict)
        else {}
    )
    viewer_id = str(viewer.get("user_id") or "").strip()
    source = source_space if isinstance(source_space, dict) else {}
    space_map = spaces_by_id or {}
    active_space_id = str(
        _space_id(source)
        or space_context.get("id")
        or space_context.get("space_id")
        or space_context.get("current_space_id")
        or ""
    ).strip()
    active_space_name = str(
        _space_name(source)
        or space_context.get("name")
        or space_map.get(active_space_id, {}).get("name")
        or space_map.get(active_space_id, {}).get("space_name")
        or ""
    ).strip()
    raw_permissions = bundle.get("permissions")
    permissions = raw_permissions if isinstance(raw_permissions, dict) else {}

    legacy_space_fields = {
        "default_space_id",
        "default_space_name",
        "home_space_id",
        "home_space_name",
        "pinned_space_id",
        "pinned_space_name",
        "current_space_id",
        "current_space_name",
        "effective_space_id",
        "effective_space_name",
    }

    for item in items:
        if not item.get("name") and item.get("agent_name"):
            item["name"] = item.get("agent_name")
        if not item.get("display_name") and item.get("agent_name"):
            item["display_name"] = item.get("agent_name")
        if isinstance(item.get("is_own_agent"), bool) and not isinstance(item.get("is_own"), bool):
            item["is_own"] = bool(item.get("is_own_agent"))

        is_space_agent = _is_space_agent_item(item)
        owner_user_id = str(
            item.get("owner_user_id")
            or item.get("owner_id")
            or item.get("user_id")
            or item.get("created_by_user_id")
            or ""
        ).strip()
        if owner_user_id:
            item.setdefault("owner_user_id", owner_user_id)

        if is_space_agent:
            # Space agents are backed by an internal system principal, but the
            # widget contract must describe them as owned by the space.
            is_own = False
            item.pop("owner_username", None)
            item.pop("owner_handle", None)
            item.pop("owner_login", None)
            item.pop("owner_user_id", None)
            item.pop("owner_id", None)
            item.pop("user_id", None)
            item["owner_display_name"] = "Space agent"
            item["relationship_to_viewer"] = "space_agent"
        elif isinstance(item.get("is_own"), bool):
            is_own = bool(item.get("is_own"))
        elif owner_user_id and viewer_id:
            is_own = owner_user_id == viewer_id
            item.setdefault("is_own", is_own)
        else:
            is_own = bool(item.get("owned_by_viewer"))

        item["is_own"] = is_own if is_space_agent else item.get("is_own", is_own)
        item["owned_by_viewer"] = is_own if is_space_agent else item.get("owned_by_viewer", is_own)
        item.setdefault("can_control", is_own)
        item.setdefault("can_update", is_own)
        if not is_space_agent:
            item.setdefault(
                "relationship_to_viewer",
                "owned_by_viewer" if is_own else "other_user",
            )

        placement_rank = _agent_placement_rank(item)
        canonical_space_id = _agent_placement_space_id(item) or _space_id(source)
        if canonical_space_id and placement_rank == 0:
            placement_rank = _PLACEMENT_RANK_HOME_DEFAULT
        canonical_space_name = _agent_placement_space_name(item, canonical_space_id, space_map) or _space_name(source)

        access = item.get("space_access")
        if not isinstance(access, list):
            access = []
        normalized_access: list[dict[str, Any]] = []
        access_by_id: dict[str, dict[str, Any]] = {}
        for entry in access:
            if not isinstance(entry, dict):
                continue
            sid = str(entry.get("space_id") or entry.get("id") or "").strip()
            if not sid:
                continue
            normalized = dict(entry)
            normalized["space_id"] = sid
            normalized.pop("id", None)
            if not normalized.get("name") and normalized.get("space_name"):
                normalized["name"] = normalized.get("space_name")
            if not normalized.get("name"):
                normalized["name"] = _space_name(space_map.get(sid, {}))
            access_by_id[sid] = normalized

        if canonical_space_id:
            canonical_access = access_by_id.get(canonical_space_id, {})
            canonical_access.setdefault("space_id", canonical_space_id)
            if canonical_space_name:
                canonical_access["name"] = canonical_space_name
            elif not canonical_access.get("name"):
                canonical_access["name"] = _space_name(space_map.get(canonical_space_id, {}))
            if "role" not in canonical_access and active_space_id == canonical_space_id:
                canonical_access["role"] = space_context.get("role")
            access_by_id[canonical_space_id] = canonical_access

        # If the row is explicitly sourced from its active placement roster,
        # keep that roster space in the access metadata. Do not add a previous
        # roster source for agents that have moved elsewhere.
        if active_space_id and active_space_id == canonical_space_id:
            active_access = access_by_id.get(active_space_id, {})
            active_access.setdefault("space_id", active_space_id)
            if active_space_name:
                active_access.setdefault("name", active_space_name)
            if "role" not in active_access:
                active_access["role"] = space_context.get("role")
            access_by_id[active_space_id] = active_access

        if access_by_id:
            normalized_access = list(access_by_id.values())
            item["space_access"] = normalized_access
            item["space_access_count"] = len(normalized_access)

        if canonical_space_id:
            item["space_id"] = canonical_space_id
            if canonical_space_name:
                item["space_name"] = canonical_space_name
            elif not item.get("space_name"):
                item["space_name"] = _space_name(space_map.get(canonical_space_id, {}))
            item["_placement_rank"] = placement_rank

        current_space_agent_control_allowed = (
            is_space_agent
            and canonical_space_id
            and active_space_id
            and canonical_space_id == active_space_id
            and bool(permissions.get("can_control"))
        )
        if current_space_agent_control_allowed:
            item["can_control"] = True
            caps = item.get("action_capabilities")
            if not isinstance(caps, dict):
                caps = {}
            # Backend can_control is authoritative for the active space agent;
            # overwrite stale per-action caps from older roster payloads.
            caps.update(
                {
                    "can_control": True,
                    "can_disable": True,
                    "can_enable": True,
                    "can_reenable": True,
                }
            )
            item["action_capabilities"] = caps

        for legacy_field in legacy_space_fields:
            item.pop(legacy_field, None)

        if item.get("space_locked") is None and item.get("pinned") is not None:
            item["space_locked"] = bool(item.get("pinned"))

        if not _agent_action_capabilities(item):
            can_manage = is_own or bool(item.get("can_control")) or bool(item.get("can_update"))
            item["action_capabilities"] = {
                "can_update": can_manage,
                "can_control": can_manage,
                "can_delete": can_manage,
            }

        availability = item.get("availability")
        control = item.get("control")
        if isinstance(availability, dict) and isinstance(control, dict):
            item["setup"] = _derive_setup_state(item, availability, control)
            item["setup_required"] = bool(item["setup"].get("required"))

    return items



def _agent_messages_routable(item: dict[str, Any]) -> bool:
    for source in (item.get("availability"), item):
        if isinstance(source, dict) and isinstance(source.get("messages_routable"), bool):
            return bool(source.get("messages_routable"))
    control = item.get("control") if isinstance(item.get("control"), dict) else {}
    availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
    if control.get("disabled") or availability.get("state") == "unavailable":
        return False
    return availability.get("state") in {"available", "degraded"}


def _agent_gateway_connected(item: dict[str, Any]) -> bool:
    for source in (item.get("availability"), item):
        if not isinstance(source, dict):
            continue
        for key in ("gateway_connected", "connected_through_gateway", "gateway_session_active"):
            if isinstance(source.get(key), bool):
                return bool(source.get(key))
        source_name = str(source.get("source_of_truth") or source.get("presence_source") or "").lower()
        connection_mode = str(source.get("connection_mode") or source.get("connection_type") or "").lower()
        if ("gateway" in source_name or "gateway" in connection_mode) and bool(source.get("sse_connected")):
            return True
    return False


def _agent_expected_response(item: dict[str, Any]) -> str:
    for source in (item.get("availability"), item):
        if isinstance(source, dict):
            value = source.get("expected_response")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
    if availability.get("state") == "available":
        return "immediate"
    if availability.get("state") == "degraded":
        return "warming"
    return "unavailable"


def _messageability_score(item: dict[str, Any]) -> tuple[int, list[str], str]:
    control = item.get("control") if isinstance(item.get("control"), dict) else {}
    availability = item.get("availability") if isinstance(item.get("availability"), dict) else {}
    setup = item.get("setup") if isinstance(item.get("setup"), dict) else {}
    expected = _agent_expected_response(item)
    routable = _agent_messages_routable(item)
    gateway_connected = _agent_gateway_connected(item)
    sse_connected = bool(item.get("sse_connected") or availability.get("sse_connected"))
    confidence = str(availability.get("confidence") or item.get("availability_confidence") or "").lower()

    reasons: list[str] = []
    score = 0

    if control.get("disabled"):
        return 0, ["control_disabled"], "unavailable"
    if setup.get("required"):
        return 5, [str(setup.get("reason") or "setup_required")], "needs_setup"

    if routable:
        score += 35
        reasons.append("messages_routable")
    else:
        reasons.append("not_routable")

    if gateway_connected:
        score += 35
        reasons.append("gateway_connected")
    elif sse_connected:
        score += 25
        reasons.append("live_session_connected")

    if expected == "immediate":
        score += 25
        reasons.append("response_expected_immediately")
    elif expected == "warming":
        score += 12
        reasons.append("warming_expected")
    elif expected == "queued":
        score += 6
        reasons.append("queued_only")
    elif expected in {"unlikely", "unavailable"}:
        score -= 20
        reasons.append(f"response_{expected}")

    if confidence == "high":
        score += 5
    elif confidence in {"offline", "low"}:
        score -= 10

    # AGENT-LIFECYCLE-UI: soft-demote dormant agents so they fold out of the
    # default view, but NEVER force-hide one that is still reachable. The
    # lifecycle signal is uncalibrated (shadow mode), so a dormant-labeled
    # agent that is still routable / gateway-connected stays at or above the
    # "likely" fold (45); only marginal/unreachable dormant agents drop below.
    lifecycle_state = str(item.get("lifecycle_state") or "").lower()
    if lifecycle_state == "dormant":
        reasons.append("lifecycle_dormant")
        demoted = score - 25
        if (routable or gateway_connected) and score >= 45:
            demoted = max(demoted, 45)
        score = demoted

    if score >= 75:
        bucket = "send_now"
    elif score >= 45:
        bucket = "likely"
    elif score >= 20:
        bucket = "possible"
    else:
        bucket = "unlikely"
    return max(score, 0), reasons, bucket


def _annotate_messageability(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Rank agents for the default widget view: who would I message right now?"""
    counts = {"send_now": 0, "likely": 0, "possible": 0, "unlikely": 0, "needs_setup": 0, "unavailable": 0}
    availability_summary = {label: 0 for label in _AVAILABILITY_LABELS}
    for index, item in enumerate(items):
        score, reasons, bucket = _messageability_score(item)
        counts[bucket] = counts.get(bucket, 0) + 1
        default_visible = bucket in {"send_now", "likely"}
        control = item.get("control") if isinstance(item.get("control"), dict) else {}
        availability_label = _derive_availability_label(item, score, control)
        availability_summary[availability_label] = availability_summary.get(availability_label, 0) + 1
        item["messageability"] = {
            "score": score,
            "bucket": bucket,
            "default_visible": default_visible,
            "messages_routable": _agent_messages_routable(item),
            "gateway_connected": _agent_gateway_connected(item),
            "expected_response": _agent_expected_response(item),
            "lifecycle_state": str(item.get("lifecycle_state") or "").lower() or None,
            "last_active_at": item.get("last_active_at"),
            "availability_label": availability_label,
            "reasons": reasons,
        }
        # Sort genuinely-live agents first (label priority), then by score.
        item["_messageability_sort"] = (_LABEL_PRIORITY.get(availability_label, 9), -score, index)

    items.sort(key=lambda row: row.get("_messageability_sort", (9, 0, 0)))
    for item in items:
        item.pop("_messageability_sort", None)

    default_count = sum(1 for item in items if item.get("messageability", {}).get("default_visible"))
    return {
        "availability_summary": availability_summary,
        "default_filter": {
            "id": "most_likely_messageable",
            "label": "Most likely to message now",
            "description": "Gateway-connected and otherwise routable agents are shown first; disabled/setup/offline agents are hidden from the default view.",
            "count": default_count,
            "predicate": {"messageability.default_visible": True},
        },
        "filters": [
            {"id": "most_likely_messageable", "label": "Most likely", "count": default_count},
            {"id": "send_now", "label": "Send now", "count": counts.get("send_now", 0)},
            {"id": "gateway_connected", "label": "Gateway connected", "count": sum(1 for item in items if item.get("messageability", {}).get("gateway_connected"))},
            {"id": "routable", "label": "Routable", "count": sum(1 for item in items if item.get("messageability", {}).get("messages_routable"))},
            {"id": "needs_setup", "label": "Needs setup", "count": counts.get("needs_setup", 0)},
            {"id": "all", "label": "All agents", "count": len(items)},
        ],
    }

def _merge_agent_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for group in groups:
        for item in group:
            key = str(item.get("id") or item.get("agent_id") or item.get("name") or "").strip()
            if not key:
                continue
            if key not in merged:
                merged[key] = dict(item)
                order.append(key)
                continue

            existing = merged[key]
            incoming_placement_rank = int(item.get("_placement_rank") or 0)
            existing_placement_rank = int(existing.get("_placement_rank") or 0)
            for field, value in item.items():
                if field == "space_access":
                    continue
                if field in {"space_id", "space_name"}:
                    if (
                        value not in (None, "", [], {})
                        and (
                            incoming_placement_rank > existing_placement_rank
                            or existing.get(field) in (None, "", [], {})
                        )
                    ):
                        existing[field] = value
                    continue
                if field == "action_capabilities" and isinstance(value, dict):
                    caps = existing.get("action_capabilities")
                    if not isinstance(caps, dict):
                        caps = {}
                    for cap_key, cap_value in value.items():
                        if isinstance(cap_value, bool):
                            caps[cap_key] = bool(caps.get(cap_key)) or cap_value
                        elif cap_key not in caps:
                            caps[cap_key] = cap_value
                    existing["action_capabilities"] = caps
                    continue
                if field in {"is_own", "owned_by_viewer"} and isinstance(value, bool):
                    existing[field] = bool(existing.get(field)) or value
                    continue
                if value not in (None, "", [], {}) and existing.get(field) in (None, "", [], {}):
                    existing[field] = value

            if incoming_placement_rank > existing_placement_rank:
                existing["_placement_rank"] = incoming_placement_rank

            access_by_id: dict[str, dict[str, Any]] = {}
            for access in existing.get("space_access") or []:
                if isinstance(access, dict) and (sid := str(access.get("space_id") or access.get("id") or "").strip()):
                    access_by_id[sid] = dict(access)
            for access in item.get("space_access") or []:
                if isinstance(access, dict) and (sid := str(access.get("space_id") or access.get("id") or "").strip()):
                    access_by_id.setdefault(sid, dict(access))
            if access_by_id:
                existing["space_access"] = list(access_by_id.values())
                existing["space_access_count"] = len(access_by_id)

            if existing.get("owned_by_viewer") or existing.get("is_own"):
                existing["relationship_to_viewer"] = "owned_by_viewer"

    return [merged[key] for key in order]


def _strip_internal_agent_fields(items: list[dict[str, Any]]) -> None:
    for item in items:
        item.pop("_placement_rank", None)


async def _fetch_agent_for_permission(
    ctx: dict[str, Any],
    agent_id: str,
) -> dict[str, Any] | None:
    try:
        result = await _api_request_with_ctx(ctx, "GET", f"/api/v1/agents/{agent_id}")
        items = _extract_agent_items(result)
        if items:
            return items[0]
        if isinstance(result, dict) and not result.get("error"):
            return result
    except Exception:
        logger.debug("Agent detail permission lookup failed for %s", agent_id, exc_info=True)

    try:
        result = await _api_request_with_ctx(
            ctx,
            "GET",
            "/api/v1/agents",
            params={"limit": 200, "offset": 0},
        )
        for item in _extract_agent_items(result):
            if str(item.get("id") or item.get("agent_id") or "") == str(agent_id):
                return item
    except Exception:
        logger.debug("Agent list permission lookup failed for %s", agent_id, exc_info=True)
    return None


async def _agent_write_allowed(
    ctx: dict[str, Any],
    permission_bundle: dict[str, Any],
    selected_agent_id: str | None,
    capability: str,
) -> bool:
    if permissions_allow(permission_bundle, capability):
        return True
    if not selected_agent_id:
        return False
    item = await _fetch_agent_for_permission(ctx, selected_agent_id)
    return bool(item and _agent_item_allows(item, capability))


def _agent_owner_only_message(permission_bundle: dict[str, Any]) -> str:
    return blocked_reason(
        permission_bundle,
        "You can manage agents you own. Agents owned by other users are read-only.",
    )


def _agent_mode_from_kind(kind: Any) -> str:
    value = str(kind or "")
    if value.endswith(".privileged"):
        return "privileged"
    if value.endswith(".with_credentials"):
        return "with_credentials"
    return "sandbox"


def _draft_agent_payload(
    *,
    name: Optional[str],
    description: Optional[str],
    system_prompt: Optional[str],
    model: Optional[str],
) -> dict[str, Any]:
    agent: dict[str, Any] = {"name": (name or "").strip()}
    if description is not None:
        agent["description"] = description.strip()
    if system_prompt is not None and system_prompt.strip():
        agent["system_prompt"] = system_prompt.strip()
    if model is not None and model.strip():
        agent["model"] = model.strip()
    return agent


def _draft_to_widget_draft(result: dict[str, Any]) -> dict[str, Any]:
    agent = result.get("agent") if isinstance(result.get("agent"), dict) else {}
    return {
        "draft_id": result.get("draft_id"),
        "version": result.get("version"),
        "status": result.get("status"),
        "kind": result.get("kind"),
        "card": result.get("card"),
        "agent_mode": _agent_mode_from_kind(result.get("kind")),
        "name": agent.get("name") or "",
        "description": agent.get("description") or "",
        "system_prompt": agent.get("system_prompt") or "",
        "model": agent.get("model") or "",
        "target_space_id": result.get("target_space_id"),
        "enabled_tools": result.get("enabled_tools") or {},
        "editable_fields": result.get("editable_fields") or [],
        "requested_scopes": result.get("requested_scopes") or [],
        "approval_required": result.get("approval_required", True),
        "risk_class": result.get("risk_class"),
        "backend": result,
    }


def _created_agent_from_draft_result(result: dict[str, Any]) -> dict[str, Any] | None:
    execution = result.get("execution_result")
    if not isinstance(execution, dict):
        return None
    created = execution.get("agent")
    if isinstance(created, dict):
        return created
    return None


def _agent_draft_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    if result.get("error"):
        message = str(result.get("detail") or result.get("error"))
        structured = build_tool_output(
            "agent_collection",
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
            code="agent_draft_error",
        )
        return widget_tool_result(
            "agents",
            action=action,
            content=result,
            structured_content=structured,
        )

    draft = _draft_to_widget_draft(result)
    created = _created_agent_from_draft_result(result)
    data: dict[str, Any]
    state = "ready"
    if created:
        items = _enrich_widget_agent_contract(
            _enrich_with_availability([created]),
            permission_bundle,
        )
        _strip_internal_agent_fields(items)
        data = {
            "scope": "created",
            "created": items[0],
            "items": items,
            "count": len(items),
            "draft": draft,
            "credential": (result.get("execution_result") or {}).get("credential"),
            "hint": "Agent created.",
        }
    elif action in {"reject_draft", "cancel_draft"} or result.get("status") in {"rejected", "cancelled"}:
        data = {
            "scope": "draft_dismissed",
            "draft": draft,
            "hint": "Draft dismissed.",
        }
    else:
        data = {
            "scope": "create",
            "draft": draft,
            "required_fields": _DRAFT_REQUIRED_FIELDS,
            "hint": "Review this draft before creating the agent.",
        }

    structured = build_tool_output(
        "agent_collection",
        2,
        state,
        attach_permission_bundle(data, permission_bundle),
    )
    return widget_tool_result(
        "agents",
        action=action,
        content=result,
        structured_content=structured,
    )


def _agent_control_review_result(
    *,
    agent_id: str,
    agent_name: str,
    action: Literal["disable", "enable", "set_control"],
    desired_state: str,
    duration_minutes: Optional[int],
    reason: Optional[str],
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    duration_label = "until you re-enable it"
    if isinstance(duration_minutes, int) and duration_minutes > 0:
        if duration_minutes < 60:
            duration_label = f"for {duration_minutes} minute{'s' if duration_minutes != 1 else ''}"
        elif duration_minutes % 60 == 0:
            hours = duration_minutes // 60
            duration_label = f"for {hours} hour{'s' if hours != 1 else ''}"
        else:
            duration_label = f"for {duration_minutes} minutes"

    control_key = desired_state
    control = {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "action": action,
        "state": desired_state,
        "duration_minutes": duration_minutes,
        "duration_label": duration_label,
        "reason": reason or "",
        "help": _CONTROL_REASONS[control_key],
    }
    structured = build_tool_output(
        "agent_collection",
        2,
        "ready",
        attach_permission_bundle(
            {"scope": "control_review", "control": control},
            permission_bundle,
        ),
    )
    return widget_tool_result("agents", action=action, content=control, structured_content=structured)


def _resolve_control_action(
    action: str,
    desired_state: Optional[str],
) -> tuple[str, Optional[str]]:
    """Allow MCP callers to use explicit state semantics without divergent payloads."""
    normalized = str(desired_state or "").strip().lower() or None
    if action == "enable":
        return action, "active"
    if action == "disable":
        return action, (normalized or ("break" if desired_state else "disabled"))
    if action in {"toggle", "set_control"}:
        if normalized not in _CONTROL_STATES:
            return "", normalized
        return ("enable" if normalized == "active" else "disable"), normalized
    return "", normalized



def _build_control_payload(
    desired_state: str,
    duration_minutes: Optional[int],
    reason: Optional[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {"scope": "agent"}
    if desired_state == "active":
        payload.update({
            "disabled": False,
            "disabled_until": None,
            "reason": None,
        })
        return payload

    payload["disabled"] = True
    payload["reason"] = reason
    if desired_state == "break":
        if duration_minutes is None or duration_minutes <= 0:
            raise ValueError("duration_minutes must be > 0 when state=break")
        until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        payload["disabled_until"] = until.isoformat().replace("+00:00", "Z")
    else:
        payload["disabled_until"] = None
    return payload


async def _apply_control_action(
    *,
    ctx: dict[str, Any],
    agent_id: str,
    action: Literal["disable", "enable"],
    desired_state: str,
    duration_minutes: Optional[int],
    reason: Optional[str],
) -> dict[str, Any]:
    payload = _build_control_payload(desired_state, duration_minutes, reason)
    return await _api_request_with_ctx(
        ctx,
        "PATCH",
        f"/auth/agents/{agent_id}/control",
        json_data=payload,
    )


def _control_widget_result(
    result: dict[str, Any],
    action: str,
    desired_state: str,
    agent_id: str | None = None,
    *,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    items = _enrich_widget_agent_contract(
        _enrich_with_availability(_extract_agent_items(result)),
        permission_bundle,
    )
    _strip_internal_agent_fields(items)
    agent = items[0] if items else None
    # The PATCH /agents/{id}/control endpoint returns a control state
    # object (is_disabled, no_reply, disabled_by, etc.), not an agent
    # object. If the response has control state fields but no agent
    # items, that's a successful control action — not an error.
    has_control_state = "is_disabled" in result or "no_reply" in result
    normalized_control = None
    if agent:
        normalized_control = agent.get("control")
    elif has_control_state:
        normalized_control = _normalize_control_state({"control": result})
    state = "ready"
    scope = result.get("scope", "control_applied")
    if result.get("error") or (not agent and not has_control_state):
        state = "error"
        scope = "control_error"
    structured = build_tool_output(
        "agent_collection",
        2,
        state,
        attach_permission_bundle({
            "scope": scope,
            "agent": agent,
            "agent_id": agent.get("id") if isinstance(agent, dict) else agent_id,
            "control": normalized_control,
            "control_action": action,
            "control_state_requested": desired_state,
            "hint": result.get("detail") or result.get("hint") or result.get("error"),
        }, permission_bundle),
    )
    if result.get("error"):
        structured["notice"] = build_notice(
            str(result.get("detail") or result.get("error")),
            severity="error",
            code="agent_control_error",
        )
    return widget_tool_result("agents", action=action, content=result, structured_content=structured)


def _agents_widget_result(
    result: dict[str, Any],
    action: str,
    availability_map: Optional[dict[str, dict[str, Any]]] = None,
    *,
    permission_bundle: dict[str, Any] | None = None,
    spaces: list[dict[str, Any]] | None = None,
    source_space: dict[str, Any] | None = None,
    extra_items: list[dict[str, Any]] | None = None,
    filter_current_space_id: str | None = None,
) -> ToolResult:
    include_spaces = spaces is not None
    spaces_list = spaces or []
    spaces_by_id = _space_lookup(spaces_list)
    if result.get("error"):
        message = str(result.get("detail") or result.get("error"))
        messageability_meta = _annotate_messageability([])
        view_scope = result.get("scope", action)
        data = attach_permission_bundle({
            "items": [],
            "count": 0,
            "total": 0,
            "scope": view_scope,
            "view_scope": view_scope,
            "default_view_scope": "space",
            "limit": result.get("limit"),
            "offset": result.get("offset"),
            "has_more": False,
            "cross_space_loaded": bool(result.get("cross_space_loaded")),
            "default_filter": messageability_meta["default_filter"],
            "filters": messageability_meta["filters"],
            "availability_summary": messageability_meta["availability_summary"],
        }, permission_bundle)
        if include_spaces:
            data["spaces"] = spaces_list
        structured = build_tool_output(
            "agent_collection",
            2,
            "error",
            data,
            actions=_agents_actions(),
        )
        structured["notice"] = build_notice(message, severity="error", code="agents_list_error")
        return widget_tool_result("agents", action=action, content=result, structured_content=structured)

    raw_items = _filter_agents_currently_in_space(
        _extract_agent_items(result),
        filter_current_space_id,
    )
    items = _enrich_widget_agent_contract(
        _enrich_with_availability(raw_items, availability_map),
        permission_bundle,
        source_space=source_space,
        spaces_by_id=spaces_by_id,
    )
    if extra_items:
        items = _merge_agent_items(items, extra_items)
    _strip_internal_agent_fields(items)
    # When filtering by current placement, backend totals describe the broader
    # accessible roster. The widget needs the post-filter count.
    total = len(items) if filter_current_space_id else result.get(
        "total",
        result.get("total_count", result.get("count", len(items))),
    )
    if extra_items:
        total = max(int(total or 0), len(items))
    messageability_meta = _annotate_messageability(items)
    count = len(items)
    view_scope = result.get("scope", action)
    data = attach_permission_bundle({
        "items": items,
        "count": count,
        "total": total,
        "scope": view_scope,
        "view_scope": view_scope,
        "default_view_scope": "space",
        "view_options": [
            {
                "id": "space",
                "label": "By space",
                "description": "Agents currently placed in the active space.",
            },
            {
                "id": "all",
                "label": "View all",
                "description": "All visible agents across spaces you can access.",
            },
            {
                "id": "mine",
                "label": "My agents",
                "description": "Agents owned by the current viewer.",
            },
            {
                "id": "others",
                "label": "Other users",
                "description": "Agents owned by other users across accessible spaces.",
            },
        ],
        "limit": result.get("limit"),
        "offset": result.get("offset"),
        # The current-space view is post-filtered by placement. Backend
        # pagination describes the broader roster, so more pages would not
        # necessarily produce visible cards for this scope.
        "has_more": False if filter_current_space_id else bool(result.get("has_more")),
        "cross_space_loaded": bool(result.get("cross_space_loaded")),
        "default_filter": messageability_meta["default_filter"],
        "filters": messageability_meta["filters"],
        "availability_summary": messageability_meta["availability_summary"],
    }, permission_bundle)
    if include_spaces:
        data["spaces"] = spaces_list
    structured = build_tool_output(
        "agent_collection",
        2,
        "empty" if not items else "ready",
        data,
        actions=_agents_actions(),
    )
    return widget_tool_result("agents", action=action, content=result, structured_content=structured)


def _agents_blocked_result(
    action: str,
    *,
    permission_bundle: dict[str, Any],
    message: str,
) -> ToolResult:
    structured = build_tool_output(
        "agent_collection",
        2,
        "ready",
        attach_permission_bundle(
            {
                "items": [],
                "count": 0,
                "scope": (
                    "control_blocked"
                    if action in _CONTROL_ACTIONS | {"toggle"}
                    else "create_blocked"
                    if action in _DRAFT_ACTIONS
                    else "placement_blocked"
                    if action in _PLACEMENT_ACTIONS
                    else f"{action}_blocked"
                ),
                "hint": message,
            },
            permission_bundle,
        ),
    )
    structured["notice"] = build_notice(
        message,
        severity="error",
        code=f"agents_{action}_blocked",
    )
    return widget_tool_result(
        "agents",
        action=action,
        content={"error": message},
        structured_content=structured,
    )


def register_agents_tool(mcp: FastMCP):

    @mcp.tool(
        annotations=bounded_write_annotations(),
        app=tool_app_config("agents"),
        meta=tool_meta("agents"),
        output_schema=tool_output_schema("agents"),
    )
    async def agents(
        action: Annotated[
            Literal[
                "list",
                "get",
                "update",
                "disable",
                "enable",
                "toggle",
                "set_control",
                "set_placement",
                "create",
                "create_draft",
                "get_draft",
                "edit_draft",
                "approve_draft",
                "reject_draft",
                "cancel_draft",
                "group_list",
                "group_get",
                "group_create",
                "group_update",
                "group_delete",
                "group_add_members",
                "group_remove_member",
                "group_send",
            ],
            Field(
                default="list",
                description=(
                    "Agent action. Use list/get to read, update for admin/owner "
                    "profile edits of managed agents, disable/enable/toggle/set_control for control "
                    "state, set_placement to move an owned agent, the *_draft "
                    "flow for HITL agent creation, and group_* actions to "
                    "manage and message agent groups."
                ),
            ),
        ] = "list",
        limit: Annotated[
            int,
            Field(default=200, ge=1, le=500, description="Maximum agents to return for list."),
        ] = 200,
        offset: Annotated[
            int,
            Field(default=0, ge=0, description="Result offset for paging list results."),
        ] = 0,
        view_scope: Annotated[
            Optional[Literal["space", "mine", "all"]],
            Field(
                default="space",
                description=(
                    "List scope: space for agents in the active space, mine "
                    "for agents the caller owns, all for every visible agent."
                ),
            ),
        ] = "space",
        agent_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Agent ID or name. Required for get, update, disable, "
                    "enable, toggle, set_control, and set_placement."
                ),
            ),
        ] = None,
        target: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Alias for agent_id kept for client compatibility.",
            ),
        ] = None,
        state: Annotated[
            Optional[Literal["active", "break", "disabled"]],
            Field(
                default=None,
                description="For set_control/disable, the desired control state.",
            ),
        ] = None,
        status: Annotated[
            Optional[Literal["active", "inactive", "paused", "disabled"]],
            Field(
                default=None,
                description="Legacy status value for toggle-style control actions.",
            ),
        ] = None,
        name: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Agent or group display name for create/update actions.",
            ),
        ] = None,
        bio: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=update, the agent's public bio.",
            ),
        ] = None,
        specialization: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=update, the agent's specialization or role.",
            ),
        ] = None,
        description: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Description text for agent updates or group create/update.",
            ),
        ] = None,
        avatar_url: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "For admin/owner updates to a managed agent, avatar as an https URL, "
                    'data:image URI, or raw <svg> markup (auto-wrapped); pass "" to clear. '
                    "Ordinary self avatar edits belong on whoami.update."
                ),
            ),
        ] = None,
        avatar_emoji: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    'For admin/owner updates to a managed agent, avatar as a single emoji '
                    '(e.g. "🍑"), rendered to an inline SVG — no image hosting needed. '
                    "Ordinary self avatar edits belong on whoami.update."
                ),
            ),
        ] = None,
        system_prompt: Annotated[
            Optional[str],
            Field(
                default=None,
                description="System prompt for agent create/draft definitions.",
            ),
        ] = None,
        model: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Model identifier for agent create/draft definitions.",
            ),
        ] = None,
        agent_mode: Annotated[
            Optional[Literal["sandbox", "privileged", "with_credentials"]],
            Field(
                default="sandbox",
                description="Runtime permission mode for created agents (default sandbox).",
            ),
        ] = "sandbox",
        agent_type: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Agent type/category label for create/draft definitions.",
            ),
        ] = None,
        declared_capabilities: Annotated[
            Optional[list[str]],
            Field(
                default=None,
                description="Capability labels the created agent advertises to others.",
            ),
        ] = None,
        enabled_tools: Annotated[
            Optional[dict[str, bool]],
            Field(
                default=None,
                description="Per-tool enable/disable map for the created agent.",
            ),
        ] = None,
        requested_scopes: Annotated[
            Optional[list[str]],
            Field(
                default=None,
                description="Permission scopes requested for the created agent.",
            ),
        ] = None,
        management_rights: Annotated[
            bool,
            Field(
                default=False,
                description="When true, list/get include management metadata for owned agents.",
            ),
        ] = False,
        pinned: Annotated[
            Optional[bool],
            Field(
                default=None,
                description="For set_placement, pin the agent in the target space.",
            ),
        ] = None,
        duration_minutes: Annotated[
            Optional[int],
            Field(
                default=None,
                description="For disable with state=break, how long the break lasts.",
            ),
        ] = None,
        reason: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Short audit reason recorded with control-state changes.",
            ),
        ] = None,
        confirmed: Annotated[
            bool,
            Field(
                default=False,
                description="Explicit confirmation flag required by some control actions.",
            ),
        ] = False,
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
        space_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Target space for set_placement; otherwise the active "
                    "space is resolved from the authenticated session."
                ),
            ),
        ] = None,
        group_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Agent group ID. Required for group_get, group_update, "
                    "group_delete, group_add_members, group_remove_member, "
                    "and group_send."
                ),
            ),
        ] = None,
        member_agent_ids: Annotated[
            Optional[list[str]],
            Field(
                default=None,
                description="Agent IDs to include when creating a group or adding members.",
            ),
        ] = None,
        group_member_agent_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Single agent ID to remove for group_remove_member.",
            ),
        ] = None,
        visibility: Annotated[
            Optional[Literal["space", "private"]],
            Field(
                default=None,
                description="Group visibility: space-wide or private to the creator.",
            ),
        ] = None,
        is_archived: Annotated[
            Optional[bool],
            Field(
                default=None,
                description="For group_update, archive (true) or unarchive (false) the group.",
            ),
        ] = None,
        is_dynamic: Annotated[
            Optional[bool],
            Field(
                default=None,
                description="Whether the group membership is rule-driven instead of static.",
            ),
        ] = None,
        dynamic_rules: Annotated[
            Optional[dict[str, Any]],
            Field(
                default=None,
                description="Membership rules object for dynamic groups.",
            ),
        ] = None,
        include_archived: Annotated[
            bool,
            Field(
                default=False,
                description="For group_list, include archived groups in results.",
            ),
        ] = False,
        content: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Message body for group_send.",
            ),
        ] = None,
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> ToolResult | dict:
        """Discover available agents, update profiles, or control kill-switch state.

        Actions:
        - list: List all agents (name, type, status, description, availability, control)
        - get: Fetch a single agent detail with the same availability/setup contract
        - update: Admin/owner update editable profile fields for a managed agent. Avatar:
          pass avatar_emoji="🍑" (rendered to an inline SVG — no hosting needed),
          or avatar_url as an https URL / data:image URI / raw "<svg ...>" markup
          (auto-wrapped); avatar_url="" clears it. Ordinary self avatar edits belong on whoami.update.
        - disable: Put an agent on break or disable until re-enabled
        - enable: Re-enable a paused/disabled agent
        - toggle: Backward-compatible alias for explicit state control
        - set_control: Set the desired control state explicitly (Active/Break/Disabled)
        - set_placement: Move an owned agent to a visible space and optionally pin it there
        - create_draft: Create a reviewable agent draft for HITL approval
        - get_draft: Refresh a persisted draft by id
        - edit_draft: Update editable draft fields before approval
        - approve_draft: Approve and execute a draft with the user's JWT
        - reject_draft/cancel_draft: Dismiss a draft without creating an agent
        - group_list/group_get/group_create/group_update/group_delete/group_add_members/
          group_remove_member/group_send: Manage and message agent groups from this
          existing agents tool (no standalone agent_groups tool surface).
        """
        ctx = extract_agent_context(token, request)
        permission_bundle = await resolve_space_scoped_permissions(ctx, "agents")
        selected_agent_id = agent_id or target


        if action in _GROUP_ACTION_MAP:
            return await agent_groups_action(
                action=_GROUP_ACTION_MAP[action],
                group_id=group_id,
                name=name,
                description=description,
                member_agent_ids=member_agent_ids,
                agent_id=group_member_agent_id or agent_id,
                visibility=visibility,
                is_archived=is_archived,
                is_dynamic=is_dynamic,
                dynamic_rules=dynamic_rules,
                include_archived=include_archived,
                content=content,
                ctx=ctx,
            )

        if action == "list":
            normalized_view_scope = view_scope or "space"
            active_space_id = str(ctx.get("space_id") or "").strip()
            params = {"limit": limit, "offset": offset}
            if normalized_view_scope == "mine":
                params["owner"] = "me"

            # Cross-space ownership views need the management roster in every
            # permission mode; backend auth scopes user and route-bound-agent
            # responses without MCP reimplementing ownership rules.
            include_owned_cross_space = normalized_view_scope in {"mine", "all"}
            # Pass space_id as query param to roster reads so backend uses the
            # intended active space, not stale JWT session space. Management
            # reads use X-Space-Id plus the backend's actual Agent.space_id;
            # backend auth remains the enforcement boundary for this endpoint.
            if active_space_id and not include_owned_cross_space:
                params["space_id"] = active_space_id

            if include_owned_cross_space:
                spaces_task = asyncio.create_task(_fetch_visible_spaces(ctx))
                fetch_task = asyncio.create_task(
                    _parallel_fetch(ctx, params, path=_AGENTS_MANAGEMENT_PATH)
                )
                spaces = await spaces_task
                result, avail_map = await fetch_task
            else:
                spaces = None
                result, avail_map = await _parallel_fetch(ctx, params, path=_AGENTS_ROSTER_PATH)

            spaces_by_id = _space_lookup(spaces or [])
            active_space = spaces_by_id.get(active_space_id, {})
            if not active_space and active_space_id:
                space_context = permission_bundle.get("space_context")
                active_space = {
                    "id": active_space_id,
                    "name": space_context.get("name") if isinstance(space_context, dict) else None,
                }

            result = dict(result)
            result["scope"] = normalized_view_scope
            result["cross_space_loaded"] = include_owned_cross_space
            return _agents_widget_result(
                result,
                action,
                avail_map,
                permission_bundle=permission_bundle,
                spaces=spaces,
                source_space=active_space,
                extra_items=[],
                filter_current_space_id=active_space_id if normalized_view_scope == "space" else None,
            )

        if action == "get":
            if not selected_agent_id:
                return {"error": "agent_id is required for action=get"}
            detail_task = asyncio.create_task(
                _api_request_with_ctx(ctx, "GET", f"/api/v1/agents/{selected_agent_id}")
            )
            avail_task = asyncio.create_task(_fetch_availability_map(ctx))
            spaces_task = asyncio.create_task(_fetch_visible_spaces(ctx))
            result = await detail_task
            if result.get("error"):
                for task in (avail_task, spaces_task):
                    task.cancel()
                await asyncio.gather(avail_task, spaces_task, return_exceptions=True)
                return {"error": result.get("detail") or result.get("error")}
            avail_map = await avail_task
            spaces = await spaces_task
            return _agents_widget_result(
                dict(result),
                action,
                avail_map,
                permission_bundle=permission_bundle,
                spaces=spaces,
            )

        if action == "update":
            if not await _agent_write_allowed(ctx, permission_bundle, selected_agent_id, "can_update"):
                return _agents_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=_agent_owner_only_message(permission_bundle),
                )
            if not selected_agent_id:
                return {"error": "agent_id is required for action=update"}

            try:
                normalized_avatar = _normalize_avatar(avatar_url, avatar_emoji)
            except ValueError as exc:
                return {"error": str(exc)}

            payload = {
                key: value
                for key, value in {
                    "bio": bio,
                    "specialization": specialization,
                    "description": description,
                    "avatar_url": normalized_avatar,
                    "model": model,
                    "status": status,
                    "enabled_tools": enabled_tools,
                    "capabilities": declared_capabilities,
                }.items()
                if value is not None
            }
            if not payload:
                return {
                    "error": (
                        "At least one editable field is required for action=update: "
                        "bio, specialization, description, avatar_url (or avatar_emoji), "
                        "model, declared_capabilities, status, enabled_tools"
                    )
                }

            result = await _api_request_with_ctx(
                ctx,
                "PATCH",
                f"/api/v1/agents/{selected_agent_id}",
                json_data=payload,
            )
            return _agents_widget_result(
                result,
                action,
                permission_bundle=permission_bundle,
            )

        if action in _PLACEMENT_ACTIONS:
            if not await _agent_write_allowed(ctx, permission_bundle, selected_agent_id, "can_update"):
                return _agents_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=_agent_owner_only_message(permission_bundle),
                )
            if not selected_agent_id:
                return {"error": "agent_id is required for action=set_placement"}
            normalized_space_id = str(space_id or "").strip()
            if not normalized_space_id:
                return {"error": "space_id is required for action=set_placement"}
            if pinned is None:
                return {"error": "pinned is required for action=set_placement"}

            result = await _api_request_with_ctx(
                ctx,
                "POST",
                f"/api/v1/agents/{selected_agent_id}/placement",
                json_data={"space_id": normalized_space_id, "pinned": pinned},
            )
            if not result.get("error"):
                try:
                    detail = await _api_request_with_ctx(
                        ctx,
                        "GET",
                        f"/api/v1/agents/{selected_agent_id}",
                    )
                    if isinstance(detail, dict) and not detail.get("error"):
                        if isinstance(detail.get("agent"), dict):
                            detail["agent"].setdefault("space_locked", pinned)
                            detail["agent"].setdefault("space_id", normalized_space_id)
                        elif detail.get("id") or detail.get("agent_id"):
                            detail.setdefault("space_locked", pinned)
                            detail.setdefault("space_id", normalized_space_id)
                        detail.setdefault("scope", "placement")
                        detail.setdefault("hint", "Agent placement updated.")
                        return _agents_widget_result(
                            detail,
                            action,
                            permission_bundle=permission_bundle,
                        )
                except Exception:
                    logger.debug("Failed to refresh agent detail after placement update for %s", selected_agent_id, exc_info=True)

            fallback = {
                "scope": result.get("scope", "placement"),
                "hint": result.get("detail") or result.get("hint") or ("Agent placement updated." if not result.get("error") else None),
                "items": [{
                    "id": result.get("agent_id") or selected_agent_id,
                    "agent_id": result.get("agent_id") or selected_agent_id,
                    "name": result.get("agent_name"),
                    "space_id": result.get("space_id") or normalized_space_id,
                    "space_locked": bool(result.get("pinned")) if not result.get("error") else pinned,
                }],
            }
            if result.get("error"):
                fallback["error"] = result.get("error")
                if result.get("detail"):
                    fallback["detail"] = result.get("detail")
            return _agents_widget_result(
                fallback,
                action,
                permission_bundle=permission_bundle,
            )

        if action in _DRAFT_ACTIONS:
            if not permissions_allow(permission_bundle, "can_use_hitl_approval"):
                return _agents_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=blocked_reason(permission_bundle),
                )

            if action == "create_draft":
                agent_name = (name or "").strip()
                if not agent_name:
                    return {"error": "name is required for action=create_draft"}
                if agent_mode == "with_credentials":
                    return {"error": "agent_mode=with_credentials requires credential handling and is not exposed in this widget yet"}
                payload = {
                    "agent_mode": agent_mode or "sandbox",
                    "agent": _draft_agent_payload(
                        name=agent_name,
                        description=description,
                        system_prompt=system_prompt,
                        model=model,
                    ),
                    "target_space_id": space_id or ctx.get("space_id"),
                    "enabled_tools": enabled_tools,
                    "requested_scopes": requested_scopes,
                    "management_rights": management_rights,
                    "idempotency_key": idempotency_key,
                }
                # Drop unset optional fields so backend validation sees the
                # same shape as native platform draft callers.
                payload = {key: value for key, value in payload.items() if value is not None}
                result = await _api_request_with_ctx(
                    ctx,
                    "POST",
                    "/api/v1/drafts/agents",
                    json_data=payload,
                )
                return _agent_draft_widget_result(
                    result,
                    "create_draft",
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
                return _agent_draft_widget_result(
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
                return _agent_draft_widget_result(
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
            return _agent_draft_widget_result(
                result,
                action,
                permission_bundle=permission_bundle,
            )

        if action in _CONTROL_ACTIONS | {"toggle"}:
            if not await _agent_write_allowed(ctx, permission_bundle, selected_agent_id, "can_control"):
                return _agents_blocked_result(
                    action,
                    permission_bundle=permission_bundle,
                    message=_agent_owner_only_message(permission_bundle),
                )
            resolved_action, desired_state = _resolve_control_action(action, state)
            if not selected_agent_id:
                return {"error": f"agent_id is required for action={action}"}
            if action in {"toggle", "set_control"} and desired_state not in _CONTROL_STATES:
                return {"error": f"state is required for action={action} (active, break, disabled)"}
            if resolved_action == "disable" and duration_minutes is not None and duration_minutes <= 0:
                return {"error": "duration_minutes must be > 0 when provided"}
            if desired_state == "break" and duration_minutes is None:
                return {"error": "duration_minutes is required when state=break"}
            if desired_state == "disabled":
                duration_minutes = None
            if resolved_action == "enable":
                duration_minutes = None

            if not confirmed:
                return _agent_control_review_result(
                    agent_id=selected_agent_id,
                    agent_name=str(selected_agent_id),
                    action="set_control" if action in {"toggle", "set_control"} else resolved_action,  # type: ignore[arg-type]
                    desired_state=desired_state or ("active" if resolved_action == "enable" else "disabled"),
                    duration_minutes=duration_minutes,
                    reason=reason,
                    permission_bundle=permission_bundle,
                )

            result = await _apply_control_action(
                ctx=ctx,
                agent_id=selected_agent_id,
                action=resolved_action,  # type: ignore[arg-type]
                desired_state=desired_state or ("active" if resolved_action == "enable" else "disabled"),
                duration_minutes=duration_minutes,
                reason=reason,
            )
            return _control_widget_result(
                result,
                action,
                desired_state or ("active" if resolved_action == "enable" else "disabled"),
                agent_id=selected_agent_id,
                permission_bundle=permission_bundle,
            )

        return {
            "error": (
                f"Unknown action: {action}. Available: list, update, disable, enable, "
                "toggle, set_control, set_placement, create_draft, get_draft, edit_draft, approve_draft, "
                "reject_draft, cancel_draft"
            )
        }


async def _parallel_fetch(
    ctx: dict[str, Any], params: dict[str, Any], *, path: str = _AGENTS_ROSTER_PATH,
) -> tuple[dict[str, Any], Optional[dict[str, dict[str, Any]]]]:
    """Fetch agent list and availability in parallel."""
    agents_task = asyncio.create_task(
        _api_request_with_ctx(
            ctx,
            "GET", path,
            params=params,
        )
    )
    avail_task = asyncio.create_task(_fetch_availability_map(ctx))

    try:
        result = await agents_task
    except Exception:
        avail_task.cancel()
        raise

    try:
        avail_map = await asyncio.wait_for(avail_task, timeout=1.5)
    except asyncio.TimeoutError:
        logger.debug("Availability fetch timed out after 1.5s — proceeding without enrichment")
        avail_task.cancel()
        avail_map = None
    except Exception:
        logger.debug("Availability fetch failed — proceeding without enrichment", exc_info=True)
        avail_task.cancel()
        avail_map = None
    return result, avail_map
