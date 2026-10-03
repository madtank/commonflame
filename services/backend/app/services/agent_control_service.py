"""Agent control and kill switch management.

This module centralizes kill-switches, rate limit overrides, and other
runtime controls for managed agents that run in Pax's GCP environment.

The intention is that every managed agent (starting with Chirpy) queries
this service before acting. This gives us a single location to plug in new
controls as we add premium agents or additional automation surfaces.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from app.core.config import settings

import logging

logger = logging.getLogger(__name__)


@dataclass
class AgentRateLimitConfig:
    """Rate limit overrides for a managed agent."""

    user_hourly_limit: Optional[int] = None
    user_daily_limit: Optional[int] = None
    agent_hourly_limit: Optional[int] = None
    agent_daily_limit: Optional[int] = None

    def merge(self, other: "AgentRateLimitConfig") -> None:
        """Merge overrides, preferring the most specific (non-None) values."""

        if other.user_hourly_limit is not None:
            self.user_hourly_limit = other.user_hourly_limit
        if other.user_daily_limit is not None:
            self.user_daily_limit = other.user_daily_limit
        if other.agent_hourly_limit is not None:
            self.agent_hourly_limit = other.agent_hourly_limit
        if other.agent_daily_limit is not None:
            self.agent_daily_limit = other.agent_daily_limit

    def as_dict(self) -> dict:
        return {
            "user_hourly_limit": self.user_hourly_limit,
            "user_daily_limit": self.user_daily_limit,
            "agent_hourly_limit": self.agent_hourly_limit,
            "agent_daily_limit": self.agent_daily_limit,
        }


@dataclass
class AgentControlState:
    """Full control state for an agent after merging all scopes."""

    is_disabled: bool = False
    disabled_reason: Optional[str] = None
    disabled_by: list[str] = field(default_factory=list)
    disabled_until: Optional[str] = None
    no_reply: bool = False
    no_reply_reason: Optional[str] = None
    no_reply_by: list[str] = field(default_factory=list)
    no_reply_until: Optional[str] = None
    routing_only: bool = False
    routing_only_reason: Optional[str] = None
    routing_only_by: list[str] = field(default_factory=list)
    routing_only_until: Optional[str] = None
    rate_limits: AgentRateLimitConfig = field(default_factory=AgentRateLimitConfig)
    legacy_cache: dict = field(
        default_factory=lambda: {
            "status": None,
            "disabled": False,
            "paused": False,
            "blocking_keys": [],
        }
    )

    def mark_disabled(
        self,
        scope: str,
        reason: Optional[str],
        until: Optional[str] = None,
    ) -> None:
        self.is_disabled = True
        if reason:
            self.disabled_reason = reason
        if until:
            self.disabled_until = until
        self.disabled_by.append(scope)

    def mark_no_reply(
        self,
        scope: str,
        reason: Optional[str],
        until: Optional[str] = None,
    ) -> None:
        self.no_reply = True
        if reason:
            self.no_reply_reason = reason
        if until:
            self.no_reply_until = until
        self.no_reply_by.append(scope)

    def mark_routing_only(
        self,
        scope: str,
        reason: Optional[str],
        until: Optional[str] = None,
    ) -> None:
        self.routing_only = True
        if reason:
            self.routing_only_reason = reason
        if until:
            self.routing_only_until = until
        self.routing_only_by.append(scope)

    def as_dict(self) -> dict:
        payload = {
            "is_disabled": self.is_disabled,
            "disabled_reason": self.disabled_reason,
            "disabled_by": list(self.disabled_by),
            "disabled_until": self.disabled_until,
            "no_reply": self.no_reply,
            "no_reply_reason": self.no_reply_reason,
            "no_reply_by": list(self.no_reply_by),
            "no_reply_until": self.no_reply_until,
            "routing_only": self.routing_only,
            "routing_only_reason": self.routing_only_reason,
            "routing_only_by": list(self.routing_only_by),
            "routing_only_until": self.routing_only_until,
        }
        payload.update(self.rate_limits.as_dict())
        payload["legacy_cache"] = dict(self.legacy_cache)
        return payload


class AgentControlService:
    """Interface for toggling managed agents on/off and overriding limits.

    Controls are hierarchical:
    1. Environment defaults (from settings)
    2. Global overrides (Redis key `ax:agent-control:global`)
    3. Workspace overrides (Redis key `ax:agent-control:space:{space_id}`)
    4. Agent overrides (Redis key `ax:agent-control:agent:{agent_id}`)

    The most specific scope wins. For example, an agent-level hourly limit
    overrides both workspace and global values. This structure lets us add new
    agents later without reworking each service – they simply reuse this class.
    """

    GLOBAL_KEY = "ax:agent-control:global"

    def __init__(self, redis_client):
        self.redis = redis_client

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _workspace_key(space_id: UUID) -> str:
        return f"ax:agent-control:space:{space_id}"

    @staticmethod
    def _agent_key(agent_id: UUID) -> str:
        return f"ax:agent-control:agent:{agent_id}"

    @staticmethod
    def _managed_key(agent_slug: str) -> str:
        return f"ax:agent-control:managed:{agent_slug}"

    @staticmethod
    def _legacy_status_key(agent_id: UUID) -> str:
        return f"agent:{agent_id}:status"

    @staticmethod
    def _legacy_disabled_key(agent_id: UUID) -> str:
        return f"agent:{agent_id}:disabled"

    @staticmethod
    def _legacy_paused_key(agent_id: UUID) -> str:
        return f"agent:{agent_id}:paused"

    @staticmethod
    def _decode_redis_value(value) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @classmethod
    def _build_legacy_cache_state(
        cls,
        *,
        agent_id: UUID,
        status_value=None,
        disabled_value=None,
        paused_value=None,
    ) -> dict:
        status = cls._decode_redis_value(status_value)
        # The legacy dispatch path blocks on Redis key existence, not a specific
        # value. Treat any returned value (including "0" or an empty string) as
        # blocking so roster/audit readback matches runtime routing behavior.
        disabled = disabled_value is not None
        paused = paused_value is not None
        blocking_keys: list[str] = []
        if disabled:
            blocking_keys.append(cls._legacy_disabled_key(agent_id))
        if paused:
            blocking_keys.append(cls._legacy_paused_key(agent_id))
        return {
            "status": status,
            "disabled": disabled,
            "paused": paused,
            "blocking_keys": blocking_keys,
        }

    async def _attach_legacy_cache_state(self, agent_id: UUID, state: AgentControlState) -> None:
        state.legacy_cache = self._build_legacy_cache_state(
            agent_id=agent_id,
            status_value=await self.redis.get(self._legacy_status_key(agent_id)),
            disabled_value=await self.redis.get(self._legacy_disabled_key(agent_id)),
            paused_value=await self.redis.get(self._legacy_paused_key(agent_id)),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def get_control_state(
        self,
        *,
        agent_id: UUID,
        space_id: Optional[UUID] = None,
        agent_slug: Optional[str] = None,
    ) -> AgentControlState:
        """Return the merged control state for the requested agent."""

        state = AgentControlState()
        # Default rate limits for managed agents
        rate_limits = AgentRateLimitConfig(
            user_hourly_limit=30,
            user_daily_limit=200,
            agent_hourly_limit=500,
            agent_daily_limit=10000,
        )
        state.rate_limits.merge(rate_limits)

        # Environment-wide kill switch takes precedence.
        if settings.agents_global_kill_switch:
            state.mark_disabled("environment", "Global agent kill switch is active")

        # Merge global overrides (operations can pause everything here).
        await self._merge_scope(self.GLOBAL_KEY, "global", state)

        # Merge workspace overrides.
        if space_id:
            await self._merge_scope(self._workspace_key(space_id), "workspace", state)

        # Merge managed-agent overrides (e.g., chirpy runtime) if provided.
        if agent_slug:
            await self._merge_scope(self._managed_key(agent_slug), f"managed:{agent_slug}", state)

        # Merge agent-specific overrides.
        await self._merge_scope(self._agent_key(agent_id), "agent", state)

        await self._attach_legacy_cache_state(agent_id, state)

        logger.info(
            "AGENT_CONTROL_STATE",
            extra={
                "agent_id": str(agent_id),
                "space_id": str(space_id) if space_id else None,
                "agent_slug": agent_slug,
                "is_disabled": state.is_disabled,
                "disabled_by": list(state.disabled_by),
                "disabled_reason": state.disabled_reason,
                "rate_limits": state.rate_limits.as_dict(),
            },
        )

        return state

    async def get_control_states_batch(
        self,
        agents: list[tuple[UUID, UUID | None, str | None]],
    ) -> dict[UUID, "AgentControlState"]:
        """Batch-fetch control states for multiple agents using Redis pipeline.

        Args:
            agents: List of (agent_id, space_id, agent_slug) tuples.

        Returns:
            Dict mapping agent_id → AgentControlState.

        Uses a single Redis pipeline instead of 4×N sequential GETs.
        """
        if not agents:
            return {}

        # Build all keys we need to fetch
        keys_to_fetch = []
        key_map = []  # (index, agent_id, scope)
        legacy_key_map = {}  # agent_id -> {status|disabled|paused: index}

        # Global key is shared — fetch once
        keys_to_fetch.append(self.GLOBAL_KEY)
        global_idx = 0

        for agent_id, space_id, agent_slug in agents:
            if space_id:
                keys_to_fetch.append(self._workspace_key(space_id))
                key_map.append((len(keys_to_fetch) - 1, agent_id, "workspace"))
            if agent_slug:
                keys_to_fetch.append(self._managed_key(agent_slug))
                key_map.append((len(keys_to_fetch) - 1, agent_id, "managed"))
            keys_to_fetch.append(self._agent_key(agent_id))
            key_map.append((len(keys_to_fetch) - 1, agent_id, "agent"))
            legacy_key_map[agent_id] = {
                "status": len(keys_to_fetch),
                "disabled": len(keys_to_fetch) + 1,
                "paused": len(keys_to_fetch) + 2,
            }
            keys_to_fetch.extend(
                [
                    self._legacy_status_key(agent_id),
                    self._legacy_disabled_key(agent_id),
                    self._legacy_paused_key(agent_id),
                ]
            )

        # Fetch all keys in one pipeline
        pipe = self.redis.pipeline(transaction=False)
        for idx, key in enumerate(keys_to_fetch):
            if any(idx in indexes.values() for indexes in legacy_key_map.values()):
                pipe.get(key)
            else:
                pipe.hgetall(key)
        results = await pipe.execute()

        # Parse global data once
        global_data = results[global_idx] if results else {}

        # Build per-agent states
        states: dict[UUID, AgentControlState] = {}
        # Group key_map by agent_id
        agent_scopes: dict[UUID, list[tuple[int, str]]] = {}
        for idx, agent_id, scope in key_map:
            if agent_id not in agent_scopes:
                agent_scopes[agent_id] = []
            agent_scopes[agent_id].append((idx, scope))

        for agent_id, space_id, agent_slug in agents:
            state = AgentControlState()
            rate_limits = AgentRateLimitConfig(
                user_hourly_limit=30,
                user_daily_limit=200,
                agent_hourly_limit=500,
                agent_daily_limit=10000,
            )
            state.rate_limits.merge(rate_limits)

            # Environment kill switch
            if settings.agents_global_kill_switch:
                state.mark_disabled("environment", "Global agent kill switch is active")

            # Merge global
            self._merge_scope_data(global_data, "global", state)

            # Merge agent-specific scopes
            for idx, scope in agent_scopes.get(agent_id, []):
                data = results[idx] if idx < len(results) else {}
                if data:
                    self._merge_scope_data(data, scope, state)

            legacy_indexes = legacy_key_map.get(agent_id, {})
            state.legacy_cache = self._build_legacy_cache_state(
                agent_id=agent_id,
                status_value=results[legacy_indexes["status"]] if "status" in legacy_indexes else None,
                disabled_value=results[legacy_indexes["disabled"]] if "disabled" in legacy_indexes else None,
                paused_value=results[legacy_indexes["paused"]] if "paused" in legacy_indexes else None,
            )

            states[agent_id] = state

        return states

    def _merge_scope_data(self, data: dict, scope: str, state: "AgentControlState") -> None:
        """Merge scope data into state (sync version for batch use)."""
        if not data:
            return

        disabled = data.get("disabled")
        disabled_until = data.get("disabled_until")
        if disabled == "1" and self._is_control_window_active(disabled_until):
            reason = data.get("reason")
            state.mark_disabled(scope, reason, disabled_until)
        elif disabled == "0":
            if scope in state.disabled_by:
                state.disabled_by.remove(scope)
            if not state.disabled_by:
                state.is_disabled = False
                state.disabled_reason = None
                state.disabled_until = None

        no_reply = data.get("no_reply")
        no_reply_until = data.get("no_reply_until")
        if no_reply == "1" and self._is_control_window_active(no_reply_until):
            reason = data.get("no_reply_reason") or data.get("reason")
            state.mark_no_reply(scope, reason, no_reply_until)
        elif no_reply == "0":
            if scope in state.no_reply_by:
                state.no_reply_by.remove(scope)
            if not state.no_reply_by:
                state.no_reply = False
                state.no_reply_reason = None
                state.no_reply_until = None

        routing_only = data.get("routing_only")
        routing_only_until = data.get("routing_only_until")
        if routing_only == "1" and self._is_control_window_active(routing_only_until):
            reason = data.get("routing_only_reason") or data.get("reason")
            state.mark_routing_only(scope, reason, routing_only_until)
        elif routing_only == "0":
            # A more specific explicit clear opts out of broader routing-only
            # scopes for this resolved agent state.
            state.routing_only = False
            state.routing_only_reason = None
            state.routing_only_until = None
            state.routing_only_by.clear()

        overrides = AgentRateLimitConfig(
            user_hourly_limit=self._parse_optional_int(data.get("user_hourly_limit")),
            user_daily_limit=self._parse_optional_int(data.get("user_daily_limit")),
            agent_hourly_limit=self._parse_optional_int(data.get("agent_hourly_limit")),
            agent_daily_limit=self._parse_optional_int(data.get("agent_daily_limit")),
        )
        state.rate_limits.merge(overrides)

    async def clear_legacy_kill_switch_cache(self, agent_id: UUID) -> tuple[str, str]:
        """Clear legacy Redis circuit-breaker keys that can shadow control state.

        The authoritative control state lives in ``ax:agent-control:*`` hashes,
        but the dispatch fast path also consults the older
        ``agent:{id}:disabled`` and ``agent:{id}:paused`` keys. This helper is
        the explicit self-serve/admin flush affordance for those legacy keys and
        is also used by normal agent-scope re-enable.
        """

        keys = (
            f"agent:{agent_id}:disabled",
            f"agent:{agent_id}:paused",
        )
        await self.redis.delete(*keys)
        return keys

    async def update_agent_control(
        self,
        *,
        agent_id: Optional[UUID] = None,
        space_id: Optional[UUID] = None,
        agent_slug: Optional[str] = None,
        scope: str,
        updates: dict,
    ) -> AgentControlState:
        """Persist control updates for the desired scope and return the new state."""

        key = self._resolve_scope_key(scope=scope, agent_id=agent_id, space_id=space_id, agent_slug=agent_slug)
        if key is None:
            raise ValueError(f"Unsupported scope '{scope}'")

        mapping, removals = self._sanitize_updates(updates)

        if mapping:
            await self.redis.hset(key, mapping=mapping)
        if removals:
            await self.redis.hdel(key, *removals)

        # When we explicitly enable an agent we also remove the reason so that
        # future state reflects the current setting instead of stale text.
        if "disabled" in mapping and mapping["disabled"] == "0":
            await self.redis.hdel(key, "reason", "disabled_until")
            if scope == "agent" and agent_id is not None:
                try:
                    await self.clear_legacy_kill_switch_cache(agent_id)
                except Exception:
                    logger.warning(
                        "Failed to clear legacy agent kill-switch cache for %s",
                        agent_id,
                        exc_info=True,
                    )
        if "no_reply" in mapping and mapping["no_reply"] == "0":
            await self.redis.hdel(key, "no_reply_reason", "no_reply_until")
        if "routing_only" in mapping and mapping["routing_only"] == "0":
            await self.redis.hdel(key, "routing_only_reason", "routing_only_until")

        await self._sync_control_key_ttl(key)

        # Return merged state so callers can update UI immediately.
        return await self.get_control_state(agent_id=agent_id, space_id=space_id, agent_slug=agent_slug)

    async def flush_agent_kill_switch_cache(self, agent_id: UUID) -> dict:
        """Clear legacy dispatch-blocking cache keys for one agent.

        This is intentionally narrower than deleting the full agent status
        cache: status readback remains useful for audits, while the legacy
        disabled/paused keys are the stale dispatch blockers.
        """

        disabled_key = self._legacy_disabled_key(agent_id)
        paused_key = self._legacy_paused_key(agent_id)
        legacy_cache = self._build_legacy_cache_state(
            agent_id=agent_id,
            status_value=await self.redis.get(self._legacy_status_key(agent_id)),
            disabled_value=await self.redis.get(disabled_key),
            paused_value=await self.redis.get(paused_key),
        )
        await self.redis.delete(disabled_key, paused_key)
        return {
            "agent_id": str(agent_id),
            "cleared_keys": legacy_cache["blocking_keys"],
            "status_cache": legacy_cache["status"],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _merge_scope(self, key: str, scope: str, state: AgentControlState) -> None:
        if not key:
            return

        data = await self.redis.hgetall(key)
        if not data:
            return

        disabled = data.get("disabled")
        disabled_until = data.get("disabled_until")
        if disabled == "1" and self._is_control_window_active(disabled_until):
            reason = data.get("reason")
            state.mark_disabled(scope, reason, disabled_until)
        elif disabled == "0":
            # Explicitly enabled at this scope; ensure we don't carry a reason
            # from a more general scope if a more specific scope opted back in.
            if scope in state.disabled_by:
                state.disabled_by.remove(scope)
            if not state.disabled_by:
                state.is_disabled = False
                state.disabled_reason = None
                state.disabled_until = None

        no_reply = data.get("no_reply")
        no_reply_until = data.get("no_reply_until")
        if no_reply == "1" and self._is_control_window_active(no_reply_until):
            reason = data.get("no_reply_reason") or data.get("reason")
            state.mark_no_reply(scope, reason, no_reply_until)
        elif no_reply == "0":
            if scope in state.no_reply_by:
                state.no_reply_by.remove(scope)
            if not state.no_reply_by:
                state.no_reply = False
                state.no_reply_reason = None
                state.no_reply_until = None

        routing_only = data.get("routing_only")
        routing_only_until = data.get("routing_only_until")
        if routing_only == "1" and self._is_control_window_active(routing_only_until):
            reason = data.get("routing_only_reason") or data.get("reason")
            state.mark_routing_only(scope, reason, routing_only_until)
        elif routing_only == "0":
            # A more specific explicit clear opts out of broader routing-only
            # scopes for this resolved agent state.
            state.routing_only = False
            state.routing_only_reason = None
            state.routing_only_until = None
            state.routing_only_by.clear()

        overrides = AgentRateLimitConfig(
            user_hourly_limit=self._parse_optional_int(data.get("user_hourly_limit")),
            user_daily_limit=self._parse_optional_int(data.get("user_daily_limit")),
            agent_hourly_limit=self._parse_optional_int(data.get("agent_hourly_limit")),
            agent_daily_limit=self._parse_optional_int(data.get("agent_daily_limit")),
        )
        state.rate_limits.merge(overrides)

        logger.debug(
            "AGENT_CONTROL_MERGE",
            extra={
                "scope": scope,
                "key": key,
                "disabled": data.get("disabled"),
                "reason": data.get("reason"),
                "disabled_until": data.get("disabled_until"),
                "no_reply": data.get("no_reply"),
                "no_reply_reason": data.get("no_reply_reason"),
                "no_reply_until": data.get("no_reply_until"),
                "state_is_disabled": state.is_disabled,
                "state_disabled_by": list(state.disabled_by),
                "state_disabled_reason": state.disabled_reason,
                "state_no_reply": state.no_reply,
                "state_no_reply_by": list(state.no_reply_by),
                "state_no_reply_reason": state.no_reply_reason,
            },
        )

    @staticmethod
    def _parse_until(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            normalized = (
                value
                if value.endswith("Z") or "+" in value[10:]
                else f"{value}Z"
            )
            return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Invalid timestamp stored in agent control: %s", value)
            return None

    async def _sync_control_key_ttl(self, key: str) -> None:
        """Auto-expire temporary kill-switch hashes once their window has passed.

        This keeps temporary/test control keys from lingering indefinitely in
        Redis after the control window ends.
        """
        data = await self.redis.hgetall(key)
        if not data:
            try:
                await self.redis.delete(key)
            except Exception:
                logger.debug("Failed to delete empty agent control key %s", key, exc_info=True)
            return

        expiries: list[datetime] = []
        if data.get("disabled") == "1":
            until = self._parse_until(data.get("disabled_until"))
            if until is not None:
                expiries.append(until)
        if data.get("no_reply") == "1":
            until = self._parse_until(data.get("no_reply_until"))
            if until is not None:
                expiries.append(until)
        if data.get("routing_only") == "1":
            until = self._parse_until(data.get("routing_only_until"))
            if until is not None:
                expiries.append(until)

        if expiries:
            expire_at = max(expiries) + timedelta(days=1)
            await self.redis.expireat(key, int(expire_at.timestamp()))
            return

        try:
            await self.redis.persist(key)
        except Exception:
            logger.debug("Failed to persist agent control key %s", key, exc_info=True)

    @staticmethod
    def _parse_optional_int(value: Optional[str]) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning("Invalid integer stored in agent control: %s", value)
            return None

    @classmethod
    def _is_control_window_active(cls, until_value: Optional[str]) -> bool:
        if not until_value:
            return True

        until = cls._parse_until(until_value)
        if until is None:
            return True

        return until > datetime.now(timezone.utc)

    def _resolve_scope_key(
        self,
        *,
        scope: str,
        agent_id: Optional[UUID],
        space_id: Optional[UUID],
        agent_slug: Optional[str],
    ) -> Optional[str]:
        if scope == "agent":
            if agent_id is None:
                raise ValueError("agent_id is required for agent scope updates")
            return self._agent_key(agent_id)
        if scope in {"workspace", "org"}:
            if space_id is None:
                raise ValueError("space_id is required for workspace scope updates")
            return self._workspace_key(space_id)
        if scope == "global":
            return self.GLOBAL_KEY
        if scope == "managed":
            if not agent_slug:
                raise ValueError("agent_slug is required for managed scope updates")
            return self._managed_key(agent_slug)
        return None

    @staticmethod
    def _sanitize_updates(updates: dict) -> tuple[dict, list[str]]:
        """Split updates into Redis HSET mapping vs. HDEL removals."""

        mapping: dict[str, str] = {}
        removals: list[str] = []

        for key, value in updates.items():
            if value is None:
                removals.append(key)
                continue
            if key == "disabled":
                mapping[key] = "1" if bool(value) else "0"
            elif key in {"no_reply", "routing_only"}:
                mapping[key] = "1" if bool(value) else "0"
            elif key in {"user_hourly_limit", "user_daily_limit", "agent_hourly_limit", "agent_daily_limit"}:
                mapping[key] = str(int(value))
            elif key in {"reason", "no_reply_reason", "routing_only_reason"}:
                mapping[key] = str(value)
            elif key in {"disabled_until", "no_reply_until", "routing_only_until"}:
                if isinstance(value, datetime):
                    mapping[key] = value.astimezone(timezone.utc).isoformat()
                else:
                    mapping[key] = str(value)
            else:
                logger.debug("Ignoring unsupported agent control field: %s", key)
        return mapping, removals


__all__ = [
    "AgentControlService",
    "AgentControlState",
    "AgentRateLimitConfig",
]
