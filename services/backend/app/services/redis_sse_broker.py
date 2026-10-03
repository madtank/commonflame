"""
Redis-based SSE Event Broker
Production-ready replacement for in-memory org_event_broker using Redis Streams
"""

import json
import asyncio
import logging
from typing import Dict, Any, Iterable, Optional
import redis.asyncio as redis
import os

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _resolve_sse_stream_prefix() -> str:
    """Resolve the Redis stream prefix shared by SSE publishers/subscribers."""
    override = os.getenv("SSE_STREAM_PREFIX")
    if override:
        return override

    if os.getenv("DOCKER_CONTAINER"):
        return "sse:dev"

    if os.getenv("K_SERVICE"):
        return "sse:prod"

    return "sse"


class RedisSSEBroker:
    """
    Redis Streams-based SSE event broker for cross-process event delivery.

    Replaces the in-memory org_event_broker which only works within a single process.
    Uses Redis Streams for horizontally scalable, persistent event delivery.
    """

    def __init__(self, redis_url: Optional[str] = None, redis_client: Optional[redis.Redis] = None):
        # Use settings.redis_url (includes password) or fallback with password.
        # IMPORTANT: Some callers historically passed an already-open Redis client
        # as the first positional arg. Support that shape defensively so we don't
        # treat a Redis object as a URL (which triggers decode errors in from_url).
        self._redis: Optional[redis.Redis] = redis_client

        if self._redis is None and isinstance(redis_url, redis.Redis):
            # Backward-compat path: caller provided a Redis client instance.
            self._redis = redis_url
            self.redis_url = settings.redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        else:
            self.redis_url = redis_url or settings.redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")

        self.stream_prefix = _resolve_sse_stream_prefix()
        self.max_len = 10_000  # Bounded backlog for recovery

    async def _get_redis(self) -> redis.Redis:
        """
        Lazy Redis connection with large pool for concurrent SSE subscribers.

        Each SSE connection holds a blocking XREAD for up to 15s, so we need
        enough connections to handle many concurrent users. Default pool size
        of 10 would limit us to ~10 concurrent SSE subscribers.
        """
        if self._redis is None:
            self._redis = redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=20,  # Must exceed XREAD block time (15s) + buffer
                max_connections=100,  # Support 100 concurrent SSE subscribers
                health_check_interval=30,  # Keep connections alive
            )
        return self._redis

    def _stream_key(self, space_id: str) -> str:
        """Generate Redis stream key for organization"""
        return f"{self.stream_prefix}:space:{space_id}:events"

    async def publish(self, space_id: str, event: str, data: Dict[str, Any]) -> None:
        """
        Publish SSE event to Redis Stream for an organization.

        Args:
            space_id: Organization ID for routing
            event: SSE event type (message, mention, task_update, etc.)
            data: Event payload (keep small - IDs, content, timestamps)
        """
        import time
        publish_start = time.time()

        try:
            r = await self._get_redis()

            # SSE consumers key off space_id for client-side isolation. Make it
            # explicit on every event unless the caller already set it.
            if "space_id" not in data and "org_id" not in data:
                data["space_id"] = space_id

            # Inject correlation_id into event data if available (OBS-001)
            try:
                from app.middleware.correlation import get_correlation_id
                cid = get_correlation_id()
                if cid and "correlation_id" not in data:
                    data["correlation_id"] = cid
            except Exception:
                pass

            # Prepare fields for Redis stream
            fields = {
                "event": event,
                "data": json.dumps(data, separators=(",", ":")),
            }

            # Add to stream with bounded length
            stream_id = await r.xadd(
                self._stream_key(space_id),
                fields,
                maxlen=self.max_len,
                approximate=True,
            )

            publish_duration_ms = int((time.time() - publish_start) * 1000)

            # DIAGNOSTIC: Log SSE publish with timing for dispatch delay investigation
            # Include message_id if present for correlation with dispatch logs
            message_id = data.get("id") or data.get("message_id", "unknown")
            dispatch_id = data.get("dispatch_id", "n/a")
            logger.info(
                f"SSE_PUBLISH org={space_id} event={event} stream_id={stream_id} "
                f"message_id={message_id} dispatch_id={dispatch_id} "
                f"publish_ms={publish_duration_ms} published_at={time.time()}"
            )

        except Exception as e:
            # Log but don't fail the operation (broadcasting is best-effort)
            logger.warning(f"Failed to publish SSE event: org={space_id}, event={event}, error={e}")

    async def get_stream_max_id(self, space_id: str) -> str:
        """
        Get the current maximum ID in the stream (for subscribing after bootstrap).

        Returns the last entry ID in the stream, or "0-0" if stream doesn't exist.
        This allows subscribing to events that occurred during a time window.
        """
        try:
            r = await self._get_redis()
            stream_key = self._stream_key(space_id)

            # Get last entry in stream (XREVRANGE with count=1)
            result = await r.xrevrange(stream_key, "+", "-", count=1)

            if result:
                # result is list of (id, fields) tuples
                return result[0][0]  # Return the ID of the last entry
            else:
                # Stream is empty or doesn't exist, use "0-0"
                return "0-0"

        except Exception as e:
            logger.warning(f"Failed to get stream max ID: org={space_id}, error={e}")
            return "0-0"  # Safe fallback

    async def get_stream_max_ids(self, space_ids: Iterable[str]) -> Dict[str, str]:
        """Return the current maximum stream ID for each requested space."""
        return {
            str(space_id): await self.get_stream_max_id(str(space_id))
            for space_id in dict.fromkeys(str(s) for s in space_ids if s)
        }

    async def subscribe(self, space_id: str, last_id: str = "$") -> "RedisSSESubscription":
        """
        Subscribe to SSE events for an organization.

        Args:
            space_id: Organization ID to subscribe to
            last_id: Redis stream ID to start from ("$" for new events, "0" for all)

        Returns:
            RedisSSESubscription instance for reading events
        """
        return RedisSSESubscription(self, space_id, last_id)

    async def subscribe_many(
        self,
        space_ids: Iterable[str],
        last_ids: Optional[Dict[str, str]] = None,
    ) -> "RedisSSESubscription":
        """Subscribe to SSE events across multiple spaces with one XREAD."""
        normalized_space_ids = [str(s) for s in dict.fromkeys(space_ids) if s]
        if not normalized_space_ids:
            raise ValueError("at least one space_id is required")
        return RedisSSESubscription(self, normalized_space_ids, last_ids or "$")

    async def close(self):
        """Close Redis connection"""
        if self._redis:
            await self._redis.close()
            self._redis = None


class RedisSSESubscription:
    """
    Subscription handle for reading SSE events from Redis Streams.

    Implements async iteration for consuming events in SSE endpoint.
    """

    def __init__(self, broker: RedisSSEBroker, space_id: str | Iterable[str], last_id: str | Dict[str, str] = "$"):
        self.broker = broker
        if isinstance(space_id, str):
            self.space_ids = [space_id]
        else:
            self.space_ids = [str(s) for s in dict.fromkeys(space_id) if s]
        if not self.space_ids:
            raise ValueError("at least one space_id is required")
        self.space_id = self.space_ids[0]
        self.stream_key = broker._stream_key(self.space_id)
        self.stream_spaces = {broker._stream_key(s): s for s in self.space_ids}
        if isinstance(last_id, dict):
            self.last_ids = {broker._stream_key(s): last_id.get(s, "$") for s in self.space_ids}
        else:
            self.last_ids = {broker._stream_key(s): last_id for s in self.space_ids}
        self.last_id = self.last_ids[self.stream_key]

    async def get(self, timeout: float = 15.0) -> Dict[str, Any]:
        """
        Get next event from stream with timeout.

        Args:
            timeout: Max seconds to wait for new event

        Returns:
            Event dict with "event" and "data" keys

        Raises:
            asyncio.TimeoutError: If no events within timeout
        """
        try:
            r = await self.broker._get_redis()

            # Block waiting for new events
            # XREAD blocks until timeout or new data arrives
            result = await r.xread(
                dict(self.last_ids),
                count=1,
                block=int(timeout * 1000),  # milliseconds
            )

            if not result:
                # No events within timeout
                raise asyncio.TimeoutError()

            # Parse result: [(stream_key, [(id, {fields})])]
            stream_key, messages = result[0]
            message_id, fields = messages[0]

            # Update last_id for next read
            self.last_ids[stream_key] = message_id
            if stream_key == self.stream_key:
                self.last_id = message_id

            # Parse event
            event_type = fields.get("event", "message")
            event_data = json.loads(fields.get("data", "{}"))
            event_data.setdefault("space_id", self.stream_spaces.get(stream_key))

            return {
                "event": event_type,
                "data": event_data,
            }

        except asyncio.TimeoutError:
            # Re-raise timeout for heartbeat handling (this is normal - not an error)
            raise
        except Exception as e:
            # Only log actual errors, not timeouts
            logger.warning(f"Redis SSE read error: {e}")
            raise asyncio.TimeoutError()  # Treat errors as timeout for reconnection


# Global singleton instance
redis_sse_broker = RedisSSEBroker()


# Event name emitted to a live agent SSE stream when its DB placement changes.
# The adapter uses this purely as a wake/re-resolve trigger; it MUST NOT trust
# new_space_id to set its placement (DB-authoritative via GET /api/v1/agents/me).
AGENT_PLACEMENT_CHANGED_EVENT = "agent_placement_changed"


async def publish_placement_changed(old_space_id, new_space_id, agent_id) -> None:
    """Emit an ``agent_placement_changed`` SSE event for an instant agent move.

    Publishes to the agent's OLD space stream (``sse:space:{old_space_id}:events``)
    — that is where the live, still-subscribed gateway connection is reading — so
    the agent can re-resolve its placement and reconnect to the new space without
    waiting for drift-poll detection.

    Defense-in-depth: also publishes a copy to the NEW space stream, in case the
    agent already reconnected onto the new space between the DB mutation and this
    call. The adapter's control branch runs before its space-match gate, so a
    new-space copy is accepted regardless; the adapter single-flights the handler.

    No-op when ``old_space_id`` is falsy (no live in-flight stream to signal) or
    when ``old == new`` (no real move). Best-effort: ``redis_sse_broker.publish``
    swallows its own exceptions, so callers can fire-and-forget after commit.
    """
    from datetime import datetime, timezone

    if not old_space_id:
        return
    old = str(old_space_id)
    new = str(new_space_id)
    if old == new:
        return

    # Wake-only signal: carry ONLY the agent_id (so the right agent reacts) + ts.
    # We deliberately do NOT include new_space_id/old_space_id — sse.py forwards
    # every stream event to all subscribers without agent-specific filtering, so
    # cross-space IDs in this payload would leak the other space's id to members
    # who aren't in it. The adapter is DB-authoritative (re-reads its placement),
    # so it needs no space id here. (P2 review on #392.)
    payload = {
        "agent_id": str(agent_id),
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # Publish to OLD space stream (where the live connection is reading).
    await redis_sse_broker.publish(
        space_id=old,
        event=AGENT_PLACEMENT_CHANGED_EVENT,
        data=dict(payload),
    )
    # Dual-publish to NEW space stream (defense for already-reconnected agents).
    await redis_sse_broker.publish(
        space_id=new,
        event=AGENT_PLACEMENT_CHANGED_EVENT,
        data=dict(payload),
    )
