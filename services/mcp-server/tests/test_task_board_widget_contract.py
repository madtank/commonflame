"""Regression guards for the task-board widget hydration contract.

These intentionally assert specific JS contract fragments; they are not
general-purpose logic tests.
"""

from __future__ import annotations

from pathlib import Path


WIDGET_HTML = (
    Path(__file__).resolve().parents[1]
    / "fastmcp_server"
    / "resources"
    / "static"
    / "widgets"
    / "task-board.html"
)


def test_task_board_hydrates_cli_task_signal_initial_data() -> None:
    html = WIDGET_HTML.read_text(encoding="utf-8")

    assert "function taskDetailFromSignalData(data)" in html
    assert "selected_task_id" in html
    assert 'data?.kind === "task"' in html
    assert 'data?.action === "get"' in html
    assert 'data?.action !== "list"' in html
    assert 'unwrapped.action !== "list"' in html
    assert "Older CLI task signals used a selected_task_id plus one item" in html
    assert "TODO: remove this fallback after all CLI agents emit kind/action" in html
    assert "function detailStateFromTask(task)" in html
    assert "state.selected = detailStateFromTask(signalDetail)" in html
    assert "state.tasks = [signalDetail]" in html
    assert "if (!state.tasks.length || state.total > state.tasks.length)" in html


def test_task_board_hydrates_cli_task_collection_signal_items() -> None:
    html = WIDGET_HTML.read_text(encoding="utf-8")

    assert "function taskCollectionFromSignalData(data)" in html
    assert 'data?.kind === "tasks"' in html
    assert "Array.isArray(unwrapped.items)" in html
    assert "state.tasks = signalCollection.items" in html
    assert "state.total = signalCollection.total" in html
    assert "const unwrapped = unwrapV2(data) || {};" in html
    assert "state.total = data.total || unwrapped.total || data.count || unwrapped.count || 1;" in html
    assert "function hasPartialTaskCollection()" in html
    assert "state.total > state.tasks.length" in html
    assert "if (hasPartialTaskCollection())" in html


def test_task_board_suppresses_success_notice_banner_inside_app() -> None:
    html = WIDGET_HTML.read_text(encoding="utf-8")

    assert "Signal cards own success/info receipts" in html
    assert 'if (severity === "error")' in html
    assert 'root.appendChild(el("div", { className: "notice notice-error" }, message));' in html


def test_task_detail_header_show_all_uses_shared_list_handler() -> None:
    html = WIDGET_HTML.read_text()

    assert 'function showTaskList()' in html
    assert 'host.callServerTool("tasks", { action: "list" })' in html
    assert 'command === "tasks/all"' in html
    assert 'command === "tasks:return-to-list"' in html
    assert 'void showTaskList();' in html


def test_task_detail_field_updates_preserve_viewport() -> None:
    html = WIDGET_HTML.read_text()

    assert "function handleResult(res, view, options = {})" in html
    assert 'handleResult(updateRes, "detail", { preserveViewport: true })' in html
    assert "if (ok && !options.preserveViewport)" in html


def test_task_board_canonicalizes_active_reminder_state_to_armed() -> None:
    html = WIDGET_HTML.read_text()

    assert 'else if (looksArmedReminder(rawState, reminder, nextAt)) stateValue = "armed";' in html
    assert 'stateValue = rawState && rawState !== "none" ? rawState : "armed"' not in html
    assert "raw_state: rawState" in html
    assert 'rawState === "looping" || rawState === "exhausted"' in html


def test_task_board_reminder_strip_uses_aligned_chip_and_action_columns() -> None:
    html = WIDGET_HTML.read_text()

    assert ".reminder-action-strip {" in html
    assert "grid-template-columns: minmax(0, 1fr) auto;" in html
    assert ".reminder-strip-chips { display: flex; gap: 6px; flex-wrap: wrap; min-width: 0; }" in html
    assert ".strip-actions { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; min-width: 0; }" in html
    assert 'const chips = el("div", { className: "reminder-strip-chips", "data-testid": "task-reminder-strip-chips" });' in html
    assert "strip.appendChild(chips);" in html


def test_task_board_reminder_controls_v1_surface() -> None:
    html = WIDGET_HTML.read_text()

    assert "function canonicalReminderState(task, detail = null)" in html
    assert "function reminderPayloadFromTask(task, detail = null)" in html
    assert "findListTaskForDetail(task)" in html
    assert "const reminder = projectedReminder(task, detail);" in html
    assert "const reminder = projectedReminder(t, detail);" in html
    assert "terminal = isTerminalTaskStatus(task?.status)" in html
    assert "explicitInactive" in html
    assert 'data-testid": "task-reminder-strip-complete"' in html
    assert 'data-testid": "task-reminder-strip-cancel-task"' in html
    assert 'window.confirm("Cancel this task?")' not in html
    assert 'data-testid": "task-reminder-strip-schedule-until-done"' in html
    assert 'reminder_action: "silence"' not in html
    assert "silence" not in html.lower()
    assert 'reminder_action: "snooze", snoozed_until: snoozedUntil' in html
    assert 'task-reminder-strip-snooze-1h' in html
    assert 'task-reminder-strip-snooze-4h' in html
    assert 'reminder_action: "schedule"' in html
    assert 'reminder_action: "snooze"' in html
    assert 'reminder_action: "cancel"' in html
    assert 'data-testid": "tasks-reminder-filter-row"' in html
    assert 'tasks-reminder-filter-overdue' in html
    assert 'tasks-reminder-filter-armed' in html
    assert 'tasks-reminder-filter-none' in html
    assert 'tasks-reminder-filter-noisy' in html
    assert 'tasks-reminder-filter-mine-due' in html
    assert 'tasks-mute-reminders' in html
    assert 'tasks-unmute-reminders' in html
    assert 'state.activeFilter = nextActive ? f.filter : null' in html
    assert 'tasks-assignee-filter' in html
    assert 'task-reminder-history-drawer' in html
    assert 'task-reminder-history-row' in html
    assert 'task-reminder-history-truncated' in html
    assert 'history.total > events.length' in html
    assert 'const now = Date.now();' in html
    assert 'snoozedMs > now' in html
    assert 'function looksArmedReminder(rawState, reminder, nextAt)' in html
    assert 'if (value !== undefined) target[key] = value;' in html
    assert 'if (value !== undefined) target[targetKey] = value;' in html
    assert "Math.max(...historyCounts)" in html
    assert 'if (reminder.has_reminder || reminder.last_fire_at || reminder.last_reminded_at' in html
    assert 'card.appendChild(renderReminderHistoryDrawer(reminder, detail));' in html
    assert "detail-fields signal-fields" in html
