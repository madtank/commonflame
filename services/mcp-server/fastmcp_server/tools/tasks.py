"""Tasks tool for FastMCP server.

Provides task creation, listing, updating, and retrieval.
All operations route through backend API (ROUTER-001).
Uses CurrentAccessToken() DI to forward backend-issued JWTs.
"""

import re
from typing import Annotated, Any, Literal, Optional

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.tools.tool import ToolResult
from starlette.requests import Request

from fastmcp_server.api_client import (
    api_request_with_context,
    extract_agent_context,
    user_request_space_payload,
    user_request_space_params,
)
from fastmcp_server.backend_routes import (
    TASKS_COLLECTION_PATH,
    TASKS_WRITE_COLLECTION_PATH,
    task_read_item_path,
    task_write_item_path,
)
from fastmcp_server.mcp_ui import (
    bounded_write_annotations,
    build_notice,
    build_action,
    build_tool_output,
    tool_app_config,
    tool_meta,
    tool_output_schema,
    widget_tool_result,
)

# Reads and writes use the public v1 task-management routes. The backend keeps
# /api/tasks as a legacy alias for compatibility, but MCP should not depend on
# the legacy mount for normal task creation or updates.
_SUMMARY_MAX_LEN = 120
_FILTER_ALIASES = {
    "all_tasks": "all",
    "all-tasks": "all",
    "all tasks": "all",
    "open": "all",
    "open_tasks": "all",
    "open-tasks": "all",
}
_STATUS_ALIASES = {
    "pending": "open",
    "closed": "cancelled",
}


def _strip_markdown_summary(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(^|\n)\s{0,3}#{1,6}\s*", " ", text)
    text = re.sub(r"(^|\n)\s*[-*+]\s+", " ", text)
    text = re.sub(r"(^|\n)\s*\d+\.\s+", " ", text)
    text = re.sub(r"[*_~>#]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_summary(text: str, *, limit: int = _SUMMARY_MAX_LEN) -> str:
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip(" ,;:-") + "..."


def _normalize_list_filter(value: Any) -> str | None:
    raw = _first_non_empty_string(value)
    if not raw:
        return None
    normalized = raw.strip().lower()
    return _FILTER_ALIASES.get(normalized, normalized)


def _normalize_task_status(value: Any) -> str | None:
    raw = _first_non_empty_string(value)
    if not raw:
        return None
    normalized = raw.strip().lower()
    return _STATUS_ALIASES.get(normalized, normalized)


def _task_status_value(task: dict[str, Any]) -> str:
    return (
        _first_non_empty_string(task.get("work_status"), task.get("status"))
        or "pending"
    )


def _task_status_matches(actual: Any, expected: str | None) -> bool:
    actual_normalized = _normalize_task_status(actual)
    expected_normalized = _normalize_task_status(expected)
    return bool(actual_normalized and actual_normalized == expected_normalized)


def task_status_write_item_path(task_id: str) -> str:
    return f"{task_write_item_path(task_id)}/status"


def task_nudge_write_item_path(task_id: str) -> str:
    return f"{task_write_item_path(task_id)}/nudge"


def _derive_task_summary(task: dict[str, Any]) -> str | None:
    candidate = _first_non_empty_string(
        task.get("summary"),
        task.get("ai_summary"),
        task.get("description"),
        task.get("title"),
    )
    if not candidate:
        return None
    cleaned = _strip_markdown_summary(candidate)
    if not cleaned:
        return None
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
    preferred = first_sentence or cleaned
    return _truncate_summary(preferred)


def _first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def _normalize_handle(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lstrip("@")
    return cleaned or None


def _humanize_label(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        return None
    return cleaned[:1].upper() + cleaned[1:]


def _extract_task_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = result.get("tasks")
    if isinstance(tasks, list):
        return [task for task in tasks if isinstance(task, dict)]

    items = result.get("items")
    if isinstance(items, list):
        return [task for task in items if isinstance(task, dict)]

    task = result.get("task")
    if isinstance(task, dict):
        return [task]

    if isinstance(result.get("id"), str) and "title" in result:
        return [result]

    return []


def _normalize_assignee(task: dict[str, Any]) -> dict[str, str] | None:
    assignee = task.get("assignee")
    if not isinstance(assignee, dict):
        assignee = task.get("assigned_agent")
    if isinstance(assignee, dict):
        assignee_handle = _normalize_handle(
            _first_non_empty_string(
                assignee.get("handle"),
                assignee.get("username"),
                assignee.get("agent_name"),
                assignee.get("name"),
            )
        )
        assignee_name = _first_non_empty_string(
            assignee.get("display_name"), assignee.get("name")
        )
        assignee_id = _first_non_empty_string(assignee.get("id"))
        if assignee_handle or assignee_name or assignee_id:
            payload: dict[str, str] = {}
            if assignee_id:
                payload["id"] = assignee_id
            assignee_type = _first_non_empty_string(assignee.get("type"), assignee.get("assignee_type"))
            if assignee_type:
                payload["type"] = assignee_type
            if assignee_handle:
                payload["handle"] = assignee_handle
            if assignee_name:
                payload["display_name"] = assignee_name
            return payload

    assignee_handle = _normalize_handle(
        _first_non_empty_string(
            task.get("assigned_agent_handle"),
            task.get("assigned_agent_name"),
            task.get("assignee_handle"),
            task.get("assignee_name"),
        )
    )
    assignee_id = _first_non_empty_string(
        task.get("assigned_agent_id"), task.get("assignee_id")
    )
    if assignee_handle or assignee_id:
        payload = {}
        if assignee_id:
            payload["id"] = assignee_id
        assignee_type = _first_non_empty_string(task.get("assignee_type"))
        if assignee_type:
            payload["type"] = assignee_type
        elif task.get("assigned_agent_id"):
            payload["type"] = "agent"
        if assignee_handle:
            payload["handle"] = assignee_handle
        name = _first_non_empty_string(
            task.get("assigned_agent_display_name"), task.get("assignee_display_name")
        )
        if name:
            payload["display_name"] = name
        return payload
    return None


def _normalize_reminder(task: dict[str, Any]) -> dict[str, Any] | None:
    reminder = task.get("reminder")
    if not isinstance(reminder, dict):
        reminder = task.get("reminder_policy")
    if not isinstance(reminder, dict):
        reminder = {}

    payload: dict[str, Any] = {}
    policy = _first_non_empty_string(
        reminder.get("policy"),
        reminder.get("cadence"),
        task.get("reminder_policy"),
        task.get("reminder_cadence"),
    )
    status = _first_non_empty_string(
        reminder.get("status"),
        task.get("reminder_status"),
    )
    next_fire_at = _first_non_empty_string(
        reminder.get("next_fire_at"),
        reminder.get("next_reminder_at"),
        task.get("next_reminder_at"),
        task.get("reminder_next_fire_at"),
    )
    last_fired_at = _first_non_empty_string(
        reminder.get("last_fired_at"),
        reminder.get("last_reminder_at"),
        task.get("last_reminder_at"),
        task.get("reminder_last_fired_at"),
    )
    snoozed_until = _first_non_empty_string(reminder.get("snoozed_until"), task.get("snoozed_until"))
    state = _first_non_empty_string(reminder.get("state"), task.get("reminder_state"))
    last_reminded_at = _first_non_empty_string(reminder.get("last_reminded_at"), task.get("last_reminded_at"))
    reminder_count = task.get("reminder_count", reminder.get("reminder_count"))
    cadence_minutes = task.get(
        "reminder_cadence_minutes",
        task.get("reminder_interval_minutes", reminder.get("cadence_minutes")),
    )
    max_count = task.get("reminder_max_count", reminder.get("max_count"))
    until_done = task.get("reminder_until_done", reminder.get("until_done"))
    history = reminder.get("history") or task.get("reminder_history")

    if policy:
        payload["policy"] = policy
    if status:
        payload["status"] = status
    if state:
        payload["state"] = state
    if next_fire_at:
        payload["next_fire_at"] = next_fire_at
    if last_fired_at:
        payload["last_fired_at"] = last_fired_at
    if last_reminded_at:
        payload["last_reminded_at"] = last_reminded_at
    if snoozed_until:
        payload["snoozed_until"] = snoozed_until
    if reminder_count is not None:
        payload["reminder_count"] = reminder_count
        payload.setdefault("fire_count", reminder_count)
    if cadence_minutes is not None:
        payload["cadence_minutes"] = cadence_minutes
    if max_count is not None:
        payload["max_count"] = max_count
    if until_done is not None:
        payload["until_done"] = until_done
    if isinstance(history, list):
        payload["history"] = history

    return payload or None


def _normalize_provenance(
    task: dict[str, Any], result: dict[str, Any] | None = None
) -> dict[str, str] | None:
    source = task
    if result and isinstance(result.get("provenance"), dict):
        source = result["provenance"]

    if isinstance(source.get("provenance"), dict):
        source = source["provenance"]

    kind = _first_non_empty_string(source.get("kind"), source.get("provenance_kind"))
    source_id = _first_non_empty_string(
        source.get("id"),
        source.get("message_id"),
        source.get("contract_id"),
        source.get("source_id"),
    )
    label = _first_non_empty_string(
        source.get("label"),
        source.get("title"),
        source.get("source_label"),
    )

    if not kind and task.get("message_id"):
        kind = "message"
        source_id = _first_non_empty_string(task.get("message_id"))
        label = label or "Linked message"

    if kind and source_id:
        payload = {"kind": kind, "id": source_id}
        if label:
            payload["label"] = label
        return payload
    return None


def _copy_task_reference_fields(task: dict[str, Any], payload: dict[str, Any]) -> None:
    for source_key in ("task_ref", "task_deep_link", "task_display_id", "display_id", "space_id"):
        value = _first_non_empty_string(task.get(source_key))
        if value:
            payload[source_key] = value

    task_reference = task.get("task_reference")
    if isinstance(task_reference, dict):
        normalized_reference: dict[str, str] = {}
        copyable_ref = _first_non_empty_string(task_reference.get("copyable_ref"))
        deep_link = _first_non_empty_string(task_reference.get("deep_link"))
        if copyable_ref:
            normalized_reference["copyable_ref"] = copyable_ref
        if deep_link:
            normalized_reference["deep_link"] = deep_link
        if normalized_reference:
            payload["task_reference"] = normalized_reference


def _normalize_task_collection_item(task: dict[str, Any]) -> dict[str, Any]:
    status = _task_status_value(task)
    item: dict[str, Any] = {
        "id": _first_non_empty_string(task.get("id")) or "",
        "title": _first_non_empty_string(task.get("title")) or "Untitled",
        "status": status,
        "status_label": _humanize_label(status) or "Pending",
        "updated_at": _first_non_empty_string(
            task.get("updated_at"), task.get("created_at")
        )
        or "",
    }

    priority = _first_non_empty_string(task.get("priority"))
    if priority:
        item["priority"] = priority.lower()

    _copy_task_reference_fields(task, item)

    assignee = _normalize_assignee(task)
    if assignee:
        item["assignee"] = assignee

    summary = _first_non_empty_string(task.get("summary"), task.get("description"))
    if summary:
        item["summary"] = summary
    elif ai_summary := _derive_task_summary(task):
        item["summary"] = ai_summary

    due_at = _first_non_empty_string(task.get("due_at"), task.get("deadline"))
    if due_at:
        item["due_at"] = due_at

    reminder = _normalize_reminder(task)
    if reminder:
        item["reminder"] = reminder

    provenance = _normalize_provenance(task)
    if provenance:
        item["provenance"] = provenance

    return item


def _normalize_activity_items(
    result: dict[str, Any], task: dict[str, Any]
) -> list[dict[str, Any]]:
    raw_activity = result.get("activity")
    if not isinstance(raw_activity, list):
        raw_activity = task.get("activity")
    if not isinstance(raw_activity, list):
        return []

    items: list[dict[str, Any]] = []
    for entry in raw_activity:
        if not isinstance(entry, dict):
            continue
        activity_id = _first_non_empty_string(entry.get("id"), entry.get("event_id"))
        timestamp = _first_non_empty_string(
            entry.get("timestamp"), entry.get("created_at"), entry.get("updated_at")
        )
        summary = _first_non_empty_string(
            entry.get("summary"), entry.get("message"), entry.get("description")
        )
        actor_label = _first_non_empty_string(
            entry.get("actor_label"), entry.get("actor"), entry.get("user")
        )
        payload: dict[str, Any] = {}
        if activity_id:
            payload["id"] = activity_id
        if timestamp:
            payload["timestamp"] = timestamp
        if actor_label:
            payload["actor_label"] = actor_label
        if summary:
            payload["summary"] = summary
        if payload:
            items.append(payload)
    return items


def _normalize_artifacts(
    result: dict[str, Any], task: dict[str, Any]
) -> list[dict[str, Any]]:
    raw_artifacts = result.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raw_artifacts = task.get("artifacts")
    if not isinstance(raw_artifacts, list):
        return []

    items: list[dict[str, Any]] = []
    for artifact in raw_artifacts:
        if not isinstance(artifact, dict):
            continue
        artifact_id = _first_non_empty_string(
            artifact.get("id"), artifact.get("artifact_id")
        )
        label = _first_non_empty_string(
            artifact.get("label"), artifact.get("name"), artifact.get("title")
        )
        artifact_type = _first_non_empty_string(
            artifact.get("type"), artifact.get("kind")
        )
        if not artifact_id and not label:
            continue
        payload: dict[str, Any] = {}
        if artifact_id:
            payload["id"] = artifact_id
        if label:
            payload["label"] = label
        if artifact_type:
            payload["type"] = artifact_type
        items.append(payload)
    return items


def _normalize_task_detail_data(result: dict[str, Any]) -> dict[str, Any]:
    task = result.get("task", result)
    if not isinstance(task, dict):
        task = {}

    status = _task_status_value(task)
    detail_task: dict[str, Any] = {
        "id": _first_non_empty_string(task.get("id")) or "",
        "title": _first_non_empty_string(task.get("title")) or "Untitled",
        "status": status,
        "status_label": _humanize_label(status) or "Pending",
        "updated_at": _first_non_empty_string(
            task.get("updated_at"), task.get("created_at")
        )
        or "",
    }

    description = _first_non_empty_string(task.get("description"))
    if description:
        detail_task["description"] = description
    summary = _first_non_empty_string(task.get("summary"))
    if summary:
        detail_task["summary"] = summary
    ai_summary = _derive_task_summary(task)
    if ai_summary:
        detail_task["ai_summary"] = ai_summary

    priority = _first_non_empty_string(task.get("priority"))
    if priority:
        detail_task["priority"] = priority.lower()

    _copy_task_reference_fields(task, detail_task)

    assignee = _normalize_assignee(task)
    if assignee:
        detail_task["assignee"] = assignee

    created_at = _first_non_empty_string(task.get("created_at"))
    if created_at:
        detail_task["created_at"] = created_at

    due_at = _first_non_empty_string(task.get("due_at"), task.get("deadline"))
    if due_at:
        detail_task["due_at"] = due_at

    reminder = _normalize_reminder(task)
    if reminder:
        detail_task["reminder"] = reminder

    requirements = task.get("requirements")
    if isinstance(requirements, dict):
        detail_task["requirements"] = requirements

    data: dict[str, Any] = {"task": detail_task}

    provenance = _normalize_provenance(task, result)
    if provenance:
        data["provenance"] = provenance

    activity = _normalize_activity_items(result, task)
    if activity:
        data["activity"] = activity

    artifacts = _normalize_artifacts(result, task)
    if artifacts:
        data["artifacts"] = artifacts

    return data


def _task_collection_fallback_text(items: list[dict[str, Any]], total: int) -> str:
    if not items:
        return "No tasks found."
    first = items[0]
    title = first.get("title") or "Untitled"
    if total == 1:
        return f"1 task: {title}"
    return f"{total} tasks. First: {title}"


def _task_detail_fallback_text(data: dict[str, Any]) -> str:
    task = data.get("task", {})
    title = task.get("title") or "Untitled"
    status_label = task.get("status_label")
    if status_label:
        return f"{title} ({status_label})"
    return str(title)


def _task_collection_actions() -> list[dict[str, Any]]:
    return [
        build_action(
            "create-task",
            "New task",
            kind="tool",
            target="tasks",
            args={"action": "create"},
            style="primary",
            enabled=True,
            idempotent=False,
        )
    ]


def _task_detail_actions(
    task_id: str, status: str, provenance: dict[str, Any] | None
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        build_action(
            "show-all-tasks",
            "Show all tasks",
            kind="tool",
            target="tasks",
            args={"action": "list"},
            style="secondary",
            enabled=True,
            idempotent=True,
        )
    ]
    if task_id and status != "completed":
        actions.append(
            build_action(
                "mark-complete",
                "Mark complete",
                kind="tool",
                target="tasks",
                args={"action": "update", "task_id": task_id, "status": "completed"},
                style="primary",
                enabled=True,
                requires_confirmation=False,
                idempotent=True,
            )
        )
    if provenance and provenance.get("id"):
        actions.append(
            build_action(
                "open-provenance",
                "Open linked item",
                kind="nav",
                target=f"{provenance.get('kind', 'item')}:{provenance['id']}",
                style="secondary",
                enabled=True,
            )
        )
    return actions


def _tasks_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    filter_value: str | None = None,
    status_value: str | None = None,
    space_id_value: str | None = None,
) -> ToolResult:
    if isinstance(result, dict) and result.get("error"):
        return widget_tool_result(
            "tasks", content=result["error"], structured_content=result
        )

    if action == "list":
        items = [
            _normalize_task_collection_item(task)
            for task in _extract_task_items(result)
        ]
        structured = build_tool_output(
            "task_collection",
            2,
            "empty" if not items else "ready",
            {
                "scope": {
                    key: value
                    for key, value in {
                        "filter": filter_value,
                        "status": status_value,
                        "space_id": space_id_value,
                    }.items()
                    if value
                },
                "items": items,
                "total": result.get("total", result.get("count", len(items))),
            },
            actions=_task_collection_actions(),
        )
        return widget_tool_result(
            "tasks",
            action=action,
            content=_task_collection_fallback_text(items, structured["data"]["total"]),
            structured_content=structured,
        )

    if action in {"get", "create", "update", "nudge"}:
        data = _normalize_task_detail_data(result)
        if space_id_value:
            data.setdefault("scope", {})["space_id"] = space_id_value
        task = data.get("task", {})
        structured = build_tool_output(
            "task_detail",
            2,
            "ready",
            data,
            actions=_task_detail_actions(
                task.get("id", ""), task.get("status", ""), data.get("provenance")
            ),
        )
        if action == "create":
            if result.get("assignment_failed"):
                structured["notice"] = build_notice(
                    "Task created, but assignment failed. You can retry assignment from the task detail.",
                    severity="warning",
                    code="task_created_assignment_failed",
                )
                structured["data"]["assignment_error"] = result.get("assignment_error")
            else:
                structured["notice"] = build_notice("Task created.", code="task_created")
        elif action == "update":
            structured["notice"] = build_notice("Task updated.", code="task_updated")
        elif action == "nudge":
            structured["notice"] = build_notice(
                "Reminder nudge sent to the task assignee.",
                code="task_reminder_nudged",
            )
        return widget_tool_result(
            "tasks",
            action=action,
            content=_task_detail_fallback_text(data),
            structured_content=structured,
        )

    tasks = _extract_task_items(result)
    structured = {
        "tasks": tasks,
        "total": result.get("total", result.get("count", len(tasks))),
    }
    return widget_tool_result("tasks", content=result, structured_content=structured)


def _ctx_for_requested_space(ctx: dict[str, Any], requested_space_id: str | None) -> dict[str, Any]:
    """Honor explicit task space selection for user-authored calls only.

    Agent/scoped runtime sessions remain bound to signed auth context; browser/user
    sessions may target the panel-selected space and backend membership checks stay
    authoritative.
    """
    requested = str(requested_space_id or "").strip()
    if requested and ctx.get("principal_type") == "user":
        return {**ctx, "space_id": requested}
    return ctx


def _reminder_write_payload(
    action: str | None,
    cadence_minutes: int | None,
    max_count: int | None,
    until_done: bool | None,
    next_fire_at: str | None,
    snoozed_until: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"action": action or "schedule"}
    if cadence_minutes is not None:
        payload["cadence_minutes"] = cadence_minutes
    if max_count is not None:
        payload["max_count"] = max_count
    if until_done is not None:
        payload["until_done"] = until_done
    if next_fire_at is not None:
        payload["next_fire_at"] = next_fire_at
    if action == "snooze" and snoozed_until is not None:
        payload["snoozed_until"] = snoozed_until
    return payload


def _reminder_pause_payload(paused: bool, reason: str | None) -> dict[str, Any]:
    return {"global": {"paused": paused, "reason": reason}}


def register_tasks_tool(mcp: FastMCP):
    @mcp.tool(
        annotations=bounded_write_annotations(),
        app=tool_app_config("tasks"),
        meta=tool_meta("tasks"),
        output_schema=tool_output_schema("tasks"),
    )
    async def tasks(
        action: Annotated[
            Literal["list", "create", "update", "get", "nudge", "reminder_pause"],
            Field(
                default="list",
                description=(
                    "Task action. Use list to browse, create for a new task "
                    "(requires title), get/update for a single task (require "
                    "task_id), nudge to fire a task's reminder now, and "
                    "reminder_pause to read or set the space reminder mute."
                ),
            ),
        ] = "list",
        task_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Task ID. Required for get, update, and nudge.",
            ),
        ] = None,
        title: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Short task title. Required for create; optional on update.",
            ),
        ] = None,
        description: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Longer task description or acceptance notes.",
            ),
        ] = None,
        requirements: Annotated[
            Optional[dict],
            Field(
                default=None,
                description="Structured requirements object stored with the task.",
            ),
        ] = None,
        priority: Annotated[
            Optional[str],
            Field(
                default=None,
                description='Task priority such as "low", "medium", or "high".',
            ),
        ] = None,
        status: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Task status. On update, sets the new status (e.g. pending, "
                    "in_progress, completed). On list, filters by status."
                ),
            ),
        ] = None,
        deadline: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Due date/time in ISO 8601 (e.g. 2026-06-20T17:00:00Z).",
            ),
        ] = None,
        assignee_type: Annotated[
            Optional[Literal["user", "agent"]],
            Field(
                default=None,
                description="Whether the assignee in assignee_id is a user or an agent.",
            ),
        ] = None,
        assignee_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="ID of the user or agent to assign; pair with assignee_type.",
            ),
        ] = None,
        assigned_agent_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Legacy agent-assignment field; prefer assignee_type + assignee_id.",
            ),
        ] = None,
        completed_at: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Completion timestamp (ISO 8601) recorded on update.",
            ),
        ] = None,
        reminder_action: Annotated[
            Optional[Literal["schedule", "cancel", "snooze"]],
            Field(
                default=None,
                description=(
                    "Reminder control: schedule (with reminder_cadence_minutes), "
                    "snooze (with snoozed_until), or cancel to turn the reminder off."
                ),
            ),
        ] = None,
        reminder_cadence_minutes: Annotated[
            Optional[int],
            Field(
                default=None,
                description="Minutes between recurring reminder deliveries when scheduling.",
            ),
        ] = None,
        reminder_max_count: Annotated[
            Optional[int],
            Field(
                default=None,
                description="Maximum number of reminder deliveries before stopping.",
            ),
        ] = None,
        reminder_until_done: Annotated[
            Optional[bool],
            Field(
                default=None,
                description="When true, keep reminding until the task is completed.",
            ),
        ] = None,
        next_fire_at: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Explicit next reminder time (ISO 8601), e.g. for snooze.",
            ),
        ] = None,
        snoozed_until: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Snooze the reminder until this time (ISO 8601). Ignored "
                    "when reminder_action is also set."
                ),
            ),
        ] = None,
        reminder_paused: Annotated[
            Optional[bool],
            Field(
                default=None,
                description=(
                    "For action=reminder_pause, true mutes recurring reminder "
                    "delivery for the current space; false unmutes it. Omit to read."
                ),
            ),
        ] = None,
        reminder_pause_reason: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Optional audit reason for action=reminder_pause.",
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(default=50, ge=1, le=200, description="Maximum tasks to return for list."),
        ] = 50,
        offset: Annotated[
            int,
            Field(default=0, ge=0, description="Result offset for paging list results."),
        ] = 0,
        filter: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    'List filter; use "my_tasks" to show only tasks assigned '
                    "to the caller."
                ),
            ),
        ] = None,
        space_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Accepted for MCP client compatibility; the active space "
                    "is resolved from the authenticated session."
                ),
            ),
        ] = None,
        # DI params (hidden from MCP schema):
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> ToolResult | dict:
        """Manage tasks. Create, list, update, and track work items.

        Actions:
        - list: List tasks (use filter='my_tasks' for assigned tasks)
        - create: Create a new task (requires title)
        - update: Update a task (requires task_id)
        - get: Get a specific task (requires task_id)
        - nudge: Fire the task's reminder to its assignee right now (requires
          task_id). Works even while recurring reminder delivery is paused and
          does not consume the reminder policy's max_count budget.
        - reminder_pause: Read or set the current space's recurring-reminder
          mute without mutating individual task schedules.

        Reminder scheduling uses the canonical widget contract:
        reminder_action="schedule" with reminder_cadence_minutes/next_fire_at,
        reminder_action="snooze" with snoozed_until, or reminder_action="cancel"
        to turn reminders off. Top-level snoozed_until is intentionally ignored
        unless reminder_action="snooze" so schedule/cancel writes stay
        unambiguous. Legacy backend readback fields such as next_reminder_at are
        still normalized into reminder.next_fire_at.
        """
        ctx = extract_agent_context(token, request)
        ctx = _ctx_for_requested_space(ctx, space_id)
        normalized_filter = _normalize_list_filter(filter)
        normalized_status = _normalize_task_status(status)

        if action == "list":
            params = {"limit": limit, "offset": offset}
            if normalized_filter:
                params["filter"] = normalized_filter
            if normalized_status:
                params["status"] = normalized_status
            # Agent sessions rely on signed/X-Space-Id context. User widget
            # replays need the explicit API-first space override because their
            # JWT session space can lag behind the active panel space.
            params = user_request_space_params(ctx, params)
            result = await api_request_with_context(
                ctx,
                "GET",
                TASKS_COLLECTION_PATH,
                params=params,
            )
            return _tasks_widget_result(
                result,
                action,
                filter_value=normalized_filter,
                status_value=normalized_status,
                space_id_value=ctx.get("space_id"),
            )

        if action == "reminder_pause":
            if reminder_paused is None:
                result = await api_request_with_context(
                    ctx,
                    "GET",
                    f"{TASKS_COLLECTION_PATH}/reminders/pause",
                )
            else:
                result = await api_request_with_context(
                    ctx,
                    "PUT",
                    f"{TASKS_COLLECTION_PATH}/reminders/pause",
                    json_data=_reminder_pause_payload(
                        reminder_paused,
                        reminder_pause_reason,
                    ),
                )
            structured = build_tool_output(
                "task_reminder_pause",
                1,
                "error" if isinstance(result, dict) and result.get("error") else "ready",
                result if isinstance(result, dict) else {"value": result},
            )
            if reminder_paused is not None and structured["state"] != "error":
                structured["notice"] = build_notice(
                    "Task reminders muted." if reminder_paused else "Task reminders unmuted.",
                    code="task_reminder_pause_updated",
                )
            return widget_tool_result(
                "tasks",
                action=action,
                content=(
                    "Task reminder pause state updated."
                    if reminder_paused is not None
                    else "Task reminder pause state."
                ),
                structured_content=structured,
            )

        if action == "create":
            if not title:
                return {"error": "'title' required for create action"}
            payload = {"title": title}
            if description is not None:
                payload["description"] = description
            if requirements is not None:
                payload["requirements"] = requirements
            if priority is not None:
                payload["priority"] = priority
            if deadline is not None:
                payload["deadline"] = deadline
            if assignee_type is not None:
                payload["assignee_type"] = assignee_type
            if assignee_id is not None:
                payload["assignee_id"] = assignee_id
            if assigned_agent_id is not None:
                payload["assigned_agent_id"] = assigned_agent_id
            cadence_minutes = reminder_cadence_minutes
            if reminder_action in {"schedule", "snooze"} or cadence_minutes is not None:
                payload["reminder"] = _reminder_write_payload(
                    reminder_action,
                    cadence_minutes,
                    reminder_max_count,
                    reminder_until_done,
                    next_fire_at,
                    snoozed_until,
                )
            elif reminder_action == "cancel":
                payload["reminder"] = {"action": "cancel"}
            payload = user_request_space_payload(ctx, payload)
            result = await api_request_with_context(
                ctx,
                "POST",
                TASKS_WRITE_COLLECTION_PATH,
                json_data=payload,
            )
            if assigned_agent_id and not result.get("error"):
                task_data = result.get("task", result)
                created_id = task_data.get("id") or task_data.get("task_id") or ""
                if created_id:
                    assigned = await api_request_with_context(
                        ctx,
                        "PUT",
                        task_write_item_path(str(created_id)),
                        json_data={"assigned_agent_id": assigned_agent_id},
                    )
                    if not assigned.get("error"):
                        result = assigned
                    else:
                        result = {
                            **result,
                            "assignment_failed": True,
                            "assignment_error": assigned.get("detail") or assigned.get("error"),
                        }
            return _tasks_widget_result(result, action, space_id_value=ctx.get("space_id"))

        if action == "update":
            if not task_id:
                return {"error": "'task_id' required for update action"}
            field_payload = {}
            if title is not None:
                field_payload["title"] = title
            if description is not None:
                field_payload["description"] = description
            if requirements is not None:
                field_payload["requirements"] = requirements
            if priority is not None:
                field_payload["priority"] = priority
            status_payload = {}
            if normalized_status is not None:
                status_payload["status"] = normalized_status
            if deadline is not None:
                field_payload["deadline"] = deadline
            if assignee_type is not None:
                field_payload["assignee_type"] = assignee_type
            if assignee_id is not None:
                field_payload["assignee_id"] = assignee_id
            if assigned_agent_id is not None:
                field_payload["assigned_agent_id"] = assigned_agent_id
            cadence_minutes = reminder_cadence_minutes
            if reminder_action in {"schedule", "snooze"} or cadence_minutes is not None:
                field_payload["reminder"] = _reminder_write_payload(
                    reminder_action,
                    cadence_minutes,
                    reminder_max_count,
                    reminder_until_done,
                    next_fire_at,
                    snoozed_until,
                )
            elif next_fire_at is not None:
                field_payload["next_reminder_at"] = next_fire_at
            if snoozed_until is not None and reminder_action is None:
                field_payload["snoozed_until"] = snoozed_until
            if reminder_action == "cancel":
                field_payload["reminder"] = {"action": "cancel"}
            if completed_at is not None:
                status_payload["completed_at"] = completed_at
            if not field_payload and not status_payload:
                return {"error": "No fields to update. Provide at least one field."}

            result: dict[str, Any] | None = None
            if field_payload:
                # Backend PATCH is status-only. Field edits, including
                # assigned_agent_id, must go through the partial PUT endpoint.
                result = await api_request_with_context(
                    ctx,
                    "PUT",
                    task_write_item_path(task_id),
                    json_data=field_payload,
                )
                if result.get("error"):
                    return _tasks_widget_result(result, action, space_id_value=ctx.get("space_id"))

            requested_status = status_payload.get("status")
            if status_payload:
                # Use the explicit status endpoint for widget-facing lifecycle
                # mutations. PATCH /tasks/{id} is a compatibility alias and can
                # drift behind adapters; /status is unambiguous.
                result = await api_request_with_context(
                    ctx,
                    "PUT",
                    task_status_write_item_path(task_id),
                    json_data=status_payload,
                )
                if result.get("error"):
                    return _tasks_widget_result(result, action, space_id_value=ctx.get("space_id"))
                if requested_status and not _task_status_matches(
                    _task_status_value(result), requested_status
                ):
                    return _tasks_widget_result(
                        {
                            "error": (
                                "Task status update did not persist: "
                                f"requested {requested_status!r}, backend returned "
                                f"{_task_status_value(result)!r}."
                            ),
                            "code": "task_status_not_persisted",
                            "task": result,
                            "requested_status": requested_status,
                            "actual_status": _task_status_value(result),
                        },
                        action,
                        space_id_value=ctx.get("space_id"),
                    )

            refreshed = await api_request_with_context(
                ctx,
                "GET",
                task_read_item_path(task_id),
                params=user_request_space_params(ctx),
            )
            if not refreshed.get("error"):
                result = refreshed
            if requested_status and result is not None and not _task_status_matches(
                _task_status_value(result), requested_status
            ):
                return _tasks_widget_result(
                    {
                        "error": (
                            "Task status update did not persist after refresh: "
                            f"requested {requested_status!r}, backend returned "
                            f"{_task_status_value(result)!r}."
                        ),
                        "code": "task_status_not_persisted",
                        "task": result,
                        "requested_status": requested_status,
                        "actual_status": _task_status_value(result),
                    },
                    action,
                    space_id_value=ctx.get("space_id"),
                )
            return _tasks_widget_result(result, action, space_id_value=ctx.get("space_id"))

        if action == "nudge":
            if not task_id:
                return {"error": "'task_id' required for nudge action"}
            result = await api_request_with_context(
                ctx,
                "POST",
                task_nudge_write_item_path(task_id),
            )
            if result.get("error"):
                return _tasks_widget_result(result, action, space_id_value=ctx.get("space_id"))
            refreshed = await api_request_with_context(
                ctx,
                "GET",
                task_read_item_path(task_id),
                params=user_request_space_params(ctx),
            )
            if not refreshed.get("error"):
                result = refreshed
            return _tasks_widget_result(result, action, space_id_value=ctx.get("space_id"))

        if action == "get":
            if not task_id:
                return {"error": "'task_id' required for get action"}
            result = await api_request_with_context(
                ctx,
                "GET",
                task_read_item_path(task_id),
                params=user_request_space_params(ctx),
            )
            return _tasks_widget_result(result, action, space_id_value=ctx.get("space_id"))

        return {
            "error": f"Unknown action: {action}. Available: list, create, update, get, nudge"
        }
