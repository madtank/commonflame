"""Transient cache for tool-call render payloads.

Tool-call audit rows stay intentionally thin in Postgres. We keep the initial
render payload in Redis just long enough for the dispatch pipeline to attach it
to durable message metadata.

Cache keys are namespaced by ``space_id`` so that tool_call_id collisions or
guesses across spaces cannot leak render payloads. Callers must pass the
authoritative ``space_id`` from the request session or the audit row — never
from caller-supplied input.

Both store and load **canonicalize** ``space_id`` via ``uuid.UUID(...)`` before
key construction, so write paths and read paths land on the same Redis key
regardless of whether the caller hands in a hyphenated, hyphenless, upper- or
lower-case representation. A non-parseable space_id is treated as missing and
fails closed (no write, no read).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from .redis_client import redis_client

logger = logging.getLogger(__name__)

TOOL_CALL_INITIAL_DATA_TTL = 3600


def _canonical_space_id(space_id: str | None) -> str | None:
    """Return the canonical lowercased-hyphenated form of ``space_id``, or
    ``None`` if it cannot be parsed as a UUID. Used to make sure store/load
    keys agree even when callers hand in non-canonical representations
    (the DB column is UUID-typed and round-trips through ``str()`` as
    canonical, but request bodies and session strings may not be)."""
    if not space_id:
        return None
    try:
        return str(uuid.UUID(str(space_id)))
    except (TypeError, ValueError, AttributeError):
        return None


def _tool_call_initial_data_key(space_id: str, tool_call_id: str) -> str:
    return f"tool_call_initial_data:{space_id}:{tool_call_id}"


async def store_tool_call_initial_data(
    tool_call_id: str,
    initial_data: dict[str, Any] | None,
    space_id: str,
) -> None:
    """Store structured initial widget data for later dispatch attachment.

    ``space_id`` is required and becomes part of the Redis key so render
    payloads are isolated per-space. The value is canonicalized via
    ``uuid.UUID`` so write/read keys agree across representations.
    """
    canonical_space_id = _canonical_space_id(space_id)
    if (
        not tool_call_id
        or not canonical_space_id
        or not isinstance(initial_data, dict)
        or not initial_data
    ):
        return

    try:
        await redis_client.setex(
            _tool_call_initial_data_key(canonical_space_id, tool_call_id),
            TOOL_CALL_INITIAL_DATA_TTL,
            json.dumps(initial_data),
        )
    except Exception as exc:
        logger.warning(
            "TOOL_CALL_CACHE_STORE_FAIL tool_call_id=%s space_id=%s error=%s",
            tool_call_id,
            canonical_space_id,
            exc,
            exc_info=False,
        )


async def load_tool_call_initial_data(
    tool_call_id: str,
    space_id: str,
) -> dict[str, Any] | None:
    """Load structured initial widget data for a tool call, if still cached.

    ``space_id`` is required and must match the space that originally stored
    the payload. A cross-space lookup returns ``None`` rather than the cached
    value from another space. The value is canonicalized via ``uuid.UUID`` so
    callers passing different representations of the same UUID still hit the
    same key.
    """
    canonical_space_id = _canonical_space_id(space_id)
    if not tool_call_id or not canonical_space_id:
        return None

    try:
        cached = await redis_client.get(
            _tool_call_initial_data_key(canonical_space_id, tool_call_id)
        )
        if not cached:
            return None
        return json.loads(cached)
    except Exception as exc:
        logger.warning(
            "TOOL_CALL_CACHE_LOAD_FAIL tool_call_id=%s space_id=%s error=%s",
            tool_call_id,
            canonical_space_id,
            exc,
            exc_info=False,
        )
        return None
