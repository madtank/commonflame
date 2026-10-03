
from fastapi import APIRouter, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, AsyncGenerator
import json
import asyncio
import logging
import re
from datetime import datetime, timezone
from app.core.jwt_verify import _resolve_user_from_bearer_token
from app.models.user import User
from app.core.redis_client import redis_client
from app.services.redis_sse_broker import redis_sse_broker
from app.constants import CONTEXT_DEFAULT_TTL, CONTEXT_MAX_TTL
from app.validators import validate_topic
from app.core.authorization import verify_space_actor_access
from app.core.context_payload import (
    ContextPayloadTooLarge,
    context_record_for_event,
    ensure_context_inline_size,
)
from app.core.database import set_rls_context
from app.core.rls import SecureSession, SecureSessionDep, system_session_context
from app.services.entity_summary_service import summarize_context_leaf
from app.services.workspace_intelligence_service import WorkspaceIntelligenceService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/context", tags=["context"])
sse_router = APIRouter(prefix="/api/sse", tags=["context-sse"])
SSE_HEARTBEAT_TIMEOUT_S = 15.0

def get_space_id(user: User, session_space_id: Optional[str] = None) -> str:
    space_id = (
        session_space_id
        or getattr(user, "_effective_space_id", None)
        or getattr(user, "current_space_id", None)
        or str(user.space_id)
    )
    if not space_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current organization context is unavailable"
        )
    return str(space_id)


def context_actor_name(session: SecureSession) -> str:
    """Return the display-safe actor label stored with context records."""
    if getattr(session, "is_agent", False):
        agent_label = getattr(session, "agent_name", None) or getattr(session, "agent_id", None)
        if agent_label:
            agent_label = str(agent_label)
            if agent_label.startswith(("agent:", "user:")):
                return agent_label
            return f"agent:{agent_label}"

    username = getattr(session.user, "username", None) or getattr(session.user, "email", None)
    return f"user:{username or session.user.id}"


async def resolve_context_space_id(session: SecureSession, requested_space_id: Optional[str] = None) -> str:
    """Resolve and authorize the context space for this request.

    The CLI and MCP paths are API-first: callers pass an explicit space_id and
    context must be stored/read from that same space. SecureSession defaults can
    lag behind the active UI space, so explicit overrides are allowed only after
    verifying the current actor can access the target space.
    """
    space_id = get_space_id(session.user, requested_space_id or session.space_id)
    await verify_space_actor_access(
        session.db,
        user_id=session.user.id,
        space_id=space_id,
        is_agent=session.is_agent,
        agent_id=session.agent_id,
    )
    await set_rls_context(
        session.db,
        user_id=str(session.user.id),
        space_id=space_id,
        agent_id=session.agent_id,
    )
    return space_id


def extract_sse_auth_token(request: Request, token: Optional[str]) -> Optional[str]:
    """Resolve SSE auth token from query param, Authorization header, or access_token cookie."""
    if token:
        return token

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    access_cookie = request.cookies.get("access_token")
    if access_cookie:
        return access_cookie

    return None

@router.get("", response_model=Dict[str, Any])
async def list_context(
    session: SecureSessionDep,
    prefix: Optional[str] = None,
    topic: Optional[str] = None,
    space_id: Optional[str] = Query(None),
):
    """List all shared context keys and values for the current organization."""
    # Validate topic parameter (P1 fix)
    if topic:
        try:
            validate_topic(topic)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    space_id = await resolve_context_space_id(session, space_id)

    # If prefix is provided, append it
    if prefix:
        search_pattern = f"context:{space_id}:{prefix}*"
    else:
        search_pattern = f"context:{space_id}:*"

    # Use SCAN to avoid blocking Redis (Critical Fix #3)
    cursor = 0
    keys = []
    while True:
        cursor, batch = await redis_client.scan(cursor, match=search_pattern, count=100)
        keys.extend(batch)
        if cursor == 0:
            break

    result = {}
    if keys:
        # Use pipeline to batch GET operations (Critical Fix #2)
        async with redis_client.pipeline() as pipe:
            for full_key in keys:
                pipe.get(full_key)
            values = await pipe.execute()

        prefix_len = len(f"context:{space_id}:")

        for full_key, value in zip(keys, values):
            clean_key = full_key[prefix_len:]
            try:
                parsed_value = json.loads(value) if value else None

                # Filter by topic if specified (Medium Fix #6)
                if topic:
                    # Check if value is a dict and has matching topic
                    if isinstance(parsed_value, dict) and parsed_value.get("topic") == topic:
                        result[clean_key] = parsed_value
                    # If topic filter is active but item doesn't match, skip it
                else:
                    result[clean_key] = parsed_value

            except json.JSONDecodeError:
                # Legacy data - skip if topic filter is active
                if not topic:
                    result[clean_key] = value

    # Merge durable Workspace Intelligence Vault entries so collection listings
    # remain discoverable after Redis TTL expiry/hot-key deletion. Redis wins for
    # keys that are still hot because it represents the freshest ephemeral view.
    try:
        from uuid import UUID

        vault_service = WorkspaceIntelligenceService(session.db)
        vault_limit = 100
        vault_offset = 0
        while True:
            vault_items = await vault_service.list_intelligence(
                space_id=UUID(space_id),
                key_prefix=prefix,
                limit=vault_limit,
                offset=vault_offset,
                include_payload=True,
            )
            items = vault_items.get("items", [])
            for item in items:
                clean_key = item.get("key")
                if not clean_key or clean_key in result:
                    continue
                payload = item.get("payload")
                if topic:
                    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    if not (
                        isinstance(payload, dict) and payload.get("topic") == topic
                    ) and metadata.get("topic") != topic:
                        continue
                if isinstance(payload, dict):
                    result[clean_key] = {
                        **payload,
                        "_source": "vault",
                        "_artifact_type": item.get("artifact_type"),
                        "_summary_snippet": item.get("summary_snippet"),
                        "_metadata": item.get("metadata"),
                        "_version": item.get("version"),
                        "_access_count": item.get("access_count"),
                    }
                else:
                    result[clean_key] = {"value": payload, "_source": "vault"}

            if not vault_items.get("has_more") or not items:
                break
            vault_offset += len(items)
    except ValueError:
        logger.debug("Invalid space_id for Workspace Intelligence list fallback: %s", space_id)
    except Exception as e:
        logger.error("Workspace Intelligence list fallback failed: %s", e, exc_info=True)

    return result

@router.get("/{key:path}", response_model=Dict[str, Any])
async def get_context_item(
    key: str,
    session: SecureSessionDep,
    space_id: Optional[str] = Query(None),
):
    """
    Get a specific context item by key.

    Uses tiered storage with fallback:
    1. First checks Redis (ephemeral storage)
    2. Falls back to Postgres durable_context table if not found in Redis

    This ensures promoted context remains accessible even after Redis TTL expires.
    """
    space_id = await resolve_context_space_id(session, space_id)
    full_key = f"context:{space_id}:{key}"

    # First, check Redis (ephemeral storage)
    value = await redis_client.get(full_key)
    if value is not None:
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            parsed_value = value
        return {"key": key, "value": parsed_value, "source": "redis"}

    # Fallback to Postgres Workspace Intelligence Vault
    try:
        from uuid import UUID
        from app.services.workspace_intelligence_service import WorkspaceIntelligenceService

        service = WorkspaceIntelligenceService(session.db)
        result = await service.get_intelligence(
            space_id=UUID(space_id),
            key_or_id=key,
            increment_access=True,
            log_access=False,  # Disable access logging to prevent write amplification
        )

        if result is not None:
            return {
                "key": key,
                "value": result["payload"],
                "source": "vault",
                "artifact_type": result.get("artifact_type"),
                "summary_snippet": result.get("summary_snippet"),
                "metadata": result.get("metadata"),
                "version": result.get("version"),
                "access_count": result.get("access_count"),
            }
    except ValueError:
        # Key not found in vault - this is expected, fall through to 404
        logger.debug(f"Key '{key}' not found in Workspace Intelligence Vault")
    except Exception as e:
        # Unexpected error - log with details but don't expose internals
        logger.error(f"Workspace Intelligence fallback failed for key '{key}': {e}", exc_info=True)

    raise HTTPException(status_code=404, detail="Context key not found in Redis or Vault")


@router.delete("/{key:path}")
async def delete_context_item(
    key: str,
    session: SecureSessionDep,
    space_id: Optional[str] = Query(None),
):
    """Delete a context item."""
    space_id = await resolve_context_space_id(session, space_id)
    full_key = f"context:{space_id}:{key}"

    # Check if exists
    if not await redis_client.exists(full_key):
        raise HTTPException(status_code=404, detail="Context key not found")

    # Delete from Redis
    await redis_client.delete(full_key)

    # Publish delete event so all clients update immediately
    await redis_sse_broker.publish(
        f"{space_id}:context",
        "context_update",
        {
            "action": "delete",
            "key": key,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    )

    return {"status": "deleted", "key": key}


class CreateContextRequest(BaseModel):
    """Request body for creating a context item."""
    key: str = Field(..., description="Key for the context item")
    value: Any = Field(..., description="Value to store (JSON-serializable)")
    ttl: Optional[int] = Field(None, description="Time-to-live in seconds (default: 24h, max: 7 days)")
    space_id: Optional[str] = Field(None, description="Override target space for API-first context writes")
    topic: Optional[str] = Field(
        None,
        max_length=50,
        description="Topic/category for organizing (lowercase, alphanumeric, dashes, underscores only)"
    )

    @field_validator('topic')
    @classmethod
    def validate_topic_field(cls, v: Optional[str]) -> Optional[str]:
        """Use shared validator for consistency across API and MCP tool"""
        return validate_topic(v)

class ScratchpadRequest(BaseModel):
    """Quick note with optional subject for easy search/reference."""
    subject: Optional[str] = Field(None, max_length=100, description="Optional subject/title for the note (helps with search)")
    note: str = Field(..., max_length=5000, description="The note content (max 5000 characters)")
    ttl: Optional[int] = Field(None, description="Time-to-live in seconds (default: 24h, max: 7 days)")


@router.post("", response_model=Dict[str, Any])
async def create_context_item(
    request: CreateContextRequest,
    session: SecureSessionDep,
):
    """Create or update a context item."""
    space_id = await resolve_context_space_id(session, request.space_id)
    full_key = f"context:{space_id}:{request.key}"

    # Validate and clamp TTL
    ttl_val = min(request.ttl or CONTEXT_DEFAULT_TTL, CONTEXT_MAX_TTL)

    try:
        value_size_bytes = ensure_context_inline_size(request.value)
    except ContextPayloadTooLarge as e:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                "Context payload is too large for inline Redis storage. "
                f"size_bytes={e.size_bytes}, max_bytes={e.max_bytes}. "
                "Upload large files/images first and store a context reference instead."
            ),
        )
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Value must be JSON serializable: {e}",
        )

    now = datetime.now(timezone.utc)
    existing_raw = await redis_client.get(full_key)
    existing_created_at = None
    existing_topic = None
    if existing_raw is not None:
        try:
            existing_parsed = json.loads(existing_raw)
            if isinstance(existing_parsed, dict):
                existing_created_at = existing_parsed.get("created_at")
                existing_topic = existing_parsed.get("topic")
        except json.JSONDecodeError:
            existing_created_at = None

    summary_payload = await summarize_context_leaf(
        key=request.key,
        value=request.value,
        topic=request.topic or existing_topic,
    ) or {}

    # Wrap value with metadata (same format as MCP tool)
    wrapped_value = {
        "value": request.value,
        "agent_name": context_actor_name(session),
        "created_at": existing_created_at or now.isoformat(),
        "updated_at": now.isoformat(),
        "ttl": ttl_val,
        "expires_at": now.timestamp() + ttl_val,
        "topic": request.topic if request.topic is not None else existing_topic,
        "value_size_bytes": value_size_bytes,
        **summary_payload,
    }

    try:
        json_val = json.dumps(wrapped_value)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Value must be JSON serializable: {e}"
        )

    await redis_client.setex(full_key, ttl_val, json_val)

    # Publish SSE event for real-time updates
    await redis_sse_broker.publish(
        f"{space_id}:context",
        "context_update",
        {
            "action": "set",
            "key": request.key,
            "value": context_record_for_event(wrapped_value, key=request.key),
            "ttl": ttl_val,
            "topic": request.topic,
            "timestamp": now.isoformat()
        }
    )

    return {
        "status": "stored",
        "key": request.key,
        "ttl": ttl_val,
        "topic": request.topic,
        "summary": wrapped_value.get("summary"),
        "updated_at": wrapped_value.get("updated_at"),
        "summary_model": wrapped_value.get("summary_model"),
        "summary_generated_at": wrapped_value.get("summary_generated_at"),
        "value_size_bytes": value_size_bytes,
    }


@router.post("/scratchpad", response_model=Dict[str, Any])
async def create_scratchpad_note(
    request: ScratchpadRequest,
    session: SecureSessionDep,
):
    """
    Quick note endpoint with optional subject for easy search/reference.

    Creates a context item with:
    - Key: Subject (slugified) + timestamp for uniqueness
    - Topic: scratchpad
    - Default TTL: 24 hours (configurable up to 7 days)

    Example: subject="deployment checklist" -> key="deployment_checklist_20251126_143052"
    No subject: key="user_20251126_143052"

    Perfect for quick notes! Subject makes it searchable by agents.
    Timestamp ensures multiple notes with same subject don't overwrite.
    """
    current_user = session.user
    space_id = await resolve_context_space_id(session, None)
    now = datetime.now(timezone.utc)
    # Include microseconds to prevent race conditions (multiple notes in same second)
    # Format: YYYYmmdd_HHMMSS_μμμμμμ (6 microsecond digits = 1M unique values per second)
    timestamp = now.strftime("%Y%m%d_%H%M%S_%f")  # Returns exactly 22 chars

    # Generate key from subject or fallback to username_timestamp
    if request.subject and request.subject.strip():
        # Slugify the subject: lowercase, replace spaces/special chars with underscore
        subject_clean = request.subject.strip().lower()
        subject_slug = re.sub(r'[^a-z0-9]+', '_', subject_clean)
        subject_slug = re.sub(r'_+', '_', subject_slug)  # Collapse multiple underscores
        subject_slug = subject_slug.strip('_')[:40]

        if not subject_slug:
            # Fallback if subject became empty after slugification (e.g. "!!!")
            key = f"{current_user.username}_{timestamp}"
        else:
            key = f"{subject_slug}_{timestamp}"
        display_subject = request.subject.strip()
    else:
        # No subject: use username + timestamp
        key = f"{current_user.username}_{timestamp}"
        display_subject = None

    full_key = f"context:{space_id}:{key}"

    # Validate and clamp TTL
    ttl_val = min(request.ttl or CONTEXT_DEFAULT_TTL, CONTEXT_MAX_TTL)

    scratchpad_value = {
        "subject": display_subject,
        "note": request.note
    }
    try:
        value_size_bytes = ensure_context_inline_size(scratchpad_value)
    except ContextPayloadTooLarge as e:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                "Scratchpad payload is too large for inline Redis storage. "
                f"size_bytes={e.size_bytes}, max_bytes={e.max_bytes}."
            ),
        )

    # Store both subject and note
    wrapped_value = {
        "value": scratchpad_value,
        "agent_name": context_actor_name(session),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "ttl": ttl_val,
        "expires_at": now.timestamp() + ttl_val,
        "topic": "scratchpad",
        "value_size_bytes": value_size_bytes,
        **(
            await summarize_context_leaf(
                key=key,
                value=scratchpad_value,
                topic="scratchpad",
            )
            or {}
        ),
    }

    json_val = json.dumps(wrapped_value)
    await redis_client.setex(full_key, ttl_val, json_val)

    # Publish SSE event
    await redis_sse_broker.publish(
        f"{space_id}:context",
        "context_update",
        {
            "action": "set",
            "key": key,
            "value": context_record_for_event(wrapped_value, key=key),
            "ttl": ttl_val,
            "topic": "scratchpad",
            "timestamp": now.isoformat()
        }
    )

    return {
        "status": "stored",
        "key": key,
        "subject": display_subject,
        "ttl": ttl_val,
        "expires_in_hours": round(ttl_val / 3600, 1),
        "message": f"Note saved as '{key}'!"
    }


async def context_event_generator(
    user: User,
    request: Request
) -> AsyncGenerator[str, None]:
    """
    Generate SSE events for shared context updates.

    Streams real-time updates when context items are set, deleted, or changed.
    """
    space_id = get_space_id(user)
    client_id = f"{user.id}:{datetime.utcnow().isoformat()}"

    try:
        logger.info(f"📡 Context SSE client connected: {client_id} for space {space_id}")

        # Capture stream's current max ID BEFORE bootstrap
        stream_start_id = await redis_sse_broker.get_stream_max_id(f"{space_id}:context")

        # Send initial connection confirmation
        yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'space_id': space_id, 'user': user.username, 'server_time': datetime.utcnow().isoformat()})}\n\n"

        # Bootstrap snapshot: send current context state
        search_pattern = f"context:{space_id}:*"

        # Use SCAN instead of KEYS to avoid blocking Redis (P0 fix)
        cursor = 0
        keys = []
        while True:
            cursor, batch = await redis_client.scan(cursor, match=search_pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        context_data = {}
        if keys:
            # Use pipeline to batch GET operations
            async with redis_client.pipeline() as pipe:
                for full_key in keys:
                    pipe.get(full_key)
                values = await pipe.execute()

            prefix_len = len(f"context:{space_id}:")
            for full_key, value in zip(keys, values):
                clean_key = full_key[prefix_len:]
                try:
                    parsed = json.loads(value) if value else None
                except json.JSONDecodeError:
                    parsed = value

                if isinstance(parsed, dict):
                    context_data[clean_key] = context_record_for_event(parsed, key=clean_key)
                else:
                    context_data[clean_key] = context_record_for_event(
                        {"value": parsed},
                        key=clean_key,
                    ).get("value")

        bootstrap_payload = {
            "context": context_data,
            "count": len(context_data),
            "server_time": datetime.utcnow().isoformat(),
            "space_id": space_id,
            "user": user.username,
        }
        yield f"event: bootstrap\ndata: {json.dumps(bootstrap_payload)}\n\n"
        logger.info(f"📦 Context SSE bootstrap sent: user={user.username} space={space_id} items={len(context_data)}")

        # Subscribe to Redis Streams for context updates
        subscription = await redis_sse_broker.subscribe(f"{space_id}:context", last_id=stream_start_id)

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                logger.info(f"📡 Context SSE client disconnected: {client_id}")
                break

            try:
                # Wait for events with timeout for heartbeat
                event = await subscription.get(timeout=SSE_HEARTBEAT_TIMEOUT_S)

                # Format and send event
                event_type = event.get("event", "context_update")
                event_data = event.get("data", {})

                # Add server timestamp for freshness tracking
                event_data["server_time"] = datetime.utcnow().isoformat()

                # Send as SSE formatted event
                sse_message = f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                yield sse_message

                logger.debug(f"📤 Sent {event_type} event to {client_id}")

            except asyncio.TimeoutError:
                # No events in 30 seconds, send heartbeat
                yield f"event: ping\ndata: {json.dumps({'timestamp': datetime.utcnow().isoformat()})}\n\n"

    except asyncio.CancelledError:
        logger.info(f"📡 Context SSE stream cancelled for {client_id}")
    except Exception as e:
        logger.error(f"❌ Context SSE error for {client_id}: {e}")
    finally:
        logger.info(f"📡 Context SSE cleanup complete for {client_id}")


@sse_router.get("/context", response_class=StreamingResponse)
async def stream_context_updates(
    request: Request,
    token: Optional[str] = Query(None)
):
    """
    SSE endpoint for real-time context updates.

    Accepts authentication via query parameter since EventSource cannot set custom headers.

    Client should connect with EventSource:
    ```javascript
    const eventSource = new EventSource('/api/v1/context/stream?token=...');
    eventSource.addEventListener('context_update', (event) => {
        const data = JSON.parse(event.data);
        // Update UI with new context data
    });
    ```
    """
    auth_token = extract_sse_auth_token(request, token)
    if not auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token"
        )

    # Authenticate using the same query/header/cookie token resolution as the newer SSE surface.
    try:
        from sqlalchemy import select
        from app.models.user import User as UserModel

        async with system_session_context() as system_ctx:
            current_user = await _resolve_user_from_bearer_token(
                auth_token, system_ctx.db, allow_agent_tokens=True
            )
            result = await system_ctx.db.execute(
                select(UserModel).where(UserModel.id == current_user.id)
            )
            current_user = result.scalar_one()

        current_user._effective_space_id = str(
            getattr(current_user, "current_space_id", None) or current_user.space_id
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SSE auth failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}"
        )

    return StreamingResponse(
        context_event_generator(current_user, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
