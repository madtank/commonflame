"""
Unified V1 API surface for the aX platform (API-V1-001).

All consumers (frontend, MCP server, agents) use these endpoints.
Auth: get_user_from_jwt_or_mcp on every endpoint.
Vocabulary: "space_id" everywhere (maps to space_id internally).

Prefix: /api/v1
"""

import base64
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import String, and_, case, cast, func as sa_func, or_, select, delete as sa_delete, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...core.ax_jwt import get_issuer
from ...core.agent_runtime import display_model
from ...core.database import get_db_session
from ...core.jwt_verify import get_user_from_jwt_or_mcp, _resolve_user_from_bearer_token
from ...core.rls import SecureSession, get_secure_session, set_rls_context, system_session_context
from ...models.agent import Agent
from ...models.agent_relationship import AgentRelationship
from ...models.agent_space_access import AgentSpaceAccess
from ...models.credential_fingerprint import CredentialFingerprint
from ...models.guardrail_violation import GuardrailViolation
from ...models.message import Message
from ...models.space import Space
from ...models.space_invite_code import SpaceInviteCode
from ...models.space_membership import SpaceMembership
from ...models.task import Task
from ...models.user import User
from ...core.agent_resolver import resolve_agent as _resolve_agent, _effective_space_id
from ...core.agent_constraints import validate_agent_name
from ...core.agent_toggles import (
    build_enabled_tools_from_agent,
    get_enabled_tools_defaults,
    get_legacy_column_updates_from_enabled_tools,
    validate_enabled_tools_update,
)
from ...core.agent_space import grant_space_access
from ...core.agent_management_auth import extract_management_actor
from ...core.container_registry import get_template
from ...core.models_config import validate_model_for_user_role
from ...core.authorization import verify_space_actor_access, verify_space_membership
from ...core.context_payload import ContextPayloadTooLarge, ensure_context_inline_size
from ...core.api_action_registry import (
    ResolvedRouteAction,
    declare_route_action,
)
from ...core.config import get_settings
from ...core.credential_service import VALID_SCOPES
from ...constants import CONTEXT_DEFAULT_TTL, CONTEXT_MAX_TTL
from ...services.space_agent_service import ensure_space_agent_for_org
from ...services.widget_cache import widget_cache_key
from ...services.redis_sse_broker import redis_sse_broker
from ...services.conversation_card_service import serialize_card as _serialize_conversation_card
from ...services.message_visibility import exclude_ui_only_no_reply_clause, is_ui_only_no_reply_metadata
from ...services.agent_control_service import AgentControlService, AgentControlState
from ...services.workspace_intelligence_service import WorkspaceIntelligenceService
from ...models.agent_management import AgentManagementApproval, AgentManagementProposal
from .messages import WidgetResolveRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["v1"])

# Business priority rank (lower number = more urgent). Keep owned-work roster
# ordering aligned with the internal task dispatch views instead of relying on
# lexicographic string sorting of values like urgent/medium/low/high/critical.
TASK_PRIORITY_RANK = case(
    (Task.priority == "critical", 1),
    (Task.priority == "urgent", 2),
    (Task.priority == "high", 3),
    (Task.priority == "medium", 4),
    (Task.priority == "low", 5),
    else_=6,
)

# ---------------------------------------------------------------------------
# Redis client (for context + agent memory)
# ---------------------------------------------------------------------------

_redis = aioredis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6380/0"),
    decode_responses=True,
)
agent_control_service = AgentControlService(_redis)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _authenticated_agent_id(user: User, session: SecureSession) -> Optional[str]:
    """Return the authoring agent only for true agent-principal sessions.

    User sessions may carry agent targeting metadata for management/UI flows.
    That metadata is a resource target, not an authorship identity. Only
    backend-issued agent JWTs or agent-access exchange JWTs may author messages,
    reactions, tasks, processing signals, or tool-call records as an agent.
    """
    if getattr(session, "principal_type", None) != "agent":
        return None
    return getattr(user, "_agent_id", None) or session.agent_id


def _authenticated_agent_name(user: User, session: SecureSession) -> Optional[str]:
    if getattr(session, "principal_type", None) != "agent":
        return None
    return getattr(user, "_agent_name", None) or session.agent_name


async def _resolve_space_id(
    user: User,
    space_id: Optional[str],
    db: AsyncSession,
    session: SecureSession | None = None,
) -> str:
    """Resolve space_id from query param or fall back to auth context.

    When an explicit *space_id* is supplied by the caller we verify that
    the authenticated user is actually a member of that space before
    trusting the value.
    """
    if space_id:
        try:
            resolved = str(uuid.UUID(str(space_id)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid space_id format") from exc
        if session is not None and getattr(session, "is_agent", False):
            effective_space_id = str(
                getattr(user, "_effective_space_id", None)
                or getattr(session, "space_id", None)
                or _effective_space_id(user)
            )
            if resolved != effective_space_id:
                raise HTTPException(
                    status_code=403,
                    detail="Agent normal requests must use the authenticated agent's effective space",
                )
        if session is not None:
            await verify_space_actor_access(
                db,
                user_id=user.id,
                space_id=resolved,
                is_agent=session.is_agent,
                agent_id=session.agent_id,
            )
        else:
            await verify_space_membership(db, user.id, resolved)
        return resolved
    return _effective_space_id(user)


async def _resolve_write_space_id(
    user: User,
    session: SecureSession,
    requested_space_id: Optional[str],
    db: AsyncSession,
) -> str:
    """Resolve the only valid write space for this request.

    When the request is acting through a targeted agent surface, writes are
    bound to that agent's effective space. Caller-provided space overrides must
    not expand that boundary.
    """
    acting_agent_id = _authenticated_agent_id(user, session)

    if acting_agent_id:
        effective_space_id = str(
            getattr(user, "_effective_space_id", None)
            or getattr(session, "space_id", None)
            or _effective_space_id(user)
        )
        if requested_space_id and str(requested_space_id) != effective_space_id:
            raise HTTPException(
                status_code=403,
                detail="Agent-targeted writes must use the targeted agent's effective space",
            )
        return effective_space_id

    return await _resolve_space_id(user, requested_space_id, db, session=session)


async def _resolve_parent_reply_space_id(
    user: User,
    session: SecureSession,
    *,
    parent_id: Optional[str],
    channel: str,
    requested_space_id: Optional[str],
    db: AsyncSession,
) -> Optional[str]:
    """Return the parent message's space for replies, after access checks.

    Reply routing is anchored to the thread, not to a user's sticky
    current_space_id or an agent's possibly stale runtime space. This keeps
    multi-tab and moved-agent replies landing in the same space as the parent.
    """
    if not parent_id:
        return None

    try:
        parent_uuid = uuid.UUID(str(parent_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid parent message ID format") from exc

    async with system_session_context() as system_session:
        result = await system_session.db.execute(
            select(Message.space_id, Message.channel).where(Message.id == parent_uuid)
        )
        parent_row = result.one_or_none()

    if not parent_row:
        raise HTTPException(status_code=404, detail="Parent message not found")

    parent_space_id, parent_channel = parent_row
    normalized_parent_space_id = str(parent_space_id)
    normalized_parent_channel = parent_channel or "main"
    normalized_reply_channel = channel or "main"

    if normalized_parent_channel != normalized_reply_channel:
        raise HTTPException(
            status_code=400,
            detail=(
                "Reply channel must match parent message channel "
                f"({normalized_parent_channel})"
            ),
        )

    if requested_space_id:
        try:
            normalized_requested_space_id = str(uuid.UUID(str(requested_space_id)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid space_id format") from exc
        if normalized_requested_space_id != normalized_parent_space_id:
            raise HTTPException(
                status_code=400,
                detail="Reply space_id must match parent message space_id",
            )

    acting_agent_id = _authenticated_agent_id(user, session)
    await set_rls_context(
        db,
        user_id=str(user.id),
        space_id=normalized_parent_space_id,
        agent_id=acting_agent_id,
    )
    await verify_space_actor_access(
        db,
        user_id=user.id,
        space_id=normalized_parent_space_id,
        is_agent=bool(acting_agent_id),
        agent_id=acting_agent_id,
    )
    return normalized_parent_space_id


async def _proxy_widget_resource_from_mcp(
    *,
    resource_uri: str,
    authorization: str | None,
) -> dict | None:
    """Resolve a ui:// resource through the MCP server using API-side proxying.

    This keeps the frontend on the API boundary while still returning the real
    MCP App HTML when available.
    """
    if not resource_uri.startswith("ui://") or not authorization:
        return None

    mcp_server_url = get_settings().mcp_server_url.rstrip("/")
    mcp_url = f"{mcp_server_url}/mcp"
    body = {
        "jsonrpc": "2.0",
        "id": f"widget-read-{uuid.uuid4()}",
        "method": "resources/read",
        "params": {
            "uri": resource_uri,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.post(
                mcp_url,
                json=body,
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
    except Exception as error:
        logger.warning("WIDGET_PROXY_MCP_ERROR uri=%s error=%s", resource_uri, error)
        return None

    if response.status_code != 200:
        logger.warning(
            "WIDGET_PROXY_MCP_HTTP_ERROR uri=%s status=%s",
            resource_uri,
            response.status_code,
        )
        return None

    try:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            payload = None
            for line in reversed(response.text.strip().splitlines()):
                if line.startswith("data: "):
                    try:
                        payload = json.loads(line[6:])
                        break
                    except json.JSONDecodeError:
                        continue
            if payload is None:
                raise ValueError("No JSON-RPC payload found in SSE response")
        else:
            payload = response.json()
    except Exception:
        logger.warning("WIDGET_PROXY_MCP_PARSE_ERROR uri=%s", resource_uri)
        return None

    result = payload.get("result") or {}
    contents = result.get("contents") or []
    primary = next(
        (
            item for item in contents
            if isinstance(item, dict)
            and (
                isinstance(item.get("text"), str)
                or isinstance(item.get("blob"), str)
                or isinstance(item.get("uri"), str)
            )
        ),
        None,
    )
    if not isinstance(primary, dict):
        return None

    mime_type = (
        primary.get("mimeType")
        or primary.get("mime_type")
        or "text/html;profile=mcp-app"
    )

    html_value = primary.get("text")
    if not isinstance(html_value, str):
        html_value = None

    blob_value = primary.get("blob")
    if not html_value and isinstance(blob_value, str):
        try:
            html_value = base64.b64decode(blob_value).decode("utf-8")
        except Exception:
            html_value = None

    resource_url = primary.get("uri")
    if not isinstance(resource_url, str):
        resource_url = None

    if not html_value and not resource_url:
        return None

    return {
        "html": html_value,
        "resource_url": resource_url,
        "resource_mime_type": mime_type,
        "title": resource_uri.replace("ui://", ""),
        "csp": None,
    }


def _has_ambiguous_widget_resource(
    root_widget_meta: dict | None,
    *,
    resource_uri: str,
) -> bool:
    widgets = root_widget_meta.get("widgets") if isinstance(root_widget_meta, dict) else None
    if not isinstance(widgets, list):
        return False

    matches = 0
    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        if widget.get("resource_uri") != resource_uri:
            continue
        matches += 1
        if matches > 1:
            return True

    return False


async def _find_agent_by_name_or_id(name_or_id: str, db: AsyncSession) -> Optional[Agent]:
    """Find an agent by name or UUID."""
    result = await db.execute(select(Agent).where(Agent.name == name_or_id))
    agent = result.scalar_one_or_none()
    if agent:
        return agent
    try:
        agent_uuid = uuid.UUID(name_or_id)
        result = await db.execute(select(Agent).where(Agent.id == agent_uuid))
        return result.scalar_one_or_none()
    except ValueError:
        return None


_WIDGET_BLOB_KEYS = {"structured_content", "fallback_text", "structuredContent"}


def _strip_widget_blobs(metadata: dict) -> dict:
    """Remove heavy rendering blobs from widget metadata.

    Keeps widget pointer fields plus lightweight first-paint fields like
    `initial_data`, and strips heavyweight rendering blobs
    (`structured_content` / `fallback_text`) that the frontend does not need
    from the messages API.
    """
    ui = metadata.get("ui")
    if not ui or not isinstance(ui, dict):
        return metadata
    widget = ui.get("widget")
    if not widget or not isinstance(widget, dict):
        return metadata
    if not _WIDGET_BLOB_KEYS.intersection(widget):
        return metadata
    # Shallow copy to avoid mutating the DB object
    metadata = {**metadata}
    metadata["ui"] = {**ui}
    metadata["ui"]["widget"] = {
        k: v for k, v in widget.items() if k not in _WIDGET_BLOB_KEYS
    }
    return metadata


async def _serialize_message(msg: Message, db: AsyncSession) -> dict:
    """Build the canonical message response shape per API-V1-001 spec."""
    sender_type = "agent" if msg.agent_id else "human"

    # Compute display_name
    display_name: Optional[str] = None
    if msg.agent_id:
        # Try eager-loaded relationship first
        agent = getattr(msg, "agent", None)
        if agent is None:
            result = await db.execute(select(Agent).where(Agent.id == msg.agent_id))
            agent = result.scalar_one_or_none()
        display_name = agent.name if agent else None
    else:
        user = getattr(msg, "user", None)
        if user is None:
            result = await db.execute(select(User).where(User.id == msg.user_id))
            user = result.scalar_one_or_none()
        display_name = (user.username or user.full_name) if user else None

    # conversation_id = root of thread
    conversation_id = str(msg.parent_id) if msg.parent_id else str(msg.id)

    # Strip heavy blobs from widget metadata — messages carry thin pointers
    # only (tool_call_id, resource_uri, tool_name, lifecycle).
    # Frontend fetches widget data directly from the MCP server.
    metadata = msg.message_metadata
    if metadata:
        metadata = _strip_widget_blobs(metadata)

    return {
        "id": str(msg.id),
        "content": msg.content,
        "space_id": str(msg.space_id),
        "channel": msg.channel or "main",
        "sender_id": str(msg.user_id) if msg.user_id else None,
        "sender_type": sender_type,
        "display_name": display_name,
        "agent_id": str(msg.agent_id) if msg.agent_id else None,
        "parent_id": str(msg.parent_id) if msg.parent_id else None,
        "conversation_id": conversation_id,
        "message_type": msg.message_type or "text",
        "ai_summary": msg.ai_summary,
        "metadata": metadata,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
        "updated_at": msg.updated_at.isoformat() if msg.updated_at else None,
    }


def _memory_key(agent_id: str) -> str:
    """Redis hash key for agent memory."""
    return f"agent:memory:{agent_id}"


# ---------------------------------------------------------------------------
# Pydantic request schemas
# ---------------------------------------------------------------------------

class MessageSendBody(BaseModel):
    content: str
    space_id: Optional[str] = None
    channel: str = "main"
    parent_id: Optional[str] = None
    message_type: str = "text"
    metadata: Optional[dict[str, Any]] = None
    attachments: Optional[list[dict[str, Any]]] = None


class MessageEditBody(BaseModel):
    content: str


class ReactionBody(BaseModel):
    emoji: str


class AgentProcessingProgress(BaseModel):
    current: int
    total: int
    unit: str


class AgentProcessingStatusBody(BaseModel):
    message_id: str
    status: str = "completed"  # processing | completed | error | accepted | thinking | tool_call | tool_complete | streaming | working
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    activity: Optional[str] = None
    progress: Optional[AgentProcessingProgress] = None
    detail: Optional[dict[str, Any]] = None


def _is_deliberate_no_reply_status(body: AgentProcessingStatusBody) -> bool:
    """True for adapter/listener no-reply suppression status, not prose."""
    status_value = str(body.status or "").strip().lower().replace("-", "_")
    detail = body.detail if isinstance(body.detail, dict) else {}
    reason_candidates = [
        detail.get("reason_code"),
        detail.get("signal_kind"),
        detail.get("reason"),
        detail.get("safe_word"),
    ]
    normalized_reasons = {
        str(value).strip().lower().replace("-", "_").replace(" ", "_")
        for value in reason_candidates
        if value is not None
    }
    return (
        status_value in {"skipped", "no_reply"}
        and bool(normalized_reasons & {"no_reply", "no_reply_requested"})
    )


class ContextSetBody(BaseModel):
    key: str
    value: str
    space_id: Optional[str] = None
    ttl: Optional[int] = None
    topic: Optional[str] = None


class SearchMessagesBody(BaseModel):
    query: str
    space_id: Optional[str] = None
    limit: int = 20


class AgentUpdateBody(BaseModel):
    bio: Optional[str] = None
    specialization: Optional[str] = None
    capabilities: Optional[list[str]] = None
    # Self-service directory icon. Rendered as an <img src> in the frontend, so the
    # scheme is restricted to http(s) URLs or inline data:image/ URIs (an empty
    # string clears it). Capped at 512 to match the agents.avatar_url VARCHAR(512)
    # column — fits URLs and small inline SVG icons without a migration. Larger
    # inline icons need a column-widening migration (see PR notes). (Task b7f73029.)
    avatar_url: Optional[str] = Field(None, max_length=512)

    @field_validator("avatar_url")
    @classmethod
    def _validate_avatar_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return v
        allowed = ("https://", "http://", "data:image/")
        if not v.startswith(allowed):
            raise ValueError("avatar_url must be an http(s) URL or a data:image/ URI")
        return v


class AgentProfileUpdateBody(AgentUpdateBody):
    description: Optional[str] = None
    model: Optional[str] = None
    status: Optional[str] = None
    enabled_tools: Optional[dict[str, bool]] = None

class MemoryStoreBody(BaseModel):
    key: str
    value: str


class FollowBody(BaseModel):
    target_agent: str
    relationship_type: str = "follow"


class CredentialFingerprintCreate(BaseModel):
    agent_id: uuid.UUID
    token_sha256: str = Field(..., min_length=64, max_length=64)
    host_binding: str = Field(..., min_length=1, max_length=255)


class CredentialFingerprintResponse(BaseModel):
    id: str
    agent_id: str
    token_sha256: str
    host_binding: str
    created_at: str | None


class CredentialViolationResponse(BaseModel):
    violation_id: str
    agent_id: str | None
    violation_type: str
    severity: str
    description: str
    endpoint: str
    method: str
    created_at: str | None
    credential_fingerprint_id: str | None = None
    token_sha256: str | None = None
    host_binding: str | None = None


class ToolCallNotification(BaseModel):
    tool_name: str
    tool_call_id: str
    space_id: Optional[str] = None
    tool_action: Optional[str] = None
    resource_uri: Optional[str] = None
    arguments_hash: Optional[str] = None
    kind: Optional[str] = None
    arguments: Optional[dict] = None
    initial_data: Optional[dict] = None
    status: str = "success"
    duration_ms: Optional[int] = None
    agent_name: Optional[str] = None
    agent_id: Optional[str] = None
    message_id: Optional[str] = None
    correlation_id: Optional[str] = None


# =========================================================================
# 1. MESSAGES (7 endpoints)
# =========================================================================

@router.post("/credentials/fingerprint", status_code=201, response_model=CredentialFingerprintResponse)
async def create_credential_fingerprint(
    body: CredentialFingerprintCreate,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    await verify_space_membership(session.db, user.id, str(_effective_space_id(user)))

    agent_result = await session.db.execute(
        select(Agent).where(
            Agent.id == body.agent_id,
            Agent.space_id == uuid.UUID(str(_effective_space_id(user))),
        )
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found in current space")

    fingerprint = CredentialFingerprint(
        agent_id=body.agent_id,
        token_sha256=body.token_sha256.lower(),
        host_binding=body.host_binding,
    )
    add_result = session.db.add(fingerprint)
    if hasattr(add_result, "__await__"):
        await add_result
    await session.db.commit()
    await session.db.refresh(fingerprint)

    return CredentialFingerprintResponse(
        id=str(fingerprint.id),
        agent_id=str(fingerprint.agent_id),
        token_sha256=fingerprint.token_sha256,
        host_binding=fingerprint.host_binding,
        created_at=fingerprint.created_at.isoformat() if fingerprint.created_at else None,
    )


@router.get("/credentials/violations", response_model=list[CredentialViolationResponse])
async def list_credential_violations(
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    effective_space_id = uuid.UUID(str(_effective_space_id(user)))
    await verify_space_membership(session.db, user.id, str(effective_space_id))

    result = await session.db.execute(
        select(GuardrailViolation, CredentialFingerprint)
        .outerjoin(
            CredentialFingerprint,
            and_(
                GuardrailViolation.agent_id == CredentialFingerprint.agent_id,
                GuardrailViolation.user_agent == CredentialFingerprint.host_binding,
            ),
        )
        .where(GuardrailViolation.space_id == effective_space_id)
        .order_by(GuardrailViolation.created_at.desc())
    )

    rows = result.all()
    return [
        CredentialViolationResponse(
            violation_id=str(violation.id),
            agent_id=str(violation.agent_id) if violation.agent_id else None,
            violation_type=violation.violation_type,
            severity=violation.severity,
            description=violation.description,
            endpoint=violation.endpoint,
            method=violation.method,
            created_at=violation.created_at.isoformat() if violation.created_at else None,
            credential_fingerprint_id=str(fingerprint.id) if fingerprint else None,
            token_sha256=fingerprint.token_sha256 if fingerprint else None,
            host_binding=fingerprint.host_binding if fingerprint else None,
        )
        for violation, fingerprint in rows
    ]


@router.get("/messages")
async def list_messages(
    space_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    before: Optional[str] = Query(None),
    after: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List messages in a space."""
    sid = await _resolve_space_id(user, space_id, session.db, session=session)

    query = (
        select(Message)
        .options(selectinload(Message.agent), selectinload(Message.user))
        .where(Message.space_id == sid)
        .where(Message.message_type != "reaction")
        .where(exclude_ui_only_no_reply_clause())
    )

    if channel:
        query = query.where(Message.channel == channel)

    if before:
        try:
            before_uuid = uuid.UUID(before)
            # Get the cursor position / created_at of the reference message
            ref = await session.db.execute(
                select(Message.created_at).where(Message.id == before_uuid)
            )
            ref_row = ref.scalar_one_or_none()
            if ref_row:
                query = query.where(Message.created_at < ref_row)
        except ValueError:
            pass

    if after:
        try:
            after_uuid = uuid.UUID(after)
            ref = await session.db.execute(
                select(Message.created_at).where(Message.id == after_uuid)
            )
            ref_row = ref.scalar_one_or_none()
            if ref_row:
                query = query.where(Message.created_at > ref_row)
        except ValueError:
            pass

    query = query.order_by(Message.created_at.desc()).limit(limit + 1)
    result = await session.db.execute(query)
    messages = list(result.scalars().all())

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    serialized = [await _serialize_message(msg, session.db) for msg in messages]

    return {
        "messages": serialized,
        "count": len(serialized),
        "has_more": has_more,
    }


@router.post("/messages")
async def send_message(
    request: Request,
    body: MessageSendBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Send a message to a space.

    Uses MessagesService.send() to ensure full routing + dispatch pipeline
    (Space Agent default routing, @mention dispatch, SSE broadcast, etc).
    """
    from ...core.actor import CAP_MESSAGES_SEND, Actor
    from ...services.messages_service import MessagesService

    sid = await _resolve_parent_reply_space_id(
        user,
        session,
        parent_id=body.parent_id,
        channel=body.channel,
        requested_space_id=body.space_id,
        db=session.db,
    ) or await _resolve_write_space_id(user, session, body.space_id, session.db)
    await set_rls_context(
        session.db,
        user_id=str(user.id),
        space_id=sid,
        agent_id=getattr(session, "agent_id", None),
    )

    # Determine sender identity. Agent-targeting headers on user sessions are
    # resource selectors, not authorship credentials.
    agent_id = _authenticated_agent_id(user, session)
    is_agent = bool(agent_id)

    # DEFENSE-IN-DEPTH: Audit when a programmatic client sends without agent identity.
    # This is a guardrail — the real fix is agents having their own RS256 JWT.
    # We log but do NOT block, because blocking masks the upstream token issue.
    if not agent_id:
        token_issuer = getattr(user, "_token_issuer", None)
        user_agent = request.headers.get("user-agent", "")
        is_programmatic = (
            token_issuer == get_issuer()
            or "python-httpx" in user_agent
            or "python-requests" in user_agent
        )
        if is_programmatic:
            logger.warning(
                "AGENT_IDENTITY_AUDIT user_id=%s token_issuer=%s ua=%s "
                "path=%s — programmatic client without agent identity, "
                "message will post as human. Agent should use RS256 JWT.",
                user.id, token_issuer, user_agent[:50], request.url.path,
            )

    # Build Actor for the service layer
    if is_agent and agent_id:
        actor = Actor(
            id=uuid.UUID(agent_id), type="agent", space_id=uuid.UUID(sid),
            capabilities={CAP_MESSAGES_SEND},
        )
        display_name = None
        r = await session.db.execute(select(Agent.name).where(Agent.id == agent_id))
        display_name = r.scalar() or "Agent"
    else:
        actor = Actor(
            id=user.id, type="human", space_id=uuid.UUID(sid),
            capabilities={CAP_MESSAGES_SEND},
        )
        display_name = user.username or user.full_name or "User"

    svc = MessagesService(
        db=session.db,
        redis_client=_redis,
        sse_broker=redis_sse_broker,
    )
    message_metadata: dict[str, Any] = dict(body.metadata or {})
    if body.attachments:
        message_metadata.setdefault("attachments", body.attachments)
        message_metadata.setdefault("accepted_attachments", body.attachments)

    try:
        msg = await svc.send(
            actor=actor,
            content=body.content,
            channel=body.channel,
            message_type=body.message_type,
            parent_id=body.parent_id,
            metadata=message_metadata or None,
            author_display_name=display_name,
            adapter="mcp" if is_agent else "api",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    accepted_attachments = []
    raw_accepted = message_metadata.get("accepted_attachments")
    if isinstance(raw_accepted, list):
        accepted_attachments = [
            attachment for attachment in raw_accepted if isinstance(attachment, dict)
        ]
    if accepted_attachments:
        try:
            from app.api.v1.attachment_linking import link_user_uploads_to_message

            updated = await link_user_uploads_to_message(
                session.db,
                accepted_attachments=accepted_attachments,
                upload_owner_id=user.id,
                space_id=uuid.UUID(sid),
                message_id=msg.id,
                logger=logger,
            )
            if updated:
                await session.db.commit()
        except Exception as exc:
            logger.warning("Failed to link attachments to message %s: %s", msg.id, exc)

    # Build API-V1-001 response shape
    sender_type = "agent" if msg.agent_id else "human"
    resp_display_name: Optional[str] = None
    if msg.agent_id:
        r = await session.db.execute(select(Agent.name).where(Agent.id == msg.agent_id))
        resp_display_name = r.scalar() or None
    else:
        resp_display_name = user.username or user.full_name

    conversation_id = str(msg.parent_id) if msg.parent_id else str(msg.id)
    response_metadata = getattr(msg, "message_metadata", None)

    return {
        "message": {
            "id": str(msg.id),
            "content": msg.content,
            "space_id": sid,
            "sender_id": str(msg.agent_id) if msg.agent_id else str(user.id),
            "sender_type": sender_type,
            "display_name": resp_display_name,
            "parent_id": str(msg.parent_id) if msg.parent_id else None,
            "conversation_id": conversation_id,
            "message_type": msg.message_type,
            "metadata": response_metadata,
            "accepted_attachments": accepted_attachments,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "updated_at": msg.updated_at.isoformat() if getattr(msg, "updated_at", None) else None,
        }
    }


@router.get("/messages/{message_id}")
async def get_message(
    message_id: str,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Get a single message by ID."""
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    result = await session.db.execute(
        select(Message)
        .options(selectinload(Message.agent), selectinload(Message.user))
        .where(Message.id == msg_uuid)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if is_ui_only_no_reply_metadata(msg.message_type, getattr(msg, "message_metadata", None)):
        raise HTTPException(status_code=404, detail="Message not found")

    return {"message": await _serialize_message(msg, session.db)}


@router.patch("/messages/{message_id}")
async def edit_message(
    message_id: str,
    body: MessageEditBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Edit a message (author only)."""
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    result = await session.db.execute(
        select(Message)
        .options(selectinload(Message.agent), selectinload(Message.user))
        .where(Message.id == msg_uuid)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    agent_id = _authenticated_agent_id(user, session)
    is_agent = bool(agent_id)
    if is_agent:
        if str(msg.agent_id) != str(agent_id):
            raise HTTPException(status_code=403, detail="Not the message author")
    elif msg.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not the message author")

    msg.content = body.content
    msg.updated_at = datetime.now(timezone.utc)
    await session.db.commit()

    # SSE broadcast edit — include "field": "content" so the frontend
    # handleMessageUpdate handler calls refreshTranscript().
    # Without the field key, the handler ignores the event.
    try:
        serialized = await _serialize_message(msg, session.db)
        await redis_sse_broker.publish(
            space_id=str(msg.space_id),
            event="message_updated",
            data={
                **serialized,
                "field": "content",
                "message_id": str(msg.id),
            },
        )
    except Exception as e:
        logger.warning(f"SSE broadcast failed (non-critical): {e}")

    return {"message": await _serialize_message(msg, session.db)}


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: str,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Delete a message (author only)."""
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    result = await session.db.execute(
        select(Message).where(Message.id == msg_uuid)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    # Authorization: can this token delete this message?
    #
    # Rules:
    #   1. Message author (user or agent) can always delete their own message
    #   2. Space owner/admin with an unbound token can delete any message in
    #      spaces they own or admin — NOT in spaces they're just a member of
    #   3. Agent-scoped tokens can only delete that agent's messages
    #
    # This protects team and community spaces: being a member doesn't grant
    # delete-others. Only space owners/admins with the most powerful token
    # scope can moderate.
    agent_id = _authenticated_agent_id(user, session)
    is_agent = bool(agent_id)
    credential_scope = getattr(user, "_credential_agent_scope", None)
    allowed_agents = getattr(user, "_credential_allowed_agent_ids", None)
    is_unbound = credential_scope in (None, "unbound") and allowed_agents is None

    is_user_author = msg.user_id is not None and msg.user_id == user.id
    is_agent_author = is_agent and msg.agent_id is not None and str(msg.agent_id) == str(agent_id)

    # Space owner/admin check for unbound tokens
    is_space_moderator = False
    if is_unbound and not (is_user_author or is_agent_author):
        membership = (await session.db.execute(
            select(SpaceMembership).where(
                SpaceMembership.user_id == user.id,
                SpaceMembership.space_id == msg.space_id,
                SpaceMembership.role.in_(["admin", "owner"]),
            )
        )).scalar_one_or_none()
        if membership:
            is_space_moderator = True

    if not (is_user_author or is_agent_author or is_space_moderator):
        raise HTTPException(status_code=403, detail="Not the message author")

    space_id = str(msg.space_id)
    await session.db.delete(msg)
    await session.db.commit()

    # SSE broadcast delete
    try:
        await redis_sse_broker.publish(
            space_id=space_id,
            event="message_deleted",
            data={"id": str(msg_uuid)},
        )
    except Exception as e:
        logger.warning(f"SSE broadcast failed (non-critical): {e}")

    return {"ok": True, "id": str(msg_uuid)}


@router.post("/messages/{message_id}/reactions")
async def add_reaction(
    message_id: str,
    body: ReactionBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Add an emoji reaction to a message."""
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    result = await session.db.execute(
        select(Message).where(Message.id == msg_uuid)
    )
    target_msg = result.scalar_one_or_none()
    if not target_msg:
        raise HTTPException(status_code=404, detail="Message not found")

    space_id = str(target_msg.space_id)
    agent_id = _authenticated_agent_id(user, session)

    reaction = Message(
        id=uuid.uuid4(),
        content=body.emoji,
        space_id=space_id,
        user_id=None if agent_id else user.id,
        agent_id=agent_id,
        parent_id=msg_uuid,
        message_type="reaction",
        created_at=datetime.now(timezone.utc),
    )
    session.db.add(reaction)
    await session.db.commit()

    # SSE broadcast so reactions appear in real-time
    try:
        await redis_sse_broker.publish(
            space_id=space_id,
            event="reaction_added",
            data={
                "id": str(reaction.id),
                "message_id": str(msg_uuid),
                "emoji": body.emoji,
                "user_id": str(user.id),
                "agent_id": str(agent_id) if agent_id else None,
                "created_at": reaction.created_at.isoformat(),
            },
        )
    except Exception as e:
        logger.warning(f"SSE broadcast for reaction failed: {e}")

    return {"ok": True, "message_id": str(msg_uuid), "emoji": body.emoji}


@router.get("/messages/{message_id}/replies")
async def list_replies(
    message_id: str,
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List threaded replies to a message."""
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    result = await session.db.execute(
        select(Message)
        .options(selectinload(Message.agent), selectinload(Message.user))
        .where(
            and_(
                Message.parent_id == msg_uuid,
                Message.message_type != "reaction",
                exclude_ui_only_no_reply_clause(),
            )
        )
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    replies = result.scalars().all()

    serialized = [await _serialize_message(r, session.db) for r in replies]

    return {
        "messages": serialized,
        "count": len(serialized),
        "parent_id": str(msg_uuid),
    }


@router.post("/messages/widgets/resolve")
async def resolve_widget(
    request: Request,
    body: WidgetResolveRequest,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Resolve a widget resource_uri to renderable HTML.

    Security: RLS ensures user can only access messages in their space.
    Caching: Resolved widgets are cached by (message_id, resource_uri) for 5 min
    to handle chat replay bursts without hammering the DB or MCP proxy.
    """
    from ...services.widget_resolver import select_widget_meta, widget_resolver

    def attach_widget_hydration_fields(
        resolved_payload: dict,
        widget_meta: dict | None,
    ) -> dict:
        """Attach lightweight first-paint data from message metadata.

        Transcript surfaces can be thin or stale while SSE/refetch races settle.
        The resolve endpoint already selects the authoritative widget metadata
        from the persisted message, so include the small hydration fields the
        iframe host needs instead of forcing the browser to replay agent tools.
        """
        if not isinstance(widget_meta, dict):
            return resolved_payload
        hydrated = dict(resolved_payload)
        for key in (
            "tool_call_id",
            "tool_name",
            "tool_action",
            "arguments",
            "initial_data",
            "result_kind",
        ):
            value = widget_meta.get(key)
            if value is not None and hydrated.get(key) is None:
                hydrated[key] = value
        if (
            widget_meta.get("initial_data") is not None
            and hydrated.get("tool_result") is None
        ):
            hydrated["tool_result"] = widget_meta["initial_data"]
        return hydrated

    # Check server-side cache first (keyed on message + resource, space-scoped)
    cache_key = widget_cache_key(
        message_id=body.message_id,
        resource_uri=body.resource_uri,
        space_id=body.space_id,
        tool_call_id=body.tool_call_id,
    )
    cached = await _redis.get(cache_key)
    if cached:
        return json.loads(cached)

    # Load the message (RLS enforces space boundary)
    msg_uuid = uuid.UUID(body.message_id)
    result = await session.db.execute(
        select(Message).where(
            and_(Message.id == msg_uuid, Message.space_id == uuid.UUID(body.space_id))
        )
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    meta = msg.message_metadata or {}
    root_widget_meta = (meta.get("ui") or {}).get("widget")
    widget_meta = select_widget_meta(
        root_widget_meta,
        resource_uri=body.resource_uri,
        tool_call_id=body.tool_call_id,
    )

    resolved = widget_resolver.resolve(
        resource_uri=body.resource_uri,
        widget_meta=widget_meta,
        message_meta=meta,
    )

    authorization = request.headers.get("Authorization")
    if not authorization:
        access_cookie = request.cookies.get("access_token")
        if access_cookie:
            authorization = f"Bearer {access_cookie}"

    proxied = None
    if not _has_ambiguous_widget_resource(
        root_widget_meta,
        resource_uri=body.resource_uri,
    ):
        proxied = await _proxy_widget_resource_from_mcp(
            resource_uri=body.resource_uri,
            authorization=authorization,
        )
    if proxied:
        proxied = attach_widget_hydration_fields(proxied, widget_meta)
        # Cache proxied result for 5 minutes
        await _redis.setex(cache_key, 300, json.dumps(proxied))
        return proxied

    if not resolved:
        raise HTTPException(status_code=404, detail="Unknown widget resource_uri")

    resolved = attach_widget_hydration_fields(resolved, widget_meta)

    # Cache resolved result for 5 minutes
    try:
        await _redis.setex(cache_key, 300, json.dumps(resolved))
    except (TypeError, ValueError):
        pass  # Skip caching if result isn't JSON-serializable

    return resolved


# =========================================================================
# 2. SPACES (3 endpoints)
# =========================================================================

class RecentSpaceAgentEntry(BaseModel):
    id: str
    name: str
    display_name: str
    avatar_url: str | None = None
    last_message_at: datetime | None = None
    is_online: bool = False


class RecentSpaceAgentsResponse(BaseModel):
    agents: list[RecentSpaceAgentEntry]
    cached: bool = False

@router.get("/spaces")
async def list_spaces(
    slug: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List spaces the user belongs to."""
    query = (
        select(Space, SpaceMembership.role)
        .join(SpaceMembership, Space.id == SpaceMembership.space_id)
        .where(SpaceMembership.user_id == user.id)
        .order_by(Space.name)
    )

    if slug:
        query = query.where(Space.slug == slug.lower())

    result = await session.db.execute(query)
    rows = result.all()

    # Get member counts
    member_counts: dict[str, int] = {}
    if rows:
        org_ids = [org.id for org, _ in rows]
        count_query = (
            select(
                SpaceMembership.space_id,
                sa_func.count(SpaceMembership.id).label("cnt"),
            )
            .where(SpaceMembership.space_id.in_(org_ids))
            .group_by(SpaceMembership.space_id)
        )
        count_result = await session.db.execute(count_query)
        for row in count_result.all():
            member_counts[str(row[0])] = row[1]

    spaces = []
    for org, role in rows:
        spaces.append({
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "description": org.description,
            "visibility": org.visibility,
            "is_member": True,
            "is_current": str(org.id)
            == str(user.current_space_id or user.space_id),
            "is_personal": str(org.id) == str(user.space_id),
            "space_mode": "personal" if str(org.id) == str(user.space_id) else org.visibility,
            "viewer_role": role,
            "role": role,
            "member_role": role,
            "space_agent_id": str(org.space_agent_id) if org.space_agent_id else None,
            "member_count": member_counts.get(str(org.id), 0),
            "created_at": org.created_at.isoformat() if org.created_at else None,
        })

    return {"spaces": spaces, "count": len(spaces)}


@router.get("/spaces/{space_id}")
async def get_space(
    space_id: str,
    slug: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Get a specific space by ID or slug."""
    if slug:
        result = await session.db.execute(
            select(Space).where(Space.slug == slug.lower())
        )
    else:
        try:
            space_uuid = uuid.UUID(space_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid space ID")
        result = await session.db.execute(
            select(Space).where(Space.id == space_uuid)
        )

    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Space not found")

    membership_result = await session.db.execute(
        select(SpaceMembership)
        .where(SpaceMembership.space_id == org.id)
        .where(SpaceMembership.user_id == user.id)
    )
    membership = membership_result.scalar_one_or_none()

    # Member count
    count_result = await session.db.execute(
        select(sa_func.count(SpaceMembership.id))
        .where(SpaceMembership.space_id == org.id)
    )
    member_count = count_result.scalar() or 0

    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "description": org.description,
        "visibility": org.visibility,
        "is_member": membership is not None,
        "is_personal": str(org.id) == str(user.space_id),
        "space_mode": "personal" if str(org.id) == str(user.space_id) else org.visibility,
        "viewer_role": membership.role if membership else None,
        "role": membership.role if membership else None,
        "member_role": membership.role if membership else None,
        "space_agent_id": str(org.space_agent_id) if org.space_agent_id else None,
        "member_count": member_count,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


@router.get("/spaces/{space_id}/members")
async def list_space_members(
    space_id: str,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List members (humans + agents) of a space."""
    try:
        space_uuid = uuid.UUID(space_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid space ID")

    # Humans
    human_query = (
        select(User, SpaceMembership.role)
        .join(SpaceMembership, User.id == SpaceMembership.user_id)
        .where(SpaceMembership.space_id == space_uuid)
    )
    human_result = await session.db.execute(human_query)
    humans = human_result.all()

    # Agents
    agent_query = (
        select(Agent)
        .where(
            and_(
                Agent.space_id == space_uuid,
                Agent.status == "active",
                Agent.is_internal == False,  # noqa: E712
            )
        )
    )
    agent_result = await session.db.execute(agent_query)
    agents = agent_result.scalars().all()

    members = []
    for u, role in humans:
        members.append({
            "id": str(u.id),
            "display_name": u.username or u.full_name,
            "type": "human",
            "role": role,
            "status": "active" if u.active else "inactive",
            "avatar_url": getattr(u, "github_avatar_url", None),
        })
    for a in agents:
        members.append({
            "id": str(a.id),
            "display_name": a.name,
            "type": "agent",
            "role": "member",
            "status": a.status,
            "agent_type": a.agent_type,
        })

    return {"members": members, "count": len(members)}


@router.get("/spaces/{space_id}/recent-agents", response_model=RecentSpaceAgentsResponse)
async def list_recent_space_agents(
    space_id: str,
    limit: int = Query(10, ge=1, le=50, description="Number of agents to return"),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List recently active agents for launcher quick actions."""
    try:
        space_uuid = uuid.UUID(space_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid space ID")

    await verify_space_actor_access(
        session.db,
        user_id=user.id,
        space_id=space_uuid,
        is_agent=getattr(session, "is_agent", False),
        agent_id=getattr(session, "agent_id", None),
    )

    query = sa_text("""
        SELECT
            a.id,
            a.name,
            a.capabilities->>'avatar_url' AS avatar_url,
            MAX(m.created_at) AS last_message_at
        FROM agents a
        JOIN messages m ON m.agent_id = a.id
        WHERE m.space_id = :space_id
          AND a.is_internal = false
          AND a.status = 'active'
        GROUP BY a.id, a.name, a.capabilities->>'avatar_url'
        ORDER BY last_message_at DESC
        LIMIT :limit
    """)

    result = await session.db.execute(
        query,
        {"space_id": str(space_uuid), "limit": limit},
    )
    rows = result.mappings().all()

    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    agents = []
    for row in rows:
        last_message_at = row["last_message_at"]
        if last_message_at is not None and last_message_at.tzinfo is None:
            last_message_at = last_message_at.replace(tzinfo=timezone.utc)

        agents.append(
            RecentSpaceAgentEntry(
                id=str(row["id"]),
                name=row["name"],
                display_name=row["name"],
                avatar_url=row["avatar_url"],
                last_message_at=last_message_at,
                is_online=bool(
                    last_message_at is not None and last_message_at >= one_hour_ago
                ),
            )
        )

    return RecentSpaceAgentsResponse(agents=agents, cached=False)


# =========================================================================
# 3. AGENTS (8 endpoints)
# =========================================================================

@router.get("/agents")
async def list_agents(
    space_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None, description="Search by name (case-insensitive substring)"),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List active agents in the current space.

    When `search` is provided, filters by name (case-insensitive substring match)
    and returns up to `limit` results. Default limit is 50, max 200.
    """
    sid = await _resolve_space_id(user, space_id, session.db, session=session)

    # Query agents via agent_space_access (canonical space membership),
    # NOT Agent.space_id (legacy home-space column that misses
    # multi-space agents).
    conditions = [
        AgentSpaceAccess.space_id == sid,
        AgentSpaceAccess.state == "active",
        Agent.status == "active",
        Agent.is_internal == False,  # noqa: E712
        # ALC: never surface lifecycle-archived agents in the roster.
        Agent.lifecycle_state != "archived",
    ]
    if search and search.strip():
        term = search.strip().lower()
        conditions.append(or_(
            sa_func.lower(Agent.name).contains(term),
            cast(Agent.id, String).ilike(f"%{term}%"),
        ))

    query = (
        select(Agent)
        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
        .where(and_(*conditions))
        # ALC: surface recently-active agents first (most useful at top),
        # then alphabetical. Non-destructive — clients may still re-sort.
        .order_by(Agent.last_active_at.desc().nullslast(), Agent.name)
        .limit(limit)
    )

    result = await session.db.execute(query)
    agents = result.scalars().all()

    # Batch lookups for owner usernames and space names (no N+1)
    owner_ids = {a.user_id for a in agents if a.user_id}
    space_ids = {a.space_id for a in agents if a.space_id}
    try:
        space_ids.add(uuid.UUID(str(sid)))
    except ValueError:
        pass
    owner_names: dict[str, str] = {}
    space_names: dict[str, str] = {}
    if owner_ids:
        rows = await session.db.execute(
            select(User.id, User.username).where(User.id.in_(owner_ids))
        )
        owner_names = {str(r.id): r.username for r in rows}
    if space_ids:
        rows = await session.db.execute(
            select(Space.id, Space.name, Space.space_agent_id).where(Space.id.in_(space_ids))
        )
        selected_space_agent_id: str | None = None
        for r in rows:
            row_space_id = str(r.id)
            space_names[row_space_id] = r.name
            if row_space_id == str(sid) and getattr(r, "space_agent_id", None):
                selected_space_agent_id = str(r.space_agent_id)
    else:
        selected_space_agent_id = None

    requesting_user_id = str(user.id)
    listing_space_name = space_names.get(str(sid))
    has_registered_space_agent = any(str(a.id) == selected_space_agent_id for a in agents)
    space_agent_control_allowed = False
    if has_registered_space_agent:
        membership_result = await session.db.execute(
            select(SpaceMembership.role).where(
                and_(
                    SpaceMembership.user_id == user.id,
                    SpaceMembership.space_id == sid,
                )
            )
        )
        space_agent_control_allowed = membership_result.scalar_one_or_none() is not None

    # ALC: compute the DISPLAY lifecycle state live from last_active_at (the persisted
    # column is 'active' for everyone in shadow mode since the sweep doesn't write it).
    from app.core.agent_lifecycle import DEFAULT_THRESHOLDS, compute_display_lifecycle
    _now = datetime.now(timezone.utc)

    return {
        "agents": [
            {
                "id": str(a.id),
                "name": a.name,
                "display_name": a.name,
                "description": a.description,
                "agent_type": a.agent_type,
                "status": a.status,
                "model": display_model(a),
                # ALC: lifecycle surfaced on the roster so the MCP agents tool +
                # widget (and agents-using-MCP) can dim idle / flag dormant / hide archived.
                "lifecycle_state": compute_display_lifecycle(a.lifecycle_state, a.last_active_at, _now, DEFAULT_THRESHOLDS),
                "last_active_at": a.last_active_at.isoformat() if a.last_active_at else None,
                # This endpoint lists availability in the requested space via
                # AgentSpaceAccess. Return that listing context, not the legacy
                # Agent.space_id home-space column, so mention resolution and
                # follow-up UI actions stay keyed to the roster the caller asked for.
                "space_id": str(sid),
                "space_name": listing_space_name,
                "user_id": str(a.user_id) if a.user_id else None,
                "owner_username": owner_names.get(str(a.user_id)) if a.user_id else None,
                "is_own": str(a.user_id) == requesting_user_id if a.user_id else False,
                "can_control": (
                    (str(a.user_id) == requesting_user_id if a.user_id else False)
                    or (str(a.id) == selected_space_agent_id and space_agent_control_allowed)
                ),
                "can_update": (
                    (str(a.user_id) == requesting_user_id if a.user_id else False)
                    or (str(a.id) == selected_space_agent_id and space_agent_control_allowed)
                ),
            }
            for a in agents
        ],
        "count": len(agents),
    }


@router.post("/agents/processing-status")
async def agent_processing_status(
    body: AgentProcessingStatusBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Publish an agent_processing SSE event.

    Used by CLI agents to signal processing start/completion to the frontend.
    Accepts regular agent auth (Bearer + X-Agent-Id), no internal API key needed.
    """
    agent_id = _authenticated_agent_id(user, session)
    if not agent_id:
        raise HTTPException(status_code=403, detail="Agent identity required")

    space_id = session.space_id
    if not space_id:
        raise HTTPException(status_code=400, detail="Space context required")

    payload: dict[str, Any] = {
        "status": body.status,
        "agent_id": str(agent_id),
        "agent_name": body.agent_name or str(agent_id),
        "message_id": body.message_id,
    }
    if body.tool_name is not None:
        payload["tool_name"] = body.tool_name
    if body.activity is not None:
        payload["activity"] = body.activity
    if body.progress is not None:
        payload["progress"] = body.progress.model_dump()
    if body.detail is not None:
        payload["detail"] = body.detail

    if _is_deliberate_no_reply_status(body):
        from ...core.dispatch_executor import _publish_agent_skipped_signal

        await _publish_agent_skipped_signal(
            agent_id=str(agent_id),
            agent_name=body.agent_name or _authenticated_agent_name(user, session) or str(agent_id),
            message_id=body.message_id,
            space_id=str(space_id),
            intelligence={
                **(body.detail or {}),
                "reason_code": "no_reply",
                "reason_text": "no reply",
                "label": "no reply",
                "emoji": "",
                "signal_only": True,
            },
        )

    await redis_sse_broker.publish(
        space_id=str(space_id),
        event="agent_processing",
        data=payload,
    )

    return {"ok": True, "event": "agent_processing", "status": body.status}


def _parse_presence_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _presence_age_seconds(value: Any) -> int | None:
    parsed = _parse_presence_timestamp(value)
    if parsed is None:
        return None
    return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))


def _presence_last_heartbeat(presence_data: dict[str, Any] | None) -> Any:
    if not isinstance(presence_data, dict):
        return None
    return presence_data.get("last_heartbeat") or presence_data.get("connected_at")


def _presence_is_fresh(presence_data: dict[str, Any] | None) -> bool:
    age_seconds = _presence_age_seconds(_presence_last_heartbeat(presence_data))
    return age_seconds is not None and age_seconds < 60


def _connection_path_for_agent(agent: Any, presence_data: dict[str, Any] | None) -> str:
    source = (presence_data or {}).get("source") or (presence_data or {}).get("connection_source")
    if source == "gateway" or (presence_data or {}).get("gateway_id") or (presence_data or {}).get("gateway_instance_id"):
        return "gateway_managed"
    if source == "cli":
        return "direct_cli"
    if source == "sse" or presence_data:
        return "direct_sse"

    origin = (getattr(agent, "origin", None) or "").lower()
    agent_type = (getattr(agent, "agent_type", None) or "").lower()
    if origin == "mcp" or agent_type == "mcp":
        return "mcp_only"
    return "unknown"


def _connection_path_label(connection_path: str) -> str:
    return {
        "gateway_managed": "Gateway",
        "mcp_only": "MCP Dispatch",
        "direct_cli": "CLI",
        "direct_sse": "SSE",
        "unknown": "Unknown",
    }.get(connection_path, "Unknown")


def _badge_for_expected_response(expected_response: str) -> tuple[str, str, str]:
    return {
        "immediate": ("live", "Live", "success"),
        "warming": ("routable_delayed", "Warming", "warning"),
        "dispatch_delayed": ("routable_delayed", "Dispatch", "warning"),
        "routing_only": ("routing_only", "Routing only", "info"),
        "queued": ("queued_only", "Queued", "neutral"),
        "unlikely": ("blocked", "Stuck", "danger"),
        "unavailable": ("offline", "Offline", "muted"),
        "unknown": ("unknown", "Unknown", "neutral"),
    }.get(expected_response, ("unknown", "Unknown", "neutral"))


def _pre_send_warning(expected_response: str, connection_path: str) -> dict[str, str] | None:
    if expected_response == "immediate":
        return None
    if expected_response == "dispatch_delayed" or connection_path == "mcp_only":
        return {
            "severity": "info",
            "title": "Delivery may be delayed",
            "body": "This agent is reachable through MCP dispatch, not a live immediate session.",
        }
    if expected_response == "routing_only":
        return {
            "severity": "info",
            "title": "Routing-only mode",
            "body": "This agent can route or delegate work but will not send a direct reply.",
        }
    if expected_response == "unavailable":
        return {
            "severity": "warning",
            "title": "Agent unavailable",
            "body": "No viable live delivery path is currently known for this agent.",
        }
    return {
        "severity": "info",
        "title": "Non-immediate delivery",
        "body": "This agent may not respond immediately.",
    }


def _build_resolved_agent_state(
    *,
    agent: Any,
    space_id: uuid.UUID | str | None,
    presence_data: dict[str, Any] | None,
    control_state: AgentControlState,
) -> dict[str, Any]:
    """Build AVAIL-CONTRACT v4 resolved state consumed by UI/CLI clients.

    Raw presence observations remain in Redis/diagnostics. This DTO is the
    stable resolved surface for badges, mention pickers, and `ax agents check`.
    """
    control_dict = control_state.as_dict()
    is_disabled = bool(getattr(control_state, "is_disabled", False))
    routing_only = bool(getattr(control_state, "routing_only", False))
    disabled_reason = control_dict.get("disabled_reason") if isinstance(control_dict, dict) else None
    last_seen_at = _presence_last_heartbeat(presence_data)
    connected_since = presence_data.get("connected_at") if presence_data else None
    freshness_seconds = _presence_age_seconds(last_seen_at)
    runtime_connected = _presence_is_fresh(presence_data)
    control_active = not is_disabled
    connection_path = _connection_path_for_agent(agent, presence_data)
    mcp_only = connection_path == "mcp_only"

    if is_disabled:
        expected_response = "unavailable"
        routable = False
        responsive = False
        confidence = 0.95
        unavailable_reason = disabled_reason or "disabled"
        reason_code = "disabled"
        status_explanation = "Agent is disabled by control policy."
    elif mcp_only:
        # Hard AVAIL-CONTRACT invariant: MCP-only agents must not render as
        # live/immediate. They can be routable through dispatch, but delayed.
        expected_response = "dispatch_delayed"
        routable = True
        responsive = False
        confidence = 0.72 if runtime_connected else 0.62
        unavailable_reason = None
        reason_code = "mcp_dispatch"
        status_explanation = "Agent is reachable through MCP dispatch; immediate live response is not expected."
    elif routing_only and runtime_connected:
        expected_response = "routing_only"
        routable = True
        responsive = False
        confidence = 0.86
        unavailable_reason = None
        reason_code = "routing_only"
        status_explanation = "Agent is online and routing work, but direct replies are suppressed by control policy."
    elif runtime_connected:
        expected_response = "immediate"
        routable = True
        responsive = True
        confidence = 0.9
        unavailable_reason = None
        reason_code = None
        status_explanation = "Agent has a live heartbeat and is routable now."
    else:
        expected_response = "unavailable"
        routable = False
        responsive = False
        confidence = 0.35
        unavailable_reason = "no_live_session"
        reason_code = "no_live_session"
        status_explanation = "No live agent session is currently known."

    badge_state, badge_label, badge_color = _badge_for_expected_response(expected_response)
    recently_active = freshness_seconds is not None and freshness_seconds <= 300

    return {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "space_id": str(space_id) if space_id is not None else None,
        "environment": os.getenv("AX_ENV") or os.getenv("ENVIRONMENT") or "unknown",
        "registered": True,
        "control_active": control_active,
        "runtime_connected": runtime_connected,
        "responsive": responsive,
        "routable": routable,
        "recently_active": recently_active,
        "online_now": runtime_connected,
        "connection_path": connection_path,
        "connection_path_label": _connection_path_label(connection_path),
        "expected_response": expected_response,
        "badge_state": badge_state,
        "badge_label": badge_label,
        "badge_color": badge_color,
        "confidence": confidence,
        "connected_since": connected_since,
        "last_seen_at": last_seen_at,
        "freshness_seconds": freshness_seconds,
        "source": (presence_data or {}).get("source") or ("heartbeat" if runtime_connected else "inferred"),
        "source_of_truth": "heartbeat" if runtime_connected else "inferred",
        "presence_confidence": "high" if runtime_connected else ("medium" if mcp_only else "low"),
        "messages_routable": routable,
        "connection_mode": connection_path,
        "gateway_label": (presence_data or {}).get("gateway_id") or (presence_data or {}).get("gateway_instance_id"),
        "disconnect_reason": None if runtime_connected or mcp_only else unavailable_reason,
        "unavailable_reason": unavailable_reason,
        "reason_code": reason_code,
        "presence_age_seconds": freshness_seconds,
        "last_evidence_age_seconds": freshness_seconds,
        "status_explanation": status_explanation,
        "pre_send_warning": _pre_send_warning(expected_response, connection_path),
    }


def _availability_contract_fields(
    *,
    agent: Any | None = None,
    space_id: uuid.UUID | str | None = None,
    presence_data: dict[str, Any] | None,
    is_disabled: bool | None = None,
    disabled_reason: str | None = None,
    control_state: AgentControlState | None = None,
) -> dict[str, Any]:
    if control_state is None:
        control_state = AgentControlState()
        if is_disabled:
            control_state.is_disabled = True
            control_state.disabled_reason = disabled_reason
    if agent is not None:
        resolved = _build_resolved_agent_state(
            agent=agent,
            space_id=space_id,
            presence_data=presence_data,
            control_state=control_state,
        )
        legacy = dict(resolved)
        legacy["agent_state"] = resolved
        return legacy

    last_evidence = _presence_last_heartbeat(presence_data)
    connected_since = presence_data.get("connected_at") if presence_data else None
    online_now = _presence_is_fresh(presence_data)
    control_active = not bool(is_disabled)
    messages_routable = online_now and control_active

    if is_disabled:
        expected_response = "unavailable"
        unavailable_reason = disabled_reason or "disabled"
        reason_code = "disabled"
        status_explanation = "Agent is disabled by control policy."
    elif online_now:
        expected_response = "immediate"
        unavailable_reason = None
        reason_code = None
        status_explanation = "Agent has a live heartbeat and is routable now."
    else:
        expected_response = "unavailable"
        unavailable_reason = "no_live_session"
        reason_code = "no_live_session"
        status_explanation = "No live agent session is currently known."

    return {
        "registered": True,
        "control_active": control_active,
        "runtime_connected": online_now,
        "responsive": online_now and control_active,
        "routable": messages_routable,
        "recently_active": online_now,
        "online_now": online_now,
        "connected_since": connected_since,
        "last_seen_at": last_evidence,
        "source_of_truth": "heartbeat" if online_now else "inferred",
        "presence_confidence": "high" if online_now else "low",
        "messages_routable": messages_routable,
        "connection_mode": "space_agent",
        "gateway_label": None,
        "disconnect_reason": None if online_now else unavailable_reason,
        "expected_response": expected_response,
        "unavailable_reason": unavailable_reason,
        "reason_code": reason_code,
        "presence_age_seconds": _presence_age_seconds(last_evidence),
        "last_evidence_age_seconds": _presence_age_seconds(last_evidence),
        "status_explanation": status_explanation,
    }


@router.get("/agents/availability")
async def bulk_agent_availability(
    space_id: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Return widget-ready agent availability for the current space.

    MCP agents/widgets use this as an optional enrichment call before falling
    back to the plain agent list payload. Keep it as a real endpoint so normal
    agent discovery does not emit noisy 404s in production logs.
    """
    from ...core.agent_reliability import AgentPresence

    sid = await _resolve_space_id(user, space_id, session.db, session=session)
    sid_uuid = uuid.UUID(str(sid))

    query = (
        select(
            Agent.id,
            Agent.name,
            Agent.agent_type,
            Agent.status,
            Agent.origin,
            Agent.description,
            Agent.bio,
            Agent.specialization,
        )
        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
        .where(
            and_(
                AgentSpaceAccess.space_id == sid_uuid,
                AgentSpaceAccess.state == "active",
                Agent.status == "active",
                Agent.is_internal == False,  # noqa: E712
            )
        )
        .order_by(Agent.name)
    )
    result = await session.db.execute(query)
    agents = result.all()

    agent_ids = [str(a.id) for a in agents]
    presence_store = AgentPresence(_redis)
    try:
        presence_map = await presence_store.get_bulk_presence(agent_ids)
    except Exception:
        logger.warning("Failed to load agent availability presence map", exc_info=True)
        presence_map = {}

    control_states = {}
    if agents:
        try:
            control_states = await agent_control_service.get_control_states_batch([
                (agent.id, sid_uuid, (agent.name or "").strip().lower() or None)
                for agent in agents
            ])
        except Exception:
            logger.warning(
                "Failed to load agent availability control states for space %s",
                sid_uuid,
                exc_info=True,
            )

    owns_what_by_agent: dict[uuid.UUID, list[dict[str, Any]]] = {agent.id: [] for agent in agents}
    if agents:
        try:
            task_result = await session.db.execute(
                select(
                    Task.id,
                    Task.assigned_agent_id,
                    Task.task_number,
                    Task.title,
                    Task.work_status,
                    Task.priority,
                    Task.queue_state,
                )
                .where(
                    and_(
                        Task.space_id == sid_uuid,
                        Task.assigned_agent_id.in_([agent.id for agent in agents]),
                        Task.work_status.notin_(["completed", "cancelled"]),
                    )
                )
                .order_by(TASK_PRIORITY_RANK.asc(), Task.updated_at.desc())
                .limit(100)
            )
            for task in task_result.all():
                assigned_agent_id = getattr(task, "assigned_agent_id", None)
                if assigned_agent_id is None:
                    continue
                owns_what_by_agent.setdefault(assigned_agent_id, []).append(
                    {
                        "id": str(task.id),
                        "task_number": getattr(task, "task_number", None),
                        "title": getattr(task, "title", None),
                        "work_status": getattr(task, "work_status", None),
                        "priority": getattr(task, "priority", None),
                        "queue_state": getattr(task, "queue_state", None),
                    }
                )
        except Exception:
            logger.warning(
                "Failed to load agent availability ownership tasks for space %s",
                sid_uuid,
                exc_info=True,
            )

    items = []
    for agent in agents:
        agent_id = str(agent.id)
        presence_data = presence_map.get(agent_id)
        control_state = control_states.get(agent.id, AgentControlState())
        is_disabled = bool(getattr(control_state, "is_disabled", False))
        sse_connected = _presence_is_fresh(presence_data)
        control_dict = control_state.as_dict()
        disabled_reason = control_dict.get("disabled_reason") if isinstance(control_dict, dict) else None
        if is_disabled:
            availability = "disabled"
            presence = "disabled"
        else:
            availability = "high" if sse_connected else "offline"
            presence = "connected" if sse_connected else "offline"
        contract_fields = _availability_contract_fields(
            agent=agent,
            space_id=sid_uuid,
            presence_data=presence_data,
            control_state=control_state,
            is_disabled=is_disabled,
            disabled_reason=disabled_reason,
        )
        items.append({
            "agent_id": agent_id,
            "id": agent_id,
            "name": agent.name,
            "display_name": agent.name,
            "description": getattr(agent, "description", None),
            "bio": getattr(agent, "bio", None),
            "specialization": getattr(agent, "specialization", None),
            "is_online": sse_connected,
            "last_seen": _presence_last_heartbeat(presence_data),
            "last_heartbeat_at": _presence_last_heartbeat(presence_data),
            "owns_what": owns_what_by_agent.get(agent.id, []),
            "availability": availability,
            "availability_confidence": availability,
            "sse_connected": sse_connected,
            "presence": presence,
            "last_active": _presence_last_heartbeat(presence_data),
            "operational_status": "disabled" if is_disabled else agent.status,
            "agent_type": agent.agent_type or "assistant",
            "control": control_dict,
            **contract_fields,
        })

    return {"agents": items, "count": len(items)}


@router.get("/agents/presence")
async def bulk_agent_presence(
    space_id: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Get presence info for all agents in the space."""
    from ...core.agent_reliability import AgentPresence

    sid = await _resolve_space_id(user, space_id, session.db, session=session)
    sid_uuid = uuid.UUID(str(sid))

    query = (
        select(Agent.id, Agent.name, Agent.agent_type, Agent.status, Agent.origin)
        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
        .where(
            and_(
                AgentSpaceAccess.space_id == sid_uuid,
                AgentSpaceAccess.state == "active",
                Agent.status == "active",
                Agent.is_internal == False,  # noqa: E712
            )
        )
        .order_by(Agent.name)
    )
    result = await session.db.execute(query)
    agents = result.all()

    agent_ids = [str(a.id) for a in agents]
    presence_store = AgentPresence(_redis)
    try:
        presence_map = await presence_store.get_bulk_presence(agent_ids)
    except Exception:
        logger.warning("Failed to load agent bulk presence map", exc_info=True)
        presence_map = {}

    control_states = {}
    if agents:
        try:
            control_states = await agent_control_service.get_control_states_batch([
                (agent.id, sid_uuid, (agent.name or "").strip().lower() or None)
                for agent in agents
            ])
        except Exception:
            logger.warning(
                "Failed to load agent presence control states for space %s",
                sid_uuid,
                exc_info=True,
            )

    items = []
    for a in agents:
        aid = str(a.id)
        pdata = presence_map.get(aid)
        control_state = control_states.get(a.id, AgentControlState())
        is_disabled = bool(getattr(control_state, "is_disabled", False))
        items.append({
            "agent_id": aid,
            "name": a.name,
            "presence": "disabled" if is_disabled else ("online" if _presence_is_fresh(pdata) else "offline"),
            "responsive": _presence_is_fresh(pdata) and not is_disabled,
            "last_active": _presence_last_heartbeat(pdata),
            "last_heartbeat_at": _presence_last_heartbeat(pdata),
            "agent_type": a.agent_type or "assistant",
            "control": control_state.as_dict(),
        })

    return {"agents": items, "count": len(items)}



async def _get_space_agent_by_identifier(
    *,
    identifier: str,
    session: SecureSession,
    user: User,
    space_id: str | None = None,
) -> tuple[Any, uuid.UUID]:
    sid = await _resolve_space_id(user, space_id, session.db, session=session)
    sid_uuid = uuid.UUID(str(sid))
    filters = [
        AgentSpaceAccess.space_id == sid_uuid,
        AgentSpaceAccess.state == "active",
        Agent.is_internal == False,  # noqa: E712
    ]
    try:
        filters.append(Agent.id == uuid.UUID(identifier))
    except ValueError:
        filters.append(sa_func.lower(Agent.name) == identifier.strip().lower())

    result = await session.db.execute(
        select(Agent)
        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
        .where(and_(*filters))
        .limit(1)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent, sid_uuid


@router.get("/agents/{agent_id_or_name}/state")
async def single_agent_state(
    agent_id_or_name: str,
    space_id: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Return the resolved AVAIL-CONTRACT v4 agent_state DTO for one agent.

    This is the firm shape intended for CLI `ax agents check <name>` and UI
    badge consumers. It accepts either UUID or name, scoped to the caller's
    resolved space (or explicit `space_id`).
    """
    from ...core.agent_reliability import AgentPresence

    agent, sid_uuid = await _get_space_agent_by_identifier(
        identifier=agent_id_or_name,
        session=session,
        user=user,
        space_id=space_id,
    )

    presence_store = AgentPresence(_redis)
    try:
        presence_data = await presence_store.get_presence(str(agent.id))
    except Exception:
        logger.warning("Failed to load agent state presence for %s", agent.id, exc_info=True)
        presence_data = None

    try:
        control_state = await agent_control_service.get_control_state(
            agent_id=agent.id,
            space_id=sid_uuid,
            agent_slug=(agent.name or "").strip().lower() or None,
        )
    except Exception:
        logger.warning("Failed to load agent state control for %s", agent.id, exc_info=True)
        control_state = AgentControlState()

    resolved = _build_resolved_agent_state(
        agent=agent,
        space_id=sid_uuid,
        presence_data=presence_data,
        control_state=control_state,
    )
    return {
        "agent_state": resolved,
        "raw_presence": presence_data,
        "control": control_state.as_dict(),
    }


@router.get("/agents/{agent_id_or_name}/presence")
async def single_agent_presence(
    agent_id_or_name: str,
    space_id: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Get detailed presence diagnostics plus resolved state for one agent."""
    state_payload = await single_agent_state(
        agent_id_or_name=agent_id_or_name,
        space_id=space_id,
        user=user,
        session=session,
    )
    resolved = state_payload["agent_state"]
    return {
        "agent_id": resolved["agent_id"],
        "name": resolved["agent_name"],
        "presence": "disabled" if not resolved["control_active"] else ("online" if resolved["runtime_connected"] else "offline"),
        "responsive": resolved["responsive"],
        "last_active": resolved["last_seen_at"],
        "agent_type": None,
        **state_payload,
    }


@router.post("/agents/heartbeat")
async def agent_heartbeat(
    user: User = Depends(get_user_from_jwt_or_mcp),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Accept heartbeat (pong) from CLI agents.
    Updates presence in Redis so the agent shows as online.
    """
    bound_agent_id = getattr(user, "_bound_agent_id", None)
    if not bound_agent_id:
        raise HTTPException(status_code=400, detail="Not a bound agent session")

    # Verify agent is active
    agent_result = await db.execute(
        select(Agent.status).where(Agent.id == uuid.UUID(bound_agent_id))
    )
    agent_status = agent_result.scalar_one_or_none()
    if not agent_status or agent_status != "active":
        raise HTTPException(status_code=403, detail="Agent is not active")

    # Update presence in Redis (same format as SSE heartbeat)
    PRESENCE_TTL = 30  # matches PRESENCE_TTL_SECONDS in sse.py
    now = datetime.utcnow().isoformat()
    presence_data = json.dumps({
        "connected": True,
        "connected_at": now,
        "last_heartbeat": now,
        "space_id": str(getattr(user, "_effective_space_id", "")),
    })
    presence_key = f"ax:presence:{bound_agent_id}"
    await _redis.setex(presence_key, PRESENCE_TTL, presence_data)

    return {
        "agent_id": bound_agent_id,
        "presence": "online",
        "last_heartbeat": now,
        "ttl_seconds": PRESENCE_TTL,
    }


@router.get("/agents/me")
async def get_agent_me(
    request: Request,
    user: User = Depends(get_user_from_jwt_or_mcp),
    db: AsyncSession = Depends(get_db_session),
):
    """Get the authenticated agent's identity (MCP agents)."""
    agent = await _resolve_agent(user, db, request)
    effective_space_id = getattr(user, "_effective_space_id", None) or str(agent.space_id)

    # Owner info
    owner_result = await db.execute(select(User).where(User.id == agent.user_id))
    owner = owner_result.scalar_one_or_none()

    # Workspace info
    org_result = await db.execute(
        select(Space).where(Space.id == effective_space_id)
    )
    org = org_result.scalar_one_or_none()

    # Social counts
    followers = 0
    following = 0
    try:
        followers_result = await db.execute(
            select(sa_func.count()).where(AgentRelationship.followed_agent_id == agent.id)
        )
        followers = followers_result.scalar() or 0
        following_result = await db.execute(
            select(sa_func.count()).where(AgentRelationship.follower_agent_id == agent.id)
        )
        following = following_result.scalar() or 0
    except Exception:
        pass

    capabilities = []
    if hasattr(agent, "capabilities") and agent.capabilities:
        if isinstance(agent.capabilities, list):
            capabilities = agent.capabilities
        elif isinstance(agent.capabilities, dict):
            capabilities = list(agent.capabilities.keys())

    return {
        "id": str(agent.id),
        "name": agent.name,
        "handle": f"@{agent.name}",
        "bio": getattr(agent, "bio", None) or agent.description,
        "specialization": getattr(agent, "specialization", None),
        "capabilities": capabilities,
        "space_id": effective_space_id,
        "owner": {
            "id": str(owner.id) if owner else None,
            "name": owner.username if owner else None,
        },
        "workspace": {
            "id": str(org.id) if org else None,
            "name": org.name if org else None,
        },
        "social": {"followers": followers, "following": following},
    }


@router.patch("/agents/me")
async def update_agent_me(
    request: Request,
    body: AgentUpdateBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    db: AsyncSession = Depends(get_db_session),
):
    """Update the authenticated agent's profile."""
    agent = await _resolve_agent(user, db, request)
    updated_fields = []

    if body.bio is not None:
        if hasattr(agent, "bio"):
            agent.bio = body.bio
        elif hasattr(agent, "description"):
            agent.description = body.bio
        updated_fields.append("bio")

    if body.specialization is not None and hasattr(agent, "specialization"):
        agent.specialization = body.specialization
        updated_fields.append("specialization")

    if body.capabilities is not None and hasattr(agent, "capabilities"):
        agent.capabilities = body.capabilities
        updated_fields.append("capabilities")

    if body.avatar_url is not None and hasattr(agent, "avatar_url"):
        # Empty string clears the icon back to the default. (Task b7f73029.)
        agent.avatar_url = body.avatar_url or None
        updated_fields.append("avatar_url")

    if updated_fields:
        await db.commit()

    return {"ok": True, "updated_fields": updated_fields}


async def _update_owned_agent_profile(
    *,
    identifier: str,
    body: AgentProfileUpdateBody,
    session: SecureSession,
) -> dict:
    """Update profile fields for an owned or self-addressed agent."""
    owner_id = session.user.id
    if session.agent_id and str(session.agent_id) != str(identifier):
        owner_id = await _authorize_agent_management(session)

    agent = await _resolve_agent_by_identifier(session.db, identifier, owner_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{identifier}' not found")

    if session.agent_id and str(session.agent_id) == str(agent.id):
        pass
    elif agent.user_id != owner_id:
        raise HTTPException(status_code=403, detail="You can only update your own agents")

    updated_fields: list[str] = []
    if body.bio is not None:
        agent.bio = body.bio or None
        updated_fields.append("bio")
    if body.specialization is not None:
        agent.specialization = body.specialization or None
        updated_fields.append("specialization")
    if body.capabilities is not None:
        agent.capabilities = body.capabilities
        updated_fields.append("capabilities")
    if body.description is not None:
        agent.description = body.description
        updated_fields.append("description")
    if body.avatar_url is not None:
        agent.avatar_url = body.avatar_url or None
        updated_fields.append("avatar_url")
    if body.model is not None:
        user_role = getattr(session.user, "role", "user") or "user"
        is_valid, error_msg = validate_model_for_user_role(body.model, user_role)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
        agent.model = body.model
        updated_fields.append("model")
    if body.status is not None:
        if body.status not in ("active", "inactive", "paused", "disabled"):
            raise HTTPException(status_code=400, detail="Invalid agent status")
        agent.status = body.status
        updated_fields.append("status")
    if body.enabled_tools is not None:
        template = get_template(agent.template_type or "ax_agent")
        template_caps = template.get("capabilities", {}) if template else {}
        validated_tools = validate_enabled_tools_update(
            enabled_tools=body.enabled_tools,
            user=session.user,
            template_capabilities=template_caps,
        )
        current_tools = build_enabled_tools_from_agent(agent)
        current_tools.update(validated_tools)
        agent.enabled_tools = current_tools
        for legacy_field, value in get_legacy_column_updates_from_enabled_tools(current_tools).items():
            setattr(agent, legacy_field, value)
        updated_fields.append("enabled_tools")

    if updated_fields:
        await session.db.commit()

    return {"ok": True, "updated_fields": updated_fields, "agent": _serialize_managed_agent(agent)}


@router.patch("/agents/{identifier}")
async def patch_agent_profile(
    identifier: str,
    body: AgentProfileUpdateBody,
    session: SecureSession = Depends(get_secure_session),
):
    """Patch editable profile fields for an agent. Mirrors MCP agents.update."""
    return await _update_owned_agent_profile(identifier=identifier, body=body, session=session)


@router.put("/agents/{identifier}")
async def put_agent_profile(
    identifier: str,
    body: AgentProfileUpdateBody,
    session: SecureSession = Depends(get_secure_session),
):
    """Put editable profile fields for clients that use PUT instead of PATCH."""
    return await _update_owned_agent_profile(identifier=identifier, body=body, session=session)


@router.get("/agents/me/memory")
async def list_memory_keys(
    request: Request,
    user: User = Depends(get_user_from_jwt_or_mcp),
    db: AsyncSession = Depends(get_db_session),
    include_values: bool = Query(False),
):
    """List all memory keys for the authenticated agent."""
    agent = await _resolve_agent(user, db, request)
    key = _memory_key(str(agent.id))

    all_fields = await _redis.hgetall(key)
    keys = [
        {
            "key": k,
            "stored_at": None,
            "value": all_fields.get(k) if include_values else None,
        }
        for k in sorted(all_fields.keys())
    ]

    return {"keys": keys, "count": len(keys)}


@router.get("/agents/me/memory/{mem_key:path}")
async def get_memory_value(
    request: Request,
    mem_key: str,
    user: User = Depends(get_user_from_jwt_or_mcp),
    db: AsyncSession = Depends(get_db_session),
):
    """Get a specific memory value."""
    agent = await _resolve_agent(user, db, request)
    key = _memory_key(str(agent.id))

    value = await _redis.hget(key, mem_key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Memory key '{mem_key}' not found")

    return {"key": mem_key, "value": value, "stored_at": None}


@router.post("/agents/me/memory")
async def store_memory(
    request: Request,
    body: MemoryStoreBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    db: AsyncSession = Depends(get_db_session),
):
    """Store a key-value pair in agent memory."""
    agent = await _resolve_agent(user, db, request)
    key = _memory_key(str(agent.id))

    await _redis.hset(key, body.key, body.value)

    return {"ok": True, "key": body.key}


@router.post("/agents/me/follow")
async def follow_agent(
    request: Request,
    body: FollowBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    db: AsyncSession = Depends(get_db_session),
):
    """Follow an agent."""
    agent = await _resolve_agent(user, db, request)

    target_name = body.target_agent.lstrip("@")
    target_agent = await _find_agent_by_name_or_id(target_name, db)

    if not target_agent:
        raise HTTPException(status_code=404, detail=f"Agent '{body.target_agent}' not found")

    if target_agent.id == agent.id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")

    existing = await db.execute(
        select(AgentRelationship).where(
            and_(
                AgentRelationship.follower_agent_id == agent.id,
                AgentRelationship.followed_agent_id == target_agent.id,
                AgentRelationship.relationship_type == body.relationship_type,
            )
        )
    )
    if existing.scalar_one_or_none():
        return {"ok": True, "target_agent": target_agent.name, "relationship_type": body.relationship_type}

    rel = AgentRelationship(
        follower_agent_id=agent.id,
        followed_agent_id=target_agent.id,
        relationship_type=body.relationship_type,
    )
    db.add(rel)
    await db.commit()

    return {"ok": True, "target_agent": target_agent.name, "relationship_type": body.relationship_type}


@router.delete("/agents/me/follow/{target}")
async def unfollow_agent(
    request: Request,
    target: str,
    user: User = Depends(get_user_from_jwt_or_mcp),
    db: AsyncSession = Depends(get_db_session),
):
    """Unfollow an agent."""
    agent = await _resolve_agent(user, db, request)

    target_name = target.lstrip("@")
    target_agent = await _find_agent_by_name_or_id(target_name, db)

    if not target_agent:
        raise HTTPException(status_code=404, detail=f"Agent '{target}' not found")

    await db.execute(
        sa_delete(AgentRelationship).where(
            and_(
                AgentRelationship.follower_agent_id == agent.id,
                AgentRelationship.followed_agent_id == target_agent.id,
            )
        )
    )
    await db.commit()

    return {"ok": True}


# =========================================================================
# 4. CONTEXT (4 endpoints) — Redis KV, space-scoped
# =========================================================================

@router.get("/context")
async def list_context(
    space_id: Optional[str] = Query(None),
    prefix: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List context keys in a space."""
    sid = await _resolve_space_id(user, space_id, session.db, session=session)
    pattern = f"context:{sid}:"
    if prefix:
        pattern += f"{prefix}*"
    else:
        pattern += "*"

    keys = await _redis.keys(pattern)
    prefix_len = len(f"context:{sid}:")

    items = []
    seen_keys = set()
    for k in sorted(keys):
        short_key = k[prefix_len:]
        items.append({"key": short_key, "source": "redis"})
        seen_keys.add(short_key)

    # Keep the canonical /api/v1/context listing durable by merging Vault-backed
    # collection records. This prevents promoted games/artifacts from vanishing
    # from discovery when their Redis hot keys expire or are removed.
    try:
        service = WorkspaceIntelligenceService(session.db)
        offset = 0
        while True:
            vault_items = await service.list_intelligence(
                space_id=uuid.UUID(sid),
                key_prefix=prefix,
                limit=100,
                offset=offset,
                include_payload=False,
            )
            batch = vault_items.get("items", [])
            for vault_item in batch:
                key = vault_item.get("key")
                if not key or key in seen_keys:
                    continue
                if topic:
                    # The legacy unified list endpoint returns metadata-only key rows;
                    # topic filtering remains Redis-only unless the full context router
                    # includes Vault payload/metadata for topic checks.
                    continue
                items.append({
                    "key": key,
                    "source": "vault",
                    "artifact_type": vault_item.get("artifact_type"),
                    "summary_snippet": vault_item.get("summary_snippet"),
                    "version": vault_item.get("version"),
                    "access_count": vault_item.get("access_count"),
                })
                seen_keys.add(key)
            if not vault_items.get("has_more") or not batch:
                break
            offset += len(batch)
    except ValueError:
        logger.debug("Invalid space_id for Workspace Intelligence list fallback: %s", sid)
    except Exception as exc:
        logger.error("Workspace Intelligence list fallback failed: %s", exc, exc_info=True)

    return {"keys": items, "count": len(items)}


@router.get("/context/{key:path}")
async def get_context(
    key: str,
    space_id: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Get a context value."""
    sid = await _resolve_space_id(user, space_id, session.db, session=session)
    redis_key = f"context:{sid}:{key}"

    value = await _redis.get(redis_key)
    if value is None:
        try:
            intelligence = await WorkspaceIntelligenceService(session.db).get_intelligence(
                space_id=uuid.UUID(sid),
                key_or_id=key,
                increment_access=True,
                log_access=False,
            )
        except ValueError:
            intelligence = None
        except Exception as exc:
            logger.error("Workspace Intelligence get fallback failed for key %s: %s", key, exc, exc_info=True)
            intelligence = None

        if intelligence is None:
            raise HTTPException(status_code=404, detail=f"Context key '{key}' not found")
        return {
            "key": key,
            "value": intelligence["payload"],
            "space_id": sid,
            "topic": (intelligence.get("metadata") or {}).get("topic") if isinstance(intelligence.get("metadata"), dict) else None,
            "created_at": intelligence.get("created_at"),
            "expires_at": None,
            "source": "vault",
            "artifact_type": intelligence.get("artifact_type"),
            "summary_snippet": intelligence.get("summary_snippet"),
            "metadata": intelligence.get("metadata"),
            "version": intelligence.get("version"),
            "access_count": intelligence.get("access_count"),
        }

    # Check TTL for expires_at
    ttl = await _redis.ttl(redis_key)
    expires_at = None
    if ttl and ttl > 0:
        expires_at = datetime.now(timezone.utc).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")

    return {
        "key": key,
        "value": value,
        "space_id": sid,
        "topic": None,  # TODO: store topic metadata
        "created_at": None,
        "expires_at": expires_at,
    }


@router.post("/context")
async def set_context(
    body: ContextSetBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Set a context value with optional TTL."""
    sid = await _resolve_write_space_id(user, session, body.space_id, session.db)
    redis_key = f"context:{sid}:{body.key}"

    value = body.value
    try:
        value_size_bytes = ensure_context_inline_size(value)
    except ContextPayloadTooLarge as e:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                "Context payload is too large for inline Redis storage. "
                f"size_bytes={e.size_bytes}, max_bytes={e.max_bytes}. "
                "Upload large files/images first and store a context reference instead."
            ),
        )

    ttl_val = min(body.ttl or CONTEXT_DEFAULT_TTL, CONTEXT_MAX_TTL)
    await _redis.set(redis_key, value, ex=ttl_val)

    return {"ok": True, "key": body.key, "space_id": sid, "ttl": ttl_val, "value_size_bytes": value_size_bytes}


@router.delete("/context/{key:path}")
async def delete_context(
    key: str,
    space_id: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Delete a context key."""
    sid = await _resolve_write_space_id(user, session, space_id, session.db)
    redis_key = f"context:{sid}:{key}"

    deleted = await _redis.delete(redis_key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Context key '{key}' not found")

    return {"ok": True, "key": key}


# =========================================================================
# 6. SEARCH (1 endpoint)
# =========================================================================

@router.post("/search/messages")
async def search_messages(
    body: SearchMessagesBody,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Full-text search across messages in a space."""
    sid = await _resolve_space_id(user, body.space_id, session.db, session=session)
    limit = min(body.limit, 50)

    if not body.query.strip():
        raise HTTPException(status_code=400, detail="query is required")

    sql = sa_text("""
        SELECT m.id, m.content, m.agent_id, m.user_id, m.created_at, m.channel,
               ts_rank(to_tsvector('english', m.content), plainto_tsquery('english', :query)) as rank
        FROM messages m
        WHERE m.space_id = :space_id
          AND NOT (
              m.message_type = 'agent_pause'
              AND (
                  coalesce(m.metadata->>'reason', '') in ('no_reply', 'no_reply_requested', 'user_requested_silence', 'not_best_fit', 'agent_working')
                  OR coalesce(m.metadata->>'reason_code', '') in ('no_reply', 'no_reply_requested', 'user_requested_silence', 'not_best_fit', 'agent_working')
                  OR coalesce(m.metadata->>'pause_reason', '') in ('no_reply', 'no_reply_requested', 'user_requested_silence', 'not_best_fit', 'agent_working')
                  OR coalesce(m.metadata->>'signal_kind', '') in ('no_reply', 'no_reply_requested', 'user_requested_silence', 'not_best_fit', 'agent_working')
                  OR coalesce(m.metadata->>'signal_only', 'false') = 'true'
              )
          )
          AND to_tsvector('english', m.content) @@ plainto_tsquery('english', :query)
        ORDER BY rank DESC, m.created_at DESC
        LIMIT :limit
    """)

    result = await session.db.execute(sql, {
        "space_id": sid,
        "query": body.query,
        "limit": limit,
    })
    rows = result.mappings().all()

    # Resolve display names for results
    results = []
    for r in rows:
        sender_type = "agent" if r["agent_id"] else "human"
        display_name = None

        if r["agent_id"]:
            agent_result = await session.db.execute(
                select(Agent.name).where(Agent.id == r["agent_id"])
            )
            agent_name = agent_result.scalar_one_or_none()
            display_name = agent_name
        elif r["user_id"]:
            user_result = await session.db.execute(
                select(User.username).where(User.id == r["user_id"])
            )
            display_name = user_result.scalar_one_or_none()

        results.append({
            "id": str(r["id"]),
            "content": r["content"],
            "space_id": sid,
            "sender_type": sender_type,
            "display_name": display_name,
            "relevance": float(r["rank"]),
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })

    return {
        "results": results,
        "count": len(results),
        "query": body.query,
    }


# =========================================================================
# 7. SSE (1 endpoint)
# =========================================================================

SSE_HEARTBEAT_TIMEOUT_S = 15.0


@router.get("/sse/messages")
async def sse_messages_stream(
    request: Request,
    space_id: Optional[str] = Query(None),
    token: Optional[str] = Query(None, description="JWT token for EventSource auth"),
):
    """
    Stream real-time messages via Server-Sent Events.

    Auth via `token` query param (upstream JWT) since EventSource
    does not support custom headers.
    """
    import asyncio

    # --- Auth ---
    auth_token = token

    if not auth_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]

    if not auth_token:
        access_cookie = request.cookies.get("access_token")
        if access_cookie:
            auth_token = access_cookie

    if not auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for SSE connection",
        )

    try:
        async with system_session_context() as system_ctx:
            current_user = await _resolve_user_from_bearer_token(
                auth_token, system_ctx.db, allow_agent_tokens=True
            )
            # Re-fetch within session to ensure proper identity map binding
            result = await system_ctx.db.execute(
                select(User).where(User.id == current_user.id)
            )
            current_user = result.scalar_one()

        # Resolve effective org
        if space_id:
            current_user._effective_space_id = space_id
        else:
            current_user._effective_space_id = str(
                current_user.current_space_id or current_user.space_id
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SSE auth error: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed")

    if not current_user._effective_space_id:
        raise HTTPException(status_code=400, detail="User must be in a space to receive events")

    # --- SSE Generator ---
    space_id = current_user._effective_space_id

    async def _event_generator():
        client_id = f"{current_user.id}:{datetime.utcnow().isoformat()}"
        try:
            logger.info(f"SSE v1 client connected: {client_id} for space {space_id}")

            stream_start_id = await redis_sse_broker.get_stream_max_id(space_id)

            yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'space_id': space_id, 'user': current_user.username})}\n\n"

            # Bootstrap snapshot
            try:
                query = (
                    select(Message)
                    .options(selectinload(Message.user), selectinload(Message.agent))
                    .where(Message.space_id == space_id)
                    .order_by(Message.created_at.desc())
                    .limit(50)
                )
                async with system_session_context() as system_ctx:
                    result = await system_ctx.db.execute(query)
                    messages = list(result.scalars().all() or [])

                    # Conversation cards for bootstrap
                    from app.models.conversation_card import ConversationCard as BootstrapCard
                    card_result = await system_ctx.db.execute(
                        select(BootstrapCard)
                        .where(BootstrapCard.space_id == uuid.UUID(space_id))
                        .where(BootstrapCard.channel == "main")
                        .order_by(BootstrapCard.last_activity_at.desc())
                        .limit(50)
                    )
                    bootstrap_cards = list(card_result.scalars().all() or [])

                message_list = []
                for msg in reversed(messages):
                    sender_type = "agent" if msg.agent_id else "human"
                    display_name = None
                    if msg.agent_id and getattr(msg, "agent", None):
                        display_name = msg.agent.name
                    elif getattr(msg, "user", None):
                        display_name = msg.user.username or msg.user.full_name

                    message_list.append({
                        "id": str(msg.id),
                        "content": msg.content,
                        "space_id": str(msg.space_id),
                        "channel": msg.channel or "main",
                        "sender_id": str(msg.user_id) if msg.user_id else None,
                        "sender_type": sender_type,
                        "display_name": display_name,
                        "agent_id": str(msg.agent_id) if msg.agent_id else None,
                        "parent_id": str(msg.parent_id) if msg.parent_id else None,
                        "conversation_id": str(msg.parent_id) if msg.parent_id else str(msg.id),
                        "message_type": msg.message_type or "text",
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    })

                bootstrap_payload = {
                    "messages": message_list,
                    "count": len(message_list),
                    "space_id": space_id,
                    "conversation_cards": [
                        _serialize_conversation_card(c, include_extended=True)
                        for c in bootstrap_cards
                    ],
                }
                yield f"event: bootstrap\ndata: {json.dumps(bootstrap_payload)}\n\n"
            except Exception as be:
                logger.error(f"SSE bootstrap failed: {be}")

            # Subscribe to Redis Streams
            subscription = await redis_sse_broker.subscribe(space_id, last_id=stream_start_id)

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await subscription.get(timeout=SSE_HEARTBEAT_TIMEOUT_S)
                    event_type = event.get("event", "message")
                    event_data = event.get("data", {})
                    event_data["server_time"] = datetime.utcnow().isoformat()
                    yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                except asyncio.TimeoutError:
                    yield f"event: ping\ndata: {json.dumps({'timestamp': datetime.utcnow().isoformat()})}\n\n"

        except asyncio.CancelledError:
            logger.info(f"SSE stream cancelled for {client_id}")
        except Exception as e:
            logger.error(f"SSE error for {client_id}: {e}")
        finally:
            logger.info(f"SSE cleanup for {client_id}")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
    }

    environment = os.getenv("ENVIRONMENT", "development")
    if environment == "production":
        allowed_origins = ["https://paxai.app", "https://www.paxai.app", "https://next.paxai.app"]
    else:
        allowed_origins = ["http://localhost:3000", "http://localhost:8001", "http://127.0.0.1:3000"]

    request_origin = request.headers.get("origin")
    if request_origin and (request_origin in allowed_origins or environment == "development"):
        headers["Access-Control-Allow-Origin"] = request_origin
        headers["Access-Control-Allow-Credentials"] = "true"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Conversation Cards
# ---------------------------------------------------------------------------

@router.get("/conversations")
async def list_conversation_cards(
    channel: str = Query("main"),
    limit: int = Query(50, le=100),
    status: Optional[str] = Query(None),
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List conversation cards for the current space, ordered by latest activity."""
    from app.models.conversation_card import ConversationCard

    query = (
        select(ConversationCard)
        .where(ConversationCard.space_id == uuid.UUID(session.space_id))
        .where(ConversationCard.channel == channel)
        .order_by(ConversationCard.last_activity_at.desc())
        .limit(limit)
    )
    if status:
        query = query.where(ConversationCard.status == status)

    result = await session.db.execute(query)
    cards = result.scalars().all()

    return {
        "cards": [
            _serialize_conversation_card(c, include_extended=True)
            for c in cards
        ],
        "count": len(cards),
    }


# ---------------------------------------------------------------------------
# Drafts (canonical HITL / governance primitives)
# ---------------------------------------------------------------------------

_DRAFT_SECRET_DESTINATIONS = ("reveal_once", "platform_vault", "direct_binding")
_DRAFT_STATUSES_REQUIRING_REVIEW = {"under_review"}
_DRAFT_STATUSES_TERMINAL = {"executed", "rejected", "cancelled", "failed"}


class AgentDraftAgentSpec(BaseModel):
    name: str = Field(..., min_length=3, max_length=50)
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None


class AgentDraftCredentialSpec(BaseModel):
    kind: str = Field(..., description="ephemeral or durable")
    destination: str = Field("reveal_once", description="reveal_once, platform_vault, or direct_binding")
    ttl_seconds: int | None = None


class AgentDraftCreateRequest(BaseModel):
    agent_mode: str = Field("sandbox", description="sandbox or privileged; agent access requires sponsored OAuth")
    agent: AgentDraftAgentSpec
    target_space_id: str | None = None
    enabled_tools: dict[str, bool] | None = None
    requested_scopes: list[str] | None = None
    management_rights: bool = False
    credential: AgentDraftCredentialSpec | None = None
    idempotency_key: str | None = None


class SpaceDraftSpaceSpec(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    visibility: str | None = Field(None, pattern="^(private|invite_only|public)$")
    join_policy: str | None = None


class SpaceDraftTeamSpec(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    membership_policy: str | None = None


class SpaceDraftJoinSpec(BaseModel):
    mode: str = Field(..., description="community or protected")
    target_space_id: str | None = None
    join_reason: str | None = None
    invite_material: str | None = None


class SpaceDraftCreateRequest(BaseModel):
    space_mode: str | None = Field(None, description="personal, team, or community")
    space: SpaceDraftSpaceSpec | None = None
    team: SpaceDraftTeamSpec | None = None
    join: SpaceDraftJoinSpec | None = None
    idempotency_key: str | None = None


class DraftPatchRequest(BaseModel):
    version: int
    changes: dict[str, Any]


class DraftDecisionRequest(BaseModel):
    version: int


class _DraftOwnerContext(BaseModel):
    owner_user_id: str
    requested_by_user_id: str | None = None
    requested_by_agent_id: str | None = None
    origin_space_id: str
    origin_space_type: str
    actor_mode: str


def _draft_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_json_payload(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _set_path_value(payload: dict[str, Any], path: str, value: Any) -> None:
    current: dict[str, Any] = payload
    parts = path.split(".")
    for part in parts[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            nested = {}
            current[part] = nested
        current = nested
    current[parts[-1]] = value


def _draft_status_to_card_kind(status_value: str) -> str:
    if status_value == "executed":
        return "draft.executed"
    if status_value == "rejected":
        return "draft.rejected"
    if status_value == "cancelled":
        return "draft.cancelled"
    if status_value == "failed":
        return "draft.failed"
    return "draft.review"


def _draft_status_to_card_status(status_value: str) -> str:
    mapping = {
        "under_review": "review",
        "executed": "executed",
        "rejected": "rejected",
        "cancelled": "cancelled",
        "failed": "failed",
        "executing": "executing",
    }
    return mapping.get(status_value, "review")


def _draft_editable_fields(draft_kind: str) -> list[str]:
    if draft_kind == "agents.create.sandbox":
        return ["agent.name", "agent.description", "agent_mode", "target_space_id", "enabled_tools"]
    if draft_kind == "agents.create.privileged":
        return ["agent.name", "agent.description", "agent_mode", "target_space_id", "requested_scopes", "management_rights"]
    if draft_kind == "agents.create.with_credentials":
        return [
            "agent.name",
            "agent.description",
            "target_space_id",
            "requested_scopes",
            "credential.kind",
            "credential.ttl_seconds",
            "credential.destination",
        ]
    return _space_draft_editable_fields(draft_kind)


def _draft_title(draft_kind: str, payload: dict[str, Any]) -> str:
    agent_name = payload.get("agent", {}).get("name", "agent")
    if draft_kind == "agents.create.sandbox":
        return f"Create sandbox agent {agent_name}"
    if draft_kind == "agents.create.privileged":
        return f"Create privileged agent {agent_name}"
    if draft_kind == "agents.create.with_credentials":
        return f"Create agent {agent_name} with credential"
    if draft_kind.startswith("spaces.create."):
        space_name = payload.get("space", {}).get("name", "space")
        return f"Create space {space_name}"
    if draft_kind == "spaces.join.community":
        return "Join community space"
    if draft_kind == "spaces.join.protected":
        return "Join protected space"
    return "Review draft"


def _draft_summary(draft_kind: str) -> str:
    if draft_kind == "agents.create.with_credentials":
        return "Review the agent configuration and post-approval credential behavior before execution."
    if draft_kind == "agents.create.privileged":
        return "Review the privileged agent configuration before approval."
    if draft_kind.startswith("spaces.create."):
        return "Review the proposed space configuration before approval."
    if draft_kind.startswith("spaces.join."):
        return "Review the space join request before approval."
    return "Review the proposed agent before approval."


def _normalize_agent_draft_payload(
    body: AgentDraftCreateRequest,
    *,
    default_space_id: str,
    origin_space_id: str,
    origin_space_type: str,
) -> dict[str, Any]:
    if body.agent_mode == "with_credentials" or body.credential is not None:
        raise _draft_error(410, "credential_flow_retired", "Connect agents through sponsored OAuth; see /auth.md")

    if body.agent_mode not in {"sandbox", "privileged", "with_credentials"}:
        raise _draft_error(400, "draft_invalid_kind", "agent_mode must be sandbox, privileged, or with_credentials")

    if body.requested_scopes:
        invalid_scopes = sorted(set(body.requested_scopes) - set(VALID_SCOPES))
        if invalid_scopes:
            raise _draft_error(400, "scope_denied", f"Unsupported scopes: {invalid_scopes}")

    target_space_id = str(body.target_space_id or default_space_id)

    credential_payload: dict[str, Any] | None = None
    action_ids: list[str]
    risk_class: str
    if body.agent_mode == "with_credentials":
        if body.credential is None:
            raise _draft_error(400, "draft_invalid_payload", "credential is required for with_credentials drafts")
        if body.credential.kind not in {"ephemeral", "durable"}:
            raise _draft_error(400, "draft_invalid_payload", "credential.kind must be ephemeral or durable")
        if body.credential.destination not in _DRAFT_SECRET_DESTINATIONS:
            raise _draft_error(
                400,
                "draft_invalid_payload",
                f"credential.destination must be one of {list(_DRAFT_SECRET_DESTINATIONS)}",
            )
        draft_kind = "agents.create.with_credentials"
        risk_class = "R3"
        action_ids = [
            "agents.create.with_credentials",
            "credentials.mint.ephemeral" if body.credential.kind == "ephemeral" else "credentials.mint.durable",
        ]
        credential_payload = {
            "kind": body.credential.kind,
            "destination": body.credential.destination,
            "ttl_seconds": body.credential.ttl_seconds,
        }
    elif body.agent_mode == "privileged":
        draft_kind = "agents.create.privileged"
        risk_class = "R3"
        action_ids = ["agents.create.privileged"]
        if body.management_rights:
            action_ids.append("agents.create.with_management")
    else:
        draft_kind = "agents.create.sandbox"
        risk_class = "R2"
        action_ids = ["agents.create.sandbox"]

    return {
        "kind": draft_kind,
        "action_family": "agents.create",
        "action_ids": action_ids,
        "risk_class": risk_class,
        "origin_space_id": origin_space_id,
        "origin_space_type": origin_space_type,
        "target_space_id": target_space_id,
        "agent": body.agent.model_dump(),
        "enabled_tools": body.enabled_tools or get_enabled_tools_defaults(),
        "requested_scopes": body.requested_scopes or [],
        "management_rights": bool(body.management_rights),
        "credential": credential_payload,
        "editable_fields": _draft_editable_fields(draft_kind),
        "requested_operations": [
            {
                "op": "create_agent_draft",
                "target_resource_type": "agent",
                "target_space_id": target_space_id,
            }
        ],
        "normalized_operations": [
            {
                "op": "create_agent",
                "target_resource_type": "agent",
                "target_resource_space_field": "target_space_id",
            }
        ]
        + (
            [
                {
                    "op": "mint_agent_credential",
                    "target_resource_type": "credential",
                    "target_resource_space_field": "target_space_id",
                }
            ]
            if credential_payload
            else []
        ),
        "policy_snapshot": {
            "decision": "allow_with_draft",
            "reason": "agent provisioning requires human review",
        },
        "result_surface_hint": "ui://agents/detail",
    }


def _space_draft_editable_fields(draft_kind: str) -> list[str]:
    if draft_kind == "spaces.create.personal":
        return ["space.name", "space.description", "space.visibility", "space_mode"]
    if draft_kind == "spaces.create.team":
        return ["space.name", "space.description", "space.visibility", "space_mode", "team.name", "team.membership_policy"]
    if draft_kind == "spaces.create.community":
        return ["space.name", "space.description", "space.visibility", "space_mode", "space.join_policy"]
    if draft_kind == "spaces.join.community":
        return ["join.target_space_id", "join.join_reason"]
    if draft_kind == "spaces.join.protected":
        return ["join.target_space_id", "join.join_reason", "join.invite_material"]
    return []


def _space_create_kind_for_mode(space_mode: str) -> str:
    if space_mode not in {"personal", "team", "community"}:
        raise _draft_error(400, "draft_invalid_kind", "space_mode must be personal, team, or community")
    return f"spaces.create.{space_mode}"


def _normalize_space_draft_payload(
    body: SpaceDraftCreateRequest,
    *,
    origin_space_id: str,
    origin_space_type: str,
) -> dict[str, Any]:
    if body.space_mode:
        space_mode = body.space_mode
        _space_create_kind_for_mode(space_mode)
        if body.space is None or not body.space.name:
            raise _draft_error(400, "draft_invalid_payload", "space.name is required for create-space drafts")

        if space_mode == "personal":
            draft_kind = "spaces.create.personal"
            action_ids = ["spaces.create.personal"]
            visibility = body.space.visibility or "private"
        elif space_mode == "team":
            draft_kind = "spaces.create.team"
            action_ids = ["spaces.create.team", "teams.create"]
            visibility = body.space.visibility or "invite_only"
        else:
            draft_kind = "spaces.create.community"
            action_ids = ["spaces.create.community"]
            visibility = body.space.visibility or "public"

        return {
            "kind": draft_kind,
            "action_family": "spaces.create",
            "action_ids": action_ids,
            "risk_class": "R3",
            "origin_space_id": origin_space_id,
            "origin_space_type": origin_space_type,
            "space": {
                "name": body.space.name,
                "description": body.space.description,
                "visibility": visibility,
                "join_policy": body.space.join_policy,
            },
            "team": body.team.model_dump() if body.team else None,
            "editable_fields": _space_draft_editable_fields(draft_kind),
            "requested_operations": [
                {
                    "op": "create_space_draft",
                    "target_resource_type": "space",
                }
            ],
            "normalized_operations": [
                {
                    "op": "create_space",
                    "target_resource_type": "space",
                }
            ],
            "policy_snapshot": {
                "decision": "allow_with_draft",
                "reason": "space creation requires human review",
            },
            "result_surface_hint": "ui://spaces/navigator",
        }

    if body.join:
        join_mode = body.join.mode
        if join_mode not in {"community", "protected"}:
            raise _draft_error(400, "draft_invalid_kind", "join.mode must be community or protected")
        if not body.join.target_space_id:
            raise _draft_error(400, "draft_invalid_payload", "join.target_space_id is required")

        draft_kind = f"spaces.join.{join_mode}"
        return {
            "kind": draft_kind,
            "action_family": "spaces.join",
            "action_ids": [draft_kind],
            "risk_class": "R2" if join_mode == "community" else "R3",
            "origin_space_id": origin_space_id,
            "origin_space_type": origin_space_type,
            "join": body.join.model_dump(),
            "target_space_id": body.join.target_space_id,
            "editable_fields": _space_draft_editable_fields(draft_kind),
            "requested_operations": [
                {
                    "op": "join_space_draft",
                    "target_resource_type": "space_membership",
                    "target_space_id": body.join.target_space_id,
                }
            ],
            "normalized_operations": [
                {
                    "op": "join_space",
                    "target_resource_type": "space_membership",
                    "target_resource_space_field": "target_space_id",
                }
            ],
            "policy_snapshot": {
                "decision": "allow_with_draft",
                "reason": "space join requires review",
            },
            "result_surface_hint": "ui://spaces/navigator",
        }

    raise _draft_error(400, "draft_invalid_payload", "Provide either space_mode or join")


async def _load_allowed_owner_spaces(db: AsyncSession, owner_user_id: str) -> list[dict[str, str]]:
    result = await db.execute(
        select(Space)
        .join(SpaceMembership, SpaceMembership.space_id == Space.id)
        .where(SpaceMembership.user_id == uuid.UUID(owner_user_id))
        .where(Space.is_archived.is_(False))
        .order_by(Space.name.asc())
    )
    spaces = result.scalars().all()
    return [
        {
            "id": str(space.id),
            "name": space.name,
            "slug": space.slug,
            "visibility": space.visibility,
        }
        for space in spaces
    ]


async def _load_joinable_spaces(
    db: AsyncSession,
    *,
    owner_user_id: str,
    visibility: str,
) -> list[dict[str, str]]:
    membership_subq = (
        select(SpaceMembership.space_id)
        .where(SpaceMembership.user_id == uuid.UUID(owner_user_id))
        .subquery()
    )
    result = await db.execute(
        select(Space)
        .where(Space.visibility == visibility)
        .where(Space.is_archived.is_(False))
        .where(Space.id.not_in(select(membership_subq.c.space_id)))
        .order_by(Space.name.asc())
    )
    spaces = result.scalars().all()
    return [
        {
            "id": str(space.id),
            "name": space.name,
            "slug": space.slug,
            "visibility": space.visibility,
        }
        for space in spaces
    ]


async def _serialize_draft_envelope(db: AsyncSession, proposal: AgentManagementProposal) -> dict[str, Any]:
    payload = _copy_json(proposal.proposed_payload or {})
    selector_options: dict[str, Any] = {}
    if payload.get("kind", "").startswith("agents.create."):
        selector_options["target_space_id"] = await _load_allowed_owner_spaces(db, str(proposal.target_owner_user_id))
    elif payload.get("kind") == "spaces.join.community":
        selector_options["join.target_space_id"] = await _load_joinable_spaces(
            db,
            owner_user_id=str(proposal.target_owner_user_id),
            visibility="public",
        )
    elif payload.get("kind") == "spaces.join.protected":
        selector_options["join.target_space_id"] = await _load_joinable_spaces(
            db,
            owner_user_id=str(proposal.target_owner_user_id),
            visibility="invite_only",
        )
    if payload.get("credential"):
        selector_options["credential.destination"] = [
            {"id": value, "label": value.replace("_", " ")}
            for value in _DRAFT_SECRET_DESTINATIONS
        ]

    actions: list[dict[str, str]] = []
    if proposal.status in _DRAFT_STATUSES_REQUIRING_REVIEW:
        actions = [
            {"id": "edit", "kind": "api"},
            {"id": "approve", "kind": "api"},
            {"id": "reject", "kind": "api"},
            {"id": "cancel", "kind": "api"},
        ]

    card = {
        "schema": "ax.card/v1",
        "card_id": f"draft:{proposal.id}",
        "kind": _draft_status_to_card_kind(proposal.status),
        "status": _draft_status_to_card_status(proposal.status),
        "title": _draft_title(payload.get("kind", "draft.review"), payload),
        "summary": _draft_summary(payload.get("kind", "draft.review")),
        "draft_id": str(proposal.id),
        "version": proposal.version,
        "action_ids": payload.get("action_ids", []),
        "policy": {
            "approval_required": True,
            "risk_class": payload.get("risk_class"),
            "reason": payload.get("policy_snapshot", {}).get("reason"),
        },
        "actions": actions,
    }

    return {
        "draft_id": str(proposal.id),
        "kind": payload.get("kind"),
        "status": proposal.status,
        "version": proposal.version,
        "action_family": payload.get("action_family"),
        "action_ids": payload.get("action_ids", []),
        "approval_required": True,
        "risk_class": payload.get("risk_class"),
        "origin_space_id": str(proposal.space_id),
        "origin_space_type": payload.get("origin_space_type"),
        "target_space_id": str(proposal.target_space_id) if proposal.target_space_id else None,
        "editable_fields": payload.get("editable_fields", []),
        "selector_options": selector_options,
        "requested_operations": payload.get("requested_operations", []),
        "normalized_operations": payload.get("normalized_operations", []),
        "policy_snapshot": payload.get("policy_snapshot", {}),
        "result_surface_hint": payload.get("result_surface_hint"),
        "agent": payload.get("agent"),
        "enabled_tools": payload.get("enabled_tools"),
        "requested_scopes": payload.get("requested_scopes", []),
        "credential": payload.get("credential"),
        "space": payload.get("space"),
        "team": payload.get("team"),
        "join": payload.get("join"),
        "execution_result": payload.get("execution_result"),
        "card": card,
    }


async def _resolve_draft_owner_context(
    request: Request,
    session: SecureSession,
    *,
    require_agent_management: bool = True,
) -> _DraftOwnerContext:
    on_behalf_of = request.headers.get("x-on-behalf-of")
    if on_behalf_of:
        actor = await extract_management_actor(session, request)
        if actor.mode != "concierge_delegated" or not actor.user_id or not actor.agent_id:
            raise _draft_error(403, "origin_space_denied", "Concierge draft creation requires delegated user authority")

        async with system_session_context() as system_ctx:
            result = await system_ctx.db.execute(
                select(User.space_id).where(User.id == actor.user_id)
            )
            personal_space_id = result.scalar_one_or_none()
        origin_space_type = (
            "personal"
            if personal_space_id and str(personal_space_id) == str(actor.space_id)
            else "non_personal"
        )

        return _DraftOwnerContext(
            owner_user_id=str(actor.user_id),
            requested_by_user_id=None,
            requested_by_agent_id=str(actor.agent_id),
            origin_space_id=str(actor.space_id),
            origin_space_type=origin_space_type,
            actor_mode="concierge_delegated",
        )

    owner_id = (
        await _authorize_agent_management(session)
        if require_agent_management
        else session.user.id
    )
    origin_space_type = "personal" if str(session.user.space_id) == str(session.space_id) else "non_personal"
    return _DraftOwnerContext(
        owner_user_id=str(owner_id),
        requested_by_user_id=str(session.user.id),
        requested_by_agent_id=session.agent_id,
        origin_space_id=str(session.space_id),
        origin_space_type=origin_space_type,
        actor_mode="user",
    )


async def _load_draft_or_404(db: AsyncSession, draft_id: str) -> AgentManagementProposal:
    try:
        draft_uuid = uuid.UUID(draft_id)
    except ValueError as exc:
        raise _draft_error(404, "draft_not_found", "Draft not found") from exc

    result = await db.execute(
        select(AgentManagementProposal).where(AgentManagementProposal.id == draft_uuid)
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise _draft_error(404, "draft_not_found", "Draft not found")
    return proposal


def _ensure_draft_actor_can_view(
    *,
    proposal: AgentManagementProposal,
    session: SecureSession,
) -> None:
    actor_user_id = str(session.user.id) if session.user else None
    actor_agent_id = str(session.agent_id) if session.agent_id else None
    if actor_user_id == str(proposal.target_owner_user_id):
        return
    if actor_agent_id and actor_agent_id == str(proposal.requested_by_agent_id):
        return
    raise _draft_error(403, "policy_denied", "You do not have access to this draft")


def _require_human_approver(request: Request, session: SecureSession) -> uuid.UUID:
    auth_header = request.headers.get("authorization", "")
    if not session.user:
        raise _draft_error(401, "approval_required", "Human approval is required")
    if auth_header.startswith("Bearer axp_"):
        raise _draft_error(403, "approval_required", "PAT credentials cannot approve governance drafts")
    if session.principal_type == "agent" and not request.headers.get("x-on-behalf-of"):
        raise _draft_error(403, "approval_required", "Agent principals cannot approve governance drafts")
    return session.user.id


def _apply_draft_patch_payload(
    *,
    payload: dict[str, Any],
    changes: dict[str, Any],
) -> dict[str, Any]:
    editable_fields = set(payload.get("editable_fields", []))
    invalid_fields = sorted(set(changes) - editable_fields)
    if invalid_fields:
        raise _draft_error(403, "editable_field_denied", f"Fields are not editable: {invalid_fields}")

    if "space_mode" in changes:
        space_mode = str(changes["space_mode"] or "").strip()
        next_kind = _space_create_kind_for_mode(space_mode)
        if not str(payload.get("kind", "")).startswith("spaces.create."):
            raise _draft_error(403, "editable_field_denied", "space_mode is only editable for create-space drafts")
        next_editable_fields = set(_draft_editable_fields(next_kind))
        invalid_after_mode = sorted(set(changes) - next_editable_fields)
        if invalid_after_mode:
            raise _draft_error(
                403,
                "editable_field_denied",
                f"Fields are not editable after space_mode change: {invalid_after_mode}",
            )

    updated = _copy_json(payload)
    for field_path, value in changes.items():
        if field_path == "agent_mode":
            agent_mode = str(value or "").strip()
            if agent_mode not in {"sandbox", "privileged"}:
                raise _draft_error(400, "draft_invalid_kind", "agent_mode must be sandbox or privileged")
            if agent_mode == "privileged":
                updated["kind"] = "agents.create.privileged"
                updated["risk_class"] = "R3"
                action_ids = ["agents.create.privileged"]
                if updated.get("management_rights"):
                    action_ids.append("agents.create.with_management")
                updated["action_ids"] = action_ids
            else:
                updated["kind"] = "agents.create.sandbox"
                updated["risk_class"] = "R2"
                updated["action_ids"] = ["agents.create.sandbox"]
                updated["management_rights"] = False
                updated["requested_scopes"] = []
            updated["editable_fields"] = _draft_editable_fields(updated["kind"])
            continue
        if field_path == "space_mode":
            space_mode = str(value or "").strip()
            _space_create_kind_for_mode(space_mode)
            if not str(updated.get("kind", "")).startswith("spaces.create."):
                raise _draft_error(403, "editable_field_denied", "space_mode is only editable for create-space drafts")

            space_payload = updated.setdefault("space", {})
            current_visibility = str(space_payload.get("visibility") or "").strip().lower()
            if space_mode == "personal":
                updated["kind"] = "spaces.create.personal"
                updated["action_ids"] = ["spaces.create.personal"]
                space_payload["visibility"] = "private"
                updated["team"] = None
            elif space_mode == "team":
                updated["kind"] = "spaces.create.team"
                updated["action_ids"] = ["spaces.create.team", "teams.create"]
                space_payload["visibility"] = (
                    current_visibility
                    if current_visibility in {"private", "invite_only"}
                    else "invite_only"
                )
                updated.setdefault("team", None)
            else:
                updated["kind"] = "spaces.create.community"
                updated["action_ids"] = ["spaces.create.community"]
                space_payload["visibility"] = "public"
                updated["team"] = None
            updated["editable_fields"] = _draft_editable_fields(updated["kind"])
            continue
        _set_path_value(updated, field_path, value)
    return updated


async def _validate_agent_draft_payload(
    db: AsyncSession,
    *,
    payload: dict[str, Any],
    owner_user_id: str,
) -> None:
    agent_payload = payload.get("agent") or {}
    valid, error_msg = validate_agent_name(agent_payload.get("name", ""))
    if not valid:
        raise _draft_error(400, "draft_invalid_payload", error_msg)

    target_space_id = payload.get("target_space_id")
    if not target_space_id:
        raise _draft_error(400, "draft_invalid_payload", "target_space_id is required")

    await verify_space_membership(db, uuid.UUID(owner_user_id), target_space_id)

    requested_scopes = payload.get("requested_scopes") or []
    invalid_scopes = sorted(set(requested_scopes) - set(VALID_SCOPES))
    if invalid_scopes:
        raise _draft_error(400, "scope_denied", f"Unsupported scopes: {invalid_scopes}")

    credential = payload.get("credential")
    if credential:
        if credential.get("kind") not in {"ephemeral", "durable"}:
            raise _draft_error(400, "draft_invalid_payload", "credential.kind must be ephemeral or durable")
        if credential.get("destination") not in _DRAFT_SECRET_DESTINATIONS:
            raise _draft_error(
                400,
                "draft_invalid_payload",
                f"credential.destination must be one of {list(_DRAFT_SECRET_DESTINATIONS)}",
            )

    duplicate_query = select(Agent).where(
        Agent.name == agent_payload.get("name"),
        Agent.space_id == uuid.UUID(str(target_space_id)),
    )
    existing = await db.execute(duplicate_query)
    if existing.scalar_one_or_none():
        raise _draft_error(409, "idempotency_conflict", f"Agent '{agent_payload.get('name')}' already exists in this space")


async def _validate_space_draft_payload(
    db: AsyncSession,
    *,
    payload: dict[str, Any],
    owner_user_id: str,
) -> None:
    draft_kind = payload.get("kind")
    if draft_kind.startswith("spaces.create."):
        space_payload = payload.get("space") or {}
        name = (space_payload.get("name") or "").strip()
        if not name:
            raise _draft_error(400, "draft_invalid_payload", "space.name is required")
        visibility = space_payload.get("visibility") or "private"
        if visibility not in {"private", "invite_only", "public"}:
            raise _draft_error(400, "draft_invalid_payload", "space.visibility must be private, invite_only, or public")
        if draft_kind == "spaces.create.personal" and visibility != "private":
            raise _draft_error(400, "draft_invalid_payload", "Personal spaces must remain private")
        if draft_kind == "spaces.create.community" and visibility != "public":
            raise _draft_error(400, "draft_invalid_payload", "Community spaces must be public in the current domain model")
        if draft_kind == "spaces.create.team" and visibility == "public":
            raise _draft_error(400, "draft_invalid_payload", "Team spaces cannot be public")

        existing = await db.execute(
            select(Space).where(
                sa_func.lower(Space.name) == name.lower(),
                Space.created_by == uuid.UUID(owner_user_id),
            )
        )
        if existing.scalar_one_or_none():
            raise _draft_error(409, "idempotency_conflict", f"Space '{name}' already exists for this owner")
        return

    join_payload = payload.get("join") or {}
    target_space_id = join_payload.get("target_space_id")
    if not target_space_id:
        raise _draft_error(400, "draft_invalid_payload", "join.target_space_id is required")

    result = await db.execute(select(Space).where(Space.id == uuid.UUID(str(target_space_id))))
    target_space = result.scalar_one_or_none()
    if not target_space or target_space.is_archived:
        raise _draft_error(404, "target_boundary_denied", "Target space not found")

    existing_membership = await db.execute(
        select(SpaceMembership).where(
            SpaceMembership.user_id == uuid.UUID(owner_user_id),
            SpaceMembership.space_id == target_space.id,
        )
    )
    if existing_membership.scalar_one_or_none():
        raise _draft_error(409, "idempotency_conflict", "User is already a member of this space")

    if draft_kind == "spaces.join.community":
        if target_space.visibility != "public":
            raise _draft_error(400, "target_boundary_denied", "Community join drafts require a public space")
        return

    if target_space.visibility == "public":
        raise _draft_error(400, "target_boundary_denied", "Protected join drafts require an invite-only space")
    invite_material = (join_payload.get("invite_material") or "").strip()
    if not invite_material:
        raise _draft_error(400, "draft_invalid_payload", "join.invite_material is required for protected joins")

    invite_result = await db.execute(
        select(SpaceInviteCode).where(
            SpaceInviteCode.invite_code == invite_material,
            SpaceInviteCode.space_id == target_space.id,
            SpaceInviteCode.active.is_(True),
            or_(SpaceInviteCode.expires_at.is_(None), SpaceInviteCode.expires_at > datetime.utcnow()),
        )
    )
    invite = invite_result.scalar_one_or_none()
    if not invite:
        raise _draft_error(404, "target_boundary_denied", "Invite material is invalid or expired")


def _proposal_action_ids(proposal: AgentManagementProposal) -> list[str]:
    payload = proposal.proposed_payload or {}
    action_ids = payload.get("action_ids")
    if isinstance(action_ids, list) and action_ids:
        return [str(value) for value in action_ids]
    fallback = payload.get("kind")
    return [str(fallback)] if fallback else []


def _resolve_agent_draft_route_action(
    *,
    body: AgentDraftCreateRequest,
    session: SecureSession,
) -> ResolvedRouteAction:
    payload = _normalize_agent_draft_payload(
        body,
        default_space_id=str(body.target_space_id or session.space_id),
        origin_space_id=str(session.space_id),
        origin_space_type="personal" if str(session.user.space_id) == str(session.space_id) else "non_personal",
    )
    return ResolvedRouteAction(
        action_family="drafts.agents",
        action_id=payload["action_ids"][0],
        target_resource_type="draft",
        target_resource_space_id=str(payload["target_space_id"]),
    )


def _resolve_existing_draft_route_action(
    *,
    proposal: AgentManagementProposal,
    **_: Any,
) -> ResolvedRouteAction:
    action_ids = _proposal_action_ids(proposal)
    return ResolvedRouteAction(
        action_family="drafts.lifecycle",
        action_id=action_ids[0] if action_ids else "agents.create.sandbox",
        target_resource_type="draft",
        target_resource_id=str(proposal.id),
        target_resource_space_id=str(proposal.space_id),
    )


def _resolve_space_draft_route_action(
    *,
    body: SpaceDraftCreateRequest,
    session: SecureSession,
) -> ResolvedRouteAction:
    payload = _normalize_space_draft_payload(
        body,
        origin_space_id=str(session.space_id),
        origin_space_type="personal" if str(session.user.space_id) == str(session.space_id) else "non_personal",
    )
    return ResolvedRouteAction(
        action_family="drafts.spaces",
        action_id=payload["action_ids"][0],
        target_resource_type="draft",
        target_resource_space_id=str(payload.get("target_space_id") or session.space_id),
    )


async def _create_space_record(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    name: str,
    description: str | None,
    visibility: str,
) -> Space:
    base_slug = name.lower().replace(" ", "-").replace("_", "-")
    base_slug = "".join(c for c in base_slug if c.isalnum() or c == "-")
    slug = base_slug
    counter = 1
    while True:
        existing = await db.execute(select(Space).where(Space.slug == slug))
        if not existing.scalar_one_or_none():
            break
        slug = f"{base_slug}-{counter}"
        counter += 1

    new_space = Space(
        id=uuid.uuid4(),
        name=name,
        slug=slug,
        description=description,
        visibility=visibility,
        created_by=owner_user_id,
    )
    db.add(new_space)
    await db.flush()

    db.add(
        SpaceMembership(
            user_id=owner_user_id,
            space_id=new_space.id,
            role="admin",
        )
    )

    await db.execute(
        sa_text("SELECT set_config('app.current_space_id', :space_id, true)"),
        {"space_id": str(new_space.id)},
    )
    try:
        await ensure_space_agent_for_org(db, new_space)
    except Exception as exc:
        logger.warning("Space agent provisioning failed for draft-created space %s: %s", new_space.id, exc)

    return new_space


async def _join_public_space(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    target_space_id: str,
) -> Space:
    result = await db.execute(
        select(Space).where(
            Space.id == uuid.UUID(str(target_space_id)),
            Space.visibility == "public",
            Space.is_archived.is_(False),
        )
    )
    target_space = result.scalar_one_or_none()
    if not target_space:
        raise _draft_error(404, "target_boundary_denied", "Public space not found")

    existing = await db.execute(
        select(SpaceMembership).where(
            SpaceMembership.user_id == owner_user_id,
            SpaceMembership.space_id == target_space.id,
        )
    )
    if existing.scalar_one_or_none():
        return target_space

    db.add(
        SpaceMembership(
            user_id=owner_user_id,
            space_id=target_space.id,
            role="member",
        )
    )
    return target_space


async def _join_protected_space(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    target_space_id: str,
    invite_material: str,
) -> Space:
    invite_query = (
        select(SpaceInviteCode, Space)
        .join(Space, SpaceInviteCode.space_id == Space.id)
        .where(
            and_(
                SpaceInviteCode.invite_code == invite_material,
                SpaceInviteCode.active,
                or_(SpaceInviteCode.expires_at.is_(None), SpaceInviteCode.expires_at > datetime.utcnow()),
            )
        )
    )
    invite_result = await db.execute(invite_query)
    invite_data = invite_result.first()
    if not invite_data:
        raise _draft_error(404, "target_boundary_denied", "Invite material is invalid or expired")

    invite, target_space = invite_data
    if str(target_space.id) != str(target_space_id):
        raise _draft_error(400, "target_boundary_denied", "Invite material does not match the selected target space")
    if invite.max_uses and invite.current_uses >= invite.max_uses:
        raise _draft_error(400, "target_boundary_denied", "Invite has reached maximum uses")

    existing = await db.execute(
        select(SpaceMembership).where(
            SpaceMembership.user_id == owner_user_id,
            SpaceMembership.space_id == target_space.id,
        )
    )
    if existing.scalar_one_or_none():
        return target_space

    db.add(
        SpaceMembership(
            user_id=owner_user_id,
            space_id=target_space.id,
            role="member",
        )
    )
    invite.current_uses += 1
    return target_space


async def _execute_space_draft(
    db: AsyncSession,
    *,
    proposal: AgentManagementProposal,
) -> dict[str, Any]:
    payload = _copy_json(proposal.proposed_payload or {})
    draft_kind = payload.get("kind")
    owner_user_id = proposal.target_owner_user_id

    if draft_kind.startswith("spaces.create."):
        space_payload = payload.get("space") or {}
        created_space = await _create_space_record(
            db,
            owner_user_id=owner_user_id,
            name=space_payload["name"],
            description=space_payload.get("description"),
            visibility=space_payload.get("visibility") or "private",
        )
        return {
            "space": {
                "id": str(created_space.id),
                "name": created_space.name,
                "slug": created_space.slug,
                "description": created_space.description,
                "visibility": created_space.visibility,
                "created_at": created_space.created_at.isoformat() if created_space.created_at else None,
            }
        }

    join_payload = payload.get("join") or {}
    if draft_kind == "spaces.join.community":
        joined_space = await _join_public_space(
            db,
            owner_user_id=owner_user_id,
            target_space_id=join_payload["target_space_id"],
        )
    elif draft_kind == "spaces.join.protected":
        joined_space = await _join_protected_space(
            db,
            owner_user_id=owner_user_id,
            target_space_id=join_payload["target_space_id"],
            invite_material=join_payload["invite_material"],
        )
    else:
        raise _draft_error(400, "draft_execution_failed", f"Unsupported space draft type: {draft_kind}")

    return {
        "space": {
            "id": str(joined_space.id),
            "name": joined_space.name,
            "slug": joined_space.slug,
            "description": joined_space.description,
            "visibility": joined_space.visibility,
        }
    }


async def _create_owned_agent_record(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    body: "AgentCreateRequest",
    allow_management_rights: bool,
    default_space_id: str,
) -> Agent:
    from ...core.config import get_settings
    from ...core.models_config import DEFAULT_MODEL

    valid, error_msg = validate_agent_name(body.name)
    if not valid:
        raise HTTPException(status_code=400, detail=error_msg)

    target_space = body.space_id or default_space_id
    if body.space_id:
        await verify_space_membership(db, owner_id, body.space_id)

    existing = await db.execute(
        select(Agent).where(
            Agent.name == body.name,
            Agent.space_id == target_space,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Agent '{body.name}' already exists in this space")

    cloud_function_url = None
    origin = "mcp"
    if body.enable_cloud_agent:
        settings = get_settings()
        cloud_function_url = settings.agent_runner_stable_url
        origin = "cloud"

    agent_model = body.model or DEFAULT_MODEL
    enabled_tools = body.enabled_tools or get_enabled_tools_defaults()
    can_manage = body.can_manage_agents if allow_management_rights else False

    agent = Agent(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        user_id=owner_id,
        owner_type="user",
        owner_user_id=owner_id,
        space_id=target_space,
        origin=origin,
        status="active",
        agent_type="assistant",
        model=agent_model,
        cloud_function_url=cloud_function_url,
        enabled_tools=enabled_tools,
        can_manage_agents=can_manage,
    )
    db.add(agent)
    await db.flush()
    await grant_space_access(db, agent_id=agent.id, space_id=uuid.UUID(str(target_space)), is_default=True)
    return agent


async def _execute_agent_draft(
    db: AsyncSession,
    *,
    proposal: AgentManagementProposal,
    approver_user_id: uuid.UUID,
) -> dict[str, Any]:
    payload = _copy_json(proposal.proposed_payload or {})
    draft_kind = payload.get("kind")
    if draft_kind == "agents.create.with_credentials" or payload.get("credential"):
        raise _draft_error(410, "credential_flow_retired", "Connect agents through sponsored OAuth; see /auth.md")
    action_ids = payload.get("action_ids", [])
    agent_payload = payload.get("agent") or {}

    body = AgentCreateRequest(
        name=agent_payload["name"],
        description=agent_payload.get("description"),
        system_prompt=agent_payload.get("system_prompt"),
        model=agent_payload.get("model"),
        space_id=payload.get("target_space_id"),
        enable_cloud_agent=draft_kind == "agents.create.privileged",
        can_manage_agents="agents.create.with_management" in action_ids,
        enabled_tools=payload.get("enabled_tools"),
    )

    agent = await _create_owned_agent_record(
        db,
        owner_id=proposal.target_owner_user_id,
        body=body,
        allow_management_rights=True,
        default_space_id=str(payload.get("target_space_id")),
    )

    result: dict[str, Any] = {
        "agent": _serialize_managed_agent(agent),
    }

    return result


@router.post("/drafts/agents", status_code=201)
async def create_agent_draft(
    body: AgentDraftCreateRequest,
    request: Request,
    session: SecureSession = Depends(get_secure_session),
):
    owner_ctx = await _resolve_draft_owner_context(request, session)
    normalized_payload = _normalize_agent_draft_payload(
        body,
        default_space_id=str(body.target_space_id or owner_ctx.origin_space_id),
        origin_space_id=owner_ctx.origin_space_id,
        origin_space_type=owner_ctx.origin_space_type,
    )

    async with system_session_context() as system_ctx:
        if body.idempotency_key:
            existing = await system_ctx.db.execute(
                select(AgentManagementProposal).where(
                    AgentManagementProposal.idempotency_key == body.idempotency_key
                )
            )
            existing_proposal = existing.scalar_one_or_none()
            if existing_proposal:
                if str(existing_proposal.target_owner_user_id) != owner_ctx.owner_user_id:
                    raise _draft_error(409, "idempotency_conflict", "idempotency_key is already in use")
                return await _serialize_draft_envelope(system_ctx.db, existing_proposal)

        await _validate_agent_draft_payload(
            system_ctx.db,
            payload=normalized_payload,
            owner_user_id=owner_ctx.owner_user_id,
        )

        proposal = AgentManagementProposal(
            id=uuid.uuid4(),
            space_id=uuid.UUID(owner_ctx.origin_space_id),
            proposal_type=normalized_payload["kind"],
            status="under_review",
            requested_by_user_id=uuid.UUID(owner_ctx.requested_by_user_id) if owner_ctx.requested_by_user_id else None,
            requested_by_agent_id=uuid.UUID(owner_ctx.requested_by_agent_id) if owner_ctx.requested_by_agent_id else None,
            target_owner_user_id=uuid.UUID(owner_ctx.owner_user_id),
            target_space_id=uuid.UUID(str(normalized_payload["target_space_id"])),
            source_space_id=uuid.UUID(owner_ctx.origin_space_id),
            destination_space_id=uuid.UUID(str(normalized_payload["target_space_id"])),
            approval_requirements={
                "approval_required": True,
                "review_mode": "edit_before_approve",
                "actor_mode": owner_ctx.actor_mode,
            },
            proposed_payload=normalized_payload,
            payload_hash=_hash_json_payload(normalized_payload),
            expires_at=_now_utc() + timedelta(days=7),
            idempotency_key=body.idempotency_key,
            version=1,
        )
        system_ctx.db.add(proposal)
        await system_ctx.db.commit()
        await system_ctx.db.refresh(proposal)
        return await _serialize_draft_envelope(system_ctx.db, proposal)


@router.post("/drafts/spaces", status_code=201)
async def create_space_draft(
    body: SpaceDraftCreateRequest,
    request: Request,
    session: SecureSession = Depends(get_secure_session),
):
    owner_ctx = await _resolve_draft_owner_context(
        request,
        session,
        require_agent_management=False,
    )
    normalized_payload = _normalize_space_draft_payload(
        body,
        origin_space_id=owner_ctx.origin_space_id,
        origin_space_type=owner_ctx.origin_space_type,
    )

    async with system_session_context() as system_ctx:
        if body.idempotency_key:
            existing = await system_ctx.db.execute(
                select(AgentManagementProposal).where(
                    AgentManagementProposal.idempotency_key == body.idempotency_key
                )
            )
            existing_proposal = existing.scalar_one_or_none()
            if existing_proposal:
                if str(existing_proposal.target_owner_user_id) != owner_ctx.owner_user_id:
                    raise _draft_error(409, "idempotency_conflict", "idempotency_key is already in use")
                return await _serialize_draft_envelope(system_ctx.db, existing_proposal)

        await _validate_space_draft_payload(
            system_ctx.db,
            payload=normalized_payload,
            owner_user_id=owner_ctx.owner_user_id,
        )

        target_space_id = normalized_payload.get("target_space_id")
        proposal = AgentManagementProposal(
            id=uuid.uuid4(),
            space_id=uuid.UUID(owner_ctx.origin_space_id),
            proposal_type=normalized_payload["kind"],
            status="under_review",
            requested_by_user_id=uuid.UUID(owner_ctx.requested_by_user_id) if owner_ctx.requested_by_user_id else None,
            requested_by_agent_id=uuid.UUID(owner_ctx.requested_by_agent_id) if owner_ctx.requested_by_agent_id else None,
            target_owner_user_id=uuid.UUID(owner_ctx.owner_user_id),
            target_space_id=uuid.UUID(str(target_space_id)) if target_space_id else None,
            source_space_id=uuid.UUID(owner_ctx.origin_space_id),
            destination_space_id=uuid.UUID(str(target_space_id)) if target_space_id else None,
            approval_requirements={
                "approval_required": True,
                "review_mode": "edit_before_approve",
                "actor_mode": owner_ctx.actor_mode,
            },
            proposed_payload=normalized_payload,
            payload_hash=_hash_json_payload(normalized_payload),
            expires_at=_now_utc() + timedelta(days=7),
            idempotency_key=body.idempotency_key,
            version=1,
        )
        system_ctx.db.add(proposal)
        await system_ctx.db.commit()
        await system_ctx.db.refresh(proposal)
        return await _serialize_draft_envelope(system_ctx.db, proposal)


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    async with system_session_context() as system_ctx:
        proposal = await _load_draft_or_404(system_ctx.db, draft_id)
        _ensure_draft_actor_can_view(proposal=proposal, session=session)
        return await _serialize_draft_envelope(system_ctx.db, proposal)


@router.patch("/drafts/{draft_id}")
async def patch_draft(
    draft_id: str,
    body: DraftPatchRequest,
    request: Request,
    session: SecureSession = Depends(get_secure_session),
):
    approver_user_id = _require_human_approver(request, session)

    async with system_session_context() as system_ctx:
        proposal = await _load_draft_or_404(system_ctx.db, draft_id)
        if str(proposal.target_owner_user_id) != str(approver_user_id):
            raise _draft_error(403, "policy_denied", "Only the owning user can edit this draft")
        if proposal.status not in _DRAFT_STATUSES_REQUIRING_REVIEW:
            raise _draft_error(409, "draft_not_editable", f"Draft cannot be edited while {proposal.status}")
        if proposal.version != body.version:
            raise _draft_error(409, "draft_version_stale", "Draft version is stale")

        payload = _copy_json(proposal.proposed_payload or {})
        updated_payload = _apply_draft_patch_payload(payload=payload, changes=body.changes)
        if updated_payload.get("kind", "").startswith("agents."):
            await _validate_agent_draft_payload(
                system_ctx.db,
                payload=updated_payload,
                owner_user_id=str(proposal.target_owner_user_id),
            )
        else:
            await _validate_space_draft_payload(
                system_ctx.db,
                payload=updated_payload,
                owner_user_id=str(proposal.target_owner_user_id),
            )

        proposal.proposed_payload = updated_payload
        proposal.payload_hash = _hash_json_payload(updated_payload)
        proposal.version = int(proposal.version) + 1
        await system_ctx.db.commit()
        await system_ctx.db.refresh(proposal)
        return await _serialize_draft_envelope(system_ctx.db, proposal)


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: str,
    body: DraftDecisionRequest,
    request: Request,
    session: SecureSession = Depends(get_secure_session),
):
    approver_user_id = _require_human_approver(request, session)

    async with system_session_context() as system_ctx:
        proposal = await _load_draft_or_404(system_ctx.db, draft_id)
        if str(proposal.target_owner_user_id) != str(approver_user_id):
            raise _draft_error(403, "policy_denied", "Only the owning user can approve this draft")
        if proposal.version != body.version:
            raise _draft_error(409, "draft_version_stale", "Draft version is stale")
        if proposal.status == "executed":
            return await _serialize_draft_envelope(system_ctx.db, proposal)
        if proposal.status not in _DRAFT_STATUSES_REQUIRING_REVIEW:
            raise _draft_error(409, "approval_required", f"Draft cannot be approved while {proposal.status}")

        payload = _copy_json(proposal.proposed_payload or {})
        if payload.get("kind", "").startswith("agents."):
            await _validate_agent_draft_payload(
                system_ctx.db,
                payload=payload,
                owner_user_id=str(proposal.target_owner_user_id),
            )
        else:
            await _validate_space_draft_payload(
                system_ctx.db,
                payload=payload,
                owner_user_id=str(proposal.target_owner_user_id),
            )

        approval = AgentManagementApproval(
            proposal_id=proposal.id,
            approver_user_id=approver_user_id,
            approver_basis="owner",
            decision="approved",
            approval_patch=None,
            approved_payload_hash=proposal.payload_hash,
        )
        system_ctx.db.add(approval)

        proposal.status = "executing"
        proposal.version = int(proposal.version) + 1
        await system_ctx.db.flush()

        try:
            if payload.get("kind", "").startswith("agents."):
                execution_result = await _execute_agent_draft(
                    system_ctx.db,
                    proposal=proposal,
                    approver_user_id=approver_user_id,
                )
            else:
                execution_result = await _execute_space_draft(
                    system_ctx.db,
                    proposal=proposal,
                )
            updated_payload = _copy_json(payload)
            updated_payload["execution_result"] = execution_result
            proposal.proposed_payload = updated_payload
            proposal.payload_hash = _hash_json_payload(updated_payload)
            proposal.status = "executed"
            proposal.executed_at = _now_utc()
            await system_ctx.db.commit()

            # Emit agent_roster_changed when an agent draft lands so the
            # frontend roster/composer refresh without a hard F5. The
            # underlying create_agent service already emits for non-draft
            # paths; drafts go through _execute_agent_draft which bypasses
            # that service layer.
            if payload.get("kind", "").startswith("agents."):
                from app.services.agent_roster_events import publish_agent_roster_changed
                agent_info = (execution_result or {}).get("agent") or {}
                target_space_id = payload.get("target_space_id")
                if target_space_id:
                    await publish_agent_roster_changed(
                        space_id=str(target_space_id),
                        action="approved",
                        agent_id=agent_info.get("id"),
                        agent_name=agent_info.get("name"),
                    )
        except HTTPException:
            await system_ctx.db.rollback()
            failed_proposal = await _load_draft_or_404(system_ctx.db, draft_id)
            failed_proposal.status = "failed"
            await system_ctx.db.commit()
            raise
        except Exception as exc:
            await system_ctx.db.rollback()
            failed_proposal = await _load_draft_or_404(system_ctx.db, draft_id)
            failed_proposal.status = "failed"
            await system_ctx.db.commit()
            raise _draft_error(500, "draft_execution_failed", str(exc)) from exc

        await system_ctx.db.refresh(proposal)
        return await _serialize_draft_envelope(system_ctx.db, proposal)


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(
    draft_id: str,
    body: DraftDecisionRequest,
    request: Request,
    session: SecureSession = Depends(get_secure_session),
):
    approver_user_id = _require_human_approver(request, session)

    async with system_session_context() as system_ctx:
        proposal = await _load_draft_or_404(system_ctx.db, draft_id)
        if str(proposal.target_owner_user_id) != str(approver_user_id):
            raise _draft_error(403, "policy_denied", "Only the owning user can reject this draft")
        if proposal.version != body.version:
            raise _draft_error(409, "draft_version_stale", "Draft version is stale")
        if proposal.status not in _DRAFT_STATUSES_REQUIRING_REVIEW:
            raise _draft_error(409, "approval_required", f"Draft cannot be rejected while {proposal.status}")

        approval = AgentManagementApproval(
            proposal_id=proposal.id,
            approver_user_id=approver_user_id,
            approver_basis="owner",
            decision="rejected",
            approval_patch=None,
            approved_payload_hash=proposal.payload_hash,
        )
        system_ctx.db.add(approval)
        proposal.status = "rejected"
        proposal.version = int(proposal.version) + 1
        await system_ctx.db.commit()
        await system_ctx.db.refresh(proposal)
        return await _serialize_draft_envelope(system_ctx.db, proposal)


@router.post("/drafts/{draft_id}/cancel")
async def cancel_draft(
    draft_id: str,
    body: DraftDecisionRequest,
    request: Request,
    session: SecureSession = Depends(get_secure_session),
):
    approver_user_id = _require_human_approver(request, session)

    async with system_session_context() as system_ctx:
        proposal = await _load_draft_or_404(system_ctx.db, draft_id)
        if str(proposal.target_owner_user_id) != str(approver_user_id):
            raise _draft_error(403, "policy_denied", "Only the owning user can cancel this draft")
        if proposal.version != body.version:
            raise _draft_error(409, "draft_version_stale", "Draft version is stale")
        if proposal.status in _DRAFT_STATUSES_TERMINAL:
            return await _serialize_draft_envelope(system_ctx.db, proposal)
        proposal.status = "cancelled"
        proposal.version = int(proposal.version) + 1
        await system_ctx.db.commit()
        await system_ctx.db.refresh(proposal)
        return await _serialize_draft_envelope(system_ctx.db, proposal)


# ---------------------------------------------------------------------------
# Agent Management (CRUD)
# ---------------------------------------------------------------------------

class AgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=50)
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    space_id: str | None = Field(None, description="Target space. Defaults to session space.")
    enable_cloud_agent: bool = False
    can_manage_agents: bool = False
    enabled_tools: dict[str, bool] | None = None


class AgentUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    status: str | None = None
    avatar_url: str | None = Field(None, max_length=512)
    can_manage_agents: bool | None = None
    enabled_tools: dict[str, bool] | None = None


class AgentManagementResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    origin: str
    agent_type: str | None = None
    status: str
    model: str | None = None
    avatar_url: str | None = None
    space_id: str
    can_manage_agents: bool = False
    enabled_tools: dict[str, bool] | None = None
    created_at: str | None = None


def _resolve_create_agent_action(
    *,
    body: AgentCreateRequest,
    session: SecureSession,
) -> ResolvedRouteAction:
    target_space_id = str(body.space_id or session.space_id)
    if body.can_manage_agents:
        action_id = "agents.create.with_management"
    elif body.enable_cloud_agent:
        action_id = "agents.create.privileged"
    else:
        action_id = "agents.create.sandbox"
    return ResolvedRouteAction(
        action_family="agents.management",
        action_id=action_id,
        target_resource_type="agent",
        target_resource_space_id=target_space_id,
    )


def _resolve_update_managed_agent_action(
    *,
    identifier: str,
    body: AgentUpdateRequest,
) -> ResolvedRouteAction:
    action_id = (
        "agents.delegate.management"
        if body.can_manage_agents is not None
        else "agents.update.any_owned"
    )
    return ResolvedRouteAction(
        action_family="agents.management",
        action_id=action_id,
        target_resource_type="agent",
        target_resource_id=identifier,
    )


def _serialize_managed_agent(agent: Agent) -> dict:
    return {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "origin": agent.origin,
        "agent_type": agent.agent_type,
        "status": agent.status,
        "model": display_model(agent),
        "avatar_url": getattr(agent, "avatar_url", None),
        "space_id": str(agent.space_id),
        "can_manage_agents": agent.can_manage_agents,
        "enabled_tools": build_enabled_tools_from_agent(agent),
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }


async def _authorize_agent_management(session: SecureSession) -> uuid.UUID:
    """Authorize agent management. Returns the owning user_id.

    - No agent header (user-direct): return session.user.id
    - Agent header: check can_manage_agents, not space_agent origin
    """
    if not session.agent_id:
        return session.user.id

    result = await session.db.execute(
        select(Agent).where(Agent.id == uuid.UUID(session.agent_id))
    )
    calling_agent = result.scalar_one_or_none()
    if not calling_agent:
        raise HTTPException(status_code=403, detail="Calling agent not found")
    if not calling_agent.can_manage_agents:
        raise HTTPException(status_code=403, detail="This agent does not have can_manage_agents permission")
    if calling_agent.origin == "space_agent":
        raise HTTPException(status_code=403, detail="Space agents cannot manage other agents")

    return session.user.id


async def _resolve_agent_by_identifier(
    db, identifier: str, user_id: uuid.UUID,
) -> Agent | None:
    """Resolve agent by UUID or name within user's agents."""
    try:
        agent_uuid = uuid.UUID(identifier)
        result = await db.execute(
            select(Agent).where(Agent.id == agent_uuid, Agent.user_id == user_id)
        )
        return result.scalar_one_or_none()
    except ValueError:
        pass

    result = await db.execute(
        select(Agent).where(Agent.name == identifier, Agent.user_id == user_id)
    )
    return result.scalar_one_or_none()


@router.post("/agents")
async def create_agent(
    body: AgentCreateRequest,
    session: SecureSession = Depends(get_secure_session),
):
    """Create a new agent owned by the authenticated user."""
    owner_id = await _authorize_agent_management(session)
    agent = await _create_owned_agent_record(
        session.db,
        owner_id=owner_id,
        body=body,
        allow_management_rights=not session.agent_id,
        default_space_id=str(session.space_id),
    )
    await session.db.commit()

    logger.info(
        "AGENT_CREATED agent=%s name=%s owner=%s space=%s origin=%s",
        agent.id,
        agent.name,
        owner_id,
        agent.space_id,
        agent.origin,
    )

    return _serialize_managed_agent(agent)


@router.get("/agents/manage/{identifier}")
async def get_agent(
    identifier: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Get agent by name or UUID. Must be owned by the authenticated user."""
    agent = await _resolve_agent_by_identifier(session.db, identifier, session.user.id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{identifier}' not found")
    return _serialize_managed_agent(agent)


@router.put("/agents/manage/{identifier}")
async def update_agent(
    identifier: str,
    body: AgentUpdateRequest,
    session: SecureSession = Depends(get_secure_session),
):
    """Update an agent. Must be owned by the authenticated user."""
    owner_id = await _authorize_agent_management(session)

    agent = await _resolve_agent_by_identifier(session.db, identifier, owner_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{identifier}' not found")

    if body.name is not None:
        valid, error_msg = validate_agent_name(body.name)
        if not valid:
            raise HTTPException(status_code=400, detail=error_msg)
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.system_prompt is not None:
        agent.system_prompt = body.system_prompt
    if body.model is not None:
        agent.model = body.model
    if body.status is not None:
        if body.status not in ("active", "inactive"):
            raise HTTPException(status_code=400, detail="Status must be 'active' or 'inactive'")
        agent.status = body.status
    if body.avatar_url is not None:
        agent.avatar_url = body.avatar_url
    if body.enabled_tools is not None:
        agent.enabled_tools = body.enabled_tools
    # can_manage_agents only changeable by user-direct
    if body.can_manage_agents is not None:
        if session.agent_id:
            raise HTTPException(status_code=403, detail="Only user-direct calls can change can_manage_agents")
        agent.can_manage_agents = body.can_manage_agents

    await session.db.commit()

    logger.info("AGENT_UPDATED agent=%s name=%s by=%s", agent.id, agent.name, owner_id)
    return _serialize_managed_agent(agent)


@router.delete("/agents/manage/{identifier}")
async def delete_agent(
    identifier: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Delete an agent. Must be owned by the authenticated user."""
    owner_id = await _authorize_agent_management(session)

    agent = await _resolve_agent_by_identifier(session.db, identifier, owner_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{identifier}' not found")

    if agent.origin == "space_agent":
        raise HTTPException(status_code=403, detail="Cannot delete space agents")

    agent_id = str(agent.id)
    agent_name = agent.name

    # Delete agent (agent_space_access cascades via FK)
    await session.db.execute(
        sa_delete(Agent).where(Agent.id == agent.id)
    )
    await session.db.commit()

    logger.info("AGENT_DELETED agent=%s name=%s by=%s", agent_id, agent_name, owner_id)
    return {"message": f"Agent '{agent_name}' deleted", "agent_id": agent_id}


# =========================================================================
# 8. TOOL CALLS (internal — MCP server fire-and-forget)
# =========================================================================

@router.post("/tool-calls", status_code=202)
async def create_tool_call(
    body: ToolCallNotification,
    request: Request,
    user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Record a tool call notification from MCP server middleware.

    Internal endpoint — called fire-and-forget by the MCP server after each
    tool invocation. Stores an audit record and broadcasts an SSE event so
    the frontend can update widgets in real time.
    """
    from sqlalchemy.exc import IntegrityError
    from ...models.tool_call import ToolCall

    auth_agent_id = _authenticated_agent_id(user, session)
    auth_agent_name = _authenticated_agent_name(user, session)
    auth_space_id = (
        getattr(user, "_effective_space_id", None)
        or session.space_id
    )

    if not auth_agent_id:
        raise HTTPException(
            status_code=403,
            detail="tool_calls.record requires authenticated agent context",
        )

    resolved_agent_id = body.agent_id
    resolved_agent_name = body.agent_name
    resolved_space_id = body.space_id

    if auth_agent_id:
        if body.agent_id and str(body.agent_id) != str(auth_agent_id):
            logger.warning(
                "TOOL_CALL_AGENT_ID_MISMATCH path=%s body=%s auth=%s using auth context",
                request.url.path,
                body.agent_id,
                auth_agent_id,
            )
        resolved_agent_id = str(auth_agent_id)

    if auth_agent_name:
        if body.agent_name and body.agent_name != auth_agent_name:
            logger.warning(
                "TOOL_CALL_AGENT_NAME_MISMATCH path=%s body=%s auth=%s using auth context",
                request.url.path,
                body.agent_name,
                auth_agent_name,
            )
        resolved_agent_name = auth_agent_name

    if auth_space_id:
        if body.space_id and str(body.space_id) != str(auth_space_id):
            logger.warning(
                "TOOL_CALL_SPACE_MISMATCH path=%s body=%s auth=%s using auth context",
                request.url.path,
                body.space_id,
                auth_space_id,
            )
        resolved_space_id = str(auth_space_id)

    # Canonicalize the space_id once before *any* downstream code uses it.
    # ToolCall.space_id is a UUID column that round-trips through str() as
    # canonical (lowercase, hyphenated). The Redis cache key in
    # tool_call_cache.py is built directly from this string, so a non-canonical
    # input here would write under one key and be read under another by paths
    # that pull space_id from the DB row (canonical) — silently dropping
    # initial_data. The cache module also normalizes defensively, but we
    # canonicalize here so DB write, cache write, and SSE broadcast all agree.
    if resolved_space_id:
        try:
            resolved_space_id = str(uuid.UUID(str(resolved_space_id)))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid space_id format",
            ) from exc

    record = ToolCall(
        tool_call_id=body.tool_call_id,
        tool_name=body.tool_name,
        tool_action=body.tool_action,
        resource_uri=body.resource_uri,
        arguments_hash=body.arguments_hash,
        kind=body.kind,
        arguments=body.arguments,
        status=body.status,
        duration_ms=body.duration_ms,
        agent_name=resolved_agent_name,
        agent_id=uuid.UUID(resolved_agent_id) if resolved_agent_id else None,
        space_id=uuid.UUID(resolved_space_id) if resolved_space_id else None,
        message_id=uuid.UUID(body.message_id) if body.message_id else None,
        correlation_id=body.correlation_id,
    )
    db.add(record)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate tool_call_id")

    if body.initial_data and resolved_space_id:
        from ...core.tool_call_cache import store_tool_call_initial_data

        await store_tool_call_initial_data(
            body.tool_call_id,
            body.initial_data,
            space_id=resolved_space_id,
        )

    # Broadcast SSE so frontend/CLI watchers can update tool progress in real time
    if resolved_space_id:
        try:
            tool_calls_total = None
            if body.message_id:
                count_result = await db.execute(
                    select(sa_func.count())
                    .select_from(ToolCall)
                    .where(
                        ToolCall.message_id == uuid.UUID(body.message_id),
                        ToolCall.space_id == uuid.UUID(resolved_space_id),
                    )
                )
                tool_calls_total = count_result.scalar_one()

            progress_message = body.tool_name
            if tool_calls_total:
                suffix = "tool" if tool_calls_total == 1 else "tools"
                progress_message = f"Working... {tool_calls_total} {suffix}"

            await redis_sse_broker.publish(
                space_id=resolved_space_id,
                event="agent_progress",
                data={
                    "dispatch_id": body.correlation_id,
                    "agent_id": resolved_agent_id,
                    "agent_name": resolved_agent_name,
                    "message_id": body.message_id,
                    "status": "tool_progress",
                    "message": progress_message,
                    "tool": body.tool_name,
                    "tool_name": body.tool_name,
                    "tool_action": body.tool_action,
                    "tool_call_id": body.tool_call_id,
                    "tool_calls": tool_calls_total,
                },
            )

            await redis_sse_broker.publish(
                space_id=resolved_space_id,
                event="tool_call_completed",
                data={
                    "tool_call_id": body.tool_call_id,
                    "tool_name": body.tool_name,
                    "tool_action": body.tool_action,
                    "resource_uri": body.resource_uri,
                    "kind": body.kind,
                    "arguments": body.arguments,
                    "initial_data": body.initial_data,
                    "status": body.status,
                    "agent_name": resolved_agent_name,
                    "agent_id": resolved_agent_id,
                    "message_id": body.message_id,
                    "tool_calls": tool_calls_total,
                },
            )
        except Exception:
            # SSE broadcast is best-effort — don't fail the request
            logger.warning("TOOL_CALL_SSE_FAIL tool_call_id=%s", body.tool_call_id, exc_info=True)

    logger.info(
        "TOOL_CALL_RECORDED tool_call_id=%s tool=%s action=%s agent=%s space=%s",
        body.tool_call_id, body.tool_name, body.tool_action, resolved_agent_name, resolved_space_id,
    )
    return {"ok": True, "tool_call_id": body.tool_call_id}


@router.get("/tool-calls/{tool_call_id}")
async def get_tool_call(
    tool_call_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Retrieve a tool call audit record by tool_call_id."""
    from ...core.tool_call_cache import load_tool_call_initial_data
    from ...models.tool_call import ToolCall

    if not session.space_id:
        raise HTTPException(status_code=403, detail="Authenticated space context required")

    try:
        effective_space_id = uuid.UUID(str(session.space_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="Invalid authenticated space context") from exc

    result = await session.db.execute(
        select(ToolCall).where(
            ToolCall.tool_call_id == tool_call_id,
            ToolCall.space_id == effective_space_id,
        )
    )
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(status_code=404, detail="Tool call not found")

    initial_data = await load_tool_call_initial_data(
        tool_call_id,
        space_id=str(effective_space_id),
    )

    return {
        "tool_call_id": record.tool_call_id,
        "tool_name": record.tool_name,
        "tool_action": record.tool_action,
        "resource_uri": record.resource_uri,
        "kind": record.kind,
        "arguments": record.arguments,
        "initial_data": initial_data,
        "status": record.status,
        "duration_ms": record.duration_ms,
        "agent_name": record.agent_name,
        "agent_id": str(record.agent_id) if record.agent_id else None,
        "space_id": str(record.space_id) if record.space_id else None,
        "message_id": str(record.message_id) if record.message_id else None,
        "correlation_id": record.correlation_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


# ---------------------------------------------------------------------------
# Unified /api/v1 action metadata
# ---------------------------------------------------------------------------

declare_route_action(
    send_message,
    action_family="messages",
    action_id="messages.send",
    target_resource_type="message",
)
declare_route_action(
    edit_message,
    action_family="messages",
    action_id="messages.update.self",
    target_resource_type="message",
)
declare_route_action(
    delete_message,
    action_family="messages",
    action_id="messages.delete.self",
    target_resource_type="message",
)
declare_route_action(
    add_reaction,
    action_family="messages",
    action_id="messages.react",
    target_resource_type="message",
)
declare_route_action(
    resolve_widget,
    action_family="widgets",
    action_id="widgets.resolve",
    target_resource_type="message",
)
declare_route_action(
    create_credential_fingerprint,
    action_family="credentials",
    action_id="credentials.fingerprint.record",
    target_resource_type="credential_fingerprint",
)
declare_route_action(
    agent_heartbeat,
    action_family="agents.runtime",
    action_id="agents.heartbeat",
    target_resource_type="agent",
)
declare_route_action(
    agent_processing_status,
    action_family="agents.runtime",
    action_id="agents.heartbeat",
    target_resource_type="agent",
)
declare_route_action(
    update_agent_me,
    action_family="agents.profile",
    action_id="agents.update.self_owned",
    target_resource_type="agent",
)
declare_route_action(
    patch_agent_profile,
    action_family="agents.profile",
    action_id="agents.update.self_owned",
    target_resource_type="agent",
)
declare_route_action(
    put_agent_profile,
    action_family="agents.profile",
    action_id="agents.update.self_owned",
    target_resource_type="agent",
)
declare_route_action(
    store_memory,
    action_family="agents.memory",
    action_id="agents.memory.write",
    target_resource_type="agent",
)
declare_route_action(
    follow_agent,
    action_family="agents.social",
    action_id="agents.follow.create",
    target_resource_type="agent_relationship",
)
declare_route_action(
    unfollow_agent,
    action_family="agents.social",
    action_id="agents.follow.delete",
    target_resource_type="agent_relationship",
)
declare_route_action(
    set_context,
    action_family="context",
    action_id="context.write",
    target_resource_type="context_entry",
)
declare_route_action(
    delete_context,
    action_family="context",
    action_id="context.delete",
    target_resource_type="context_entry",
)
declare_route_action(
    search_messages,
    action_family="search",
    action_id="search.messages",
    target_resource_type="message",
)
declare_route_action(
    create_agent_draft,
    action_family="drafts.agents",
    resolver=_resolve_agent_draft_route_action,
    target_resource_type="draft",
)
declare_route_action(
    create_space_draft,
    action_family="drafts.spaces",
    resolver=_resolve_space_draft_route_action,
    target_resource_type="draft",
)
declare_route_action(
    patch_draft,
    action_family="drafts.lifecycle",
    resolver=_resolve_existing_draft_route_action,
    target_resource_type="draft",
)
declare_route_action(
    approve_draft,
    action_family="drafts.lifecycle",
    resolver=_resolve_existing_draft_route_action,
    target_resource_type="draft",
)
declare_route_action(
    reject_draft,
    action_family="drafts.lifecycle",
    resolver=_resolve_existing_draft_route_action,
    target_resource_type="draft",
)
declare_route_action(
    cancel_draft,
    action_family="drafts.lifecycle",
    resolver=_resolve_existing_draft_route_action,
    target_resource_type="draft",
)
declare_route_action(
    create_agent,
    action_family="agents.management",
    resolver=_resolve_create_agent_action,
    target_resource_type="agent",
)
declare_route_action(
    update_agent,
    action_family="agents.management",
    resolver=_resolve_update_managed_agent_action,
    target_resource_type="agent",
)
declare_route_action(
    delete_agent,
    action_family="agents.management",
    action_id="agents.delete.owned",
    target_resource_type="agent",
)
declare_route_action(
    create_tool_call,
    action_family="tool_calls",
    action_id="tool_calls.record",
    target_resource_type="tool_call",
)
