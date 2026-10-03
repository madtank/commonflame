"""Agent lifecycle (ALC) — pure evaluator for the staleness ladder.

active -> idle -> dormant -> archived, driven by productive-output recency
(`last_active_at`), never by presence/connection. Pure functions take an injected
`now` so the whole ladder is testable in milliseconds without a real clock.

Design: docs/plans/2026-05-29-agent-lifecycle-design.md
"""
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Forward-looking liveness signal (peach's listener heartbeat), stored centrally so a
# backend sweep can read it regardless of which box the listener runs on.
SIGNAL_TTL_SECONDS = 90  # refreshed each heartbeat; absence => no live signal, fall back to clock


def signal_redis_key(agent_id) -> str:
    return f"ax:signal:{agent_id}"


async def store_agent_signal(redis, agent_id, fields: Dict[str, Any]) -> None:
    """Persist one agent's forward-looking signal with a short TTL. Best-effort."""
    try:
        await redis.setex(signal_redis_key(agent_id), SIGNAL_TTL_SECONDS, json.dumps(fields))
    except Exception:  # noqa: BLE001
        logger.warning("store_agent_signal failed for agent_id=%s", agent_id, exc_info=True)


async def read_agent_signal(redis, agent_id) -> Optional[Dict[str, Any]]:
    """Read one agent's signal, or None if absent/unreadable."""
    try:
        raw = await redis.get(signal_redis_key(agent_id))
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        logger.warning("read_agent_signal failed for agent_id=%s", agent_id, exc_info=True)
        return None

# Lifecycle states (distinct from Agent.status and Agent.global_state).
ACTIVE = "active"
IDLE = "idle"
DORMANT = "dormant"
ARCHIVED = "archived"


@dataclass(frozen=True)
class LifecycleThresholds:
    """Time windows for the ladder. Shadow-mode starting points until calibrated
    from the real dormancy distribution (see design doc, section 11)."""

    idle_after: timedelta
    dormant_after: timedelta
    nudge_grace: timedelta
    auto_archive_after: timedelta


# Shadow-mode starting points (design doc section 2). NOT committed numbers —
# calibrate from the observed dormancy distribution before enabling destructive actions.
DEFAULT_THRESHOLDS = LifecycleThresholds(
    idle_after=timedelta(days=3),
    dormant_after=timedelta(days=10),
    nudge_grace=timedelta(days=3),
    auto_archive_after=timedelta(days=30),
)


def classify_staleness(
    last_active_at: datetime, now: datetime, thresholds: LifecycleThresholds
) -> str:
    """Natural staleness state implied purely by time since last productive output.

    Boundaries belong to the staler state (``>=``), so an agent exactly at
    ``idle_after`` is already idle. This is only the staleness floor; the
    dormant->archived progression is driven by separate nudge/suggest timers.
    """
    staleness = now - last_active_at
    if staleness >= thresholds.dormant_after:
        return DORMANT
    if staleness >= thresholds.idle_after:
        return IDLE
    return ACTIVE


def compute_display_lifecycle(
    persisted_state: Optional[str],
    last_active_at: Optional[datetime],
    now: datetime,
    thresholds: "LifecycleThresholds",
) -> str:
    """The lifecycle state to SHOW on a roster/API, computed live from the clock.

    In shadow mode the sweep never persists the computed state, so the stored
    `lifecycle_state` is 'active' for everyone — useless for display. This computes
    the live staleness instead, so UIs reflect reality without a sweep. ``archived``
    is terminal/authoritative and is never recomputed.
    """
    if persisted_state == ARCHIVED:
        return ARCHIVED
    if last_active_at is None:
        return ACTIVE
    return classify_staleness(last_active_at, now, thresholds)


EPHEMERAL_NAME_RE = re.compile(
    r"(^|[-_])(smoke|probe|demo|test|setup_probe|switchboard)([-_]|$)|"
    r"(smoke_after_429|gateway-move-smoke|codex_setup_probe)",
    re.IGNORECASE,
)
PROTECTED_DURABLE_NAMES = {"atlas", "canary", "daimon", "forge", "nyx", "peach", "spark", "zephyr"}


def compute_roster_group(
    *,
    global_state: Optional[str],
    lifecycle_state: Optional[str],
    presence_fresh: bool,
) -> str:
    """Canonical grouping for roster defaults.

    ``active`` is a lifecycle/productive-output state; ``live`` is a heartbeat
    state. Keeping them separate prevents offline/on-demand agents from being
    treated as stale solely because they are not currently connected.
    """
    if global_state in {ARCHIVED, "disabled"}:
        return global_state
    if lifecycle_state == ARCHIVED:
        return ARCHIVED
    if presence_fresh:
        return "live"
    if lifecycle_state == DORMANT:
        return DORMANT
    if lifecycle_state == IDLE:
        return IDLE
    return ACTIVE


def is_ephemeral_name(name: str | None) -> bool:
    return bool(name and EPHEMERAL_NAME_RE.search(name))


def ephemeral_protection_reasons(agent: Any, *, now: datetime, recent_days: int = 7) -> list[str]:
    """Return durable/protected reasons that exclude an agent from cleanup dry-run.

    This deliberately errs on preserving agents: named durable agents, protected
    management classes, rich profiles/charters, assigned work, and recent
    productive output are excluded from the legacy ephemeral candidate report.
    """
    reasons: list[str] = []
    name = (getattr(agent, "name", "") or "").strip().lower().lstrip("@")
    if name in PROTECTED_DURABLE_NAMES:
        reasons.append("named_durable_agent")
    if getattr(agent, "platform_managed", False):
        reasons.append("platform_managed")
    if getattr(agent, "identity_locked", False):
        reasons.append("identity_locked")
    if getattr(agent, "deletion_protected", False):
        reasons.append("deletion_protected")
    if getattr(agent, "management_class", None) == "concierge":
        reasons.append("concierge")
    if getattr(agent, "current_assigned_task_id", None):
        reasons.append("assigned_work")
    if getattr(agent, "bio", None) or getattr(agent, "specialization", None):
        reasons.append("profile_charter")

    last_active_at = getattr(agent, "last_active_at", None)
    if last_active_at:
        if last_active_at.tzinfo is None:
            last_active_at = last_active_at.replace(tzinfo=timezone.utc)
        if now - last_active_at < timedelta(days=recent_days):
            reasons.append("recent_productive_output")
    return reasons


def build_ephemeral_cleanup_item(agent: Any, *, now: datetime) -> dict[str, Any] | None:
    """Return a read-only dry-run cleanup candidate for a legacy ephemeral agent.

    No destructive action is implied. Protected/durable exceptions are excluded
    from the returned candidates; callers can expose ``excluded_count`` separately.
    """
    name = getattr(agent, "name", None)
    if not is_ephemeral_name(name):
        return None
    exclusions = ephemeral_protection_reasons(agent, now=now)
    if exclusions:
        return None
    lifecycle_state = compute_display_lifecycle(
        getattr(agent, "lifecycle_state", None),
        getattr(agent, "last_active_at", None),
        now,
        DEFAULT_THRESHOLDS,
    )
    last_active_at = getattr(agent, "last_active_at", None)
    return {
        "agent_id": str(getattr(agent, "id")),
        "name": name,
        "global_state": getattr(agent, "global_state", "active") or "active",
        "lifecycle_state": lifecycle_state,
        "last_active_at": last_active_at.isoformat() if last_active_at else None,
        "matched_reason": "ephemeral_name_pattern",
        "recommended_action": "review_then_archive_or_delete",
    }


def build_ephemeral_ttl_archive_item(
    agent: Any,
    *,
    now: datetime,
    ttl: timedelta,
) -> dict[str, Any] | None:
    """Return an archive mutation candidate after review TTL elapsed.

    This is the destructive counterpart to ``build_ephemeral_cleanup_item`` and
    is intentionally stricter: the agent must still match the ephemeral cleanup
    population, must already have an ``archive_suggested_at`` review timestamp,
    and that timestamp must be older than the configured TTL. Durable/protected
    exclusions are re-evaluated at execution time so a newly-chartered or
    recently-active agent is not archived from a stale dry-run report.
    """
    name = getattr(agent, "name", None)
    if not is_ephemeral_name(name):
        return None
    if ephemeral_protection_reasons(agent, now=now):
        return None
    suggested_at = getattr(agent, "archive_suggested_at", None)
    if suggested_at is None:
        return None
    if suggested_at.tzinfo is None:
        suggested_at = suggested_at.replace(tzinfo=timezone.utc)
    if now - suggested_at < ttl:
        return None
    last_active_at = getattr(agent, "last_active_at", None)
    return {
        "agent_id": str(getattr(agent, "id")),
        "name": name,
        "global_state": getattr(agent, "global_state", "active") or "active",
        "lifecycle_state": ARCHIVED,
        "last_active_at": last_active_at.isoformat() if last_active_at else None,
        "archive_suggested_at": suggested_at.isoformat(),
        "retention_class": "ephemeral_cleanup",
        "ttl_metadata": {
            "ttl_days": ttl.days,
            "ttl_started_at": suggested_at.isoformat(),
            "ttl_expires_at": (suggested_at + ttl).isoformat(),
        },
        "matched_reason": "ephemeral_name_pattern",
        "archive_reason": "ttl_after_review_elapsed",
    }


# Actions a sweep may take on an agent. The evaluator only *decides*; the caller
# performs the side effect (stamp a timestamp, send the nudge, emit the alert card).
ACTION_NONE = "none"
ACTION_SEND_NUDGE = "send_nudge"
ACTION_SUGGEST_ARCHIVE = "suggest_archive"
ACTION_ARCHIVE = "archive"
ACTION_ROUTE_FIX = "route_fix"  # online-but-broken (peach's currently_401) — fix, never archive


@dataclass(frozen=True)
class AgentLifecycleSnapshot:
    """The persisted lifecycle fields the evaluator needs for one agent."""

    last_active_at: datetime
    lifecycle_state: str
    nudged_at: Optional[datetime] = None
    archive_suggested_at: Optional[datetime] = None
    # True for platform_managed / identity_locked / deletion_protected / concierge:
    # such agents may classify as idle/dormant for display but never auto-progress
    # to nudge/suggest/archive.
    exempt: bool = False
    # Forward-looking liveness from peach's heartbeat signal: the agent is connected
    # but its token is 401-broken right now. Such an agent is fixable, not dormant.
    currently_401: bool = False


@dataclass(frozen=True)
class LifecycleDecision:
    next_state: str
    action: str = ACTION_NONE
    reason: str = ""
    # True when an agent resurrected (became active) and stale nudge/suggest
    # timers should be cleared by the caller.
    clear_nudge: bool = False


def evaluate(
    snap: AgentLifecycleSnapshot, now: datetime, thresholds: LifecycleThresholds
) -> LifecycleDecision:
    """Decide the next lifecycle state and the (at most one) side-effect action.

    Pure: no I/O, no clock read. The dormant->archived progression is gated by the
    nudge/suggest timers, not raw staleness, so it advances one bounded step per sweep.
    """
    natural = classify_staleness(snap.last_active_at, now, thresholds)

    # Active or idle by recency — includes resurrection of a previously dormant agent.
    if natural in (ACTIVE, IDLE):
        clear = snap.nudged_at is not None or snap.archive_suggested_at is not None
        return LifecycleDecision(
            next_state=natural, action=ACTION_NONE, reason=f"recency:{natural}", clear_nudge=clear
        )

    # natural == DORMANT below.
    if snap.exempt:
        return LifecycleDecision(next_state=DORMANT, action=ACTION_NONE, reason="exempt")

    # Online-but-broken (peach's forward signal): stale by message history but 401 right
    # now. This is fixable, not dormant — surface for token-isolation, never archive.
    if snap.currently_401:
        return LifecycleDecision(next_state=DORMANT, action=ACTION_ROUTE_FIX, reason="currently_401")

    # Step 1: not yet nudged -> nudge.
    if snap.nudged_at is None:
        return LifecycleDecision(next_state=DORMANT, action=ACTION_SEND_NUDGE, reason="dormant:nudge")

    # Step 2: nudged, awaiting response, not yet suggested.
    if snap.archive_suggested_at is None:
        if now - snap.nudged_at >= thresholds.nudge_grace:
            return LifecycleDecision(
                next_state=DORMANT, action=ACTION_SUGGEST_ARCHIVE, reason="nudge_grace_elapsed"
            )
        return LifecycleDecision(next_state=DORMANT, action=ACTION_NONE, reason="awaiting_nudge_response")

    # Step 3: suggestion surfaced -> wait for owner, else auto-archive backstop.
    if now - snap.archive_suggested_at >= thresholds.auto_archive_after:
        return LifecycleDecision(next_state=ARCHIVED, action=ACTION_ARCHIVE, reason="auto_backstop")
    return LifecycleDecision(next_state=DORMANT, action=ACTION_NONE, reason="awaiting_owner")


@dataclass(frozen=True)
class SweepPlan:
    """Aggregated result of evaluating the whole fleet — the dry-run/shadow report.

    ``decisions`` is parallel to the input snapshots so callers can pair each back
    to its agent for a per-agent breakdown.
    """

    decisions: List[LifecycleDecision]
    by_next_state: dict
    by_action: dict


def plan_sweep(
    snapshots: Sequence[AgentLifecycleSnapshot], now: datetime, thresholds: LifecycleThresholds
) -> SweepPlan:
    """Evaluate every snapshot and aggregate the decisions. Pure — no I/O, no writes.

    This is the heart of both ``--dry-run`` (manual) and continuous shadow mode:
    compute what *would* happen and tally it, taking no action.
    """
    decisions = [evaluate(s, now, thresholds) for s in snapshots]
    return SweepPlan(
        decisions=decisions,
        by_next_state=dict(Counter(d.next_state for d in decisions)),
        by_action=dict(Counter(d.action for d in decisions)),
    )


# --- Instrumentation: the single productive-output funnel -------------------

_TOUCH_SQL = text(
    """
    UPDATE agents
    SET last_active_at = :now,
        lifecycle_changed_at = CASE
            WHEN lifecycle_state <> 'active' THEN :now ELSE lifecycle_changed_at END,
        lifecycle_state = 'active',
        nudged_at = NULL,
        archive_suggested_at = NULL,
        responded_count = responded_count + 1
    WHERE id = :agent_id
    """
)


async def touch_agent_activity(db, agent_id, now: Optional[datetime] = None) -> None:
    """Record that an agent just produced output — the ONE funnel for the ALC clock.

    Call from exactly the productive-output sites: dispatch ``/complete`` success,
    agent message create, task accept/complete. Sets ``last_active_at`` and, because
    output means the agent is alive, resurrects it to ``active`` and clears the
    nudge/suggest stamps (resurrection is free — no special reconnect handler).

    Instrumentation must never break the hot path: failures are swallowed and logged.
    """
    try:
        await db.execute(
            _TOUCH_SQL, {"now": now or datetime.now(timezone.utc), "agent_id": str(agent_id)}
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — instrumentation is best-effort, never fatal
        logger.warning("touch_agent_activity failed for agent_id=%s", agent_id, exc_info=True)
