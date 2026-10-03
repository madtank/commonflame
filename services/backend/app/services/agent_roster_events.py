"""SSE event publisher for agent roster changes.

Emits `agent_roster_changed` events when agents are created, updated,
deleted, or approved via the draft flow. Frontend listens and invalidates
its agent-directory query so the composer/roster UI doesn't go stale
between refreshes.

Best-effort: SSE publish failures never block the underlying mutation.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

logger = logging.getLogger(__name__)

AgentRosterAction = Literal["created", "updated", "deleted", "approved"]


async def publish_agent_roster_changed(
    space_id: str,
    action: AgentRosterAction,
    *,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> None:
    """Publish an `agent_roster_changed` SSE event for the given space.

    Args:
        space_id: Space the agent belongs to. Event is scoped to this space.
        action: What happened — created / updated / deleted / approved.
        agent_id: UUID of the agent (optional but recommended for client filtering).
        agent_name: Agent handle (optional, useful for client logging).
    """
    if not space_id:
        logger.warning(
            "agent_roster_changed skipped: missing space_id action=%s agent=%s",
            action,
            agent_id,
        )
        return

    try:
        from app.services.redis_sse_broker import redis_sse_broker

        await redis_sse_broker.publish(
            space_id=str(space_id),
            event="agent_roster_changed",
            data={
                "action": action,
                "agent_id": str(agent_id) if agent_id else None,
                "agent_name": agent_name,
            },
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception(
            "Failed to publish agent_roster_changed space=%s action=%s agent=%s",
            space_id,
            action,
            agent_id,
        )
