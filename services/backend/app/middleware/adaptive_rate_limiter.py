"""
Adaptive Rate Limiter for Production (GCP-Ready)
Prevents token refresh storms and DDOS with intelligent backoff
Designed for distributed systems with Redis/Memorystore backend
"""

import asyncio
import time
import hashlib
import json
import logging
import os
from typing import Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
import redis.asyncio as redis
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class RateLimitStrategy(Enum):
    """Rate limiting strategies"""
    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"
    ADAPTIVE = "adaptive"


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting"""
    # Basic settings
    requests_per_minute: int = 10
    burst_capacity: int = 20

    # Backoff settings
    min_backoff_seconds: int = 1
    max_backoff_seconds: int = 3600  # 1 hour
    backoff_multiplier: float = 2.0

    # Advanced settings
    strategy: RateLimitStrategy = RateLimitStrategy.ADAPTIVE
    enable_distributed_sync: bool = True
    enable_cost_tracking: bool = True  # For GCP billing

    # GCP-specific
    gcp_project_id: Optional[str] = None
    gcp_region: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        data = asdict(self)
        data['strategy'] = self.strategy.value
        return data


class AdaptiveRateLimiter:
    """
    Production-ready adaptive rate limiter
    Features:
    - Multiple rate limiting strategies
    - Distributed synchronization via Redis
    - Cost tracking for cloud providers
    - Automatic strategy switching based on load
    - Request coalescing for duplicate requests
    """

    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        config: Optional[RateLimitConfig] = None,
        namespace: str = "rate_limit"
    ):
        """
        Initialize the rate limiter

        Args:
            redis_client: Redis client for distributed state
            config: Rate limiting configuration
            namespace: Redis key namespace
        """
        self.redis = redis_client
        self.config = config or RateLimitConfig()
        self.namespace = namespace

        # Local state for fallback
        self.local_state: Dict[str, Dict] = {}

        # Request coalescing
        self.pending_requests: Dict[str, asyncio.Future] = {}

        # Metrics
        self.metrics = {
            "total_requests": 0,
            "blocked_requests": 0,
            "coalesced_requests": 0,
            "estimated_cost_saved": 0.0
        }

        # Start background tasks
        self._start_background_tasks()

    def _start_background_tasks(self):
        """Start background maintenance tasks"""
        asyncio.create_task(self._cleanup_loop())
        asyncio.create_task(self._metrics_reporter())
        if self.config.enable_distributed_sync:
            asyncio.create_task(self._sync_loop())

    async def check_rate_limit(
        self,
        identifier: str,
        cost: float = 1.0,
        metadata: Optional[Dict] = None
    ) -> Tuple[bool, Optional[int], Optional[Dict]]:
        """
        Check if request is allowed under rate limit

        Args:
            identifier: Unique identifier (e.g., agent_id, user_id, IP)
            cost: Cost weight of this request (for weighted rate limiting)
            metadata: Additional metadata for decision making

        Returns:
            Tuple of (allowed, retry_after_seconds, details)
        """
        self.metrics["total_requests"] += 1

        # Request coalescing - if identical request is pending, wait for it
        coalesce_key = f"{identifier}:{cost}"
        if coalesce_key in self.pending_requests:
            self.metrics["coalesced_requests"] += 1
            logger.info(f"Coalescing request for {identifier}")
            try:
                return await self.pending_requests[coalesce_key]
            except Exception:
                pass  # Original request failed, proceed with new check

        # Create future for this request
        future = asyncio.Future()
        self.pending_requests[coalesce_key] = future

        try:
            # Perform the actual rate limit check
            result = await self._do_rate_limit_check(identifier, cost, metadata)
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            # Clean up coalescing entry
            self.pending_requests.pop(coalesce_key, None)

    async def _do_rate_limit_check(
        self,
        identifier: str,
        cost: float,
        metadata: Optional[Dict]
    ) -> Tuple[bool, Optional[int], Optional[Dict]]:
        """
        Internal rate limit check implementation
        """
        current_time = time.time()

        # Choose strategy based on configuration
        if self.config.strategy == RateLimitStrategy.ADAPTIVE:
            # Adaptive strategy switches based on load
            load = await self._get_system_load()
            if load > 0.8:
                strategy = RateLimitStrategy.FIXED_WINDOW
            elif load > 0.5:
                strategy = RateLimitStrategy.SLIDING_WINDOW
            else:
                strategy = RateLimitStrategy.TOKEN_BUCKET
        else:
            strategy = self.config.strategy

        # Apply the selected strategy
        if strategy == RateLimitStrategy.TOKEN_BUCKET:
            return await self._token_bucket_check(identifier, cost, current_time)
        elif strategy == RateLimitStrategy.SLIDING_WINDOW:
            return await self._sliding_window_check(identifier, cost, current_time)
        else:
            return await self._fixed_window_check(identifier, cost, current_time)

    async def _token_bucket_check(
        self,
        identifier: str,
        cost: float,
        current_time: float
    ) -> Tuple[bool, Optional[int], Optional[Dict]]:
        """
        Token bucket algorithm - allows bursts but maintains average rate
        """
        key = f"{self.namespace}:bucket:{identifier}"

        if self.redis:
            try:
                # Atomic token bucket update using Lua script
                lua_script = """
                local key = KEYS[1]
                local capacity = tonumber(ARGV[1])
                local refill_rate = tonumber(ARGV[2])
                local cost = tonumber(ARGV[3])
                local current_time = tonumber(ARGV[4])

                local bucket = redis.call('HGETALL', key)
                local tokens = capacity
                local last_refill = current_time

                if #bucket > 0 then
                    for i = 1, #bucket, 2 do
                        if bucket[i] == 'tokens' then
                            tokens = tonumber(bucket[i + 1])
                        elseif bucket[i] == 'last_refill' then
                            last_refill = tonumber(bucket[i + 1])
                        end
                    end

                    -- Refill tokens
                    local time_passed = current_time - last_refill
                    local tokens_to_add = time_passed * refill_rate
                    tokens = math.min(capacity, tokens + tokens_to_add)
                end

                if tokens >= cost then
                    -- Consume tokens
                    tokens = tokens - cost
                    redis.call('HSET', key, 'tokens', tokens, 'last_refill', current_time)
                    redis.call('EXPIRE', key, 3600)
                    return {1, tokens}
                else
                    -- Not enough tokens
                    local tokens_needed = cost - tokens
                    local wait_time = tokens_needed / refill_rate
                    return {0, wait_time}
                end
                """

                result = await self.redis.eval(
                    lua_script,
                    1,
                    key,
                    self.config.burst_capacity,
                    self.config.requests_per_minute / 60,  # tokens per second
                    cost,
                    current_time
                )

                if result[0] == 1:
                    return (True, None, {"tokens_remaining": result[1]})
                else:
                    retry_after = int(result[1]) + 1
                    self.metrics["blocked_requests"] += 1
                    return (False, retry_after, {"reason": "token_bucket_exhausted"})

            except Exception as e:
                logger.error(f"Redis token bucket check failed: {e}")

        # Fallback to local state
        return self._local_token_bucket_check(identifier, cost, current_time)

    async def _sliding_window_check(
        self,
        identifier: str,
        cost: float,
        current_time: float
    ) -> Tuple[bool, Optional[int], Optional[Dict]]:
        """
        Sliding window algorithm - smooth rate limiting
        """
        window_size = 60  # 1 minute window
        key = f"{self.namespace}:sliding:{identifier}"

        if self.redis:
            try:
                # Remove old entries and count recent ones
                window_start = current_time - window_size

                # Atomic sliding window using sorted sets
                pipe = self.redis.pipeline()
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zcard(key)
                pipe.zadd(key, {str(current_time): current_time})
                pipe.expire(key, window_size + 10)

                results = await pipe.execute()
                request_count = results[1]

                if request_count < self.config.requests_per_minute:
                    return (True, None, {"requests_in_window": request_count})
                else:
                    # Calculate when oldest request expires
                    oldest = await self.redis.zrange(key, 0, 0, withscores=True)
                    if oldest:
                        retry_after = int(window_size - (current_time - oldest[0][1])) + 1
                    else:
                        retry_after = 60

                    self.metrics["blocked_requests"] += 1
                    return (False, retry_after, {"reason": "sliding_window_exceeded"})

            except Exception as e:
                logger.error(f"Redis sliding window check failed: {e}")

        # Fallback to local state
        return self._local_sliding_window_check(identifier, cost, current_time)

    async def _fixed_window_check(
        self,
        identifier: str,
        cost: float,
        current_time: float
    ) -> Tuple[bool, Optional[int], Optional[Dict]]:
        """
        Fixed window algorithm - simple and efficient
        """
        window_size = 60  # 1 minute window
        window_id = int(current_time // window_size)
        key = f"{self.namespace}:fixed:{identifier}:{window_id}"

        if self.redis:
            try:
                # Increment counter for current window
                count = await self.redis.incr(key)
                if count == 1:
                    # First request in window, set expiry
                    await self.redis.expire(key, window_size + 10)

                if count <= self.config.requests_per_minute:
                    return (True, None, {"requests_in_window": count})
                else:
                    # Wait until next window
                    next_window = (window_id + 1) * window_size
                    retry_after = int(next_window - current_time) + 1

                    self.metrics["blocked_requests"] += 1

                    # Apply exponential backoff for repeat offenders
                    violations_key = f"{self.namespace}:violations:{identifier}"
                    violations = await self.redis.incr(violations_key)
                    await self.redis.expire(violations_key, 3600)

                    if violations > 3:
                        backoff = min(
                            self.config.max_backoff_seconds,
                            self.config.min_backoff_seconds * (self.config.backoff_multiplier ** violations)
                        )
                        retry_after = int(backoff)

                    return (False, retry_after, {
                        "reason": "fixed_window_exceeded",
                        "violations": violations
                    })

            except Exception as e:
                logger.error(f"Redis fixed window check failed: {e}")

        # Fallback to local state
        return self._local_fixed_window_check(identifier, cost, current_time)

    def _local_token_bucket_check(
        self,
        identifier: str,
        cost: float,
        current_time: float
    ) -> Tuple[bool, Optional[int], Optional[Dict]]:
        """Local fallback for token bucket"""
        if identifier not in self.local_state:
            self.local_state[identifier] = {
                "tokens": self.config.burst_capacity,
                "last_refill": current_time
            }

        state = self.local_state[identifier]

        # Refill tokens
        time_passed = current_time - state["last_refill"]
        refill_rate = self.config.requests_per_minute / 60
        tokens_to_add = time_passed * refill_rate
        state["tokens"] = min(self.config.burst_capacity, state["tokens"] + tokens_to_add)
        state["last_refill"] = current_time

        if state["tokens"] >= cost:
            state["tokens"] -= cost
            return (True, None, {"tokens_remaining": state["tokens"]})
        else:
            tokens_needed = cost - state["tokens"]
            wait_time = int(tokens_needed / refill_rate) + 1
            self.metrics["blocked_requests"] += 1
            return (False, wait_time, {"reason": "local_token_bucket_exhausted"})

    def _local_sliding_window_check(
        self,
        identifier: str,
        cost: float,
        current_time: float
    ) -> Tuple[bool, Optional[int], Optional[Dict]]:
        """Local fallback for sliding window"""
        window_size = 60

        if identifier not in self.local_state:
            self.local_state[identifier] = {"requests": []}

        state = self.local_state[identifier]

        # Remove old requests
        window_start = current_time - window_size
        state["requests"] = [t for t in state["requests"] if t > window_start]

        if len(state["requests"]) < self.config.requests_per_minute:
            state["requests"].append(current_time)
            return (True, None, {"requests_in_window": len(state["requests"])})
        else:
            oldest = min(state["requests"]) if state["requests"] else current_time
            retry_after = int(window_size - (current_time - oldest)) + 1
            self.metrics["blocked_requests"] += 1
            return (False, retry_after, {"reason": "local_sliding_window_exceeded"})

    def _local_fixed_window_check(
        self,
        identifier: str,
        cost: float,
        current_time: float
    ) -> Tuple[bool, Optional[int], Optional[Dict]]:
        """Local fallback for fixed window"""
        window_size = 60
        window_id = int(current_time // window_size)

        if identifier not in self.local_state:
            self.local_state[identifier] = {}

        state = self.local_state[identifier]

        if "window_id" not in state or state["window_id"] != window_id:
            # New window
            state["window_id"] = window_id
            state["count"] = 0
            state["violations"] = 0

        state["count"] += cost

        if state["count"] <= self.config.requests_per_minute:
            return (True, None, {"requests_in_window": state["count"]})
        else:
            next_window = (window_id + 1) * window_size
            retry_after = int(next_window - current_time) + 1

            state["violations"] += 1
            if state["violations"] > 3:
                backoff = min(
                    self.config.max_backoff_seconds,
                    self.config.min_backoff_seconds * (self.config.backoff_multiplier ** state["violations"])
                )
                retry_after = int(backoff)

            self.metrics["blocked_requests"] += 1
            return (False, retry_after, {
                "reason": "local_fixed_window_exceeded",
                "violations": state["violations"]
            })

    async def _get_system_load(self) -> float:
        """
        Get current system load for adaptive strategy
        Returns value between 0 and 1
        """
        if self.redis:
            try:
                # Check Redis memory usage as proxy for load
                info = await self.redis.info("memory")
                used = float(info.get("used_memory", 0))
                max_mem = float(info.get("maxmemory", 1))
                if max_mem > 0:
                    return min(1.0, used / max_mem)
            except Exception:
                pass

        # Fallback: estimate based on recent blocks
        if self.metrics["total_requests"] > 0:
            return self.metrics["blocked_requests"] / self.metrics["total_requests"]
        return 0.0

    async def _cleanup_loop(self):
        """Periodic cleanup of old data"""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes

                # Clean local state
                current_time = time.time()
                cutoff = current_time - 3600  # 1 hour

                for identifier in list(self.local_state.keys()):
                    state = self.local_state[identifier]
                    if isinstance(state, dict):
                        if state.get("last_refill", current_time) < cutoff:
                            del self.local_state[identifier]

                logger.info(f"Cleaned up rate limiter state, {len(self.local_state)} entries remaining")

            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    async def _metrics_reporter(self):
        """Report metrics periodically"""
        while True:
            try:
                await asyncio.sleep(60)  # Every minute

                if self.config.enable_cost_tracking and self.metrics["blocked_requests"] > 0:
                    # Estimate cost saved by blocking requests
                    # Assume $0.001 per API request on GCP (adjust as needed)
                    cost_per_request = 0.001
                    self.metrics["estimated_cost_saved"] = self.metrics["blocked_requests"] * cost_per_request

                logger.info(f"Rate limiter metrics: {json.dumps(self.metrics)}")

                # Push to monitoring system if configured
                if self.config.gcp_project_id:
                    await self._push_to_cloud_monitoring()

            except Exception as e:
                logger.error(f"Metrics reporter error: {e}")

    async def _sync_loop(self):
        """Sync distributed state"""
        while True:
            try:
                await asyncio.sleep(10)  # Every 10 seconds

                if self.redis:
                    # Publish heartbeat
                    await self.redis.publish(
                        f"{self.namespace}:heartbeat",
                        json.dumps({
                            "node_id": os.getenv("HOSTNAME", "unknown"),
                            "timestamp": time.time(),
                            "metrics": self.metrics
                        })
                    )

            except Exception as e:
                logger.error(f"Sync loop error: {e}")

    async def _push_to_cloud_monitoring(self):
        """Push metrics to GCP Cloud Monitoring"""
        # This would integrate with Google Cloud Monitoring API
        # For now, just log it
        logger.info(f"Would push metrics to GCP project {self.config.gcp_project_id}")

    async def reset_limits(self, identifier: str, reason: str = "manual"):
        """
        Reset rate limits for an identifier

        Args:
            identifier: Identifier to reset
            reason: Reason for reset
        """
        logger.info(f"Resetting rate limits for {identifier}: {reason}")

        # Clear Redis state
        if self.redis:
            try:
                pattern = f"{self.namespace}:*:{identifier}*"
                cursor = 0
                while True:
                    cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
                    if keys:
                        await self.redis.delete(*keys)
                    if cursor == 0:
                        break
            except Exception as e:
                logger.error(f"Failed to clear Redis state: {e}")

        # Clear local state
        self.local_state.pop(identifier, None)

        # Clear pending requests
        keys_to_clear = [k for k in self.pending_requests if k.startswith(identifier)]
        for key in keys_to_clear:
            future = self.pending_requests.pop(key, None)
            if future and not future.done():
                future.cancel()

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics"""
        return {
            **self.metrics,
            "config": self.config.to_dict(),
            "local_state_size": len(self.local_state),
            "pending_requests": len(self.pending_requests)
        }


# Global instance
_rate_limiter: Optional[AdaptiveRateLimiter] = None


async def get_adaptive_rate_limiter() -> AdaptiveRateLimiter:
    """Get or create the global adaptive rate limiter"""
    global _rate_limiter

    if _rate_limiter is None:
        # Get Redis connection
        redis_client = None
        try:
            redis_url = os.getenv("REDIS_URL", "redis://redis_new:6379/0")
            redis_client = await redis.from_url(redis_url, decode_responses=False)
            await redis_client.ping()
            logger.info("Adaptive rate limiter connected to Redis")
        except Exception as e:
            logger.warning(f"Redis not available for adaptive rate limiter: {e}")

        # Configure based on environment
        environment = os.getenv("ENVIRONMENT", "development")

        if environment == "production":
            # Production: Support agent factory with multiple concurrent agents
            # Agent factory may deploy 10-25 agents simultaneously during rollout
            config = RateLimitConfig(
                requests_per_minute=120,  # 2 req/sec sustained (4x increase from 30)
                burst_capacity=500,       # Handle 25+ agents bursting (10x increase from 50)
                strategy=RateLimitStrategy.ADAPTIVE,
                enable_distributed_sync=True,
                enable_cost_tracking=True,
                gcp_project_id=os.getenv("GCP_PROJECT_ID"),
                gcp_region=os.getenv("GCP_REGION", "us-central1")
            )
        else:
            # Development: Support agent studio with many concurrent agents
            # Agent studios may connect 100+ agents at once during initialization
            config = RateLimitConfig(
                requests_per_minute=300,  # High throughput for studio testing
                burst_capacity=1000,      # Support 100+ agent connections in burst
                strategy=RateLimitStrategy.TOKEN_BUCKET,  # Most flexible for bursts
                enable_distributed_sync=True,  # Use Redis
                enable_cost_tracking=False,
                min_backoff_seconds=1,    # Short backoff for dev iteration
                max_backoff_seconds=60    # 1 minute max
            )

        _rate_limiter = AdaptiveRateLimiter(
            redis_client=redis_client,
            config=config,
            namespace="adaptive_rate"
        )

    return _rate_limiter
