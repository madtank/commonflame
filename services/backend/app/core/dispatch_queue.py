"""
Dispatch Queue Abstraction Layer

Provides a unified interface for reliable cloud agent dispatch with:
- Cloud Tasks for production (managed retries, cold start handling)
- Redis Streams for local development (portable, no GCP dependency)

Usage:
    queue = get_dispatch_queue()
    task_id = await queue.enqueue(agent_id, payload)
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class RetryPolicy:
    """Configuration for dispatch retry behavior."""
    max_attempts: int = 5
    min_backoff_seconds: int = 1
    max_backoff_seconds: int = 60
    max_doublings: int = 4  # Exponential backoff doublings


@dataclass
class DispatchTask:
    """Represents a dispatch task in the queue."""
    task_id: str
    agent_id: str
    agent_name: str
    message_id: str
    payload: dict
    dispatch_id: str
    dispatch_type: str = "cloud"  # "cloud" for agent_runner, "webhook" for external gateway
    attempt: int = 0
    created_at: Optional[str] = None
    stream_entry_id: Optional[str] = None  # Redis Stream entry ID for XACK

    @classmethod
    def from_stream_message(cls, msg_id: str, fields: dict, increment_attempt: bool = False) -> "DispatchTask":
        """Parse a DispatchTask from Redis Stream message fields."""
        attempt = int(fields.get("attempt", 0))
        if increment_attempt:
            attempt += 1
        return cls(
            task_id=fields.get("task_id", ""),
            agent_id=fields.get("agent_id", ""),
            agent_name=fields.get("agent_name", ""),
            message_id=fields.get("message_id", ""),
            payload=json.loads(fields.get("payload", "{}")),
            dispatch_id=fields.get("dispatch_id", ""),
            dispatch_type=fields.get("dispatch_type", "cloud"),  # Default to cloud for backwards compat
            attempt=attempt,
            stream_entry_id=msg_id,
        )


class DispatchQueue(ABC):
    """Abstract base class for dispatch queue implementations."""

    def __init__(self, retry_policy: Optional[RetryPolicy] = None):
        self.retry_policy = retry_policy or RetryPolicy()

    @abstractmethod
    async def enqueue(
        self,
        agent_id: str,
        agent_name: str,
        message_id: str,
        payload: dict,
        dispatch_id: str,
        delay_seconds: int = 0,
    ) -> str:
        """
        Add a dispatch task to the queue.

        Args:
            agent_id: Target agent UUID
            agent_name: Target agent name (for logging)
            message_id: Source message UUID
            payload: Full dispatch payload for agent runner
            dispatch_id: Unique dispatch ID for tracing
            delay_seconds: Optional delay before processing

        Returns:
            task_id: Unique identifier for this task
        """
        pass

    @abstractmethod
    async def acknowledge(
        self,
        task_id: str,
        stream_entry_id: Optional[str] = None,
        dispatch_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> bool:
        """
        Mark a task as successfully processed.

        Args:
            task_id: Task to acknowledge
            stream_entry_id: Optional stream entry ID (used by Redis implementation)
            dispatch_id: Optional dispatch ID for log correlation
            agent_name: Optional agent name for log correlation

        Returns:
            True if acknowledged, False if task not found
        """
        pass

    @abstractmethod
    async def get_pending_count(self) -> int:
        """Get count of pending (unprocessed) tasks."""
        pass


class RedisStreamQueue(DispatchQueue):
    """
    Redis Streams implementation for local development.

    Uses XADD/XREADGROUP for reliable delivery with consumer groups.
    """

    def __init__(
        self,
        redis_client: Any,
        stream_name: str = "dispatch:cloud_agents",
        consumer_group: str = "dispatch-workers",
        retry_policy: Optional[RetryPolicy] = None,
    ):
        super().__init__(retry_policy)
        self.redis = redis_client
        self.stream_name = stream_name
        self.consumer_group = consumer_group
        self._group_created = False

    async def _ensure_group(self):
        """Create consumer group if it doesn't exist."""
        if self._group_created:
            return
        try:
            await self.redis.xgroup_create(
                self.stream_name,
                self.consumer_group,
                id="0",
                mkstream=True,
            )
            logger.info(f"Created consumer group '{self.consumer_group}' on stream '{self.stream_name}'")
            self._group_created = True
        except Exception as e:
            if "BUSYGROUP" in str(e):
                # Group already exists, that's fine
                self._group_created = True
            else:
                # Don't set _group_created = True on failure, so we retry next time
                logger.warning(f"Failed to create consumer group: {e}")

    async def enqueue(
        self,
        agent_id: str,
        agent_name: str,
        message_id: str,
        payload: dict,
        dispatch_id: str,
        delay_seconds: int = 0,
    ) -> str:
        """Add dispatch to Redis Stream."""
        await self._ensure_group()

        task_id = str(uuid4())

        # Determine dispatch type from payload (defaults to "cloud" for backwards compat)
        dispatch_type = payload.get("dispatch_type", "cloud")

        # Get correlation_id from current request context (OBS-001)
        correlation_id = ""
        try:
            from app.middleware.correlation import get_correlation_id
            correlation_id = get_correlation_id() or ""
        except Exception:
            pass

        # Prepare message for stream
        message = {
            "task_id": task_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "message_id": message_id,
            "dispatch_id": dispatch_id,
            "dispatch_type": dispatch_type,
            "correlation_id": correlation_id,
            "payload": json.dumps(payload, separators=(",", ":")),
            "delay_seconds": str(delay_seconds),
            "attempt": "0",
        }

        settings = get_settings()
        stream_id = await self.redis.xadd(
            self.stream_name,
            message,
            maxlen=settings.dispatch_stream_maxlen,  # Bounded backlog (configurable)
        )

        log_msg = (
            f"DISPATCH_QUEUED task_id={task_id} dispatch_id={dispatch_id} "
            f"agent_name={agent_name} dispatch_type={dispatch_type} stream_id={stream_id}"
        )
        logger.info(log_msg)

        return task_id

    async def acknowledge(
        self,
        task_id: str,
        stream_entry_id: Optional[str] = None,
        dispatch_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> bool:
        """
        Acknowledge task completion (XACK).

        Args:
            task_id: Task ID for logging
            stream_entry_id: Redis Stream entry ID (required for actual XACK)
            dispatch_id: Dispatch ID for log correlation
            agent_name: Agent name for log correlation
        """
        if not stream_entry_id:
            logger.warning(f"DISPATCH_ACK task_id={task_id} - no stream_entry_id, cannot XACK")
            return False

        try:
            acked = await self.redis.xack(
                self.stream_name,
                self.consumer_group,
                stream_entry_id,
            )
            ack_msg = (
                f"DISPATCH_ACK task_id={task_id} dispatch_id={dispatch_id} "
                f"agent_name={agent_name} stream_id={stream_entry_id} acked={acked}"
            )
            logger.info(ack_msg)
            return acked > 0
        except Exception as e:
            logger.error(f"DISPATCH_ACK_FAILED task_id={task_id} dispatch_id={dispatch_id} error={e}")
            return False

    async def get_pending_count(self) -> int:
        """Get count of pending messages in stream."""
        try:
            info = await self.redis.xinfo_stream(self.stream_name)
            return info.get("length", 0)
        except Exception:
            return 0

    async def read_pending(
        self,
        consumer_name: str,
        count: int = 10,
        block_ms: int = 5000,
    ) -> list[DispatchTask]:
        """
        Read pending tasks for processing (XREADGROUP).

        Args:
            consumer_name: Unique consumer identifier
            count: Max tasks to read
            block_ms: How long to block waiting for new tasks

        Returns:
            List of DispatchTask objects to process
        """
        await self._ensure_group()

        try:
            results = await self.redis.xreadgroup(
                groupname=self.consumer_group,
                consumername=consumer_name,
                streams={self.stream_name: ">"},
                count=count,
                block=block_ms,
            )

            if not results:
                return []

            tasks = []
            for stream_name, messages in results:
                for msg_id, fields in messages:
                    try:
                        tasks.append(DispatchTask.from_stream_message(msg_id, fields))
                    except Exception as e:
                        logger.error(f"Failed to parse dispatch task: {e}")

            return tasks

        except Exception as e:
            if "NOGROUP" in str(e):
                # Consumer group was deleted or doesn't exist, reset flag to recreate on next call
                logger.warning(f"Consumer group missing, will recreate: {e}")
                self._group_created = False
            else:
                logger.error(f"Failed to read from dispatch stream: {e}")
            return []

    async def autoclaim_stale(
        self,
        consumer_name: str,
        min_idle_ms: int = 60000,
        count: int = 10,
    ) -> list[DispatchTask]:
        """
        Reclaim stale pending messages from dead consumers.

        Uses XAUTOCLAIM to transfer ownership of messages that have been
        idle for longer than min_idle_ms (default 60 seconds).

        This handles the case where a worker crashes without ACKing messages.

        Args:
            consumer_name: Consumer to claim messages for
            min_idle_ms: Minimum idle time before claiming (ms)
            count: Max messages to claim

        Returns:
            List of reclaimed DispatchTask objects
        """
        await self._ensure_group()

        try:
            # XAUTOCLAIM returns: [next_start_id, [(id, fields), ...], [deleted_ids]]
            result = await self.redis.xautoclaim(
                name=self.stream_name,
                groupname=self.consumer_group,
                consumername=consumer_name,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=count,
            )

            if not result or len(result) < 2:
                return []

            # result[1] contains the claimed messages
            messages = result[1]
            if not messages:
                return []

            tasks = []
            for msg_id, fields in messages:
                try:
                    task = DispatchTask.from_stream_message(msg_id, fields, increment_attempt=True)
                    tasks.append(task)
                    logger.info(
                        f"DISPATCH_RECLAIMED task_id={task.task_id} "
                        f"agent_name={task.agent_name} attempt={task.attempt}"
                    )
                except Exception as e:
                    logger.error(f"Failed to parse reclaimed task: {e}")

            if tasks:
                logger.info(f"Reclaimed {len(tasks)} stale tasks from dead consumers")

            return tasks

        except Exception as e:
            if "NOGROUP" in str(e):
                logger.warning(f"Consumer group missing during autoclaim, will recreate: {e}")
                self._group_created = False
            else:
                logger.error(f"Failed to autoclaim stale tasks: {e}")
            return []


class CloudTasksQueue(DispatchQueue):
    """
    Google Cloud Tasks implementation for production.

    Uses HTTP target tasks with managed retry policies.
    """

    def __init__(
        self,
        project_id: str,
        location: str,
        queue_name: str,
        target_url: str,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        super().__init__(retry_policy)
        self.project_id = project_id
        self.location = location
        self.queue_name = queue_name
        self.target_url = target_url
        self._client = None

    def _get_client(self):
        """Lazy-load Cloud Tasks client."""
        if self._client is None:
            try:
                from google.cloud import tasks_v2
                self._client = tasks_v2.CloudTasksAsyncClient()
            except ImportError:
                raise RuntimeError(
                    "google-cloud-tasks not installed. "
                    "Install with: pip install google-cloud-tasks"
                )
        return self._client

    @staticmethod
    def _get_correlation_id() -> str:
        """Get correlation ID from request context (OBS-001)."""
        try:
            from app.middleware.correlation import get_correlation_id
            return get_correlation_id() or ""
        except Exception:
            return ""

    @property
    def _queue_path(self) -> str:
        """Full queue resource path."""
        return f"projects/{self.project_id}/locations/{self.location}/queues/{self.queue_name}"

    async def enqueue(
        self,
        agent_id: str,
        agent_name: str,
        message_id: str,
        payload: dict,
        dispatch_id: str,
        delay_seconds: int = 0,
    ) -> str:
        """Add dispatch to Cloud Tasks queue."""
        from google.cloud import tasks_v2
        from google.protobuf import duration_pb2, timestamp_pb2
        import datetime

        from app.core.config import get_settings
        settings = get_settings()

        client = self._get_client()
        task_id = str(uuid4())

        # Determine dispatch type from payload (defaults to "cloud" for backwards compat)
        dispatch_type = payload.get("dispatch_type", "cloud")

        # Ensure payload has required fields for internal endpoint
        # For cloud dispatch: needs cloud_function_url
        # For webhook dispatch: needs webhook_url, webhook_secret
        if dispatch_type == "cloud" and "cloud_function_url" not in payload:
            logger.warning(
                f"DISPATCH_MISSING_URL task_id={task_id} agent_name={agent_name} "
                "- payload must include cloud_function_url for cloud dispatch"
            )
        elif dispatch_type == "webhook" and "webhook_url" not in payload:
            logger.warning(
                f"DISPATCH_MISSING_URL task_id={task_id} agent_name={agent_name} "
                "- payload must include webhook_url for webhook dispatch"
            )

        # Build the task with authentication header
        # CRITICAL: dispatch_deadline must match our HTTP timeout to prevent
        # Cloud Tasks from retrying while the agent is still processing.
        # Default 30s causes duplicate dispatches for agents taking 40-60s.
        #
        # Webhook agents have a longer timeout (600s) than cloud agents (300s)
        # to support long-running external processes.
        if dispatch_type == "webhook":
            dispatch_deadline_seconds = int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "600"))
        else:
            dispatch_deadline_seconds = settings.cloud_agent_http_timeout_seconds

        task = tasks_v2.Task(
            # How long Cloud Tasks waits for 2xx before retry (must match HTTP timeout)
            dispatch_deadline=duration_pb2.Duration(seconds=dispatch_deadline_seconds),
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=self.target_url,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": settings.internal_dispatch_api_key,
                    "X-Dispatch-ID": dispatch_id,
                    "X-Task-ID": task_id,
                    "X-Dispatch-Type": dispatch_type,
                    "X-Correlation-Id": self._get_correlation_id(),
                },
                body=json.dumps(payload).encode(),
            ),
        )

        # Add delay if specified
        if delay_seconds > 0:
            schedule_time = timestamp_pb2.Timestamp()
            schedule_time.FromDatetime(
                datetime.datetime.utcnow() + datetime.timedelta(seconds=delay_seconds)
            )
            task.schedule_time = schedule_time

        # Create the task
        import time
        enqueue_start = time.time()
        response = await client.create_task(
            parent=self._queue_path,
            task=task,
        )
        enqueue_duration_ms = int((time.time() - enqueue_start) * 1000)

        # DIAGNOSTIC: Log exact timestamp when task is created
        # This helps identify if delay is before or after queue
        logger.info(
            f"DISPATCH_QUEUED task_id={task_id} dispatch_id={dispatch_id} "
            f"agent_name={agent_name} dispatch_type={dispatch_type} cloud_task={response.name} "
            f"dispatch_deadline={dispatch_deadline_seconds}s "
            f"enqueue_duration_ms={enqueue_duration_ms} "
            f"queued_at={time.time()}"
        )

        return task_id

    async def acknowledge(
        self,
        task_id: str,
        stream_entry_id: Optional[str] = None,
        dispatch_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> bool:
        """
        Cloud Tasks auto-acknowledges on 2xx response.
        This method is a no-op for Cloud Tasks (stream_entry_id is ignored).
        """
        logger.debug(
            f"DISPATCH_ACK task_id={task_id} dispatch_id={dispatch_id} "
            f"agent_name={agent_name} (auto-acked by Cloud Tasks)"
        )
        return True

    async def get_pending_count(self) -> int:
        """Get approximate count of pending tasks."""
        # Cloud Tasks doesn't provide easy pending count
        # Would need to use Cloud Monitoring metrics
        return -1  # Unknown


# =============================================================================
# Factory Function
# =============================================================================

_queue_instance: Optional[DispatchQueue] = None


def get_dispatch_queue() -> DispatchQueue:
    """
    Get the dispatch queue instance based on environment.

    Returns CloudTasksQueue in production, RedisStreamQueue locally.

    Cloud Tasks targets our /internal/dispatch-agent endpoint, which then
    makes the HTTP call to the actual agent runner. This allows us to:
    - Handle authentication centrally
    - Add observability and metrics
    - Control retry behavior
    """
    global _queue_instance

    if _queue_instance is not None:
        return _queue_instance

    from app.core.config import get_settings
    settings = get_settings()

    # Determine which queue backend to use:
    # - Production + USE_CLOUD_TASKS=true → CloudTasksQueue (GCP managed)
    # - Local/Development → RedisStreamQueue (dispatch_worker consumes)
    use_gcp_queue = (
        settings.use_cloud_tasks
        and settings.environment == "production"
    )

    if use_gcp_queue:
        # Production: Use Cloud Tasks
        # Target URL is our internal dispatch endpoint, NOT the agent runner directly
        # Cloud Tasks → /internal/dispatch-agent → agent_runner
        backend_url = os.getenv("BACKEND_API_URL", "https://paxai.app")
        target_url = f"{backend_url.rstrip('/')}/internal/dispatch-agent"

        _queue_instance = CloudTasksQueue(
            project_id=settings.gcp_project_id,
            location=settings.gcp_region,
            queue_name=settings.dispatch_queue_name,
            target_url=target_url,
        )
        logger.info(
            f"Initialized CloudTasksQueue: {settings.dispatch_queue_name} "
            f"in {settings.gcp_region} → {target_url}"
        )
    else:
        # Local/Development: Use Redis Streams
        # dispatch_worker consumes from this queue
        import redis.asyncio as redis

        redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )

        _queue_instance = RedisStreamQueue(redis_client=redis_client)
        logger.info(
            f"Initialized RedisStreamQueue for local development "
            f"(USE_CLOUD_TASKS={settings.use_cloud_tasks}, ENV={settings.environment})"
        )

    return _queue_instance


async def reset_dispatch_queue():
    """Reset the queue instance (for testing)."""
    global _queue_instance
    _queue_instance = None
