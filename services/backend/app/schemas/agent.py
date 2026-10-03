"""
Unified Agent API schemas — single source of truth for request/response models.

All agent endpoints under /api/v1/agents/ use these schemas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AgentOrigin(str, Enum):
    cloud = "cloud"
    mcp = "mcp"
    external_gateway = "external_gateway"
    agentcore = "agentcore"
    space_agent = "space_agent"


class AgentStatus(str, Enum):
    active = "active"
    inactive = "inactive"
    paused = "paused"
    disabled = "disabled"
    quarantined = "quarantined"
    suspended = "suspended"


class DetailLevel(str, Enum):
    minimal = "minimal"
    summary = "summary"
    full = "full"


class SortOrder(str, Enum):
    name = "name"
    recent = "recent"
    relevance = "relevance"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class AgentCreateRequest(BaseModel):
    """Create a new agent."""

    name: str = Field(..., min_length=3, max_length=50)
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    space_id: str | None = Field(None, description="Target space. Defaults to session space.")
    origin: AgentOrigin | None = Field(None, description="Agent origin: cloud, mcp, external_gateway.")
    enabled_tools: dict[str, bool] | None = None
    template_type: str | None = None
    webhook_url: str | None = Field(None, description="Webhook URL for external_gateway agents.")
    avatar_url: str | None = None
    can_manage_agents: bool = False

    @field_validator("origin", mode="before")
    @classmethod
    def normalize_origin(cls, v: Any) -> Any:
        if v is None:
            return v
        if isinstance(v, str):
            return v.lower()
        return v


class AgentUpdateRequest(BaseModel):
    """Update an existing agent. All fields optional — only provided fields are changed."""

    name: str | None = None
    description: str | None = None
    bio: str | None = None
    specialization: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    status: AgentStatus | None = None
    avatar_url: str | None = None
    enabled_tools: dict[str, bool] | None = None
    template_type: str | None = None
    webhook_url: str | None = None
    can_manage_agents: bool | None = None
    capabilities: dict | list | None = None
    space_locked: bool | None = None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class AgentResponse(BaseModel):
    """Agent response — field set controlled by ?detail= query parameter."""

    # --- minimal fields (always present) ---
    id: str
    name: str
    description: str | None = None
    origin: str
    status: str
    space_id: str
    space_name: str | None = None
    space_locked: bool | None = None
    created_at: str | None = None
    user_id: str | None = None
    owner_username: str | None = None
    is_own: bool | None = None
    can_control: bool | None = None
    can_update: bool | None = None

    # --- summary fields (detail=summary, default) ---
    agent_type: str | None = None
    model: str | None = None
    enabled_tools: dict[str, bool] | None = None
    can_manage_agents: bool | None = None
    avatar_url: str | None = None
    bio: str | None = None
    specialization: str | None = None
    template_type: str | None = None
    updated_at: str | None = None
    global_state: str | None = None
    lifecycle_state: str | None = None
    last_active_at: str | None = None
    roster_group: str | None = None
    is_online: bool | None = None
    is_lifecycle_active: bool | None = None
    messages_routable: bool | None = None
    routable: bool | None = None
    expected_response: str | None = None
    connection_path: str | None = None
    pinned_to_space: str | None = None
    pinned_space_id: str | None = None
    is_pinned: bool | None = None
    permanent: bool | None = None
    is_core_agent: bool | None = None
    protected_reasons: list[str] | None = None

    # --- full fields (detail=full) ---
    home_space_id: str | None = None
    home_space_name: str | None = None
    system_prompt: str | None = None
    capabilities: Any | None = None
    visibility_level: str | None = None
    webhook_url: str | None = None
    webhook_verified: bool | None = None
    cloud_function_url: str | None = None
    reputation_score: float | None = None
    feedback_score: float | None = None
    feedback_count: int | None = None
    total_jobs_completed: int | None = None

    # --- rich extras (populated on detail=full only) ---
    default_space_id: str | None = None
    space_access: list[dict] | None = None
    control: dict | None = None
    stats: dict | None = None


class AgentListResponse(BaseModel):
    """Paginated agent list."""

    agents: list[AgentResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _coerce_heartbeat_dt(raw: Any) -> datetime | None:
    """Parse a heartbeat value (datetime / epoch / ISO string) into a datetime, or None."""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def derive_presence_fields(presence: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Canonical heartbeat-freshness fields from a Redis presence dict.

    Returns ``{last_heartbeat: ISO|None, presence_fresh: bool, presence_age_seconds: float|None,
    presence_source: str|None}``. ``presence_source`` is the Redis presence ``source`` (e.g.
    "sse", "heartbeat", "gateway") passed through verbatim so the frontend can label connection
    type on the @mention / quick-action surfaces.
    Tz-naive heartbeats (the SSE writers store NAIVE UTC via ``datetime.utcnow().isoformat()``)
    are treated as UTC. Shared by the canonical roster serializer (``serialize_agent`` /
    GET ``/api/v1/agents``) AND the legacy management serializer (GET ``/auth/agents``) so the
    two endpoints emit identical availability data — divergence here is what caused the
    agents-tab "warming up" regression. Contract pinned in
    tests/unit/test_agent_roster_presence.py.
    """
    now = now or datetime.now(timezone.utc)
    last_heartbeat_iso: str | None = None
    presence_fresh = False
    presence_age_seconds: float | None = None
    presence_source: str | None = None
    if isinstance(presence, dict):
        presence_source = presence.get("source")
        raw_hb = presence.get("last_heartbeat") or presence.get("connected_at")
        if raw_hb:
            hb_dt = _coerce_heartbeat_dt(raw_hb)
            if hb_dt is not None:
                if hb_dt.tzinfo is None:
                    hb_dt = hb_dt.replace(tzinfo=timezone.utc)
                last_heartbeat_iso = hb_dt.isoformat()
                presence_age_seconds = (now - hb_dt).total_seconds()
                presence_fresh = presence_age_seconds < 60
    return {
        "last_heartbeat": last_heartbeat_iso,
        "presence_fresh": presence_fresh,
        "presence_age_seconds": presence_age_seconds,
        "presence_source": presence_source,
    }


def serialize_space_access_row(row: Any) -> dict[str, Any]:
    return {
        "space_id": str(row.space_id),
        "is_default": bool(row.is_default),
        "state": getattr(row, "state", "active") or "active",
    }


def _agent_protection_fields(agent: Any) -> dict[str, Any]:
    """Stable widget fallback for permanent/pinned/core-agent semantics."""

    pinned_space_id = getattr(agent, "pinned_to_space", None)
    protected_reasons: list[str] = []
    if pinned_space_id:
        protected_reasons.append("pinned_to_space")
    if bool(getattr(agent, "space_locked", False)):
        protected_reasons.append("space_locked")
    if bool(getattr(agent, "platform_managed", False)):
        protected_reasons.append("platform_managed")
    if bool(getattr(agent, "identity_locked", False)):
        protected_reasons.append("identity_locked")
    if bool(getattr(agent, "deletion_protected", False)):
        protected_reasons.append("deletion_protected")
    if (getattr(agent, "management_class", None) or "") == "concierge":
        protected_reasons.append("concierge")
    if (getattr(agent, "owner_type", None) or "") == "platform":
        protected_reasons.append("platform_owner")

    is_core_agent = bool(
        getattr(agent, "platform_managed", False)
        or getattr(agent, "identity_locked", False)
        or getattr(agent, "deletion_protected", False)
        or (getattr(agent, "owner_type", None) or "") == "platform"
        or (getattr(agent, "management_class", None) or "") == "concierge"
    )
    return {
        "pinned_to_space": str(pinned_space_id) if pinned_space_id else None,
        "pinned_space_id": str(pinned_space_id) if pinned_space_id else None,
        "is_pinned": bool(pinned_space_id),
        "permanent": bool(protected_reasons),
        "is_core_agent": is_core_agent,
        "protected_reasons": protected_reasons,
    }


def _agent_messageability_fields(
    agent: Any,
    *,
    is_online: bool,
    global_state: str,
    control: Any = None,
) -> dict[str, Any]:
    """Small roster-side counterpart of the availability endpoint contract."""

    origin = (getattr(agent, "origin", None) or "").lower()
    agent_type = (getattr(agent, "agent_type", None) or "").lower()
    if is_online:
        connection_path = "direct_sse"
    elif origin == "mcp" or agent_type == "mcp":
        connection_path = "mcp_only"
    else:
        connection_path = "unknown"

    control_disabled = False
    routing_only = False
    if isinstance(control, dict):
        control_disabled = bool(control.get("is_disabled") or control.get("disabled"))
        routing_only = bool(control.get("routing_only"))
    disabled = control_disabled or global_state == "disabled" or (getattr(agent, "status", None) in {"disabled", "suspended", "quarantined"})

    if disabled:
        expected_response = "unavailable"
        messages_routable = False
    elif is_online and routing_only:
        expected_response = "routing_only"
        messages_routable = True
    elif is_online:
        expected_response = "immediate"
        messages_routable = True
    elif connection_path == "mcp_only":
        expected_response = "dispatch_delayed"
        messages_routable = True
    else:
        expected_response = "unavailable"
        messages_routable = False

    return {
        "connection_path": connection_path,
        "expected_response": expected_response,
        "messages_routable": messages_routable,
        "routable": messages_routable,
    }


def serialize_agent(
    agent: Any,
    detail: DetailLevel = DetailLevel.summary,
    *,
    current_user_id: str | None = None,
    extras: dict | None = None,
) -> dict:
    """Serialize an Agent model to a response dict at the requested detail level.

    Args:
        agent: Agent SQLAlchemy model instance.
        detail: One of minimal, summary, full.
        current_user_id: If set, populates the ``is_own`` field.
        extras: Additional fields to merge (e.g. control state, stats).
    """
    from app.core.agent_toggles import build_enabled_tools_from_agent
    # ALC: this is the canonical roster serializer. GET /api/v1/agents resolves
    # to agents_unified -> serialize_agent, which shadows api_v1's list_agents,
    # so the agents-tab grouping (#265) folds on the fields emitted *here*.
    # Display lifecycle is computed live from last_active_at because the shadow
    # sweep never persists dormancy (persisted lifecycle_state is uniformly
    # 'active'). getattr keeps partial/legacy agent objects from breaking.
    # (Route-shadowing diagnosed with claude_prime, 2026-05-30.)
    from app.core.agent_lifecycle import DEFAULT_THRESHOLDS, compute_display_lifecycle, compute_roster_group

    _extras = dict(extras or {})
    owner_username = _extras.pop("owner_username", None)
    space_name = _extras.pop("space_name", None)
    home_space_id = _extras.pop("home_space_id", None)
    home_space_name = _extras.pop("home_space_name", None)
    # ALC presence-join: Redis SSE heartbeat freshness threaded through from
    # list_agents so the agents-tab / MCP agents tool (#266) gets a live signal
    # instead of defaulting every card to "warming up". Contract pinned in
    # tests/unit/test_agent_roster_presence.py.
    # The KEY's presence in extras (not its value) signals whether the caller
    # looked Redis up: list paths always set "_presence" (None for an offline
    # agent), single-agent/create/update paths omit it. Only emit presence fields
    # when looked up — otherwise a connected agent on GET /agents/{id} would be
    # mislabeled offline (presence_fresh=false). (Codex review, #365.)
    _presence_looked_up = "_presence" in _extras
    _presence = _extras.pop("_presence", None)
    is_own = (str(agent.user_id) == current_user_id) if current_user_id else None

    base: dict[str, Any] = {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "origin": agent.origin,
        "status": agent.status,
        "space_id": str(agent.space_id),
        "space_name": space_name,
        "space_locked": getattr(agent, "space_locked", False),
        "created_at": _iso(agent.created_at),
        "user_id": str(agent.user_id) if agent.user_id else None,
        "owner_username": owner_username,
        "is_own": is_own,
        "can_control": is_own if is_own is not None else None,
        "can_update": is_own if is_own is not None else None,
    }

    if detail == DetailLevel.minimal:
        return base

    now = datetime.now(timezone.utc)
    global_state = getattr(agent, "global_state", "active") or "active"
    stored_lifecycle_state = compute_display_lifecycle(
        getattr(agent, "lifecycle_state", None),
        getattr(agent, "last_active_at", None),
        now,
        DEFAULT_THRESHOLDS,
    )
    lifecycle_state = stored_lifecycle_state
    presence_fields: dict[str, Any] = {}
    if _presence_looked_up:
        presence_fields = derive_presence_fields(_presence, now=now)
        if (
            presence_fields["presence_fresh"]
            and global_state not in {"archived", "disabled"}
            and stored_lifecycle_state != "archived"
        ):
            lifecycle_state = "active"

    control = _extras.get("control")

    # summary
    # Owner and space labels come from extras (batch-looked-up by the
    # service layer). Don't access lazy relationships here — that triggers
    # greenlet errors in async SQLAlchemy.
    base.update({
        "agent_type": agent.agent_type,
        "model": agent.model,
        "global_state": global_state,
        "lifecycle_state": lifecycle_state,
        "last_active_at": _iso(getattr(agent, "last_active_at", None)),
        "enabled_tools": build_enabled_tools_from_agent(agent),
        "can_manage_agents": agent.can_manage_agents,
        "avatar_url": agent.avatar_url,
        "bio": agent.bio,
        "specialization": agent.specialization,
        "template_type": agent.template_type,
        "updated_at": _iso(agent.updated_at),
        **_agent_protection_fields(agent),
    })

    # ALC presence-join: emit SSE heartbeat freshness via the shared helper so this
    # canonical serializer and the legacy /auth/agents management serializer stay
    # identical (their divergence caused the agents-tab "warming up" regression).
    # Only when presence was actually looked up (see _presence_looked_up) so
    # callers that don't fetch Redis don't mislabel a connected agent offline.
    if _presence_looked_up:
        base.update(presence_fields)
        is_online = bool(presence_fields["presence_fresh"])
        base.update({
            "is_online": is_online,
            "is_lifecycle_active": lifecycle_state == "active",
            "roster_group": compute_roster_group(
                global_state=global_state,
                lifecycle_state=lifecycle_state,
                presence_fresh=is_online,
            ),
            **_agent_messageability_fields(
                agent,
                is_online=is_online,
                global_state=global_state,
                control=control,
            ),
        })
    else:
        base.update({
            "is_lifecycle_active": lifecycle_state == "active",
            "roster_group": compute_roster_group(
                global_state=global_state,
                lifecycle_state=lifecycle_state,
                presence_fresh=False,
            ),
        })

    if _extras:
        base.update(_extras)

    if detail == DetailLevel.summary:
        return base

    # full
    base.update({
        "home_space_id": home_space_id,
        "home_space_name": home_space_name,
        "system_prompt": agent.system_prompt,
        "capabilities": agent.capabilities,
        "visibility_level": agent.visibility_level,
        "webhook_url": agent.webhook_url,
        "webhook_verified": agent.webhook_verified,
        "cloud_function_url": agent.cloud_function_url,
        "reputation_score": float(agent.reputation_score) if agent.reputation_score else None,
        "feedback_score": float(agent.feedback_score) if agent.feedback_score else None,
        "feedback_count": agent.feedback_count,
        "total_jobs_completed": agent.total_jobs_completed,
    })

    if _extras:
        base.update(_extras)

    return base
