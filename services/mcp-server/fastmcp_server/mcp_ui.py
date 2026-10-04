"""Shared MCP UI metadata and widget helpers."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastmcp.apps import AppConfig, ResourceCSP
from fastmcp.tools import ToolResult
from fastmcp_server.config import MCP_SERVER_URL

WIDGET_STATIC_DIR = Path(__file__).parent / "resources" / "static" / "widgets"
MCP_APPS_BRIDGE_VERSION = "2.0.3"
MCP_APPS_BRIDGE_PATH = f"/mcp/assets/ext-apps-{MCP_APPS_BRIDGE_VERSION}.js"
D3_VERSION = "7.9.0"
D3_ASSET_PATH = f"/mcp/assets/d3-{D3_VERSION}.min.js"
_public_url = urlsplit(MCP_SERVER_URL)
MCP_PUBLIC_ORIGIN = f"{_public_url.scheme}://{_public_url.netloc}"
MCP_APPS_BRIDGE_URL = MCP_PUBLIC_ORIGIN + MCP_APPS_BRIDGE_PATH
WIDGET_RESOURCE_DOMAINS = [
    MCP_PUBLIC_ORIGIN,
    # Agent dashboard avatars are rendered as static image resources. Keep this
    # list in sync with agent-dashboard.html's AVATAR_RESOURCE_ORIGINS and
    # AVATAR_UPLOAD_ORIGINS so the embedded MCP Apps CSP permits every remote
    # avatar origin the widget accepts.
    "https://avatars.githubusercontent.com",
    "https://github.com",
    "https://raw.githubusercontent.com",
    "https://user-images.githubusercontent.com",
]
WIDGET_CONNECT_DOMAINS: list[str] = []


def load_widget_html(path: Path) -> str:
    """Resolve the local bridge URL for isolated MCP Apps iframe resources."""
    return path.read_text(encoding="utf-8").replace(
        "__COMMONFLAME_MCP_APPS_BRIDGE_URL__", MCP_APPS_BRIDGE_URL,
    ).replace(
        "__COMMONFLAME_D3_URL__", MCP_PUBLIC_ORIGIN + D3_ASSET_PATH,
    )


@dataclass(frozen=True)
class WidgetSpec:
    tool_name: str
    resource_uri: str
    filename: str
    title: str
    description: str
    primitive: str
    surface: str
    actions: tuple[str, ...] = ()
    core: bool = True


EXPERIMENTAL_GAMES_FLAG = "AX_ENABLE_EXPERIMENTAL_GAMES"


WIDGET_SPECS: dict[str, WidgetSpec] = {
    # ── Tasks ───────────────────────────────────────────
    # Context-specific URIs: list → board, get/create/update → detail
    "tasks": WidgetSpec(
        tool_name="tasks",
        resource_uri="ui://tasks/board",
        filename="task-board.html",
        title="Task Board",
        description="Kanban board for tasks with priority and status transitions.",
        primitive="tasks",
        surface="board",
        actions=("list", "create", "update", "close", "complete", "remind"),
    ),
    "tasks/detail": WidgetSpec(
        tool_name="tasks",
        resource_uri="ui://tasks/detail",
        filename="task-board.html",  # Same HTML — handles both views via kind
        title="Task Detail",
        description="Single task detail with status updates and activity.",
        primitive="tasks",
        surface="detail",
        actions=("get", "update", "close", "complete", "remind"),
    ),
    # ── Messages ────────────────────────────────────────
    "messages": WidgetSpec(
        tool_name="messages",
        resource_uri="ui://messages/timeline",
        filename="message-timeline.html",
        title="Message Timeline",
        description="Rich message timeline with replies, reactions, and compose flow.",
        primitive="messages",
        surface="timeline",
        actions=("list", "send", "reply", "react"),
    ),
    # ── Agents ──────────────────────────────────────────
    "agents": WidgetSpec(
        tool_name="agents",
        resource_uri="ui://agents/dashboard",
        filename="agent-dashboard.html",
        title="Agent Dashboard",
        description="Agent list and profiles with presence, trust, and capabilities.",
        primitive="agents",
        surface="dashboard",
        actions=("list", "inspect", "draft", "approve"),
    ),
    # ── Spaces ──────────────────────────────────────────
    "spaces": WidgetSpec(
        tool_name="spaces",
        resource_uri="ui://spaces/navigator",
        filename="space-navigator.html",
        title="Space Navigator",
        description="Browse spaces, view details and members.",
        primitive="spaces",
        surface="navigator",
        actions=("list", "inspect", "switch", "discover", "join", "invite"),
    ),
    # ── Search ──────────────────────────────────────────
    "search": WidgetSpec(
        tool_name="search",
        resource_uri="ui://search/results",
        filename="search-results.html",
        title="Search Results",
        description="Message search results with previews and filters.",
        primitive="search",
        surface="results",
        actions=("query", "open"),
    ),
    # ── Context ─────────────────────────────────────────
    "context": WidgetSpec(
        tool_name="context",
        resource_uri="ui://context/explorer",
        filename="context-explorer.html",
        title="Context Explorer",
        description="Key-value context browser with drill-down.",
        primitive="context",
        surface="explorer",
        actions=("list", "inspect", "open_artifact"),
    ),
    "context/graph": WidgetSpec(
        tool_name="context",
        resource_uri="ui://context/graph",
        filename="context-graph.html",
        title="Context Graph",
        description="Force-directed knowledge graph of context entries, wikilinks, and tags.",
        primitive="context",
        surface="graph",
        actions=("query", "inspect"),
    ),
    # ── Whoami ──────────────────────────────────────────
    "whoami": WidgetSpec(
        tool_name="whoami",
        resource_uri="ui://whoami/identity",
        filename="agent-identity.html",
        title="Agent Identity",
        description="Agent identity card with profile, capabilities, and persistent memory.",
        primitive="identity",
        surface="card",
        actions=("inspect",),
    ),
}

EXPERIMENTAL_WIDGET_SPECS: dict[str, WidgetSpec] = {
    # ── Games ───────────────────────────────────────────
    "games": WidgetSpec(
        tool_name="games",
        resource_uri="ui://games/tic-tac-toe",
        filename="game-board.html",
        title="Game Board",
        description="Turn-based game board with state, code, and event views.",
        primitive="games",
        surface="board",
        actions=("start", "move", "answer"),
        core=False,
    ),
}

# Intentionally unpublished until ALERT-001 lands with a dedicated alerts
# MCP tool and backend API contract. Keeping it out of WIDGET_SPECS prevents
# the resource registry and /apps/ endpoint from exposing a preview shell as a
# production-ready widget.

# Legacy URI aliases — old URIs still resolve to the right spec
LEGACY_URI_ALIASES: dict[str, str] = {
    "ui://task-board": "tasks",
    "ui://message-timeline": "messages",
    "ui://agent-dashboard": "agents",
    "ui://space-navigator": "spaces",
    "ui://search-results": "search",
    "ui://context-explorer": "context",
    "ui://agent-identity": "whoami",
}

# Action-to-widget-key mapping for context-specific URIs
ACTION_WIDGET_MAP: dict[str, dict[str, str]] = {
    "tasks": {
        "list": "tasks",
        "get": "tasks/detail",
        "create": "tasks/detail",
        "update": "tasks/detail",
    },
    "agents": {
        "group_list": "agents",
        "group_get": "agents",
        "group_create": "agents",
        "group_update": "agents",
        "group_delete": "agents",
        "group_add_members": "agents",
        "group_remove_member": "agents",
        "group_send": "agents",
    },
}

TOOL_WIDGET_ALIASES: dict[str, str] = {}


def _action_form(
    parameters: list[str],
    *,
    required: list[str] | None = None,
    mode: str = "default",
) -> dict[str, Any]:
    """Build a compact host-facing action form descriptor."""
    return {
        "mode": mode,
        "parameters": parameters,
        "required": ["action"] if required is None else required,
    }


# Host/widget action-specific parameter forms for tools whose MCP inputSchema is
# necessarily a broad one-callable superset. These contracts let generic hosts
# render economical forms for the selected action instead of exposing every
# optional parameter at once. Context has a richer governed-catalog contract in
# fastmcp_server.tools.context and is merged at that decorator site.
_ACTION_FORMS: dict[str, dict[str, Any]] = {
    "tasks": {
        "version": 1,
        "default_mode": "browse",
        "summary": "Task forms are scoped by action so list/get/nudge stay small and create/update reveal write fields only when selected.",
        "hidden_parameters": ["space_id"],
        "actions": {
            "list": _action_form(["action", "status", "limit", "offset", "filter"], mode="browse"),
            "get": _action_form(["action", "task_id"], required=["action", "task_id"], mode="detail"),
            "create": _action_form(
                [
                    "action", "title", "description", "requirements", "priority", "status",
                    "deadline", "assignee_type", "assignee_id", "assigned_agent_id",
                    "reminder_action", "reminder_cadence_minutes",
                    "reminder_max_count", "reminder_until_done", "next_fire_at",
                ],
                required=["action", "title"],
                mode="write",
            ),
            "update": _action_form(
                [
                    "action", "task_id", "title", "description", "requirements", "priority",
                    "status", "deadline", "assignee_type", "assignee_id", "assigned_agent_id",
                    "completed_at", "reminder_action", "reminder_cadence_minutes",
                    "reminder_max_count", "reminder_until_done", "next_fire_at", "snoozed_until",
                ],
                required=["task_id"],
                mode="write",
            ),
            "nudge": _action_form(["action", "task_id"], required=["action", "task_id"], mode="reminder"),
            "reminder_pause": _action_form(["action", "reminder_paused", "reminder_pause_reason"], required=["action"], mode="reminder"),
        },
    },
    "messages": {
        "version": 1,
        "default_mode": "inbox",
        "summary": "Message forms keep check/list separate from compose, draft, reaction, edit, and delete flows.",
        "actions": {
            "check": _action_form(["action", "limit", "mark_read", "filter", "show_own_messages", "reason", "curate", "curate_max_wait"], mode="inbox"),
            "send": _action_form(["action", "content", "reply_to", "wait", "max_wait", "bypass", "status"], required=["action", "content"], mode="compose"),
            "ask_ax": _action_form(["action", "content", "wait", "max_wait", "status"], required=["action", "content"], mode="compose"),
            "draft": _action_form(["action", "content", "reply_to"], required=["action", "content"], mode="compose"),
            "react": _action_form(["action", "reply_to", "content"], required=["action", "reply_to", "content"], mode="mutation"),
            "edit": _action_form(["action", "message_id", "content"], required=["action", "message_id", "content"], mode="mutation"),
            "delete": _action_form(["action", "message_id", "reason"], required=["action", "message_id", "reason"], mode="destructive"),
        },
    },
    "agents": {
        "version": 1,
        "default_mode": "directory",
        "summary": "Agent forms separate read/directory actions, profile updates, control-state changes, HITL drafts, placement, and group operations.",
        "actions": {
            "list": _action_form(["action", "limit", "offset", "view_scope", "management_rights"], mode="directory"),
            "get": _action_form(["action", "agent_id", "target", "management_rights"], required=["action", "agent_id"], mode="profile"),
            "update": _action_form(
                [
                    "action", "agent_id", "target", "bio", "specialization", "description",
                    "avatar_url", "avatar_emoji", "model", "status", "enabled_tools",
                    "declared_capabilities",
                ],
                required=["action", "agent_id"],
                mode="profile_write",
            ),
            "set_control": _action_form(["action", "agent_id", "target", "state", "duration_minutes", "reason", "confirmed"], required=["action", "agent_id", "state"], mode="control"),
            "disable": _action_form(["action", "agent_id", "target", "state", "duration_minutes", "reason", "confirmed"], required=["action", "agent_id"], mode="control"),
            "enable": _action_form(["action", "agent_id", "target", "reason", "confirmed"], required=["action", "agent_id"], mode="control"),
            "toggle": _action_form(["action", "agent_id", "target", "state", "reason", "confirmed"], required=["action", "agent_id", "state"], mode="control"),
            "set_placement": _action_form(["action", "agent_id", "target", "space_id", "pinned"], required=["action", "agent_id", "space_id", "pinned"], mode="placement"),
            "create_draft": _action_form(["action", "name", "description", "system_prompt", "model", "agent_mode", "agent_type", "declared_capabilities", "enabled_tools", "requested_scopes"], required=["action", "name"], mode="hitl_draft"),
            "get_draft": _action_form(["action", "draft_id"], required=["action", "draft_id"], mode="hitl_draft"),
            "edit_draft": _action_form(["action", "draft_id", "version", "changes"], required=["action", "draft_id", "version", "changes"], mode="hitl_draft"),
            "approve_draft": _action_form(["action", "draft_id", "version", "idempotency_key"], required=["action", "draft_id", "version"], mode="hitl_draft"),
            "reject_draft": _action_form(["action", "draft_id", "version", "reason"], required=["action", "draft_id", "version"], mode="hitl_draft"),
            "cancel_draft": _action_form(["action", "draft_id", "version", "reason"], required=["action", "draft_id", "version"], mode="hitl_draft"),
            "group_list": _action_form(["action", "include_archived", "limit", "offset"], mode="groups"),
            "group_get": _action_form(["action", "group_id"], required=["action", "group_id"], mode="groups"),
            "group_create": _action_form(["action", "name", "description", "member_agent_ids", "visibility", "is_dynamic", "dynamic_rules"], required=["action", "name"], mode="groups"),
            "group_update": _action_form(["action", "group_id", "name", "description", "visibility", "is_archived", "is_dynamic", "dynamic_rules"], required=["action", "group_id"], mode="groups"),
            "group_delete": _action_form(["action", "group_id", "confirmed", "reason"], required=["action", "group_id"], mode="groups"),
            "group_add_members": _action_form(["action", "group_id", "member_agent_ids"], required=["action", "group_id", "member_agent_ids"], mode="groups"),
            "group_remove_member": _action_form(["action", "group_id", "group_member_agent_id"], required=["action", "group_id", "group_member_agent_id"], mode="groups"),
            "group_send": _action_form(["action", "group_id", "content"], required=["action", "group_id", "content"], mode="groups"),
        },
    },
    "spaces": {
        "version": 1,
        "default_mode": "navigator",
        "summary": "Space forms separate browsing/navigation, membership, invites, updates, and HITL draft creation.",
        "actions": {
            "list": _action_form(["action"], mode="navigator"),
            "current": _action_form(["action"], mode="navigator"),
            "get": _action_form(["action", "space_id", "slug"], required=["action"], mode="detail"),
            "members": _action_form(["action", "space_id"], required=["action", "space_id"], mode="detail"),
            "switch": _action_form(["action", "space_id"], required=["action", "space_id"], mode="navigation"),
            "create": _action_form(["action", "name", "description", "visibility"], required=["action", "name"], mode="write"),
            "update": _action_form(["action", "space_id", "name", "description", "visibility", "is_archived"], required=["action", "space_id"], mode="write"),
            "create_draft": _action_form(["action", "name", "description", "visibility", "space_mode"], required=["action", "name", "space_mode"], mode="hitl_draft"),
            "get_draft": _action_form(["action", "draft_id"], required=["action", "draft_id"], mode="hitl_draft"),
            "edit_draft": _action_form(["action", "draft_id", "version", "changes"], required=["action", "draft_id", "version", "changes"], mode="hitl_draft"),
            "approve_draft": _action_form(["action", "draft_id", "version", "idempotency_key"], required=["action", "draft_id", "version"], mode="hitl_draft"),
            "reject_draft": _action_form(["action", "draft_id", "version"], required=["action", "draft_id", "version"], mode="hitl_draft"),
            "cancel_draft": _action_form(["action", "draft_id", "version"], required=["action", "draft_id", "version"], mode="hitl_draft"),
            "discover": _action_form(["action"], mode="membership"),
            "list_public": _action_form(["action"], mode="membership"),
            "join_public": _action_form(["action", "space_id"], required=["action", "space_id"], mode="membership"),
            "join_invite": _action_form(["action", "invite_code"], required=["action", "invite_code"], mode="membership"),
            "create_invite": _action_form(["action", "space_id", "expires_hours", "max_uses"], required=["action", "space_id"], mode="invite"),
            "invite_details": _action_form(["action", "invite_code"], required=["action", "invite_code"], mode="invite"),
        },
    },
    "whoami": {
        "version": 1,
        "default_mode": "identity",
        "summary": "Identity forms separate profile reads, profile updates, memory, and follow relationships.",
        "hidden_parameters": ["space_id"],
        "actions": {
            "get": _action_form(["action"], mode="identity"),
            "update": _action_form(["action", "bio", "specialization", "capabilities", "preferences", "projects", "avatar_url", "avatar_emoji"], required=["action"], mode="profile_write"),
            "remember": _action_form(["action", "key", "value"], required=["action", "key", "value"], mode="memory"),
            "recall": _action_form(["action", "key"], required=["action", "key"], mode="memory"),
            "list": _action_form(["action"], mode="memory"),
            "follow": _action_form(["action", "target_agent", "relationship_type"], required=["action", "target_agent"], mode="relationship"),
            "unfollow": _action_form(["action", "target_agent"], required=["action", "target_agent"], mode="relationship"),
        },
    },
    "search": {
        "version": 1,
        "default_mode": "query",
        "summary": "Search has one compact query form with optional filters and pagination.",
        "hidden_parameters": ["space_id"],
        "actions": {
            "query": _action_form(["query", "limit", "offset", "channel", "sender_type", "date_from", "date_to"], required=["query"], mode="query"),
        },
    },
}


# Advertised MCP outputSchema configuration per platform tool
# (OUTPUT-SCHEMA-001). `kinds` is the closed enum of build_tool_output kinds
# the tool emits; `extra_keys` are documented tool-specific top-level keys;
# `raw_variant` covers legacy non-envelope dict returns ("error" for bare
# {"error": ...} paths, "action" for whoami's action-keyed fallbacks);
# `loose_envelope` relaxes required/state/data for the hand-rolled context
# shapes (ephemeral version 2 vs catalog version 1).
_TOOL_OUTPUT_SHAPES: dict[str, dict[str, Any]] = {
    "whoami": {
        "kinds": ["whoami_profile"],
        "extra_keys": ["active_tab", "highlighted_key", "notice"],
        "raw_variant": "action",
    },
    "messages": {
        "kinds": [
            "message_error",
            "message_timeline",
            "message_draft",
            "message_mutation",
            "message_sent",
        ],
        "extra_keys": ["notice"],
    },
    "tasks": {
        "kinds": ["task_collection", "task_detail", "task_reminder_pause"],
        "extra_keys": ["notice"],
        "raw_variant": "error",
    },
    "agents": {
        "kinds": ["agent_collection", "agent_groups"],
        "extra_keys": ["notice"],
        "raw_variant": "error",
    },
    "spaces": {
        "kinds": ["space_detail", "space_collection", "space_members"],
        "extra_keys": ["notice"],
        "raw_variant": "error",
    },
    "context": {
        "kinds": ["context"],
        "extra_keys": [
            "action",
            "count",
            "items",
            "keys",
            "returned",
            "limit",
            "offset",
            "has_more",
            "list_capped",
            "selected_key",
            "error",
            "detail",
            "requested_space_id",
            "current_space_id",
            "notice",
            "terminated_keys",
            "terminated_count",
            "failed_keys",
            "space_context",
            "permissions",
            "catalog",
            "catalog_actions",
        ],
        "loose_envelope": True,
        "raw_variant": "error",
    },
    "search": {
        "kinds": ["search_results"],
        "extra_keys": [],
    },
}


def experimental_games_enabled() -> bool:
    """Return whether experimental games widgets should be publicly exposed."""
    return os.getenv(EXPERIMENTAL_GAMES_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def get_widget_specs() -> dict[str, WidgetSpec]:
    """Return the widget registry visible in the current runtime."""
    specs = dict(WIDGET_SPECS)
    if experimental_games_enabled():
        specs.update(EXPERIMENTAL_WIDGET_SPECS)
    return specs


def get_widget_manifest() -> list[dict[str, Any]]:
    """Return a stable product manifest for MCP app/widget surfaces."""
    manifest: list[dict[str, Any]] = []
    for name, spec in get_widget_specs().items():
        item = {
            "name": name,
            "tool_name": spec.tool_name,
            "resource_uri": spec.resource_uri,
            "filename": spec.filename,
            "title": spec.title,
            "description": spec.description,
            "primitive": spec.primitive,
            "surface": spec.surface,
            "actions": list(spec.actions),
            "core": spec.core,
        }
        contract = tool_action_forms(spec.tool_name)
        if contract:
            item["action_forms"] = contract
        manifest.append(item)
    return manifest


OAUTH_SECURITY_SCHEME = {
    "type": "oauth2",
    "scopes": ["openid", "ax-api/mcp:read", "ax-api/mcp:write"],
}


def resolve_widget_name(tool_name: str) -> str:
    """Resolve a tool name to the widget spec name it should render with."""
    return TOOL_WIDGET_ALIASES.get(tool_name, tool_name)


def get_widget_spec(tool_name: str) -> WidgetSpec:
    """Get widget metadata for a tool."""
    widget_name = resolve_widget_name(tool_name)
    specs = get_widget_specs()
    if widget_name not in specs:
        raise KeyError(f"No MCP UI widget registered for tool: {tool_name}")
    return specs[widget_name]


def get_widget_resource_uri(tool_name: str, *, action: str | None = None) -> str:
    """Get the ui:// resource URI for a tool, optionally action-specific."""
    key = tool_name
    if action and tool_name in ACTION_WIDGET_MAP:
        key = ACTION_WIDGET_MAP[tool_name].get(action, tool_name)
    return get_widget_spec(key).resource_uri


def get_legacy_widget_resource_uri(tool_name: str) -> str | None:
    """Return the legacy ui:// alias for a base widget, if one exists."""
    canonical_uri = get_widget_spec(tool_name).resource_uri
    for legacy_uri, widget_name in LEGACY_URI_ALIASES.items():
        if widget_name == tool_name and canonical_uri != legacy_uri:
            return legacy_uri
    return None


def get_widget_html_path(tool_name: str) -> Path:
    """Get the local HTML path for a tool's widget."""
    spec = get_widget_spec(tool_name)
    return WIDGET_STATIC_DIR / spec.filename


def tool_app_config(tool_name: str) -> AppConfig:
    """App config for a tool that renders with an MCP UI widget."""
    return AppConfig(
        resource_uri=get_widget_resource_uri(tool_name),
        csp=ResourceCSP(
            connect_domains=WIDGET_CONNECT_DOMAINS,
            resource_domains=WIDGET_RESOURCE_DOMAINS,
            frame_domains=[],
            base_uri_domains=[],
        ),
        prefers_border=True,
    )


def resource_app_config() -> AppConfig:
    """App config for a UI resource."""
    return AppConfig(
        csp=ResourceCSP(
            connect_domains=WIDGET_CONNECT_DOMAINS,
            resource_domains=WIDGET_RESOURCE_DOMAINS,
            frame_domains=[],
            base_uri_domains=[],
        ),
        prefers_border=True,
    )


def tool_action_forms(tool_name: str) -> dict[str, Any] | None:
    """Return action-specific host form metadata for a tool, if defined."""
    contract = _ACTION_FORMS.get(resolve_widget_name(tool_name))
    if contract is None and tool_name == "context":
        # Keep the registry and tools/list aligned with the richer context forms.
        from fastmcp_server.tools.context import _context_action_forms
        contract = _context_action_forms()["ax/actionForms"]
    return copy.deepcopy(contract) if contract else None


def tool_meta(tool_name: str) -> dict[str, Any]:
    """OpenAI-compatible metadata for a tool widget."""
    spec = get_widget_spec(tool_name)
    meta = {
        "securitySchemes": [OAUTH_SECURITY_SCHEME],
        "openai/outputTemplate": get_widget_resource_uri(tool_name),
        "openai/widgetAccessible": True,
        "openai/toolInvocation/invoking": f"Loading {spec.title.lower()}",
        "openai/toolInvocation/invoked": f"{spec.title} ready",
    }
    contract = tool_action_forms(tool_name)
    if contract:
        meta["ax/actionForms"] = contract
    return meta


def read_only_annotations() -> dict[str, Any]:
    """Tool annotations for read-only tools."""
    return {
        "readOnlyHint": True,
        "openWorldHint": False,
        "destructiveHint": False,
    }


def bounded_write_annotations(*, destructive: bool = False) -> dict[str, Any]:
    """Tool annotations for tools that can mutate first-party platform state."""
    return {
        "readOnlyHint": False,
        "openWorldHint": False,
        "destructiveHint": destructive,
    }


def resource_meta(tool_name: str) -> dict[str, Any]:
    """OpenAI-compatible metadata for a widget resource."""
    spec = get_widget_spec(tool_name)
    return {
        "openai/widgetDescription": spec.description,
        "openai/widgetPrefersBorder": True,
        "openai/widgetCSP": {
            "connect_domains": WIDGET_CONNECT_DOMAINS,
            "resource_domains": WIDGET_RESOURCE_DOMAINS,
        },
    }


def widget_tool_result(
    tool_name: str,
    *,
    content: Any,
    structured_content: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    action: str | None = None,
) -> ToolResult:
    """Build a ToolResult with result-level widget metadata."""
    resource_uri = get_widget_resource_uri(tool_name, action=action)
    runtime_meta = {
        # Preferred nested MCP Apps shape.
        "ui": {
            "resourceUri": resource_uri,
        },
        # Deprecated compatibility shape still used by some hosts.
        "ui/resourceUri": resource_uri,
    }
    if meta:
        runtime_meta.update(meta)
    return ToolResult(
        content=content,
        structured_content=structured_content,
        meta=runtime_meta,
    )


def build_notice(
    message: str,
    *,
    severity: str = "info",
    code: str | None = None,
) -> dict[str, Any]:
    """Build a normalized notice payload."""
    notice = {
        "message": message,
        "severity": severity,
    }
    if code:
        notice["code"] = code
    return notice


def build_action(
    action_id: str,
    label: str,
    *,
    kind: str,
    target: str,
    args: dict[str, Any] | None = None,
    style: str | None = None,
    requires_confirmation: bool | None = None,
    enabled: bool | None = None,
    disabled_reason: str | None = None,
    busy: bool | None = None,
    idempotent: bool | None = None,
) -> dict[str, Any]:
    """Build a normalized action descriptor."""
    action: dict[str, Any] = {
        "id": action_id,
        "label": label,
        "kind": kind,
        "target": target,
    }
    if args:
        action["args"] = args
    if style:
        action["style"] = style
    if requires_confirmation is not None:
        action["requires_confirmation"] = requires_confirmation
    if enabled is not None:
        action["enabled"] = enabled
    if disabled_reason:
        action["disabled_reason"] = disabled_reason
    if busy is not None:
        action["busy"] = busy
    if idempotent is not None:
        action["idempotent"] = idempotent
    return action


def build_tool_output(
    kind: str,
    version: int,
    state: str,
    data: dict[str, Any],
    *,
    actions: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a normalized action-level structuredContent envelope."""
    payload: dict[str, Any] = {
        "kind": kind,
        "version": version,
        "state": state,
        "data": data,
    }
    if actions:
        payload["actions"] = actions
    if warnings:
        payload["warnings"] = warnings
    if errors:
        payload["errors"] = errors
    return payload


def _envelope_schema_variant(shape: dict[str, Any]) -> dict[str, Any]:
    """Build the build_tool_output envelope variant for one tool shape."""
    properties: dict[str, Any] = {
        "kind": {"type": "string", "enum": list(shape["kinds"])},
        "version": {"type": "integer"},
        "state": {"type": "string"},
        "data": {"type": "object"},
        "actions": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array", "items": {"type": "object"}},
        "errors": {"type": "array", "items": {"type": "object"}},
    }
    required = ["kind", "version", "state", "data"]
    if shape.get("loose_envelope"):
        # Hand-rolled context shapes: ephemeral (version 2) has no state/data;
        # catalog (version 1) uses an object state. Only kind+version are
        # shared invariants.
        properties["state"] = {}
        properties["data"] = {}
        required = ["kind", "version"]
    for key in shape.get("extra_keys", []):
        # build_notice always produces an object; other extras stay
        # unconstrained in v1.
        properties[key] = {"type": "object"} if key == "notice" else {}
    return {
        "type": "object",
        "description": "Commonflame tool-output envelope (build_tool_output)",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }


_RAW_SCHEMA_VARIANTS: dict[str, dict[str, Any]] = {
    # Bare {"error": ...} validation dicts and backend api_request
    # passthrough ({"error", "detail"} plus richer tasks failure keys).
    "error": {
        "type": "object",
        "description": "Legacy non-envelope error result",
        "properties": {
            "error": {"type": "string"},
            "detail": {"type": "string"},
        },
        "required": ["error"],
        "additionalProperties": True,
    },
    # whoami fallback branch: every non-envelope structuredContent carries a
    # top-level action string (validation/API errors, follow/unfollow
    # success, list-error fallback).
    "action": {
        "type": "object",
        "description": "Legacy whoami fallback result",
        "properties": {
            "action": {"type": "string"},
            "error": {"type": "string"},
            "detail": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": True,
    },
}


def tool_output_schema(tool_name: str) -> dict[str, Any]:
    """Return the advertised MCP outputSchema for a platform tool.

    Driven by _TOOL_OUTPUT_SHAPES above. Raises KeyError for unknown tools
    so a typo at a decorator site fails at import time.
    """
    shape = _TOOL_OUTPUT_SHAPES[tool_name]
    envelope = _envelope_schema_variant(shape)
    raw_variant = shape.get("raw_variant")
    if not raw_variant:
        return envelope
    # MCP requires a top-level object schema; both variants are objects, so
    # the wrapping "type": "object" is equivalent and satisfies FastMCP's
    # registration check. Deep-copy the raw variant so callers mutating a
    # returned schema cannot contaminate other tools' advertised schemas.
    return {
        "type": "object",
        "anyOf": [envelope, copy.deepcopy(_RAW_SCHEMA_VARIANTS[raw_variant])],
    }
