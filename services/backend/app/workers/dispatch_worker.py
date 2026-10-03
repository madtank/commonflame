"""
Dispatch Worker - Redis Stream Consumer for Local Development

This worker mimics Cloud Tasks behavior for local development:
- Consumes dispatch tasks from Redis Streams
- Uses the UNIFIED dispatch executor (same as GCP Cloud Tasks path)
- Handles retries with exponential backoff
- Acknowledges successful dispatches

IMPORTANT: This uses the same dispatch_executor as the GCP Cloud Tasks path.
This ensures LOCAL testing validates the SAME code that runs in production.

Usage:
    python -m app.workers.dispatch_worker

In Docker Compose:
    command: python -m app.workers.dispatch_worker
"""

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path
from uuid import uuid4

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.core.config import get_settings
from app.core.dispatch_executor import DispatchStatus, execute_dispatch
from app.core.dispatch_queue import DispatchTask, RedisStreamQueue, get_dispatch_queue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("dispatch_worker")

# Retry configuration (mimics Cloud Tasks backoff)
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 60
BACKOFF_MULTIPLIER = 2

# Staleness: drop dispatches older than this (seconds)
DISPATCH_MAX_AGE_SECONDS = int(os.getenv("DISPATCH_MAX_AGE_SECONDS", "300"))  # 5 minutes


class DispatchWorker:
    """
    Background worker that consumes Redis dispatch queue.
    Mimics Cloud Tasks behavior for local development.
    """

    def __init__(self):
        self.settings = get_settings()
        self.queue: RedisStreamQueue | None = None
        self.consumer_name = f"worker-{uuid4().hex[:8]}"
        self.running = False
        self.retry_counts: dict[str, int] = {}
        self._heartbeat_task: asyncio.Task | None = None

    async def _heartbeat_loop(self):
        """Periodically update heartbeat key in Redis for health monitoring."""
        while self.running:
            try:
                if self.queue and self.queue.redis:
                    await self.queue.redis.setex(
                        f"worker:{self.consumer_name}:heartbeat",
                        30,  # 30 second TTL
                        "alive",
                    )
            except Exception as e:
                logger.warning(f"Heartbeat update failed: {e}")
            await asyncio.sleep(10)  # Update every 10 seconds

    async def setup(self):
        """Initialize the queue connection."""
        queue = get_dispatch_queue()
        if not isinstance(queue, RedisStreamQueue):
            logger.warning("Dispatch worker only runs with Redis queue (USE_CLOUD_TASKS=false)")
            return False

        self.queue = queue
        logger.info(f"Worker {self.consumer_name} connected to Redis stream")
        return True

    def calculate_backoff(self, retry_count: int) -> float:
        """Calculate exponential backoff delay."""
        delay = INITIAL_BACKOFF_SECONDS * (BACKOFF_MULTIPLIER ** retry_count)
        return min(delay, MAX_BACKOFF_SECONDS)

    async def _persist_dlq_fallback_notice(self, task: DispatchTask) -> str | None:
        """Persist a durable space-agent reply when a dispatch exhausts retries."""
        try:
            from datetime import datetime, timezone
            from uuid import UUID

            from sqlalchemy import text

            from app.core.database import AsyncSessionLocal
            from app.core.redis_client import redis_client
            from app.models.message import Message
            from app.models.space import Space
            from app.services.messages_notifications import MessagesNotificationHelper
            from app.services.redis_sse_broker import redis_sse_broker
            from app.services.space_agent_service import ensure_space_agent_for_org

            space_id = task.payload.get("space_id")
            message_id = task.payload.get("message_id") or task.message_id
            if not space_id or not message_id:
                logger.warning(
                    "DISPATCH_DLQ_FALLBACK_SKIP dispatch_id=%s reason=missing_required_fields "
                    "space_id=%s message_id=%s",
                    task.dispatch_id,
                    space_id,
                    message_id,
                )
                return None

            space_uuid = UUID(str(space_id))
            original_message_uuid = UUID(str(message_id))

            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("SELECT set_config('app.current_space_id', :sid, true)"),
                    {"sid": str(space_uuid)},
                )

                org = await session.get(Space, space_uuid)
                space_agent = await ensure_space_agent_for_org(session, org) if org else None
                if not space_agent or space_agent.status != "active":
                    logger.warning(
                        "DISPATCH_DLQ_FALLBACK_SKIP dispatch_id=%s reason=no_active_space_agent",
                        task.dispatch_id,
                    )
                    return None

                original_msg = await session.get(Message, original_message_uuid)
                if not original_msg:
                    logger.warning(
                        "DISPATCH_DLQ_FALLBACK_SKIP dispatch_id=%s reason=parent_message_missing "
                        "message_id=%s",
                        task.dispatch_id,
                        message_id,
                    )
                    return None

                notifier = MessagesNotificationHelper(
                    db=session,
                    redis_client=redis_client,
                    sse_broker=redis_sse_broker,
                )
                if await notifier._is_agent_disabled_cache(str(space_agent.id), str(space_uuid)):
                    logger.info(
                        "DISPATCH_DLQ_FALLBACK_SKIP dispatch_id=%s reason=space_agent_disabled agent_id=%s space_id=%s",
                        task.dispatch_id,
                        space_agent.id,
                        space_uuid,
                    )
                    return None

                notice = Message(
                    space_id=space_uuid,
                    agent_id=space_agent.id,
                    user_id=None,
                    content=(
                        f"Agent {task.agent_name} did not respond after {MAX_RETRIES} retries. "
                        "I can take over this thread if you want to continue here."
                    ),
                    channel=original_msg.channel or "main",
                    parent_id=original_message_uuid,
                    message_metadata={
                        "dispatch_id": task.dispatch_id,
                        "fallback": "space_agent",
                        "notice_kind": "dispatch_dlq_fallback",
                        "unresolved_dispatch": True,
                        "original_agent": task.agent_name,
                        "retry_count": MAX_RETRIES,
                    },
                    created_at=datetime.now(timezone.utc),
                )

                session.add(notice)
                await session.commit()
                await session.refresh(notice)

                await notifier.broadcast_sse(
                    space_id=space_uuid,
                    msg=notice,
                    author_name=space_agent.name,
                    author_type="agent",
                )

                return str(notice.id)
        except Exception as fallback_err:
            logger.warning(
                "DISPATCH_DLQ_FALLBACK_ERROR dispatch_id=%s error=%s",
                task.dispatch_id,
                fallback_err,
            )
            return None

    async def process_task(self, task: DispatchTask) -> bool:
        """
        Process a single dispatch task using the UNIFIED dispatch executor.

        This uses the SAME execute_dispatch() function as the GCP Cloud Tasks path,
        ensuring LOCAL and PRODUCTION use identical dispatch logic.

        Returns True if successfully processed, False if should retry.
        """
        # Staleness check — drop dispatches older than threshold
        # Redis Stream entry IDs are formatted as "<epoch_ms>-<seq>"
        if task.stream_entry_id and DISPATCH_MAX_AGE_SECONDS > 0:
            try:
                entry_ts_ms = int(task.stream_entry_id.split("-")[0])
                age_seconds = (time.time() * 1000 - entry_ts_ms) / 1000
                if age_seconds > DISPATCH_MAX_AGE_SECONDS:
                    logger.warning(
                        f"DISPATCH_STALE dispatch_id={task.dispatch_id} "
                        f"agent_name={task.agent_name} age_seconds={int(age_seconds)}"
                    )
                    return True  # Mark as complete — will be ACK'd
            except (ValueError, IndexError):
                pass

        retry_count = self.retry_counts.get(task.task_id, 0)

        # Use the unified dispatch executor (SAME code as GCP path)
        result = await execute_dispatch(
            payload=task.payload,
            dispatch_id=task.dispatch_id,
            retry_count=retry_count,
            task_id=task.task_id,
        )

        # Determine if task should be retried
        # Success and permanent failures (4xx, missing URL) are complete
        # Retryable failures (5xx, timeout, connection) should be retried
        is_complete = result.is_success or not result.should_retry

        if is_complete:
            self.retry_counts.pop(task.task_id, None)

        return is_complete

    async def handle_retry(self, task: DispatchTask):
        """Handle retry logic with exponential backoff."""
        task_id = task.task_id
        retry_count = self.retry_counts.get(task_id, 0) + 1
        self.retry_counts[task_id] = retry_count

        if retry_count >= MAX_RETRIES:
            logger.error(
                f"DISPATCH_DLQ dispatch_id={task.dispatch_id} task_id={task_id} "
                f"agent_name={task.agent_name} reason=max_retries_exceeded "
                f"retries={retry_count}"
            )
            # Clear retry count (task is dead)
            self.retry_counts.pop(task_id, None)
            # ACK the task so autoclaim_stale doesn't reclaim it forever
            if task.stream_entry_id and self.queue:
                try:
                    await self.queue.acknowledge(
                        task.task_id,
                        task.stream_entry_id,
                        dispatch_id=task.dispatch_id,
                        agent_name=task.agent_name,
                    )
                except Exception as ack_err:
                    logger.warning(f"DISPATCH_DLQ_ACK_ERROR dispatch_id={task.dispatch_id} error={ack_err}")
            # Notify frontend so it can clear the "Waiting for first response..." spinner
            try:
                space_id = task.payload.get("space_id", "")
                if space_id:
                    from app.services.redis_sse_broker import redis_sse_broker
                    await redis_sse_broker.publish(
                        space_id=space_id,
                        event="agent_processing",
                        data={
                            "status": "error",
                            "dispatch_id": task.dispatch_id,
                            "message_id": task.message_id,
                            "agent_name": task.agent_name,
                            "error": "Agent unavailable after retries",
                        },
                    )
            except Exception as sse_err:
                logger.warning(f"DISPATCH_DLQ_SSE_ERROR dispatch_id={task.dispatch_id} error={sse_err}")

            # Dead-letter fallback: persist a visible space-agent reply so the
            # notice survives refresh instead of being SSE-only.
            reply_message_id = await self._persist_dlq_fallback_notice(task)
            if reply_message_id:
                logger.info(
                    "DISPATCH_DLQ_FALLBACK dispatch_id=%s agent_name=%s fallback=space_agent "
                    "reply_message_id=%s",
                    task.dispatch_id,
                    task.agent_name,
                    reply_message_id,
                )

            return

        backoff = self.calculate_backoff(retry_count)
        logger.info(
            f"DISPATCH_RETRY dispatch_id={task.dispatch_id} task_id={task_id} "
            f"agent_name={task.agent_name} retry={retry_count}/{MAX_RETRIES} "
            f"backoff={backoff}s"
        )

        await asyncio.sleep(backoff)

        # Re-process the task
        success = await self.process_task(task)
        if not success:
            await self.handle_retry(task)

    async def run(self):
        """Main worker loop."""
        import time

        if not await self.setup():
            logger.error("Failed to initialize worker, exiting")
            return

        self.running = True
        last_autoclaim = time.time()
        AUTOCLAIM_INTERVAL = 60  # Reclaim stale tasks every 60 seconds

        # Start heartbeat for health monitoring
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        logger.info(f"Starting dispatch worker: {self.consumer_name}")

        while self.running:
            try:
                # Read tasks from the stream (blocking with 5s timeout)
                tasks = await self.queue.read_pending(
                    consumer_name=self.consumer_name,
                    count=10,
                    block_ms=5000,
                )

                # Process new tasks
                for task in tasks:
                    if not self.running:
                        break

                    success = await self.process_task(task)

                    if success:
                        # Acknowledge successful dispatch (XACK with stream entry ID)
                        await self.queue.acknowledge(
                            task.task_id,
                            task.stream_entry_id,
                            dispatch_id=task.dispatch_id,
                            agent_name=task.agent_name,
                        )
                    else:
                        # Handle retry (task stays pending until reclaimed)
                        await self.handle_retry(task)

                # Periodically reclaim stale tasks from dead consumers
                if time.time() - last_autoclaim > AUTOCLAIM_INTERVAL:
                    try:
                        stale_tasks = await self.queue.autoclaim_stale(
                            consumer_name=self.consumer_name,
                            min_idle_ms=60000,  # 1 minute idle
                            count=10,
                        )
                        for stale_task in stale_tasks:
                            if not self.running:
                                break
                            success = await self.process_task(stale_task)
                            if success:
                                await self.queue.acknowledge(
                                    stale_task.task_id,
                                    stale_task.stream_entry_id,
                                    dispatch_id=stale_task.dispatch_id,
                                    agent_name=stale_task.agent_name,
                                )
                            else:
                                await self.handle_retry(stale_task)
                    except Exception as autoclaim_err:
                        logger.warning(f"Autoclaim failed: {autoclaim_err}")
                    last_autoclaim = time.time()

            except asyncio.CancelledError:
                logger.info("Worker cancelled, shutting down")
                break
            except Exception as e:
                logger.error(f"Worker loop error: {e}")
                if "NOGROUP" in str(e):
                    # Consumer group was deleted, recreate it
                    logger.info("Consumer group missing, recreating...")
                    await self.setup()
                await asyncio.sleep(1)  # Prevent tight loop on persistent errors

        logger.info(f"Worker {self.consumer_name} stopped")

    def stop(self):
        """Signal the worker to stop."""
        self.running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()


async def main():
    """Entry point for the dispatch worker."""
    worker = DispatchWorker()

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        worker.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
        worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
