"""
Internal API Endpoints

These endpoints are NOT for external use. They are called by:
- Cloud Tasks (dispatch queue)
- Other internal services

All endpoints require internal API key authentication.

IMPORTANT: The dispatch-agent endpoint uses the UNIFIED dispatch_executor,
which is the SAME code used by the local dispatch_worker. This ensures
LOCAL and GCP use identical dispatch logic.
"""

import asyncio
import logging
import os
import json
import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel, Field

from sqlalchemy import case

from app.constants import CONTEXT_DEFAULT_TTL
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.dispatch_executor import DispatchStatus, execute_dispatch
from app.core.redis_client import redis_client
from app.models.agent import Agent
from app.models.message import Message
from app.models.space import Space
from app.models.task import Task
from app.services.messages_notifications import MessagesNotificationHelper
from app.services.redis_sse_broker import redis_sse_broker
from app.services.space_agent_service import ensure_space_agent_for_org

logger = logging.getLogger(__name__)

# Business priority rank (lower number = more urgent). Used to order task
# queries by real priority instead of lexicographic string sort, which
# would otherwise scramble values like "urgent"/"medium"/"low"/"high"/"critical".
TASK_PRIORITY_RANK = case(
    (Task.priority == "critical", 1),
    (Task.priority == "urgent", 2),
    (Task.priority == "high", 3),
    (Task.priority == "medium", 4),
    (Task.priority == "low", 5),
    else_=6,
)

# Strong reference set to prevent background tasks from being garbage collected
_background_tasks: set[asyncio.Task] = set()
router = APIRouter(prefix="/internal", tags=["internal"])


class DispatchPayload(BaseModel):
    """Payload for agent dispatch task."""

    # Required fields
    agent_name: str
    agent_id: str
    message_id: str
    cloud_function_url: str
    payload_version: str = "2"

    # The full payload to forward to agent runner
    # All other fields are forwarded as-is
    class Config:
        extra = "allow"  # Allow additional fields to pass through


class DispatchResult(BaseModel):
    """Result of dispatch attempt."""

    status: str  # "success", "failed", "retryable"
    dispatch_id: str
    duration_ms: Optional[int] = None
    http_status: Optional[int] = None
    error: Optional[str] = None


@router.post("/dispatch-agent", response_model=DispatchResult)
async def dispatch_agent_task(
    request: Request,
    x_api_key: str = Header(..., alias="X-API-Key"),
    x_dispatch_id: str = Header(default="unknown", alias="X-Dispatch-ID"),
    x_task_id: str = Header(default=None, alias="X-Task-ID"),
    x_cloudtasks_taskretrycount: str = Header(default="0", alias="X-CloudTasks-TaskRetryCount"),
):
    """
    Internal endpoint called by Cloud Tasks to dispatch to agent runner.

    NOT for external use - authenticated by internal API key.

    IMPORTANT: Uses the UNIFIED dispatch_executor - same code as local dispatch_worker.
    This ensures LOCAL and GCP use identical dispatch logic.

    Cloud Tasks will:
    - Retry on 5xx responses (with exponential backoff)
    - NOT retry on 4xx responses (client error)
    - Auto-acknowledge on 2xx response

    Returns:
        DispatchResult with status indicating success or failure type
    """
    import time
    handler_start = time.time()

    # DIAGNOSTIC: Log exactly when Cloud Tasks handler receives request
    logger.info(
        f"DISPATCH_HANDLER_START dispatch_id={x_dispatch_id} "
        f"task_id={x_task_id} retry={x_cloudtasks_taskretrycount} "
        f"received_at={handler_start}"
    )

    settings = get_settings()

    # Validate internal API key
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        logger.warning(
            f"DISPATCH_UNAUTHORIZED dispatch_id={x_dispatch_id} "
            f"task_id={x_task_id} reason=invalid_api_key"
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Parse the dispatch payload
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"DISPATCH_PARSE_ERROR dispatch_id={x_dispatch_id} error={e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    agent_name = body.get("agent_name", "unknown")
    retry_count = int(x_cloudtasks_taskretrycount)

    # Use the UNIFIED dispatch executor (SAME code as local dispatch_worker)
    result = await execute_dispatch(
        payload=body,
        dispatch_id=x_dispatch_id,
        retry_count=retry_count,
        task_id=x_task_id,
    )

    # Translate executor result to Cloud Tasks HTTP response
    # Cloud Tasks uses HTTP status codes to determine retry behavior:
    # - 2xx = success (auto-acknowledge)
    # - 4xx = permanent failure (don't retry)
    # - 5xx = retryable failure (retry with backoff)

    # DIAGNOSTIC: Calculate total handler time including executor
    handler_duration_ms = int((time.time() - handler_start) * 1000)

    if result.is_success:
        # DIAGNOSTIC: Log exactly when returning HTTP 200 to Cloud Tasks
        logger.info(
            f"DISPATCH_HANDLER_DONE dispatch_id={x_dispatch_id} "
            f"status=success handler_duration_ms={handler_duration_ms} "
            f"executor_duration_ms={result.duration_ms} "
            f"returning_http=200 returning_at={time.time()}"
        )
        return DispatchResult(
            status="success",
            dispatch_id=result.dispatch_id,
            duration_ms=result.duration_ms,
            http_status=result.http_status,
        )
    elif result.status == DispatchStatus.FAILED_MISSING_URL:
        # Missing URL is a permanent error - 400 tells Cloud Tasks not to retry
        logger.info(
            f"DISPATCH_HANDLER_DONE dispatch_id={x_dispatch_id} "
            f"status=failed_missing_url handler_duration_ms={handler_duration_ms} "
            f"returning_http=400 returning_at={time.time()}"
        )
        raise HTTPException(status_code=400, detail="Missing cloud_function_url")
    elif result.status == DispatchStatus.FAILED_PERMANENT:
        # 4xx from agent runner - return success to Cloud Tasks (don't retry)
        logger.info(
            f"DISPATCH_HANDLER_DONE dispatch_id={x_dispatch_id} "
            f"status=failed_permanent handler_duration_ms={handler_duration_ms} "
            f"executor_duration_ms={result.duration_ms} "
            f"returning_http=200 returning_at={time.time()}"
        )
        return DispatchResult(
            status="failed",
            dispatch_id=result.dispatch_id,
            duration_ms=result.duration_ms,
            http_status=result.http_status,
            error=result.error,
        )
    else:
        # Retryable error (5xx, timeout, connection error)
        # Return 503 to trigger Cloud Tasks retry
        logger.info(
            f"DISPATCH_HANDLER_DONE dispatch_id={x_dispatch_id} "
            f"status=failed_retryable handler_duration_ms={handler_duration_ms} "
            f"executor_duration_ms={result.duration_ms} "
            f"returning_http=503 returning_at={time.time()} "
            f"error={result.error}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"Dispatch failed (retryable): {result.error}",
        )


class AgentMeshNudgePayload(BaseModel):
    interval_minutes: int | None = Field(default=None, ge=5, le=120)
    agent_names: list[str] | None = None
    channel: str = Field(default="main", min_length=1, max_length=50)
    dry_run: bool = False


class AgentMeshNudgeResult(BaseModel):
    space_id: str
    space_slug: str | None = None
    target_agent: str
    context_key: str
    open_task_count: int
    task_ids: list[str]
    message_id: str | None = None
    dry_run: bool = False


class AgentMeshSweepFailure(BaseModel):
    target_agent: str
    space_id: str | None = None
    space_slug: str | None = None
    error: str


class AgentMeshSweepResult(BaseModel):
    status: str
    interval_minutes: int
    target_agents: list[str]
    nudges_created: int
    results: list[AgentMeshNudgeResult]
    failures: list[AgentMeshSweepFailure] = Field(default_factory=list)


def _build_agent_mesh_nudge_prompt(*, target_agent: Agent, open_tasks: list[Task], interval_minutes: int) -> tuple[str, dict]:
    active_tasks = []
    blocked_tasks = []
    completed_recently = []
    for task in open_tasks:
        item = {
            "id": str(task.id),
            "title": task.title,
            "status": task.work_status or task.status or "unknown",
            "priority": task.priority or "medium",
        }
        if item["status"] == "blocked":
            blocked_tasks.append(item)
        elif item["status"] == "completed":
            completed_recently.append(item)
        else:
            active_tasks.append(item)

    task_lines = [
        f"- {item['title']} [{item['status']}, priority={item['priority']}, id={item['id'][-6:]}]"
        for item in active_tasks[:8]
    ]
    if blocked_tasks:
        task_lines.append("Blocked:")
        task_lines.extend(
            f"- {item['title']} [{item['status']}, priority={item['priority']}, id={item['id'][-6:]}]"
            for item in blocked_tasks[:5]
        )
    if not task_lines:
        task_lines.append("- No open assigned tasks found. Pick up the next available task or report idle status.")

    prompt = (
        f"@{target_agent.name} mesh check-in: stay hot and keep work flowing. "
        f"This is your {interval_minutes}-minute autonomous nudge.\n\n"
        f"Open assigned tasks ({len(open_tasks)}):\n" + "\n".join(task_lines) + "\n\n"
        "Reply with: 1) current task, 2) concrete progress since last update, "
        "3) any blocker, and 4) who you need from the mesh if blocked. "
        "Use aX tasks, messages, and context for coordination — do not wait for a human ping."
    )
    payload = {
        "target_agent": target_agent.name,
        "open_task_count": len(open_tasks),
        "task_ids": [str(task.id) for task in open_tasks],
        "tasks": active_tasks[:8] + blocked_tasks[:5],
        "blocked_task_ids": [item["id"] for item in blocked_tasks],
        "completed_recently": completed_recently[:5],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic": "agent_mesh.checkin",
    }
    return prompt, payload


async def _run_agent_mesh_sweep(payload: AgentMeshNudgePayload) -> AgentMeshSweepResult:
    settings = get_settings()
    interval_minutes = payload.interval_minutes or settings.autonomous_agent_mesh_interval_minutes
    target_agents = payload.agent_names or settings.autonomous_agent_mesh_names
    target_agents = [name.strip().lstrip('@') for name in target_agents if str(name).strip()]
    if not target_agents:
        return AgentMeshSweepResult(
            status="no_targets",
            interval_minutes=interval_minutes,
            target_agents=[],
            nudges_created=0,
            results=[],
            failures=[],
        )

    results: list[AgentMeshNudgeResult] = []
    failures: list[AgentMeshSweepFailure] = []
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select, text

        from app.models.agent_space_access import AgentSpaceAccess
        agent_rows = await db.execute(
            select(Agent, Space)
            .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
            .join(Space, Space.id == AgentSpaceAccess.space_id)
            .where(
                AgentSpaceAccess.state == "active",
                Agent.status == "active",
                Space.is_archived.is_(False),
                Space.is_internal.is_(False),
                Agent.origin.in_(["cloud", "external_gateway", "agentcore", "mcp", "space_agent"]),
                Agent.name.in_(target_agents),
            )
            .order_by(Space.slug.asc(), Agent.name.asc())
        )

        for agent, space in agent_rows.all():
            context_key = f"context:{space.id}:agent-mesh/checkins/{agent.name}"
            try:
                async with db.begin_nested():
                    await db.execute(
                        text("SELECT set_config('app.current_space_id', :sid, true)"),
                        {"sid": str(space.id)},
                    )
                    space_agent = await ensure_space_agent_for_org(db, space)
                    if not space_agent or space_agent.status != "active":
                        raise RuntimeError("space agent unavailable for mesh check-in")

                    tasks_result = await db.execute(
                        select(Task)
                        .where(
                            Task.space_id == space.id,
                            Task.assigned_agent_id == agent.id,
                            Task.work_status.notin_(["completed", "cancelled"]),
                        )
                        .order_by(
                            TASK_PRIORITY_RANK.asc(),
                            Task.updated_at.desc(),
                            Task.created_at.desc(),
                        )
                    )
                    open_tasks = list(tasks_result.scalars().all())
                    prompt, context_payload = _build_agent_mesh_nudge_prompt(
                        target_agent=agent,
                        open_tasks=open_tasks,
                        interval_minutes=interval_minutes,
                    )
                    ttl = max(CONTEXT_DEFAULT_TTL, settings.autonomous_agent_mesh_context_ttl_seconds)

                    message_id = None
                    if not payload.dry_run:
                        msg = Message(
                            id=uuid4(),
                            space_id=space.id,
                            agent_id=space_agent.id,
                            user_id=None,
                            content=prompt,
                            channel=payload.channel,
                            message_type="message",
                            message_metadata={
                                "agent_mesh": {
                                    "kind": "checkin",
                                    "interval_minutes": interval_minutes,
                                    "target_agent": agent.name,
                                    "context_key": f"agent-mesh/checkins/{agent.name}",
                                    "open_task_count": len(open_tasks),
                                    "task_ids": [str(task.id) for task in open_tasks],
                                },
                                "mentions": [agent.name],
                                "top_level_ingress": False,
                                "routing": {"mode": "direct_mention", "source": "agent_mesh_scheduler"},
                            },
                            created_at=datetime.now(timezone.utc),
                        )
                        db.add(msg)
                        await db.flush()
                        await redis_client.setex(
                            context_key,
                            ttl,
                            json.dumps(context_payload, separators=(",", ":")),
                        )
                        notifier = MessagesNotificationHelper(
                            db=db,
                            redis_client=redis_client,
                            sse_broker=redis_sse_broker,
                        )
                        await notifier.broadcast_sse(
                            space_id=space.id,
                            msg=msg,
                            author_name=space_agent.name,
                            author_type="agent",
                        )
                        message_id = str(msg.id)

                    results.append(
                        AgentMeshNudgeResult(
                            space_id=str(space.id),
                            space_slug=space.slug,
                            target_agent=agent.name,
                            context_key=f"agent-mesh/checkins/{agent.name}",
                            open_task_count=len(open_tasks),
                            task_ids=[str(task.id) for task in open_tasks],
                            message_id=message_id,
                            dry_run=payload.dry_run,
                        )
                    )
            except Exception as exc:
                logger.exception(
                    "AGENT_MESH_SWEEP_AGENT_FAILED space_id=%s space_slug=%s target_agent=%s",
                    space.id,
                    space.slug,
                    agent.name,
                )
                if not payload.dry_run:
                    try:
                        await redis_client.delete(context_key)
                    except Exception:
                        logger.warning(
                            "AGENT_MESH_SWEEP_REDIS_CLEANUP_FAILED context_key=%s",
                            context_key,
                            exc_info=True,
                        )
                failures.append(
                    AgentMeshSweepFailure(
                        target_agent=agent.name,
                        space_id=str(space.id),
                        space_slug=space.slug,
                        error=str(exc),
                    )
                )

        if payload.dry_run:
            await db.rollback()
        else:
            await db.commit()

    status = "partial" if failures else "ok"
    return AgentMeshSweepResult(
        status=status,
        interval_minutes=interval_minutes,
        target_agents=target_agents,
        nudges_created=len([r for r in results if r.message_id]),
        results=results,
        failures=failures,
    )


class DispatchTriggerPayload(BaseModel):
    """Minimal payload from MCP to trigger cloud agent dispatch."""
    message_id: str
    space_id: str
    mentions: list[str] = []


class DispatchTriggerResult(BaseModel):
    """Result of dispatch trigger."""
    status: str  # "queued", "no_cloud_agents", "error"
    message_id: str
    agents_queued: int = 0
    error: Optional[str] = None


@router.post("/dispatch-trigger", response_model=DispatchTriggerResult)
async def dispatch_trigger(
    payload: DispatchTriggerPayload,
    background_tasks: BackgroundTasks,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Trigger cloud agent dispatch for a message.

    Called by MCP server after message creation to unify dispatch through
    the Backend API's queue system.

    This is a fire-and-forget endpoint - it queues the dispatch and returns
    immediately. The actual dispatch happens asynchronously via BackgroundTasks.
    """
    from uuid import UUID

    import redis.asyncio as aioredis
    from sqlalchemy import select, text, and_, or_, func

    from app.core.database import AsyncSessionLocal
    from app.models.agent import Agent
    from app.models.message import Message
    from app.models.user import User
    from app.services.messages_notifications import MessagesNotificationHelper
    from app.services.redis_sse_broker import redis_sse_broker

    settings = get_settings()

    # Validate internal API key
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        logger.warning(
            f"DISPATCH_TRIGGER_UNAUTHORIZED message_id={payload.message_id} "
            f"reason=invalid_api_key"
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(
        f"DISPATCH_TRIGGER_RECEIVED message_id={payload.message_id} "
        f"space_id={payload.space_id} mentions={payload.mentions}"
    )

    if not payload.mentions:
        logger.info(
            f"DISPATCH_TRIGGER_SKIP message_id={payload.message_id} "
            f"reason=no_mentions"
        )
        return DispatchTriggerResult(
            status="no_mentions",
            message_id=payload.message_id,
            agents_queued=0,
        )

    try:
        # Fetch the message from DB
        async with AsyncSessionLocal() as db:
            # Set RLS context - validate UUID format to prevent SQL injection
            space_uuid = UUID(payload.space_id)  # Raises if invalid
            await db.execute(
                text("SELECT set_config('app.current_space_id', :sid, true)"),
                {"sid": str(space_uuid)},
            )

            result = await db.execute(
                select(Message).where(Message.id == UUID(payload.message_id))
            )
            msg = result.scalar_one_or_none()

            if not msg:
                logger.warning(
                    f"DISPATCH_TRIGGER_NOT_FOUND message_id={payload.message_id}"
                )
                return DispatchTriggerResult(
                    status="not_found",
                    message_id=payload.message_id,
                    error="Message not found",
                )

            # Get author name for dispatch
            author_name = "unknown"
            if msg.agent_id:
                agent_result = await db.execute(
                    select(Agent.name).where(Agent.id == msg.agent_id)
                )
                author_name = agent_result.scalar() or "unknown"
            elif msg.user_id:
                user_result = await db.execute(
                    select(User.username).where(User.id == msg.user_id)
                )
                author_name = user_result.scalar() or "unknown"

            # Estimate matched dispatch targets for truthful queue telemetry.
            normalized_mentions = [m.lower().lstrip("@").strip() for m in payload.mentions if m]
            alias_map: dict[str, str] = {}
            alias_raw = settings.model_dump().get("mention_alias_map_json") if hasattr(settings, "model_dump") else None
            if not alias_raw:
                alias_raw = os.environ.get("MENTION_ALIAS_MAP_JSON", "")
            if alias_raw:
                try:
                    loaded_alias = json.loads(alias_raw)
                    if isinstance(loaded_alias, dict):
                        alias_map = {
                            str(k).lower().lstrip("@").strip(): str(v).lower().lstrip("@").strip()
                            for k, v in loaded_alias.items()
                            if k and v
                        }
                except Exception:
                    alias_map = {}

            resolved_mentions = [alias_map.get(m, m) for m in normalized_mentions]
            resolved_mentions = list(dict.fromkeys(resolved_mentions))

            from app.models.agent_space_access import AgentSpaceAccess
            dispatchable_scope = and_(
                or_(
                    Agent.cloud_function_url.isnot(None),
                    and_(Agent.origin == "external_gateway", Agent.webhook_url.isnot(None)),
                ),
                Agent.status == "active",
                or_(
                    AgentSpaceAccess.space_id == space_uuid,
                    Agent.visibility_level == "global",
                ),
                AgentSpaceAccess.state == "active",  # Skip suspended/detached space access records
            )

            matched_count_result = await db.execute(
                select(func.count(Agent.id))
                .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
                .where(
                    and_(func.lower(Agent.name).in_(resolved_mentions), dispatchable_scope)
                )
            )
            matched_agents_estimate = int(matched_count_result.scalar() or 0)

            # Keep estimate aligned with runtime fallback behavior in notification service.
            if matched_agents_estimate == 0 and resolved_mentions:
                prefix_conditions = [func.lower(Agent.name).like(f"{m}%") for m in resolved_mentions if len(m) >= 3]
                if prefix_conditions:
                    prefix_result = await db.execute(
                        select(Agent.id)
                        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
                        .where(and_(or_(*prefix_conditions), dispatchable_scope)).limit(5)
                    )
                    prefix_candidates = [row[0] for row in prefix_result.all()]
                    if len(prefix_candidates) == 1:
                        matched_agents_estimate = 1

            logger.info(
                "DISPATCH_TRIGGER_MATCH_ESTIMATE message_id=%s mentions=%s resolved_mentions=%s matched_agents_estimate=%s",
                payload.message_id,
                payload.mentions,
                resolved_mentions,
                matched_agents_estimate,
            )

            # Queue background task for dispatch
            # Note: We pass IDs, not objects, because the db session closes after response
            async def run_dispatch():
                """Background task that creates its own db session."""
                async with AsyncSessionLocal() as bg_db:
                    # Set RLS context
                    await bg_db.execute(
                        text("SELECT set_config('app.current_space_id', :sid, true)"),
                        {"sid": str(space_uuid)},
                    )

                    # Re-fetch message in this session
                    bg_result = await bg_db.execute(
                        select(Message).where(Message.id == UUID(payload.message_id))
                    )
                    bg_msg = bg_result.scalar_one_or_none()
                    if not bg_msg:
                        logger.warning(f"DISPATCH_TRIGGER_BG_NOT_FOUND message_id={payload.message_id}")
                        return

                    # Create fresh Redis client for this task
                    bg_redis = aioredis.from_url(settings.redis_url, decode_responses=True)

                    notification_service = MessagesNotificationHelper(
                        db=bg_db,
                        redis_client=bg_redis,
                        sse_broker=redis_sse_broker,
                    )

                    await notification_service._dispatch_cloud_agents(
                        msg=bg_msg,
                        author_name=author_name,
                        mentions=resolved_mentions,
                    )

            background_tasks.add_task(run_dispatch)

            logger.info(
                f"DISPATCH_TRIGGER_QUEUED message_id={payload.message_id} "
                f"mentions={payload.mentions}"
            )

            return DispatchTriggerResult(
                status="queued",
                message_id=payload.message_id,
                agents_queued=matched_agents_estimate,
            )

    except Exception as e:
        logger.error(
            f"DISPATCH_TRIGGER_ERROR message_id={payload.message_id} error={e}"
        )
        return DispatchTriggerResult(
            status="error",
            message_id=payload.message_id,
            error=str(e),
        )


class AgentReplyPayload(BaseModel):
    """Payload for agent response callback."""
    agent_id: str
    agent_name: str
    message_id: str  # Original message being replied to
    space_id: str
    content: str
    dispatch_id: Optional[str] = None


class AgentReplyResult(BaseModel):
    """Result of agent reply."""
    status: str  # "success", "error"
    reply_message_id: Optional[str] = None
    error: Optional[str] = None
    suppressed: bool = False


def _enforce_agent_reply_identity(agent, payload: AgentReplyPayload, space_uuid) -> str:
    """Enforce sender identity binding for internal agent reply callbacks."""
    canonical_agent_name = (agent.name or "").strip()
    payload_agent_name = (payload.agent_name or "").strip()

    if canonical_agent_name and payload_agent_name and canonical_agent_name.lower() != payload_agent_name.lower():
        logger.error(
            "AGENT_REPLY_IDENTITY_MISMATCH agent_id=%s payload_agent_name=%s canonical_agent_name=%s dispatch_id=%s",
            payload.agent_id,
            payload_agent_name,
            canonical_agent_name,
            payload.dispatch_id,
        )
        raise HTTPException(status_code=403, detail="Agent identity mismatch")

    if str(agent.space_id) != str(space_uuid):
        logger.error(
            "AGENT_REPLY_ORG_MISMATCH agent_id=%s payload_org=%s agent_org=%s dispatch_id=%s",
            payload.agent_id,
            payload.space_id,
            agent.space_id,
            payload.dispatch_id,
        )
        raise HTTPException(status_code=403, detail="Agent organization mismatch")

    return canonical_agent_name or payload_agent_name or "unknown_agent"


async def _is_reply_delivery_blocked(*, notifier, agent_id, space_id, agent_name, dispatch_id, source) -> bool:
    blocked = await notifier._is_agent_disabled_cache(str(agent_id), str(space_id))
    if blocked:
        logger.info(
            "%s_SKIP_DISABLED agent_id=%s agent_name=%s space_id=%s dispatch_id=%s",
            source,
            agent_id,
            agent_name,
            space_id,
            dispatch_id,
        )
    return blocked


@router.post("/agent-reply", response_model=AgentReplyResult)
async def agent_reply(
    payload: AgentReplyPayload,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Receive agent response and save as a message.

    Called by agent_runner after the agent completes processing.
    This is the OUTPUT step of the agent lifecycle:
    INPUT (dispatch) → PROCESS (tools/thinking) → OUTPUT (this endpoint)

    The response is saved as a new message attributed to the agent,
    then broadcast via SSE to the frontend.
    """
    import time
    from datetime import datetime, timezone
    from uuid import UUID

    import redis.asyncio as aioredis
    from sqlalchemy import select, text

    from app.core.database import AsyncSessionLocal
    from app.models.agent import Agent
    from app.models.message import Message
    from app.services.messages_notifications import MessagesNotificationHelper, sanitize_self_mentions
    from app.services.redis_sse_broker import redis_sse_broker

    # DIAGNOSTIC: Track timing of agent-reply handler
    reply_handler_start = time.time()

    settings = get_settings()

    # Validate internal API key
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        logger.warning(
            f"AGENT_REPLY_UNAUTHORIZED agent_id={payload.agent_id} "
            f"dispatch_id={payload.dispatch_id} reason=invalid_api_key"
        )
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(
        f"AGENT_REPLY_RECEIVED agent_name={payload.agent_name} "
        f"message_id={payload.message_id} dispatch_id={payload.dispatch_id} "
        f"space_id={payload.space_id} content_len={len(payload.content)}"
    )

    if not payload.content or not payload.content.strip():
        logger.warning(
            f"AGENT_REPLY_EMPTY agent_name={payload.agent_name} "
            f"dispatch_id={payload.dispatch_id}"
        )
        return AgentReplyResult(
            status="error",
            error="Empty response content",
        )

    try:
        # decode_responses=True ensures Redis returns strings (not bytes)
        # Required for kill-switch checks that compare val == "1"
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)
        async with AsyncSessionLocal() as db:
            # Set RLS context
            space_uuid = UUID(payload.space_id)
            await db.execute(
                text("SELECT set_config('app.current_space_id', :sid, true)"),
                {"sid": str(space_uuid)},
            )

            # Verify agent exists
            agent_uuid = UUID(payload.agent_id)
            agent_result = await db.execute(
                select(Agent).where(Agent.id == agent_uuid)
            )
            agent = agent_result.scalar_one_or_none()

            if not agent:
                logger.error(
                    f"AGENT_REPLY_AGENT_NOT_FOUND agent_id={payload.agent_id}"
                )
                return AgentReplyResult(
                    status="error",
                    error="Agent not found",
                )

            # Identity guardrail: sender attribution must come from canonical agent row.
            # Mismatches are hard failures (403) with audit log entries.
            canonical_agent_name = _enforce_agent_reply_identity(
                agent=agent,
                payload=payload,
                space_uuid=space_uuid,
            )

            notifier = MessagesNotificationHelper(
                db=db,
                redis_client=redis_conn,
                sse_broker=redis_sse_broker,
            )
            if await _is_reply_delivery_blocked(
                notifier=notifier,
                agent_id=agent.id,
                space_id=space_uuid,
                agent_name=canonical_agent_name,
                dispatch_id=payload.dispatch_id,
                source="AGENT_REPLY",
            ):
                return AgentReplyResult(status="success", error="Agent is paused or disabled", suppressed=True)

            # Get original message for threading. This SELECT runs on the same
            # AsyncSession/transaction immediately after set_config(..., true), so
            # PostgreSQL keeps app.current_space_id in scope for the messages RLS
            # policy. A parent message from another space is therefore filtered out
            # here and treated as not found instead of creating a cross-space link.
            original_msg_uuid = UUID(payload.message_id)
            original_result = await db.execute(
                select(Message).where(Message.id == original_msg_uuid)
            )
            original_msg = original_result.scalar_one_or_none()

            # Validate original message exists before creating reply
            # This prevents orphaned parent_id references that cause
            # "Original message not found" errors in the UI
            if not original_msg:
                logger.warning(
                    f"AGENT_REPLY_PARENT_NOT_FOUND message_id={payload.message_id} "
                    f"agent_name={canonical_agent_name} dispatch_id={payload.dispatch_id} "
                    f"space_id={payload.space_id} - original message may have been deleted "
                    f"during long dispatch latency"
                )
                return AgentReplyResult(
                    status="error",
                    error="Original message not found - message may have been deleted",
                )

            # Sanitize self-mentions to prevent loops
            sanitized_content, was_stripped = sanitize_self_mentions(
                payload.content, canonical_agent_name
            )

            if was_stripped:
                logger.info(
                    f"AGENT_REPLY_SANITIZED agent_name={canonical_agent_name} "
                    f"stripped_self_mention=True"
                )

            # Create the reply message
            # IMPORTANT: agent_id is set, user_id must be None for agent messages
            # Setting user_id would make it appear as if the user sent it!
            # THREADING: parent_id should be the original message ID so the reply
            # threads as a response to the triggering message
            reply_message = Message(
                space_id=space_uuid,
                agent_id=agent_uuid,
                user_id=None,  # Must be None - this is an AGENT message, not user
                content=sanitized_content.strip(),
                parent_id=original_msg_uuid,  # Reply is child of triggering message
                created_at=datetime.now(timezone.utc),
            )

            db.add(reply_message)

            # DIAGNOSTIC: Track DB commit timing
            db_commit_start = time.time()
            await db.commit()
            await db.refresh(reply_message)
            db_commit_duration_ms = int((time.time() - db_commit_start) * 1000)

            content_preview = sanitized_content[:100].replace("\n", " ")
            logger.info(
                f"AGENT_REPLY_SAVED agent_name={canonical_agent_name} "
                f"reply_id={reply_message.id} parent_id={reply_message.parent_id} "
                f"dispatch_id={payload.dispatch_id} space_id={payload.space_id} "
                f"db_commit_ms={db_commit_duration_ms} saved_at={time.time()} "
                f"content_preview=\"{content_preview}\""
            )

            # Intelligence handled by aX dispatch — see dispatch_executor.py
            # Legacy IntelligenceService trigger removed (aX consolidation).

            # Broadcast via SSE using the standard message pipeline
            try:
                # DIAGNOSTIC: Track SSE broadcast timing
                sse_broadcast_start = time.time()
                notifier = MessagesNotificationHelper(
                    db=db,
                    redis_client=redis_conn,
                    sse_broker=redis_sse_broker,
                )
                await notifier.broadcast_sse(
                    space_id=space_uuid,
                    msg=reply_message,
                    author_name=canonical_agent_name,
                    author_type="agent",  # SECURITY: Agent replies must always be typed as "agent"
                )
                sse_broadcast_duration_ms = int((time.time() - sse_broadcast_start) * 1000)
                logger.info(
                    f"AGENT_REPLY_BROADCAST agent_name={canonical_agent_name} "
                    f"reply_id={reply_message.id} "
                    f"sse_broadcast_ms={sse_broadcast_duration_ms} broadcast_at={time.time()}"
                )
            except Exception as sse_err:
                logger.warning(
                    f"AGENT_REPLY_SSE_ERROR agent_name={canonical_agent_name} "
                    f"error={sse_err}"
                )
                # Don't fail the request - message is saved

            # Audit-based widget attachment — look up ALL tool-call records
            # linked by correlation_id (dispatch_id) and attach to reply.
            # Supports multiple widgets per turn (e.g. context.set + tasks.create).
            if payload.dispatch_id and payload.space_id:
                try:
                    from app.core.dispatch_executor import _get_all_widgets_from_audit, _set_widget
                    all_widgets = await _get_all_widgets_from_audit(payload.dispatch_id)
                    if all_widgets:
                        tools_used = [w["tool_name"] for w in all_widgets]
                        logger.info(
                            "AGENT_REPLY_WIDGETS dispatch_id=%s count=%d tools=%s",
                            payload.dispatch_id, len(all_widgets), tools_used,
                        )
                        # Primary widget (most recent) gets set on the message
                        primary = all_widgets[0]
                        widget_extra = {
                            k: v for k, v in {
                                "title": "Request processed",
                                "tools_used": tools_used,
                                "arguments": primary.get("arguments"),
                                "initial_data": primary.get("initial_data"),
                                "result_kind": primary.get("result_kind"),
                                "tool_action": primary.get("tool_action"),
                                "tool_call_id": primary.get("tool_call_id"),
                                "agent_id": primary.get("agent_id"),
                                # All widgets for frontend to render multiple cards
                                "widgets": all_widgets if len(all_widgets) > 1 else None,
                            }.items() if v is not None
                        }
                        await _set_widget(
                            str(reply_message.id),
                            str(space_uuid),
                            "complete",
                            tool_name=primary["tool_name"],
                            extra_fields=widget_extra,
                            resource_uri=primary.get("resource_uri"),
                        )
                except Exception as widget_err:
                    logger.warning(
                        "AGENT_REPLY_WIDGET_ERROR dispatch_id=%s error=%s",
                        payload.dispatch_id, widget_err,
                    )

            # Clear processing status
            try:
                await redis_sse_broker.publish(
                    space_id=str(space_uuid),
                    event="agent_processing",
                    data={
                        "agent_id": str(agent_uuid),
                        "agent_name": canonical_agent_name,
                        "message_id": payload.message_id,
                        "parent_message_id": payload.message_id,
                        "status": "completed",
                    }
                )
            except Exception:
                pass  # Best effort

            # Emit message_lifecycle:resolved so frontend clears the
            # "Waiting for @agent" pending bubble immediately.
            try:
                await redis_sse_broker.publish(
                    space_id=str(space_uuid),
                    event="message_lifecycle",
                    data={
                        "message_id": payload.message_id,
                        "dispatch_id": payload.dispatch_id,
                        "agent_name": canonical_agent_name,
                        "lifecycle": "resolved",
                    }
                )
            except Exception:
                pass  # Best effort

            # DIAGNOSTIC: Log total handler completion time
            reply_handler_duration_ms = int((time.time() - reply_handler_start) * 1000)
            logger.info(
                f"AGENT_REPLY_HANDLER_DONE agent_name={canonical_agent_name} "
                f"reply_id={reply_message.id} dispatch_id={payload.dispatch_id} "
                f"total_handler_ms={reply_handler_duration_ms} "
                f"returning_at={time.time()}"
            )

            return AgentReplyResult(
                status="success",
                reply_message_id=str(reply_message.id),
            )

    except HTTPException:
        raise
    except Exception as e:
        reply_handler_duration_ms = int((time.time() - reply_handler_start) * 1000)
        logger.error(
            f"AGENT_REPLY_ERROR agent_name={payload.agent_name} "
            f"dispatch_id={payload.dispatch_id} error={e} "
            f"handler_duration_ms={reply_handler_duration_ms}"
        )
        return AgentReplyResult(
            status="error",
            error=str(e),
        )


# =============================================================================
# Async Dispatch Callbacks (Heartbeat + Completion)
# =============================================================================
# These endpoints are called BY Cloudflare containers back INTO GCP.
# They enable the async request-reply pattern where:
# 1. Cloud Tasks dispatches to Cloudflare → returns 2xx immediately
# 2. Cloudflare container processes → calls heartbeat periodically
# 3. Cloudflare container completes → calls complete with response
#
# IMPORTANT: These endpoints work identically in local Docker and GCP.
# The source of the callback doesn't matter - same handler logic.
# =============================================================================


class DispatchHeartbeatPayload(BaseModel):
    """Payload for dispatch heartbeat/progress updates."""
    agent_name: Optional[str] = None
    agent_id: Optional[str] = None
    space_id: Optional[str] = None  # For SSE broadcast
    message_id: Optional[str] = None  # Original triggering message

    # Progress info for UI
    progress: Optional[str] = None  # Human-readable status, e.g., "Processing step 3/10"
    percent_complete: Optional[int] = None  # 0-100 for progress bar
    current_step: Optional[str] = None  # Current step name
    total_steps: Optional[int] = None  # Total number of steps
    step_number: Optional[int] = None  # Current step number
    logs: Optional[list[str]] = None  # Recent log lines

    # Token/usage metrics (for cost tracking and UI display)
    tokens_used: Optional[int] = None  # Total tokens consumed so far
    input_tokens: Optional[int] = None  # Input tokens this step
    output_tokens: Optional[int] = None  # Output tokens this step
    model: Optional[str] = None  # Model being used (e.g., "claude-3-opus")

    # Tool usage tracking
    tool_calls: Optional[int] = None  # Number of tool calls made
    current_tool: Optional[str] = None  # Currently executing tool
    tools_used: Optional[list[str]] = None  # List of tools used so far

    # Timing info
    elapsed_ms: Optional[int] = None  # Time since dispatch started
    estimated_remaining_ms: Optional[int] = None  # Estimated time to completion

    # Optional metadata for extensibility
    metadata: Optional[dict] = None


class DispatchHeartbeatResult(BaseModel):
    """Result of heartbeat update."""
    status: str  # "ok", "error"
    dispatch_id: str
    error: Optional[str] = None


class DispatchCompletePayload(BaseModel):
    """Payload for dispatch completion callback."""
    agent_name: Optional[str] = None
    agent_id: Optional[str] = None
    space_id: Optional[str] = None
    message_id: Optional[str] = None  # Original triggering message

    # Completion status
    completion_status: str = "success"  # "success", "failed", "cancelled"

    # Response content (for success)
    response: Optional[str] = None

    # Error info (for failure)
    error: Optional[str] = None
    error_code: Optional[str] = None

    # Final stats
    total_duration_ms: Optional[int] = None
    steps_completed: Optional[int] = None

    # Token/usage metrics (final totals)
    total_tokens: Optional[int] = None
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    model: Optional[str] = None

    # Tool usage summary
    total_tool_calls: Optional[int] = None
    tools_used: Optional[list[str]] = None

    # Cost estimate (if available)
    estimated_cost_usd: Optional[float] = None


class DispatchCompleteResult(BaseModel):
    """Result of completion callback."""
    status: str  # "ok", "error"
    dispatch_id: str
    reply_message_id: Optional[str] = None
    error: Optional[str] = None


def _validate_dispatch_id(dispatch_id: str) -> None:
    """Validate dispatch_id format to prevent Redis key injection."""
    import re
    if not dispatch_id or not re.fullmatch(r'[a-f0-9\-]{36}', dispatch_id):
        raise HTTPException(status_code=400, detail="Invalid dispatch_id format")


@router.post("/dispatch/{dispatch_id}/heartbeat", response_model=DispatchHeartbeatResult)
async def dispatch_heartbeat(
    dispatch_id: str,
    payload: DispatchHeartbeatPayload,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Receive heartbeat/progress update from Cloudflare container.

    Called periodically by long-running agents to:
    1. Prove they're still alive (prevent stale detection)
    2. Report progress for UI display

    Updates are broadcast via SSE for real-time progress bars.
    This endpoint is designed for frequent calls (every few seconds).
    """
    import time
    from datetime import datetime, timezone

    import redis.asyncio as aioredis

    from app.services.redis_sse_broker import redis_sse_broker

    settings = get_settings()

    _validate_dispatch_id(dispatch_id)

    # Validate internal API key
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        logger.warning(f"DISPATCH_HEARTBEAT_UNAUTHORIZED dispatch_id={dispatch_id}")
        raise HTTPException(status_code=401, detail="Invalid API key")

    heartbeat_time = time.time()

    logger.info(
        f"DISPATCH_HEARTBEAT dispatch_id={dispatch_id} "
        f"agent_name={payload.agent_name} progress={payload.progress} "
        f"percent={payload.percent_complete} step={payload.step_number}/{payload.total_steps}"
    )

    try:
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)

        # Store heartbeat state in Redis
        # Key: dispatch:{dispatch_id}
        # This allows stale detection and state queries
        heartbeat_data = {
            "last_heartbeat": str(heartbeat_time),
            "status": "processing",
            "agent_name": payload.agent_name or "",
            "progress": payload.progress or "",
            "percent_complete": str(payload.percent_complete) if payload.percent_complete is not None else "",
            "current_step": payload.current_step or "",
            "step_number": str(payload.step_number) if payload.step_number is not None else "",
            "total_steps": str(payload.total_steps) if payload.total_steps is not None else "",
            # Token/usage metrics
            "tokens_used": str(payload.tokens_used) if payload.tokens_used is not None else "",
            "model": payload.model or "",
            "tool_calls": str(payload.tool_calls) if payload.tool_calls is not None else "",
            "current_tool": payload.current_tool or "",
            "elapsed_ms": str(payload.elapsed_ms) if payload.elapsed_ms is not None else "",
        }

        await redis_conn.hset(f"dispatch:{dispatch_id}", mapping=heartbeat_data)
        # Set TTL of 1 hour - heartbeats older than this are stale
        await redis_conn.expire(f"dispatch:{dispatch_id}", 3600)

        # Broadcast progress via SSE for real-time UI updates
        # Include ALL details for rich frontend display
        if payload.space_id:
            try:
                await redis_sse_broker.publish(
                    space_id=payload.space_id,
                    event="dispatch_progress",
                    data={
                        "dispatch_id": dispatch_id,
                        "agent_id": payload.agent_id,
                        "agent_name": payload.agent_name,
                        "message_id": payload.message_id,
                        "status": "processing",
                        # Progress
                        "progress": payload.progress,
                        "percent_complete": payload.percent_complete,
                        "current_step": payload.current_step,
                        "step_number": payload.step_number,
                        "total_steps": payload.total_steps,
                        # Token usage
                        "tokens_used": payload.tokens_used,
                        "input_tokens": payload.input_tokens,
                        "output_tokens": payload.output_tokens,
                        "model": payload.model,
                        # Tool usage
                        "tool_calls": payload.tool_calls,
                        "current_tool": payload.current_tool,
                        "tools_used": payload.tools_used,
                        # Timing
                        "elapsed_ms": payload.elapsed_ms,
                        "estimated_remaining_ms": payload.estimated_remaining_ms,
                        "timestamp": heartbeat_time,
                    }
                )
            except Exception as sse_err:
                logger.warning(f"DISPATCH_HEARTBEAT_SSE_ERROR dispatch_id={dispatch_id} error={sse_err}")
                # Don't fail - SSE is best-effort

            # Also publish as agent_activity so the frontend processing
            # indicator updates with tool names and status in real time.
            # The frontend listens for agent_activity (not dispatch_progress)
            # to drive the spinner / "Calling <tool>..." display.
            try:
                # Map heartbeat status to agent_activity status
                activity_status = "processing"
                if payload.current_tool:
                    activity_status = "tool_call"

                await redis_sse_broker.publish(
                    space_id=payload.space_id,
                    event="agent_activity",
                    data={
                        "agent_id": payload.agent_id,
                        "agent_name": payload.agent_name,
                        "status": activity_status,
                        "activity": activity_status,
                        "message_id": payload.message_id,
                        "parent_message_id": payload.message_id,
                        "tool_name": payload.current_tool or "",
                        "is_healthy": True,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as sse_err:
                logger.warning(f"DISPATCH_HEARTBEAT_ACTIVITY_SSE_ERROR dispatch_id={dispatch_id} error={sse_err}")
                # Don't fail - SSE is best-effort

        await redis_conn.close()

        return DispatchHeartbeatResult(
            status="ok",
            dispatch_id=dispatch_id,
        )

    except Exception as e:
        logger.error(f"DISPATCH_HEARTBEAT_ERROR dispatch_id={dispatch_id} error={e}")
        return DispatchHeartbeatResult(
            status="error",
            dispatch_id=dispatch_id,
            error=str(e),
        )


@router.post("/dispatch/{dispatch_id}/complete", response_model=DispatchCompleteResult)
async def dispatch_complete(
    dispatch_id: str,
    payload: DispatchCompletePayload,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Receive completion callback from Cloudflare container.

    Called when agent finishes processing to:
    1. Deliver the response (posted as a message)
    2. Clear dispatch tracking state
    3. Broadcast completion via SSE

    This replaces the synchronous HTTP response pattern that was
    failing due to connection drops.
    """
    import time
    from datetime import datetime, timezone
    from uuid import UUID

    import redis.asyncio as aioredis
    from sqlalchemy import select, text

    from app.core.database import AsyncSessionLocal
    from app.models.agent import Agent
    from app.models.message import Message
    from app.services.messages_notifications import MessagesNotificationHelper, sanitize_self_mentions
    from app.services.redis_sse_broker import redis_sse_broker

    settings = get_settings()

    _validate_dispatch_id(dispatch_id)

    # Validate internal API key
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        logger.warning(f"DISPATCH_COMPLETE_UNAUTHORIZED dispatch_id={dispatch_id}")
        raise HTTPException(status_code=401, detail="Invalid API key")

    complete_time = time.time()

    # Create response preview for logging (first 100 chars)
    response_preview = ""
    if payload.response:
        response_preview = payload.response[:100].replace("\n", " ").replace('"', "'")

    logger.info(
        f"DISPATCH_COMPLETE dispatch_id={dispatch_id} "
        f"agent_name={payload.agent_name} status={payload.completion_status} "
        f"response_len={len(payload.response) if payload.response else 0} "
        f"duration_ms={payload.total_duration_ms} "
        f'response_preview="{response_preview}"'
    )

    reply_message_id = None

    try:
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)

        # Update dispatch state to completed
        complete_data = {
            "status": "completed",
            "completion_status": payload.completion_status,
            "completed_at": str(complete_time),
            "total_duration_ms": str(payload.total_duration_ms) if payload.total_duration_ms else "",
        }
        await redis_conn.hset(f"dispatch:{dispatch_id}", mapping=complete_data)
        # Keep completed dispatch for 24 hours for debugging
        await redis_conn.expire(f"dispatch:{dispatch_id}", 86400)

        # If success with response, post it as a message
        if payload.completion_status == "success" and payload.response and payload.response.strip():
            if payload.agent_id and payload.message_id and payload.space_id:
                try:
                    async with AsyncSessionLocal() as db:
                        space_uuid = UUID(payload.space_id)
                        agent_uuid = UUID(payload.agent_id)
                        message_uuid = UUID(payload.message_id)

                        # Set RLS context
                        await db.execute(
                            text("SELECT set_config('app.current_space_id', :sid, true)"),
                            {"sid": str(space_uuid)},
                        )

                        # Verify agent exists
                        agent_result = await db.execute(
                            select(Agent).where(Agent.id == agent_uuid)
                        )
                        agent = agent_result.scalar_one_or_none()

                        if not agent:
                            logger.error(f"DISPATCH_COMPLETE_AGENT_NOT_FOUND dispatch_id={dispatch_id} agent_id={payload.agent_id}")
                        else:
                            # Verify original message exists
                            original_result = await db.execute(
                                select(Message).where(Message.id == message_uuid)
                            )
                            original_msg = original_result.scalar_one_or_none()

                            if not original_msg:
                                logger.warning(
                                    f"DISPATCH_COMPLETE_PARENT_NOT_FOUND dispatch_id={dispatch_id} "
                                    f"message_id={payload.message_id}"
                                )
                            else:
                                # Sanitize self-mentions
                                sanitized_content, was_stripped = sanitize_self_mentions(
                                    payload.response, payload.agent_name or agent.name
                                )

                                notifier = MessagesNotificationHelper(
                                    db=db,
                                    redis_client=redis_conn,
                                    sse_broker=redis_sse_broker,
                                )
                                if await _is_reply_delivery_blocked(
                                    notifier=notifier,
                                    agent_id=agent.id,
                                    space_id=space_uuid,
                                    agent_name=payload.agent_name or agent.name,
                                    dispatch_id=dispatch_id,
                                    source="DISPATCH_COMPLETE",
                                ):
                                    logger.info(
                                        "DISPATCH_COMPLETE_REPLY_SUPPRESSED dispatch_id=%s agent_id=%s space_id=%s",
                                        dispatch_id,
                                        payload.agent_id,
                                        payload.space_id,
                                    )
                                else:
                                    # Create the reply message
                                    # SECURITY: user_id must be None for agent messages to prevent
                                    # impersonation. Matches the agent-reply handler pattern.
                                    reply_message = Message(
                                        space_id=space_uuid,
                                        agent_id=agent_uuid,
                                        user_id=None,  # Agent message — never carries user_id
                                        content=sanitized_content.strip(),
                                        parent_id=message_uuid,
                                        created_at=datetime.now(timezone.utc),
                                    )

                                    db.add(reply_message)
                                    await db.commit()
                                    await db.refresh(reply_message)

                                    reply_message_id = str(reply_message.id)

                                    # ALC: agent produced output (dispatch reply) — keep the
                                    # lifecycle clock alive. This is the site that makes
                                    # on_demand agents count as alive. Non-fatal.
                                    from app.core.agent_lifecycle import touch_agent_activity
                                    await touch_agent_activity(db, agent_uuid)

                                    logger.info(
                                        f"DISPATCH_COMPLETE_SAVED dispatch_id={dispatch_id} "
                                        f"reply_id={reply_message_id} agent_name={payload.agent_name}"
                                    )

                                    # Broadcast the message via SSE
                                    await notifier.broadcast_sse(
                                        space_id=space_uuid,
                                        msg=reply_message,
                                        author_name=payload.agent_name or agent.name,
                                        author_type="agent",  # SECURITY: Agent replies must always be typed as "agent"
                                    )

                                    # Intelligence handled by aX dispatch — see dispatch_executor.py

                                    # Emit message_lifecycle:resolved so frontend clears the
                                    # "Waiting for @agent" pending bubble immediately.
                                    try:
                                        await redis_sse_broker.publish(
                                            space_id=str(space_uuid),
                                            event="message_lifecycle",
                                            data={
                                                "message_id": str(message_uuid),
                                                "dispatch_id": dispatch_id,
                                                "agent_name": payload.agent_name or agent.name,
                                                "lifecycle": "resolved",
                                            }
                                        )
                                    except Exception:
                                        pass  # Best effort

                except Exception as save_err:
                    logger.error(f"DISPATCH_COMPLETE_SAVE_ERROR dispatch_id={dispatch_id} error={save_err}")
            else:
                logger.warning(
                    f"DISPATCH_COMPLETE_MISSING_IDS dispatch_id={dispatch_id} "
                    f"agent_id={payload.agent_id} message_id={payload.message_id} space_id={payload.space_id}"
                )

        # Broadcast completion via SSE with all final stats
        if payload.space_id:
            try:
                await redis_sse_broker.publish(
                    space_id=payload.space_id,
                    event="dispatch_progress",
                    data={
                        "dispatch_id": dispatch_id,
                        "agent_id": payload.agent_id,
                        "agent_name": payload.agent_name,
                        "message_id": payload.message_id,
                        "status": "completed",
                        "completion_status": payload.completion_status,
                        "reply_message_id": reply_message_id,
                        "percent_complete": 100,
                        # Final stats
                        "total_duration_ms": payload.total_duration_ms,
                        "steps_completed": payload.steps_completed,
                        # Token usage totals
                        "total_tokens": payload.total_tokens,
                        "total_input_tokens": payload.total_input_tokens,
                        "total_output_tokens": payload.total_output_tokens,
                        "model": payload.model,
                        # Tool usage summary
                        "total_tool_calls": payload.total_tool_calls,
                        "tools_used": payload.tools_used,
                        # Cost
                        "estimated_cost_usd": payload.estimated_cost_usd,
                        "timestamp": complete_time,
                    }
                )

                # Also send agent_processing completed event for backwards compat
                await redis_sse_broker.publish(
                    space_id=payload.space_id,
                    event="agent_processing",
                    data={
                        "agent_id": payload.agent_id,
                        "agent_name": payload.agent_name,
                        "message_id": payload.message_id,
                        "parent_message_id": payload.message_id,
                        "status": "completed",
                    }
                )
            except Exception as sse_err:
                logger.warning(f"DISPATCH_COMPLETE_SSE_ERROR dispatch_id={dispatch_id} error={sse_err}")

        # Delivery confirmation: update routing tracking and publish status
        try:
            routing_key = f"ax:routing-track:{dispatch_id}"
            routing_data = await redis_conn.hgetall(routing_key)
            if routing_data:
                # Determine final status
                if payload.completion_status == "success" and reply_message_id:
                    routing_status = "responded"
                elif payload.completion_status == "success":
                    routing_status = "acknowledged"
                else:
                    routing_status = "failed"

                await redis_conn.hset(routing_key, mapping={
                    "status": routing_status,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "reply_message_id": reply_message_id or "",
                })

                # Publish routing_status SSE event for sender visibility
                if payload.space_id:
                    await redis_sse_broker.publish(
                        space_id=routing_data.get("space_id", payload.space_id),
                        event="routing_status",
                        data={
                            "original_message_id": routing_data.get("original_msg_id", ""),
                            "target_agent": routing_data.get("target_agent_name", payload.agent_name),
                            "target_agent_id": routing_data.get("target_agent_id", payload.agent_id),
                            "status": routing_status,
                            "reply_message_id": reply_message_id,
                            "dispatch_id": dispatch_id,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    )

                logger.info(
                    "ROUTING_STATUS_UPDATED dispatch_id=%s status=%s target=%s",
                    dispatch_id, routing_status, routing_data.get("target_agent_name"),
                )
        except Exception as routing_err:
            logger.warning(
                "ROUTING_STATUS_UPDATE_FAILED dispatch_id=%s error=%s",
                dispatch_id, routing_err,
            )

        await redis_conn.close()

        return DispatchCompleteResult(
            status="ok",
            dispatch_id=dispatch_id,
            reply_message_id=reply_message_id,
        )

    except Exception as e:
        logger.error(f"DISPATCH_COMPLETE_ERROR dispatch_id={dispatch_id} error={e}")
        return DispatchCompleteResult(
            status="error",
            dispatch_id=dispatch_id,
            error=str(e),
        )


@router.get("/dispatch/{dispatch_id}/status")
async def dispatch_status(
    dispatch_id: str,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Get current status of a dispatch.

    Used by UI to poll for status if SSE is unavailable,
    and for debugging/observability.
    """
    import redis.asyncio as aioredis

    settings = get_settings()

    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)

        data = await redis_conn.hgetall(f"dispatch:{dispatch_id}")
        await redis_conn.close()

        if not data:
            return {"status": "not_found", "dispatch_id": dispatch_id}

        return {
            "status": data.get("status", "unknown"),
            "dispatch_id": dispatch_id,
            **data,
        }

    except Exception as e:
        logger.error(f"DISPATCH_STATUS_ERROR dispatch_id={dispatch_id} error={e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dispatch-health")
async def dispatch_health():
    """Health check for dispatch system."""
    return {
        "status": "healthy",
        "service": "internal-dispatch",
    }




@router.post("/agent-mesh/check-in", response_model=AgentMeshSweepResult)
async def agent_mesh_check_in(
    payload: AgentMeshNudgePayload,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """Create task-aware recurring check-ins for the autonomous agent mesh.

    Intended for an external scheduler (Cloud Scheduler, cron, etc.) to call every
    15-30 minutes. The sweep writes a context packet and posts a normal aX message
    from the space agent that @mentions each target agent, so existing routing,
    dispatch, and SSE paths are exercised by the agents themselves.
    """
    settings = get_settings()
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return await _run_agent_mesh_sweep(payload)




@router.post("/mcp-notify")
async def mcp_notify(request: Request):
    """Trigger webhook dispatch for MCP-originated messages with @mentions."""
    import json
    import uuid
    from uuid import UUID

    import redis.asyncio as aioredis
    from sqlalchemy import func, select, text

    from app.core.database import AsyncSessionLocal
    from app.models.agent import Agent
    from app.models.message import Message
    from app.services.messages_notifications import MessagesNotificationHelper
    from app.services.redis_sse_broker import redis_sse_broker

    settings = get_settings()
    x_api_key = request.headers.get("X-API-Key", "")
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.json()
    message_id = body.get("message_id")
    space_id = body.get("space_id")
    mentions = body.get("mentions", [])
    sender_agent_id = body.get("sender_agent_id")

    if not message_id or not space_id or not mentions:
        raise HTTPException(status_code=400, detail="message_id, space_id, mentions required")

    # Validate UUIDs up front — return 400, not 500, on bad input
    try:
        message_uuid = UUID(message_id)
        space_uuid = UUID(space_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {e}")

    logger.info(f"MCP_NOTIFY msg={message_id} mentions={mentions}")

    try:
        # Single Redis client for all agent dispatches in this request
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

        try:
            async with AsyncSessionLocal() as db:
                # Set RLS context
                await db.execute(
                    text("SELECT set_config('app.current_space_id', :sid, true)"),
                    {"sid": str(space_uuid)},
                )

                # Find the message using SQLAlchemy
                msg_result = await db.execute(
                    select(Message).where(Message.id == message_uuid)
                )
                msg = msg_result.scalar_one_or_none()

                if not msg:
                    return {"status": "message_not_found"}

                # Find external gateway agents matching mentions using SQLAlchemy
                # Convert mentions to lowercase for case-insensitive matching
                lower_mentions = [mention.lower() for mention in mentions]

                from app.core.agent_space import agents_in_space_subquery
                agents_result = await db.execute(
                    select(Agent.id, Agent.name, Agent.webhook_url)
                    .where(
                        func.lower(Agent.name).in_(lower_mentions),
                        Agent.id.in_(agents_in_space_subquery(space_uuid)),
                        Agent.status == 'active',
                        Agent.origin == 'external_gateway'
                    )
                )
                agents = agents_result.fetchall()

                notifier = MessagesNotificationHelper(
                    db=db,
                    redis_client=redis_client,
                    sse_broker=redis_sse_broker,
                )

                dispatched = []
                for agent in agents:
                    if sender_agent_id and str(agent.id) == sender_agent_id:
                        continue
                    if await notifier._is_agent_disabled_cache(str(agent.id), str(msg.space_id)):
                        logger.info(
                            "MCP_NOTIFY_SKIP_DISABLED message_id=%s agent_id=%s agent_name=%s space_id=%s",
                            message_id,
                            agent.id,
                            agent.name,
                            msg.space_id,
                        )
                        continue

                    dispatch_id = str(uuid.uuid4())
                    agent_name = agent.name

                    # Build payload matching dispatch worker format.
                    # webhook_secret intentionally omitted — dispatch worker fetches
                    # it from the DB at send time to avoid secrets in the stream.
                    payload = {
                        "payload_version": "3",
                        "message_id": str(msg.id),
                        "content": msg.content or "",
                        "space_id": str(msg.space_id),
                        "space_id": str(msg.space_id),
                        "created_at": msg.created_at.isoformat() if msg.created_at else "",
                        "dispatch_id": dispatch_id,
                        "dispatch_type": "webhook",
                        "agent_name": agent_name,
                        "agent_id": str(agent.id),
                        "webhook_url": agent.webhook_url or "",
                    }

                    task_data = {
                        "task_id": str(uuid.uuid4()),
                        "dispatch_id": dispatch_id,
                        "dispatch_type": "webhook",
                        "agent_id": str(agent.id),
                        "agent_name": agent_name,
                        "message_id": str(msg.id),
                        "payload": json.dumps(payload),
                        "attempt": "0",
                    }

                    stream_id = await redis_client.xadd(
                        "dispatch:cloud_agents",
                        {k: str(v) for k, v in task_data.items()},
                        maxlen=10000,
                    )
                    dispatched.append(agent_name)
                    logger.info(
                        f"MCP_NOTIFY_QUEUED agent={agent_name} dispatch_id={dispatch_id} stream_id={stream_id}"
                    )

            return {"status": "ok", "dispatched": dispatched}
        finally:
            await redis_client.aclose()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MCP_NOTIFY_ERROR: {e}", exc_info=True)
        return {"status": "error", "detail": str(e)}


# ---------------------------------------------------------------------------
# POST /internal/embed — Text embedding for concierge router scoring
# ---------------------------------------------------------------------------

class EmbedRequest(BaseModel):
    """Embed one string or a batch."""
    text: Optional[str] = None
    texts: Optional[list[str]] = None

    def validate_input(self):
        if self.text is None and not self.texts:
            raise ValueError("Provide 'text' (single) or 'texts' (batch)")
        if self.text is not None and self.texts is not None:
            raise ValueError("Provide either 'text' or 'texts', not both")


class EmbedResponse(BaseModel):
    """
    embedding  — present for single-text requests
    embeddings — present for batch requests
    model      — model identifier (for cache-key hygiene)
    dim        — vector dimensionality
    """
    embedding: Optional[list[float]] = None
    embeddings: Optional[list[list[float]]] = None
    model: str
    dim: int


@router.post(
    "/embed",
    response_model=EmbedResponse,
    summary="Embed text for concierge router semantic scoring (internal only)",
)
async def embed_text_endpoint(
    body: EmbedRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Internal endpoint for concierge router scoring layer.
    Model: sentence-transformers/all-MiniLM-L6-v2 via fastembed (ONNX).
    Requires X-API-Key header == INTERNAL_API_KEY env var.
    """
    from app.core.config import get_settings
    from app.core.embed import embed_text, embed_batch

    settings = get_settings()
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        raise HTTPException(status_code=401, detail="Invalid internal API key")

    try:
        body.validate_input()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    try:
        loop = asyncio.get_event_loop()
        if body.text is not None:
            # run_in_executor: embed_text is synchronous (ONNX inference) —
            # must not block the async event loop or Uvicorn workers will crash
            vec = await loop.run_in_executor(None, embed_text, body.text)
            return EmbedResponse(
                embedding=vec,
                model="sentence-transformers/all-MiniLM-L6-v2",
                dim=len(vec),
            )
        else:
            vecs = await loop.run_in_executor(None, embed_batch, body.texts)
            dim = len(vecs[0]) if vecs else 384
            return EmbedResponse(
                embeddings=vecs,
                model="sentence-transformers/all-MiniLM-L6-v2",
                dim=dim,
            )
    except Exception as e:
        logger.error(f"Embed error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)}")


# ---------------------------------------------------------------------------
# POST /internal/routing-logs  — Create concierge routing log entry
# PATCH /internal/routing-logs/{request_id}/outcome — Async outcome write-back
#
# Called by ax-mcp-server/concierge_router.py:
#   - POST at decision time (outcome_status = NULL / pending)
#   - PATCH after delivery confirmation (outcome_status = accepted|redirected|…)
#
# Auth: X-API-Key == INTERNAL_DISPATCH_API_KEY (same key as /internal/embed)
# Owner: @logic_runner_677
# Story: shared/state/stories/concierge-router-scoring-fix.md
# Migration: router02_create_concierge_routing_logs.py  ← must run first
# ---------------------------------------------------------------------------

VALID_OUTCOME_STATUSES = {"accepted", "redirected", "escalated", "no_response"}


class RoutingLogCreateRequest(BaseModel):
    """Payload sent by concierge_router.py at decision time."""
    request_id: str                          # UUID string from router
    query_hash: str                          # 16-char SHA-256 prefix
    combined_winner: Optional[str] = None   # winning agent handle
    resolution_step: Optional[int] = None   # 1-6
    confidence: Optional[float] = None      # 0.0–1.0
    embedding_model: Optional[str] = None
    latency_ms: Optional[int] = None
    pipeline_json: Optional[dict] = None    # full pipeline data
    decision_json: Optional[dict] = None    # full decision data
    # Filter pipeline observability (BE-ROUTER-FILTERS-01)
    filter_stage: Optional[str] = None      # 'passed' | 'flagged_injection' | 'flagged_spam' | 'rate_limited'
    filter_reason: Optional[str] = None     # human-readable reason if flagged
    override_used: Optional[bool] = None    # true if user override applied
    # Filter pipeline columns (router03 migration)
    filter_stage: Optional[str] = None      # passed | flagged_injection | flagged_spam | rate_limited
    filter_reason: Optional[str] = None     # human-readable reason (set when flagged)
    override_used: Optional[bool] = None    # True when override_token bypassed a flag


class RoutingLogCreateResponse(BaseModel):
    id: str
    request_id: str


class RoutingLogOutcomePatch(BaseModel):
    outcome_status: str                          # one of VALID_OUTCOME_STATUSES
    outcome_json: Optional[dict] = None          # rich metadata for Phase 2 training


@router.post(
    "/routing-logs",
    response_model=RoutingLogCreateResponse,
    status_code=201,
    summary="Create a concierge routing log entry (internal only)",
)
async def create_routing_log(
    body: RoutingLogCreateRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Called by concierge_router.py immediately after a routing decision.
    outcome_status starts NULL — patched to final value by delivery layer.
    Requires X-API-Key header == INTERNAL_DISPATCH_API_KEY.
    """
    import uuid as _uuid
    from app.core.config import get_settings
    from app.core.database import AsyncSessionLocal
    from app.models.concierge_routing_log import ConciergeRoutingLog
    from sqlalchemy import select

    settings = get_settings()
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        raise HTTPException(status_code=401, detail="Invalid internal API key")

    try:
        request_uuid = _uuid.UUID(body.request_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="request_id must be a valid UUID")

    async with AsyncSessionLocal() as db:
        # Idempotency: return existing row if request_id already logged
        existing = await db.execute(
            select(ConciergeRoutingLog).where(
                ConciergeRoutingLog.request_id == request_uuid
            )
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            logger.info(f"routing-logs: duplicate request_id {body.request_id}, returning existing")
            return RoutingLogCreateResponse(id=str(row.id), request_id=str(row.request_id))

        log = ConciergeRoutingLog(
            request_id=request_uuid,
            query_hash=body.query_hash,
            combined_winner=body.combined_winner,
            resolution_step=body.resolution_step,
            confidence=body.confidence,
            embedding_model=body.embedding_model,
            latency_ms=body.latency_ms,
            pipeline_json=body.pipeline_json,
            decision_json=body.decision_json,
            # outcome_status intentionally omitted — NULL until delivery confirms
            # Filter pipeline observability (router03): pass through if provided
            filter_stage=body.filter_stage,
            filter_reason=body.filter_reason,
            override_used=body.override_used,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        logger.info(
            f"routing-logs: created {log.id} for request {body.request_id} "
            f"winner={body.combined_winner!r} step={body.resolution_step}"
        )
        return RoutingLogCreateResponse(id=str(log.id), request_id=str(log.request_id))


@router.patch(
    "/routing-logs/{request_id}/outcome",
    summary="Write-back routing outcome after delivery (internal only)",
)
async def patch_routing_log_outcome(
    request_id: str,
    body: RoutingLogOutcomePatch,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    """
    Called by the delivery layer once a routed message is confirmed delivered,
    redirected, escalated, or timed out.

    Flips outcome_status from NULL to the final value. This is the signal that
    populates the Phase 2 classifier training set — every NULL is a missing
    data point.

    Requires X-API-Key header == INTERNAL_DISPATCH_API_KEY.
    """
    import uuid as _uuid
    from app.core.config import get_settings
    from app.core.database import AsyncSessionLocal
    from app.models.concierge_routing_log import ConciergeRoutingLog
    from sqlalchemy import select

    settings = get_settings()
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        raise HTTPException(status_code=401, detail="Invalid internal API key")

    if body.outcome_status not in VALID_OUTCOME_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"outcome_status must be one of {sorted(VALID_OUTCOME_STATUSES)}",
        )

    try:
        request_uuid = _uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="request_id must be a valid UUID")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ConciergeRoutingLog).where(
                ConciergeRoutingLog.request_id == request_uuid
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"No routing log found for request_id {request_id}",
            )

        previous = row.outcome_status
        row.outcome_status = body.outcome_status
        if body.outcome_json is not None:
            row.outcome_json = body.outcome_json
        await db.commit()
        logger.info(
            f"routing-logs: outcome patched for {request_id}: "
            f"{previous!r} → {body.outcome_status!r}"
        )
        return {
            "request_id": request_id,
            "outcome_status": body.outcome_status,
            "outcome_json": row.outcome_json,
            "previous": previous,
        }


# ---------------------------------------------------------------------------
# POST /internal/agent-signal — store an agent's forward-looking liveness signal
#
# Called by the ax-presence listener each heartbeat (peach's agent-signal-record).
# The listener writes a box-local file; this endpoint makes the same fields readable
# centrally so the Agent Lifecycle (ALC) sweep can distinguish dormant from
# online-but-broken (currently_401) and route the latter to FIX, not archive.
#
# Auth: X-API-Key == INTERNAL_DISPATCH_API_KEY (same key as other /internal endpoints).
# Design: docs/plans/2026-05-29-agent-lifecycle-design.md (section 7)
# ---------------------------------------------------------------------------


class AgentSignalPayload(BaseModel):
    agent_id: str
    currently_401: bool = False
    responsiveness_ratio: Optional[float] = None
    mentions_seen: Optional[int] = None
    replies_sent: Optional[int] = None
    last_reply_at: Optional[str] = None


@router.post("/agent-signal", summary="Store an agent's forward-looking liveness signal (internal only)")
async def store_agent_signal_endpoint(
    body: AgentSignalPayload,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    settings = get_settings()
    if not secrets.compare_digest(x_api_key, settings.internal_dispatch_api_key):
        logger.warning("AGENT_SIGNAL_UNAUTHORIZED agent_id=%s", body.agent_id)
        raise HTTPException(status_code=401, detail="Invalid API key")

    from app.core.agent_lifecycle import store_agent_signal

    fields = body.model_dump(exclude={"agent_id"})
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    await store_agent_signal(redis_client, body.agent_id, fields)
    return {"ok": True, "agent_id": body.agent_id, "ttl_seconds": 90}
