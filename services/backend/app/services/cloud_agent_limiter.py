"""Lightweight rate limiting for cloud agents with daily caps and cost metering."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

from app.core.config import settings


@dataclass
class LimitOutcome:
    blocked: bool
    window: Optional[str] = None
    count: int = 0
    limit: int = 0
    retry_after_seconds: int = 0
    scope: Optional[str] = None
    reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "blocked": self.blocked,
            "window": self.window,
            "count": self.count,
            "limit": self.limit,
            "retry_after_seconds": self.retry_after_seconds,
            "scope": self.scope,
            "reason": self.reason,
            "metadata": self.metadata,
        }


@dataclass
class CostOutcome:
    cents_added: int
    estimate: bool
    buckets_crossed: Dict[str, str]
    totals: Dict[str, int]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "cents_added": self.cents_added,
            "estimate": self.estimate,
            "buckets_crossed": self.buckets_crossed,
            "totals": self.totals,
        }


class CloudAgentLimiter:
    """
    Enforces lightweight limits for cloud agents.

    The only blocking control is a per-user daily message cap to stop runaway usage.
    Cost tracking remains warn-only so we still have monitoring signals without
    preventing traffic.
    """

    def __init__(self, redis_client, *, logger: Optional[logging.Logger] = None):
        self.redis = redis_client
        self.logger = logger or logging.getLogger(__name__)

        self.default_limits = {
            "burst_limit": settings.cloud_agent_burst_limit,
            "burst_window": settings.cloud_agent_burst_window_seconds,
            "sustained_limit": settings.cloud_agent_sustained_limit,
            "sustained_window": settings.cloud_agent_sustained_window_seconds,
            "daily_agent_limit": settings.cloud_agent_daily_agent_limit,
            "daily_org_limit": settings.cloud_agent_daily_org_limit,
            "daily_user_limit": settings.cloud_agent_per_user_daily_limit,
            "consecutive_limit": settings.cloud_agent_consecutive_limit,
            "consecutive_ttl": settings.cloud_agent_consecutive_ttl_seconds,
            "agent_budget_cents": settings.cloud_agent_daily_budget_cents,
            "org_budget_cents": settings.cloud_agent_org_daily_budget_cents,
            "user_budget_cents": settings.cloud_agent_user_daily_budget_cents,
            "platform_budget_cents": settings.cloud_agent_platform_daily_budget_cents,
        }
        self.absolute_limit_caps = {
            "burst_limit": int(os.getenv("CLOUD_AGENT_MAX_BURST_LIMIT", "10") or 10),
            "sustained_limit": int(os.getenv("CLOUD_AGENT_MAX_SUSTAINED_LIMIT", "100") or 100),
            "daily_agent_limit": int(os.getenv("CLOUD_AGENT_MAX_DAILY_AGENT_LIMIT", "300") or 300),
            "daily_org_limit": int(os.getenv("CLOUD_AGENT_MAX_DAILY_ORG_LIMIT", "0") or 0),
            "daily_user_limit": int(os.getenv("CLOUD_AGENT_MAX_DAILY_USER_LIMIT", "1000") or 1000),
        }

        self.tier_map = settings.cloud_agent_tiers

        self.input_cost_per_1m = settings.gemini_input_cost_per_1m
        self.output_cost_per_1m = settings.gemini_output_cost_per_1m

    def _key(self, agent_id: str, space_id: str, suffix: str) -> str:
        return f"ax:cloud-agent:{agent_id}:{space_id}:{suffix}"

    async def _increment_with_ttl(self, key: str, ttl_seconds: int) -> tuple[int, int]:
        count = await self.redis.incr(key)
        ttl = await self.redis.ttl(key)
        if ttl is None or ttl < 0:
            await self.redis.expire(key, ttl_seconds)
            ttl = ttl_seconds
        return int(count), int(ttl)

    async def _check_window(
        self, *, agent_id: str, space_id: str, suffix: str, limit: int, ttl_seconds: int
    ) -> Optional[LimitOutcome]:
        if limit is None or limit < 1:
            return None
        key = self._key(agent_id, space_id, suffix)
        count, ttl = await self._increment_with_ttl(key, ttl_seconds)
        if count > limit:
            return LimitOutcome(
                blocked=True,
                window=suffix,
                count=count,
                limit=limit,
                retry_after_seconds=ttl,
                scope="agent",
                reason="window",
                metadata={
                    "window_seconds": ttl_seconds,
                    "limit": limit,
                    "count": count,
                    "reason": suffix,
                },
            )
        return None

    async def _check_daily_scopes(
        self, *, agent_id: str, space_id: str, user_id: Optional[str], limits: Dict[str, int]
    ) -> Optional[LimitOutcome]:
        # Per-user daily (if user id is provided)
        if user_id and limits["daily_user_limit"] and limits["daily_user_limit"] > 0:
            outcome = await self._check_window(
                agent_id=f"user-{user_id}",
                space_id=space_id,
                suffix="daily",
                limit=limits["daily_user_limit"],
                ttl_seconds=86400,
            )
            if outcome:
                outcome.scope = "user"
                outcome.reason = "daily_user"
                return outcome

        # Agent daily
        if limits["daily_agent_limit"] and limits["daily_agent_limit"] > 0:
            outcome = await self._check_window(
                agent_id=agent_id,
                space_id=space_id,
                suffix="daily",
                limit=limits["daily_agent_limit"],
                ttl_seconds=86400,
            )
            if outcome:
                outcome.scope = "agent"
                outcome.reason = "daily_agent"
                return outcome

        # Org/project daily
        if limits["daily_org_limit"] and limits["daily_org_limit"] > 0:
            outcome = await self._check_window(
                agent_id="org",
                space_id=space_id,
                suffix="daily",
                limit=limits["daily_org_limit"],
                ttl_seconds=86400,
            )
            if outcome:
                outcome.scope = "org"
                outcome.reason = "daily_org"
                return outcome

        return None

    async def check_and_increment(
        self,
        *,
        agent_id: str,
        space_id: str,
        user_id: Optional[str],
        sender_is_human: bool,
        tier: Optional[str] = None,
        user_daily_limit_override: Optional[int] = None,
    ) -> LimitOutcome:
        """
        Increment counters and return a LimitOutcome indicating whether the send should be blocked.
        """
        if not self.redis:
            return LimitOutcome(blocked=False)

        agent_id = str(agent_id)
        space_id = str(space_id)
        user_id = str(user_id) if user_id else None

        if user_daily_limit_override is None:
            user_daily_limit_override = await self._get_user_override(user_id) if user_id else None

        limits = self._limits_for_tier(tier, override_daily_user_limit=user_daily_limit_override)

        try:
            # Burst window (anti-loop)
            if limits["burst_limit"] and limits["burst_limit"] > 0:
                burst_outcome = await self._check_window(
                    agent_id=agent_id,
                    space_id=space_id,
                    suffix="burst",
                    limit=limits["burst_limit"],
                    ttl_seconds=limits["burst_window"],
                )
                if burst_outcome:
                    burst_outcome.scope = "agent"
                    burst_outcome.reason = "burst"
                    burst_outcome.metadata = (burst_outcome.metadata or {}) | {
                        "window_seconds": limits["burst_window"],
                        "tier": tier,
                    }
                    self._log_block(agent_id, space_id, tier, burst_outcome)
                    return burst_outcome

            # Sustained window
            if limits["sustained_limit"] and limits["sustained_limit"] > 0:
                sustained_outcome = await self._check_window(
                    agent_id=agent_id,
                    space_id=space_id,
                    suffix="sustained",
                    limit=limits["sustained_limit"],
                    ttl_seconds=limits["sustained_window"],
                )
                if sustained_outcome:
                    sustained_outcome.scope = "agent"
                    sustained_outcome.reason = "sustained"
                    sustained_outcome.metadata = (sustained_outcome.metadata or {}) | {
                        "window_seconds": limits["sustained_window"],
                        "tier": tier,
                    }
                    self._log_block(agent_id, space_id, tier, sustained_outcome)
                    return sustained_outcome

            daily_outcome = await self._check_daily_scopes(
                agent_id=agent_id,
                space_id=space_id,
                user_id=user_id,
                limits=limits,
            )
            if daily_outcome:
                # Attach usage snapshot for observability even when blocked
                usage = await self._capture_usage(
                    agent_id=agent_id,
                    space_id=space_id,
                    user_id=user_id,
                    limits=limits,
                )
                if usage:
                    daily_outcome.metadata = (daily_outcome.metadata or {}) | {"usage": usage}
                self._log_block(agent_id, space_id, tier, daily_outcome)
                return daily_outcome

            usage = await self._capture_usage(
                agent_id=agent_id,
                space_id=space_id,
                user_id=user_id,
                limits=limits,
            )

            return LimitOutcome(blocked=False, metadata={"usage": usage} if usage else None)
        except Exception as exc:  # defensive: never block on limiter failure
            self.logger.warning("CloudAgentLimiter failed open: %s", exc)
            return LimitOutcome(blocked=False)

    async def _capture_usage(
        self,
        *,
        agent_id: str,
        space_id: str,
        user_id: Optional[str],
        limits: Dict[str, int],
    ) -> Optional[Dict[str, Any]]:
        """Capture current daily usage for observability (best-effort)."""
        if not user_id or not self.redis:
            return None

        try:
            key = self._key(agent_id=f"user-{user_id}", space_id=space_id, suffix="daily")
            raw_count = await self.redis.get(key)
            count = int(raw_count) if raw_count is not None else 0
            ttl_raw = await self.redis.ttl(key)
            ttl = int(ttl_raw) if ttl_raw is not None else -1

            limit = limits.get("daily_user_limit") or 0
            remaining = max(0, limit - count) if limit else None

            usage = {
                "scope": "user",
                "user_id": str(user_id),
                "space_id": str(space_id),
                "agent_id": str(agent_id),
                "count": count,
                "limit": limit,
                "remaining": remaining,
                "ttl_seconds": ttl,
            }

            self.logger.info("CLOUD_AGENT_USAGE", extra=usage)
            return usage
        except Exception as exc:
            self.logger.warning("CloudAgentLimiter usage logging failed: %s", exc)
            return None

    async def _get_user_override(self, user_id: Optional[str]) -> Optional[int]:
        """Fetch per-user override limit if present."""
        if not user_id or not self.redis:
            return None
        try:
            key = f"ax:cloud-agent:limit:user:{user_id}"
            raw = await self.redis.get(key)
            if raw is None:
                return None
            val = int(raw)
            if val <= 0:
                return None
            # Safety cap to avoid accidental "infinite" limits
            return min(val, 1000)
        except Exception as exc:
            self.logger.warning("CloudAgentLimiter override lookup failed: %s", exc)
            return None

    async def record_cost(
        self,
        *,
        agent_id: str,
        space_id: str,
        user_id: Optional[str],
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        input_chars: Optional[int],
        output_chars: Optional[int],
        tier: Optional[str] = None,
    ) -> Optional[CostOutcome]:
        """
        Warn-only cost tracking. Returns CostOutcome with buckets crossed; never blocks.
        """
        if not self.redis:
            return None

        tokens_in, tokens_out, estimate = self._resolve_tokens(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_chars=input_chars,
            output_chars=output_chars,
        )
        if tokens_in is None and tokens_out is None:
            return None

        cents = self._calculate_cents(tokens_in or 0, tokens_out or 0)
        today = datetime.now(timezone.utc).date().isoformat()

        totals: Dict[str, int] = {}
        buckets_crossed: Dict[str, str] = {}

        limits = self._limits_for_tier(tier)

        scopes: list[Tuple[str, str, int]] = [
            ("agent", str(agent_id), limits["agent_budget_cents"]),
            ("org", str(space_id), limits["org_budget_cents"]),
            ("platform", "platform", limits["platform_budget_cents"]),
        ]
        if user_id:
            scopes.append(("user", str(user_id), limits["user_budget_cents"]))

        for scope, scope_id, budget in scopes:
            total, bucket = await self._record_scope_cost(
                scope=scope,
                scope_id=scope_id,
                budget_cents=budget,
                cents=cents,
                date=today,
                tier=tier,
            )
            totals[scope] = total
            if bucket:
                buckets_crossed[scope] = bucket

        return CostOutcome(
            cents_added=cents,
            estimate=estimate,
            buckets_crossed=buckets_crossed,
            totals=totals,
        )

    def _log_block(self, agent_id: str, space_id: str, tier: Optional[str], outcome: LimitOutcome) -> None:
        self.logger.warning(
            "CLOUD_AGENT_RATE_LIMIT",
            extra={
                "agent_id": agent_id,
                "space_id": space_id,
                "tier": tier,
                "window": outcome.window,
                "limit": outcome.limit,
                "count": outcome.count,
                "retry_after_seconds": outcome.retry_after_seconds,
                "scope": outcome.scope,
                "reason": outcome.reason,
            },
        )

    def _resolve_tokens(
        self,
        *,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        input_chars: Optional[int],
        output_chars: Optional[int],
    ) -> Tuple[Optional[int], Optional[int], bool]:
        estimate = False
        tin = input_tokens
        tout = output_tokens

        if tin is None and input_chars is not None:
            tin = int(input_chars * 0.25)
            estimate = True
        if tout is None and output_chars is not None:
            tout = int(output_chars * 0.25)
            estimate = True
        if tin is None and tout is None:
            return None, None, estimate
        return tin or 0, tout or 0, estimate

    def _calculate_cents(self, tokens_in: int, tokens_out: int) -> int:
        cost_dollars = (
            (tokens_in / 1_000_000) * self.input_cost_per_1m
            + (tokens_out / 1_000_000) * self.output_cost_per_1m
        )
        return int(round(cost_dollars * 100))

    async def _record_scope_cost(
        self,
        *,
        scope: str,
        scope_id: str,
        budget_cents: int,
        cents: int,
        date: str,
        tier: Optional[str] = None,
    ) -> Tuple[int, Optional[str]]:
        key = f"ax:cloud-agent:cost:{scope}:{scope_id}:{date}"

        try:
            pipe = self.redis.pipeline()
            pipe.hget(key, "cents")
            pipe.hget(key, "last_bucket")
            pipe.hincrby(key, "cents", cents)
            pipe.expire(key, 86400)
            prev_cents, prev_bucket, new_cents, _ = await pipe.execute()
        except Exception as exc:
            self.logger.warning("CloudAgentLimiter cost tracking failed (scope=%s): %s", scope, exc)
            return 0, None

        prev_cents_int = int(prev_cents) if prev_cents is not None else 0
        prev_bucket_str = prev_bucket if isinstance(prev_bucket, str) else (prev_bucket.decode() if isinstance(prev_bucket, bytes) else None)
        new_cents_int = int(new_cents) if new_cents is not None else cents

        bucket_crossed = None
        if budget_cents and budget_cents > 0:
            ratio = new_cents_int / budget_cents
            bucket_crossed = self._bucket(prev_bucket_str, ratio)
            if bucket_crossed:
                try:
                    await self.redis.hset(key, "last_bucket", bucket_crossed)
                except Exception:
                    pass
                self.logger.warning(
                    "CLOUD_AGENT_COST_THRESHOLD",
                    extra={
                        "scope": scope,
                        "scope_id": scope_id,
                        "tier": tier,
                        "bucket": bucket_crossed,
                        "budget_cents": budget_cents,
                        "new_cents": new_cents_int,
                        "cents_added": cents,
                    },
                )

        # Always log cost increments for visibility (info level to avoid alert fatigue)
        self.logger.info(
            "CLOUD_AGENT_COST",
            extra={
                "scope": scope,
                "scope_id": scope_id,
                "tier": tier,
                "new_cents": new_cents_int,
                "cents_added": cents,
                "previous_cents": prev_cents_int,
            },
        )

        return new_cents_int, bucket_crossed

    @staticmethod
    def _bucket(prev_bucket: Optional[str], ratio: float) -> Optional[str]:
        thresholds = [("100", 1.0), ("90", 0.9), ("75", 0.75)]
        for label, value in thresholds:
            if ratio >= value:
                # Only fire if we crossed a new threshold
                if prev_bucket is None or float(prev_bucket) < float(label):
                    return label
                break
        return None

    def _limits_for_tier(self, tier: Optional[str], override_daily_user_limit: Optional[int] = None) -> Dict[str, int]:
        t = (tier or "regular").lower()
        cfg = self.tier_map.get(t, {})
        # Resolve legacy aliases (free→regular, pro→plus, enterprise→admin)
        if "_alias" in cfg:
            cfg = self.tier_map.get(cfg["_alias"], {})
        daily_user_limit = (
            override_daily_user_limit
            if override_daily_user_limit is not None
            else cfg.get("daily_user_limit", self.default_limits["daily_user_limit"])
        )
        daily_org_limit = self.default_limits["daily_org_limit"]
        return {
            "burst_limit": self._cap_limit(
                "burst_limit", cfg.get("burst_limit", self.default_limits["burst_limit"])
            ),
            "burst_window": self.default_limits["burst_window"],
            "sustained_limit": self._cap_limit(
                "sustained_limit", cfg.get("sustained_limit", self.default_limits["sustained_limit"])
            ),
            "sustained_window": self.default_limits["sustained_window"],
            "daily_agent_limit": self._cap_limit(
                "daily_agent_limit", cfg.get("daily_agent_limit", self.default_limits["daily_agent_limit"])
            ),
            "daily_org_limit": self._cap_limit("daily_org_limit", daily_org_limit),
            "daily_user_limit": self._cap_limit("daily_user_limit", daily_user_limit),
            "consecutive_limit": self.default_limits["consecutive_limit"],
            "consecutive_ttl": self.default_limits["consecutive_ttl"],
            "agent_budget_cents": cfg.get("agent_budget_cents", self.default_limits["agent_budget_cents"]),
            "org_budget_cents": cfg.get("org_budget_cents", self.default_limits["org_budget_cents"]),
            "user_budget_cents": cfg.get("user_budget_cents", self.default_limits["user_budget_cents"]),
            "platform_budget_cents": self.default_limits["platform_budget_cents"],
        }

    def _cap_limit(self, name: str, raw_value: Optional[int]) -> int:
        value = int(raw_value or 0)
        cap = int(self.absolute_limit_caps.get(name, 0) or 0)
        if value <= 0 or cap <= 0:
            return value
        return min(value, cap)


__all__ = ["CloudAgentLimiter", "LimitOutcome", "CostOutcome"]
