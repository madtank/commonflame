"""
Token Refresh Rate Limiter
Prevents token refresh storms that can DDOS the API during idle periods
Implements exponential backoff and per-agent rate limiting
"""

import time
import asyncio
import logging
from typing import Dict, Optional, Tuple
from collections import defaultdict, deque
from datetime import datetime, timedelta
import redis.asyncio as redis
import json

logger = logging.getLogger(__name__)


class TokenRefreshRateLimiter:
    """
    Advanced rate limiter for token refresh operations
    Features:
    - Per-agent rate limiting
    - Exponential backoff for repeat offenders
    - Redis-backed persistence for distributed systems
    - Automatic cleanup of old entries
    """

    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        base_window_seconds: int = 60,
        max_refreshes_per_window: int = 2,
        backoff_multiplier: float = 2.0,
        max_backoff_seconds: int = 3600,  # 1 hour max
        cleanup_interval_seconds: int = 300  # 5 minutes
    ):
        """
        Initialize the rate limiter

        Args:
            redis_client: Redis client for distributed rate limiting
            base_window_seconds: Base time window for rate limiting
            max_refreshes_per_window: Maximum refreshes allowed per window
            backoff_multiplier: Multiplier for exponential backoff
            max_backoff_seconds: Maximum backoff time
            cleanup_interval_seconds: How often to cleanup old entries
        """
        self.redis_client = redis_client
        self.base_window_seconds = base_window_seconds
        self.max_refreshes_per_window = max_refreshes_per_window
        self.backoff_multiplier = backoff_multiplier
        self.max_backoff_seconds = max_backoff_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds

        # In-memory cache for fast lookups (if Redis not available)
        self.local_cache: Dict[str, Dict] = {}
        self.last_cleanup = time.time()

        # Track violation counts for exponential backoff
        self.violation_counts: Dict[str, int] = defaultdict(int)

        # Start cleanup task
        self._start_cleanup_task()

    def _start_cleanup_task(self):
        """Start background cleanup task"""
        async def cleanup_loop():
            while True:
                await asyncio.sleep(self.cleanup_interval_seconds)
                await self.cleanup_expired_entries()

        # Start cleanup in background (don't await)
        asyncio.create_task(cleanup_loop())

    async def check_rate_limit(
        self,
        agent_id: str,
        user_id: Optional[str] = None
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Check if a token refresh is allowed for this agent

        Args:
            agent_id: Agent identifier
            user_id: Optional user ID for additional context

        Returns:
            Tuple of (allowed, retry_after_seconds, reason)
        """
        key = f"refresh_limit:{agent_id}"
        if user_id:
            key = f"{key}:{user_id}"

        current_time = time.time()

        # Try Redis first
        if self.redis_client:
            try:
                return await self._check_redis_rate_limit(key, current_time)
            except Exception as e:
                logger.error(f"Redis rate limit check failed: {e}, falling back to local cache")

        # Fallback to local cache
        return self._check_local_rate_limit(key, current_time)

    async def _check_redis_rate_limit(
        self,
        key: str,
        current_time: float
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """Check rate limit using Redis"""

        # Get rate limit data from Redis
        data = await self.redis_client.get(f"rate:{key}")

        if not data:
            # First request - initialize
            await self._init_redis_rate_limit(key, current_time)
            return (True, None, None)

        limit_data = json.loads(data)

        # Check if we're in a backoff period
        backoff_until = limit_data.get("backoff_until", 0)
        if current_time < backoff_until:
            retry_after = int(backoff_until - current_time)
            return (False, retry_after, "exponential_backoff")

        # Check rate limit window
        window_start = limit_data.get("window_start", 0)
        window_end = window_start + self.base_window_seconds

        if current_time > window_end:
            # New window - reset
            await self._init_redis_rate_limit(key, current_time)
            return (True, None, None)

        # Check count within window
        count = limit_data.get("count", 0)
        if count >= self.max_refreshes_per_window:
            # Violation - apply exponential backoff
            violations = limit_data.get("violations", 0) + 1
            backoff_seconds = min(
                self.base_window_seconds * (self.backoff_multiplier ** violations),
                self.max_backoff_seconds
            )

            new_data = {
                **limit_data,
                "violations": violations,
                "backoff_until": current_time + backoff_seconds,
                "last_violation": current_time
            }

            await self.redis_client.setex(
                f"rate:{key}",
                int(backoff_seconds) + 60,  # Extra time for cleanup
                json.dumps(new_data)
            )

            logger.warning(
                f"Rate limit exceeded for {key}: {count} refreshes in window, "
                f"violations={violations}, backoff={backoff_seconds}s"
            )

            return (False, int(backoff_seconds), "rate_limit_exceeded")

        # Allow request and increment counter
        limit_data["count"] = count + 1
        limit_data["last_refresh"] = current_time

        await self.redis_client.setex(
            f"rate:{key}",
            self.base_window_seconds + 60,
            json.dumps(limit_data)
        )

        return (True, None, None)

    async def _init_redis_rate_limit(self, key: str, current_time: float):
        """Initialize rate limit entry in Redis"""
        data = {
            "window_start": current_time,
            "count": 1,
            "violations": 0,
            "last_refresh": current_time
        }

        await self.redis_client.setex(
            f"rate:{key}",
            self.base_window_seconds + 60,
            json.dumps(data)
        )

    def _check_local_rate_limit(
        self,
        key: str,
        current_time: float
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """Check rate limit using local cache (fallback)"""

        # Cleanup old entries periodically
        if current_time - self.last_cleanup > self.cleanup_interval_seconds:
            self._cleanup_local_cache(current_time)
            self.last_cleanup = current_time

        if key not in self.local_cache:
            # First request
            self.local_cache[key] = {
                "window_start": current_time,
                "count": 1,
                "violations": 0,
                "last_refresh": current_time
            }
            return (True, None, None)

        limit_data = self.local_cache[key]

        # Check backoff
        backoff_until = limit_data.get("backoff_until", 0)
        if current_time < backoff_until:
            retry_after = int(backoff_until - current_time)
            return (False, retry_after, "exponential_backoff")

        # Check window
        window_start = limit_data["window_start"]
        window_end = window_start + self.base_window_seconds

        if current_time > window_end:
            # New window
            self.local_cache[key] = {
                "window_start": current_time,
                "count": 1,
                "violations": 0,
                "last_refresh": current_time
            }
            return (True, None, None)

        # Check count
        if limit_data["count"] >= self.max_refreshes_per_window:
            # Apply backoff
            violations = limit_data.get("violations", 0) + 1
            backoff_seconds = min(
                self.base_window_seconds * (self.backoff_multiplier ** violations),
                self.max_backoff_seconds
            )

            limit_data["violations"] = violations
            limit_data["backoff_until"] = current_time + backoff_seconds

            return (False, int(backoff_seconds), "rate_limit_exceeded")

        # Allow request
        limit_data["count"] += 1
        limit_data["last_refresh"] = current_time

        return (True, None, None)

    def _cleanup_local_cache(self, current_time: float):
        """Remove expired entries from local cache"""
        expired = []
        for key, data in self.local_cache.items():
            # Remove entries older than max_backoff + buffer
            if current_time - data.get("last_refresh", 0) > self.max_backoff_seconds + 300:
                expired.append(key)

        for key in expired:
            del self.local_cache[key]

        if expired:
            logger.info(f"Cleaned up {len(expired)} expired rate limit entries")

    async def cleanup_expired_entries(self):
        """Clean up expired entries from Redis"""
        if not self.redis_client:
            return

        try:
            # Redis TTL handles expiration automatically
            # This is just for monitoring/logging
            pattern = "rate:refresh_limit:*"
            cursor = 0
            expired_count = 0

            while True:
                cursor, keys = await self.redis_client.scan(
                    cursor,
                    match=pattern,
                    count=100
                )

                for key in keys:
                    ttl = await self.redis_client.ttl(key)
                    if ttl and ttl < 0:  # Key expired but not yet removed
                        await self.redis_client.delete(key)
                        expired_count += 1

                if cursor == 0:
                    break

            if expired_count > 0:
                logger.info(f"Cleaned up {expired_count} expired Redis rate limit entries")

        except Exception as e:
            logger.error(f"Error during Redis cleanup: {e}")

    async def get_stats(self, agent_id: Optional[str] = None) -> Dict:
        """
        Get rate limiter statistics

        Args:
            agent_id: Optional agent ID to get stats for specific agent

        Returns:
            Dictionary with rate limit statistics
        """
        stats = {
            "timestamp": datetime.utcnow().isoformat(),
            "base_window_seconds": self.base_window_seconds,
            "max_refreshes_per_window": self.max_refreshes_per_window,
            "total_agents_tracked": len(self.local_cache)
        }

        if agent_id:
            key = f"refresh_limit:{agent_id}"

            # Try Redis first
            if self.redis_client:
                try:
                    data = await self.redis_client.get(f"rate:{key}")
                    if data:
                        limit_data = json.loads(data)
                        stats["agent_stats"] = {
                            "agent_id": agent_id,
                            "current_count": limit_data.get("count", 0),
                            "violations": limit_data.get("violations", 0),
                            "backoff_until": limit_data.get("backoff_until"),
                            "last_refresh": limit_data.get("last_refresh")
                        }
                except Exception as e:
                    logger.error(f"Error getting Redis stats: {e}")

            # Fallback to local cache
            if key in self.local_cache and "agent_stats" not in stats:
                limit_data = self.local_cache[key]
                stats["agent_stats"] = {
                    "agent_id": agent_id,
                    "current_count": limit_data.get("count", 0),
                    "violations": limit_data.get("violations", 0),
                    "backoff_until": limit_data.get("backoff_until"),
                    "last_refresh": limit_data.get("last_refresh")
                }

        return stats


# Global rate limiter instance
token_refresh_rate_limiter = None


async def get_rate_limiter() -> TokenRefreshRateLimiter:
    """Get or create the global rate limiter instance"""
    global token_refresh_rate_limiter

    if token_refresh_rate_limiter is None:
        # Try to connect to Redis with authentication
        try:
            import os
            # MUST use REDIS_URL from environment - it contains the correct password
            redis_url = os.getenv("REDIS_URL", "redis://redis_new:6379/0")
            # If REDIS_URL doesn't have auth, build it from components
            if "@" not in redis_url and os.getenv("REDIS_PASSWORD"):
                redis_password = os.getenv("REDIS_PASSWORD")
                redis_host = os.getenv("REDIS_HOST", "redis_new")
                redis_port = int(os.getenv("REDIS_PORT", "6379"))
                redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/0"

            redis_client = redis.from_url(
                redis_url,
                decode_responses=False
            )
            await redis_client.ping()  # Test connection
            logger.info("Token refresh rate limiter using Redis backend")
        except Exception as e:
            logger.warning(f"Redis not available for rate limiter: {e}, using local cache")
            redis_client = None

        token_refresh_rate_limiter = TokenRefreshRateLimiter(
            redis_client=redis_client,
            base_window_seconds=60,
            max_refreshes_per_window=2,  # Allow 2 refreshes per minute
            backoff_multiplier=2.0,
            max_backoff_seconds=3600  # Max 1 hour backoff
        )

    return token_refresh_rate_limiter
