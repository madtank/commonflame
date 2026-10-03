# @ax:tag area=backend component=sse_broker tech=sse,fastapi guide=backend/AGENT.md
import asyncio
import json
import logging
from typing import Dict, Set, Any

logger = logging.getLogger(__name__)


class OrgEventBroker:
    """Simple in-memory org-scoped event broker for SSE.

    Not horizontally scalable by itself; swap to Pub/Sub in production later.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, space_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            if space_id not in self._subscribers:
                self._subscribers[space_id] = set()
            self._subscribers[space_id].add(queue)
        return queue

    async def unsubscribe(self, space_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._subscribers.get(space_id, set()).discard(queue)
                if not self._subscribers.get(space_id):
                    self._subscribers.pop(space_id, None)
            except Exception:
                # Best-effort cleanup
                pass

    async def publish(self, space_id: str, event: str, data: Dict[str, Any]) -> None:
        # Snapshot subscribers to avoid holding the lock during puts
        async with self._lock:
            subscribers = list(self._subscribers.get(space_id, set()))
        if not subscribers:
            subscribers = []
        payload = {"event": event, "data": data}
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop if overwhelmed to keep server healthy
                pass

        # Bridge to Redis Streams broker so legacy publishers still reach new SSE path
        try:
            from app.services.redis_sse_broker import redis_sse_broker  # local import to avoid circular
            await redis_sse_broker.publish(space_id=space_id, event=event, data=data)
        except Exception as e:
            # Redis publishing is best-effort; log errors but don't break legacy callers
            logger.warning(f"⚠️ SSE bridge to Redis failed: org={space_id}, event={event}, error={e}")


org_event_broker = OrgEventBroker()
