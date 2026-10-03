"""
Server-Sent Events (SSE) endpoint for real-time updates
Provides low-cost real-time messaging without aggressive polling
"""
# --------------------------------------------------------------------------------------
# LEGACY-JWT NOTICE
# This module currently authenticates SSE connections using stateless JWT access tokens.
# We are migrating to OAuth 2.1 / OpenID Connect (Authorization Code + PKCE) with
# session-bound cookies for long-lived streams. JWT handling remains only for backward
# compatibility during transition. Do NOT expand JWT usage.
#
# Migration plan (summary):
#  1) Issue short-lived access tokens via OAuth/OIDC; store session in HttpOnly cookie
#  2) Accept SSE auth via cookie session (primary) and bearer token (fallback only)
#  3) Validate tokens against the IdP (JWKS) or session store; remove query ?token usage
#  4) Sunset direct JWT verification once all clients are on OAuth/OIDC flows
# --------------------------------------------------------------------------------------
from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import json
import os
import time
from typing import AsyncGenerator, Optional, Dict, Any, List
from datetime import datetime
import logging
import uuid

from ...core.database import get_db_session
from ...core.rls import system_session_context
from ...services.redis_sse_broker import redis_sse_broker  # Production Redis Streams broker
import redis.asyncio as aioredis
from ...core.jwt_verify import get_current_user_from_token, _resolve_user_from_bearer_token
from ...models.user import User
from ...models.message import Message
from ...models.agent import Agent
from ...models.agent_space_access import AgentSpaceAccess
from ...models.conversation_card import ConversationCard
from sqlalchemy.orm import selectinload
from sqlalchemy import select

logger = logging.getLogger(__name__)

# Heartbeat interval — must stay under GCP LB default 30s backend timeout
SSE_HEARTBEAT_TIMEOUT_S = 15.0

# Agent presence TTL — must be > heartbeat interval so presence survives between heartbeats
PRESENCE_TTL_SECONDS = 30

router = APIRouter(prefix="/api/sse", tags=["sse"])


def _resolve_effective_sse_space(
    resolved_space_id: Optional[Any],
    current_space_id: Optional[Any],
    home_space_id: Optional[Any],
) -> Optional[str]:
    """Choose the space an SSE connection subscribes to, by precedence.

    1. ``resolved_space_id`` — the space the bearer-token resolver bound. For
       agent-principal tokens this is the AGENT's OWN space, so an agent keeps
       listening to its own space regardless of which space its owner account is
       currently navigated to. (Binding to the owner's active space instead is
       what blinded the fleet in the 2026-06-02 incident.)
    2. ``current_space_id`` — the user's active space (human/browser sessions,
       and the fallback when the resolver set no space).
    3. ``home_space_id`` — last-resort fallback.

    Returns ``None`` when every candidate is empty so the caller can reject the
    connection rather than subscribe to a bogus ``"None"`` stream.
    """
    for candidate in (resolved_space_id, current_space_id, home_space_id):
        if candidate:
            return str(candidate)
    return None


async def event_generator(
    user: User,
    request: Request
) -> AsyncGenerator[str, None]:
    """
    Generate SSE events for a specific user/organization

    This replaces expensive polling with efficient server push:
    - Client maintains one persistent connection
    - Server pushes updates only when they occur
    - Massive cost reduction: 1 connection vs 720 API calls/hour
    """
    space_id = str(user._effective_space_id)
    subscribed_space_ids = await _resolve_sse_subscription_space_ids(user, space_id)
    client_id = f"{user.id}:{datetime.utcnow().isoformat()}"
    bound_agent_id = getattr(user, "_bound_agent_id", None) or getattr(user, "_agent_id", None)

    # Presence tracking for bound agents (CLI agents connected via SSE)
    presence_redis = None
    presence_key = f"ax:presence:{bound_agent_id}" if bound_agent_id else None

    try:
        logger.info(
            "📡 SSE client connected: %s for org %s subscribed_spaces=%s",
            client_id,
            space_id,
            subscribed_space_ids,
        )

        # Register agent presence on connect
        if presence_key:
            try:
                from ...core.config import get_settings
                _settings = get_settings()
                presence_redis = aioredis.from_url(_settings.redis_url, decode_responses=True)
                presence_data = json.dumps({
                    "connected": True,
                    "connected_at": datetime.utcnow().isoformat(),
                    "last_heartbeat": datetime.utcnow().isoformat(),
                    "space_id": space_id,
                    "space_ids": subscribed_space_ids,
                })
                await presence_redis.setex(presence_key, PRESENCE_TTL_SECONDS, presence_data)
                logger.info("PRESENCE_CONNECTED agent_id=%s space_id=%s", bound_agent_id, space_id)
            except Exception as pres_err:
                logger.warning("PRESENCE_CONNECT_FAILED agent_id=%s error=%s", bound_agent_id, pres_err)

        # Capture stream's current max ID BEFORE bootstrap
        # This ensures we don't miss events published during bootstrap queries
        stream_start_ids = await redis_sse_broker.get_stream_max_ids(subscribed_space_ids)

        # Send initial connection confirmation
        connected_payload = {
            'status': 'connected',
            'space_id': space_id,
            'space_ids': subscribed_space_ids,
            'user': user.username,
            'server_time': datetime.utcnow().isoformat(),
        }
        yield f"event: connected\ndata: {json.dumps(connected_payload)}\n\n"

        # Bootstrap snapshot: last 50 messages for instant-fill UX
        # IMPORTANT: Acquire a short-lived DB session ONLY for bootstrap,
        # and release it before entering the long-running stream loop to
        # prevent holding a connection for the entire SSE duration.
        try:
            query = (
                select(Message)
                .options(selectinload(Message.user), selectinload(Message.agent))
                .where(Message.space_id == space_id)
                .order_by(Message.created_at.desc())
                .limit(50)
            )
            # Short-lived session scope
            identity_payload = None
            cards = []
            async with system_session_context() as system_ctx:
                result = await system_ctx.db.execute(query)
                messages = list(result.scalars().all() or [])
                identity_payload = await _build_identity_bootstrap(system_ctx.db, user, space_id)

                # Conversation cards for bootstrap
                card_result = await system_ctx.db.execute(
                    select(ConversationCard)
                    .where(ConversationCard.space_id == space_id)
                    .where(ConversationCard.channel == "main")
                    .order_by(ConversationCard.last_activity_at.desc())
                    .limit(50)
                )
                cards = list(card_result.scalars().all() or [])

            server_time = datetime.utcnow().isoformat()
            latest_timestamp = messages[0].created_at.isoformat() if messages else None

            message_list = []
            for msg in reversed(messages):  # oldest first for chat-like scroll
                message_list.append({
                    "id": str(msg.id),
                    "content": msg.content,
                    "username": msg.agent.name if getattr(msg, 'agent', None) else (msg.user.username if getattr(msg, 'user', None) else "Unknown"),
                    "channel": msg.channel or "general",
                    "uploaded_at": msg.created_at.isoformat() if getattr(msg, 'created_at', None) else None,
                    "agent_id": str(msg.agent_id) if getattr(msg, 'agent_id', None) else None,
                    "user_id": str(msg.user_id) if getattr(msg, 'user_id', None) else None,
                    "parent_id": str(msg.parent_id) if getattr(msg, 'parent_id', None) else None,
                    "metadata": msg.message_metadata or {},
                    "agent_type": "agent" if getattr(msg, 'agent_id', None) else "user",
                })

            bootstrap_payload = {
                "posts": message_list,
                "count": len(message_list),
                "server_time": server_time,
                "latest_timestamp": latest_timestamp,
                "space_id": space_id,
                "user": user.username,
                "conversation_cards": [
                    {
                        "id": str(c.id),
                        "root_message_id": str(c.root_message_id),
                        "summary": c.summary,
                        "message_count": c.message_count,
                        "participants": c.participants,
                        "status": c.status,
                        "last_activity_at": c.last_activity_at.isoformat() if c.last_activity_at else None,
                    }
                    for c in cards
                ],
            }
            yield f"event: bootstrap\ndata: {json.dumps(bootstrap_payload)}\n\n"
            logger.info(f"📦 SSE bootstrap sent: user={user.username} org={space_id} posts={len(message_list)}")

            if identity_payload:
                yield f"event: identity_bootstrap\ndata: {json.dumps(identity_payload)}\n\n"
                logger.info(
                    "🪪 Identity bootstrap sent: user=%s org=%s owned_agents=%s team_preview=%s",
                    user.username,
                    space_id,
                    len(identity_payload.get("owned_agents", [])),
                    len(identity_payload.get("team_agents_preview", [])),
                )
        except Exception as be:
            logger.error(f"⚠️ SSE bootstrap failed for user={user.username} org={space_id}: {be}")

        # Subscribe to Redis Streams AFTER bootstrap using the stream ID captured BEFORE
        # This ensures we receive events published during the bootstrap window
        # (events between stream_start_id and now will be delivered on first .get() call)
        subscription = await redis_sse_broker.subscribe_many(subscribed_space_ids, last_ids=stream_start_ids)

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                logger.info(f"📡 SSE client disconnected: {client_id}")
                break

            try:
                # Wait for events with timeout for heartbeat
                # IMPORTANT: Use 15s timeout to stay well under GCP Load Balancer's
                # default 30s backend timeout. This prevents connection resets in production.
                event = await subscription.get(timeout=SSE_HEARTBEAT_TIMEOUT_S)

                # Format and send event
                event_type = event.get("event", "message")
                event_data = event.get("data", {})

                # Add server timestamp for freshness tracking
                event_data["server_time"] = datetime.utcnow().isoformat()

                # Send as SSE formatted event
                sse_message = f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                yield sse_message

                logger.debug(f"📤 Sent {event_type} event to {client_id}")

            except asyncio.TimeoutError:
                # No events in 15 seconds, send heartbeat to keep connection alive
                yield f"event: ping\ndata: {json.dumps({'timestamp': datetime.utcnow().isoformat()})}\n\n"

                # Refresh agent presence TTL on heartbeat
                if presence_redis and presence_key:
                    try:
                        presence_data = json.dumps({
                            "connected": True,
                            "connected_at": datetime.utcnow().isoformat(),
                            "last_heartbeat": datetime.utcnow().isoformat(),
                            "space_id": space_id,
                            "space_ids": subscribed_space_ids,
                        })
                        await presence_redis.setex(presence_key, PRESENCE_TTL_SECONDS, presence_data)
                    except Exception:
                        pass  # Best effort

    except asyncio.CancelledError:
        logger.info(f"📡 SSE stream cancelled for {client_id}")
    except Exception as e:
        logger.error(f"❌ SSE error for {client_id}: {e}")
    finally:
        # Clean up presence on disconnect
        if presence_redis and presence_key:
            try:
                await presence_redis.delete(presence_key)
                logger.info("PRESENCE_DISCONNECTED agent_id=%s", bound_agent_id)
            except Exception:
                pass  # Best effort
            try:
                await presence_redis.close()
            except Exception:
                pass
        logger.info(f"📡 SSE cleanup complete for {client_id}")


# Heartbeat is handled via timeout in the main event loop
# No separate heartbeat task needed as we use asyncio.wait_for timeout


async def _resolve_sse_subscription_space_ids(user: User, fallback_space_id: str) -> List[str]:
    """Resolve every space this SSE connection should consume.

    Human/browser streams stay scoped to the current effective space. Agent
    streams listen across every active AgentSpaceAccess row for the
    authenticated agent, plus the agent's native space as a defensive fallback
    for older rows that have not been backfilled.
    """
    bound_agent_id = getattr(user, "_bound_agent_id", None) or getattr(user, "_agent_id", None)
    principal_type = getattr(user, "_principal_type", None)
    if not bound_agent_id or principal_type != "agent":
        return [fallback_space_id]

    try:
        agent_uuid = uuid.UUID(str(bound_agent_id))
    except (TypeError, ValueError):
        logger.warning("SSE_MULTI_SPACE_INVALID_AGENT_ID agent_id=%s", bound_agent_id)
        return [fallback_space_id]

    space_ids: list[str] = []
    try:
        async with system_session_context() as system_ctx:
            agent_result = await system_ctx.db.execute(
                select(Agent).where(Agent.id == agent_uuid)
            )
            agent = agent_result.scalar_one_or_none()
            native_space_id = str(getattr(agent, "space_id", "") or "") if agent else ""

            # Pull ALL access rows (not just active) so suspended/detached rows
            # are visible: those are blocking states and must NOT be re-subscribed
            # via the native/effective-space fallback below.
            access_result = await system_ctx.db.execute(
                select(AgentSpaceAccess.space_id, AgentSpaceAccess.state)
                .where(AgentSpaceAccess.agent_id == agent_uuid)
                .order_by(AgentSpaceAccess.is_default.desc(), AgentSpaceAccess.created_at.asc())
            )
            access_rows = access_result.all()
            active_spaces = [str(sid) for sid, state in access_rows if sid and state == "active"]
            spaces_with_row = {str(sid) for sid, _ in access_rows if sid}

            def _fallback_allowed(sid: str) -> bool:
                # Defensive fallback applies only when the space isn't a blocking
                # (suspended/detached) access row — i.e. its row is active, or no
                # row exists yet (un-backfilled agent).
                return bool(sid) and (sid in active_spaces or sid not in spaces_with_row)

            space_ids.extend(active_spaces)
            if _fallback_allowed(native_space_id):
                space_ids.append(native_space_id)
            if _fallback_allowed(str(fallback_space_id)):
                space_ids.append(str(fallback_space_id))
    except Exception as exc:
        logger.warning(
            "SSE_MULTI_SPACE_RESOLVE_FAILED agent_id=%s fallback_space=%s error=%s",
            bound_agent_id,
            fallback_space_id,
            exc,
        )
        return [fallback_space_id]

    resolved = list(dict.fromkeys(s for s in space_ids if s))
    return resolved or [fallback_space_id]


async def _build_identity_bootstrap(session: AsyncSession, user: User, space_id: str) -> Optional[Dict[str, Any]]:
    """Construct identity context for SSE bootstrap."""
    try:
        space_uuid = uuid.UUID(space_id)
    except ValueError:
        return None

    owned_agents: List[Dict[str, Any]] = []
    team_agents: List[Dict[str, Any]] = []

    result = await session.execute(
        select(Agent)
        .options(selectinload(Agent.user))
        .where(Agent.space_id == space_uuid)
        .order_by(Agent.name)
    )

    for agent in result.scalars().all() or []:
        entry = {
            "id": str(agent.id),
            "name": agent.name,
            "owner_user_id": str(agent.user_id),
            "visibility": agent.visibility_level,
            "status": agent.status,
        }
        if agent.user_id == user.id:
            owned_agents.append(entry)
        else:
            visibility = (agent.visibility_level or "org_visible").lower()
            if visibility in {"public", "org_visible"}:
                team_agents.append(entry)

    viewer_payload = {
        "id": str(user.id),
        "display_name": user.username or user.full_name,
        "handle": f"@{user.username}" if user.username else user.email,
    }

    return {
        "viewer": viewer_payload,
        "space_id": space_id,
        "owned_agents": owned_agents,
        "team_agents_preview": team_agents[:10],
        "team_agents_total": len(team_agents),
        "server_time": datetime.utcnow().isoformat(),
    }


@router.get("/messages")
async def sse_messages_stream(
    request: Request,
    token: Optional[str] = Query(None, description="JWT token for EventSource auth"),
):
    """
    Stream real-time messages and events via Server-Sent Events

    Cost Analysis:
    - Polling (old): 12 requests/min = 720/hour = 17,280/day per user
    - SSE (new): 1 persistent connection + events only when needed
    - Reduction: ~99.9% fewer requests

    Benefits:
    - Near real-time updates (< 100ms latency)
    - Minimal server load (event-driven, not polling)
    - Automatic reconnection handled by browser
    - Works through proxies and firewalls
    """
    # LEGACY-JWT: Authentication for SSE currently accepts JWTs as a transition measure.
    # OAuth/OIDC migration guidance:
    #  - Primary: accept session (HttpOnly) cookie established by OAuth code flow (PKCE)
    #  - Fallback: accept Bearer access token in Authorization header (short-lived only)
    #  - Remove/disable query-string ?token once clients are migrated (it is less secure)
    #  - Ensure SSE proxying preserves cookies and headers (no buffering, HTTP/1.1)
    # The following three methods are ordered by desired future priority.

    # Method 1: Token from query parameter (preferred for EventSource)
    auth_token = token

    # OAuth/OIDC-preferred path (Bearer access token) – keep short-lived; refresh via IdP
    if not auth_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]

    # OAuth/OIDC session cookie path (TARGET): preferred for long-lived SSE streams
    if not auth_token:
        # Try to get token from cookie (for browser-based SSE)
        access_cookie = request.cookies.get("access_token")
        if access_cookie:
            auth_token = access_cookie
            logger.info("SSE using cookie-based authentication")

    if not auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for SSE connection"
        )

    # Authenticate via unified Cognito/legacy path (same as all other endpoints)
    try:
        async with system_session_context() as system_ctx:
            current_user = await _resolve_user_from_bearer_token(
                auth_token, system_ctx.db, allow_agent_tokens=True, request=request
            )
            runtime_attrs = {
                name: getattr(current_user, name)
                for name in (
                    "_agent_id",
                    "_agent_name",
                    "_bound_agent_id",
                    "_credential_agent_scope",
                    "_credential_allowed_agent_ids",
                    "_effective_space_id",
                    "_principal_type",
                    "_principal_id",
                    "_principal_agent_id",
                    "_principal_user_id",
                )
                if hasattr(current_user, name)
            }
            # Ensure user is attached to this session's identity map by
            # re-fetching within the system context, then restore auth runtime
            # attributes added by token resolution. Without this, agent SSE
            # connections collapse back to a single human current_space_id.
            result = await system_ctx.db.execute(
                select(User).where(User.id == current_user.id)
            )
            current_user = result.scalar_one()
            for name, value in runtime_attrs.items():
                setattr(current_user, name, value)

        # Derive the effective space the SSE stream subscribes to. Prefer the
        # space the bearer-token resolver bound (for agent-principal tokens this
        # is the AGENT's own/native space) over the owner User's active space.
        # Human/browser sessions are unchanged because the resolver's effective
        # space already matches the user's current space.
        db_space_id = _resolve_effective_sse_space(
            getattr(current_user, "_effective_space_id", None),
            current_user.current_space_id,
            current_user.space_id,
        )
        current_user._effective_space_id = db_space_id

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SSE auth error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )

    # Validate user has access - use the JWT space_id we just set
    if not current_user._effective_space_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must be in an organization to receive events"
        )

    # NOTE: When switching to cookie-based OAuth sessions, confirm CORS and Set-Cookie
    # headers pass through the proxy and that SSE uses HTTP/1.1 with buffering disabled.
    # Set up SSE response headers with proper CORS
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }

    # Configure CORS based on environment
    environment = os.getenv("ENVIRONMENT", "development")
    if environment == "production":
        allowed_origins = ["https://paxai.app", "https://www.paxai.app", "https://next.paxai.app"]
    else:
        # Development: Allow localhost origins
        allowed_origins = ["http://localhost:3000", "http://localhost:8001", "http://127.0.0.1:3000"]

    # Set CORS header if origin is allowed
    request_origin = request.headers.get("origin")
    if request_origin and request_origin in allowed_origins:
        headers["Access-Control-Allow-Origin"] = request_origin
        headers["Access-Control-Allow-Credentials"] = "true"
    elif environment == "development" and request_origin:
        # In development, echo actual origin to support credentialed connections
        # (browsers reject Access-Control-Allow-Origin: * with credentials)
        headers["Access-Control-Allow-Origin"] = request_origin
        headers["Access-Control-Allow-Credentials"] = "true"

    # Return streaming response
    return StreamingResponse(
        event_generator(current_user, request),
        media_type="text/event-stream",
        headers=headers
    )


@router.get("/health")
async def sse_health_check():
    """Check if SSE service is operational"""
    return {
        "status": "healthy",
        "service": "sse",
        "timestamp": datetime.utcnow().isoformat(),
        "features": {
            "messages": True,
            "tasks": True,
            "mentions": True,
            "presence": True,
        }
    }
