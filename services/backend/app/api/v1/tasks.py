"""
Task Management API endpoints
Handles task creation, assignment, and lifecycle management
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, update, delete, and_, or_, text
from sqlalchemy.orm import selectinload
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from typing import Annotated, Any, List, Optional, Literal
from datetime import datetime, timezone, timedelta
import uuid

from ...core.authorization import verify_space_actor_access
from ...core.api_action_registry import declare_route_action
from ...core.rls import SecureSession, get_secure_session
from ...core.schema_readiness import check_tasks_typed_assignee_schema
from ...models.task import Task
from ...models.task_note import TaskNote
from ...models.agent import Agent
from ...models.agent_space_access import AgentSpaceAccess
from ...models.space_membership import SpaceMembership
from ...models.user import User
from ...services.auto_assignment import AutoAssignmentService
from ...services.redis_sse_broker import redis_sse_broker
from ...services.entity_summary_service import summarize_task_leaf
from ...services.task_reminder_delivery import (
    get_task_reminder_pause_readback,
    get_task_reminder_pause_state,
    notify_task_completed,
    set_task_reminder_pause_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["tasks"])

TASK_WORK_STATUS_PATTERN = "^(open|assigned|not_started|in_progress|blocked|completed|cancelled)$"
TASK_QUEUE_STATE_PATTERN = "^(queued|active|blocked|snoozed|inactive)$"
ASSIGNEE_TYPES = {"user", "agent"}
TASK_STATUS_MAPPING = {
    "open": "not_started",
    "assigned": "in_progress",
    "not_started": "not_started",
    "in_progress": "in_progress",
    "blocked": "blocked",
    "completed": "completed",
    "cancelled": "cancelled",
}
TERMINAL_TASK_STATUSES = {"completed", "cancelled"}

class TaskReminderGlobalPausePayload(BaseModel):
    paused: bool = False
    reason: Optional[str] = None


class TaskReminderWorkingHoursPayload(BaseModel):
    enabled: bool = False
    timezone: str = "UTC"
    start: str = Field(default="08:00", pattern=r"^\d{2}:\d{2}$")
    end: str = Field(default="22:00", pattern=r"^\d{2}:\d{2}$")

    @field_validator("start", "end")
    @classmethod
    def validate_hhmm(cls, value: str) -> str:
        hour_s, minute_s = value.split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("must be HH:MM with a 00-23 hour and 00-59 minute")
        return value


class TaskReminderPausePayload(BaseModel):
    global_pause: TaskReminderGlobalPausePayload = Field(default_factory=TaskReminderGlobalPausePayload, alias="global")
    working_hours: TaskReminderWorkingHoursPayload = Field(default_factory=TaskReminderWorkingHoursPayload)
    updated_by: Optional[str] = None

    model_config = {"populate_by_name": True}


def _as_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _datetime_lte(value: Optional[datetime], now: datetime) -> bool:
    value = _as_aware_utc(value)
    if value is None:
        return False
    return value <= now


def _task_queue_state_for_status(work_status: Optional[str]) -> str:
    if work_status == "in_progress":
        return "active"
    if work_status == "blocked":
        return "blocked"
    if work_status in TERMINAL_TASK_STATUSES:
        return "inactive"
    return "queued"


def _effective_task_queue_state(task: Task) -> str:
    return getattr(task, "queue_state", None) or _task_queue_state_for_status(getattr(task, "work_status", None))


def _is_reminder_eligible(task: Task, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if getattr(task, "work_status", None) in TERMINAL_TASK_STATUSES:
        return False
    if _effective_task_queue_state(task) == "inactive":
        return False
    if getattr(task, "stale_at", None) is not None:
        return False
    if not _datetime_lte(getattr(task, "next_reminder_at", None), now):
        return False
    snoozed_until = getattr(task, "snoozed_until", None)
    return snoozed_until is None or _datetime_lte(snoozed_until, now)


def _task_reason_fields(task: Task) -> dict[str, Optional[str]]:
    metadata = task.task_metadata if isinstance(getattr(task, "task_metadata", None), dict) else {}
    return {
        "blocked_reason": metadata.get("blocked_reason"),
        "completed_reason": metadata.get("completed_reason") or metadata.get("completion_notes"),
    }



def _task_actor_payload(session: SecureSession) -> dict[str, Optional[str]]:
    return {
        "type": "agent" if session.is_agent else "user",
        "user_id": str(session.user.id) if getattr(session, "user", None) is not None else None,
        "agent_id": str(session.agent_id) if getattr(session, "agent_id", None) else None,
    }


async def _require_task_reminder_pause_admin(session: SecureSession) -> None:
    """Only space admins/owners may mutate the global reminder pause switch."""
    membership = await session.db.scalar(
        select(SpaceMembership).where(
            and_(
                SpaceMembership.user_id == session.user.id,
                SpaceMembership.space_id == uuid.UUID(str(session.space_id)),
            )
        )
    )
    role = (getattr(membership, "role", None) or "").lower()
    if role not in {"admin", "owner"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only space admins can mutate task reminder pause state",
        )


async def _resolve_effective_task_space_id(
    session: SecureSession,
    space_id: Optional[str],
) -> str:
    """Resolve an explicit task-space override after membership verification."""
    if not space_id:
        return session.space_id
    try:
        effective_space_id = str(uuid.UUID(space_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid space_id format",
        ) from exc
    await verify_space_actor_access(
        session.db,
        user_id=session.user.id,
        space_id=effective_space_id,
        is_agent=session.is_agent,
        agent_id=session.agent_id,
    )
    return effective_space_id


def _task_mutation_activity_payload(
    *,
    task_id: str,
    mutation: str,
    outcome: Literal["succeeded", "failed"],
    actor: dict,
    space_id: str,
    attempted: Optional[dict] = None,
    task: Optional[Task] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "task_mutation",
        "mutation": mutation,
        "outcome": outcome,
        "task_id": str(getattr(task, "id", task_id)),
        "space_id": str(getattr(task, "space_id", space_id)),
        "actor": actor,
        "attempted": attempted or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if task is not None:
        payload.update(
            {
                "task_display_id": _task_display_id(task),
                **_task_reference_fields(task),
                "title": getattr(task, "title", None),
                "status": getattr(task, "work_status", None),
                "queue_state": _effective_task_queue_state(task),
                **_task_reason_fields(task),
            }
        )
    if error:
        payload["error"] = error
    return payload


async def _publish_task_mutation_activity(
    *,
    space_id: str,
    task_id: str,
    mutation: str,
    outcome: Literal["succeeded", "failed"],
    actor: dict,
    attempted: Optional[dict] = None,
    task: Optional[Task] = None,
    error: Optional[str] = None,
) -> None:
    payload = _task_mutation_activity_payload(
        task_id=task_id,
        mutation=mutation,
        outcome=outcome,
        actor=actor,
        space_id=space_id,
        attempted=attempted,
        task=task,
        error=error,
    )
    for event_name in ("task_mutation", "task_activity"):
        try:
            await redis_sse_broker.publish(space_id=space_id, event=event_name, data=payload)
        except Exception as exc:  # pragma: no cover - audit/activity must not mask API result
            logger.warning("Failed to publish task mutation activity event=%s task_id=%s: %s", event_name, task_id, exc)


async def _reload_task_for_response(db, task_id: uuid.UUID) -> Task:
    # Bulk UPDATE statements can leave an already-loaded Task/relationship in the
    # session identity map. Force the response reload to overwrite that cached
    # state so update endpoints never acknowledge a mutation with stale
    # assignee/status/reminder values.
    await db.flush()
    result = await db.execute(
        select(Task)
        .options(
            selectinload(Task.posted_by_user),
            selectinload(Task.posted_by_agent),
            selectinload(Task.assigned_agent),
        )
        .where(Task.id == task_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()



def _task_display_id(task: Task) -> str:
    """Canonical copyable task reference exposed across task API/tool surfaces."""
    task_number = getattr(task, "task_number", None)
    if task_number:
        return f"task_{int(task_number):06d}"
    return "task_legacy"


def _task_reference_fields(task: Task) -> dict[str, Any]:
    """Return stable copy/deep-link fields for task UI surfaces."""
    display_id = _task_display_id(task)
    copyable_ref = display_id if display_id != "task_legacy" else str(task.id)
    deep_link = f"/ax/tasks/{copyable_ref}?space_id={task.space_id}"
    return {
        "task_ref": copyable_ref,
        "task_deep_link": deep_link,
        "task_reference": {
            "id": str(task.id),
            "display_id": display_id,
            "copyable_ref": copyable_ref,
            "deep_link": deep_link,
        },
    }


def _task_ref_filters(task_ref: str, space_id: str):
    """Build resolver predicates for UUID, canonical human ref, or exact title fallback."""
    ref = (task_ref or "").strip()
    if not ref:
        return []

    space_uuid = uuid.UUID(space_id)
    try:
        return [and_(Task.space_id == space_uuid, Task.id == uuid.UUID(ref))]
    except ValueError:
        pass

    normalized = ref.lower()
    if normalized.startswith("task_"):
        numeric = normalized.removeprefix("task_")
        if numeric.isdigit():
            return [and_(Task.space_id == space_uuid, Task.task_number == int(numeric))]
    elif ref.isdigit():
        return [and_(Task.space_id == space_uuid, Task.task_number == int(ref))]

    return [and_(Task.space_id == space_uuid, Task.title == ref)]


async def _resolve_task_for_space(db, task_ref: str, space_id: str, *, options=()) -> Optional[Task]:
    """Resolve a task by UUID, task_display_id/human ref, or exact title fallback."""
    filters = _task_ref_filters(task_ref, space_id)
    if not filters:
        return None
    query = select(Task)
    if options:
        query = query.options(*options)
    result = await db.execute(
        query.where(or_(*filters)).order_by(Task.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()

def _task_lifecycle_fields(task: Task, now: Optional[datetime] = None) -> dict:
    return {
        "queue_state": _effective_task_queue_state(task),
        "queue_rank": getattr(task, "queue_rank", None),
        "reminder_policy": getattr(task, "reminder_policy", None),
        "next_reminder_at": getattr(task, "next_reminder_at", None),
        "last_reminded_at": getattr(task, "last_reminded_at", None),
        "reminder_count": getattr(task, "reminder_count", None) or 0,
        "snoozed_until": getattr(task, "snoozed_until", None),
        "stale_at": getattr(task, "stale_at", None),
        "stale_reason": getattr(task, "stale_reason", None),
        "cancelled_reason": getattr(task, "cancelled_reason", None),
        "reminder_eligible": _is_reminder_eligible(task, now),
    }


def _normalize_actor_payload(actor: Optional[dict]) -> Optional[dict]:
    if not isinstance(actor, dict):
        return None

    actor_id = actor.get("id")
    handle = (
        actor.get("handle")
        or actor.get("username")
        or actor.get("agent_name")
        or actor.get("name")
    )
    if isinstance(handle, str):
        handle = handle.strip().lstrip("@") or None
    display_name = actor.get("display_name") or actor.get("full_name") or actor.get("name")

    payload = {
        "id": str(actor_id) if actor_id else None,
        "handle": handle,
        "display_name": display_name,
        "type": actor.get("type") or actor.get("agent_type"),
    }
    compact = {key: value for key, value in payload.items() if value}
    return compact or None


def _task_reminder_state(lifecycle: dict, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if lifecycle.get("stale_at"):
        return "stale"
    if lifecycle.get("cancelled_reason"):
        return "cancelled"
    if lifecycle.get("snoozed_until") and not _datetime_lte(lifecycle.get("snoozed_until"), now):
        return "snoozed"
    if lifecycle.get("reminder_eligible"):
        return "due"
    if lifecycle.get("next_reminder_at"):
        return "scheduled"
    if lifecycle.get("reminder_policy"):
        return "armed"
    return "none"


def _reminder_policy_int(
    policy: Optional[dict],
    key: str,
    default: Optional[int] = None,
    *,
    legacy_key: Optional[str] = None,
    minimum: int = 1,
    maximum: int = 10_000,
) -> Optional[int]:
    if not isinstance(policy, dict):
        return default
    value = policy.get(key)
    if value is None and legacy_key:
        value = policy.get(legacy_key)
    if value is None:
        return default
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _task_datetime_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _task_reminder_read_payload(lifecycle: dict, now: Optional[datetime] = None) -> dict[str, Any]:
    policy = lifecycle.get("reminder_policy") if isinstance(lifecycle.get("reminder_policy"), dict) else {}
    cadence_minutes = _reminder_policy_int(
        policy,
        "cadence_minutes",
        None,
        legacy_key="interval_minutes",
        minimum=1,
        maximum=7 * 24 * 60,
    )
    max_count = _reminder_policy_int(policy, "max_count", None, minimum=1, maximum=365)
    until_done = policy.get("until_done", True) if isinstance(policy, dict) else True
    last_fire_at = _task_datetime_iso(lifecycle.get("last_reminded_at"))
    fire_count = lifecycle.get("reminder_count", 0) or 0
    next_fire_at = _task_datetime_iso(lifecycle.get("next_reminder_at"))
    snoozed_until = _task_datetime_iso(lifecycle.get("snoozed_until"))
    reminder = {
        "state": _task_reminder_state(lifecycle, now=now),
        "cadence_minutes": cadence_minutes,
        "max_count": max_count,
        "until_done": bool(until_done),
        "next_fire_at": next_fire_at,
        "last_fire_at": last_fire_at,
        "fire_count": fire_count,
        "snoozed_until": snoozed_until,
        "eligible": lifecycle.get("reminder_eligible", False),
        "history": {
            "last_fire_at": last_fire_at,
            "fire_count": fire_count,
        },
        # Legacy nested fields kept for transitional clients; top-level legacy fields remain canonical.
        "policy": lifecycle.get("reminder_policy"),
        "next_reminder_at": next_fire_at,
        "last_reminded_at": last_fire_at,
        "reminder_count": fire_count,
    }
    return {key: value for key, value in reminder.items() if value is not None}


def _default_reminder_cadence_minutes(priority: Optional[str] = None) -> int:
    if (priority or "").lower() in {"high", "urgent", "critical"}:
        return 15
    return 60


def _task_reminder_update_data(
    reminder: "TaskReminderWrite",
    now: Optional[datetime] = None,
    *,
    priority: Optional[str] = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if reminder.action in {"cancel", "untrigger"}:
        return {
            "reminder_policy": None,
            "next_reminder_at": None,
            "snoozed_until": None,
        }
    if reminder.action == "snooze":
        snoozed_until = reminder.snoozed_until
        if snoozed_until is None:
            minutes = reminder.snooze_minutes if reminder.snooze_minutes is not None else 60
            snoozed_until = now + timedelta(minutes=max(1, min(int(minutes), 7 * 24 * 60)))
        return {
            "snoozed_until": snoozed_until,
            "queue_state": "snoozed",
        }

    cadence_minutes = reminder.cadence_minutes
    if cadence_minutes is None:
        cadence_minutes = reminder.interval_minutes
    if cadence_minutes is None:
        cadence_minutes = _default_reminder_cadence_minutes(priority)
    cadence_minutes = max(1, min(int(cadence_minutes), 7 * 24 * 60))

    next_fire_at = reminder.next_fire_at or (now + timedelta(minutes=cadence_minutes))
    policy: dict[str, Any] = {
        "cadence_minutes": cadence_minutes,
        "until_done": True if reminder.until_done is None else bool(reminder.until_done),
    }
    if reminder.max_count is not None or policy["until_done"] is False:
        max_count = reminder.max_count if reminder.max_count is not None else 24
        policy["max_count"] = max(1, min(int(max_count), 365))

    return {
        "reminder_policy": policy,
        "next_reminder_at": next_fire_at,
        "snoozed_until": None,
        # Re-arming a reminder must clear the durable snoozed queue state as
        # well as snoozed_until; otherwise the delivery worker keeps treating
        # the task as paused even after a new next_reminder_at is set.
        "queue_state": "active",
    }


def _task_work_pulse_fields(
    *,
    posted_by: Optional[dict],
    assigned_agent: Optional[dict],
    lifecycle: dict,
) -> dict[str, Any]:
    creator = _normalize_actor_payload(posted_by)
    assignee = _normalize_actor_payload(assigned_agent)
    owner = assignee or creator
    reminder = _task_reminder_read_payload(lifecycle)
    return {
        "creator": creator,
        "owner": owner,
        "assignee": assignee,
        "reminder": {key: value for key, value in reminder.items() if value is not None},
    }




def _task_terminal_lifecycle_repair_payload(task: Task, now: Optional[datetime] = None) -> dict[str, Any]:
    """Return a minimal repair for terminal tasks missing lifecycle side effects.

    Some older status mutations only persisted work_status/status. When a task is
    already terminal, responses should not continue to expose completed_at=null
    or active reminder schedules; this payload is safe to apply idempotently.
    """
    now = now or datetime.now(timezone.utc)
    work_status = getattr(task, "work_status", None)
    if work_status not in TERMINAL_TASK_STATUSES:
        return {}

    update_data: dict[str, Any] = {}
    if getattr(task, "queue_state", None) != "inactive":
        update_data["queue_state"] = "inactive"
    if getattr(task, "next_reminder_at", None) is not None:
        update_data["next_reminder_at"] = None
    if getattr(task, "snoozed_until", None) is not None:
        update_data["snoozed_until"] = None
    if work_status == "completed" and getattr(task, "completed_at", None) is None:
        update_data["completed_at"] = getattr(task, "updated_at", None) or now
    return update_data


async def _repair_terminal_task_lifecycle(db, task: Task) -> Task:
    repair_data = _task_terminal_lifecycle_repair_payload(task)
    if not repair_data:
        return task
    repair_data["updated_at"] = getattr(task, "updated_at", None) or datetime.now(timezone.utc)
    await db.execute(update(Task).where(Task.id == task.id).values(**repair_data))
    await db.commit()
    return await _reload_task_for_response(db, task.id)

def _task_status_persisted(task: Task, requested_status: str) -> bool:
    """Return whether a reloaded task reflects the requested status lifecycle."""

    mapped_status = TASK_STATUS_MAPPING.get(requested_status, requested_status)
    if getattr(task, "work_status", None) != mapped_status:
        return False
    if mapped_status == "completed" and getattr(task, "completed_at", None) is None:
        return False
    if mapped_status in TERMINAL_TASK_STATUSES:
        if getattr(task, "next_reminder_at", None) is not None:
            return False
        if getattr(task, "snoozed_until", None) is not None:
            return False
        if _effective_task_queue_state(task) != "inactive":
            return False
    return True


def _assert_task_status_persisted(task: Task, requested_status: str) -> None:
    """Prevent false-success responses when a status mutation did not persist."""

    if not _task_status_persisted(task, requested_status):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Task status update did not persist: "
                f"requested {requested_status!r}, current {getattr(task, 'work_status', None)!r}"
            ),
        )


def _task_status_update_payload(
    task: Task,
    requested_status: str,
    completed_at: Optional[datetime] = None,
    cancelled_reason: Optional[str] = None,
    stale_reason: Optional[str] = None,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    mapped_status = TASK_STATUS_MAPPING.get(requested_status, requested_status)
    if requested_status not in TASK_STATUS_MAPPING:
        supported = ", ".join(sorted(TASK_STATUS_MAPPING))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported task status {requested_status!r}. Supported values: {supported}",
        )

    update_data = {
        # work_status is canonical; keep the legacy status column in lock-step
        # so MCP/older consumers that still read Task.status do not report a
        # false-success update as open while work_status changed.
        "status": mapped_status,
        "work_status": mapped_status,
        "queue_state": _task_queue_state_for_status(mapped_status),
        "updated_at": now,
    }

    if mapped_status == "completed":
        update_data["completed_at"] = completed_at or now
    elif getattr(task, "work_status", None) == "completed":
        update_data["completed_at"] = None

    if mapped_status == "in_progress" and getattr(task, "claimed_at", None) is None:
        update_data["claimed_at"] = now

    if mapped_status in TERMINAL_TASK_STATUSES:
        update_data["next_reminder_at"] = None
        update_data["snoozed_until"] = None

    if cancelled_reason is not None:
        update_data["cancelled_reason"] = cancelled_reason

    if stale_reason is not None:
        update_data["stale_reason"] = stale_reason
        update_data["stale_at"] = getattr(task, "stale_at", None) or now

    normalized_reason = reason.strip() if isinstance(reason, str) else None
    if normalized_reason:
        metadata = dict(task.task_metadata) if isinstance(getattr(task, "task_metadata", None), dict) else {}
        if mapped_status == "blocked":
            metadata["blocked_reason"] = normalized_reason
            metadata["blocked_at"] = now.isoformat()
        elif mapped_status == "completed":
            metadata["completed_reason"] = normalized_reason
            metadata.setdefault("completion_notes", normalized_reason)
        metadata["last_status_reason"] = normalized_reason
        update_data["task_metadata"] = metadata

    return update_data


def _task_to_sse_payload(task, display_id: str, posted_by: dict = None, assigned_agent: dict = None) -> dict:
    """Convert task to SSE payload format for broadcasting"""
    task_meta = task.task_metadata if isinstance(task.task_metadata, dict) else {}
    lifecycle = _task_lifecycle_fields(task)
    next_reminder_at = getattr(task, "next_reminder_at", None)
    last_reminded_at = getattr(task, "last_reminded_at", None)
    snoozed_until = getattr(task, "snoozed_until", None)
    stale_at = getattr(task, "stale_at", None)
    return {
        "id": str(task.id),
        "space_id": str(task.space_id),
        "task_display_id": display_id,
        **_task_reference_fields(task),
        "title": task.title,
        "description": task.description,
        "requirements": task.requirements or {},
        "status": task.work_status,
        "priority": task.priority,
        "links": task.links if isinstance(task.links, list) else [],
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "queue_state": lifecycle["queue_state"],
        "queue_rank": getattr(task, "queue_rank", None),
        "reminder_policy": getattr(task, "reminder_policy", None),
        "next_reminder_at": next_reminder_at.isoformat() if next_reminder_at else None,
        "last_reminded_at": last_reminded_at.isoformat() if last_reminded_at else None,
        "reminder_count": lifecycle["reminder_count"],
        "snoozed_until": snoozed_until.isoformat() if snoozed_until else None,
        "stale_at": stale_at.isoformat() if stale_at else None,
        "stale_reason": getattr(task, "stale_reason", None),
        "cancelled_reason": getattr(task, "cancelled_reason", None),
        **_task_reason_fields(task),
        "reminder_eligible": lifecycle["reminder_eligible"],
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "ai_summary": task_meta.get("ai_summary"),
        "summary_model": task_meta.get("summary_model"),
        "summary_generated_at": task_meta.get("summary_generated_at"),
        "posted_by": posted_by,
        "assignee_type": _effective_assignee_type(task),
        "assignee_id": str(_effective_assignee_id(task)) if _effective_assignee_id(task) else None,
        "assigned_by_type": getattr(task, "assigned_by_type", None),
        "assigned_by_id": str(getattr(task, "assigned_by_id", None)) if getattr(task, "assigned_by_id", None) else None,
        "assigned_at": task.assigned_at.isoformat() if getattr(task, "assigned_at", None) else None,
        "assigned_agent_id": str(_legacy_assigned_agent_id(task)) if _legacy_assigned_agent_id(task) else None,
        "assigned_agent": assigned_agent,
        **_task_work_pulse_fields(
            posted_by=posted_by,
            assigned_agent=assigned_agent,
            lifecycle=lifecycle,
        ),
    }


def _task_summary_fields(task: Task) -> dict:
    metadata = task.task_metadata if isinstance(task.task_metadata, dict) else {}
    return {
        "ai_summary": metadata.get("ai_summary"),
        "summary_model": metadata.get("summary_model"),
        "summary_generated_at": metadata.get("summary_generated_at"),
    }


def _legacy_assigned_agent_id(task: Task):
    """Agent assignment UUID for legacy clients, with typed-assignee fallback."""
    assigned_agent_id = getattr(task, "assigned_agent_id", None)
    if assigned_agent_id:
        return assigned_agent_id
    if getattr(task, "assignee_type", None) == "agent":
        return getattr(task, "assignee_id", None)
    return None


def _effective_assignee_type(task: Task) -> Optional[str]:
    assignee_type = getattr(task, "assignee_type", None)
    if assignee_type:
        return assignee_type
    if getattr(task, "assigned_agent_id", None):
        return "agent"
    return None


def _effective_assignee_id(task: Task):
    assignee_id = getattr(task, "assignee_id", None)
    if assignee_id:
        return assignee_id
    if _effective_assignee_type(task) == "agent":
        return getattr(task, "assigned_agent_id", None)
    return None


def _agent_payload(agent: Optional[Agent]) -> Optional[dict]:
    if not agent:
        return None
    return {
        "id": str(agent.id),
        "name": agent.name,
        "username": f"@{agent.name}",
        "display_name": f"@{agent.name}",
        "agent_type": agent.agent_type,
        "type": "agent",
    }


def _user_payload(user: Optional[User]) -> Optional[dict]:
    if not user:
        return None
    return {
        "id": str(user.id),
        "username": user.username,
        "full_name": user.full_name,
        "type": "user",
    }


async def _task_assignment_fields(db, task: Task) -> dict:
    assignee_type = _effective_assignee_type(task)
    assignee_id = _effective_assignee_id(task)
    assigned_agent_id = _legacy_assigned_agent_id(task)
    assigned_agent = _agent_payload(getattr(task, "assigned_agent", None))
    assignee = None

    if assignee_type == "agent":
        assignee = assigned_agent
        if assignee is None and assignee_id:
            result = await db.execute(select(Agent).where(Agent.id == assignee_id))
            assignee = _agent_payload(result.scalar_one_or_none())
            assigned_agent = assignee
    elif assignee_type == "user" and assignee_id:
        result = await db.execute(select(User).where(User.id == assignee_id))
        assignee = _user_payload(result.scalar_one_or_none())

    return {
        "assignee_type": assignee_type,
        "assignee_id": str(assignee_id) if assignee_id else None,
        "assignee": assignee,
        "assigned_by_type": getattr(task, "assigned_by_type", None),
        "assigned_by_id": str(getattr(task, "assigned_by_id", None)) if getattr(task, "assigned_by_id", None) else None,
        "assigned_at": getattr(task, "assigned_at", None),
        "assigned_agent_id": str(assigned_agent_id) if assigned_agent_id else None,
        "assigned_agent": assigned_agent,
    }


def _task_assignment_response_kwargs(assignment_fields: dict) -> dict:
    # `assignee` is supplied by _task_work_pulse_fields so owner/reminder cards
    # continue to use the normalized work-pulse shape for either users or agents.
    return {key: value for key, value in assignment_fields.items() if key != "assignee"}


def _actor_assignment_attribution(session: SecureSession) -> dict:
    if getattr(session, "is_agent", False) and getattr(session, "agent_id", None):
        return {"assigned_by_type": "agent", "assigned_by_id": uuid.UUID(session.agent_id)}
    return {"assigned_by_type": "user", "assigned_by_id": session.user.id}


async def _validate_user_assignee(db, *, space_id: uuid.UUID, assignee_id: uuid.UUID) -> User:
    result = await db.execute(
        select(User)
        .join(SpaceMembership, SpaceMembership.user_id == User.id)
        .where(
            User.id == assignee_id,
            User.active.is_(True),
            SpaceMembership.space_id == space_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User assignee is not an active member of this space",
        )
    return user


async def _validate_agent_assignee(db, *, space_id: uuid.UUID, assignee_id: uuid.UUID) -> Agent:
    result = await db.execute(
        select(Agent)
        .outerjoin(
            AgentSpaceAccess,
            and_(
                AgentSpaceAccess.agent_id == Agent.id,
                AgentSpaceAccess.space_id == space_id,
                AgentSpaceAccess.state == "active",
            ),
        )
        .where(
            Agent.id == assignee_id,
            Agent.status == "active",
            or_(Agent.space_id == space_id, AgentSpaceAccess.id.isnot(None)),
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent assignee is not active in this space",
        )
    return agent


def _assignment_mutation_attempt(task_update: "TaskUpdate") -> dict[str, Any]:
    fields = task_update.model_fields_set
    attempted: dict[str, Any] = {}
    for field in ("assignee_type", "assignee_id", "assigned_agent_id"):
        if field in fields:
            attempted[field] = getattr(task_update, field)
    return attempted


def _has_assignment_mutation(task_update: "TaskUpdate") -> bool:
    return bool(task_update.model_fields_set.intersection({"assignee_type", "assignee_id", "assigned_agent_id"}))


async def _ensure_tasks_typed_assignee_ready(db) -> None:
    readiness = await check_tasks_typed_assignee_schema(db)
    if not readiness.ready:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=readiness.detail)


async def _task_assignment_update_data(
    db,
    *,
    task: Task,
    task_update: "TaskUpdate",
    session: SecureSession,
) -> Optional[dict]:
    fields = task_update.model_fields_set
    has_typed = "assignee_type" in fields or "assignee_id" in fields
    has_legacy = "assigned_agent_id" in fields
    if not has_typed and not has_legacy:
        return None

    update_data = {}

    def clear_assignment():
        update_data.update({
            "assignee_type": None,
            "assignee_id": None,
            "assigned_agent_id": None,
            "assigned_by_type": None,
            "assigned_by_id": None,
        })
        update_data.setdefault("queue_state", "queued")
        if task.work_status in ["assigned", "in_progress"]:
            update_data["work_status"] = "not_started"

    if has_legacy and task_update.assigned_agent_id in (None, "") and not has_typed:
        clear_assignment()
        return update_data

    assignee_type = task_update.assignee_type if "assignee_type" in fields else None
    assignee_id_raw = task_update.assignee_id if "assignee_id" in fields else None

    if has_legacy and task_update.assigned_agent_id not in (None, ""):
        if has_typed:
            if assignee_type != "agent" or str(assignee_id_raw) != str(task_update.assigned_agent_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="assigned_agent_id must match assignee_type='agent' and assignee_id when both are provided",
                )
        assignee_type = "agent"
        assignee_id_raw = task_update.assigned_agent_id

    if has_typed and (assignee_type is None or assignee_id_raw in (None, "")):
        clear_assignment()
        return update_data

    if assignee_type not in ASSIGNEE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="assignee_type must be one of: user, agent",
        )

    try:
        assignee_uuid = uuid.UUID(str(assignee_id_raw))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid assignee_id format",
        ) from exc

    if assignee_type == "user":
        await _validate_user_assignee(db, space_id=task.space_id, assignee_id=assignee_uuid)
        assigned_agent_id = None
    else:
        await _validate_agent_assignee(db, space_id=task.space_id, assignee_id=assignee_uuid)
        assigned_agent_id = assignee_uuid

    update_data.update({
        "assignee_type": assignee_type,
        "assignee_id": assignee_uuid,
        "assigned_agent_id": assigned_agent_id,
        **_actor_assignment_attribution(session),
    })
    update_data.setdefault("assigned_at", task.assigned_at or datetime.now(timezone.utc))
    if task.work_status in ["not_started", "open"]:
        update_data["work_status"] = "in_progress"
        update_data.setdefault("queue_state", "active")
    # Auto-arm the reminder loop on assignment to an agent: the delivery worker only
    # picks up tasks where next_reminder_at IS NOT NULL, so an assigned task that is
    # never explicitly scheduled would never remind. If no reminder is already armed
    # (and the caller did not explicitly arm/cancel one), default a policy so the
    # assignee gets activity-stream reminders until the task is closed.
    explicit_reminder_mutation = bool(
        fields.intersection({"reminder", "reminder_policy", "next_reminder_at"})
    )
    effective_queue_state = (
        task_update.queue_state
        if "queue_state" in fields and task_update.queue_state is not None
        else update_data.get("queue_state", getattr(task, "queue_state", None))
    )
    effective_priority = (
        task_update.priority
        if "priority" in fields and task_update.priority is not None
        else getattr(task, "priority", None)
    )
    if (
        assignee_type == "agent"
        and effective_queue_state == "active"
        and getattr(task, "next_reminder_at", None) is None
        and getattr(task, "reminder_policy", None) is None
        and not explicit_reminder_mutation
    ):
        update_data.update(
            _task_reminder_update_data(
                TaskReminderWrite(action="schedule", until_done=True),
                priority=effective_priority,
            )
        )
    return update_data


def _task_reminder_create_data(task_data: "TaskCreate") -> dict[str, Any]:
    if (
        "reminder" in task_data.model_fields_set
        and task_data.reminder is not None
        and task_data.reminder.action == "snooze"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot snooze a task reminder during task creation",
        )
    return (
        _task_reminder_update_data(task_data.reminder, priority=task_data.priority)
        if "reminder" in task_data.model_fields_set and task_data.reminder is not None
        else {
            "reminder_policy": task_data.reminder_policy,
            "next_reminder_at": task_data.next_reminder_at,
        }
    )


# Pydantic models for API
class TaskReminderWrite(BaseModel):
    # Frontend quick controls call this trigger/untrigger; schedule/cancel remain
    # supported for the lower-level API contract. Snooze is the reminder-control
    # contract action that temporarily pauses an armed reminder loop.
    action: Literal["schedule", "cancel", "trigger", "untrigger", "snooze"]
    cadence_minutes: Optional[int] = Field(None, ge=1, le=7 * 24 * 60)
    # Legacy alias accepted during migration from the original worker policy key.
    interval_minutes: Optional[int] = Field(None, ge=1, le=7 * 24 * 60)
    max_count: Optional[int] = Field(None, ge=1, le=365)
    until_done: Optional[bool] = True
    next_fire_at: Optional[datetime] = None
    snooze_minutes: Optional[int] = Field(None, ge=1, le=7 * 24 * 60)
    snoozed_until: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate_schedule_payload(self):
        if self.action in {"cancel", "untrigger", "snooze"}:
            return self
        if self.cadence_minutes is None and self.interval_minutes is None and self.next_fire_at is None:
            # Allow schedule with no explicit timing; the API will default to one hour from now.
            return self
        return self

class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None)
    requirements: Optional[dict] = None
    space_id: Optional[str] = Field(
        None,
        description="Override space context (verified against membership/access)",
    )
    priority: str = Field("medium", pattern="^(low|medium|high|urgent|critical)$")
    deadline: Optional[datetime] = Field(None, validation_alias=AliasChoices("deadline", "due_date"))
    queue_rank: Optional[float] = Field(None, ge=0)
    reminder_policy: Optional[dict] = None
    next_reminder_at: Optional[datetime] = None
    reminder: Optional[TaskReminderWrite] = None
    assignee_type: Optional[Literal["user", "agent"]] = None
    assignee_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None

class TaskUpdate(BaseModel):
    space_id: Optional[str] = Field(
        None,
        description="Override space context (verified against membership/access)",
    )
    status: Optional[str] = Field(None, pattern=TASK_WORK_STATUS_PATTERN)
    completed_at: Optional[datetime] = None
    reason: Optional[str] = Field(None, max_length=500)
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    requirements: Optional[dict] = None
    priority: Optional[str] = Field(None, pattern="^(low|medium|high|urgent|critical)$")
    deadline: Optional[datetime] = Field(None, validation_alias=AliasChoices("deadline", "due_date"))
    assignee_type: Optional[Literal["user", "agent"]] = None
    assignee_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    queue_state: Optional[str] = Field(None, pattern=TASK_QUEUE_STATE_PATTERN)
    queue_rank: Optional[float] = Field(None, ge=0)
    reminder_policy: Optional[dict] = None
    next_reminder_at: Optional[datetime] = None
    snoozed_until: Optional[datetime] = None
    stale_at: Optional[datetime] = None
    stale_reason: Optional[str] = Field(None, max_length=120)
    cancelled_reason: Optional[str] = Field(None, max_length=120)
    reminder: Optional[TaskReminderWrite] = None

class TaskStatusUpdate(BaseModel):
    space_id: Optional[str] = Field(
        None,
        description="Override space context (verified against membership/access)",
    )
    status: str = Field(..., pattern=TASK_WORK_STATUS_PATTERN)
    completed_at: Optional[datetime] = None
    reason: Optional[str] = Field(None, max_length=500)
    cancelled_reason: Optional[str] = Field(None, max_length=120)
    stale_reason: Optional[str] = Field(None, max_length=120)

class TaskResponse(BaseModel):
    id: str
    space_id: str
    task_display_id: str  # Friendly sequential ID like 'task_000012'
    task_ref: str
    task_deep_link: str
    task_reference: dict[str, Any]
    title: str
    description: Optional[str]
    requirements: Optional[dict]
    status: Optional[str] = "open"  # Nullable-safe: defaults to 'open' if work_status is NULL
    priority: str
    links: Optional[List[str]] = None  # Artifact links (PRs, docs, deployments)
    deadline: Optional[datetime]
    queue_state: str = "queued"
    queue_rank: Optional[float] = None
    reminder_policy: Optional[dict] = None
    next_reminder_at: Optional[datetime] = None
    last_reminded_at: Optional[datetime] = None
    reminder_count: int = 0
    snoozed_until: Optional[datetime] = None
    stale_at: Optional[datetime] = None
    stale_reason: Optional[str] = None
    cancelled_reason: Optional[str] = None
    blocked_reason: Optional[str] = None
    completed_reason: Optional[str] = None
    reminder_eligible: bool = False
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    ai_summary: Optional[str] = None
    summary_model: Optional[str] = None
    summary_generated_at: Optional[datetime] = None
    posted_by: Optional[dict] = None  # User info, can be None for system/anonymous tasks
    assignee_type: Optional[str] = None
    assignee_id: Optional[str] = None
    assigned_by_type: Optional[str] = None
    assigned_by_id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    assigned_agent_id: Optional[str] = None
    assigned_agent: Optional[dict] = None  # Agent info if assigned
    creator: Optional[dict] = None
    owner: Optional[dict] = None
    assignee: Optional[dict] = None
    reminder: Optional[dict] = None
    notes_count: int = 0  # Badge count for task card UI

class TaskListResponse(BaseModel):
    tasks: List[TaskResponse]
    total: int
    limit: int
    offset: int
    filters: dict

# Task Notes Pydantic models
class TaskNoteCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    note_type: str = Field("general", pattern="^(general|progress|issue|solution)$")
    visibility: str = Field("public", pattern="^(public|private|team)$")

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_note_payload(cls, values):
        """Backward compatibility for older FE payloads using `note` + `visibility=org`."""
        if not isinstance(values, dict):
            return values

        if (not values.get("content")) and values.get("note"):
            values["content"] = values.get("note")

        # Legacy frontend sometimes sends org visibility for team/public scope.
        if values.get("visibility") == "org":
            values["visibility"] = "team"

        return values

    @field_validator("visibility", mode="before")
    @classmethod
    def _normalize_visibility(cls, value):
        if value is None:
            return value
        v = str(value).strip().lower()
        if v == "org":
            return "team"
        return v

class TaskNoteUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)

class TaskNoteResponse(BaseModel):
    id: str
    task_id: str
    content: str         # API surface name; maps to DB column 'note'
    note_type: str
    visibility: str
    author_id: str
    author_type: str     # 'user' or 'agent'
    author_name: str
    created_at: datetime
    updated_at: datetime

class TaskNotesListResponse(BaseModel):
    notes: List[TaskNoteResponse]
    total: int


@router.get("/v1/tasks", response_model=TaskListResponse)
@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    session: SecureSession = Depends(get_secure_session),
    limit: int = Query(50, le=500, description="Number of tasks to return"),
    offset: int = Query(0, ge=0, description="Number of tasks to skip"),
    task_status: Optional[str] = Query(None, pattern=TASK_WORK_STATUS_PATTERN),
    task_filter: Optional[str] = Query(
        None,
        alias="filter",
        pattern="^(my_tasks|available|assigned|all|completed)$",
        description="Named task filter for board views",
    ),
    priority: Optional[str] = Query(None, pattern="^(low|medium|high|urgent|critical)$"),
    assigned_agent_id: Optional[str] = Query(None, description="Filter by assigned agent ID"),
    queue_state: Annotated[Optional[str], Query(pattern=TASK_QUEUE_STATE_PATTERN)] = None,
    include_stale: Annotated[bool, Query(description="Include tasks hidden by stale lifecycle cleanup")] = False,
    reminder_ready: Annotated[Optional[bool], Query(description="Filter tasks that are ready for reminder delivery")] = None,
    posted_by_me: bool = Query(False, description="Show only tasks posted by current user"),
    space_id: Optional[str] = Query(None, description="Override space context (verified against membership)"),
):
    """List tasks with filtering and pagination"""
    try:
        # Resolve effective space: explicit parameter (verified) or session default
        effective_space_id = session.space_id
        if space_id:
            try:
                effective_space_id = str(uuid.UUID(space_id))
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid space_id format",
                ) from exc
            await verify_space_actor_access(
                session.db,
                user_id=session.user.id,
                space_id=effective_space_id,
                is_agent=session.is_agent,
                agent_id=session.agent_id,
            )

        # Build base query with space isolation
        query = select(Task).options(
            selectinload(Task.posted_by_user),
            selectinload(Task.posted_by_agent),
            selectinload(Task.assigned_agent)
        ).where(Task.space_id == uuid.UUID(effective_space_id))

        # Apply filters
        filters_applied = {}

        if not include_stale:
            query = query.where(Task.stale_at.is_(None))
        else:
            filters_applied["include_stale"] = True

        if task_status:
            mapped_status = TASK_STATUS_MAPPING.get(task_status, task_status)
            query = query.where(Task.work_status == mapped_status)
            filters_applied["status"] = mapped_status

        # Named board filters used by frontend/MCP surfaces
        if task_filter and task_filter != "all":
            if task_filter == "available":
                query = query.where(
                    Task.assigned_agent_id.is_(None),
                    Task.work_status.notin_(["completed", "cancelled"]),
                )
            elif task_filter == "assigned":
                query = query.where(
                    Task.assigned_agent_id.isnot(None),
                    Task.work_status.notin_(["completed", "cancelled"]),
                )
            elif task_filter == "my_tasks":
                # Tasks assigned to agents owned by current user
                query = query.where(
                    Task.assigned_agent_id.isnot(None),
                    Task.assigned_agent.has(Agent.user_id == session.user.id),
                    Task.work_status.notin_(["completed", "cancelled"]),
                )
            elif task_filter == "completed":
                query = query.where(Task.work_status == "completed")

            filters_applied["filter"] = task_filter

        if priority:
            query = query.where(Task.priority == priority)
            filters_applied["priority"] = priority

        if queue_state:
            query = query.where(Task.queue_state == queue_state)
            filters_applied["queue_state"] = queue_state

        if reminder_ready is not None:
            now = datetime.now(timezone.utc)
            reminder_ready_clause = and_(
                Task.assigned_agent_id.isnot(None),
                Task.work_status == "in_progress",
                Task.queue_state == "active",
                Task.stale_at.is_(None),
                Task.next_reminder_at.isnot(None),
                Task.next_reminder_at <= now,
                or_(Task.snoozed_until.is_(None), Task.snoozed_until <= now),
            )
            if reminder_ready:
                query = query.where(reminder_ready_clause)
            else:
                query = query.where(
                    or_(
                        Task.assigned_agent_id.is_(None),
                        Task.work_status.is_(None),
                        Task.work_status != "in_progress",
                        Task.queue_state.is_(None),
                        Task.queue_state != "active",
                        Task.stale_at.isnot(None),
                        Task.next_reminder_at.is_(None),
                        Task.next_reminder_at > now,
                        and_(Task.snoozed_until.isnot(None), Task.snoozed_until > now),
                    )
                )
            filters_applied["reminder_ready"] = reminder_ready

        if assigned_agent_id:
            try:
                agent_uuid = uuid.UUID(assigned_agent_id)
                query = query.where(Task.assigned_agent_id == agent_uuid)
                filters_applied["assigned_agent_id"] = assigned_agent_id
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid agent ID format"
                )

        if posted_by_me:
            query = query.where(Task.posted_by == session.user.id)
            filters_applied["posted_by_me"] = True

        # Get total count for pagination
        count_query = query.with_only_columns(Task.id)
        count_result = await session.db.execute(count_query)
        total = len(count_result.scalars().all())

        # Apply pagination and ordering
        if reminder_ready:
            query = query.order_by(Task.next_reminder_at.asc(), Task.created_at.asc())
        else:
            query = query.order_by(Task.created_at.desc())
        query = query.offset(offset).limit(limit)
        result = await session.db.execute(query)
        tasks = result.scalars().all()

        # Bulk fetch note counts to avoid N+1 (one query for all tasks)
        task_ids = [task.id for task in tasks]
        notes_count_map: dict = {}
        if task_ids:
            from sqlalchemy import func as sqlfunc
            counts_result = await session.db.execute(
                select(TaskNote.task_id, sqlfunc.count(TaskNote.id).label("cnt"))
                .where(TaskNote.task_id.in_(task_ids))
                .group_by(TaskNote.task_id)
            )
            notes_count_map = {row.task_id: row.cnt for row in counts_result}

        # Convert to response format
        task_list = []
        for task in tasks:
            # Generate short UUID display ID (like message IDs)
            display_id = _task_display_id(task)

            # Determine creator: agent if posted_by_agent exists, otherwise user, otherwise unknown
            creator_info = None
            if task.posted_by_agent:
                creator_info = {
                    "id": str(task.posted_by_agent.id),
                    "username": f"@{task.posted_by_agent.name}",  # Show as @CodeWeaver
                    "full_name": f"Agent: {task.posted_by_agent.name}",
                    "type": "agent"
                }
            elif task.posted_by_user:
                creator_info = {
                    "id": str(task.posted_by_user.id),
                    "username": task.posted_by_user.username,
                    "full_name": task.posted_by_user.full_name,
                    "type": "user"
                }
            else:
                # Handle case where both posted_by fields are NULL
                creator_info = {
                    "id": "unknown",
                    "username": "Unknown",
                    "full_name": "Unknown Creator",
                    "type": "system"
                }
            lifecycle = _task_lifecycle_fields(task)
            assignment_fields = await _task_assignment_fields(session.db, task)

            task_response = TaskResponse(
                id=str(task.id),
                space_id=str(task.space_id),
                task_display_id=display_id,
                **_task_reference_fields(task),
                title=task.title,
                description=task.description,
                requirements=task.requirements or {},
                status=task.work_status,  # Use work_status instead of legacy status field
                priority=task.priority,
                links=task.links if isinstance(task.links, list) else [],
                deadline=task.deadline,
                **lifecycle,
                **_task_reason_fields(task),
                created_at=task.created_at,
                updated_at=task.updated_at,
                completed_at=task.completed_at,
                **_task_summary_fields(task),
                posted_by=creator_info,
                **_task_assignment_response_kwargs(assignment_fields),
                **_task_work_pulse_fields(
                    posted_by=creator_info,
                    assigned_agent=assignment_fields.get("assignee") or assignment_fields.get("assigned_agent"),
                    lifecycle=lifecycle,
                ),
                notes_count=notes_count_map.get(task.id, 0)
            )
            task_list.append(task_response)

        return TaskListResponse(
            tasks=task_list,
            total=total,
            limit=limit,
            offset=offset,
            filters=filters_applied
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Failed to fetch tasks: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch tasks: {str(e)}"
        )


@router.get("/v1/tasks/list", response_model=TaskListResponse)
@router.get("/tasks/list", response_model=TaskListResponse)
async def list_tasks_alternative(
    session: SecureSession = Depends(get_secure_session),
    limit: int = Query(500, le=500, description="Number of tasks to return"),
    task_filter: Optional[str] = Query(
        None,
        alias="filter",
        pattern="^(my_tasks|available|assigned|all|completed)$",
    ),
):
    """Alternative endpoint for task listing (used by some frontend components)"""
    # Delegate to main list_tasks endpoint with default parameters
    return await list_tasks(
        session=session,
        limit=limit,
        offset=0,
        task_status=None,
        task_filter=task_filter,
        priority=None,
        assigned_agent_id=None,
        queue_state=None,
        include_stale=False,
        reminder_ready=None,
        posted_by_me=False,
        space_id=None,
    )


@router.post("/v1/tasks", response_model=TaskResponse)
@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    task_data: TaskCreate,
    session: SecureSession = Depends(get_secure_session),
):
    """Create a new task"""
    try:
        effective_space_id = session.space_id
        if task_data.space_id:
            try:
                effective_space_id = str(uuid.UUID(task_data.space_id))
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid space_id format",
                ) from exc
            await verify_space_actor_access(
                session.db,
                user_id=session.user.id,
                space_id=effective_space_id,
                is_agent=session.is_agent,
                agent_id=session.agent_id,
            )

        is_agent = bool(session.is_agent and session.agent_id)
        posted_by_user_id = None if is_agent else session.user.id
        posted_by_agent_id = uuid.UUID(session.agent_id) if is_agent else None

        reminder_create_data = _task_reminder_create_data(task_data)

        # Create new task in current org context
        summary_payload = await summarize_task_leaf(
            title=task_data.title,
            description=task_data.description,
            requirements=task_data.requirements or {},
        )
        # Generate the sequential display ID only after potentially slow summary
        # generation, so the advisory lock inside get_next_task_number is held
        # only across final task construction and commit.
        result = await session.db.execute(
            text("SELECT get_next_task_number(CAST(:space_id AS uuid))"),
            {"space_id": effective_space_id},
        )
        task_number = result.scalar()

        new_task = Task(
            id=uuid.uuid4(),
            space_id=uuid.UUID(effective_space_id),
            posted_by=posted_by_user_id,
            posted_by_agent_id=posted_by_agent_id,
            task_number=task_number,
            title=task_data.title,
            description=task_data.description,
            requirements=task_data.requirements or {},
            work_status="not_started",  # Fixed: use work_status and proper initial status
            priority=task_data.priority,
            deadline=task_data.deadline,
            queue_state="queued",
            queue_rank=task_data.queue_rank,
            reminder_policy=reminder_create_data["reminder_policy"],
            next_reminder_at=reminder_create_data["next_reminder_at"],
            task_metadata=summary_payload,
        )

        assignment_fields = {"assignee_type", "assignee_id", "assigned_agent_id"}
        provided_assignment_fields = task_data.model_fields_set.intersection(assignment_fields)
        if provided_assignment_fields:
            await _ensure_tasks_typed_assignee_ready(session.db)
            reminder_fields = task_data.model_fields_set.intersection(
                {"reminder", "reminder_policy", "next_reminder_at", "priority"}
            )
            assignment_update = TaskUpdate.model_construct(
                assignee_type=task_data.assignee_type,
                assignee_id=task_data.assignee_id,
                assigned_agent_id=task_data.assigned_agent_id,
                reminder=task_data.reminder,
                reminder_policy=task_data.reminder_policy,
                next_reminder_at=task_data.next_reminder_at,
                priority=task_data.priority,
                _fields_set=provided_assignment_fields | reminder_fields,
            )
            assignment_data = await _task_assignment_update_data(
                session.db,
                task=new_task,
                task_update=assignment_update,
                session=session,
            )
            if assignment_data:
                for key, value in assignment_data.items():
                    setattr(new_task, key, value)

        session.db.add(new_task)
        await session.db.commit()
        await session.db.refresh(new_task)

        # Load relationships for response
        refresh_attrs = ["posted_by_user"]
        if posted_by_agent_id:
            refresh_attrs.append("posted_by_agent")
        if new_task.assigned_agent_id:
            refresh_attrs.append("assigned_agent")
        await session.db.refresh(new_task, refresh_attrs)

        # Generate short UUID display ID (like message IDs)
        display_id = _task_display_id(new_task)

        # Broadcast task_created SSE event for real-time frontend updates
        posted_by_data = (
            {
                "id": session.agent_id,
                "username": session.agent_name or "Agent",
                "full_name": session.agent_name or "Agent",
                "type": "agent",
            }
            if is_agent
            else {
                "id": str(session.user.id),
                "username": session.user.username,
                "full_name": session.user.full_name,
                "type": "user",
            }
        )
        await redis_sse_broker.publish(
            space_id=effective_space_id,
            event="task_created",
            data=_task_to_sse_payload(new_task, display_id, posted_by=posted_by_data)
        )

        lifecycle = _task_lifecycle_fields(new_task)
        assignment_fields = await _task_assignment_fields(session.db, new_task)
        return TaskResponse(
            id=str(new_task.id),
            space_id=str(new_task.space_id),
            task_display_id=display_id,
            **_task_reference_fields(new_task),
            title=new_task.title,
            description=new_task.description,
            requirements=new_task.requirements or {},
            status=new_task.work_status,  # Fixed: use work_status consistently
            priority=new_task.priority,
            links=new_task.links if isinstance(new_task.links, list) else [],
            deadline=new_task.deadline,
            **lifecycle,
            **_task_reason_fields(new_task),
            created_at=new_task.created_at,
            updated_at=new_task.updated_at,
            completed_at=new_task.completed_at,
            **_task_summary_fields(new_task),
            posted_by=posted_by_data,
            **_task_assignment_response_kwargs(assignment_fields),
            **_task_work_pulse_fields(
                posted_by=posted_by_data,
                assigned_agent=assignment_fields.get("assignee") or assignment_fields.get("assigned_agent"),
                lifecycle=lifecycle,
            ),
        )

    except HTTPException:
        await session.db.rollback()
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create task: {str(e)}"
        )


@router.get("/v1/tasks/my-assignment")
@router.get("/tasks/my-assignment")
async def get_my_current_assignment(
    session: SecureSession = Depends(get_secure_session),
):
    return await _get_my_current_assignment_impl(session)


@router.get("/v1/tasks/{task_id}", response_model=TaskResponse)
@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    session: SecureSession = Depends(get_secure_session),
    space_id: Optional[str] = Query(None, description="Override space context (verified against membership)"),
):
    """Get a specific task by ID"""
    try:
        effective_space_id = await _resolve_effective_task_space_id(session, space_id)
        task = await _resolve_task_for_space(
            session.db,
            task_id,
            effective_space_id,
            options=(
                selectinload(Task.posted_by_user),
                selectinload(Task.posted_by_agent),
                selectinload(Task.assigned_agent),
            ),
        )

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )

        task = await _repair_terminal_task_lifecycle(session.db, task)

        # Generate display ID
        display_id = _task_display_id(task)

        posted_by_data = {
            "id": str(task.posted_by_agent.id),
            "username": f"@{task.posted_by_agent.name}",
            "full_name": f"Agent: {task.posted_by_agent.name}",
            "type": "agent"
        } if task.posted_by_agent else ({
            "id": str(task.posted_by_user.id),
            "username": task.posted_by_user.username,
            "full_name": task.posted_by_user.full_name,
            "type": "user"
        } if task.posted_by_user else {
            "id": "unknown",
            "username": "unknown",
            "full_name": "Unknown Creator",
            "type": "unknown"
        })
        lifecycle = _task_lifecycle_fields(task)
        assignment_fields = await _task_assignment_fields(session.db, task)

        return TaskResponse(
            id=str(task.id),
            space_id=str(task.space_id),
            task_display_id=display_id,
            **_task_reference_fields(task),
            title=task.title,
            description=task.description,
            requirements=task.requirements or {},
            status=task.work_status,  # Fixed: use work_status consistently
            priority=task.priority,
            links=task.links if isinstance(task.links, list) else [],
            deadline=task.deadline,
            **lifecycle,
            **_task_reason_fields(task),
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
            **_task_summary_fields(task),
            posted_by=posted_by_data,
            **_task_assignment_response_kwargs(assignment_fields),
            **_task_work_pulse_fields(
                posted_by=posted_by_data,
                assigned_agent=assignment_fields.get("assignee") or assignment_fields.get("assigned_agent"),
                lifecycle=lifecycle,
            ),
        )

    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID format: {task_id} - {str(ve)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch task: {str(e)}"
        )


@router.put("/v1/tasks/{task_id}", response_model=TaskResponse)
@router.put("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    task_update: TaskUpdate,
    session: SecureSession = Depends(get_secure_session),
):
    """Update a task"""
    mutation_actor = _task_actor_payload(session)
    assignment_mutation = _has_assignment_mutation(task_update)
    assignment_attempt = _assignment_mutation_attempt(task_update) if assignment_mutation else None
    effective_space_id = session.space_id
    try:
        effective_space_id = await _resolve_effective_task_space_id(session, task_update.space_id)
        if assignment_mutation:
            await _ensure_tasks_typed_assignee_ready(session.db)

        # Verify task exists and belongs to current user's org
        task = await _resolve_task_for_space(
            session.db,
            task_id,
            effective_space_id,
            options=(
                selectinload(Task.posted_by_user),
                selectinload(Task.posted_by_agent),
                selectinload(Task.assigned_agent),
            ),
        )

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )

        # Build update dictionary
        update_data = {}
        if task_update.title is not None:
            update_data["title"] = task_update.title
        if task_update.description is not None:
            update_data["description"] = task_update.description
        if task_update.requirements is not None:
            update_data["requirements"] = task_update.requirements
        if task_update.priority is not None:
            update_data["priority"] = task_update.priority
        if "deadline" in task_update.model_fields_set:
            update_data["deadline"] = task_update.deadline
        if task_update.queue_state is not None:
            update_data["queue_state"] = task_update.queue_state
        if "queue_rank" in task_update.model_fields_set:
            update_data["queue_rank"] = task_update.queue_rank
        if "reminder_policy" in task_update.model_fields_set:
            update_data["reminder_policy"] = task_update.reminder_policy
        if "next_reminder_at" in task_update.model_fields_set:
            update_data["next_reminder_at"] = task_update.next_reminder_at
        if "reminder" in task_update.model_fields_set and task_update.reminder is not None:
            reminder_priority = update_data.get("priority", task.priority)
            update_data.update(_task_reminder_update_data(task_update.reminder, priority=reminder_priority))
        if "snoozed_until" in task_update.model_fields_set:
            update_data["snoozed_until"] = task_update.snoozed_until
        if "stale_at" in task_update.model_fields_set:
            update_data["stale_at"] = task_update.stale_at
        if "stale_reason" in task_update.model_fields_set:
            update_data["stale_reason"] = task_update.stale_reason
        if "cancelled_reason" in task_update.model_fields_set:
            update_data["cancelled_reason"] = task_update.cancelled_reason
        assignment_update_data = await _task_assignment_update_data(
            session.db,
            task=task,
            task_update=task_update,
            session=session,
        )
        if assignment_update_data:
            update_data.update(assignment_update_data)

        requested_status = task_update.status if "status" in task_update.model_fields_set else None
        status_mutation = requested_status is not None
        if requested_status is not None:
            update_data.update(
                _task_status_update_payload(
                    task=task,
                    requested_status=requested_status,
                    completed_at=task_update.completed_at,
                    cancelled_reason=task_update.cancelled_reason,
                    stale_reason=task_update.stale_reason,
                    reason=task_update.reason,
                )
            )

        if any(field in update_data for field in ("title", "description", "requirements")):
            current_metadata = dict(task.task_metadata or {})
            summary_payload = await summarize_task_leaf(
                title=update_data.get("title", task.title),
                description=update_data.get("description", task.description),
                requirements=update_data.get("requirements", task.requirements or {}),
            )
            if summary_payload:
                current_metadata.update(summary_payload)
                update_data["task_metadata"] = current_metadata

        if update_data:
            update_data["updated_at"] = datetime.now(timezone.utc)
            await session.db.execute(
                update(Task)
                .where(Task.id == task.id)
                .values(**update_data)
            )
            task_id_value = task.id
            if status_mutation and update_data.get("work_status") == "completed":
                task = await _reload_task_for_response(session.db, task_id_value)
                await notify_task_completed(session.db, task)
            elif assignment_mutation:
                from ...services.task_reminder_delivery import notify_task_assignment

                task = await _reload_task_for_response(session.db, task_id_value)
                await notify_task_assignment(session.db, task)
            elif status_mutation and update_data.get("work_status") == "blocked":
                from ...services.task_reminder_delivery import notify_task_blocked

                task = await _reload_task_for_response(session.db, task_id_value)
                await notify_task_blocked(session.db, task)
            await session.db.commit()
            task = await _reload_task_for_response(session.db, task_id_value)
            if requested_status is not None:
                _assert_task_status_persisted(task, requested_status)

        # Generate display ID
        display_id = _task_display_id(task)

        # Build response data for both SSE and API response
        posted_by_data = {
            "id": str(task.posted_by_agent.id),
            "username": f"@{task.posted_by_agent.name}",
            "full_name": f"Agent: {task.posted_by_agent.name}",
            "type": "agent"
        } if task.posted_by_agent else ({
            "id": str(task.posted_by_user.id),
            "username": task.posted_by_user.username,
            "full_name": task.posted_by_user.full_name,
            "type": "user"
        } if task.posted_by_user else {
            "id": "unknown",
            "username": "unknown",
            "full_name": "Unknown Creator",
            "type": "unknown"
        })
        assigned_agent_data = {
            "id": str(task.assigned_agent.id),
            "name": task.assigned_agent.name,
            "agent_type": task.assigned_agent.agent_type
        } if task.assigned_agent else None

        # Broadcast task_updated SSE event for real-time frontend updates
        await redis_sse_broker.publish(
            space_id=str(task.space_id),
            event="task_updated",
            data=_task_to_sse_payload(task, display_id, posted_by=posted_by_data, assigned_agent=assigned_agent_data)
        )
        if assignment_mutation:
            await _publish_task_mutation_activity(
                space_id=str(task.space_id),
                task_id=task_id,
                mutation="assignment",
                outcome="succeeded",
                actor=mutation_actor,
                attempted=assignment_attempt,
                task=task,
            )

        lifecycle = _task_lifecycle_fields(task)
        assignment_fields = await _task_assignment_fields(session.db, task)
        return TaskResponse(
            id=str(task.id),
            space_id=str(task.space_id),
            task_display_id=display_id,
            **_task_reference_fields(task),
            title=task.title,
            description=task.description,
            requirements=task.requirements or {},
            status=task.work_status,
            priority=task.priority,
            links=task.links if isinstance(task.links, list) else [],
            deadline=task.deadline,
            **lifecycle,
            **_task_reason_fields(task),
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
            **_task_summary_fields(task),
            posted_by=posted_by_data,
            **_task_assignment_response_kwargs(assignment_fields),
            **_task_work_pulse_fields(
                posted_by=posted_by_data,
                assigned_agent=assignment_fields.get("assignee") or assignment_fields.get("assigned_agent"),
                lifecycle=lifecycle,
            ),
        )

    except HTTPException as exc:
        await session.db.rollback()
        if assignment_mutation:
            await _publish_task_mutation_activity(
                space_id=effective_space_id,
                task_id=task_id,
                mutation="assignment",
                outcome="failed",
                actor=mutation_actor,
                attempted=assignment_attempt,
                error=str(exc.detail),
            )
        raise
    except ValueError as ve:
        await session.db.rollback()
        error_detail = f"Invalid task ID format: {task_id} - {str(ve)}"
        if assignment_mutation:
            await _publish_task_mutation_activity(
                space_id=effective_space_id,
                task_id=task_id,
                mutation="assignment",
                outcome="failed",
                actor=mutation_actor,
                attempted=assignment_attempt,
                error=error_detail,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail
        )
    except Exception as e:
        await session.db.rollback()
        error_detail = f"Failed to update task: {str(e)}"
        if assignment_mutation:
            await _publish_task_mutation_activity(
                space_id=effective_space_id,
                task_id=task_id,
                mutation="assignment",
                outcome="failed",
                actor=mutation_actor,
                attempted=assignment_attempt,
                error=error_detail,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail
        )


@router.patch("/v1/tasks/{task_id}", response_model=TaskResponse)
@router.patch("/tasks/{task_id}", response_model=TaskResponse)
@router.put("/v1/tasks/{task_id}/status", response_model=TaskResponse)
@router.put("/tasks/{task_id}/status", response_model=TaskResponse)
async def update_task_status(
    task_id: str,
    status_update: TaskStatusUpdate,
    session: SecureSession = Depends(get_secure_session),
):
    """Update only the status of a task"""
    mutation_actor = _task_actor_payload(session)
    mutation_attempt = {
        "status": status_update.status,
        "reason": status_update.reason,
        "completed_at": status_update.completed_at.isoformat() if status_update.completed_at else None,
        "cancelled_reason": status_update.cancelled_reason,
        "stale_reason": status_update.stale_reason,
    }
    effective_space_id = session.space_id
    try:
        effective_space_id = await _resolve_effective_task_space_id(session, status_update.space_id)
        # Verify task exists and belongs to current user's org
        task = await _resolve_task_for_space(
            session.db,
            task_id,
            effective_space_id,
            options=(
                selectinload(Task.posted_by_user),
                selectinload(Task.posted_by_agent),
                selectinload(Task.assigned_agent),
            ),
        )

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )

        update_data = _task_status_update_payload(
            task=task,
            requested_status=status_update.status,
            completed_at=status_update.completed_at,
            cancelled_reason=status_update.cancelled_reason,
            stale_reason=status_update.stale_reason,
            reason=status_update.reason,
        )

        await session.db.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(**update_data)
        )
        task_id_value = task.id
        task = await _reload_task_for_response(session.db, task_id_value)
        if update_data.get("work_status") == "completed":
            await notify_task_completed(session.db, task)
        elif update_data.get("work_status") == "blocked":
            from ...services.task_reminder_delivery import notify_task_blocked

            await notify_task_blocked(session.db, task)
        await session.db.commit()
        task = await _reload_task_for_response(session.db, task_id_value)
        _assert_task_status_persisted(task, status_update.status)

        # Generate display ID
        display_id = _task_display_id(task)

        # Build response data for both SSE and API response
        posted_by_data = {
            "id": str(task.posted_by_agent.id),
            "username": f"@{task.posted_by_agent.name}",
            "full_name": f"Agent: {task.posted_by_agent.name}",
            "type": "agent"
        } if task.posted_by_agent else ({
            "id": str(task.posted_by_user.id),
            "username": task.posted_by_user.username,
            "full_name": task.posted_by_user.full_name,
            "type": "user"
        } if task.posted_by_user else {
            "id": "unknown",
            "username": "unknown",
            "full_name": "Unknown Creator",
            "type": "unknown"
        })
        assigned_agent_data = {
            "id": str(task.assigned_agent.id),
            "name": task.assigned_agent.name,
            "agent_type": task.assigned_agent.agent_type
        } if task.assigned_agent else None

        # Broadcast task_updated SSE event for real-time frontend updates
        await redis_sse_broker.publish(
            space_id=str(task.space_id),
            event="task_updated",
            data=_task_to_sse_payload(task, display_id, posted_by=posted_by_data, assigned_agent=assigned_agent_data)
        )
        await _publish_task_mutation_activity(
            space_id=str(task.space_id),
            task_id=task_id,
            mutation="status",
            outcome="succeeded",
            actor=mutation_actor,
            attempted=mutation_attempt,
            task=task,
        )

        lifecycle = _task_lifecycle_fields(task)
        assignment_fields = await _task_assignment_fields(session.db, task)
        return TaskResponse(
            id=str(task.id),
            space_id=str(task.space_id),
            task_display_id=display_id,
            **_task_reference_fields(task),
            title=task.title,
            description=task.description,
            requirements=task.requirements or {},
            status=task.work_status,
            priority=task.priority,
            links=task.links if isinstance(task.links, list) else [],
            deadline=task.deadline,
            **lifecycle,
            **_task_reason_fields(task),
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
            **_task_summary_fields(task),
            posted_by=posted_by_data,
            **_task_assignment_response_kwargs(assignment_fields),
            **_task_work_pulse_fields(
                posted_by=posted_by_data,
                assigned_agent=assignment_fields.get("assignee") or assignment_fields.get("assigned_agent"),
                lifecycle=lifecycle,
            ),
        )

    except HTTPException as exc:
        await session.db.rollback()
        await _publish_task_mutation_activity(
            space_id=effective_space_id,
            task_id=task_id,
            mutation="status",
            outcome="failed",
            actor=mutation_actor,
            attempted=mutation_attempt,
            error=str(exc.detail),
        )
        raise
    except ValueError as ve:
        await session.db.rollback()
        error_detail = f"Invalid task ID format: {task_id} - {str(ve)}"
        await _publish_task_mutation_activity(
            space_id=effective_space_id,
            task_id=task_id,
            mutation="status",
            outcome="failed",
            actor=mutation_actor,
            attempted=mutation_attempt,
            error=error_detail,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail
        )
    except Exception as e:
        await session.db.rollback()
        error_detail = f"Failed to update task status: {str(e)}"
        await _publish_task_mutation_activity(
            space_id=effective_space_id,
            task_id=task_id,
            mutation="status",
            outcome="failed",
            actor=mutation_actor,
            attempted=mutation_attempt,
            error=error_detail,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail
        )


@router.delete("/v1/tasks/{task_id}")
@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Delete a task (only by the user who posted it)"""
    try:
        # Verify task exists, belongs to current user's org, and was posted by current user
        task = await _resolve_task_for_space(session.db, task_id, session.space_id)
        if task and task.posted_by != session.user.id:
            task = None

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found or you don't have permission to delete it"
            )

        # Store task ID before deletion for SSE broadcast
        deleted_task_id = str(task.id)

        # Delete the task
        await session.db.execute(
            delete(Task).where(Task.id == task.id)
        )
        await session.db.commit()

        # Broadcast task_deleted SSE event for real-time frontend updates
        await redis_sse_broker.publish(
            space_id=session.space_id,
            event="task_deleted",
            data={"id": deleted_task_id}
        )

        return {"message": "Task deleted successfully", "task_id": deleted_task_id}

    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID format: {task_id} - {str(ve)}"
        )
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete task: {str(e)}"
        )


# ======================================================================
# TASK NOTES ENDPOINTS
# ======================================================================

@router.get("/v1/tasks/{task_id}/notes", response_model=TaskNotesListResponse)
@router.get("/tasks/{task_id}/notes", response_model=TaskNotesListResponse)
async def get_task_notes(
    task_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Get all notes for a specific task"""
    try:
        # Verify task exists and belongs to current user's org
        task = await _resolve_task_for_space(session.db, task_id, session.space_id)

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )

        # Get all notes for the task
        notes_result = await session.db.execute(
            select(TaskNote).options(
                selectinload(TaskNote.author_user),
                selectinload(TaskNote.author_agent)
            ).where(and_(
                TaskNote.task_id == task.id,
                TaskNote.space_id == uuid.UUID(session.space_id)
            )).order_by(TaskNote.created_at.asc())
        )
        notes = notes_result.scalars().all()

        # Convert to response format
        notes_list = []
        for note in notes:
            notes_list.append(TaskNoteResponse(
                id=str(note.id),
                task_id=str(note.task_id),
                content=note.note,  # DB column 'note' → API field 'content'
                note_type=note.note_type,
                visibility=note.visibility,
                author_id=str(note.author_agent_id or note.author_user_id),
                author_type=note.author_type,
                author_name=note.author_name,
                created_at=note.created_at,
                updated_at=note.updated_at
            ))

        return TaskNotesListResponse(
            notes=notes_list,
            total=len(notes_list)
        )

    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID format: {task_id} - {str(ve)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch task notes: {str(e)}"
        )


@router.post("/v1/tasks/{task_id}/notes", response_model=TaskNoteResponse)
@router.post("/tasks/{task_id}/notes", response_model=TaskNoteResponse)
async def create_task_note(
    task_id: str,
    note_data: TaskNoteCreate,
    session: SecureSession = Depends(get_secure_session),
):
    """Create a new note for a specific task"""
    try:
        # Verify task exists and belongs to current user's org
        task = await _resolve_task_for_space(session.db, task_id, session.space_id)

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )

        # Create new task note
        new_note = TaskNote(
            id=uuid.uuid4(),
            task_id=task.id,
            space_id=uuid.UUID(session.space_id),
            author_user_id=None if session.is_agent else session.user.id,
            author_agent_id=uuid.UUID(session.agent_id) if session.is_agent else None,
            note=note_data.content,   # API field 'content' → DB column 'note'
            note_type=note_data.note_type,
            visibility=note_data.visibility
        )

        session.db.add(new_note)
        await session.db.commit()
        await session.db.refresh(new_note)

        # Load relationships for response
        await session.db.refresh(new_note, ["author_user", "author_agent"])

        return TaskNoteResponse(
            id=str(new_note.id),
            task_id=str(new_note.task_id),
            content=new_note.note,   # DB column 'note' → API field 'content'
            note_type=new_note.note_type,
            visibility=new_note.visibility,
            author_id=str(new_note.author_agent_id or new_note.author_user_id),
            author_type=new_note.author_type,
            author_name=new_note.author_name,
            created_at=new_note.created_at,
            updated_at=new_note.updated_at
        )

    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID format: {task_id} - {str(ve)}"
        )
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create task note: {str(e)}"
        )


@router.patch("/v1/tasks/{task_id}/notes/{note_id}", response_model=TaskNoteResponse)
@router.patch("/tasks/{task_id}/notes/{note_id}", response_model=TaskNoteResponse)
async def update_task_note(
    task_id: str,
    note_id: str,
    note_update: TaskNoteUpdate,
    session: SecureSession = Depends(get_secure_session),
):
    """Edit a task note (author only)"""
    try:
        task = await _resolve_task_for_space(session.db, task_id, session.space_id)
        if not task:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

        if session.is_agent and session.agent_id:
            author_filter = TaskNote.author_agent_id == uuid.UUID(session.agent_id)
        else:
            author_filter = TaskNote.author_user_id == session.user.id

        note_result = await session.db.execute(
            select(TaskNote).options(
                selectinload(TaskNote.author_user),
                selectinload(TaskNote.author_agent)
            ).where(and_(
                TaskNote.id == uuid.UUID(note_id),
                TaskNote.task_id == task.id,
                TaskNote.space_id == uuid.UUID(session.space_id),
                author_filter  # author only
            ))
        )
        note = note_result.scalar_one_or_none()
        if not note:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Note not found or you don't have permission to edit it"
            )

        await session.db.execute(
            update(TaskNote)
            .where(TaskNote.id == note.id)
            .values(note=note_update.content, updated_at=datetime.now(timezone.utc))  # content → note
        )
        await session.db.commit()
        await session.db.refresh(note, ["author_user", "author_agent"])

        return TaskNoteResponse(
            id=str(note.id),
            task_id=str(note.task_id),
            content=note_update.content,  # echo back the updated content
            note_type=note.note_type,
            visibility=note.visibility,
            author_id=str(note.author_agent_id or note.author_user_id),
            author_type=note.author_type,
            author_name=note.author_name,
            created_at=note.created_at,
            updated_at=note.updated_at
        )

    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid ID format - {str(ve)}")
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update task note: {str(e)}")


@router.delete("/v1/tasks/{task_id}/notes/{note_id}")
@router.delete("/tasks/{task_id}/notes/{note_id}")
async def delete_task_note(
    task_id: str,
    note_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Delete a task note (only by the author)"""
    try:
        # Verify task exists and belongs to current user's org
        task = await _resolve_task_for_space(session.db, task_id, session.space_id)

        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task not found"
            )

        # Verify note exists, belongs to task and caller can delete it
        # Agents match on author_agent_id; users match on author_user_id
        if session.is_agent:
            author_filter = TaskNote.author_agent_id == uuid.UUID(session.agent_id)
        else:
            author_filter = TaskNote.author_user_id == session.user.id

        note_result = await session.db.execute(
            select(TaskNote).where(and_(
                TaskNote.id == uuid.UUID(note_id),
                TaskNote.task_id == task.id,
                TaskNote.space_id == uuid.UUID(session.space_id),
                author_filter  # Only the original author can delete
            ))
        )
        note = note_result.scalar_one_or_none()

        if not note:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Note not found or you don't have permission to delete it"
            )

        # Delete the note
        await session.db.execute(
            delete(TaskNote).where(TaskNote.id == note.id)
        )
        await session.db.commit()

        return {"message": "Task note deleted successfully", "note_id": str(note.id)}

    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID format - {str(ve)}"
        )
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete task note: {str(e)}"
        )


# ======================================================================
# AUTO-ASSIGNMENT ENDPOINTS - Progressive Disclosure System
# ======================================================================

@router.get("/v1/tasks/{task_id}/preview", response_model=TaskResponse)
@router.get("/tasks/{task_id}/preview", response_model=TaskResponse)
async def get_task_preview(
    task_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """
    Get task preview without triggering assignment
    Safe endpoint for browsing tasks without commitment
    """
    # This is identical to get_task but documented as safe preview
    return await get_task(task_id, session)


# DISABLED: Auto-assignment endpoint - problematic UX (assigns tasks just by viewing)
# This endpoint was never used by the frontend and creates bad user experience
# Keep commented for potential future modular auto-assignment system
#
# @router.get("/tasks/{task_id}/details", response_model=TaskResponse)
# async def get_task_details_with_auto_assignment(
#     task_id: str,
#     current_user: User = Depends(get_current_user_from_token),
#     db: AsyncSession = Depends(get_db_session),
#     reference: bool = Query(False, description="View for reference only (no assignment)")
# ):
#     """
#     Get full task details WITH AUTO-ASSIGNMENT
#
#     This endpoint automatically assigns the task to the current user's agent
#     unless they already have a task assigned (single-task constraint).
#
#     Use ?reference=true to view without assignment for reference purposes.
#     """
#     try:
#         # Get current user's agent (assuming user has one agent)
#         agent_result = await db.execute(
#             select(Agent).where(and_(
#                 Agent.user_id == current_user.id,
#                 Agent.space_id == current_user._effective_space_id,
#                 Agent.status == "active"
#             )).limit(1)
#         )
#         agent = agent_result.scalar_one_or_none()
#
#         if not agent:
#             # If no agent, just return regular task details
#             return await get_task(task_id, current_user, db)
#
#         # If reference mode, just return task details without assignment
#         if reference:
#             return await get_task(task_id, current_user, db)
#
#         # Check if this is the agent's own created task
#         task_uuid = uuid.UUID(task_id)
#         task_result = await db.execute(
#             select(Task).where(and_(
#                 Task.id == task_uuid,
#                 Task.space_id == current_user._effective_space_id
#             ))
#         )
#         task = task_result.scalar_one_or_none()
#
#         if not task:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND,
#                 detail="Task not found"
#             )
#
#         # Don't auto-assign if agent created the task
#         if task.posted_by_agent_id == agent.id:
#             return await get_task(task_id, current_user, db)
#
#         # Don't auto-assign completed or cancelled tasks
#         if task.work_status in ['completed', 'cancelled']:
#             return await get_task(task_id, current_user, db)
#
#         # Try auto-assignment
#         try:
#             assignment_result = await AutoAssignmentService.auto_assign_task(
#                 task_id=task_uuid,
#                 agent_id=agent.id,
#                 db=db
#             )
#
#             # Return the task details after assignment
#             return await get_task(task_id, current_user, db)
#
#         except TaskConflictException as e:
#             # Agent has another task assigned - return conflict
#             raise e
#
#     except TaskConflictException:
#         raise
#     except HTTPException:
#         raise
#     except ValueError as ve:
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail=f"Invalid task ID format: {task_id} - {str(ve)}"
#         )
#     except Exception as e:
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Failed to get task details with auto-assignment: {str(e)}"
#         )



@router.get("/v1/tasks/reminders/pause")
@router.get("/tasks/reminders/pause")
async def get_task_reminder_pause(session: SecureSession = Depends(get_secure_session)):
    """Read the current recurring task-reminder pause/admin state.

    The state gates only the automatic reminder delivery loop. Manual nudge remains
    available for controlled proof while recurring delivery is held.
    """
    readback = await get_task_reminder_pause_readback()
    readback["actor"] = _task_actor_payload(session)
    return readback


@router.put("/v1/tasks/reminders/pause")
@router.put("/tasks/reminders/pause")
async def set_task_reminder_pause(
    payload: TaskReminderPausePayload,
    session: SecureSession = Depends(get_secure_session),
):
    """Set global and working-hours pause state for recurring task reminders."""
    actor = _task_actor_payload(session)
    await _require_task_reminder_pause_admin(session)
    current = await get_task_reminder_pause_state()
    state_payload = payload.model_dump(by_alias=True)
    state = {
        **current,
        "global": state_payload.get("global", {}),
        "working_hours": state_payload.get("working_hours", {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": payload.updated_by or actor.get("agent_id") or actor.get("user_id"),
        "scope": "task_reminder_delivery",
    }
    saved = await set_task_reminder_pause_state(state)
    readback = await get_task_reminder_pause_readback()
    readback["state"] = saved
    readback["actor"] = actor
    return readback


@router.post("/v1/tasks/{task_id}/nudge")
@router.post("/tasks/{task_id}/nudge")
async def nudge_task_reminder(
    task_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """Manually fire a single task reminder right now (out-of-band "nudge").

    Delivers the same activity-stream card + notification + SSE to the task's
    assignee immediately, WITHOUT touching the auto-cadence (``next_reminder_at``)
    or counting against the policy's ``max_count``. Used to drive reminders by hand
    while the recurring delivery loop is being tuned.
    """
    from app.services.task_reminder_delivery import (
        _emit_task_reminder,
        _task_assignee,
    )

    task = await _resolve_task_for_space(
        session.db,
        task_id,
        session.space_id,
        options=(
            selectinload(Task.assigned_agent),
            selectinload(Task.posted_by_user),
            selectinload(Task.posted_by_agent),
        ),
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    if (getattr(task, "work_status", None) or "").lower() in {
        "completed",
        "closed",
        "cancelled",
        "canceled",
    }:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot nudge a completed or cancelled task",
        )

    assignee = await _task_assignee(session.db, task)
    if assignee is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task has no assignee to nudge",
        )

    try:
        emit = await _emit_task_reminder(
            session.db,
            task,
            assignee,
            now=datetime.now(timezone.utc),
            advance_schedule=False,
            manual=True,
            kind="task_nudge",
        )
        await session.db.commit()
    except Exception as exc:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to nudge task reminder: {exc}",
        ) from exc

    return {
        "success": True,
        "task_id": task_id,
        "nudged": True,
        "assignee_type": getattr(task, "assignee_type", None),
        "message_id": emit["message_id"],
    }


@router.post("/v1/tasks/{task_id}/complete")
@router.post("/tasks/{task_id}/complete")
async def complete_task_assignment(
    task_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """
    Complete the current task assignment and release the agent
    This allows the agent to take on new tasks
    """
    try:
        # Get current user's agent
        agent_result = await session.db.execute(
            select(Agent).where(and_(
                Agent.user_id == session.user.id,
                Agent.status == "active"
            )).limit(1)
        )
        agent = agent_result.scalar_one_or_none()

        if not agent:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active agent found"
            )

        # Verify this task reference resolves to the assignment held by the agent
        task = await _resolve_task_for_space(session.db, task_id, session.space_id)
        if task is None or str(agent.current_assigned_task_id) != str(task.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Task is not assigned to you"
            )

        # Release the assignment and mark as completed
        result = await AutoAssignmentService.release_assignment(
            agent_id=agent.id,
            db=session.db,
            new_task_status="completed"
        )

        return {
            "success": True,
            "message": result["message"],
            "task_id": result["task_id"],
            "task_display_id": result["task_display_id"],
            "status": "Ready for next assignment"
        }

    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID format: {task_id} - {str(ve)}"
        )
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to complete task assignment: {str(e)}"
        )


@router.post("/v1/tasks/{task_id}/abandon")
@router.post("/tasks/{task_id}/abandon")
async def abandon_task_assignment(
    task_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    """
    Abandon the current task assignment without completing it
    Returns the task to unassigned status
    """
    try:
        # Get current user's agent
        agent_result = await session.db.execute(
            select(Agent).where(and_(
                Agent.user_id == session.user.id,
                Agent.status == "active"
            )).limit(1)
        )
        agent = agent_result.scalar_one_or_none()

        if not agent:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active agent found"
            )

        # Verify this task reference resolves to the assignment held by the agent
        task = await _resolve_task_for_space(session.db, task_id, session.space_id)
        if task is None or str(agent.current_assigned_task_id) != str(task.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Task is not assigned to you"
            )

        # Release the assignment without completing
        result = await AutoAssignmentService.release_assignment(
            agent_id=agent.id,
            db=session.db,
            new_task_status="not_started"  # Reset to not started
        )

        return {
            "success": True,
            "message": f"Abandoned assignment: {result['task_display_id']}",
            "task_id": result["task_id"],
            "task_display_id": result["task_display_id"],
            "status": "Ready for next assignment"
        }

    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID format: {task_id} - {str(ve)}"
        )
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to abandon task assignment: {str(e)}"
        )


async def _get_my_current_assignment_impl(
    session: SecureSession = Depends(get_secure_session),
):
    """
    Get the current task assignment for the authenticated agent
    """
    try:
        # Get current user's agent
        agent_result = await session.db.execute(
            select(Agent).where(and_(
                Agent.user_id == session.user.id,
                Agent.status == "active"
            )).limit(1)
        )
        agent = agent_result.scalar_one_or_none()

        if not agent:
            return {
                "has_assignment": False,
                "message": "No active agent found"
            }

        # Get current assignment
        current_task = await AutoAssignmentService.get_agent_current_task(agent.id, session.db)

        if not current_task:
            return {
                "has_assignment": False,
                "message": "No task currently assigned"
            }

        return {
            "has_assignment": True,
            "current_task": current_task,
            "agent_id": str(agent.id),
            "agent_name": agent.name
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get current assignment: {str(e)}"
        )


declare_route_action(
    create_task,
    action_family="tasks",
    action_id="tasks.create",
    target_resource_type="task",
)
declare_route_action(
    update_task,
    action_family="tasks",
    action_id="tasks.update.any",
    target_resource_type="task",
)
