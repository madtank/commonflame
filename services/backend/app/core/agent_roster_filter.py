"""Shared roster inclusion helpers for live/default agent surfaces."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.agent_lifecycle import DEFAULT_THRESHOLDS, ARCHIVED, compute_display_lifecycle


def roster_display_lifecycle(agent: Any, *, now: datetime | None = None) -> str:
    """Return the display lifecycle used by roster filters and serializers."""

    now = now or datetime.now(UTC)
    return compute_display_lifecycle(
        getattr(agent, "lifecycle_state", None),
        getattr(agent, "last_active_at", None),
        now,
        DEFAULT_THRESHOLDS,
    )


def roster_presence_fresh(presence_data: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    """True when Redis presence contains a fresh heartbeat for live-online default rosters."""

    if not isinstance(presence_data, dict):
        return False
    raw = presence_data.get("last_heartbeat") or presence_data.get("connected_at")
    if not raw:
        return False
    if now is None:
        now = datetime.now(UTC)
    try:
        if isinstance(raw, datetime):
            heartbeat = raw
        else:
            heartbeat = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    return 0 <= (now - heartbeat.astimezone(UTC)).total_seconds() < 60


def include_agent_in_roster(
    agent: Any,
    presence_data: dict[str, Any] | None,
    *,
    include_dormant: bool = False,
    include_archived: bool = False,
    include_offline: bool = False,
    now: datetime | None = None,
) -> bool:
    """Default roster is live-online only; dormant/archived/offline are explicit opt-ins.

    ``include_dormant`` admits idle/dormant non-live agents for audit/management views.
    ``include_archived`` admits lifecycle/global archived agents. ``include_offline`` is
    a broader management opt-in for active-but-not-live agents.
    """

    global_state = (getattr(agent, "global_state", None) or "").lower()
    lifecycle = roster_display_lifecycle(agent, now=now)
    archived = global_state == ARCHIVED or lifecycle == ARCHIVED
    live = roster_presence_fresh(presence_data, now=now)

    if archived:
        return bool(include_archived)
    if global_state == "disabled":
        return False
    if live:
        return True
    if lifecycle in {"idle", "dormant"}:
        return bool(include_dormant or include_offline)
    return bool(include_offline)
