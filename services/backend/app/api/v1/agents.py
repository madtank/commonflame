"""
Agent Management API endpoints
Handles agent registration, authentication, and configuration
"""

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import String, and_, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Setup logger
logger = logging.getLogger(__name__)

# Redis client for minting opaque MCP tokens
import json
import hashlib
import secrets as secrets_module
from ...core.redis_client import redis_client

from ...core.agent_constraints import is_cloud_agent, validate_agent_name
from ...core.agent_management_auth import extract_management_actor
from ...models.agent_space_access import AgentSpaceAccess
from ...core.beta_config import get_beta_config
from ...core.config import get_settings
from ...core.database import get_db_session
from ...core.rls import SecureSession, get_secure_session, SystemSession, get_system_session, system_session_context
from ...core.mcp_config_version import (
    CONFIG_VERSION_V1_HTTP_NATIVE,
    HTTP_NATIVE_CLIENT_ID,
    MCP_CONFIG_VERSION,
    VALID_CONFIG_VERSIONS,
    normalize_config_version,
)
from ...core.redis_client import redis_client
from ...models.agent import Agent
from ...models.message import Message
from ...models.space import Space
from ...models.task import Task
from ...models.user import User
from ...models.agent_management import AgentManagementProposal
from ...core.agent_reliability import AgentStatus, AgentStatusStore, AgentError, ErrorCategory
from ...services.agent_control_service import AgentControlService, AgentControlState
from ...services.redis_sse_broker import redis_sse_broker
from ...services.credits_service import CreditsService, award_first_agent
from ...core.jwt_verify import get_current_user_from_token

router = APIRouter(prefix="/auth", tags=["agents"])

# Import dynamic models configuration (single source of truth)
from ...core.models_config import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    get_available_models,
    get_default_model,
    get_models_for_user_role,
    validate_model_for_user_role,
    is_valid_model,
)

# Virtual system workspace ID for "Follow User" mode
# Agents set to this org are treated as following the user's current workspace
FOLLOW_UUID = uuid.UUID("11111111-1111-1111-1111-111111111111")

agent_control_service = AgentControlService(redis_client)
SPACE_MEMBER_AGENT_CONTROL_FIELDS = {"scope", "disabled", "reason", "disabled_until"}
SPACE_MEMBER_AGENT_UPDATE_FIELDS = {"enable_cloud_agent"}
agent_status_store = AgentStatusStore(redis_client)


def _json_payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _resolve_default_private_space_id(session: SecureSession) -> uuid.UUID:
    private_space_id = getattr(session.user, "space_id", None)
    if not private_space_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have a default private space configured",
        )
    return private_space_id


async def _write_agent_status_cache(agent_id: str, status: str) -> None:
    """Best-effort cache of agent status in Redis for fast circuit breakers."""
    try:
        status_key = f"agent:{agent_id}:status"
        disabled_key = f"agent:{agent_id}:disabled"
        paused_key = f"agent:{agent_id}:paused"

        await redis_client.set(status_key, status)

        if status in {"disabled", "paused", "inactive", "suspended"}:
            await redis_client.set(disabled_key, "1", ex=24 * 3600)
            if status == "paused":
                await redis_client.set(paused_key, "1", ex=24 * 3600)
            else:
                await redis_client.delete(paused_key)
        else:
            await redis_client.delete(disabled_key)
            await redis_client.delete(paused_key)
    except Exception as exc:
        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to write agent status cache for {agent_id}: {exc}")

# Import cached token minting from core module
from ...core.mcp_token_cache import get_or_mint_mcp_token

async def _build_agent_response(agent: Agent) -> "AgentResponse":
    from app.core.agent_lifecycle import DEFAULT_THRESHOLDS, compute_display_lifecycle
    agent_slug = (agent.name or "").strip().lower()
    control_state = await agent_control_service.get_control_state(
        agent_id=agent.id,
        space_id=agent.space_id,
        agent_slug=agent_slug,
    )
    # Build enabled_tools from agent (uses JSONB if available, falls back to legacy columns)
    enabled_tools = build_enabled_tools_from_agent(agent)
    # Build legacy fields from enabled_tools (DRY - uses toggle registry)
    legacy_fields = get_legacy_columns_from_enabled_tools(enabled_tools)
    return AgentResponse(
        id=str(agent.id),
        name=agent.name,
        description=agent.description,
        bio=agent.bio,
        specialization=agent.specialization,
        agent_type=agent.agent_type,
        capabilities=agent.capabilities or {},
        status=agent.status,
        system_prompt=agent.system_prompt,
        cloud_function_url=agent.cloud_function_url,
        enable_cloud_agent=bool(agent.cloud_function_url),
        is_automated=bool(agent.cloud_function_url),
        # External agent fields (Moltbot integration)
        origin=agent.origin or "cloud",
        webhook_url=agent.webhook_url,
        webhook_verified=agent.webhook_verified or False,
        sub_type=(agent.capabilities or {}).get("sub_type"),
        enabled_tools=enabled_tools,
        # Legacy fields (backwards compat - populated from toggle registry)
        web_browsing_enabled=agent.web_browsing_enabled,
        **legacy_fields,
        template_type=agent.template_type or "ax_agent",
        model=agent.model or DEFAULT_MODEL,
        model_tier=AVAILABLE_MODELS.get(agent.model or DEFAULT_MODEL, {}).get("tier_required", "free"),
        reputation_score=float(agent.reputation_score or 0.0),
        total_jobs_completed=agent.total_jobs_completed or 0,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        has_token=bool(agent.api_token_hash),
        space_locked=agent.space_locked or False,
        control=AgentControlStateResponse(**control_state.as_dict()),
        # ALC: computed live from last_active_at (persisted col is 'active' in shadow mode).
        lifecycle_state=compute_display_lifecycle(
            agent.lifecycle_state, agent.last_active_at, datetime.now(UTC), DEFAULT_THRESHOLDS
        ),
        last_active_at=agent.last_active_at,
    )

def _normalize_utc_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

async def _get_agent_for_user(
    db: AsyncSession,
    current_user: User,
    agent_identifier: str,
    *,
    allow_admin_override: bool = False
) -> Agent:
    """Resolve an agent by UUID or name ensuring it belongs to the user."""
    return await _get_agent_for_owner(
        db,
        owner_id=current_user.id,
        owner_role=getattr(current_user, "role", ""),
        agent_identifier=agent_identifier,
        allow_admin_override=allow_admin_override,
    )


async def _get_agent_for_owner(
    db: AsyncSession,
    owner_id: uuid.UUID,
    agent_identifier: str,
    *,
    owner_role: str | None = None,
    allow_admin_override: bool = False,
) -> Agent:
    """Resolve an agent by UUID or name ensuring it belongs to owner_id."""

    query = None
    try:
        agent_uuid = uuid.UUID(agent_identifier)
        query = select(Agent).where(
            Agent.id == agent_uuid,
            Agent.is_internal.is_(False),  # Prevent access to system agents
        )
    except ValueError:
        query = select(Agent).where(
            and_(
                func.lower(Agent.name) == agent_identifier.lower(),
                Agent.user_id == owner_id,
                Agent.is_internal.is_(False),  # Prevent access to system agents
            )
        )

    result = await db.execute(query)
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )

    if agent.user_id != owner_id:
        if allow_admin_override and (owner_role or "").lower() == "admin":
            return agent
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage controls for your own agents",
        )

    return agent

async def _resolve_agent_control_owner(
    session: SecureSession,
    request: Request,
) -> tuple[uuid.UUID, str | None, bool]:
    """Return the human owner for agent-control operations.

    Delegated aX calls in a private workspace arrive as an agent session plus
    X-On-Behalf-Of. That header is intentionally required so control actions are
    attributed to the human owner, not the space-agent system account.
    """
    if session.agent_id and request.headers.get("x-on-behalf-of"):
        actor = await extract_management_actor(session, request)
        if actor.mode != "concierge_delegated" or not actor.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agent control requires delegated personal-space authority",
            )
        return actor.user_id, actor.space_role, True

    return session.user.id, getattr(session.user, "role", ""), False


async def _resolve_agent_control_space_id(
    session: SecureSession,
    request: Request,
) -> str:
    """Resolve the selected UI space for space-agent control fallbacks."""

    query_params = getattr(request, "query_params", {}) or {}
    headers = getattr(request, "headers", {}) or {}
    requested_space_id = (
        query_params.get("space_id")
        or headers.get("x-space-id")
        or headers.get("X-Space-Id")
        or session.space_id
    )

    try:
        space_uuid = uuid.UUID(str(requested_space_id))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current space context is unavailable",
        )

    session_space_id = str(getattr(session, "space_id", "") or "")
    if session_space_id == str(space_uuid):
        return str(space_uuid)

    from ...models.space_membership import SpaceMembership

    membership_result = await session.db.execute(
        select(SpaceMembership).where(
            and_(
                SpaceMembership.user_id == session.user.id,
                SpaceMembership.space_id == space_uuid,
            )
        )
    )
    if not membership_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of the selected space",
        )

    return str(space_uuid)


async def _get_agent_in_current_space(
    db: AsyncSession,
    agent_identifier: str,
    current_space_id: str,
) -> Agent:
    """Resolve a non-internal agent constrained to the caller's current space."""

    try:
        agent_uuid = uuid.UUID(agent_identifier)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )

    try:
        space_uuid = uuid.UUID(str(current_space_id))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current space context is unavailable",
        )

    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_uuid,
            Agent.space_id == space_uuid,
            Agent.is_internal.is_(False),
        )
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )

    return agent


async def _get_registered_space_agent_for_member(
    db: AsyncSession,
    agent_identifier: str,
    current_space_id: str,
) -> Agent:
    """Resolve the selected space's registered aX agent for member-scoped actions."""

    agent = await _get_agent_in_current_space(db, agent_identifier, current_space_id)
    if getattr(agent, "origin", None) != "space_agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the selected space's aX agent can be managed by space members",
        )

    result = await db.execute(select(Space.space_agent_id).where(Space.id == agent.space_id))
    registered_space_agent_id = result.scalar_one_or_none()
    if not registered_space_agent_id or str(registered_space_agent_id) != str(agent.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the selected space's registered aX agent can be managed by space members",
        )

    return agent


def _is_space_member_ax_enable_update(agent_update: "AgentUpdate") -> bool:
    """Return true when a non-owner update is only enabling the selected space's aX."""

    fields = set(agent_update.model_fields_set)
    return fields == SPACE_MEMBER_AGENT_UPDATE_FIELDS and agent_update.enable_cloud_agent is True

# Capabilities validation helper
# Supports flexible formats for enterprise agent registry:
# - List of strings: ["code review", "testing", "🔧 debugging"]
# - List of dicts: [{"name": "code review", "version": "1.0"}]
# - Dict: {"code_review": {"enabled": true}, "testing": {"priority": "high"}}
def validate_capabilities_format(v):
    """Validate capabilities field accepts list[str], list[dict], or dict."""
    if v is None:
        return v
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        if len(v) == 0:
            return v
        # Check if all items are same type (strings or dicts)
        first_type = type(v[0])
        if first_type not in (str, dict):
            raise ValueError("capabilities list items must be strings or dicts")
        if not all(isinstance(item, (str, dict)) for item in v):
            raise ValueError("capabilities list items must be strings or dicts")
        return v
    raise ValueError("capabilities must be a dict or list")

# Pydantic models for API
class AgentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=3, max_length=50)
    description: str | None = Field(None, max_length=500)
    agent_type: str | None = Field(None, max_length=50, deprecated=True,
        description="DEPRECATED: Ignored. agent_type is derived from origin (AGENTS-001).")
    capabilities: dict | list | None = None  # JSON field - can be dict or list
    # Optional brandable avatar for system/marketplace agents. Stored and reused across clients.
    avatar_url: str | None = Field(None, max_length=512)
    pinned_space_id: str | None = Field(None, description="If provided, pin this agent to a specific space")
    follow_user: bool | None = Field(None, description="If true, agent follows user's workspace changes")
    system_prompt: str | None = Field(None, description="Custom instructions for the agent", alias="systemPrompt")
    enable_cloud_agent: bool | None = Field(
        False,
        description="Enable cloud-hosted AI for this agent (auto-responds to @mentions)",
        alias="enableCloudAgent",
    )
    model: str | None = Field(
        default=DEFAULT_MODEL,
        description=f"LLM model for cloud agent (default: {DEFAULT_MODEL}). Pro models require Plus subscription.",
        max_length=100,
    )
    web_browsing_enabled: bool | None = Field(
        False,
        description="[LEGACY] Enable web browsing",
        alias="webBrowsingEnabled",
    )
    web_fetch_enabled: bool | None = Field(
        False,
        description="Enable web page fetching",
        alias="webFetchEnabled",
    )
    brave_search_enabled: bool | None = Field(
        False,
        description="Enable Brave web search (plus tier)",
        alias="braveSearchEnabled",
    )
    ax_mcp_enabled: bool | None = Field(
        True,
        description="Enable aX Platform MCP tools (messages, tasks, context)",
        alias="axMcpEnabled",
    )
    image_gen_enabled: bool | None = Field(
        False,  # Opt-in: disabled by default
        description="[DEPRECATED] Use enabled_tools instead",
        alias="imageGenEnabled",
    )
    template_type: str | None = Field(
        "ax_agent",
        description="Agent template type (ax_agent, gemma_research, etc.)",
        alias="templateType",
        max_length=50,
    )
    # New unified tool toggles field - replaces individual _enabled fields
    enabled_tools: dict[str, bool] | None = Field(
        None,
        description="Tool toggles as a dict: {ax_mcp: true, web_fetch: false, brave_search: false, image_gen: false}",
        alias="enabledTools",
    )

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v):
        return validate_capabilities_format(v)

class AgentUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(None, min_length=3, max_length=50)
    description: str | None = Field(None, max_length=500)
    # Agent self-documentation fields (also settable via whoami MCP tool)
    bio: str | None = Field(None, max_length=5000, description="Agent biography/background")
    specialization: str | None = Field(None, max_length=1000, description="What the agent specializes in")
    agent_type: str | None = Field(None, max_length=50, deprecated=True,
        description="DEPRECATED: Ignored. agent_type is derived from origin (AGENTS-001).")
    capabilities: dict | list | None = None  # JSON field - can be dict or list
    space_locked: bool | None = Field(None, description="Lock agent to current space (prevents cross-space moves)")
    # Extended statuses to support pause/disable controls for cloud agents
    status: str | None = Field(None, pattern="^(active|inactive|suspended|paused|disabled)$")
    pinned_space_id: str | None = Field(None, description="Update pinned space (null to unpin)")
    space_id: str | None = Field(None, description="Set agent's current space (ignored if pinned)")
    follow_user: bool | None = Field(None, description="If true, agent follows user's workspace changes")
    # Allows marketplace builders to upload/change a branded icon.
    avatar_url: str | None = Field(None, max_length=512, description="Optional avatar image URL for the agent")
    system_prompt: str | None = Field(None, description="Custom instructions for the agent", alias="systemPrompt")
    enable_cloud_agent: bool | None = Field(
        None,
        description="Enable/disable cloud-hosted AI for this agent",
        alias="enableCloudAgent",
    )
    web_browsing_enabled: bool | None = Field(
        None,
        description="[LEGACY] Enable web browsing",
        alias="webBrowsingEnabled",
    )
    web_fetch_enabled: bool | None = Field(
        None,
        description="Enable web page fetching",
        alias="webFetchEnabled",
    )
    brave_search_enabled: bool | None = Field(
        None,
        description="Enable Brave web search (plus tier)",
        alias="braveSearchEnabled",
    )
    ax_mcp_enabled: bool | None = Field(
        None,
        description="Enable aX Platform MCP tools",
        alias="axMcpEnabled",
    )
    image_gen_enabled: bool | None = Field(
        None,
        description="[DEPRECATED] Use enabled_tools instead",
        alias="imageGenEnabled",
    )
    template_type: str | None = Field(
        None,
        description="Agent template type (ax_agent, gemma_research, etc.)",
        alias="templateType",
        max_length=50,
    )
    # New unified tool toggles field - replaces individual _enabled fields
    enabled_tools: dict[str, bool] | None = Field(
        None,
        description="Tool toggles as a dict: {ax_mcp: true, web_fetch: false, ...}",
        alias="enabledTools",
    )
    cloud_function_url: str | None = Field(
        None,
        description="Direct URL override for cloud agent runner (used for version switching)",
        alias="cloudFunctionUrl",
    )
    engine_version: Literal["v1", "v2"] | None = Field(
        None,
        description="Agent engine version: 'v1' (stable) or 'v2' (with image generation). Backend maps to appropriate runner URL.",
        alias="engineVersion",
    )
    model: str | None = Field(
        None,
        description=f"LLM model for cloud agent (e.g., {DEFAULT_MODEL}). Pro models require Plus subscription.",
        max_length=100,
    )
    # External agent fields (Moltbot/Clawdbot)
    webhook_url: str | None = Field(
        None,
        description="Webhook URL for external gateway agents (Clawdbot, Moltbot)",
        max_length=512,
        alias="webhookUrl",
    )

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v):
        return validate_capabilities_format(v)

class AgentLegacyCacheResponse(BaseModel):
    status: str | None = None
    disabled: bool = False
    paused: bool = False
    blocking_keys: list[str] = Field(default_factory=list)


class AgentControlStateResponse(BaseModel):
    is_disabled: bool = False
    disabled_reason: str | None = None
    disabled_by: list[str] = Field(default_factory=list)
    disabled_until: datetime | None = None
    no_reply: bool = False
    no_reply_reason: str | None = None
    no_reply_by: list[str] = Field(default_factory=list)
    no_reply_until: datetime | None = None
    routing_only: bool = False
    routing_only_reason: str | None = None
    routing_only_by: list[str] = Field(default_factory=list)
    routing_only_until: datetime | None = None
    user_hourly_limit: int | None = None
    user_daily_limit: int | None = None
    agent_hourly_limit: int | None = None
    agent_daily_limit: int | None = None
    legacy_cache: AgentLegacyCacheResponse = Field(default_factory=AgentLegacyCacheResponse)

class AgentControlFlushResponse(BaseModel):
    agent_id: str
    flushed_legacy_keys: list[str] = Field(default_factory=list)
    status_cache: str | None = None
    control: AgentControlStateResponse


class AgentControlUpdateRequest(BaseModel):
    scope: Literal["agent", "workspace", "global", "managed"] = "agent"
    target_slug: str | None = None
    disabled: bool | None = None
    disabled_until: datetime | None = None
    reason: str | None = Field(None, max_length=200)
    no_reply: bool | None = None
    no_reply_reason: str | None = Field(None, max_length=200)
    no_reply_until: datetime | None = None
    routing_only: bool | None = None
    routing_only_reason: str | None = Field(None, max_length=200)
    routing_only_until: datetime | None = None
    user_hourly_limit: int | None = Field(None, ge=0)
    user_daily_limit: int | None = Field(None, ge=0)
    agent_hourly_limit: int | None = Field(None, ge=0)
    agent_daily_limit: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def validate_control_semantics(self):
        now = datetime.now(UTC)

        if self.disabled is True and self.no_reply is True:
            raise ValueError("disabled and no_reply cannot both be true in the same update")
        if self.disabled is True and self.routing_only is True:
            raise ValueError("disabled and routing_only cannot both be true in the same update")

        if self.disabled_until is not None:
            if self.disabled is not True:
                raise ValueError("disabled_until requires disabled=true")
            if self.disabled_until <= now:
                raise ValueError("disabled_until must be in the future")

        if self.no_reply_until is not None:
            if self.no_reply is not True:
                raise ValueError("no_reply_until requires no_reply=true")
            if self.no_reply_until <= now:
                raise ValueError("no_reply_until must be in the future")

        if self.routing_only_until is not None:
            if self.routing_only is not True:
                raise ValueError("routing_only_until requires routing_only=true")
            if self.routing_only_until <= now:
                raise ValueError("routing_only_until must be in the future")

        if self.disabled is False:
            if self.disabled_until is not None:
                raise ValueError("disabled_until cannot be set when disabled=false")
            if self.reason:
                raise ValueError("reason cannot be set when disabled=false")

        if self.no_reply is False:
            if self.no_reply_until is not None:
                raise ValueError("no_reply_until cannot be set when no_reply=false")
            if self.no_reply_reason:
                raise ValueError("no_reply_reason cannot be set when no_reply=false")

        if self.routing_only is False:
            if self.routing_only_until is not None:
                raise ValueError("routing_only_until cannot be set when routing_only=false")
            if self.routing_only_reason:
                raise ValueError("routing_only_reason cannot be set when routing_only=false")

        return self

class AgentResponse(BaseModel):
    id: str
    name: str
    description: str | None
    # Agent self-documentation fields (set via whoami MCP tool or REST API)
    bio: str | None = None
    specialization: str | None = None
    agent_type: str
    capabilities: dict | list | None  # JSON field - can be dict or list
    status: str
    system_prompt: str | None = None
    cloud_function_url: str | None = None
    enable_cloud_agent: bool = False
    is_automated: bool = False
    # External agent fields (Moltbot integration)
    origin: str = "cloud"  # cloud | mcp | external_gateway
    webhook_url: str | None = None  # For external_gateway agents
    webhook_verified: bool = False  # Is webhook verified?
    webhook_secret: str | None = None  # Returned ONCE when URL changes (Stripe pattern - store securely!)
    sub_type: str | None = None  # External agent sub-type (moltbot, ollama, custom)
    # Tool toggles - new unified format (preferred)
    enabled_tools: dict[str, bool] = Field(
        default_factory=lambda: {"ax_mcp": True, "web_fetch": False, "brave_search": False, "image_gen": False},
        description="Tool toggles: {ax_mcp, web_fetch, brave_search, image_gen}",
    )
    # Legacy individual fields (deprecated, kept for backwards compatibility)
    web_browsing_enabled: bool = False  # [DEPRECATED] Use enabled_tools
    web_fetch_enabled: bool = False  # [DEPRECATED] Use enabled_tools
    brave_search_enabled: bool = False  # [DEPRECATED] Use enabled_tools
    ax_mcp_enabled: bool = True  # [DEPRECATED] Use enabled_tools
    image_gen_enabled: bool = False  # [DEPRECATED] Use enabled_tools
    template_type: str = "ax_agent"  # Agent template type (ax_agent, gemma_research, etc.)
    model: str = DEFAULT_MODEL  # LLM model for cloud agents
    model_tier: str = "free"  # Tier required for this model (free/plus) - for UI badges
    reputation_score: float
    total_jobs_completed: int
    created_at: datetime
    updated_at: datetime
    has_token: bool
    space_locked: bool = False
    control: AgentControlStateResponse | None = None
    # Agent Lifecycle (ALC): organic staleness state + last productive output.
    # Frontend dims idle, flags dormant, hides archived. Read-only (shadow mode).
    lifecycle_state: str = "active"  # active | idle | dormant | archived
    last_active_at: datetime | None = None

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, v):
        return validate_capabilities_format(v)

class AgentWithToken(AgentResponse):
    api_token: str | None = None  # Deprecated - tokens no longer generated

class AgentRegistrationResponse(BaseModel):
    agent_token: str | None = None  # Deprecated - tokens no longer generated
    mcp_config: dict
    config_version: str = Field(
        default=MCP_CONFIG_VERSION, description="Indicates which MCP transport config was generated."
    )


class AgentMoveRequest(BaseModel):
    destination_space_id: str
    reason: str | None = Field(None, max_length=500)
    idempotency_key: str | None = Field(None, max_length=255)


class AgentMoveResponse(BaseModel):
    proposal_id: str
    status: str
    approval_state: str
    agent_id: str
    source_space_id: str
    destination_space_id: str
    current_space_id: str
    default_space_id: str | None = None


class AgentConfig(BaseModel):
    agent_id: str
    agent_name: str
    api_token: str | None = None  # Deprecated - use OAuth device flow
    server_url: str
    mcp_config: dict
    config_version: str = Field(
        default=MCP_CONFIG_VERSION, description="Indicates which MCP transport config was generated."
    )

class AgentHealth(BaseModel):
    agent_id: str
    name: str
    status: str
    last_seen: datetime | None
    is_online: bool

class AgentObservabilityStatus(BaseModel):
    """Real-time agent operational status from Redis."""
    agent_id: str
    name: str
    operational_status: str  # active, processing, rate_limited, degraded, paused, error, offline
    since: datetime | None = None
    expires_at: datetime | None = None
    reason: str | None = None
    retry_after_seconds: int | None = None
    error_category: str | None = None
    error_message: str | None = None
    is_healthy: bool = True

class AgentStatusUpdate(BaseModel):
    """Request model for agents to report their status."""
    agent_id: str
    agent_name: str
    status: str = Field(..., pattern="^(processing|active|complete|idle|rate_limited|error|degraded|thinking|tool_call|tool_complete|streaming|started|completed|accepted)$")
    message_id: str | None = None
    space_id: str | None = None
    reason: str | None = None
    error_message: str | None = None
    retry_after_seconds: int | None = None
    tool_name: str | None = Field(None, description="Name of the tool being called (Read, Bash, Edit, messages, tasks, etc.)")

class AgentFilterItem(BaseModel):
    """Lightweight agent info for dropdown filters"""

    agent_name: str
    post_count: int
    last_activity: datetime | None
    cloud_function_url: str | None = None
    enable_cloud_agent: bool = False
    is_automated: bool = False

class AgentFilterResponse(BaseModel):
    """Paginated response for agent filter dropdown"""

    agents: list[AgentFilterItem]
    total: int
    limit: int
    offset: int
    has_more: bool

# NOTE: generate_jwt_token removed - agent tokens no longer generated (tech debt cleanup)

def _resolve_config_version_param(requested: str | None) -> str:
    """Validate and normalize the requested config version."""

    normalized = normalize_config_version(requested)
    if normalized:
        return normalized
    if requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported MCP config_version '{requested}'. " f"Valid options: {sorted(VALID_CONFIG_VERSIONS)}",
        )
    return MCP_CONFIG_VERSION

def _resolve_mcp_environment(settings):
    """Return environment-aware URLs for MCP + OAuth endpoints."""

    environment = getattr(settings, "environment", "development")
    mcp_base_url = settings.mcp_server_url.rstrip("/")
    api_base_url = os.getenv(
        "API_URL",
        "https://paxai.app" if environment == "production" else "http://localhost:8001",
    ).rstrip("/")
    allow_http = not mcp_base_url.startswith("https://")
    return environment, mcp_base_url, api_base_url, allow_http

def _build_agent_mcp_config(
    *,
    agent_name: str,
    config_version: str,
    environment: str,
    mcp_base_url: str,
    api_base_url: str,
    allow_http: bool
) -> dict:
    """Construct the MCP client configuration for the requested transport."""

    encoded_agent_name = quote(agent_name, safe="")
    agent_endpoint_url = f"{mcp_base_url}/mcp/agents/{encoded_agent_name}"

    if config_version == CONFIG_VERSION_V1_HTTP_NATIVE:
        server_label = "ax-platform"
        http_entry = {
            "url": agent_endpoint_url,
            "transport": {"type": "http"},
            "oauth": {
                "authorizationUrl": f"{api_base_url}/oauth/authorize",
                "tokenUrl": f"{api_base_url}/oauth/token",
                "clientConfigurationUrl": f"{api_base_url}/oauth/client-config",
                "clientId": HTTP_NATIVE_CLIENT_ID,
            },
            "scopes": ["mcp:read", "mcp:write"],
        }
        if allow_http:
            http_entry["dangerouslyAllowInsecureTransport"] = True
        return {"mcpServers": {server_label: http_entry}}

    # Default legacy mcp-remote configuration - clean and simple
    server_label = "ax-platform" if environment == "production" else "ax-local"
    return {
        "mcpServers": {
            server_label: {
                "command": "npx",
                "args": [
                    "mcp-remote@0.1.37",
                    agent_endpoint_url,
                ],
            }
        }
    }

class AgentNameCheckResponse(BaseModel):
    """Response for agent name availability check."""
    available: bool
    message: str | None = None

@router.get("/agents/check-name", response_model=AgentNameCheckResponse)
async def check_agent_name(
    name: str = Query(..., min_length=1, max_length=50, description="Agent name to check"),
    session: SecureSession = Depends(get_secure_session)
):
    """Check if an agent name is available.

    Performs case-insensitive uniqueness check against existing agents
    and validates against reserved names and naming conventions.

    Lightweight endpoint intended for real-time validation on input blur.
    """
    # Step 1: Validate name format and reserved names
    is_valid, error_message = validate_agent_name(name)
    if not is_valid:
        return AgentNameCheckResponse(available=False, message=error_message)

    # Step 2: Check uniqueness (must match registration endpoint logic)
    # For non-cloud agents: check global uniqueness FIRST
    if not is_cloud_agent(name):
        global_query = select(Agent.id).where(
            func.lower(Agent.name) == name.lower()
        ).limit(1)

        result = await session.db.execute(global_query)
        if result.scalar_one_or_none():
            return AgentNameCheckResponse(
                available=False,
                message="This agent name is already taken"
            )

    # For ALL agents (cloud and non-cloud): check per-user uniqueness
    # This matches registration endpoint lines 1082-1091
    user_query = select(Agent.id).where(
        and_(
            func.lower(Agent.name) == name.lower(),
            Agent.user_id == session.user.id,
        )
    ).limit(1)

    result = await session.db.execute(user_query)
    if result.scalar_one_or_none():
        return AgentNameCheckResponse(
            available=False,
            message="You already have an agent with this name"
        )

    return AgentNameCheckResponse(available=True, message=None)

class ModelInfo(BaseModel):
    """Model information returned by the models endpoint."""
    id: str
    name: str
    description: str
    tier_required: str
    is_default: bool
    sort_order: int
    available: bool

class ModelsResponse(BaseModel):
    """Response from the models endpoint."""
    models: list[ModelInfo]
    default_model: str
    user_role: str

@router.get("/agents/models", response_model=ModelsResponse)
async def list_available_models(
    current_user: User = Depends(get_current_user_from_token)
):
    """Get available LLM models for cloud agents.

    Returns all models with availability based on the user's role (user/plus/admin).
    Frontend should use this to populate model picker dropdowns.

    Models marked as unavailable (available=false) can be shown in a locked/disabled
    state to encourage upgrades.
    """
    # Get user's role for model access (subscription is per-user, not per-workspace)
    user_role = getattr(current_user, "role", "user") or "user"

    # Get models for this user's role (includes all models with availability flag)
    models = get_models_for_user_role(user_role)

    return ModelsResponse(
        models=models,
        default_model=get_default_model(),
        user_role=user_role,
    )

# =============================================================================
# Agent Templates Endpoint
# =============================================================================
# Returns available agent templates based on user's role.
# Frontend uses this to populate the template selector in agent creation.

from ...core.container_registry import get_available_templates, get_template
from ...core.agent_toggles import (
    TOGGLE_BY_LEGACY_FIELD,
    build_enabled_tools_from_agent,
    apply_enabled_tools_defaults,
    validate_enabled_tools_update,
    merge_legacy_toggles_into_enabled_tools,
    get_legacy_columns_from_enabled_tools,
    get_legacy_column_updates_from_enabled_tools,
    extract_legacy_toggle_values,
    process_toggle_updates,
)

class AgentTemplateResponse(BaseModel):
    """Response model for agent templates."""
    template_type: str
    display_name: str
    description: str
    icon: str | None = None
    badge: str | None = None
    capabilities: dict
    models: list[dict]
    default_model: str
    is_default: bool = False

    model_config = ConfigDict(from_attributes=True)

class AgentTemplatesListResponse(BaseModel):
    """Response model for list of agent templates."""
    templates: list[AgentTemplateResponse]
    default_template: str

@router.get("/agent-templates", response_model=AgentTemplatesListResponse)
async def get_agent_templates(
    current_user: User = Depends(get_current_user_from_token)
):
    """
    Get available agent templates for the current user.

    Returns templates filtered by user's role/access level.
    Frontend uses this to populate the template selector.
    """
    user_role = current_user.role or "user"
    templates = get_available_templates(user_role)

    # Find default template
    default_template = "ax_agent"
    for t in templates:
        if t.get("is_default"):
            default_template = t["template_type"]
            break

    return AgentTemplatesListResponse(
        templates=[AgentTemplateResponse(**t) for t in templates],
        default_template=default_template,
    )

@router.get("/agents")
async def list_agents(
    limit: int = Query(20, ge=1, le=500, description="Page size (max 500 for 'Load All')"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    owner: str | None = Query(None, description="Filter by owner: 'me' for own agents only"),
    sort: str | None = Query(None, description="Sort order: 'relevance' (activity + trust), 'recent', 'name'"),
    search: str | None = Query(None, description="Search agents by name (case-insensitive, searches all space agents)"),
    session: SecureSession = Depends(get_secure_session)
):
    """List agents visible in the current space with pagination.

    Returns paginated list with own agents first, then team agents sorted by last activity.
    Supports sort=relevance for activity-based ordering.

    When `search` is provided, searches all active agents in the space by name
    (case-insensitive prefix/substring match). Useful for PAT scope pickers.
    """
    try:
        space_id_value = session.space_id
        if not space_id_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Current space context is unavailable"
            )

        try:
            space_uuid = uuid.UUID(str(space_id_value))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid space identifier")

        # Build base query conditions for non-deprecated, non-internal agents
        base_conditions = [
            Agent.is_internal.is_(False),
            Agent.status != "deprecated",
        ]

        # Search filter: match name OR agent UUID (so users can paste an ID from whoami)
        search_filter = None
        if search and search.strip():
            search_term = search.strip().lower()
            search_filter = or_(
                func.lower(Agent.name).contains(search_term),
                cast(Agent.id, String).ilike(f"%{search_term}%"),
            )

        # Get user's personal agents from ALL spaces (excluding deprecated and internal agents)
        personal_q = (
            select(Agent)
            .options(selectinload(Agent.user), selectinload(Agent.space_access))
            .where(Agent.user_id == session.user.id)
            .where(Agent.is_internal.is_(False))
            .where(Agent.status != "deprecated")
            .order_by(Agent.created_at.desc())
        )
        if search_filter is not None:
            personal_q = personal_q.where(search_filter)
        personal_agents_result = await session.db.execute(personal_q)
        personal_agents = list(personal_agents_result.scalars().all())

        if owner == "me":
            # Only return user's own agents
            all_personal_agents = personal_agents
            team_agents = []
        else:
            # Get team agents from CURRENT space only (excluding user's own agents, deprecated, and internal)
            team_q = (
                select(Agent)
                .options(selectinload(Agent.user), selectinload(Agent.space_access))
                .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
                .where(AgentSpaceAccess.space_id == space_uuid)
                .where(Agent.user_id != session.user.id)
                .where(Agent.is_internal.is_(False))
                .where(Agent.status != "deprecated")
                .order_by(Agent.created_at.desc())
            )
            if search_filter is not None:
                team_q = team_q.where(search_filter)
            team_agents_result = await session.db.execute(team_q)
            team_agents = list(team_agents_result.scalars().all())
            all_personal_agents = personal_agents

        # Combine all agents for lightweight sorting data
        all_agents = all_personal_agents + team_agents
        all_agent_ids = [agent.id for agent in all_agents]

        # ── Phase 1: Lightweight sort data (ALL agents, fast queries) ──
        # Only fetch what we need for sorting: message_count, last_seen, tasks_completed
        message_counts: dict[uuid.UUID, int] = {}
        last_activity: dict[uuid.UUID, datetime | None] = {}
        tasks_completed: dict[uuid.UUID, int] = {}

        if all_agent_ids:
            # Message counts + last activity (single query, indexed)
            message_stats = await session.db.execute(
                select(Message.agent_id, func.count(Message.id), func.max(Message.created_at))
                .where(Message.agent_id.in_(all_agent_ids))
                .group_by(Message.agent_id)
            )
            for agent_id, count, last_seen in message_stats:
                message_counts[agent_id] = count or 0
                last_activity[agent_id] = last_seen

            # Tasks completed (single query, indexed)
            completed_stats = await session.db.execute(
                select(Task.assigned_agent_id, func.count(Task.id))
                .where(and_(Task.assigned_agent_id.in_(all_agent_ids), Task.work_status == "completed"))
                .group_by(Task.assigned_agent_id)
            )
            for agent_id, count in completed_stats:
                tasks_completed[agent_id] = count or 0

        # ── Phase 2: Sort and paginate on lightweight data ──
        # Build sortable tuples: (agent, sort_key)
        def _sort_key(agent):
            posts = message_counts.get(agent.id, 0)
            tasks = tasks_completed.get(agent.id, 0)
            ls = last_activity.get(agent.id) or agent.updated_at
            is_pinned = 1 if agent.pinned_to_space == space_uuid else 0
            has_activity = 1 if (posts > 0 or tasks > 0) else 0
            return agent, is_pinned, has_activity, posts, tasks, ls

        sort_data = [_sort_key(a) for a in all_agents]

        if sort == "relevance":
            sort_data.sort(key=lambda x: (-x[1], -x[2], -x[3], -x[4]))
        elif sort == "recent":
            sort_data.sort(key=lambda x: x[5] or datetime.min, reverse=True)
        elif sort == "name":
            sort_data.sort(key=lambda x: (x[0].name or "").lower())

        total_count = len(sort_data)
        page_data = sort_data[offset:offset + limit]
        page_agents = [sd[0] for sd in page_data]
        page_agent_ids = [a.id for a in page_agents]

        # ── Phase 3: Rich stats for PAGE ONLY (expensive queries) ──
        org_ids = list(set(a.space_id for a in page_agents if a.space_id))
        org_names: dict[uuid.UUID, str] = {}
        if org_ids:
            org_result = await session.db.execute(
                select(Space.id, Space.name).where(Space.id.in_(org_ids))
            )
            for oid, oname in org_result:
                org_names[oid] = oname

        tasks_assigned: dict[uuid.UUID, int] = {}
        reactions_by_agent: dict[uuid.UUID, dict[str, int]] = {}
        trust_scores_by_agent: dict[uuid.UUID, dict[str, Any]] = {}

        if page_agent_ids:
            # Tasks assigned (page only)
            assigned_stats = await session.db.execute(
                select(Task.assigned_agent_id, func.count(Task.id))
                .where(Task.assigned_agent_id.in_(page_agent_ids))
                .group_by(Task.assigned_agent_id)
            )
            for agent_id, count in assigned_stats:
                tasks_assigned[agent_id] = count or 0

            # Reactions (page only) — use subquery to avoid loading all message IDs
            reactions_subq = (
                select(Message.id, Message.agent_id)
                .where(Message.agent_id.in_(page_agent_ids))
                .subquery()
            )
            reactions_result = await session.db.execute(
                select(reactions_subq.c.agent_id, Message.content, func.count(Message.id))
                .join(Message, Message.parent_id == reactions_subq.c.id)
                .where(and_(Message.message_type == "reaction", func.length(Message.content) <= 10))
                .group_by(reactions_subq.c.agent_id, Message.content)
            )
            for agent_id, emoji, count in reactions_result:
                if agent_id not in reactions_by_agent:
                    reactions_by_agent[agent_id] = {}
                reactions_by_agent[agent_id][emoji] = reactions_by_agent[agent_id].get(emoji, 0) + count

            # Trust scores (page only)
            from ...models.message_intelligence import MessageIntelligence
            trust_stats = await session.db.execute(
                select(
                    Message.agent_id,
                    func.count(MessageIntelligence.message_id).label("messages_analyzed"),
                    func.avg(MessageIntelligence.quality_score).label("avg_quality_score"),
                    func.avg(MessageIntelligence.spam_score).label("avg_spam_score"),
                    func.avg(MessageIntelligence.toxicity_score).label("avg_toxicity_score"),
                )
                .join(MessageIntelligence, MessageIntelligence.message_id == Message.id)
                .where(Message.agent_id.in_(page_agent_ids))
                .group_by(Message.agent_id)
            )
            for row in trust_stats:
                agent_id = row.agent_id
                quality = float(row.avg_quality_score or 0)
                spam = float(row.avg_spam_score or 0)
                toxicity = float(row.avg_toxicity_score or 0)
                trust_score = quality * 0.5 + (1 - spam) * 0.25 + (1 - toxicity) * 0.25
                trust_scores_by_agent[agent_id] = {
                    "trust_score": round(trust_score, 3),
                    "avg_quality_score": round(quality, 3),
                    "avg_spam_score": round(spam, 3),
                    "avg_toxicity_score": round(toxicity, 3),
                    "messages_analyzed": int(row.messages_analyzed or 0),
                }

        # ── Phase 3b: Live presence from SSE Redis keys ──
        # presence_map keeps the legacy bool (drives the "connected/recent/offline"
        # string); presence_raw keeps the full dict so we can emit the canonical
        # heartbeat-freshness fields (last_heartbeat / presence_fresh /
        # presence_age_seconds) — same contract as GET /api/v1/agents.
        presence_map: dict[str, bool] = {}
        presence_raw: dict[str, dict] = {}
        if page_agent_ids:
            try:
                from app.core.agent_reliability import AgentPresence
                presence_store = AgentPresence(redis_client)
                presence_data = await presence_store.get_bulk_presence([str(aid) for aid in page_agent_ids])
                presence_map = {aid: bool(pdata) for aid, pdata in presence_data.items()}
                presence_raw = {aid: pdata for aid, pdata in presence_data.items() if isinstance(pdata, dict)}
            except Exception:
                pass  # Graceful degradation — presence is best-effort

        # ── Phase 4: Batch control states (single Redis pipeline) ──
        control_batch_input = [
            (a.id, a.space_id, (a.name or "").strip().lower())
            for a in page_agents
        ]
        control_states = await agent_control_service.get_control_states_batch(control_batch_input)

        # ── Phase 5: Build response for page agents only ──
        from app.core.agent_lifecycle import DEFAULT_THRESHOLDS, compute_display_lifecycle
        from app.schemas.agent import derive_presence_fields
        agent_list = []
        for agent in page_agents:
            is_own_agent = str(agent.user_id) == str(session.user.id)
            owner = agent.user

            control_state = control_states.get(agent.id, AgentControlState())

            permissions = {
                "can_edit": is_own_agent,
                "can_delete": is_own_agent,
                "can_regenerate_token": is_own_agent,
                "can_view_token": is_own_agent,
                "can_assign_tasks": is_own_agent,
                "can_message": True if is_own_agent else False,
            }

            posts_count = message_counts.get(agent.id, 0)
            completed_count = tasks_completed.get(agent.id, 0)
            assigned_count = tasks_assigned.get(agent.id, 0)

            completion_rate = round((completed_count / assigned_count) * 100, 1) if assigned_count > 0 else 0

            last_seen = last_activity.get(agent.id) or agent.updated_at
            last_seen_utc = _normalize_utc_timestamp(last_seen)

            agent_list.append(
                {
                    "id": str(agent.id),
                    "agent_name": agent.name,
                    "description": agent.description,
                    "bio": agent.bio,
                    "specialization": agent.specialization,
                    "agent_type": agent.agent_type,
                    "status": agent.status,
                    "visibility_level": agent.visibility_level,
                    "avatar_url": agent.avatar_url,
                    "cloud_function_url": agent.cloud_function_url,
                    "enable_cloud_agent": bool(agent.cloud_function_url) or agent.origin in ("cloud", "external_gateway", "agentcore"),
                    "is_automated": bool(agent.cloud_function_url) or agent.origin in ("cloud", "external_gateway", "agentcore"),
                    "origin": agent.origin or "cloud",
                    "webhook_url": agent.webhook_url,
                    "webhook_verified": agent.webhook_verified or False,
                    "sub_type": (agent.capabilities or {}).get("sub_type") if isinstance(agent.capabilities, dict) else None,
                    "enabled_tools": build_enabled_tools_from_agent(agent),
                    "web_browsing_enabled": agent.web_browsing_enabled,
                    "web_fetch_enabled": agent.web_fetch_enabled,
                    "brave_search_enabled": agent.brave_search_enabled,
                    "ax_mcp_enabled": agent.ax_mcp_enabled,
                    "image_gen_enabled": agent.image_gen_enabled,
                    "model": agent.model or DEFAULT_MODEL,
                    "model_tier": AVAILABLE_MODELS.get(agent.model or DEFAULT_MODEL, {}).get("tier_required", "free"),
                    "system_prompt": agent.system_prompt,
                    "is_own_agent": is_own_agent,
                    "owner_id": str(agent.user_id),
                    "owner_username": owner.username if owner else None,
                    "owner_email": owner.email if owner else None,
                    "owner_full_name": owner.full_name if owner else None,
                    "permissions": permissions,
                    "created_at": agent.created_at.isoformat() if agent.created_at else None,
                    "last_seen": last_seen.isoformat() if last_seen else None,
                    "presence": "connected" if presence_map.get(str(agent.id)) else ("recent" if last_seen_utc and (datetime.now(UTC) - last_seen_utc).total_seconds() < 3600 else "offline"),
                    # Canonical availability fields — parity with GET /api/v1/agents so the
                    # frontend agents-tab (which reads /auth/agents) shows real
                    # Online/Idle/Dormant + can sort heartbeating agents first.
                    "lifecycle_state": compute_display_lifecycle(
                        agent.lifecycle_state, agent.last_active_at, datetime.now(UTC), DEFAULT_THRESHOLDS
                    ),
                    "last_active_at": agent.last_active_at.isoformat() if agent.last_active_at else None,
                    **derive_presence_fields(presence_raw.get(str(agent.id))),
                    "posts_count": posts_count,
                    "tasks_completed": completed_count,
                    "tasks_assigned": assigned_count,
                    "completion_rate": completion_rate,
                    "capabilities": agent.capabilities or [],
                    "space_id": str(agent.space_id) if agent.space_id else None,
                    "current_space_name": org_names.get(agent.space_id) if agent.space_id else None,
                    "pinned_to_space": str(agent.pinned_to_space) if agent.pinned_to_space else None,
                    "pinned_space_id": str(agent.pinned_to_space) if agent.pinned_to_space else None,
                    "spaces": [
                        {"space_id": str(sa.space_id), "is_default": sa.is_default}
                        for sa in (agent.space_access or [])
                    ],
                    "control": control_state.as_dict(),
                    "reactions": reactions_by_agent.get(agent.id, {}),
                    **trust_scores_by_agent.get(agent.id, {
                        "trust_score": None,
                        "avg_quality_score": None,
                        "avg_spam_score": None,
                        "avg_toxicity_score": None,
                        "messages_analyzed": 0,
                    }),
                }
            )

        paginated_agents = agent_list

        # has_more: check if there are more agents to show
        has_more = offset + limit < total_count

        return {
            "agents": paginated_agents,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Failed to fetch agents: {e!s}\n{traceback.format_exc()}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch agents: {e!s}")

@router.get("/agents/filter", response_model=AgentFilterResponse)
async def filter_agents(
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    search: str | None = Query(None, description="Filter by agent name"),
    sort_by: str = Query("messages", regex="^(messages|activity|name)$", description="Sort field"),
    only_with_messages: bool = Query(False, description="Only show agents with messages"),
    session: SecureSession = Depends(get_secure_session)
):
    """
    Lightweight paginated agent list for dropdown filters.

    Optimized query that returns only essential data (agent_name, post_count, last_activity)
    without expensive control_state or permission checks.

    Query Parameters:
    - limit: Page size (1-100, default 20)
    - offset: Pagination offset (default 0)
    - search: Filter by agent name (case-insensitive LIKE)
    - sort_by: Sort by "messages", "activity", or "name" (default "messages")
    - only_with_messages: Only include agents with >0 messages (default false)

    Returns:
    - agents: List of AgentFilterItem objects
    - total: Total count of matching agents
    - limit: Requested page size
    - offset: Current offset
    - has_more: Whether more results exist
    """
    try:
        # Get current org context
        space_id_value = session.space_id
        if not space_id_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Current space context is unavailable"
            )

        try:
            space_uuid = uuid.UUID(str(space_id_value))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid space identifier")

        # Build the query with aggregation
        # Single query that gets agents with message counts and last activity
        query = (
            select(
                Agent.name.label("agent_name"),
                func.count(Message.id).label("post_count"),
                func.max(Message.created_at).label("last_activity"),
                Agent.cloud_function_url.label("cloud_function_url"),
            )
            .outerjoin(Message, Message.agent_id == Agent.id)
            .where(
                # User's personal agents OR team agents from current org
                (Agent.user_id == session.user.id) | (Agent.space_id == space_uuid)
            )
            .where(Agent.is_internal.is_(False))  # Exclude internal system agents
            .group_by(Agent.id, Agent.name, Agent.cloud_function_url)
        )

        # Apply search filter
        if search:
            query = query.where(Agent.name.ilike(f"%{search}%"))

        # Apply "only with messages" filter
        if only_with_messages:
            query = query.having(func.count(Message.id) > 0)

        # Get total count (before pagination)
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await session.db.execute(count_query)
        total = total_result.scalar() or 0

        # Apply sorting
        if sort_by == "messages":
            query = query.order_by(func.count(Message.id).desc())
        elif sort_by == "activity":
            query = query.order_by(func.max(Message.created_at).desc().nullslast())
        else:  # name
            query = query.order_by(Agent.name.asc())

        # Apply pagination
        query = query.limit(limit).offset(offset)

        # Execute query
        result = await session.db.execute(query)
        rows = result.all()

        # Transform to response model
        agents = [
            AgentFilterItem(
                agent_name=row.agent_name,
                post_count=row.post_count,
                last_activity=row.last_activity,
                cloud_function_url=row.cloud_function_url,
                enable_cloud_agent=bool(row.cloud_function_url),
                is_automated=bool(row.cloud_function_url),
            )
            for row in rows
        ]

        return AgentFilterResponse(
            agents=agents, total=total, limit=limit, offset=offset, has_more=(offset + len(agents)) < total
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error filtering agents: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to filter agents: {e!s}")

@router.get("/agents/cloud")
async def list_cloud_agents(
    include_global: bool = Query(True, description="Include global agents (ax-guide, etc.)"),
    session: SecureSession = Depends(get_secure_session)
):
    """
    List cloud agents available in the current space.

    Returns agents that:
    - Are cloud agents (have cloud_function_url set)
    - Are in the current space OR are global agents
    - Are active and ready to respond

    This endpoint is optimized for the "available agents" panel in the UI.
    """
    try:
        from ...core.system_agents import get_all_global_agents

        space_id_value = session.space_id
        if not space_id_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current space context is unavailable"
            )

        try:
            space_uuid = uuid.UUID(str(space_id_value))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid space identifier"
            )

        # Dispatchable agents: identified by origin (AGENTS-001)
        from app.core.agent_origins import DISPATCHABLE_ORIGINS
        cloud_agents_result = await session.db.execute(
            select(Agent)
            .options(selectinload(Agent.user))
            .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
            .where(
                and_(
                    or_(
                        AgentSpaceAccess.space_id == space_uuid,
                        Agent.visibility_level == "global",
                    ),
                    or_(
                        Agent.origin.in_(DISPATCHABLE_ORIGINS - {"space_agent"}),
                        and_(  # Fallback: agents with cloud_function_url but wrong origin (migration safety)
                            Agent.cloud_function_url.isnot(None),
                            Agent.cloud_function_url != "",
                        ),
                    ),
                    Agent.status == "active",
                    Agent.is_internal.is_(False),
                )
            )
            .order_by(Agent.name.asc())
        )
        cloud_agents = list(cloud_agents_result.scalars().all())

        # Build response
        agents_list = []
        for agent in cloud_agents:
            agent_slug = (agent.name or "").strip().lower()
            control_state = await agent_control_service.get_control_state(
                agent_id=agent.id,
                space_id=agent.space_id,
                agent_slug=agent_slug,
            )

            # Skip disabled agents
            if control_state.is_disabled:
                continue

            agents_list.append({
                "id": str(agent.id),
                "name": agent.name,
                "username": agent.name,  # Frontend expects username field
                "description": agent.description,
                "bio": agent.bio,
                "avatar_url": agent.avatar_url,
                "is_global": agent.visibility_level == "global",
                "enable_cloud_agent": True,
                # Tool toggles - use helper from toggle registry (DRY)
                "enabled_tools": build_enabled_tools_from_agent(agent),
                # Legacy fields (deprecated)
                "web_browsing_enabled": agent.web_browsing_enabled,
                "web_fetch_enabled": agent.web_fetch_enabled,
                "brave_search_enabled": agent.brave_search_enabled,
                "ax_mcp_enabled": agent.ax_mcp_enabled,
                "image_gen_enabled": agent.image_gen_enabled,
                "cloud_function_url": agent.cloud_function_url,
                "space_id": str(agent.space_id) if agent.space_id else None,
                "owner_username": agent.user.username if agent.user else None,
            })

        # Add global agents from config (if not already in DB)
        if include_global:
            global_agents = get_all_global_agents()
            existing_ids = {a["id"] for a in agents_list}

            for config in global_agents:
                if str(config["id"]) not in existing_ids:
                    agents_list.append({
                        "id": str(config["id"]),
                        "name": config["name"],
                        "username": config["name"],  # Frontend expects username field
                        "description": config["description"],
                        "bio": config["bio"],
                        "avatar_url": None,
                        "is_global": True,
                        "enable_cloud_agent": config["is_cloud_agent"],
                        # Tool toggles - enabled_tools dict
                        "enabled_tools": {
                            "ax_mcp": True,
                            "web_fetch": False,
                            "brave_search": False,
                            "image_gen": False,
                        },
                        # Legacy fields (deprecated)
                        "web_browsing_enabled": False,
                        "web_fetch_enabled": False,
                        "brave_search_enabled": False,
                        "ax_mcp_enabled": True,
                        "image_gen_enabled": False,
                        "cloud_function_url": None,  # Global agents don't have cloud URLs
                        "space_id": None,
                        "owner_username": "system",
                    })

        return {
            "agents": agents_list,
            "count": len(agents_list),
            "space_id": str(space_uuid),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing cloud agents: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list cloud agents: {e!s}"
        )

@router.post("/agents/register", response_model=AgentRegistrationResponse)
async def register_agent(
    agent_data: AgentCreate,
    config_version: str | None = Query(
        None,
        description="Choose 'v0-legacy' (default) or 'v1-http-native' MCP config output.",
    ),
    session: SecureSession = Depends(get_secure_session),

    user_agent: str | None = Header(None),
    request: Request = None
):
    """Register a new agent (The operator's beta limits: 5 per user, unlimited admin)"""
    try:
        # Get user's current space
        current_space_id = session.space_id

        if not current_space_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="User must be in an organization to register agents"
            )

        # The operator's Beta Configuration - Agent Limits
        beta_config = get_beta_config()
        can_register, message = await beta_config.can_register_agent(session.db, session.user)

        if not can_register:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "AGENT_LIMIT_EXCEEDED",
                    "title": "Agent Limit Reached",
                    "message": message,
                    "upgrade_options": {
                        "contact": "support@ax-platform.com",
                        "message": "Want more agents? Contact us to upgrade your account.",
                    },
                },
            )

        # Validate agent name format (pattern, length, reserved names)
        is_valid, error_message = validate_agent_name(agent_data.name)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_message)

        # Check if agent name already exists GLOBALLY (like email addresses - first come, first served)
        # Exception: Cloud agents (e.g., chirpy) can be shared across users
        if not is_cloud_agent(agent_data.name):
            result = await session.db.execute(
                select(Agent).where(func.lower(Agent.name) == agent_data.name.lower())  # Global, case-insensitive
            )
            existing_agent = result.scalar_one_or_none()
            if existing_agent:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Agent name already taken by another user. Please choose a different name.",
                )

        # For all agents (including cloud agents), check if current user already has this name
        result = await session.db.execute(
            select(Agent).where(
                and_(func.lower(Agent.name) == agent_data.name.lower(), Agent.user_id == session.user.id)
            )
        )
        existing_user_agent = result.scalar_one_or_none()
        if existing_user_agent:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="You already have an agent with this name"
            )

        # NOTE: Agent token generation removed - no longer needed (tech debt cleanup)

        # Product default: new agents start in the user's default private space,
        # then can be moved later via HITL-approved flows.
        owner_private_space_id = _resolve_default_private_space_id(session)
        initial_space_id = owner_private_space_id
        pinned_space = owner_private_space_id

        # Handle mobility modes BEFORE creating the agent
        if agent_data.follow_user:
            # Follow User mode - use a special UUID to indicate this
            # We use 11111111-1111-1111-1111-111111111111 to mean "follow user"
            pinned_space = FOLLOW_UUID
            # IMPORTANT: Must set space_id to match due to database constraint
            initial_space_id = FOLLOW_UUID
            logger.info(f"Agent {agent_data.name} set to follow user mode")
        elif agent_data.pinned_space_id:
            # Pin to specific org
            try:
                pinned_uuid = uuid.UUID(agent_data.pinned_space_id)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid pinned_space_id")

            # Special handling for Follow mode UUID
            if pinned_uuid == FOLLOW_UUID:
                # Follow User mode via pinned_space_id - no membership check needed
                # This is a virtual/system org that indicates "follow the user"
                pinned_space = pinned_uuid
                initial_space_id = pinned_uuid
                logger.info(f"Agent {agent_data.name} set to follow user mode via pinned_space_id")
            else:
                # Regular pinning to a real organization - verify membership
                membership = await session.db.execute(select(Space).where(Space.id == pinned_uuid))
                org_exists = membership.scalar_one_or_none()
                if not org_exists:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pinned organization not found")
                # Ensure user is a member
                from ...models.space_membership import SpaceMembership

                mem = await session.db.execute(
                    select(SpaceMembership).where(
                        and_(
                            SpaceMembership.user_id == session.user.id,
                            SpaceMembership.space_id == pinned_uuid,
                        )
                    )
                )
                if not mem.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of the pinned organization"
                    )
                pinned_space = pinned_uuid
                # IMPORTANT: When pinned, space_id must equal pinned_to_space due to database constraint
                initial_space_id = pinned_uuid

        # Auto-populate cloud_function_url if cloud agent is enabled
        cloud_function_url = None
        if agent_data.enable_cloud_agent:
            settings = get_settings()
            # Check if cloud agent creation is enabled (global kill switch)
            redis = redis_client
            dynamic_setting_key = "system:settings:cloud_agent_creation_enabled"
            raw_setting = await redis.get(dynamic_setting_key)

            if raw_setting is not None:
                value_str = raw_setting.decode() if isinstance(raw_setting, bytes) else raw_setting
                creation_enabled = value_str.lower() == "true"
            else:
                creation_enabled = settings.cloud_agent_creation_enabled

            if not creation_enabled:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cloud agent creation is currently disabled. Contact support if you need access.",
                )
            cloud_function_url = settings.agent_runner_stable_url  # New agents use stable by default

            # Cloud agents are ALWAYS pinned - they don't have MCP tools to switch spaces
            # Override any follow_user or free roam settings
            if not pinned_space:
                # If no pinned_space_id was provided, pin to user's current org
                pinned_space = owner_private_space_id
                initial_space_id = owner_private_space_id
                logger.info(f"Cloud agent {agent_data.name} auto-pinned to owner private space {owner_private_space_id}")
            # Note: If pinned_space was already set via agent_data.pinned_space_id, we keep that
            # If it was set to FOLLOW_UUID, we need to override it
            elif pinned_space == FOLLOW_UUID:
                # Cloud agents cannot follow user - pin to current org instead
                pinned_space = owner_private_space_id
                initial_space_id = owner_private_space_id
                logger.info(f"Cloud agent {agent_data.name} cannot follow user, pinned to owner private space {owner_private_space_id}")

        # Validate and set model for cloud agents
        agent_model = DEFAULT_MODEL
        if agent_data.model:
            # Validate model based on user's role (subscription is per-user, not per-workspace)
            user_role = getattr(session.user, "role", "user") or "user"
            is_valid, error_msg = validate_model_for_user_role(agent_data.model, user_role)
            if not is_valid:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
            agent_model = agent_data.model

        # Determine template type and validate
        template_type = agent_data.template_type or "ax_agent"
        template = get_template(template_type)
        if not template:
            template_type = "ax_agent"  # Fallback to default
            template = get_template(template_type)

        # Build enabled_tools using central registry (DRY pattern)
        # Supports both new enabled_tools dict and legacy individual fields
        template_caps = template.get("capabilities", {})

        # Merge legacy toggle fields into enabled_tools if provided
        # Uses registry to automatically extract all legacy fields from agent_data
        legacy_data = extract_legacy_toggle_values(agent_data)
        merged_tools = merge_legacy_toggles_into_enabled_tools(agent_data.enabled_tools, legacy_data)
        enabled_tools = apply_enabled_tools_defaults(merged_tools, template_caps)

        # Get legacy column values from enabled_tools (auto-synced via registry)
        legacy_columns = get_legacy_columns_from_enabled_tools(enabled_tools)

        # Create new agent with proper space_id and enabled_tools JSONB
        # Determine origin: cloud agents get 'cloud', MCP agents get 'mcp'
        agent_origin = "cloud" if cloud_function_url else "mcp"

        new_agent = Agent(
            id=uuid.uuid4(),
            user_id=session.user.id,
            owner_type="user",
            owner_user_id=session.user.id,
            owner_space_id=owner_private_space_id,
            home_space_id=owner_private_space_id,
            created_by_user_id=session.user.id,
            space_id=initial_space_id,  # Use pinned space if pinned, otherwise owner private space
            name=agent_data.name,
            description=agent_data.description,
            avatar_url=agent_data.avatar_url,
            agent_type=agent_origin,  # Must match origin (AGENTS-001)
            capabilities=agent_data.capabilities or {},
            status="active",
            visibility_level="org_visible",  # Visible to entire space
            api_token_hash=None,  # Token generation removed - no longer needed
            reputation_score=0.0,
            total_jobs_completed=0,
            pinned_to_space=pinned_space,  # Set the pinned org if any
            system_prompt=agent_data.system_prompt or None,
            cloud_function_url=cloud_function_url,
            origin=agent_origin,  # MCP agents can switch workspaces, cloud agents are pinned
            model=agent_model,
            template_type=template_type,
            # Primary: JSONB column for all tool toggles
            enabled_tools=enabled_tools,
            # Legacy columns (kept for backwards compat, auto-synced from enabled_tools via registry)
            web_browsing_enabled=agent_data.web_browsing_enabled or False,
            **legacy_columns,
        )
        # else: Stationary mode (default) - pinned_to_space remains None

        session.db.add(new_agent)
        await session.db.flush()  # Get new_agent.id before dual-write

        # Dual-write: create default agent_space_access row
        from app.core.agent_space import grant_space_access
        await grant_space_access(session.db, new_agent.id, initial_space_id, is_default=True)

        await session.db.commit()
        await session.db.refresh(new_agent)

        # The operator's Gamification: Award credits for agent registration
        beta_config = get_beta_config()
        if beta_config.ENABLE_CREDITS:
            try:
                # Check if this is user's first agent for special achievement
                agent_count_check = await session.db.execute(
                    select(func.count(Agent.id)).where(Agent.user_id == session.user.id)
                )
                total_agents = agent_count_check.scalar()

                if total_agents == 1:  # First agent
                    await award_first_agent(session.db, str(session.user.id), str(new_agent.id))
                else:  # Additional agent
                    await CreditsService.award_credits(
                        session.db,
                        str(session.user.id),
                        10,
                        "agent_registration",
                        f"Registered agent: {new_agent.name}",
                        str(new_agent.id),
                    )
            except Exception as e:
                print(f"⚠️  Credits award failed (non-critical): {e}")
                # Don't fail agent registration if credits fail

        # Generate MCP configuration matching working format with environment-aware URLs
        settings = get_settings()

        # Preserve original case for agent name in headers AND paths
        # Using consistent case prevents auth mismatches between header and config path
        agent_name_original = new_agent.name
        # Replace spaces and hyphens but preserve case for consistency
        resolved_config_version = _resolve_config_version_param(config_version)
        environment, mcp_base_url, api_base_url, allow_http = _resolve_mcp_environment(settings)
        mcp_config = _build_agent_mcp_config(
            agent_name=agent_name_original,
            config_version=resolved_config_version,
            environment=environment,
            mcp_base_url=mcp_base_url,
            api_base_url=api_base_url,
            allow_http=allow_http,
        )

        logger.info(
            "✅ Agent '%s' configured (config_version=%s)",
            agent_name_original,
            resolved_config_version,
        )
        logger.info("Full MCP config: %s", mcp_config)

        return AgentRegistrationResponse(
            agent_token=None,  # Token generation removed - no longer needed
            mcp_config=mcp_config,
            config_version=resolved_config_version,
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to register agent: {e!s}"
        )

@router.post("/agents/{agent_id}/regenerate-token", response_model=dict, deprecated=True)
async def regenerate_agent_token(
    agent_id: str, session: SecureSession = Depends(get_secure_session)
):
    """
    DEPRECATED: Agent tokens are no longer used.

    This endpoint is kept for backwards compatibility but returns a deprecation notice.
    Agent authentication now uses OAuth device flow with opaque tokens.
    """
    return {
        "message": "Agent token regeneration is deprecated - tokens are no longer used",
        "agent_id": agent_id,
        "deprecated": True
    }

@router.get("/agents/{agent_id}/config", response_model=AgentConfig)
async def get_agent_config(
    agent_id: str,
    config_version: str | None = Query(
        None,
        description="Choose 'v0-legacy' (default) or 'v1-http-native' MCP config output.",
    ),
    session: SecureSession = Depends(get_secure_session)
):
    """
    Get universal MCP configuration for an agent

    Returns agent-specific OAuth endpoint configuration that works on all platforms
    without requiring OS detection or file-based credential storage.
    """
    try:
        # Validate UUID format
        try:
            agent_uuid = uuid.UUID(agent_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid agent ID format. Expected UUID."
            )
        # Verify agent exists and belongs to current user
        result = await session.db.execute(select(Agent).where(and_(Agent.id == agent_uuid, Agent.user_id == session.user.id)))
        agent = result.scalar_one_or_none()

        if not agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

        # Check ownership - only owner can update
        if str(agent.user_id) != str(session.user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="You can only view configuration for your own agents"
            )

        # NOTE: Token generation removed - agents use OAuth device flow now

        settings = get_settings()
        agent_name_original = agent.name
        resolved_config_version = _resolve_config_version_param(config_version)
        environment, mcp_base_url, api_base_url, allow_http = _resolve_mcp_environment(settings)
        mcp_config = _build_agent_mcp_config(
            agent_name=agent_name_original,
            config_version=resolved_config_version,
            environment=environment,
            mcp_base_url=mcp_base_url,
            api_base_url=api_base_url,
            allow_http=allow_http,
        )

        logger.info(
            "🔁 Regenerated MCP config for agent '%s' (config_version=%s)",
            agent.name,
            resolved_config_version,
        )

        return AgentConfig(
            agent_id=str(agent.id),
            agent_name=agent.name,
            api_token=None,  # Token generation removed - use OAuth device flow
            server_url=mcp_base_url,
            mcp_config=mcp_config,
            config_version=resolved_config_version,
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get agent config: {e!s}"
        )

@router.get("/agents/health", response_model=list[AgentHealth])
async def get_agents_health(
    session: SecureSession = Depends(get_secure_session)
):
    """Get health status for all agents in the organization"""
    try:
        result = await session.db.execute(
            select(Agent)
            .where(Agent.space_id == uuid.UUID(session.space_id))
            .where(Agent.is_internal.is_(False))
            .order_by(Agent.name)
        )
        agents = result.scalars().all()

        # For now, return basic health info
        # TODO: Implement actual agent ping/health checking
        health_list = []
        for agent in agents:
            health_list.append(
                AgentHealth(
                    agent_id=str(agent.id),
                    name=agent.name,
                    status=agent.status,
                    last_seen=agent.updated_at,  # Use updated_at as proxy for last_seen
                    is_online=agent.status == "active",  # Simple heuristic
                )
            )

        return health_list

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get agents health: {e!s}"
        )

@router.get("/agents/observability", response_model=list[AgentObservabilityStatus])
async def get_agents_observability(
    session: SecureSession = Depends(get_secure_session)
):
    """
    Get real-time operational status for agents in user's organization.

    Returns status from Redis for agents that have non-active states
    (processing, rate_limited, degraded, error, etc.).
    Agents without Redis status are assumed to be active/healthy.
    """
    try:
        # Get agents scoped to user's org (prevents cross-org leaks)
        user_space_id = session.space_id
        result = await session.db.execute(
            select(Agent)
            .where(
                Agent.is_internal.is_(False),
                or_(
                    Agent.space_id == user_space_id,
                    Agent.user_id == session.user.id,
                )
            )
            .order_by(Agent.name)
        )
        agents = result.scalars().all()

        status_list = []
        for agent in agents:
            agent_id = str(agent.id)

            # Check Redis for real-time status
            redis_status = await agent_status_store.get_status(agent_id)

            if redis_status:
                # Agent has active status in Redis
                error = redis_status.error
                status_list.append(
                    AgentObservabilityStatus(
                        agent_id=agent_id,
                        name=agent.name,
                        operational_status=redis_status.status.value,
                        since=redis_status.since,
                        expires_at=redis_status.expires_at,
                        reason=redis_status.reason,
                        retry_after_seconds=error.retry_after_seconds if error else None,
                        error_category=error.category.value if error else None,
                        error_message=error.user_message if error else None,
                        is_healthy=redis_status.status in (AgentStatus.ACTIVE, AgentStatus.PROCESSING),
                    )
                )
            else:
                # No Redis status = assumed active and healthy
                status_list.append(
                    AgentObservabilityStatus(
                        agent_id=agent_id,
                        name=agent.name,
                        operational_status="active",
                        is_healthy=True,
                    )
                )

        return status_list

    except Exception as e:
        logger.exception(f"Failed to get agents observability: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get agents observability: {e!s}",
        )

@router.get("/agents/{agent_id}/observability", response_model=AgentObservabilityStatus)
async def get_agent_observability(
    agent_id: str,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Get real-time operational status for a specific agent.

    Requires authentication and verifies user has access to the agent
    (owns it or it's in their organization).
    """
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent ID format")

    try:
        result = await session.db.execute(select(Agent).where(Agent.id == agent_uuid))
        agent = result.scalar_one_or_none()

        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        # Verify user has access to this agent
        user_space_id = session.space_id
        if agent.user_id != session.user.id and agent.space_id != user_space_id:
            raise HTTPException(status_code=403, detail="Access denied to this agent")

        # Check Redis for real-time status
        redis_status = await agent_status_store.get_status(agent_id)

        if redis_status:
            error = redis_status.error
            return AgentObservabilityStatus(
                agent_id=agent_id,
                name=agent.name,
                operational_status=redis_status.status.value,
                since=redis_status.since,
                expires_at=redis_status.expires_at,
                reason=redis_status.reason,
                retry_after_seconds=error.retry_after_seconds if error else None,
                error_category=error.category.value if error else None,
                error_message=error.user_message if error else None,
                is_healthy=redis_status.status in (AgentStatus.ACTIVE, AgentStatus.PROCESSING),
            )
        else:
            return AgentObservabilityStatus(
                agent_id=agent_id,
                name=agent.name,
                operational_status="active",
                is_healthy=True,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get agent observability for {agent_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get agent observability: {e!s}",
        )

# =============================================================================
# INTERNAL AGENT STATUS API (for agent runners to report status)
# =============================================================================

@router.post("/internal/agent-status")
async def update_agent_status(
    status_update: AgentStatusUpdate,
    x_api_key: str | None = Header(None, alias="X-API-Key")
):
    """
    Internal endpoint for agent runners to report their status.

    Requires X-API-Key header for authentication.

    This is called by cloud agents when they:
    - Start processing a message (status=processing)
    - Complete processing (status=complete/active)
    - Encounter an error (status=error)
    - Hit rate limits (status=rate_limited)

    The status is stored in Redis and broadcast via SSE for real-time
    frontend visibility (typing indicators, error states, etc.)
    """
    # Validate API key with timing-safe comparison to prevent timing attacks
    settings = get_settings()
    if not x_api_key or not secrets_module.compare_digest(x_api_key, settings.agent_runner_api_key):
        logger.warning(f"❌ Unauthorized internal API access attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

    try:
        # Map incoming status to AgentStatus enum
        # New activity states (thinking, tool_call, etc.) are sub-states of PROCESSING
        # Lifecycle events (started, completed) mark chain boundaries
        status_mapping = {
            "processing": AgentStatus.PROCESSING,
            "started": AgentStatus.PROCESSING,       # Chain started (lifecycle)
            "thinking": AgentStatus.PROCESSING,      # LLM generating
            "accepted": AgentStatus.PROCESSING,      # Agent committed to work
            "tool_call": AgentStatus.PROCESSING,     # Tool invocation
            "tool_complete": AgentStatus.PROCESSING, # Tool finished
            "streaming": AgentStatus.PROCESSING,     # Response streaming
            "active": AgentStatus.ACTIVE,
            "complete": AgentStatus.ACTIVE,          # complete maps to active
            "completed": AgentStatus.ACTIVE,         # Chain completed (lifecycle)
            "idle": AgentStatus.ACTIVE,              # idle maps to active
            "rate_limited": AgentStatus.RATE_LIMITED,
            "error": AgentStatus.ERROR,
            "degraded": AgentStatus.DEGRADED,
        }

        agent_status = status_mapping.get(status_update.status, AgentStatus.ACTIVE)

        # Build error if applicable
        error = None
        if status_update.status == "error" and status_update.error_message:
            error = AgentError(
                category=ErrorCategory.INTERNAL_ERROR,
                message=status_update.error_message,
                user_message=status_update.error_message,
                agent_id=status_update.agent_id,
            )
        elif status_update.status == "rate_limited":
            error = AgentError(
                category=ErrorCategory.RATE_LIMITED,
                message="Rate limited",
                user_message=status_update.reason or "Agent is cooling down",
                agent_id=status_update.agent_id,
                recoverable=True,
            )

        # Store in Redis
        if agent_status == AgentStatus.ACTIVE:
            # Clear any existing non-active status
            await agent_status_store.clear_status(
                agent_id=status_update.agent_id,
                space_id=status_update.space_id,
            )
        else:
            await agent_status_store.set_status(
                agent_id=status_update.agent_id,
                status=agent_status,
                space_id=status_update.space_id,
                reason=status_update.reason or status_update.error_message,
                error=error,
                ttl_override=status_update.retry_after_seconds,
            )

        # Broadcast via SSE for real-time frontend updates
        if status_update.space_id:
            await redis_sse_broker.publish(
                space_id=status_update.space_id,
                event="agent_activity",
                data={
                    "agent_id": status_update.agent_id,
                    "agent_name": status_update.agent_name,
                    "activity": status_update.status,
                    "status": status_update.status,  # Alias for frontend compatibility
                    "message_id": status_update.message_id,
                    "parent_message_id": status_update.message_id,  # Alias for frontend
                    "tool_name": status_update.tool_name,  # Tool being called
                    "reason": status_update.reason or status_update.error_message,
                    # All processing sub-states (thinking, tool_call, etc.) map to PROCESSING and are healthy
                    "is_healthy": agent_status in (AgentStatus.ACTIVE, AgentStatus.PROCESSING),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

        # Log with structured format for observability dashboard
        tool_info = f" tool={status_update.tool_name}" if status_update.tool_name else ""
        msg_info = f" message_id={status_update.message_id}" if status_update.message_id else ""
        org_info = f" space_id={status_update.space_id}" if status_update.space_id else ""
        log_msg = (
            f"AGENT_STATUS agent_name={status_update.agent_name} "
            f"agent_id={status_update.agent_id} status={status_update.status}"
            f"{tool_info}{msg_info}{org_info}"
        )
        print(log_msg)  # Print to ensure it shows in Docker logs
        logger.info(log_msg)

        # V3 RESPONSE CAPTURE: When agent completes, query DB for reply and log it
        if status_update.status == "completed" and status_update.message_id and status_update.space_id:
            try:
                from app.core.database import AsyncSessionLocal
                from app.models.message import Message
                from sqlalchemy import select, text
                from uuid import UUID
                import asyncio

                # Small delay to allow DB write to complete (timing issue)
                await asyncio.sleep(0.5)

                async with AsyncSessionLocal() as db:
                    # Set RLS context
                    await db.execute(
                        text("SELECT set_config('app.current_space_id', :sid, true)"),
                        {"sid": str(status_update.space_id)},
                    )

                    # Find the agent's reply (message where parent_id = trigger message_id and agent_id matches)
                    agent_uuid = UUID(status_update.agent_id)
                    trigger_uuid = UUID(status_update.message_id)

                    query_log = (
                        f"V3_RESPONSE_QUERY agent={status_update.agent_name} "
                        f"trigger={status_update.message_id} agent_id={status_update.agent_id}"
                    )
                    print(query_log)
                    logger.info(query_log)

                    result = await db.execute(
                        select(Message)
                        .where(Message.parent_id == trigger_uuid)
                        .where(Message.agent_id == agent_uuid)
                        .order_by(Message.created_at.desc())
                        .limit(1)
                    )
                    reply_msg = result.scalar_one_or_none()

                    if reply_msg and reply_msg.content:
                        content_preview = reply_msg.content[:300].replace("\n", " ")
                        response_log = (
                            f"DISPATCH_RESPONSE_SAVED agent_name={status_update.agent_name} "
                            f"agent_id={status_update.agent_id} "
                            f"trigger_message_id={status_update.message_id} "
                            f"reply_message_id={reply_msg.id} "
                            f"content_len={len(reply_msg.content)} "
                            f"payload_version=3 "
                            f'response_preview="{content_preview}"'
                        )
                        print(response_log)
                        logger.info(response_log)
                    else:
                        not_found_log = (
                            f"V3_RESPONSE_NOT_FOUND agent={status_update.agent_name} "
                            f"trigger={status_update.message_id} agent_id={status_update.agent_id}"
                        )
                        print(not_found_log)
                        logger.warning(not_found_log)
            except Exception as e:
                # Don't fail the status update, just log the error
                error_log = f"V3_RESPONSE_ERROR agent={status_update.agent_name} error={e}"
                print(error_log)
                logger.warning(error_log)
                logger.warning(f"Failed to capture V3 response: {e}")

        return {"status": "ok", "agent_status": agent_status.value}

    except Exception as e:
        logger.exception(f"Failed to update agent status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update agent status: {e!s}",
        )

@router.get("/internal/cloud-agent-context")
async def get_cloud_agent_context(
    agent_name: str = Query(..., description="Agent name/handle"),
    space_id: str = Query(..., description="Space/space ID"),
    message_id: str | None = Query(None, description="Optional message ID for context"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    session: SystemSession = Depends(get_system_session)
):
    """
    Internal endpoint for agent_runner to fetch agent context.

    Payload versions:
    - v2 (default): Full payload with history, context_data, system_prompt, etc.
    - v3 (minimal): Just identity, trigger, mcp_auth - agent fetches context via MCP tools

    Requires X-API-Key header for authentication.
    """
    # Validate API key with timing-safe comparison to prevent timing attacks
    settings = get_settings()
    if not x_api_key or not secrets_module.compare_digest(x_api_key, settings.agent_runner_api_key):
        logger.warning(f"❌ Unauthorized internal API access attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

    try:
        # Find the agent by name in the specified org
        result = await session.db.execute(
            select(Agent)
            .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
            .where(
                and_(
                    func.lower(Agent.name) == agent_name.lower(),
                    or_(
                        AgentSpaceAccess.space_id == uuid.UUID(space_id),
                        Agent.visibility_level == "global",
                    ),
                    Agent.cloud_function_url.isnot(None),
                )
            )
        )
        agent = result.scalar_one_or_none()

        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Cloud agent '{agent_name}' not found in org {space_id}",
            )

        # Generate dispatch ID for tracing
        import uuid as uuid_module
        dispatch_id = str(uuid_module.uuid4())[:8]

        # Get owner handle for context
        owner_handle = None
        if agent.user_id:
            owner_row = await session.db.execute(
                select(User.username).where(User.id == agent.user_id)
            )
            owner_handle = owner_row.scalar_one_or_none()

        # Fetch space/org info for context
        from app.models.space import Space
        space_result = await session.db.execute(
            select(Space.name)
            .where(Space.id == uuid.UUID(space_id))
        )
        space_name = space_result.scalar_one_or_none() or "Unknown Space"

        # V3 MINIMAL PAYLOAD — agent hydrates context via MCP tools
        if True:
            # Only fetch trigger message info (not full history)
            user_message = ""
            sender_handle = None
            sender_type = "user"
            trigger_attachments: list[dict] = []

            if message_id:
                from app.models.message import Message
                from app.models.message_file import MessageFile
                msg_result = await session.db.execute(
                    select(Message.content, Message.user_id, Message.agent_id)
                    .where(Message.id == uuid.UUID(message_id))
                )
                msg_row = msg_result.first()
                if msg_row:
                    user_message = msg_row[0] or ""
                    if msg_row[2]:  # agent_id = sent by agent
                        sender_type = "agent"
                        agent_sender = await session.db.execute(
                            select(Agent.name).where(Agent.id == msg_row[2])
                        )
                        sender_handle = agent_sender.scalar_one_or_none()
                    elif msg_row[1]:  # user_id = sent by user
                        sender_type = "user"
                        sender_row = await session.db.execute(
                            select(User.username).where(User.id == msg_row[1])
                        )
                        sender_handle = sender_row.scalar_one_or_none()

                    attachment_result = await session.db.execute(
                        select(MessageFile)
                        .where(MessageFile.message_id == uuid.UUID(message_id))
                        .order_by(MessageFile.created_at.asc())
                    )
                    trigger_attachments = [
                        {
                            "id": str(attachment.id),
                            "filename": attachment.filename,
                            "file_size": attachment.file_size,
                            "mime_type": attachment.mime_type,
                            "storage_path": attachment.file_path,
                            "public_url": attachment.file_url,
                            "created_at": attachment.created_at.isoformat() if attachment.created_at else None,
                        }
                        for attachment in attachment_result.scalars().all()
                    ]

            # Get cached MCP auth token or mint new one (sliding window TTL)
            # REQUIRED for V3 - agent needs token to hydrate context via MCP tools
            if not agent.user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="V3 payload requires agent to have an owner (user_id) for MCP auth",
                )

            mcp_auth = await get_or_mint_mcp_token(
                agent_id=str(agent.id),
                agent_name=agent.name,
                user_id=str(agent.user_id),
                space_id=space_id,
            )

            if not mcp_auth:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="V3 payload requires mcp_auth but token minting failed. Retry later.",
                )

            # Return minimal payload - agent fetches rest via MCP
            minimal_payload = {
                "payload_version": "3",
                "agent_id": str(agent.id),
                "agent_name": agent.name,
                "space_id": space_id,
                "space_name": space_name,
                "trigger_message": {
                    "id": message_id,
                    "content": user_message,
                    "sender_handle": sender_handle,
                    "sender_type": sender_type,
                    "attachments": trigger_attachments,
                },
                "mcp_auth": mcp_auth,
                "model": agent.model or DEFAULT_MODEL,
                # Tool configuration - use helper from toggle registry (DRY)
                "tool_config": {
                    "enabled_tools": build_enabled_tools_from_agent(agent),
                    # Legacy fields (deprecated, kept for backwards compat with runner)
                    "web_browsing_enabled": agent.web_browsing_enabled,
                    "ax_mcp_enabled": agent.ax_mcp_enabled,
                    "image_gen_enabled": agent.image_gen_enabled,
                    "capabilities": agent.capabilities or {},
                },
            }

            # Log for observability dashboard (V3 dispatch path)
            model = agent.model or DEFAULT_MODEL
            tool_config = minimal_payload["tool_config"]
            msg_preview = user_message[:200].replace("\n", " ") if user_message else ""
            print(
                f"DISPATCH_CONTEXT_FETCH dispatch_id={dispatch_id} "
                f"agent_name={agent_name} agent_id={agent.id} "
                f"message_id={message_id} space_id={space_id}"
            )
            print(
                f"DISPATCH_PAYLOAD dispatch_id={dispatch_id} "
                f"agent_name={agent_name} model={model} "
                f"sender={sender_handle} sender_type={sender_type} "
                f"web_browsing={tool_config.get('web_browsing_enabled', False)} "
                f"ax_mcp={tool_config.get('ax_mcp_enabled', False)} "
                f"image_gen={tool_config.get('image_gen_enabled', False)} "
                f"message_preview=\"{msg_preview}\""
            )

            logger.info(f"📤 V3 payload for {agent_name}")
            return minimal_payload

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to get cloud agent context: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get agent context: {e!s}",
        )

@router.put("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    agent_update: AgentUpdate,
    request: Request,
    session: SecureSession = Depends(get_secure_session)
):
    """Update an agent's details"""
    try:
        # Verify agent exists and belongs to current user.
        # Exception: any member of a selected space may enable that space's
        # registered aX agent, but cannot mutate other settings through this path.
        try:
            result = await session.db.execute(
                select(Agent).where(and_(Agent.id == uuid.UUID(agent_id), Agent.user_id == session.user.id))
            )
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid agent ID format")
        agent = result.scalar_one_or_none()
        member_scoped_ax_enable = False

        if not agent:
            if not _is_space_member_ax_enable_update(agent_update):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
            selected_space_id = await _resolve_agent_control_space_id(session, request)
            agent = await _get_registered_space_agent_for_member(session.db, agent_id, selected_space_id)
            member_scoped_ax_enable = True

        # Check ownership - only owner can update non-aX-member-scoped fields
        if str(agent.user_id) != str(session.user.id) and not member_scoped_ax_enable:
            if not _is_space_member_ax_enable_update(agent_update):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only update your own agents")
            selected_space_id = await _resolve_agent_control_space_id(session, request)
            agent = await _get_registered_space_agent_for_member(session.db, agent_id, selected_space_id)
            member_scoped_ax_enable = True

        # Build update dictionary
        update_data = {}

        # Auto-migrate existing cloud agents to pinned mode
        # Cloud agents must always be pinned - fix any that are in free roam or follow mode
        if agent.cloud_function_url:
            needs_migration = False
            target_org = None

            if agent.pinned_to_space is None:
                # Free roam mode - pin to current org
                needs_migration = True
                target_org = agent.space_id or session.space_id
                logger.info(
                    f"Auto-migrating cloud agent {agent.name} from free roam to pinned mode (org: {target_org})"
                )
            elif agent.pinned_to_space == FOLLOW_UUID:
                # Follow mode - pin to user's current org
                needs_migration = True
                target_org = session.space_id
                logger.info(
                    f"Auto-migrating cloud agent {agent.name} from follow mode to pinned mode (org: {target_org})"
                )

            if needs_migration and target_org:
                update_data["pinned_to_space"] = target_org
                update_data["space_id"] = target_org

        if agent_update.name is not None:
            # Validate agent name format (pattern, length, reserved names)
            is_valid, error_message = validate_agent_name(agent_update.name)
            if not is_valid:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_message)

            # Check for name conflicts
            # Mirror registration logic: cloud agents skip global check, only enforce per-user uniqueness
            if is_cloud_agent(agent_update.name):
                # Cloud agent (e.g., chirpy): only check per-user uniqueness
                name_check = await session.db.execute(
                    select(Agent).where(
                        and_(
                            func.lower(Agent.name) == agent_update.name.lower(),
                            Agent.user_id == session.user.id,
                            Agent.id != agent.id,
                        )
                    )
                )
                if name_check.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail="You already have a cloud agent with this name"
                    )
            else:
                # Regular agent: enforce global case-insensitive uniqueness
                name_check = await session.db.execute(
                    select(Agent).where(and_(func.lower(Agent.name) == agent_update.name.lower(), Agent.id != agent.id))
                )
                if name_check.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST, detail="Agent name already exists globally"
                    )
            update_data["name"] = agent_update.name

        if agent_update.description is not None:
            update_data["description"] = agent_update.description
        # Agent self-documentation fields (also settable via whoami MCP tool)
        if agent_update.bio is not None:
            update_data["bio"] = agent_update.bio or None
        if agent_update.specialization is not None:
            update_data["specialization"] = agent_update.specialization or None
        # agent_type is derived from origin — cannot be changed independently (AGENTS-001)
        if agent_update.capabilities is not None:
            update_data["capabilities"] = agent_update.capabilities
        if agent_update.space_locked is not None:
            update_data["space_locked"] = agent_update.space_locked
        if agent_update.system_prompt is not None:
            # Allow clearing by sending empty string
            update_data["system_prompt"] = agent_update.system_prompt or None
        if agent_update.enable_cloud_agent is not None:
            # Auto-populate or clear cloud_function_url based on toggle
            settings = get_settings()
            if agent_update.enable_cloud_agent:
                # Check if cloud agent creation is enabled (global kill switch)
                redis = redis_client
                dynamic_setting_key = "system:settings:cloud_agent_creation_enabled"
                raw_setting = await redis.get(dynamic_setting_key)

                if raw_setting is not None:
                    # Handle both bytes (some Redis clients) and str (decode_responses=True)
                    value_str = raw_setting.decode() if isinstance(raw_setting, bytes) else raw_setting
                    creation_enabled = value_str.lower() == "true"
                else:
                    creation_enabled = settings.cloud_agent_creation_enabled

                if not creation_enabled:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Cloud agent creation is currently disabled. Contact support if you need access.",
                    )
                update_data["cloud_function_url"] = settings.agent_runner_stable_url  # Default to stable
            else:
                update_data["cloud_function_url"] = None
        if agent_update.status is not None:
            update_data["status"] = agent_update.status
        if agent_update.avatar_url is not None:
            update_data["avatar_url"] = agent_update.avatar_url
        if agent_update.web_browsing_enabled is not None:
            # Determine if agent will be a cloud agent after this update
            is_disabling_cloud = agent_update.enable_cloud_agent is False
            will_be_cloud = (agent.cloud_function_url or agent_update.enable_cloud_agent is True) and not is_disabling_cloud

            # Only allow web browsing for cloud agents
            if agent_update.web_browsing_enabled and not will_be_cloud:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Web browsing can only be enabled for cloud agents",
                )
            # Only admin and plus users can enable web browsing
            user_role = getattr(session.user, "role", "user").lower()
            if agent_update.web_browsing_enabled and user_role not in ("admin", "plus"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="🌐 Web Browsing is a Plus feature! Visit ax-platform.com or join our Discord to upgrade.",
                )
            update_data["web_browsing_enabled"] = agent_update.web_browsing_enabled

        # Handle toggle fields using the central registry (DRY pattern)
        # See app/core/agent_toggles.py for toggle definitions
        current_template = agent.template_type or "ax_agent"
        template_info = get_template(current_template)
        template_caps = template_info.get("capabilities", {}) if template_info else {}

        # Support both new enabled_tools format and legacy individual fields
        if agent_update.enabled_tools is not None:
            # New format: enabled_tools dict
            validated_tools = validate_enabled_tools_update(
                enabled_tools=agent_update.enabled_tools,
                user=session.user,
                template_capabilities=template_caps,
            )
            # Get current enabled_tools and merge with updates
            current_tools = build_enabled_tools_from_agent(agent)
            current_tools.update(validated_tools)
            update_data["enabled_tools"] = current_tools
            # Also sync to legacy columns for backwards compat (auto-synced via registry)
            update_data.update(get_legacy_column_updates_from_enabled_tools(current_tools))
        else:
            # Legacy format: individual toggle fields
            toggle_updates = process_toggle_updates(
                agent_update=agent_update,
                agent=agent,
                user=session.user,
                template_capabilities=template_caps,
            )
            if toggle_updates:
                update_data.update(toggle_updates)
                # Also update enabled_tools JSONB
                current_tools = build_enabled_tools_from_agent(agent)
                for legacy_field, value in toggle_updates.items():
                    # Map legacy field name to tool key via registry
                    toggle = TOGGLE_BY_LEGACY_FIELD.get(legacy_field)
                    if toggle:
                        current_tools[toggle.tool_key] = value
                update_data["enabled_tools"] = current_tools

        # Handle template_type change
        if agent_update.template_type is not None:
            template = get_template(agent_update.template_type)
            if not template:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid template type: {agent_update.template_type}",
                )
            update_data["template_type"] = agent_update.template_type

        # Handle model selection with user role validation
        if agent_update.model is not None:
            # Validate model based on user's role (subscription is per-user, not per-workspace)
            user_role = getattr(session.user, "role", "user") or "user"
            is_valid, error_msg = validate_model_for_user_role(agent_update.model, user_role)
            if not is_valid:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
            update_data["model"] = agent_update.model

        # Handle webhook_url update for external gateway agents
        if agent_update.webhook_url is not None:
            # Only allow updating webhook_url for external_gateway agents
            if agent.origin != "external_gateway":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="webhook_url can only be set for external gateway agents"
                )
            # Basic URL validation
            if not agent_update.webhook_url.startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="webhook_url must be a valid HTTP/HTTPS URL"
                )
            # Only update if URL actually changed
            if agent_update.webhook_url != agent.webhook_url:
                # SSRF validation before accepting new URL
                from app.services.webhook_dispatch_service import validate_webhook_url
                is_valid, ssrf_error = validate_webhook_url(agent_update.webhook_url)
                if not is_valid:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid webhook URL: {ssrf_error}"
                    )

                update_data["webhook_url"] = agent_update.webhook_url

                # Auto-unquarantine if agent was quarantined due to dispatch failures
                if agent.status == "quarantined":
                    update_data["status"] = "active"
                    logger.info(f"Auto-unquarantined agent {agent.name} due to webhook URL change")

                # Clear failure counter in Redis (best effort)
                try:
                    from app.core.redis_client import redis_client as webhook_redis_client
                    await webhook_redis_client.delete(f"webhook_dispatch_failures:{agent.id}")
                except Exception:
                    pass  # Don't fail the update if Redis is unavailable

                # NOTE: Secret is NOT auto-regenerated - user must explicitly regenerate
                # via the UI button if they need a new secret. This avoids breaking
                # existing integrations when only the URL changes (e.g., tunnel restart).
                logger.info(
                    f"Webhook URL updated for agent {agent.name}: {agent_update.webhook_url} "
                    f"(secret unchanged - use regenerate button if needed)"
                )

        # Build allowed URLs list for validation (used by both engine_version and cloud_function_url)
        from urllib.parse import urlparse
        settings = get_settings()
        allowed_urls = [
            settings.agent_runner_stable_url,
            settings.agent_runner_experimental_url,
            # Legacy fallbacks (deprecated, kept for backwards compat)
            settings.agent_runner_url,
            settings.agent_runner_v1_url,
            settings.agent_runner_v2_url,
        ]
        # Filter out empty strings
        allowed_urls = [url for url in allowed_urls if url]

        def validate_runner_url(url: str) -> bool:
            """Validate URL is from our known runner URLs (security check)."""
            if url in allowed_urls:
                return True
            # Strict URL validation - compare scheme, netloc, and path exactly
            # This prevents path traversal attacks like http://agent_runner:8080/../evil
            try:
                parsed_input = urlparse(url)
                return any(
                    parsed_input.scheme == urlparse(allowed).scheme and
                    parsed_input.netloc == urlparse(allowed).netloc and
                    parsed_input.path == urlparse(allowed).path
                    for allowed in allowed_urls if allowed
                )
            except Exception:
                return False

        # Handle engine_version (cleaner API - maps version to URL)
        # Process this FIRST - if provided, skip cloud_function_url processing
        if agent_update.engine_version is not None:
            version_to_url = {
                "v1": settings.agent_runner_stable_url,  # v1 = stable
                "v2": settings.agent_runner_experimental_url,  # v2 = experimental
            }
            target_url = version_to_url.get(agent_update.engine_version)
            if not target_url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Engine version '{agent_update.engine_version}' is not available. V2 runner may not be deployed.",
                )
            # Security: validate mapped URL is in allowed list (protects against misconfigured env vars)
            if not validate_runner_url(target_url):
                logger.error(f"Misconfigured runner URL for {agent_update.engine_version}: {target_url}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Server misconfiguration: invalid runner URL. Contact admin.",
                )
            update_data["cloud_function_url"] = target_url
            logger.info(f"Agent {agent_id} engine version set to {agent_update.engine_version} -> {target_url}")

        # Handle direct cloud_function_url update (legacy - only if engine_version not provided)
        elif agent_update.cloud_function_url is not None:
            if agent_update.cloud_function_url and not validate_runner_url(agent_update.cloud_function_url):
                logger.warning(
                    f"Attempt to set non-allowed cloud_function_url: {agent_update.cloud_function_url}"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid cloud function URL. Must be a platform-managed runner URL.",
                )
            update_data["cloud_function_url"] = agent_update.cloud_function_url or None

        # Cloud agents must always stay pinned - they don't have MCP tools to switch spaces
        is_cloud_agent_instance = bool(agent.cloud_function_url) or (
            agent_update.enable_cloud_agent is True
            or (agent_update.enable_cloud_agent is None and bool(agent.cloud_function_url))
        )

        # Handle pin/unpin and org move
        # 1) Pin/unpin first - only process if field was explicitly provided in request
        if "pinned_space_id" in agent_update.model_fields_set:
            # None or empty string means unpin
            if not agent_update.pinned_space_id or agent_update.pinned_space_id == "":
                # CRITICAL: Prevent unpinning cloud agents (including Chirpy)
                if is_cloud_agent_instance:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cloud agents must be pinned to a space. They cannot operate in free roam mode.",
                    )
                update_data["pinned_to_space"] = None
                logger.info(f"Unpinning agent {agent.id}")
            else:
                try:
                    pinned_uuid = uuid.UUID(agent_update.pinned_space_id)
                except ValueError:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid pinned_space_id")
                if pinned_uuid == FOLLOW_UUID:
                    # CRITICAL: Cloud agents cannot use follow mode
                    if is_cloud_agent_instance:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cloud agents cannot use follow mode. They must be pinned to a specific space.",
                        )
                    # Virtual follow workspace: bypass existence/membership and align space_id
                    update_data["pinned_to_space"] = FOLLOW_UUID
                    update_data["space_id"] = FOLLOW_UUID
                    logger.info(f"Agent {agent.id} set to FOLLOW mode (virtual workspace)")
                else:
                    # Verify membership for real organizations
                    from ...models.space_membership import SpaceMembership

                    mem = await session.db.execute(select(Space).where(Space.id == pinned_uuid))
                    if not mem.scalar_one_or_none():
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND, detail="Pinned organization not found"
                        )
                    membership = await session.db.execute(
                        select(SpaceMembership).where(
                            and_(
                                SpaceMembership.user_id == session.user.id,
                                SpaceMembership.space_id == pinned_uuid,
                            )
                        )
                    )
                    if not membership.scalar_one_or_none():
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="You are not a member of the pinned organization",
                        )
                    # Align space_id with pinned org to satisfy DB constraint
                    update_data["pinned_to_space"] = pinned_uuid
                    update_data["space_id"] = pinned_uuid

        # 2) Move space_id only if not pinned (or switching to FOLLOW)
        if agent_update.space_id is not None:
            logger.info(f"Processing space_id update: {agent_update.space_id}")

            # Check if this is a FOLLOW request
            is_follow_request = False
            try:
                is_follow_request = str(agent_update.space_id) == str(FOLLOW_UUID)
                logger.info(
                    f"Is follow request: {is_follow_request}, space_id={agent_update.space_id}, FOLLOW_UUID={FOLLOW_UUID}"
                )
            except Exception as e:
                logger.error(f"Error checking follow request: {e}")

            # If agent is currently pinned, only allow moves to FOLLOW mode
            if agent.pinned_to_space and agent.pinned_to_space != FOLLOW_UUID:
                # Check if we're switching to FOLLOW mode in this same request
                switching_to_follow = (
                    is_follow_request
                    or update_data.get("pinned_to_space") == FOLLOW_UUID
                    or (agent_update.pinned_space_id and str(agent_update.pinned_space_id) == str(FOLLOW_UUID))
                )
                if not switching_to_follow:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Agent is pinned to a workspace and cannot be moved. Unpin first or switch to Follow mode.",
                    )

            if agent_update.space_id == "":
                update_data["space_id"] = None
            else:
                try:
                    target_org = uuid.UUID(agent_update.space_id)
                except ValueError:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid space_id format")

                # Block moves for space-locked agents (privacy guard for sensitive spaces)
                if getattr(agent, 'space_locked', False) and target_org != agent.space_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=(
                            f"Agent '{agent.name}' is locked to its current space. "
                            "Unlock it first by setting space_locked=false, then move."
                        ),
                    )

                if target_org == FOLLOW_UUID:
                    # CRITICAL: Cloud agents cannot use follow mode
                    if is_cloud_agent_instance:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Cloud agents cannot use follow mode. They must be pinned to a specific space.",
                        )
                    # Virtual follow workspace: bypass all checks
                    update_data["space_id"] = FOLLOW_UUID
                    # Also ensure pinned_to_space matches for consistency
                    if "pinned_to_space" not in update_data:
                        update_data["pinned_to_space"] = FOLLOW_UUID
                    logger.info(f"Agent {agent.id} space_id set to FOLLOW mode")
                else:
                    # Verify membership for real organizations
                    from ...models.space_membership import SpaceMembership

                    # First check if org exists
                    org_check = await session.db.execute(select(Space).where(Space.id == target_org))
                    if not org_check.scalar_one_or_none():
                        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Space not found")

                    # Then check membership
                    membership_check = await session.db.execute(
                        select(SpaceMembership).where(
                            and_(
                                SpaceMembership.user_id == session.user.id,
                                SpaceMembership.space_id == target_org,
                            )
                        )
                    )
                    if not membership_check.scalar_one_or_none():
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="You are not a member of the target organization",
                        )

                    update_data["space_id"] = target_org

        # Track if we regenerated webhook_secret (to include in response)
        new_webhook_secret = update_data.get("webhook_secret")

        if update_data:
            # Capture pre-mutation placement BEFORE the update() execute below.
            # `agent` is the pre-mutation ORM object, so agent.space_id still
            # holds the OLD value here — that is the live old-space stream key we
            # signal for an instant move.
            placement_old_space = agent.space_id
            placement_new_space = update_data.get("space_id")

            update_data["updated_at"] = datetime.utcnow()
            await session.db.execute(update(Agent).where(Agent.id == agent.id).values(**update_data))

            # Dual-write: sync agent_space_access when pinned_to_space changes
            if "pinned_to_space" in update_data:
                from app.core.agent_space import grant_space_access
                new_space = update_data["pinned_to_space"]
                if new_space and new_space != FOLLOW_UUID:
                    await grant_space_access(session.db, agent.id, new_space, is_default=True)

            await session.db.commit()
            await session.db.refresh(agent)

            # Instant move: only emit when space_id actually changed. After
            # commit+refresh so the adapter's re-read sees committed state.
            if (
                placement_new_space
                and placement_old_space
                and str(placement_new_space) != str(placement_old_space)
            ):
                from app.services.redis_sse_broker import publish_placement_changed
                await publish_placement_changed(
                    old_space_id=placement_old_space,
                    new_space_id=placement_new_space,
                    agent_id=agent.id,
                )

            # Update status cache for circuit breakers if status changed
            if "status" in update_data:
                await _write_agent_status_cache(agent.id, agent.status)

        response = await _build_agent_response(agent)

        # Include webhook_secret in response if it was regenerated (Stripe pattern - shown ONCE)
        if new_webhook_secret:
            response.webhook_secret = new_webhook_secret
            logger.info(f"Returning new webhook_secret for agent {agent.name} (URL changed)")

        return response

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update agent: {e!s}")

@router.get("/agents/{agent_id}/control", response_model=AgentControlStateResponse)
async def get_agent_control(
    agent_id: str,
    request: Request,
    session: SecureSession = Depends(get_secure_session)
):
    """Fetch kill-switch and rate limit settings for an agent."""

    owner_id, owner_role, _ = await _resolve_agent_control_owner(session, request)
    try:
        agent = await _get_agent_for_owner(
            session.db,
            owner_id,
            agent_id,
            owner_role=owner_role,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_403_FORBIDDEN:
            raise
        selected_space_id = await _resolve_agent_control_space_id(session, request)
        agent = await _get_agent_in_current_space(session.db, agent_id, selected_space_id)
        if getattr(agent, "origin", None) != "space_agent":
            raise exc
    agent_slug = (agent.name or "").strip().lower()
    control_state = await agent_control_service.get_control_state(
        agent_id=agent.id,
        space_id=agent.space_id,
        agent_slug=agent_slug,
    )
    return AgentControlStateResponse(**control_state.as_dict())

@router.post("/agents/{agent_id}/control/flush-cache", response_model=AgentControlFlushResponse)
async def flush_agent_control_cache(
    agent_id: str,
    request: Request,
    session: SecureSession = Depends(get_secure_session)
):
    """Clear legacy kill-switch Redis cache keys for an agent.

    This is an explicit recovery/audit affordance for the old dispatch fast-path
    keys (`agent:{id}:disabled` / `paused`) when the roster/control state says an
    agent is enabled but dispatch remains blocked. It does not change the
    authoritative control hash.
    """

    owner_id, owner_role, _ = await _resolve_agent_control_owner(session, request)
    try:
        agent = await _get_agent_for_owner(
            session.db,
            owner_id,
            agent_id,
            owner_role=owner_role,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_403_FORBIDDEN:
            raise
        selected_space_id = await _resolve_agent_control_space_id(session, request)
        agent = await _get_agent_in_current_space(session.db, agent_id, selected_space_id)
        if getattr(agent, "origin", None) != "space_agent":
            raise exc

    flush_result = await agent_control_service.flush_agent_kill_switch_cache(agent.id)
    agent_slug = (agent.name or "").strip().lower()
    control_state = await agent_control_service.get_control_state(
        agent_id=agent.id,
        space_id=agent.space_id,
        agent_slug=agent_slug,
    )
    logger.info(
        "AGENT_CONTROL_LEGACY_CACHE_FLUSH",
        extra={
            "agent_id": str(agent.id),
            "actor_user_id": str(session.user.id),
            "space_id": str(agent.space_id) if agent.space_id else None,
            "flushed_keys": flush_result["cleared_keys"],
            "status_cache": flush_result["status_cache"],
            "control": control_state.as_dict(),
        },
    )
    return AgentControlFlushResponse(
        agent_id=str(agent.id),
        flushed_legacy_keys=flush_result["cleared_keys"],
        status_cache=flush_result["status_cache"],
        control=AgentControlStateResponse(**control_state.as_dict()),
    )

@router.patch("/agents/{agent_id}/control", response_model=AgentControlStateResponse)
async def update_agent_control(
    agent_id: str,
    control_update: AgentControlUpdateRequest,
    request: Request,
    session: SecureSession = Depends(get_secure_session)
):
    """Update kill-switch state or rate limits for an agent."""

    owner_id, owner_role, is_delegated = await _resolve_agent_control_owner(session, request)
    if is_delegated and control_update.scope == "global":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Delegated aX control cannot update the global kill switch",
        )

    try:
        agent = await _get_agent_for_owner(
            session.db,
            owner_id,
            agent_id,
            owner_role=owner_role,
            allow_admin_override=(
                control_update.scope == "global"
                and (owner_role or "").lower() == "admin"
                and not is_delegated
            ),
        )
    except HTTPException as exc:
        can_space_toggle_space_agent = (
            exc.status_code == status.HTTP_403_FORBIDDEN
            and control_update.scope == "agent"
            and "disabled" in control_update.model_fields_set
            and control_update.model_fields_set <= SPACE_MEMBER_AGENT_CONTROL_FIELDS
        )
        if not can_space_toggle_space_agent:
            raise
        selected_space_id = await _resolve_agent_control_space_id(session, request)
        agent = await _get_agent_in_current_space(session.db, agent_id, selected_space_id)
        if getattr(agent, "origin", None) != "space_agent":
            raise exc

    agent_slug = (agent.name or "").strip().lower()
    managed_slug = (
        control_update.target_slug.strip().lower()
        if control_update.scope == "managed" and control_update.target_slug
        else agent_slug
    )

    if control_update.scope == "managed" and not managed_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Managed scope requires target_slug",
        )

    if control_update.scope == "global" and (owner_role or "").lower() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global kill switch requires admin privileges",
        )

    if control_update.scope == "global":
        logger.info(
            "Global kill switch update requested",
            extra={
                "agent_id": agent_id,
                "user": str(session.user.id),
                "updates_requested": control_update.model_dump(exclude_unset=True),
            },
        )

    updates: dict = {}
    for field in [
        "disabled",
        "disabled_until",
        "reason",
        "no_reply",
        "no_reply_reason",
        "no_reply_until",
        "routing_only",
        "routing_only_reason",
        "routing_only_until",
        "user_hourly_limit",
        "user_daily_limit",
        "agent_hourly_limit",
        "agent_daily_limit",
    ]:
        if field in control_update.model_fields_set:
            updates[field] = getattr(control_update, field)

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No control updates provided",
        )

    control_state = await agent_control_service.update_agent_control(
        agent_id=agent.id,
        space_id=agent.space_id,
        agent_slug=managed_slug,
        scope=control_update.scope,
        updates=updates,
    )

    return AgentControlStateResponse(**control_state.as_dict())

@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: str, session: SecureSession = Depends(get_secure_session)
):
    """Delete an agent"""
    try:
        # Verify agent exists and belongs to current user
        result = await session.db.execute(
            select(Agent).where(and_(Agent.id == uuid.UUID(agent_id), Agent.user_id == session.user.id))
        )
        agent = result.scalar_one_or_none()

        if not agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

        # Space Agents cannot be deleted — disable via space settings instead
        if agent.origin == "space_agent":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Space Agent cannot be deleted. Disable it via space settings.",
            )

        # Check ownership - only owner can update
        if str(agent.user_id) != str(session.user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own agents")

        # Delete the agent
        await session.db.execute(delete(Agent).where(Agent.id == agent.id))
        await session.db.commit()

        return {"message": "Agent deleted successfully", "agent_id": str(agent.id)}

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete agent: {e!s}")

# =============================================================================
# Webhook Verification Endpoint (Moltbot Integration)
# =============================================================================

class WebhookVerificationResponse(BaseModel):
    """Response for webhook verification request."""
    status: str  # pending, verified, failed
    message: str
    webhook_verified: bool
    # Stripe pattern: secret shown ONCE on successful verification
    webhook_secret: str | None = None  # Only returned on success, store securely!

@router.post("/agents/{agent_id}/verify-webhook", response_model=WebhookVerificationResponse)
async def verify_agent_webhook(
    agent_id: str,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Trigger webhook verification for an external gateway agent.

    Sends a WebSub-style challenge to the agent's webhook_url.
    The external agent must respond with the challenge token to verify ownership.

    Only the agent owner can trigger verification.
    """
    from app.services.webhook_dispatch_service import (
        verify_webhook,
        WebhookVerificationError,
        clear_dispatch_failures,
    )

    try:
        # Get agent and verify ownership
        result = await session.db.execute(
            select(Agent).where(Agent.id == uuid.UUID(agent_id))
        )
        agent = result.scalar_one_or_none()

        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found"
            )

        if str(agent.user_id) != str(session.user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the agent owner can trigger webhook verification"
            )

        if not agent.webhook_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent has no webhook_url configured"
            )

        if agent.origin != "external_gateway":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook verification only applies to external_gateway agents"
            )

        # Check if quarantined - verification is the path back to active
        was_quarantined = agent.status == "quarantined"

        # Attempt verification
        success = await verify_webhook(session.db, agent)

        if success:
            # Unlock: Clear any failure counter in Redis (prevents dispatch blocking after re-verification)
            await clear_dispatch_failures(agent.id)
            logger.info(f"Agent {agent_id} unlocked - failure counter cleared")

            # If agent was quarantined, restore to active status
            if was_quarantined:
                agent.status = "active"
                await session.db.commit()
                logger.info(f"Agent {agent_id} restored from quarantine via re-verification")

            # Refresh to get the generated webhook_secret
            await session.db.refresh(agent)

            return WebhookVerificationResponse(
                status="verified",
                message="Webhook verified successfully. IMPORTANT: Store the webhook_secret securely - it will not be shown again!",
                webhook_verified=True,
                webhook_secret=agent.webhook_secret,  # Stripe pattern: shown ONCE
            )
        else:
            return WebhookVerificationResponse(
                status="failed",
                message="Webhook verification failed - agent did not respond with correct challenge",
                webhook_verified=False,
            )

    except WebhookVerificationError as e:
        return WebhookVerificationResponse(
            status="failed",
            message=str(e),
            webhook_verified=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Webhook verification error: {e!s}"
        )

class RegenerateWebhookSecretResponse(BaseModel):
    """Response with new webhook secret (ONE TIME REVEAL)."""
    status: str
    message: str
    agent_id: str  # Agent UUID for gateway config
    agent_handle: str  # @handle format
    webhook_secret: str  # ONE TIME REVEAL - Stripe pattern
    gateway_config: str  # Copy-paste ready: AGENT_1=<id>|<secret>|@<handle>|local
    env_snippet: str  # Legacy format for backwards compat

@router.post("/agents/{agent_id}/regenerate-webhook-secret", response_model=RegenerateWebhookSecretResponse)
async def regenerate_webhook_secret(
    agent_id: str,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Regenerate the webhook_secret for an external gateway agent.

    Invalidates the old secret immediately. The agent must update its
    configuration with the new secret to continue receiving dispatches.

    Only the agent owner can regenerate the secret.
    Returns the new secret ONCE (Stripe pattern - store securely).
    """
    import secrets
    from app.services.webhook_dispatch_service import clear_dispatch_failures

    try:
        agent_uuid = uuid.UUID(agent_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent_id format"
        )

    # Get agent
    result = await session.db.execute(
        select(Agent).where(Agent.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )

    # Verify ownership
    if str(agent.user_id) != str(session.user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the agent owner can regenerate webhook secret"
        )

    # Verify it's an external gateway agent
    if agent.origin != "external_gateway":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Secret regeneration only applies to external_gateway agents"
        )

    # Generate new secret (same format as initial registration)
    new_secret = secrets.token_urlsafe(32)
    agent.webhook_secret = new_secret

    # Clear any failure counters (fresh start with new secret)
    await clear_dispatch_failures(agent.id)

    await session.db.commit()
    await session.db.refresh(agent)

    # Format gateway config for Clawdbot (copy-paste ready)
    agent_handle = f"@{agent.name}"
    gateway_config = f"AGENT_1={agent.id}|{new_secret}|{agent_handle}|local"
    env_snippet = f"AX_WEBHOOK_SECRET={new_secret}"

    logger.info(f"Webhook secret regenerated for agent {agent_id} by user {session.user.id}")

    return RegenerateWebhookSecretResponse(
        status="success",
        message="Webhook secret regenerated. IMPORTANT: Store securely - it will not be shown again!",
        agent_id=str(agent.id),
        agent_handle=agent_handle,
        webhook_secret=new_secret,
        gateway_config=gateway_config,
        env_snippet=env_snippet,
    )

# =============================================================================
# Combined External Agent Registration (Moltbot Integration)
# =============================================================================

class ExternalAgentRegisterRequest(BaseModel):
    """Request to register an external webhook agent (Moltbot, Ollama, custom)."""
    name: str = Field(..., min_length=3, max_length=50, description="Agent name (unique)")
    webhook_url: str = Field(..., max_length=512, description="Webhook URL for dispatch")
    description: str | None = Field(None, max_length=500, description="Optional description")
    sub_type: str | None = Field("moltbot", max_length=50, description="Sub-type for tracking (moltbot, ollama, custom)")

class ExternalAgentRegisterResponse(BaseModel):
    """Response with agent details and webhook secret (ONE TIME)."""
    agent_id: str
    agent_handle: str
    webhook_secret: str  # ONE TIME REVEAL - Stripe pattern
    webhook_verified: bool
    gateway_config: str  # Copy-paste ready: AGENT_1=<id>|<secret>|@<handle>|local
    env_snippet: str  # Legacy format for backwards compat



@router.post("/agents/{agent_id}/move", response_model=AgentMoveResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_agent_move(
    agent_id: str,
    body: AgentMoveRequest,
    request: Request,
    session: SecureSession = Depends(get_secure_session),
):
    """Create a human-in-the-loop move proposal for a user-owned agent."""
    del request  # reserved for future actor/audit enrichment
    agent = await _get_agent_for_user(session.db, session.user, agent_id)

    try:
        destination_space_id = uuid.UUID(body.destination_space_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid destination_space_id") from exc

    if agent.space_locked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Agent is space-locked and cannot be moved")

    if destination_space_id == agent.space_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent is already in that space")

    from ...models.space_membership import SpaceMembership

    destination = await session.db.execute(select(Space).where(Space.id == destination_space_id))
    if not destination.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination space not found")

    membership = await session.db.execute(
        select(SpaceMembership).where(
            and_(
                SpaceMembership.user_id == session.user.id,
                SpaceMembership.space_id == destination_space_id,
            )
        )
    )
    if not membership.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of the destination space")

    proposed_payload = {
        "kind": "agents.move.user_owned",
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "source_space_id": str(agent.space_id),
        "destination_space_id": str(destination_space_id),
        "requested_by_user_id": str(session.user.id),
        "approval_state": "pending_human_approval",
        "reason": body.reason,
    }

    async with system_session_context() as system_ctx:
        if body.idempotency_key:
            existing_result = await system_ctx.db.execute(
                select(AgentManagementProposal).where(
                    AgentManagementProposal.idempotency_key == body.idempotency_key
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing:
                if str(existing.target_agent_id) != str(agent.id):
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="idempotency_key is already in use")
                return AgentMoveResponse(
                    proposal_id=str(existing.id),
                    status=existing.status,
                    approval_state="pending_human_approval",
                    agent_id=str(agent.id),
                    source_space_id=str(existing.source_space_id),
                    destination_space_id=str(existing.destination_space_id),
                    current_space_id=str(agent.space_id),
                    default_space_id=str(agent.home_space_id) if agent.home_space_id else None,
                )

        proposal = AgentManagementProposal(
            id=uuid.uuid4(),
            space_id=agent.space_id,
            proposal_type="move_user_agent_between_spaces",
            status="pending",
            requested_by_user_id=session.user.id,
            target_agent_id=agent.id,
            target_owner_user_id=session.user.id,
            target_space_id=destination_space_id,
            source_space_id=agent.space_id,
            destination_space_id=destination_space_id,
            approval_requirements={
                "approval_required": True,
                "review_mode": "human_in_the_loop",
                "required_approvals": ["owner"],
            },
            proposed_payload=proposed_payload,
            payload_hash=_json_payload_hash(proposed_payload),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            idempotency_key=body.idempotency_key,
            version=1,
        )
        system_ctx.db.add(proposal)
        await system_ctx.db.commit()

    return AgentMoveResponse(
        proposal_id=str(proposal.id),
        status=proposal.status,
        approval_state="pending_human_approval",
        agent_id=str(agent.id),
        source_space_id=str(agent.space_id),
        destination_space_id=str(destination_space_id),
        current_space_id=str(agent.space_id),
        default_space_id=str(agent.home_space_id) if agent.home_space_id else None,
    )


@router.post("/agents/register-external", response_model=ExternalAgentRegisterResponse)
async def register_external_agent(
    request: ExternalAgentRegisterRequest,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Register an external webhook agent in one step (create + verify).

    This is the streamlined flow for Moltbot and similar external agents:
    1. Validates agent name (unique, valid format)
    2. Creates agent with origin: external_gateway
    3. Immediately triggers WebSub challenge-response verification
    4. Returns webhook_secret (shown ONCE - Stripe pattern)

    Requires authentication. Agent is created under the current user's account.
    """
    from app.services.webhook_dispatch_service import (
        verify_webhook,
        WebhookVerificationError,
    )

    try:
        owner_private_space_id = _resolve_default_private_space_id(session)

        # Check agent limits
        beta_config = get_beta_config()
        can_register, message = await beta_config.can_register_agent(session.db, session.user)
        if not can_register:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=message
            )

        # Validate agent name
        is_valid, error_message = validate_agent_name(request.name)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_message)

        # Check global name uniqueness
        result = await session.db.execute(
            select(Agent).where(func.lower(Agent.name) == request.name.lower())
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Agent name already taken. Please choose a different name."
            )

        # Create agent with external_gateway origin
        new_agent = Agent(
            id=uuid.uuid4(),
            user_id=session.user.id,
            owner_type="user",
            owner_user_id=session.user.id,
            owner_space_id=owner_private_space_id,
            home_space_id=owner_private_space_id,
            created_by_user_id=session.user.id,
            space_id=owner_private_space_id,
            pinned_to_space=owner_private_space_id,  # External agents start in the owner private space
            name=request.name,
            description=request.description,
            agent_type="external_gateway",  # Must match origin (AGENTS-001)
            status="active",
            visibility_level="org_visible",
            origin="external_gateway",
            webhook_url=request.webhook_url,
            webhook_verified=False,
            enabled_tools={"ax_mcp": True},  # Default MCP tools enabled
            capabilities={"sub_type": request.sub_type or "moltbot"},
        )

        session.db.add(new_agent)
        # Use flush() to get ID without committing - prevents race condition
        # where agent is visible before verification completes
        await session.db.flush()

        # Dual-write: create default agent_space_access row
        from app.core.agent_space import grant_space_access
        await grant_space_access(session.db, new_agent.id, owner_private_space_id, is_default=True)

        # Immediately attempt verification
        try:
            success = await verify_webhook(session.db, new_agent)

            if not success:
                # Verification failed - rollback (agent never committed)
                await session.db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Webhook verification failed - agent did not respond with correct challenge. Ensure your webhook is running and accessible."
                )

            # Verification succeeded - commit the transaction
            await session.db.commit()
            await session.db.refresh(new_agent)

            # Build gateway config for Clawdbot (copy-paste ready)
            agent_handle = f"@{new_agent.name}"
            gateway_config = f"AGENT_1={new_agent.id}|{new_agent.webhook_secret}|{agent_handle}|local"
            # Legacy env snippet for backwards compat
            env_snippet = f"AX_WEBHOOK_SECRET={new_agent.webhook_secret}\nAX_AGENT_ID={new_agent.id}\nAX_AGENT_HANDLE={agent_handle}"

            logger.info(f"External agent {new_agent.name} registered and verified for user {session.user.id}")

            return ExternalAgentRegisterResponse(
                agent_id=str(new_agent.id),
                agent_handle=agent_handle,
                webhook_secret=new_agent.webhook_secret,
                webhook_verified=True,
                gateway_config=gateway_config,
                env_snippet=env_snippet,
            )

        except WebhookVerificationError as e:
            # Verification error - rollback (agent never committed)
            await session.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Webhook verification failed: {e}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error registering external agent: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration error: {e!s}"
        )

# =============================================================================
# Webhook Token Refresh Endpoint (Handshake of Continuance)
# =============================================================================

class WebhookRefreshRequest(BaseModel):
    """Request to refresh an enclave token for external agents."""
    agent_id: str = Field(..., description="Agent UUID")
    timestamp: int = Field(..., description="Unix timestamp for replay protection")
    signature: str = Field(..., description="HMAC-SHA256(agent_id.timestamp, webhook_secret)")

class WebhookRefreshResponse(BaseModel):
    """Response with fresh enclave token."""
    access_token: str
    expires_in: int
    token_type: str = "Bearer"

@router.post("/webhooks/refresh", response_model=WebhookRefreshResponse)
async def refresh_webhook_token(
    request: WebhookRefreshRequest,
    session: SystemSession = Depends(get_system_session)
):
    """
    Refresh an enclave token for external webhook agents.

    External agents use this when their token is about to expire during
    long-running reasoning cycles. Uses HMAC signature with webhook_secret
    for authentication (same pattern as dispatch verification).

    Flow:
    1. Agent detects 401 or <2min TTL remaining
    2. Agent signs (agent_id.timestamp) with webhook_secret
    3. Backend verifies signature, returns fresh 15-min token
    """
    import hmac
    import hashlib
    import time

    from app.core.mcp_token_cache import mint_enclave_token

    try:
        agent_uuid = uuid.UUID(request.agent_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent_id format"
        )

    # Verify timestamp freshness for replay protection
    current_time = int(time.time())
    # Reject old timestamps (replay attack prevention)
    if current_time - request.timestamp > 300:  # 5 min max age
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Timestamp too old (max 5 min)"
        )
    # Reject future timestamps (with 60s clock drift allowance)
    if request.timestamp > current_time + 60:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Timestamp too far in future"
        )

    # Get agent
    result = await session.db.execute(
        select(Agent).where(Agent.id == agent_uuid)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found"
        )

    # Note: webhook_verified check removed - HMAC signature IS the verification
    # If agent has webhook_secret, that's sufficient for token refresh

    if not agent.webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent has no webhook secret"
        )

    if agent.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent is {agent.status}, not active"
        )

    # Verify HMAC signature
    sign_data = f"{request.agent_id}.{request.timestamp}"
    expected_sig = hmac.new(
        agent.webhook_secret.encode(),
        sign_data.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, request.signature):
        logger.warning(f"Webhook refresh signature mismatch for agent {agent.id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature"
        )

    # Mint fresh enclave token
    token_result = await mint_enclave_token(
        agent_id=str(agent.id),
        agent_name=agent.name,
        owner_id=str(agent.user_id),
        dispatch_id=f"refresh-{current_time}",
        pinned_to_space=str(agent.pinned_to_space) if agent.pinned_to_space else None,
    )

    if not token_result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to mint refresh token"
        )

    logger.info(f"WEBHOOK_TOKEN_REFRESH agent={agent.name} agent_id={agent.id}")

    return WebhookRefreshResponse(
        access_token=token_result["access_token"],
        expires_in=token_result["expires_in"],
    )

# =============================================================================
# Webhook Progress Endpoint (External Agent Status Updates)
# =============================================================================

class WebhookProgressRequest(BaseModel):
    """Progress update from external agent during processing."""
    dispatch_id: str = Field(..., description="Dispatch ID from the original payload")
    status: str = Field("processing", description="processing | completed | error")
    tool: str | None = Field(None, description="Current tool being used (e.g., bash, read_file, web_search)")
    skill: str | None = Field(None, description="Current skill being used")
    step: str | None = Field(None, description="Progress indicator (e.g., '2/5')")
    message: str | None = Field(None, max_length=500, description="Human-readable status message")
    intent: str | None = Field(None, max_length=500, description="Why the agent is taking this step")
    thinking: str | None = Field(None, max_length=500, description="Deprecated alias for intent")

    @field_validator("tool", "skill", "step", "message", "intent", "thinking", mode="before")
    @classmethod
    def _normalize_optional_progress_text(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

@router.post(
    "/webhooks/progress",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Report progress during external agent processing",
    description="""
    External agents can call this endpoint to report progress during processing.
    Progress updates are broadcast via SSE to the frontend for real-time display.

    **Authentication**: Use the `auth_token` from the dispatch payload.

    **Fire-and-forget**: Returns 202 immediately, no response body.
    """,
)
async def webhook_progress(
    request: WebhookProgressRequest,
    authorization: str = Header(..., description="Bearer {auth_token}"),
    session: SystemSession = Depends(get_system_session)
):
    """
    Receive progress updates from external agents and broadcast via SSE.

    Flow:
    1. Validate auth_token (enclave token from dispatch)
    2. Extract agent info from token
    3. Broadcast progress via SSE
    4. Return 202 immediately (fire-and-forget)
    """
    # Extract token
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header"
        )
    token = authorization[7:]  # Strip "Bearer "

    # Validate token (works for ANY token type - cloud, enclave, future)
    try:
        from app.core.mcp_token_cache import validate_token
        token_data = await validate_token(token)
        if not token_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
    except Exception as e:
        logger.warning(f"WEBHOOK_PROGRESS_AUTH_FAILED error={e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token validation failed"
        )

    agent_id = token_data.get("agent_id")
    agent_name = token_data.get("agent_name")
    space_id = token_data.get("space_id") or token_data.get("pinned_to_space")

    if not agent_id or not space_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims"
        )

    progress_intent = request.intent or request.thinking or request.message
    progress_summary = progress_intent or request.step or request.tool or request.status

    # Broadcast progress via SSE
    try:
        from app.services.redis_sse_broker import redis_sse_broker
        await redis_sse_broker.publish(
            space_id=str(space_id),
            event="agent_progress",
            data={
                "agent_id": agent_id,
                "agent_name": agent_name,
                "dispatch_id": request.dispatch_id,
                "status": request.status,
                "tool": request.tool,
                "skill": request.skill,
                "step": request.step,
                "message": request.message,
                "intent": progress_intent,
                "thinking": request.thinking or request.intent,
                "summary": progress_summary,
            }
        )
        logger.info(
            f"WEBHOOK_PROGRESS agent={agent_name} dispatch_id={request.dispatch_id} "
            f"tool={request.tool} status={request.status} intent={progress_intent}"
        )
    except Exception as e:
        # Best effort - don't fail the request if SSE broadcast fails
        logger.warning(f"WEBHOOK_PROGRESS_SSE_FAILED agent={agent_name} error={e}")

    # Fire-and-forget: return immediately
    return None

# =============================================================================
# Agent Statistics Endpoint
# =============================================================================

# Emoji list for reactions (matching frontend EmojiReactions.tsx)
REACTION_EMOJIS = ["👍", "👎", "🔥", "🚀", "💯"]

@router.get("/agents/{agent_id}/stats")
async def get_agent_stats(
    agent_id: str,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Get activity and reputation stats for an agent.

    Returns:
    - owner_username: Agent owner
    - messages_30d: Messages sent in last 30 days
    - tasks_created_30d: Tasks created in last 30 days
    - tasks_completed_30d: Tasks completed in last 30 days
    - reactions: Dict of emoji -> count
    """
    from datetime import timedelta

    # Convert agent_id to UUID
    try:
        agent_uuid = uuid.UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid agent ID format")

    # Verify agent exists
    result = await session.db.execute(select(Agent).options(selectinload(Agent.user)).where(Agent.id == agent_uuid))
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check ownership - only owner can view stats
    # (Stats may contain sensitive activity data across organizations)
    if str(agent.user_id) != str(session.user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only view stats for your own agents")

    # Calculate 30-day window
    thirty_days_ago = datetime.now(UTC) - timedelta(days=30)

    # Count messages sent by this agent in last 30 days
    messages_result = await session.db.execute(
        select(func.count(Message.id)).where(
            and_(
                Message.agent_id == agent_uuid,
                Message.created_at >= thirty_days_ago,
                Message.parent_id.is_(None),  # Exclude reactions (they have parent_id)
            )
        )
    )
    messages_count = messages_result.scalar() or 0

    # Count tasks CREATED by this agent in last 30 days
    tasks_created_result = await session.db.execute(
        select(func.count(Task.id)).where(
            and_(Task.posted_by_agent_id == agent_uuid, Task.created_at >= thirty_days_ago)
        )
    )
    tasks_created_count = tasks_created_result.scalar() or 0

    # Count tasks COMPLETED by this agent in last 30 days
    tasks_completed_result = await session.db.execute(
        select(func.count(Task.id)).where(
            and_(
                Task.assigned_agent_id == agent_uuid,
                Task.work_status == "completed",
                Task.completed_at >= thirty_days_ago,
            )
        )
    )
    tasks_completed_count = tasks_completed_result.scalar() or 0

    # Get emoji reactions on messages FROM this agent — single grouped query
    reactions_dict = {emoji: 0 for emoji in REACTION_EMOJIS}

    # Subquery: messages authored by this agent
    agent_msgs = select(Message.id).where(Message.agent_id == agent_uuid).subquery()

    # Single query: count reactions grouped by emoji
    reactions_result = await session.db.execute(
        select(Message.content, func.count(Message.id))
        .where(
            and_(
                Message.parent_id.in_(select(agent_msgs.c.id)),
                Message.message_type == "reaction",
                Message.content.in_(REACTION_EMOJIS),
            )
        )
        .group_by(Message.content)
    )
    for emoji, count in reactions_result:
        reactions_dict[emoji] = count

    # Get owner username
    owner_username = agent.user.username if agent.user else None

    return {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "owner_username": owner_username,
        "messages_30d": messages_count,
        "tasks_created_30d": tasks_created_count,
        "tasks_completed_30d": tasks_completed_count,
        "reactions": reactions_dict,
    }
