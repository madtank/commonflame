# app/services/mcp_event_publishers.py
"""
MCP Redis Streams Event Publishers
Publishes events so MCP agents can receive real-time updates via blocking wait.

IMPORTANT: Uses a fresh Redis client with decode_responses=False for each publish,
matching the MCP server's pattern. The shared redis_client uses decode_responses=True
which causes issues with xadd binary stream IDs.
"""

import json
import logging
import os
import time
from typing import Optional

from redis.asyncio import Redis

from ..core.config import settings

logger = logging.getLogger(__name__)

# Stream prefix - must match MCP server config
def _resolve_sse_stream_prefix() -> str:
    override = os.getenv("SSE_STREAM_PREFIX")
    if override:
        return override

    if os.getenv("DOCKER_CONTAINER"):
        return "sse:dev"

    if os.getenv("K_SERVICE"):
        return "sse:prod"

    return "sse:local"


SSE_STREAM_PREFIX = _resolve_sse_stream_prefix()
REDIS_URL = settings.redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")


async def get_redis_client() -> Redis:
    """Get fresh Redis client for publishing (decode_responses=False for streams)."""
    logger.debug(f"Creating fresh Redis client for event publishing: {REDIS_URL}")
    return Redis.from_url(REDIS_URL, decode_responses=False, socket_keepalive=True)


def stream_key(space_id: str, topic: str) -> str:
    """Generate Redis stream key for org/topic"""
    return f"{SSE_STREAM_PREFIX}:space:{space_id}:{topic}"


async def publish_mention_event(
    space_id: str,
    message_id: str,
    content: str,
    mentions: list[str],
    sender_name: str,
    created_at: str,
    actor_roster_id: Optional[str] = None,
):
    """Publish mention event to Redis Streams"""
    logger.info(f"🔔 publish_mention_event called: org={space_id}, mentions={mentions}, sender={sender_name}")
    if not mentions:
        logger.debug("No mentions provided, skipping publish")
        return

    redis = None
    try:
        redis = await get_redis_client()
        payload = {
            "id": str(message_id),
            "content": content,
            "mentions": mentions,
            "sender_name": sender_name,
            "created_at": created_at,
            "actor_roster_id": actor_roster_id,
        }
        fields = {
            "event": "mention",
            "ts": str(time.time()),
            "data": json.dumps(payload, separators=(",", ":")),
        }
        stream_id = await redis.xadd(stream_key(space_id, "mentions"), fields, maxlen=10000, approximate=True)
        logger.info(f"Mention event published: org={space_id}, mentions={mentions}, stream_id={stream_id}")
        return stream_id
    except Exception as e:
        logger.error(f"Failed to publish mention event: {e}")
        return None
    finally:
        if redis:
            await redis.close()


async def publish_message_event(
    space_id: str,
    message_id: str,
    content: str,
    sender_name: str,
    created_at: str,
    actor_roster_id: Optional[str] = None,
    parent_id: Optional[str] = None,
    attachments: Optional[list[dict]] = None,
):
    """Publish message event to Redis Streams"""
    logger.info(f"🔔 publish_message_event called: org={space_id}, sender={sender_name}")
    redis = None
    try:
        redis = await get_redis_client()
        attachment_refs = attachments or []
        payload = {
            "id": str(message_id),
            "content": content,
            "sender_name": sender_name,
            "created_at": created_at,
            "actor_roster_id": actor_roster_id,
            "parent_id": parent_id,
            "attachments": attachment_refs,
            "metadata": {"attachments": attachment_refs} if attachment_refs else {},
        }
        fields = {
            "event": "message",
            "ts": str(time.time()),
            "data": json.dumps(payload, separators=(",", ":")),
        }
        stream_id = await redis.xadd(stream_key(space_id, "messages"), fields, maxlen=10000, approximate=True)
        logger.info(f"Message event published: org={space_id}, stream_id={stream_id}")
        return stream_id
    except Exception as e:
        logger.error(f"Failed to publish message event: {e}")
        return None
    finally:
        if redis:
            await redis.close()
