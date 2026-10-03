"""Background delivery for task reminders and completion notifications.

Uses the lifecycle fields added in backend PR #314.  This module is backend-only
and intentionally does not depend on ax-cli gateway code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.core.redis_client import redis_client as core_redis_client
from app.core.agent_roster_filter import include_agent_in_roster
from app.models.agent import Agent
from app.models.message import Message
from app.models.task import Task
from app.models.user import User
from app.services.agent_control_service import AgentControlService
from app.services.fleet_control_service import FleetControlService
from app.services.notifications_service import NotificationsService
from app.services.redis_sse_broker import redis_sse_broker
from app.services.mcp_event_publishers import publish_mention_event

logger = logging.getLogger(__name__)
agent_control_service = AgentControlService(core_redis_client)

DEFAULT_REMINDER_INTERVAL_MINUTES = 60
DEFAULT_MAX_REMINDERS = 24
DEFAULT_BATCH_SIZE = 50
DEFAULT_POLL_SECONDS = 60
DEFAULT_MAX_REMINDERS_PER_TARGET_PER_TICK = 5
TASK_REMINDER_PAUSE_KEY = "task_reminders:pause_state"


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        hour_s, minute_s = value.split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def _time_in_window(current: time, start: time, end: time) -> bool:
    """Return True when current is inside [start, end), including overnight windows."""
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _default_pause_state() -> dict[str, Any]:
    """Fallback pause state from env, useful before the admin key is initialized."""
    return {
        "global": {
            "paused": _bool_env("TASK_REMINDER_PAUSED", False),
            "reason": os.getenv("TASK_REMINDER_PAUSE_REASON") or None,
        },
        "working_hours": {
            "enabled": _bool_env("TASK_REMINDER_WORKING_HOURS_ENABLED", False),
            "timezone": os.getenv("TASK_REMINDER_WORKING_HOURS_TZ", "UTC"),
            "start": os.getenv("TASK_REMINDER_WORKING_HOURS_START", "08:00"),
            "end": os.getenv("TASK_REMINDER_WORKING_HOURS_END", "22:00"),
        },
    }


def _normalize_pause_state(state: dict[str, Any] | None) -> dict[str, Any]:
    base = _default_pause_state()
    if isinstance(state, dict):
        if isinstance(state.get("global"), dict):
            base["global"].update({k: v for k, v in state["global"].items() if v is not None})
        if isinstance(state.get("working_hours"), dict):
            base["working_hours"].update({k: v for k, v in state["working_hours"].items() if v is not None})
        for key in ("updated_at", "updated_by", "scope"):
            if state.get(key) is not None:
                base[key] = state[key]
    return base


def task_reminder_pause_readback(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Return admin-readable pause state plus whether recurring delivery is held now."""
    state = _normalize_pause_state(state)
    now = now or datetime.now(UTC)
    global_state = state.get("global", {})
    if bool(global_state.get("paused")):
        return {
            "paused": True,
            "pause_reason": "global_pause",
            "reason": global_state.get("reason"),
            "scope": "task_reminder_delivery",
            "state": state,
            "checked_at": now.isoformat(),
        }

    wh = state.get("working_hours", {})
    if bool(wh.get("enabled")):
        timezone_name = str(wh.get("timezone") or "UTC")
        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            tz = UTC
            timezone_name = "UTC"
        local_now = now.astimezone(tz)
        start = _parse_hhmm(str(wh.get("start") or "08:00"))
        end = _parse_hhmm(str(wh.get("end") or "22:00"))
        if start is not None and end is not None and not _time_in_window(local_now.time().replace(tzinfo=None), start, end):
            return {
                "paused": True,
                "pause_reason": "outside_working_hours",
                "reason": f"outside working-hours window {start.strftime('%H:%M')}-{end.strftime('%H:%M')} {timezone_name}",
                "scope": "task_reminder_delivery",
                "state": state,
                "checked_at": now.isoformat(),
                "local_checked_at": local_now.isoformat(),
            }
    return {
        "paused": False,
        "pause_reason": None,
        "reason": None,
        "scope": "task_reminder_delivery",
        "state": state,
        "checked_at": now.isoformat(),
    }


async def get_task_reminder_pause_state(redis=core_redis_client) -> dict[str, Any]:
    raw = await redis.get(TASK_REMINDER_PAUSE_KEY)
    if not raw:
        return _normalize_pause_state(None)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid task reminder pause state in redis")
        return _normalize_pause_state(None)
    return _normalize_pause_state(data if isinstance(data, dict) else None)


async def set_task_reminder_pause_state(state: dict[str, Any], redis=core_redis_client) -> dict[str, Any]:
    normalized = _normalize_pause_state(state)
    await redis.set(TASK_REMINDER_PAUSE_KEY, json.dumps(normalized, sort_keys=True))
    return normalized


async def get_task_reminder_pause_readback(redis=core_redis_client, now: datetime | None = None) -> dict[str, Any]:
    fleet_readback = await FleetControlService(redis).enforcement_readback(surface="reminders")
    if fleet_readback["blocked"]:
        checked_at = (now or datetime.now(UTC)).isoformat()
        return {
            "paused": True,
            "pause_reason": "fleet_control",
            "reason": fleet_readback.get("reason") or "fleet emergency stop/reminder silence active",
            "scope": "fleet_control",
            "state": await get_task_reminder_pause_state(redis),
            "checked_at": checked_at,
            "fleet_control": fleet_readback,
        }
    return task_reminder_pause_readback(await get_task_reminder_pause_state(redis), now)


def _policy_int(
    policy: dict[str, Any],
    key: str,
    default: int,
    *,
    legacy_key: str | None = None,
    minimum: int = 1,
    maximum: int = 10_000,
) -> int:
    value = policy.get(key)
    if value is None and legacy_key:
        value = policy.get(legacy_key)
    if value is None:
        value = default
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _next_reminder_at(task: Task, now: datetime) -> datetime | None:
    policy = task.reminder_policy if isinstance(task.reminder_policy, dict) else {}
    until_done = bool(policy.get("until_done", True))
    explicit_max_count = policy.get("max_count") is not None
    if explicit_max_count or not until_done:
        max_count = _policy_int(policy, "max_count", DEFAULT_MAX_REMINDERS, minimum=1, maximum=365)
        if (task.reminder_count or 0) >= max_count:
            return None
    interval_minutes = _policy_int(
        policy,
        "cadence_minutes",
        DEFAULT_REMINDER_INTERVAL_MINUTES,
        legacy_key="interval_minutes",
        minimum=1,
        maximum=7 * 24 * 60,
    )
    return now + timedelta(minutes=interval_minutes)



def _display_id(task: Task) -> str:
    return f"task_{task.task_number:06d}" if getattr(task, "task_number", None) else "task_legacy"


TASK_ALERT_TERMINATOR = "Resolve by updating task status. No reply needed."


def _task_action_envelope(task: Task, kind: str, *, source: str | None = None) -> dict[str, Any]:
    """Standard instruction envelope for task-originated agent alerts.

    The terminator is intentionally part of the payload/content rather than an
    ingress suppression rule: the target resolves the loop by updating task
    status instead of replying to the alert.
    """

    if source is None:
        source = {
            "task_reminder": "task_reminder_scheduler",
            "task_nudge": "task_nudge",
            "task_assignment": "task_assignment",
        }.get(kind, "task_alert")

    return {
        "context": {
            "task_id": str(task.id),
            "task_display_id": _display_id(task),
            "title": task.title,
            "kind": kind,
            "source": source,
        },
        "actions": [
            {"key": "done", "label": "Done", "status": "completed"},
            {
                "key": "working",
                "label": "Still working",
                "action": "continue",
                "status": "in_progress",
                "note_required": True,
            },
            {"key": "blocked", "label": "Blocked", "status": "blocked", "reason_required": True},
        ],
        "terminator": TASK_ALERT_TERMINATOR,
    }


def _task_action_envelope_text(task: Task, kind: str) -> str:
    label = {
        "task_reminder": "Task Reminder",
        "task_nudge": "Task Nudge",
        "task_assignment": "Task Assignment",
    }.get(kind, "Task Alert")
    return (
        f"⏰ {label} — {_display_id(task)}\n"
        f"{task.title}\n"
        "Take action now, then update the task status to resolve this alert:\n"
        "• Done? → status=completed\n"
        "• Still working? → continue + leave a one-line progress note\n"
        "• Blocked? → status=blocked + name the blocker\n"
        f"{TASK_ALERT_TERMINATOR}"
    )


def _task_activity_content(task: Task, kind: str) -> str:
    metadata = task.task_metadata if isinstance(getattr(task, "task_metadata", None), dict) else {}
    if kind in {"task_reminder", "task_nudge", "task_assignment"}:
        return _task_action_envelope_text(task, kind)
    if kind == "task_blocked":
        blocked_reason = metadata.get("blocked_reason")
        return f"Task blocked: {task.title}" + (f" — {blocked_reason}" if blocked_reason else "")
    if kind == "task_completed":
        completed_reason = metadata.get("completed_reason") or metadata.get("completion_notes")
        return f"Task completed: {task.title}" + (f" — {completed_reason}" if completed_reason else "")
    return f"Task update: {task.title}"


def _task_open_action(task: Task) -> dict[str, Any]:
    return {
        "type": "open_task",
        "label": "Open task",
        "task_id": str(task.id),
        "deep_link": f"ax://spaces/{task.space_id}/tasks/{task.id}",
    }


def _task_alert_work_binding(payload: dict[str, Any]) -> dict[str, Any] | None:
    message_id = payload.get("message_id")
    if not message_id:
        return None
    return {
        "label": "Work",
        "stream": "messages",
        "event": "agent_processing",
        "message_id": str(message_id),
        "alert_id": payload.get("notification_id"),
        "alert_message_id": str(message_id),
        "target_agent_id": payload.get("target_agent_id"),
    }


def _add_task_alert_activity_binding(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach the message-activity binding Canary can use for the alert Work row."""

    message_id = payload.get("message_id")
    if not message_id:
        return payload
    payload["activity_stream"] = "messages"
    payload["activity_event"] = "agent_processing"
    payload["activity_message_id"] = str(message_id)
    payload["alert_message_id"] = str(message_id)
    payload["work"] = _task_alert_work_binding(payload)
    return payload


def _attach_task_alert_activity_binding(message: Message, payload: dict[str, Any]) -> None:
    """Mirror the alert→message activity binding into the durable card metadata."""

    work = _task_alert_work_binding(payload)
    if not work:
        return
    metadata = dict(message.message_metadata or {})
    metadata["activity_stream"] = "messages"
    metadata["activity_event"] = "agent_processing"
    metadata["activity_message_id"] = work["message_id"]
    metadata["notification_id"] = payload.get("notification_id")
    metadata["work"] = work

    card = dict(metadata.get("card") or {})
    card["work"] = work
    metadata["card"] = card

    ui = dict(metadata.get("ui") or {})
    cards = []
    for item in ui.get("cards") or []:
        updated = dict(item)
        if isinstance(updated.get("payload"), dict):
            updated["payload"] = {**updated["payload"], "work": work}
        cards.append(updated)
    if cards:
        ui["cards"] = cards

    widget = dict(ui.get("widget") or {})
    initial_data = dict(widget.get("initial_data") or {})
    data = dict(initial_data.get("data") or {})
    task_data = dict(data.get("task") or {})
    if task_data:
        task_data["work"] = work
        data["task"] = task_data
        initial_data["data"] = data
        widget["initial_data"] = initial_data
        ui["widget"] = widget
    metadata["ui"] = ui
    message.message_metadata = metadata


def _looks_like_uuid(value: str | None) -> bool:
    if not value:
        return False
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _agent_handle(agent: Agent | None) -> str | None:
    if agent is None:
        return None
    name = getattr(agent, "name", None)
    return str(name).strip().lstrip("@") if name else None


def _target_descriptor(task: Task, target: Agent | User | None = None) -> dict[str, Any]:
    """Return explicit non-secret reminder target fields for activity cards/SSE."""

    entity = target or getattr(task, "assigned_agent", None)
    if isinstance(entity, Agent):
        handle = _agent_handle(entity)
        return {
            "target_type": "agent",
            "target_id": str(entity.id),
            "target_agent_id": str(entity.id),
            "target_agent_handle": handle,
            "target_agent_name": handle,
        }
    if isinstance(entity, User):
        return {
            "target_type": "user",
            "target_id": str(entity.id),
            "target_user_id": str(entity.id),
        }

    if getattr(task, "assigned_agent_id", None) is not None:
        target_id = str(task.assigned_agent_id)
        return {
            "target_type": "agent",
            "target_id": target_id,
            "target_agent_id": target_id,
            "target_agent_handle": None,
            "target_agent_name": None,
        }
    if getattr(task, "assignee_type", None) == "agent" and getattr(task, "assignee_id", None) is not None:
        target_id = str(task.assignee_id)
        return {
            "target_type": "agent",
            "target_id": target_id,
            "target_agent_id": target_id,
            "target_agent_handle": None,
            "target_agent_name": None,
        }
    if getattr(task, "assignee_type", None) == "user" and getattr(task, "assignee_id", None) is not None:
        target_id = str(task.assignee_id)
        return {
            "target_type": "user",
            "target_id": target_id,
            "target_user_id": target_id,
        }
    return {"target_type": None, "target_id": None}


def _reminder_target_key(task: Task, target: Agent | User | None = None) -> str:
    """Stable per-space/target key used to cap scheduler bursts in one poll tick."""

    fields = _target_descriptor(task, target)
    target_type = fields.get("target_type") or "unknown"
    target_id = fields.get("target_id") or "unassigned"
    return f"{task.space_id}:{target_type}:{target_id}"


def _task_notification_payload(
    task: Task,
    kind: str,
    event_id: str | None = None,
    *,
    target: Agent | User | None = None,
) -> dict[str, Any]:
    metadata = task.task_metadata if isinstance(getattr(task, "task_metadata", None), dict) else {}
    owner_user_ids = _task_owner_user_ids(task)
    target_fields = _target_descriptor(task, target)
    reminder_kind = kind if kind in {"task_reminder", "task_nudge", "task_assignment"} else None
    target_id = target_fields.get("target_id")
    reminder_dedupe_key = f"{reminder_kind}:{task.id}:{target_id}" if reminder_kind and target_id else None
    payload: dict[str, Any] = {
        "kind": kind,
        "task_id": str(task.id),
        "task_display_id": _display_id(task),
        "title": task.title,
        "status": task.work_status,
        "queue_state": task.queue_state,
        "priority": task.priority,
        "assignee_type": task.assignee_type,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "assigned_agent_id": str(task.assigned_agent_id) if task.assigned_agent_id else None,
        "posted_by": str(task.posted_by) if task.posted_by else None,
        "owner_user_id": owner_user_ids[0] if owner_user_ids else None,
        "owner_user_ids": owner_user_ids,
        "reason": metadata.get("blocked_reason") if kind == "task_blocked" else metadata.get("completed_reason") or metadata.get("completion_notes"),
        "blocked_reason": metadata.get("blocked_reason"),
        "completed_reason": metadata.get("completed_reason") or metadata.get("completion_notes"),
        "content": _task_activity_content(task, kind),
        "action_envelope": _task_action_envelope(task, kind),
        "deep_link": f"ax://spaces/{task.space_id}/tasks/{task.id}",
        "action": _task_open_action(task),
        "actions": [_task_open_action(task)],
        "created_at": datetime.now(UTC).isoformat(),
        **target_fields,
    }
    if reminder_kind:
        payload["reminder_kind"] = reminder_kind
    if reminder_dedupe_key:
        payload["reminder_dedupe_key"] = reminder_dedupe_key
    delivery = metadata.get("reminder_delivery") if isinstance(metadata.get("reminder_delivery"), dict) else None
    if delivery:
        payload["reminder_delivery"] = delivery
        for key in (
            "why_fired",
            "grouped_count",
            "suppressed_count",
            "target_owner",
            "next_fire_at",
            "required_next_action",
        ):
            if key in delivery:
                payload[key] = delivery[key]
    if event_id:
        payload["notification_id"] = event_id
    _add_task_alert_activity_binding(payload)
    return payload



def _task_reminder_message(task: Task, payload: dict[str, Any]) -> Message:
    """Build the durable activity-stream card for a due task reminder."""

    task_id = str(task.id)
    task_display_id = _display_id(task)
    kind = payload.get("kind") or "task_reminder"
    source = payload.get("action_envelope", {}).get("context", {}).get("source") or "task_reminder_scheduler"
    severity = {"critical": "critical", "urgent": "warning", "high": "warning"}.get(
        (task.priority or "").lower(), "info"
    )
    _due = getattr(task, "due_at", None) or getattr(task, "deadline", None)
    due_at_iso = _due.isoformat() if hasattr(_due, "isoformat") else (_due or None)
    target_handle = payload.get("target_agent_handle") or payload.get("target_agent_name")
    reminder_reason = (
        f"Task reminder for @{target_handle}: {task.title}"
        if target_handle
        else f"Task reminder: {task.title}"
    )
    card = {
        "type": kind,
        # The frontend AlertCardBody reads payload.kind to specialize a reminder
        # within the shared "alert" card surface; the wrapper card type is "alert".
        "kind": kind,
        "title": task.title,
        "task_id": task_id,
        "task_display_id": task_display_id,
        "deep_link": payload["deep_link"],
        "action": payload["action"],
        "actions": payload["actions"],
        "priority": task.priority,
        "status": task.work_status,
        "queue_state": task.queue_state,
        "target_type": payload.get("target_type"),
        "target_agent_id": payload.get("target_agent_id"),
        "target_agent_handle": payload.get("target_agent_handle"),
        "target_agent_name": payload.get("target_agent_name"),
        "target_user_id": payload.get("target_user_id"),
        "reminder_kind": payload.get("reminder_kind"),
        "reminder_dedupe_key": payload.get("reminder_dedupe_key"),
        "action_envelope": payload.get("action_envelope"),
        "work": _task_alert_work_binding(payload),
        # Body/summary + structured alert block the frontend AlertCardBody renders
        # (title: alert.task.title -> alert.title -> payload.title; body: alert.reason
        # -> payload.summary; chips: severity, due_at, target_agent, source, alert.task).
        # Note: payload.actions is NOT rendered as CTAs (frontend supplies Copy/Share);
        # kept above only as metadata.
        "summary": reminder_reason,
        "alert": {
            "kind": kind,
            "title": task.title,
            "reason": reminder_reason,
            "severity": severity,
            "target_agent": target_handle,
            "source": source,
            "due_at": due_at_iso,
            "task": {
                "title": task.title,
                "status": task.work_status,
                "due_at": due_at_iso,
                "queue_position": task.queue_state,
            },
        },
    }
    metadata = {
        "kind": kind,
        # Frontend hint: render as the single card/widget surface (metadata.ui.cards),
        # NOT as a chat-style message bubble. Keeps reminders from reading as a
        # message "from" anyone — they are a system card targeted at the assignee.
        "render": "card_only",
        "task_id": task_id,
        "task_display_id": task_display_id,
        # Legacy compact-card shape retained for compatibility with existing
        # activity consumers that read metadata.card directly.
        "card": card,
        "ui": {
            "cards": [
                {
                    "card_id": f"{kind}:{task_id}",
                    # Wrapper type MUST be "alert": the deployed frontend
                    # (transcript-model.shouldRenderActivityEntryAsSurfaceOnly + AxSurfaceRail)
                    # renders a reminder as a sender-less system card only when a surface
                    # card has type "alert" AND message_type is "reminder"/"alert". Otherwise
                    # it falls back to a chat bubble ("from <poster>"). payload.kind keeps the
                    # task_reminder specialization.
                    "type": "alert",
                    "version": 1,
                    "payload": card,
                }
            ],
            "widget": {
                "kind": "mcp_app",
                "tool_name": "tasks",
                "tool_action": "get",
                "tool_call_id": f"{kind}:{task_id}",
                "resource_uri": "ui://tasks/detail",
                "display_mode": "inline",
                "lifecycle": "completed",
                "result_kind": "task_detail",
                "arguments": {"action": "get", "task_id": task_id},
                "initial_data": {
                    "kind": "task_detail",
                    "data": {
                        "task": {
                            "id": task_id,
                            "display_id": task_display_id,
                            "title": task.title,
                            "status": task.work_status,
                            "queue_state": task.queue_state,
                            "priority": task.priority,
                            "target_type": payload.get("target_type"),
                            "target_agent_id": payload.get("target_agent_id"),
                            "target_agent_handle": payload.get("target_agent_handle"),
                            "target_agent_name": payload.get("target_agent_name"),
                            "target_user_id": payload.get("target_user_id"),
                            "reminder_kind": payload.get("reminder_kind"),
                            "reminder_dedupe_key": payload.get("reminder_dedupe_key"),
                        }
                    },
                },
            },
        },
    }
    return Message(
        space_id=task.space_id,
        user_id=None,
        agent_id=None,
        content=payload["content"],
        channel="activity",
        # "reminder" (not "task") so the frontend treats it as an activity/alert
        # surface, not a chat message. Pairs with the "alert" card type above.
        message_type="reminder",
        message_metadata=metadata,
    )


def _task_reminder_mention_wake_payload(
    task: Task,
    message: Message,
    payload: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Build the synthetic mention SSE event that wakes the assigned agent.

    The durable artifact remains a sender-less activity reminder card. This event
    exists for headless/listener runtimes that only wake on explicit ``mention``
    SSE events; it is not a second chat message.
    """

    if payload.get("target_type") != "agent":
        return None

    target_agent_id = payload.get("target_agent_id") or payload.get("target_id")
    target_handle = payload.get("target_agent_handle") or payload.get("target_agent_name")
    if not target_agent_id and not target_handle:
        return None

    target_agent_id_text = str(target_agent_id) if target_agent_id else None
    if target_handle:
        target_handle = str(target_handle).lstrip("@")
        mentioned_agent = f"@{target_handle}"
        content = f"@{target_handle} {payload.get('content') or _task_action_envelope_text(task, 'task_reminder')}"
        mentions = [mentioned_agent, target_handle]
        if target_agent_id_text:
            mentions.append(target_agent_id_text)
    else:
        mentioned_agent = str(target_agent_id)
        content = str(payload.get("content") or _task_action_envelope_text(task, "task_reminder"))
        mentions = [mentioned_agent]

    metadata = dict(message.message_metadata or {})
    metadata.update(
        {
            "kind": payload.get("kind") or "task_reminder",
            "mentions": mentions,
            "mentioned_agent_id": target_agent_id_text,
            "target_type": "agent",
            "target_agent_id": target_agent_id_text,
            "target_agent_handle": target_handle,
            "target_agent_name": target_handle,
            "task_id": str(task.id),
            "task_display_id": _display_id(task),
            "reminder_dedupe_key": payload.get("reminder_dedupe_key"),
            "notification_id": payload.get("notification_id"),
            "action_envelope": payload.get("action_envelope"),
            "activity_stream": payload.get("activity_stream"),
            "activity_event": payload.get("activity_event"),
            "activity_message_id": payload.get("activity_message_id"),
            "alert_message_id": payload.get("alert_message_id"),
            "work": payload.get("work"),
        }
    )
    return {
        "id": str(message.id),
        "content": content,
        "author": payload.get("action_envelope", {}).get("context", {}).get("source") or "task_reminder_scheduler",
        "author_type": "system",
        # Listener self-loop guard checks the sender agent_id; reminders are
        # system-authored, so keep this unset rather than using the target id.
        "agent_id": None,
        "sender_name": payload.get("action_envelope", {}).get("context", {}).get("source") or "task_reminder_scheduler",
        "mentioned_agent": mentioned_agent,
        "mentioned_agent_id": target_agent_id_text,
        "mentions": mentions,
        "actor_roster_id": None,
        "space_id": str(task.space_id),
        "timestamp": now.isoformat(),
        "channel": "activity",
        "message_type": "reminder",
        "metadata": metadata,
    }


async def _task_reminder_target_disabled(task: Task, assignee) -> bool:
    """Return True when the assigned agent kill-switch forbids mention wakes."""

    if getattr(task, "assignee_type", None) != "agent":
        return False

    target_agent = assignee or getattr(task, "assigned_agent", None)
    target_agent_id = getattr(target_agent, "id", None) or getattr(task, "assigned_agent_id", None) or getattr(task, "assignee_id", None)
    if target_agent_id is None:
        return False

    target_handle = (getattr(target_agent, "name", None) or "").strip().lower() or None
    try:
        control_state = await agent_control_service.get_control_state(
            agent_id=target_agent_id,
            space_id=getattr(task, "space_id", None),
            agent_slug=target_handle,
        )
    except Exception:
        logger.exception(
            "Skipping task reminder mention wake after agent-control lookup failure task_id=%s agent_id=%s",
            getattr(task, "id", None),
            target_agent_id,
        )
        return True

    if control_state.is_disabled:
        logger.info(
            "Skipping task reminder mention wake for disabled agent task_id=%s agent_id=%s disabled_by=%s reason=%s",
            getattr(task, "id", None),
            target_agent_id,
            control_state.disabled_by,
            control_state.disabled_reason,
        )
        return True
    return False


def _task_owner_user_ids(task: Task) -> list[str]:
    owner_ids: list[str] = []

    def add(value) -> None:
        if value is None:
            return
        text = str(value)
        if text not in owner_ids:
            owner_ids.append(text)

    if getattr(task, "assigned_agent", None) is not None:
        add(getattr(task.assigned_agent, "user_id", None))
    if getattr(task, "assignee_type", None) == "user":
        add(getattr(task, "assignee_id", None))
    if getattr(task, "posted_by_user", None) is not None:
        add(getattr(task.posted_by_user, "id", None))
    elif getattr(task, "posted_by", None) is not None:
        add(getattr(task, "posted_by", None))
    if getattr(task, "posted_by_agent", None) is not None:
        add(getattr(task.posted_by_agent, "user_id", None))
    return owner_ids


async def _task_owner_users(db: AsyncSession, task: Task) -> list[User]:
    owner_ids = _task_owner_user_ids(task)
    if not owner_ids:
        return []
    users: list[User] = []
    seen: set[str] = set()
    for owner_id in owner_ids:
        if owner_id in seen:
            continue
        seen.add(owner_id)
        if getattr(task, "posted_by_user", None) is not None and str(task.posted_by_user.id) == owner_id:
            users.append(task.posted_by_user)
            continue
        try:
            user_ident = uuid.UUID(owner_id)
        except ValueError:
            continue
        user = await db.get(User, user_ident)
        if user is not None:
            users.append(user)
    return users


def _task_reminder_pause_reason(task: Task, now: datetime) -> str | None:
    """Return why normal work reminders must stop/pause for this task, else None."""

    work_status = (getattr(task, "work_status", None) or getattr(task, "status", None) or "").lower()
    queue_state = (getattr(task, "queue_state", None) or "").lower()
    if work_status in {"completed", "closed"}:
        return "completed"
    if work_status in {"cancelled", "canceled"}:
        return "cancelled"
    if getattr(task, "archived_at", None) is not None or queue_state == "archived":
        return "archived"
    if getattr(task, "superseded_by_task_id", None) is not None or queue_state == "superseded":
        return "superseded"
    if work_status == "blocked" or queue_state == "blocked":
        return "blocked"
    if queue_state == "deferred":
        return "deferred"
    snoozed_until = getattr(task, "snoozed_until", None)
    if queue_state == "snoozed" and (snoozed_until is None or snoozed_until > now):
        return "snoozed"
    if snoozed_until is not None and snoozed_until > now:
        return "snoozed"
    if queue_state == "inactive":
        return "inactive"
    if getattr(task, "stale_at", None) is not None or queue_state == "stale":
        return "stale"
    return None


async def _task_assignee(db: AsyncSession, task: Task) -> Agent | User | None:
    """Resolve ONLY the task's explicit assignee (agent or user) — NO fallback to the
    poster/owner. Used by the manual nudge so an unassigned task is rejected rather
    than notifying its creator."""
    if getattr(task, "assigned_agent", None) is not None:
        return task.assigned_agent
    assignee_id = getattr(task, "assignee_id", None)
    assignee_type = getattr(task, "assignee_type", None)
    if assignee_type == "user" and assignee_id is not None:
        return await db.get(User, assignee_id)
    if assignee_type == "agent" and assignee_id is not None:
        return await db.get(Agent, assignee_id)
    return None


async def _task_reminder_target_unavailable(task: Task, assignee: Agent | User | None, *, now: datetime) -> str | None:
    """Return why an agent assignee must not be woken by reminders, else None."""

    assignee_type = getattr(task, "assignee_type", None)
    legacy_agent_assignment = (
        getattr(task, "assigned_agent_id", None) is not None
        or getattr(task, "assigned_agent", None) is not None
    )
    if assignee_type != "agent" and not legacy_agent_assignment:
        return None
    target_agent = assignee or getattr(task, "assigned_agent", None)
    if target_agent is None:
        return "missing_agent_assignee"

    try:
        from app.core.agent_reliability import AgentPresence
        presence = await AgentPresence(core_redis_client).get_presence(str(target_agent.id))
    except Exception:
        logger.exception(
            "Skipping task reminder after agent presence lookup failure task_id=%s agent_id=%s",
            getattr(task, "id", None),
            getattr(target_agent, "id", None),
        )
        return "presence_lookup_failed"

    if include_agent_in_roster(target_agent, presence, now=now):
        return None
    return "agent_not_live_available"


async def _task_reminder_target(db: AsyncSession, task: Task, *, now: datetime | None = None) -> Agent | User | None:
    assignee = await _task_assignee(db, task)
    if assignee is not None:
        unavailable = await _task_reminder_target_unavailable(task, assignee, now=now or datetime.now(UTC))
        if unavailable is not None:
            logger.info(
                "Skipping task reminder for unavailable agent task_id=%s agent_id=%s reason=%s",
                getattr(task, "id", None),
                getattr(assignee, "id", None),
                unavailable,
            )
            return None
        return assignee
    owner_users = await _task_owner_users(db, task)
    if owner_users:
        return owner_users[0]
    if getattr(task, "posted_by_agent", None) is not None:
        return task.posted_by_agent
    return None


async def _emit_task_reminder(
    db: AsyncSession,
    task: Task,
    assignee,
    *,
    now: datetime,
    advance_schedule: bool = True,
    manual: bool = False,
    notifications: "NotificationsService | None" = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Emit exactly one task reminder for ``assignee``: durable activity card +
    notification record + SSE (task_reminder/task_activity).

    Shared by the polling loop and the manual ``nudge`` endpoint. The caller is
    responsible for committing the session.

    - advance_schedule=True (loop): recompute ``next_reminder_at`` for the next tick.
      The manual nudge passes False so an out-of-band fire never disturbs the cadence.
    - manual=True (nudge): do NOT increment ``reminder_count`` so a hand-fire never
      burns the policy's ``max_count`` budget.
    """
    notifications = notifications or NotificationsService(db, core_redis_client)
    alert_kind = kind or ("task_nudge" if manual else "task_reminder")
    payload = _task_notification_payload(task, alert_kind, target=assignee)
    message = _task_reminder_message(task, payload)
    db.add(message)
    await db.flush()

    event = await notifications.record_task_reminder(
        space_id=task.space_id,
        task=task,
        assignee=assignee,
        message_id=message.id,
    )
    event_id = event.get("id") if event else None
    payload = _task_notification_payload(task, alert_kind, event_id, target=assignee)
    payload["message_id"] = str(message.id)
    _add_task_alert_activity_binding(payload)
    _attach_task_alert_activity_binding(message, payload)
    payload["manual"] = manual
    await redis_sse_broker.publish(
        space_id=str(task.space_id),
        event=alert_kind,
        data=payload,
    )
    await redis_sse_broker.publish(
        space_id=str(task.space_id),
        event="task_activity",
        data=payload,
    )
    mention_wake = _task_reminder_mention_wake_payload(task, message, payload, now=now)
    if mention_wake is not None and not await _task_reminder_target_disabled(task, assignee):
        await redis_sse_broker.publish(
            space_id=str(task.space_id),
            event="mention",
            data=mention_wake,
        )
        mcp_mentions = [
            str(mention).lstrip("@")
            for mention in (mention_wake.get("mentions") or [])
            if mention and not _looks_like_uuid(str(mention).lstrip("@"))
        ]
        if not mcp_mentions and mention_wake.get("mentioned_agent"):
            mcp_mentions = [str(mention_wake["mentioned_agent"]).lstrip("@")]
        await publish_mention_event(
            space_id=str(task.space_id),
            message_id=str(message.id),
            content=str(mention_wake.get("content") or payload["content"]),
            mentions=list(dict.fromkeys(mcp_mentions)),
            sender_name=payload.get("action_envelope", {}).get("context", {}).get("source") or "task_reminder_scheduler",
            created_at=now.isoformat(),
            actor_roster_id=None,
        )
    task.last_reminded_at = now
    if not manual:
        task.reminder_count = (task.reminder_count or 0) + 1
    if advance_schedule:
        task.next_reminder_at = _next_reminder_at(task, now)
    task.updated_at = now
    return {"message_id": str(message.id), "event_id": event_id, "payload": payload}


def _next_reminder_at_after_count(task: Task, now: datetime, *, reminder_count_increment: int = 0) -> datetime | None:
    """Return the next fire time after applying a pending scheduler count update."""

    if reminder_count_increment <= 0:
        return _next_reminder_at(task, now)
    original_count = task.reminder_count
    try:
        task.reminder_count = (task.reminder_count or 0) + reminder_count_increment
        return _next_reminder_at(task, now)
    finally:
        task.reminder_count = original_count


def _task_reminder_audit_snapshot(
    task: Task,
    *,
    now: datetime,
    target: Agent | User,
    why_fired: str,
    grouped_count: int,
    suppressed_count: int = 0,
    required_next_action: str = "work_task_or_update_status",
    reminder_count_increment: int = 0,
) -> dict[str, Any]:
    next_fire = _next_reminder_at_after_count(task, now, reminder_count_increment=reminder_count_increment)
    target_fields = _target_descriptor(task, target)
    handle = target_fields.get("target_agent_handle") or target_fields.get("target_agent_name")
    target_owner = f"@{handle}" if handle else target_fields.get("target_id")
    return {
        "why_fired": why_fired,
        "grouped_count": grouped_count,
        "suppressed_count": suppressed_count,
        "target_owner": target_owner,
        "target_type": target_fields.get("target_type"),
        "target_id": target_fields.get("target_id"),
        "next_fire_at": next_fire.isoformat() if next_fire else None,
        "required_next_action": required_next_action,
        "audited_at": now.isoformat(),
    }


def _record_reminder_audit(
    task: Task,
    *,
    now: datetime,
    target: Agent | User,
    why_fired: str,
    grouped_count: int,
    suppressed_count: int = 0,
    required_next_action: str = "work_task_or_update_status",
    reminder_count_increment: int = 0,
) -> dict[str, Any]:
    metadata = dict(task.task_metadata) if isinstance(getattr(task, "task_metadata", None), dict) else {}
    snapshot = _task_reminder_audit_snapshot(
        task,
        now=now,
        target=target,
        why_fired=why_fired,
        grouped_count=grouped_count,
        suppressed_count=suppressed_count,
        required_next_action=required_next_action,
        reminder_count_increment=reminder_count_increment,
    )
    metadata["reminder_delivery"] = snapshot
    task.task_metadata = metadata
    return snapshot


def _suppress_task_reminder_burst(task: Task, *, now: datetime, target: Agent | User, grouped_count: int) -> dict[str, Any]:
    snapshot = _record_reminder_audit(
        task,
        now=now,
        target=target,
        why_fired="burst_suppressed",
        grouped_count=grouped_count,
        suppressed_count=1,
        required_next_action="digest_or_triage_grouped_reminders",
    )
    task.last_reminded_at = now
    task.next_reminder_at = _next_reminder_at(task, now)
    task.updated_at = now
    return snapshot


class TaskReminderDeliveryService:
    """Polls due active tasks and emits Redis notifications/SSE reminder events."""

    def __init__(self, *, poll_seconds: int | None = None, batch_size: int | None = None) -> None:
        self.poll_seconds = poll_seconds or int(os.getenv("TASK_REMINDER_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))
        self.batch_size = batch_size or int(os.getenv("TASK_REMINDER_BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))
        self.max_per_target_per_tick = int(
            os.getenv("TASK_REMINDER_MAX_PER_TARGET_PER_TICK", str(DEFAULT_MAX_REMINDERS_PER_TARGET_PER_TICK))
        )
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self.run_forever(), name="task-reminder-delivery")
        logger.info("Task reminder delivery loop started poll_seconds=%s batch_size=%s", self.poll_seconds, self.batch_size)

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_forever(self) -> None:
        while not self._stopping.is_set():
            try:
                async with AsyncSessionLocal() as db:
                    delivered = await self.deliver_due_reminders(db)
                    if delivered:
                        logger.info("Delivered %s task reminders", delivered)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Task reminder delivery loop failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def deliver_due_reminders(self, db: AsyncSession, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        readback = await get_task_reminder_pause_readback(now=now)
        if readback["paused"]:
            logger.info(
                "Task reminder delivery paused reason=%s detail=%s",
                readback.get("pause_reason"),
                readback.get("reason"),
            )
            return 0
        stmt = (
            select(Task)
            .options(selectinload(Task.assigned_agent), selectinload(Task.posted_by_user), selectinload(Task.posted_by_agent))
            .where(
                and_(
                    Task.work_status.notin_(("completed", "closed", "cancelled", "canceled", "blocked")),
                    or_(
                        Task.queue_state.is_(None),
                        Task.queue_state.notin_(("inactive", "blocked", "deferred", "archived", "superseded", "stale")),
                    ),
                    Task.stale_at.is_(None),
                    Task.archived_at.is_(None),
                    Task.superseded_by_task_id.is_(None),
                    Task.next_reminder_at.isnot(None),
                    Task.next_reminder_at <= now,
                    or_(Task.snoozed_until.is_(None), Task.snoozed_until <= now),
                )
            )
            .order_by(Task.next_reminder_at.asc())
            .limit(self.batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        if not tasks:
            return 0

        notifications = NotificationsService(db, core_redis_client)
        delivered = 0
        target_counts: dict[str, int] = {}
        target_group_sizes: dict[str, int] = {}
        resolved: list[tuple[Task, Agent | User, str]] = []
        for task in tasks:
            pause_reason = _task_reminder_pause_reason(task, now)
            if pause_reason is not None:
                logger.debug("Skipping task reminder task_id=%s reason=%s", task.id, pause_reason)
                continue
            assignee = await _task_reminder_target(db, task, now=now)
            if assignee is None:
                continue
            target_key = _reminder_target_key(task, assignee)
            resolved.append((task, assignee, target_key))
            target_group_sizes[target_key] = target_group_sizes.get(target_key, 0) + 1

        for task, assignee, target_key in resolved:
            current_count = target_counts.get(target_key, 0)
            grouped_count = target_group_sizes.get(target_key, 1)
            if current_count >= self.max_per_target_per_tick:
                _suppress_task_reminder_burst(task, now=now, target=assignee, grouped_count=grouped_count)
                logger.info(
                    "Suppressed burst task reminder task_id=%s target_key=%s grouped_count=%s max_per_tick=%s",
                    task.id,
                    target_key,
                    grouped_count,
                    self.max_per_target_per_tick,
                )
                continue
            _record_reminder_audit(
                task,
                now=now,
                target=assignee,
                why_fired="due_until_done",
                grouped_count=grouped_count,
                suppressed_count=max(0, grouped_count - self.max_per_target_per_tick),
                reminder_count_increment=1,
            )
            await _emit_task_reminder(
                db,
                task,
                assignee,
                now=now,
                advance_schedule=True,
                manual=False,
                notifications=notifications,
            )
            target_counts[target_key] = current_count + 1
            delivered += 1

        await db.commit()
        return delivered


async def notify_task_assignment(db: AsyncSession, task: Task) -> dict[str, Any] | None:
    """Emit the standard task→agent envelope when a task is newly assigned."""

    if task.assigned_agent is None:
        return None
    assignee = task.assigned_agent
    notifications = NotificationsService(db, core_redis_client)
    payload = _task_notification_payload(task, "task_assignment", target=assignee)
    message = _task_reminder_message(task, payload)
    db.add(message)
    await db.flush()

    event = await notifications.record_assignment(
        space_id=task.space_id,
        task=task,
        assignee=assignee,
        actor_agent_id=getattr(task, "assigned_by_id", None),
        message_id=message.id,
    )
    event_id = event.get("id") if event else None
    payload = _task_notification_payload(task, "task_assignment", event_id, target=assignee)
    payload["message_id"] = str(message.id)
    _add_task_alert_activity_binding(payload)
    _attach_task_alert_activity_binding(message, payload)
    await redis_sse_broker.publish(space_id=str(task.space_id), event="task_assignment", data=payload)
    await redis_sse_broker.publish(space_id=str(task.space_id), event="task_activity", data=payload)
    mention_wake = _task_reminder_mention_wake_payload(task, message, payload, now=datetime.now(UTC))
    if mention_wake is not None and not await _task_reminder_target_disabled(task, assignee):
        await redis_sse_broker.publish(space_id=str(task.space_id), event="mention", data=mention_wake)
        mcp_mentions = [
            str(mention).lstrip("@")
            for mention in (mention_wake.get("mentions") or [])
            if mention and not _looks_like_uuid(str(mention).lstrip("@"))
        ]
        await publish_mention_event(
            space_id=str(task.space_id),
            message_id=str(message.id),
            content=str(mention_wake.get("content") or payload["content"]),
            mentions=list(dict.fromkeys(mcp_mentions)),
            sender_name="task_assignment",
            created_at=datetime.now(UTC).isoformat(),
            actor_roster_id=None,
        )
    return {"message_id": str(message.id), "event_id": event_id, "payload": payload}


async def notify_task_completed(db: AsyncSession, task: Task) -> list[dict[str, str]]:
    """Notify task creator/owner exactly once when a task completes."""
    metadata = dict(task.task_metadata) if isinstance(task.task_metadata, dict) else {}
    if metadata.get("completion_notified_at"):
        return []

    targets: list[Agent | User] = []
    for user in await _task_owner_users(db, task):
        targets.append(user)
    if task.posted_by_agent is not None:
        targets.append(task.posted_by_agent)

    # Also notify assigned agent for MCP/headless surfaces; owner users above make it widget-visible.
    if task.assigned_agent is not None and task.assigned_agent not in targets:
        targets.append(task.assigned_agent)

    notifications = NotificationsService(db, core_redis_client)
    events: list[dict[str, str]] = []
    for target in targets:
        event = await notifications.record_task_completed(space_id=task.space_id, task=task, target=target)
        if event:
            events.append(event)

    metadata["completion_notified_at"] = datetime.now(UTC).isoformat()
    task.task_metadata = metadata
    await redis_sse_broker.publish(
        space_id=str(task.space_id),
        event="task_completed",
        data=_task_notification_payload(task, "task_completed", events[0]["id"] if events else None),
    )
    await redis_sse_broker.publish(
        space_id=str(task.space_id),
        event="task_activity",
        data=_task_notification_payload(task, "task_completed", events[0]["id"] if events else None),
    )
    return events


async def notify_task_blocked(db: AsyncSession, task: Task) -> list[dict[str, str]]:
    """Notify task owner users when a task becomes blocked."""
    notifications = NotificationsService(db, core_redis_client)
    events: list[dict[str, str]] = []
    for target in await _task_owner_users(db, task):
        event = await notifications.record_task_blocked(space_id=task.space_id, task=task, target=target)
        if event:
            events.append(event)

    await redis_sse_broker.publish(
        space_id=str(task.space_id),
        event="task_blocked",
        data=_task_notification_payload(task, "task_blocked", events[0]["id"] if events else None),
    )
    await redis_sse_broker.publish(
        space_id=str(task.space_id),
        event="task_activity",
        data=_task_notification_payload(task, "task_blocked", events[0]["id"] if events else None),
    )
    return events


task_reminder_delivery_service = TaskReminderDeliveryService()
