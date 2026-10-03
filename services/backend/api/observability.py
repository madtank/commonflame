"""
Observability API Endpoints
Lightweight telemetry for browser and monitoring
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import time
import json

from app.auth import get_current_user
from app.models.user import User
from app.core.redis_client import redis_client

router = APIRouter(prefix="/obs", tags=["observability"])

# Cache for heartbeat data (30s TTL)
HEARTBEAT_CACHE_TTL = 30

@router.get("/heartbeat")
async def heartbeat(
    request: Request,
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Lightweight heartbeat for browser telemetry
    Returns rate limit status and session health metrics
    Not counted against normal rate limits
    """
    user_id = str(current_user.id)
    org_id = str(current_user.current_org_id)

    # Check cache first
    cache_key = f"obs:heartbeat:{user_id}"
    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    # Calculate metrics
    try:
        # Get agent count for this org
        agent_pattern = f"mcp:session:*"
        agent_count = 0
        async for key in redis_client.scan_iter(agent_pattern):
            session_data = await redis_client.get(key)
            if session_data:
                session = json.loads(session_data)
                if session.get("org_id") == org_id and session.get("state") == "open":
                    agent_count += 1

        # Calculate session reuse ratio (last 5 minutes)
        now = time.time()
        window_start = now - 300  # 5 minutes

        # Get reuse metrics from Redis
        reuse_key = f"metrics:reuse:{org_id}"
        created_key = f"metrics:created:{org_id}"

        reused_count = int(await redis_client.get(reuse_key) or 0)
        created_count = int(await redis_client.get(created_key) or 0)

        total = created_count + reused_count
        reuse_ratio = (reused_count / total * 100) if total > 0 else 0

        # Get last 429 timestamp
        last_429_key = f"obs:last_429:{user_id}"
        last_429_timestamp = await redis_client.get(last_429_key)
        last_429_at = None
        if last_429_timestamp:
            last_429_at = datetime.fromtimestamp(float(last_429_timestamp)).isoformat()

        # Check current rate limit status
        rate_status = "ok"
        rate_limit_key = f"rl:mcp:{user_id}"
        current_count = await redis_client.get(rate_limit_key)
        if current_count and int(current_count) > 100:  # Near limit
            rate_status = "warning"
        elif current_count and int(current_count) > 115:  # At limit
            rate_status = "limited"

        response = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "rateStatus": rate_status,
            "agentCount": agent_count,
            "sessionReuseRatio": round(reuse_ratio, 1),
            "last429At": last_429_at,
            "metrics": {
                "sessionsCreated": created_count,
                "sessionsReused": reused_count,
                "currentRateCount": int(current_count or 0)
            }
        }

        # Cache the response
        await redis_client.setex(
            cache_key,
            HEARTBEAT_CACHE_TTL,
            json.dumps(response)
        )

        return response

    except Exception as e:
        # Return degraded response on error
        return {
            "status": "degraded",
            "timestamp": datetime.utcnow().isoformat(),
            "rateStatus": "unknown",
            "error": str(e)
        }

@router.post("/rate-event")
async def rate_event(
    request: Request,
    current_user: User = Depends(get_current_user)
) -> Dict[str, str]:
    """
    Record rate limit event from browser
    One-shot telemetry when browser gets 429
    """
    body = await request.json()

    # Extract rate limit details
    scope = body.get("scope", "unknown")
    key = body.get("key", "")
    retry_after = body.get("retryAfter", 0)
    endpoint = body.get("endpoint", "")

    # Record the 429 event
    user_id = str(current_user.id)
    last_429_key = f"obs:last_429:{user_id}"
    await redis_client.set(last_429_key, time.time())

    # Log for analysis
    import logging
    logger = logging.getLogger("observability")
    logger.warning(json.dumps({
        "event": "rate_limit_hit",
        "source": "browser",
        "user_id": user_id,
        "org_id": str(current_user.current_org_id),
        "scope": scope,
        "key": key,
        "retry_after": retry_after,
        "endpoint": endpoint,
        "timestamp": datetime.utcnow().isoformat()
    }))

    # Track in metrics (mcp_modular was extracted to separate repo)
    try:
        from mcp_modular.core.metrics import track_rate_limit
        track_rate_limit(key, endpoint)
    except ImportError:
        pass  # Metrics tracking not available

    return {"status": "recorded"}

@router.post("/error-event")
async def error_event(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user)
) -> Dict[str, str]:
    """
    Record client-side errors for debugging
    Helps track browser issues and UX problems
    """
    body = await request.json()

    # Extract error details
    error_type = body.get("type", "unknown")
    message = body.get("message", "")
    stack = body.get("stack", "")
    url = body.get("url", "")
    user_agent = request.headers.get("User-Agent", "")

    # Log for analysis
    import logging
    logger = logging.getLogger("observability")
    logger.error(json.dumps({
        "event": "client_error",
        "source": "browser",
        "user_id": str(current_user.id) if current_user else "anonymous",
        "error_type": error_type,
        "message": message,
        "url": url,
        "user_agent": user_agent,
        "timestamp": datetime.utcnow().isoformat()
    }))

    return {"status": "recorded"}

@router.get("/session-events")
async def session_events(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """
    SSE stream for real-time session events
    Pushes session create/expire notifications
    """
    from fastapi.responses import StreamingResponse
    import asyncio

    async def event_generator():
        """Generate SSE events for session changes"""
        org_id = str(current_user.current_org_id)

        # Send initial connection event
        yield f"data: {json.dumps({'type': 'connected', 'org': org_id})}\n\n"

        # Subscribe to Redis keyspace notifications
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(
            f"__keyevent@0__:expired",
            f"__keyevent@0__:set",
            f"obs:session:created",
            f"obs:session:expired",
            f"obs:rate:blocked"
        )

        try:
            while True:
                # Check for messages with timeout
                message = await pubsub.get_message(timeout=30)

                if message and message['type'] == 'message':
                    channel = message['channel'].decode()
                    data = message['data'].decode() if message['data'] else ""

                    # Parse event type
                    event = None
                    if "session:created" in channel:
                        event = {"type": "session_created", "data": data}
                    elif "session:expired" in channel:
                        event = {"type": "session_expired", "data": data}
                    elif "rate:blocked" in channel:
                        event = {"type": "rate_limited", "data": data}

                    if event:
                        yield f"data: {json.dumps(event)}\n\n"

                # Send keepalive every 30s
                yield f": keepalive\n\n"
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            await pubsub.unsubscribe()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
