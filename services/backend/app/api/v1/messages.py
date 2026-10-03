"""
Message Management API endpoints
Handles message CRUD operations, channels, and threading
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as redis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import selectinload

from typing import Literal

from ...core.actor import CAP_MESSAGES_SEND, Actor
from ...core.rls import SecureSession, get_secure_session
from ...models.message import Message
from ...services.feedback_service import FeedbackService
from ...services.messages_service import MessagesService
from ...services.redis_sse_broker import redis_sse_broker  # Production Redis Streams

# Redis client for unread tracking
redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6380/0"), decode_responses=True)

router = APIRouter(prefix="/api", tags=["messages"])

logger = logging.getLogger(__name__)


def _accepted_attachments_from_metadata(message_metadata: dict | None) -> list[dict]:
    if not isinstance(message_metadata, dict):
        return []
    attachments = message_metadata.get("accepted_attachments")
    return attachments if isinstance(attachments, list) else []


async def _link_message_attachments_before_broadcast(
    *,
    db,
    accepted_attachments: list[dict],
    upload_owner_id: UUID | None,
    space_id: UUID,
    message_id: UUID,
    logger: logging.Logger,
) -> None:
    """Best-effort attachment link hook that runs before live message events."""
    if not accepted_attachments:
        return

    try:
        from app.api.v1.attachment_linking import link_user_uploads_to_message

        updated = await link_user_uploads_to_message(
            db,
            accepted_attachments=accepted_attachments,
            upload_owner_id=upload_owner_id,
            space_id=space_id,
            message_id=message_id,
            logger=logger,
        )
        if updated:
            await db.commit()
    except Exception as exc:
        logger.warning(
            "Failed to link attachments to message %s before broadcast: %s",
            message_id,
            exc,
        )
        # Best-effort: message metadata still carries context/file pointers.


async def _get_agent_wait_state(agent_id: str) -> dict | None:
    """Check Redis for agent presence/wait state. Returns wait data or None.

    Uses same key format as MCP server: agent:presence:{agent_id}
    Data contains: since, timeout_at, waiting_for, type (send/listen), wait_mode
    """
    try:
        presence_key = f"agent:presence:{agent_id}"
        presence_json = await redis_client.get(presence_key)
        if presence_json:
            presence_data = json.loads(presence_json)
            _calculate_ttl_remaining(presence_data)
            return presence_data
    except Exception as e:
        logger.warning(f"Failed to get wait state for agent {agent_id}: {e}")
    return None


def _calculate_ttl_remaining(presence_data: dict) -> None:
    """Calculate TTL remaining from timeout_at and add to presence_data in-place."""
    timeout_at_str = presence_data.get("timeout_at")
    if timeout_at_str:
        try:
            timeout_at = datetime.fromisoformat(timeout_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            remaining = (timeout_at - now).total_seconds()
            presence_data["ttl_remaining"] = max(0, int(remaining))
        except (ValueError, TypeError):
            presence_data["ttl_remaining"] = 0
    else:
        presence_data["ttl_remaining"] = 0


async def _batch_get_agent_wait_states(agent_ids: set[str]) -> dict[str, dict]:
    """Batch fetch wait states for multiple agents using mget.

    Args:
        agent_ids: Set of agent IDs to fetch

    Returns:
        Dict mapping agent_id -> wait state data (only for agents with active wait state)
    """
    if not agent_ids:
        return {}

    try:
        keys = [f"agent:presence:{aid}" for aid in agent_ids]
        values = await redis_client.mget(keys)

        wait_states = {}
        for agent_id, val in zip(agent_ids, values):
            if val:
                try:
                    presence_data = json.loads(val)
                    _calculate_ttl_remaining(presence_data)
                    wait_states[agent_id] = presence_data
                except (json.JSONDecodeError, TypeError):
                    pass
        return wait_states
    except Exception as e:
        logger.warning(f"Failed to batch get wait states: {e}")
        return {}


# Pydantic models for API
class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1)
    channel: str = Field("main", max_length=50)
    message_type: str = Field(
        "message",
        pattern="^(message|response|reply|task|system|urgent|agent_pause|image|video|audio)$"
    )
    parent_id: str | None = None
    metadata: dict | None = None


class MessageUpdate(BaseModel):
    content: str | None = Field(None, min_length=1)
    read_state: str | None = Field(None, pattern="^(delivered|ack|read)$")
    metadata: dict | None = None


# ── Card / UI envelope models (FRONTEND-005 contract) ────────────────

class CardEnvelope(BaseModel):
    card_id: str
    type: str  # "receipt", "task", "agent", "context", "confirmation", "result", "handoff_select"
    version: int = 1
    replace_in_place: bool = False
    replace_target: str | None = None
    handoff_id: str | None = None
    payload: dict = {}

class UIPayload(BaseModel):
    cards: list[CardEnvelope] = []
    replace_target: str | None = None

class RoutingPayload(BaseModel):
    mode: str | None = None  # "space_agent", "direct", "broadcast", "system"
    target_id: str | None = None
    target_name: str | None = None
    target_type: str | None = None


class MessageResponse(BaseModel):
    id: str
    content: str
    channel: str
    message_type: str
    parent_id: str | None = None
    read_state: str
    cursor_position: int
    metadata: dict | None = None
    ai_summary: str | None = None  # AI-generated summary (cached in database)
    summarized_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    author: dict  # User or agent info
    author_id: str | None = None
    author_type: str | None = None
    replies_count: int = 0
    reactions: dict[str, int] | None = None  # Emoji -> count mapping (e.g., {"👍": 5, "🚀": 2})
    # Intelligence Data
    spam_score: float | None = None
    toxicity_score: float | None = None
    quality_score: float | None = None
    # Waiting State (for agent timer UI)
    waiting_for_response: bool = False
    waiting_since: datetime | None = None
    waiting_ttl_seconds: int | None = None
    waiting_for: str | None = None  # Agent name they're waiting for
    routed_to: list[dict] = []
    routing_story: list[str] = []
    # FRONTEND-005 contract fields
    conversation_id: str | None = None
    space_id: str | None = None
    sender_type: str | None = None  # "user" | "agent" | "space_agent" | "system"
    display_name: str | None = None
    routing: RoutingPayload | None = None
    ui: UIPayload | None = None
    received_by: dict | None = None
    accepted_attachments: list[dict] = []
    feedback_summary: dict | None = None  # {"thumbs_up": 3, "thumbs_down": 1, "user_vote": 1}


# ── Feedback models ──────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    vote: Literal[1, -1]  # thumbs up or down
    comment: str | None = None

class FeedbackResponse(BaseModel):
    id: str
    message_id: str
    agent_id: str
    vote: int
    comment: str | None
    created_at: datetime

class FeedbackSummary(BaseModel):
    message_id: str
    thumbs_up: int
    thumbs_down: int
    user_vote: int | None


class SendReceipt(BaseModel):
    """Rich receipt returned by POST /api/messages for optimistic UI."""
    message_id: str
    conversation_id: str
    space_id: str
    received_by: list[dict] = []
    routing: RoutingPayload | None = None
    action_taken: str | None = None
    result_summary: str | None = None
    watching: bool = False
    handoff_id: str | None = None
    ui: UIPayload | None = None



class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    total: int
    limit: int
    offset: int
    channel: str
    has_more: bool = False
    next_cursor: str | None = None  # ISO timestamp of oldest message for loading more
    unread_count: int = 0
    unread_message_ids: list[str] = []
    marked_read_count: int = 0


# ── Widget resolver schemas (MCP App Widget) ─────────────────────────

class WidgetResolveRequest(BaseModel):
    space_id: str
    message_id: str
    resource_uri: str
    tool_name: str | None = None
    tool_call_id: str | None = None


class WidgetResolveResponse(BaseModel):
    html: str | None = None
    resource_url: str | None = None
    resource_mime_type: str | None = None
    title: str | None = None
    csp: str | None = None


def _extract_ui_payload(message_metadata: dict | None) -> UIPayload | None:
    """Extract ui.cards from message metadata into typed UIPayload."""
    if not isinstance(message_metadata, dict):
        return None
    raw_ui = message_metadata.get("ui")
    if not isinstance(raw_ui, dict):
        return None
    raw_cards = raw_ui.get("cards")
    if not isinstance(raw_cards, list):
        return UIPayload(replace_target=raw_ui.get("replace_target"))
    cards = []
    for c in raw_cards:
        if isinstance(c, dict) and "card_id" in c and "type" in c:
            cards.append(CardEnvelope(
                card_id=c["card_id"],
                type=c["type"],
                version=c.get("version", 1),
                replace_in_place=c.get("replace_in_place", False),
                replace_target=c.get("replace_target"),
                handoff_id=c.get("handoff_id"),
                payload=c.get("payload", {}),
            ))
    if not cards and not raw_ui.get("replace_target"):
        return None
    return UIPayload(cards=cards, replace_target=raw_ui.get("replace_target"))


def _compute_sender_type(message, author_type: str | None) -> str:
    """Determine sender_type with explicit space_agent distinction."""
    if author_type == "agent":
        agent = getattr(message, "agent", None)
        if agent and getattr(agent, "origin", None) == "space_agent":
            return "space_agent"
        return "agent"
    if author_type == "user":
        return "user"
    # System messages (no user, no agent)
    msg_type = getattr(message, "message_type", "")
    if msg_type == "system":
        return "system"
    return author_type or "user"


def _compute_routing(message_metadata: dict | None, routed_to: list[dict]) -> RoutingPayload | None:
    """Build routing payload from metadata and routing targets."""
    if not routed_to:
        return None
    first = routed_to[0]
    agent_type = first.get("type", first.get("source", ""))
    mode = "space_agent" if agent_type == "space_agent" else "direct"
    return RoutingPayload(
        mode=mode,
        target_id=first.get("agent_id"),
        target_name=first.get("agent_name") or first.get("display_name"),
        target_type=agent_type or None,
    )


def _routing_payload_from_metadata(message_metadata: dict | None) -> tuple[list[dict], list[str]]:
    """Extract stable, UI-friendly routing info from message metadata."""
    routed_to: list[dict] = []
    summary: list[str] = []

    if not isinstance(message_metadata, dict):
        return routed_to, summary

    raw_story = message_metadata.get("routing_story")
    if not isinstance(raw_story, dict):
        return routed_to, summary

    raw_targets = raw_story.get("targets")
    if not isinstance(raw_targets, list):
        return routed_to, summary

    seen: set[str] = set()
    for item in raw_targets:
        if not isinstance(item, dict):
            continue

        agent_id = str(item.get("agent_id")).strip() if item.get("agent_id") else ""
        if not agent_id or agent_id in seen:
            continue

        agent_name = item.get("agent_name")
        if not isinstance(agent_name, str) or not agent_name.strip():
            agent_name = "unknown"
        display_name = item.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = f"@{agent_name}"

        routed_to.append(
            {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "source": item.get("source") or "router",
                "display_name": display_name,
            }
        )
        summary.append(display_name)
        seen.add(agent_id)

    return routed_to, summary


@router.get("/messages", response_model=MessageListResponse)
async def list_messages(
    channel: str = Query("main", description="Channel to fetch messages from"),
    # Enforce smaller page size for performance with media content
    limit: int = Query(50, le=50, description="Number of messages to return (max 50)"),
    offset: int = Query(0, ge=0, description="Number of messages to skip (deprecated, use before)"),
    before: str | None = Query(
        None,
        description="Cursor for pagination. Format: 'ISO_timestamp|message_id' (e.g., '2024-01-15T10:30:00|uuid'). Legacy timestamp-only format supported."
    ),
    message_type: str | None = Query(
        None,
        pattern="^(message|response|reply|task|system|urgent|agent_pause|image|video|audio)$",
        description="Filter by specific message type"
    ),
    media_type: str | None = Query(
        None,
        pattern="^(all|image|video|audio|media)$",
        description="Filter by media type: all (no filter), image, video, audio, or media (any media type)"
    ),
    unread_only: bool = Query(False, description="Show only unread messages"),
    mark_read: bool = Query(False, description="Mark returned unread messages as read for the current user"),
    since: datetime | None = Query(None, description="Show messages since this timestamp"),
    session: SecureSession = Depends(get_secure_session),
):
    """List messages using the unified service layer"""
    try:
        # Build Actor from SecureSession (RLS context already set)
        actor = Actor(
            id=session.user.id, type="human", space_id=session.space_id, capabilities={CAP_MESSAGES_SEND}
        )

        svc = MessagesService(
            db=session.db,
            redis_client=redis_client,
            sse_broker=redis_sse_broker,  # Use Redis Streams for cross-process delivery
        )

        # Server-side hard cap for performance (50 max for media content)
        safe_limit = max(1, min(int(limit or 1), 50))

        # Parse compound cursor (format: "timestamp|message_id")
        before_timestamp: datetime | None = None
        before_message_id: UUID | None = None
        if before:
            try:
                if "|" in before:
                    # New compound cursor format
                    ts_part, id_part = before.split("|", 1)
                    before_timestamp = datetime.fromisoformat(ts_part)
                    before_message_id = uuid.UUID(id_part)
                else:
                    # Legacy timestamp-only format (backward compatibility)
                    before_timestamp = datetime.fromisoformat(before)
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse cursor '{before}': {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid cursor format. Expected 'ISO_timestamp|message_id' or ISO timestamp."
                )

        # Determine message type filter based on media_type parameter
        effective_message_type = message_type
        media_types_filter = None
        if media_type and media_type != "all":
            if media_type == "media":
                # Filter to any media type
                media_types_filter = ["image", "video", "audio"]
            else:
                # Filter to specific media type
                effective_message_type = media_type

        unread_list: list[str] = []
        unread_uuid_ids: set[UUID] = set()
        unread_cache_loaded = False
        try:
            unread_set = await redis_client.smembers(f"unread:{session.user.id}")
            unread_cache_loaded = True
            unread_list = list(unread_set) if unread_set else []
            for raw_id in unread_list:
                try:
                    unread_uuid_ids.add(uuid.UUID(str(raw_id)))
                except (ValueError, TypeError):
                    logger.warning("Ignoring invalid unread message id for user=%s: %r", session.user.id, raw_id)
        except Exception as e:
            logger.warning("Failed to load unread messages", exc_info=e)

        if unread_only and unread_cache_loaded and not unread_uuid_ids:
            return MessageListResponse(
                messages=[],
                total=0,
                limit=safe_limit,
                offset=offset,
                channel=channel,
                has_more=False,
                next_cursor=None,
                unread_count=0,
                unread_message_ids=[],
                marked_read_count=0,
            )

        result = await svc.list_messages(
            actor=actor,
            since=since,
            limit=safe_limit,
            channel=channel,
            unread_only=unread_only,
            include_unread_counts=False,
            include_mentions=False,
            include_activity_metrics=False,
            include_intelligence=True,
            include_reactions=True,  # Include reaction counts per message
            include_total_count=False,  # Avoid expensive COUNT query
            before_timestamp=before_timestamp,
            before_message_id=before_message_id,
            message_type_filter=effective_message_type,
            media_types_filter=media_types_filter,
            unread_message_ids=unread_uuid_ids if unread_only and unread_cache_loaded else None,
            adapter="api",
        )

        messages = result.get("messages", [])

        # Intelligence data is loaded via include_intelligence=True in service call above

        # Total for current page (service returns limited set). For back-compat, report len(messages)
        total = len(messages)

        # Transform to API response models
        message_list = []

        # Batch fetch wait states for all agent authors (avoids N+1 Redis queries)
        agent_ids = {
            str(msg.agent.id)
            for msg in messages
            if msg.agent is not None
        }
        wait_states = await _batch_get_agent_wait_states(agent_ids)

        for message in messages:
            replies_count = getattr(message, "replies_count", 0)

            # Determine author info — prefer agent when agent_id is set (MCP/headless messages)
            if message.agent is not None:
                author_info = {"id": str(message.agent.id), "name": message.agent.name, "type": "agent"}
            elif message.user is not None:
                author_info = {
                    "id": str(message.user.id),
                    "name": message.user.username or "Unknown User",
                    "type": "user",
                }
            else:
                author_info = {"id": "", "name": "Unknown", "type": "user"}

            author_id = author_info.get("id") or None
            author_type = author_info.get("type") or None

            # Check wait state for agent authors (using pre-fetched batch data)
            waiting_for_response = False
            waiting_since = None
            waiting_ttl_seconds = None
            waiting_for = None
            if author_type == "agent" and author_id:
                wait_state = wait_states.get(author_id)
                if wait_state:
                    waiting_for_response = True
                    # Parse waiting_since from ISO format
                    since_str = wait_state.get("since")
                    if since_str:
                        try:
                            waiting_since = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            waiting_since = None
                    waiting_ttl_seconds = wait_state.get("ttl_remaining")
                    waiting_for = wait_state.get("waiting_for")

            routed_to, routing_story = _routing_payload_from_metadata(message.message_metadata)
            sender_type = _compute_sender_type(message, author_type)

            message_response = MessageResponse(
                id=str(message.id),
                content=message.content,
                channel=message.channel,
                message_type=message.message_type,
                parent_id=str(message.parent_id) if message.parent_id else None,
                read_state=message.read_state,
                cursor_position=message.cursor_position or 0,
                metadata=message.message_metadata,
                routed_to=routed_to,
                routing_story=routing_story,
                created_at=message.created_at,
                updated_at=message.updated_at,
                author=author_info,
                author_id=author_id,
                author_type=author_type,
                replies_count=replies_count,
                reactions=getattr(message, "reactions", None),
                # Intelligence
                spam_score=message.intelligence_data.spam_score
                if getattr(message, "intelligence_data", None)
                else None,
                toxicity_score=message.intelligence_data.toxicity_score
                if getattr(message, "intelligence_data", None)
                else None,
                quality_score=message.intelligence_data.quality_score
                if getattr(message, "intelligence_data", None)
                else None,
                ai_summary=message.ai_summary,
                summarized_at=getattr(message, "summarized_at", None),
                # Waiting state for timer UI
                waiting_for_response=waiting_for_response,
                waiting_since=waiting_since,
                waiting_ttl_seconds=waiting_ttl_seconds,
                waiting_for=waiting_for,
                # FRONTEND-005 contract fields
                conversation_id=str(message.parent_id) if message.parent_id else str(message.id),
                space_id=str(message.space_id),
                sender_type=sender_type,
                display_name=author_info.get("name"),
                routing=_compute_routing(message.message_metadata, routed_to),
                ui=_extract_ui_payload(message.message_metadata),
            )
            message_list.append(message_response)

        # Unread IDs for current user
        # unread_ids = IDs of unread messages on current page (for highlighting)
        # unread_count = total unread messages in this org (for badge display)
        unread_ids = []
        unread_count = 0
        marked_read_count = 0
        try:
            message_ids_in_response = {str(msg.id) for msg in message_list}
            # Page-specific unread IDs for highlighting
            unread_ids = [uid for uid in unread_list if uid in message_ids_in_response]

            # Total unread count for this org (batched to avoid 32k parameter limit)
            if unread_list:
                BATCH_SIZE = 10000
                total_org_unread = 0
                for i in range(0, len(unread_list), BATCH_SIZE):
                    batch = unread_list[i : i + BATCH_SIZE]
                    batch_check = await session.db.execute(
                        select(func.count(Message.id)).where(
                            and_(
                                Message.id.in_([uuid.UUID(uid) for uid in batch]),
                                Message.space_id == session.space_id,
                            )
                        )
                    )
                    total_org_unread += batch_check.scalar() or 0
                unread_count = total_org_unread

            if mark_read and unread_ids:
                marked_read_count = await redis_client.srem(f"unread:{session.user.id}", *unread_ids)
        except Exception as e:
            logger.warning("Failed to compute unread messages", exc_info=e)

        # Calculate pagination info
        # Use simple check: if we got a full page, there may be more
        has_more = len(message_list) >= safe_limit

        # next_cursor is compound format: "timestamp|message_id" for stable pagination
        # This prevents skipping/duplicating messages when they share the same timestamp
        next_cursor = None
        if message_list and has_more:
            oldest_msg = message_list[-1]  # Messages are ordered newest first
            if oldest_msg.created_at:
                # Compound cursor format for tie-breaking on same-timestamp messages
                next_cursor = f"{oldest_msg.created_at.isoformat()}|{oldest_msg.id}"

        return MessageListResponse(
            messages=message_list,
            total=total,
            limit=safe_limit,
            offset=offset,
            channel=channel,
            has_more=has_more,
            next_cursor=next_cursor,
            unread_count=unread_count,
            unread_message_ids=unread_ids,
            marked_read_count=marked_read_count,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch messages: {e!s}"
        )


@router.post("/messages/{message_id}/read")
async def mark_message_read(message_id: str, session: SecureSession = Depends(get_secure_session)):
    """Mark a message as read for the current user"""
    try:
        # Remove from unread set
        await redis_client.srem(f"unread:{session.user.id}", message_id)
        return {"status": "success", "message_id": message_id}
    except Exception as e:
        logger.warning("Failed to mark message as read", exc_info=e)
        return {"status": "error", "message": str(e)}


@router.post("/messages/mark-all-read")
async def mark_all_read(
    session: SecureSession = Depends(get_secure_session),
):
    """Mark all messages in current org as read"""
    try:
        # Get all message IDs in current org (RLS context already set)
        messages_result = await session.db.execute(select(Message.id).where(Message.space_id == session.space_id))
        org_message_ids = {str(mid) for mid in messages_result.scalars().all()}

        # Get current unread set
        unread_set = await redis_client.smembers(f"unread:{session.user.id}")

        # Remove only org messages from unread
        if unread_set and org_message_ids:
            to_remove = unread_set & org_message_ids
            if to_remove:
                await redis_client.srem(f"unread:{session.user.id}", *to_remove)
                return {"status": "success", "marked_read": len(to_remove)}

        return {"status": "success", "marked_read": 0}
    except Exception as e:
        logger.warning("Failed to mark all as read", exc_info=e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/messages", response_model=MessageResponse)
async def create_message(
    message_data: MessageCreate,
    background_tasks: BackgroundTasks,
    session: SecureSession = Depends(get_secure_session),
):
    """Create a new message (via shared service)"""
    try:
        # Build Actor — use agent identity when authenticated via client_credentials
        if session.is_agent and session.agent_id:
            actor = Actor(
                id=session.agent_id, type="agent", space_id=session.space_id,
                capabilities={CAP_MESSAGES_SEND}
            )
            display_name = session.agent_name or "Agent"
            author_type = "agent"
        else:
            actor = Actor(
                id=session.user.id, type="human", space_id=session.space_id,
                capabilities={CAP_MESSAGES_SEND}
            )
            display_name = session.user.username or session.user.full_name or "User"
            author_type = "user"

        # Initialize shared service
        svc = MessagesService(
            db=session.db,
            redis_client=redis_client,
            sse_broker=redis_sse_broker,  # Use Redis Streams for cross-process delivery
        )

        # Link accepted uploads before live broadcast so SSE/MCP consumers can
        # show clickable attachment previews without an immediate REST refetch.
        accepted_attachments = _accepted_attachments_from_metadata(message_data.metadata)

        # Execute command via service
        msg = await svc.send(
            actor=actor,
            content=message_data.content,
            channel=message_data.channel,
            message_type=message_data.message_type,
            parent_id=message_data.parent_id,
            metadata=message_data.metadata,
            author_display_name=display_name,
            adapter="mcp" if session.is_agent else "api",
            before_broadcast=lambda message: _link_message_attachments_before_broadcast(
                db=session.db,
                accepted_attachments=accepted_attachments,
                upload_owner_id=session.user.id,
                space_id=UUID(str(session.space_id)),
                message_id=message.id,
                logger=logger,
            ),
        )

        # Build API response (unchanged shape)
        author_info = {
            "id": str(session.agent_id) if session.is_agent else str(session.user.id),
            "name": display_name,
            "type": author_type,
        }

        # Intelligence processing now handled in MessagesService.send()

        routed_to, routing_story = _routing_payload_from_metadata(msg.message_metadata)

        # Derive FRONTEND-005 contract fields
        conversation_id = str(msg.parent_id) if msg.parent_id else str(msg.id)
        space_id = str(msg.space_id)
        has_space_agent = any(t.get("type") == "space_agent" for t in routed_to)
        routing_mode = "space_agent" if has_space_agent else ("direct" if routed_to else None)
        received_by = routed_to[0] if routed_to else None
        ui_payload = _extract_ui_payload(msg.message_metadata)
        routing_obj = RoutingPayload(
            mode=routing_mode,
            target_id=routed_to[0].get("agent_id") if routed_to else None,
            target_name=routed_to[0].get("display_name") if routed_to else None,
            target_type=routed_to[0].get("type", routed_to[0].get("source")) if routed_to else None,
        ) if routed_to else None

        return MessageResponse(
            id=str(msg.id),
            content=msg.content,
            channel=msg.channel,
            message_type=msg.message_type,
            parent_id=str(msg.parent_id) if msg.parent_id else None,
            read_state=msg.read_state,
            cursor_position=msg.cursor_position or 0,
            metadata=msg.message_metadata,
            routed_to=routed_to,
            routing_story=routing_story,
            created_at=msg.created_at,
            updated_at=msg.updated_at,
            author=author_info,
            author_id=author_info.get("id"),
            author_type=author_info.get("type"),
            replies_count=0,
            # Intelligence (None for new message)
            spam_score=None,
            toxicity_score=None,
            quality_score=None,
            # FRONTEND-005 contract fields
            conversation_id=conversation_id,
            space_id=space_id,
            sender_type=author_type or "user",
            display_name=display_name,
            routing=routing_obj,
            ui=ui_payload,
            received_by=received_by,
            accepted_attachments=accepted_attachments,
        )

    except PermissionError as pe:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create message: {e!s}"
        )


@router.get("/messages/{message_id}", response_model=MessageResponse)
async def get_message(
    message_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Get a specific message by ID"""
    try:
        result = await session.db.execute(
            select(Message)
            .options(selectinload(Message.user), selectinload(Message.agent), selectinload(Message.intelligence_data))
            .where(and_(Message.id == uuid.UUID(message_id), Message.space_id == session.space_id))
        )
        message = result.scalar_one_or_none()

        if not message:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

        # Count replies
        replies_result = await session.db.execute(select(Message.id).where(Message.parent_id == message.id))
        replies_count = len(replies_result.scalars().all())

        # Determine author info — prefer agent when agent_id is set
        if message.agent is not None:
            author_info = {"id": str(message.agent.id), "name": message.agent.name, "type": "agent"}
        elif message.user is not None:
            author_info = {
                "id": str(message.user.id),
                "name": message.user.username or message.user.full_name,
                "type": "user",
            }
        else:
            author_info = {"id": "", "name": "Unknown", "type": "user"}

        author_id = author_info.get("id") or None
        author_type = author_info.get("type") or None

        # Check wait state for agent authors
        waiting_for_response = False
        waiting_since = None
        waiting_ttl_seconds = None
        waiting_for = None
        if author_type == "agent" and author_id:
            wait_state = await _get_agent_wait_state(author_id)
            if wait_state:
                waiting_for_response = True
                since_str = wait_state.get("since")
                if since_str:
                    try:
                        waiting_since = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        waiting_since = None
                waiting_ttl_seconds = wait_state.get("ttl_remaining")
                waiting_for = wait_state.get("waiting_for")

        routed_to, routing_story = _routing_payload_from_metadata(message.message_metadata)
        sender_type = _compute_sender_type(message, author_type)

        return MessageResponse(
            id=str(message.id),
            content=message.content,
            channel=message.channel,
            message_type=message.message_type,
            parent_id=str(message.parent_id) if message.parent_id else None,
            read_state=message.read_state,
            cursor_position=message.cursor_position or 0,
            metadata=message.message_metadata,
            routed_to=routed_to,
            routing_story=routing_story,
            created_at=message.created_at,
            updated_at=message.updated_at,
            author=author_info,
            author_id=author_id,
            author_type=author_type,
            replies_count=replies_count,
            spam_score=message.intelligence_data.spam_score if getattr(message, "intelligence_data", None) else None,
            toxicity_score=message.intelligence_data.toxicity_score
            if getattr(message, "intelligence_data", None)
            else None,
            quality_score=message.intelligence_data.quality_score
            if getattr(message, "intelligence_data", None)
            else None,
            ai_summary=message.ai_summary,
            summarized_at=getattr(message, "summarized_at", None),
            waiting_for_response=waiting_for_response,
            waiting_since=waiting_since,
            waiting_ttl_seconds=waiting_ttl_seconds,
            waiting_for=waiting_for,
            # FRONTEND-005 contract fields
            conversation_id=str(message.parent_id) if message.parent_id else str(message.id),
            space_id=str(message.space_id),
            sender_type=sender_type,
            display_name=author_info.get("name"),
            routing=_compute_routing(message.message_metadata, routed_to),
            ui=_extract_ui_payload(message.message_metadata),
        )

    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid message ID format: {message_id}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch message: {e!s}")


@router.put("/messages/{message_id}", response_model=MessageResponse)
async def update_message(
    message_id: str,
    message_update: MessageUpdate,
    session: SecureSession = Depends(get_secure_session),
):
    """Update a message (only by the author)"""
    try:
        # If content is provided, use unified service edit() to enforce constraints
        if message_update.content is not None:
            # Include admin capability for override behavior
            capabilities = {CAP_MESSAGES_SEND}
            try:
                if getattr(session.user, "is_admin", False) or getattr(session.user, "role", "") == "admin":
                    capabilities.add("admin")
            except Exception:
                pass
            if session.is_agent and session.agent_id:
                actor = Actor(
                    id=uuid.UUID(session.agent_id),
                    type="agent",
                    space_id=uuid.UUID(session.space_id),
                    capabilities=capabilities,
                )
            else:
                actor = Actor(
                    id=session.user.id,
                    type="human",
                    space_id=session.space_id,
                    capabilities=capabilities,
                )

            svc = MessagesService(
                db=session.db,
                redis_client=redis_client,
                sse_broker=redis_sse_broker,  # Use Redis Streams for cross-process delivery
            )

            try:
                updated = await svc.edit(
                    actor=actor,
                    message_id=uuid.UUID(message_id),
                    new_content=message_update.content,
                    reason=None,
                    adapter="api",
                )
            except PermissionError as pe:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(pe))
            except ValueError as ve:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

            # Count replies via query
            replies_result = await session.db.execute(select(Message.id).where(Message.parent_id == updated.id))
            replies_count = len(replies_result.scalars().all())

            updated_with_author = await session.db.execute(
                select(Message)
                .options(selectinload(Message.user), selectinload(Message.agent))
                .where(Message.id == updated.id)
            )
            updated_message = updated_with_author.scalar_one_or_none() or updated

            if getattr(updated_message, "user", None) is not None:
                author_info = {
                    "id": str(updated_message.user.id),
                    "name": updated_message.user.username or updated_message.user.full_name,
                    "type": "user",
                }
            elif getattr(updated_message, "agent", None) is not None:
                author_info = {"id": str(updated_message.agent.id), "name": updated_message.agent.name, "type": "agent"}
            else:
                author_info = {
                    "id": str(session.user.id),
                    "name": session.user.username or session.user.full_name,
                    "type": "user",
                }

            return MessageResponse(
                id=str(updated.id),
                content=updated.content,
                channel=updated.channel,
                message_type=updated.message_type,
                parent_id=str(updated.parent_id) if updated.parent_id else None,
                read_state=updated.read_state,
                cursor_position=updated.cursor_position or 0,
                metadata=updated.message_metadata,
                routed_to=_routing_payload_from_metadata(updated.message_metadata)[0],
                routing_story=_routing_payload_from_metadata(updated.message_metadata)[1],
                created_at=updated.created_at,
                updated_at=updated.updated_at,
                author=author_info,
                author_id=author_info.get("id"),
                author_type=author_info.get("type"),
                replies_count=replies_count,
                # Intelligence (likely None after update unless re-fetched)
                spam_score=None,
                toxicity_score=None,
                quality_score=None,
            )

        # Otherwise, allow read_state/metadata updates directly (legacy behavior)
        result = await session.db.execute(
            select(Message)
            .options(selectinload(Message.user), selectinload(Message.agent))
            .where(
                and_(
                    Message.id == uuid.UUID(message_id),
                    Message.space_id == session.space_id,
                    Message.user_id == session.user.id,
                )
            )
        )
        message = result.scalar_one_or_none()
        if not message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found or you don't have permission to update it",
            )

        update_data = {}
        if message_update.read_state is not None:
            update_data["read_state"] = message_update.read_state
        if message_update.metadata is not None:
            update_data["message_metadata"] = message_update.metadata
        if update_data:
            update_data["updated_at"] = datetime.now(timezone.utc)
            await session.db.execute(update(Message).where(Message.id == message.id).values(**update_data))
            await session.db.commit()
            await session.db.refresh(message, ["user", "agent"])

        replies_result = await session.db.execute(select(Message.id).where(Message.parent_id == message.id))
        replies_count = len(replies_result.scalars().all())

        return MessageResponse(
            id=str(message.id),
            content=message.content,
            channel=message.channel,
            message_type=message.message_type,
            parent_id=str(message.parent_id) if message.parent_id else None,
            read_state=message.read_state,
            cursor_position=message.cursor_position or 0,
            metadata=message.message_metadata,
            created_at=message.created_at,
            updated_at=message.updated_at,
            author={
                "id": str(session.user.id),
                "name": session.user.username or session.user.full_name,
                "type": "user",
            },
            author_id=str(session.user.id),
            author_type="user",
            replies_count=replies_count,
            # Intelligence (likely None after update unless re-fetched)
            spam_score=None,
            toxicity_score=None,
            quality_score=None,
        )

    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid message ID format: {message_id}")
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update message: {e!s}"
        )


@router.get("/messages/{message_id}/replies", response_model=MessageListResponse)
async def get_message_replies(
    message_id: str,
    limit: int = Query(50, le=500, description="Number of replies to return"),
    offset: int = Query(0, ge=0, description="Number of replies to skip"),
    session: SecureSession = Depends(get_secure_session),
):
    """Get all replies to a specific message"""
    try:
        # Verify parent message exists
        parent_result = await session.db.execute(
            select(Message).where(
                and_(Message.id == uuid.UUID(message_id), Message.space_id == session.space_id)
            )
        )
        parent_message = parent_result.scalar_one_or_none()

        if not parent_message:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent message not found")

        # Get replies
        query = (
            select(Message)
            .options(selectinload(Message.user), selectinload(Message.agent))
            .where(and_(Message.parent_id == parent_message.id, Message.space_id == session.space_id))
        )

        # Get total count
        count_result = await session.db.execute(query.with_only_columns(Message.id))
        total = len(count_result.scalars().all())

        # Apply pagination and ordering
        query = query.order_by(Message.created_at.asc()).offset(offset).limit(limit)
        result = await session.db.execute(query)
        replies = result.scalars().all()

        # Convert to response format
        reply_list = []
        for reply in replies:
            # Determine author info (prefer user when present)
            if reply.user is not None:
                author_info = {
                    "id": str(reply.user.id),
                    "name": reply.user.username or reply.user.full_name,
                    "type": "user",
                }
            elif reply.agent is not None:
                author_info = {"id": str(reply.agent.id), "name": reply.agent.name, "type": "agent"}
            else:
                author_info = {"id": "", "name": "Unknown", "type": "user"}

            reply_response = MessageResponse(
                id=str(reply.id),
                content=reply.content,
                channel=reply.channel,
                message_type=reply.message_type,
                parent_id=str(reply.parent_id) if reply.parent_id else None,
                read_state=reply.read_state,
                cursor_position=reply.cursor_position or 0,
                metadata=reply.message_metadata,
                routed_to=_routing_payload_from_metadata(reply.message_metadata)[0],
                routing_story=_routing_payload_from_metadata(reply.message_metadata)[1],
                created_at=reply.created_at,
                updated_at=reply.updated_at,
                author=author_info,
                replies_count=0,  # Nested replies not counted for now
            )
            reply_list.append(reply_response)

        return MessageListResponse(
            messages=reply_list,
            total=total,
            limit=limit,
            offset=offset,
            channel=parent_message.channel,
            has_more=(offset + limit) < total,
        )

    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid message ID format: {message_id}")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch message replies: {e!s}"
        )


@router.patch("/messages/{message_id}/read")
async def mark_message_as_read(
    message_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Mark a message as read"""
    try:
        # Verify message exists and belongs to current user's org
        result = await session.db.execute(
            select(Message).where(
                and_(Message.id == uuid.UUID(message_id), Message.space_id == session.space_id)
            )
        )
        message = result.scalar_one_or_none()

        if not message:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

        # Update read state
        await session.db.execute(
            update(Message).where(Message.id == message.id).values(read_state="read", updated_at=datetime.now(timezone.utc))
        )
        await session.db.commit()

        return {"message": "Message marked as read", "message_id": str(message.id)}

    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid message ID format: {message_id}")
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to mark message as read: {e!s}"
        )


@router.get("/channels", response_model=list[str])
async def list_channels(
    session: SecureSession = Depends(get_secure_session),
):
    """Get list of all channels with messages in current org"""
    try:
        result = await session.db.execute(
            select(Message.channel)
            .distinct()
            .where(Message.space_id == session.space_id)
            .order_by(Message.channel)
        )
        channels = list(result.scalars().all())

        # Ensure 'main' channel is always present
        if "main" not in channels:
            channels.insert(0, "main")

        return channels

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch channels: {e!s}"
        )


# NOTE: Do not duplicate PUT /messages/{message_id}. Unified update route is defined above.


@router.delete("/messages/{message_id}")
async def delete_message(
    message_id: str,
    reason: str,
    permanent: bool = False,
    session: SecureSession = Depends(get_secure_session),
):
    """Delete a message (via shared service)"""
    try:
        # Build Actor from SecureSession
        # Grant admin capability if user is platform admin OR space owner/admin
        # with unbound token. Unbound alone is not enough — must also be
        # owner/admin of the space the message is in.
        capabilities = {CAP_MESSAGES_SEND}
        if session.user.is_admin or getattr(session.user, "is_moderator", False):
            capabilities.add("admin")

        if session.is_agent and session.agent_id:
            actor = Actor(
                id=UUID(session.agent_id),
                type="agent",
                space_id=UUID(session.space_id),
                capabilities=capabilities,
            )
        else:
            actor = Actor(
                id=session.user.id,
                type="human",
                space_id=session.space_id,
                capabilities=capabilities,
            )

        # Initialize shared service
        svc = MessagesService(
            db=session.db,
            redis_client=redis_client,
            sse_broker=redis_sse_broker,  # Use Redis Streams for cross-process delivery
        )

        # Execute delete via service
        success = await svc.delete(
            actor=actor, message_id=UUID(message_id), reason=reason, permanent=permanent, adapter="api"
        )

        return {"status": "success", "deleted": success}
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.warning("Failed to delete message", exc_info=e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete message")


@router.get("/messages/{message_id}/history")
async def get_message_history(
    message_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Get edit/delete history for a message"""
    try:
        # Build Actor from SecureSession
        capabilities = {CAP_MESSAGES_SEND}
        if session.user.is_admin or getattr(session.user, "is_moderator", False):
            capabilities.add("admin")

        actor = Actor(
            id=session.user.id, type="human", space_id=session.space_id, capabilities=capabilities
        )

        # Initialize shared service
        svc = MessagesService(
            db=session.db,
            redis_client=redis_client,
            sse_broker=redis_sse_broker,  # Use Redis Streams for cross-process delivery
        )

        # Get history via service
        history = await svc.get_history(actor=actor, message_id=UUID(message_id), adapter="api")

        return {"message_id": message_id, "history": history}
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.warning("Failed to get message history", exc_info=e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get message history")


# =============================================================================
# Feedback Endpoints (INTELLIGENCE-001)
# =============================================================================


@router.post("/messages/{message_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    message_id: str,
    feedback: FeedbackRequest,
    session: SecureSession = Depends(get_secure_session),
):
    """Submit or update feedback (thumbs up/down) on an agent message."""
    try:
        svc = FeedbackService(db=session.db)
        fb = await svc.submit_feedback(
            message_id=UUID(message_id),
            user_id=session.user.id,
            space_id=session.space_id,
            vote=feedback.vote,
            comment=feedback.comment,
        )
        await session.db.commit()

        # Broadcast SSE feedback_updated event
        try:
            summary = await svc.get_message_feedback(UUID(message_id), session.user.id)
            await redis_sse_broker.publish(
                space_id=str(session.space_id),
                event="feedback_updated",
                data={
                    "message_id": message_id,
                    "agent_id": str(fb.agent_id),
                    "thumbs_up": summary["thumbs_up"],
                    "thumbs_down": summary["thumbs_down"],
                },
            )
        except Exception as e:
            logger.warning("Failed to broadcast feedback SSE event: %s", e)

        return FeedbackResponse(
            id=str(fb.id),
            message_id=str(fb.message_id),
            agent_id=str(fb.agent_id),
            vote=fb.vote,
            comment=fb.comment,
            created_at=fb.created_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        await session.db.rollback()
        logger.warning("Failed to submit feedback", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit feedback: {e!s}",
        )


@router.get("/messages/{message_id}/feedback", response_model=FeedbackSummary)
async def get_feedback(
    message_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Get feedback summary for a message (thumbs up/down counts + user's vote)."""
    try:
        svc = FeedbackService(db=session.db)
        summary = await svc.get_message_feedback(UUID(message_id), session.user.id)
        return FeedbackSummary(
            message_id=message_id,
            thumbs_up=summary["thumbs_up"],
            thumbs_down=summary["thumbs_down"],
            user_vote=summary["user_vote"],
        )
    except Exception as e:
        logger.warning("Failed to get feedback", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get feedback: {e!s}",
        )


# =============================================================================
# Message Streaming Endpoint (SSE Contract v1)
# =============================================================================

class StreamChunkRequest(BaseModel):
    """Chunk payload from the plugin during agent response streaming."""
    delta: str = ""
    sequence: int = 0
    done: bool = False
    agent_id: str | None = None
    conversation_id: str | None = None


STREAM_BUFFER_TTL = 300  # 5 minutes — if done never arrives, buffer expires


@router.post("/messages/{message_id}/stream")
async def stream_message_chunk(
    message_id: str,
    chunk: StreamChunkRequest,
    session: SecureSession = Depends(get_secure_session),
):
    """Accept a streaming chunk from the plugin and broadcast via SSE.

    The plugin POSTs chunks as the agent generates them. Each chunk is:
    1. Appended to a Redis buffer (stream:{message_id})
    2. Broadcast as a `message_stream` SSE event to the space

    On done=True:
    1. Full content is read from the Redis buffer
    2. Persisted to the messages table (creates or updates)
    3. Summarization triggered async
    4. Buffer cleaned up
    """
    try:
        msg_uuid = UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID")

    buffer_key = f"stream:{message_id}"
    space_id = str(session.space_id)

    if not chunk.done:
        # Append delta to Redis buffer
        if chunk.delta:
            await redis_client.append(buffer_key, chunk.delta)
            await redis_client.expire(buffer_key, STREAM_BUFFER_TTL)

        # Broadcast chunk via SSE
        await redis_sse_broker.publish(
            space_id=space_id,
            event="message_stream",
            data={
                "message_id": message_id,
                "conversation_id": chunk.conversation_id or "",
                "agent_id": chunk.agent_id or "",
                "type": "chunk",
                "delta": chunk.delta,
                "sequence": chunk.sequence,
            },
        )
        return {"status": "ok", "sequence": chunk.sequence}

    # done=True — finalize the message
    # Read full content from buffer
    full_content = await redis_client.get(buffer_key)
    if not full_content:
        full_content = chunk.delta or ""

    # Check if message already exists (created by dispatch) or needs creating
    result = await session.db.execute(
        select(Message).where(Message.id == msg_uuid)
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing message with streamed content
        await session.db.execute(
            update(Message)
            .where(Message.id == msg_uuid)
            .values(content=full_content)
        )
    else:
        # Create new message from stream
        agent_uuid = UUID(chunk.agent_id) if chunk.agent_id else None
        msg = Message(
            id=msg_uuid,
            space_id=session.space_id,
            user_id=session.user.id,
            agent_id=agent_uuid,
            content=full_content,
            channel="main",
            message_type="response",
        )
        session.db.add(msg)

    await session.db.commit()

    # Clean up buffer
    await redis_client.delete(buffer_key)

    # Broadcast done event
    await redis_sse_broker.publish(
        space_id=space_id,
        event="message_stream",
        data={
            "message_id": message_id,
            "conversation_id": chunk.conversation_id or "",
            "agent_id": chunk.agent_id or "",
            "type": "done",
            "delta": "",
            "sequence": chunk.sequence,
        },
    )

    # Intelligence handled by aX dispatch — see dispatch_executor.py

    return {"status": "done", "message_id": message_id, "content_length": len(full_content)}


# ── Message Actions (Space Agent card interactions) ──────────────────────


class MessageAction(BaseModel):
    space_id: str
    conversation_id: str | None = None
    message_id: str
    card_id: str | None = None
    action_id: str
    choice_id: str | None = None
    free_text: str | None = None


class MessageActionResponse(BaseModel):
    status: str  # "accepted", "routed", "error"
    action_id: str
    routed_to: str | None = None
    message_id: str | None = None


@router.post("/messages/actions", response_model=MessageActionResponse)
async def handle_message_action(
    action: MessageAction,
    session: SecureSession = Depends(get_secure_session),
):
    """Handle a card/action interaction from the frontend.

    For MVP: converts the action into a structured message routed through
    the normal message pipeline, so agents can process it.
    """
    try:
        actor = Actor(
            id=session.user.id,
            type="human",
            space_id=session.space_id,
            capabilities={CAP_MESSAGES_SEND},
        )
        display_name = session.user.username or session.user.full_name or "User"

        # Build action content for agent consumption
        action_content = f"[action:{action.action_id}]"
        if action.choice_id:
            action_content += f" choice={action.choice_id}"
        if action.free_text:
            action_content += f" {action.free_text}"

        svc = MessagesService(
            db=session.db,
            redis_client=redis_client,
            sse_broker=redis_sse_broker,
        )

        msg = await svc.send(
            actor=actor,
            content=action_content,
            channel="main",
            message_type="message",
            parent_id=action.conversation_id,
            metadata={"card_action": action.model_dump()},
            author_display_name=display_name,
            adapter="api",
        )

        return MessageActionResponse(
            status="accepted",
            action_id=action.action_id,
            routed_to=None,
            message_id=str(msg.id),
        )

    except Exception as e:
        logging.getLogger(__name__).error(f"Message action failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process action: {e!s}",
        )
