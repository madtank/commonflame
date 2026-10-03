"""Fleet-wide emergency stop / reminder silence control state.

This is intentionally Redis-backed and side-effect-light: the API writes an
explicit operator/audit readback, while enforcement callers only read the state
and fail closed for outbound agent dispatch or reminder delivery when enabled.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

FLEET_CONTROL_KEY = "ax:fleet-control:state"
DEFAULT_SCOPE = "fleet"


@dataclass
class FleetControlState:
    emergency_stop: bool = False
    reminder_silence: bool = False
    reason: str | None = None
    actor_id: str | None = None
    actor_type: str | None = None
    updated_at: str | None = None
    transition_id: str | None = None
    audit: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def fail_closed(cls, *, reason: str = "fleet-control state unreadable") -> "FleetControlState":
        """Return an enforcement-safe state for unreadable persisted control data."""

        return cls(
            emergency_stop=True,
            reminder_silence=True,
            reason=reason,
            actor_type="system",
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "FleetControlState":
        if not isinstance(data, dict):
            return cls()
        audit_raw = data.get("audit")
        audit: list[dict[str, Any]] = audit_raw if isinstance(audit_raw, list) else []
        return cls(
            emergency_stop=_as_bool(data.get("emergency_stop")),
            reminder_silence=_as_bool(data.get("reminder_silence")),
            reason=_optional_str(data.get("reason")),
            actor_id=_optional_str(data.get("actor_id")),
            actor_type=_optional_str(data.get("actor_type")),
            updated_at=_optional_str(data.get("updated_at")),
            transition_id=_optional_str(data.get("transition_id")),
            audit=audit,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "emergency_stop": self.emergency_stop,
            "reminder_silence": self.reminder_silence,
            "reason": self.reason,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "updated_at": self.updated_at,
            "transition_id": self.transition_id,
            "audit": list(self.audit),
        }

    def blocks_agent_communication(self) -> bool:
        return self.emergency_stop

    def blocks_reminders(self) -> bool:
        return self.emergency_stop or self.reminder_silence


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class FleetControlService:
    """Read/write fleet control state with explicit transition audit."""

    def __init__(self, redis_client, *, key: str = FLEET_CONTROL_KEY):
        self.redis = redis_client
        self.key = key

    async def get_state(self) -> FleetControlState:
        raw = await self.redis.get(self.key)
        if not raw:
            return FleetControlState()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(str(raw))
        except (TypeError, ValueError):
            return FleetControlState.fail_closed()
        if not isinstance(payload, dict):
            return FleetControlState.fail_closed()
        return FleetControlState.from_mapping(payload)

    async def update_state(
        self,
        *,
        emergency_stop: bool | None = None,
        reminder_silence: bool | None = None,
        reason: str,
        actor_id: str,
        actor_type: str,
        transition_id: str | None = None,
        now: datetime | None = None,
    ) -> FleetControlState:
        if not reason or not reason.strip():
            raise ValueError("reason is required for fleet-control transitions")
        now = now or datetime.now(UTC)
        current = await self.get_state()
        before = current.as_dict()
        if emergency_stop is not None:
            current.emergency_stop = bool(emergency_stop)
        if reminder_silence is not None:
            current.reminder_silence = bool(reminder_silence)
        current.reason = reason.strip()
        current.actor_id = str(actor_id)
        current.actor_type = str(actor_type)
        current.updated_at = now.isoformat()
        current.transition_id = transition_id or f"fleet-{int(now.timestamp())}"
        audit_entry = {
            "transition_id": current.transition_id,
            "at": current.updated_at,
            "actor_id": current.actor_id,
            "actor_type": current.actor_type,
            "reason": current.reason,
            "before": {
                "emergency_stop": before.get("emergency_stop"),
                "reminder_silence": before.get("reminder_silence"),
            },
            "after": {
                "emergency_stop": current.emergency_stop,
                "reminder_silence": current.reminder_silence,
            },
        }
        current.audit = (current.audit + [audit_entry])[-50:]
        await self.redis.set(self.key, json.dumps(current.as_dict(), sort_keys=True))
        return current

    async def enforcement_readback(self, *, surface: str) -> dict[str, Any]:
        state = await self.get_state()
        blocked = state.blocks_agent_communication() if surface == "agent_communication" else state.blocks_reminders()
        return {
            "blocked": blocked,
            "surface": surface,
            "reason": state.reason,
            "emergency_stop": state.emergency_stop,
            "reminder_silence": state.reminder_silence,
            "actor_id": state.actor_id,
            "actor_type": state.actor_type,
            "updated_at": state.updated_at,
            "transition_id": state.transition_id,
        }


__all__ = ["FLEET_CONTROL_KEY", "FleetControlService", "FleetControlState"]
