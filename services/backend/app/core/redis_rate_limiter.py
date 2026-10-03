"""
Redis-backed sliding-window rate limiter with optional burst control.

- Uses a sorted set per key with timestamps (ms) for an accurate sliding window.
- Returns (allowed, remaining, retry_after_seconds).
- Also exposes simple per-key block helpers for circuit breaker propagation.

Safe to import when Redis is unavailable — callers should wrap in try/except
and fall back to in-memory logic.
"""
from __future__ import annotations

import time
from typing import Tuple, Optional

from .redis_client import redis_client


_LUA_SLIDING_WINDOW = """
-- KEYS[1] = zset key
-- ARGV[1] = limit
-- ARGV[2] = window_ms
-- ARGV[3] = now_ms

local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

-- prune old entries
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count < limit then
  redis.call('ZADD', key, now, now)
  redis.call('PEXPIRE', key, window)
  return {1, limit - count - 1, 0}
else
  -- compute retry_after based on oldest timestamp
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = 0
  if oldest and oldest[2] then
    retry_ms = (tonumber(oldest[2]) + window) - now
    if retry_ms < 0 then retry_ms = 0 end
  end
  return {0, 0, retry_ms}
end
"""


async def check_allow(key: str, limit: int, window_seconds: int, *, burst_limit: Optional[int] = None, burst_window_seconds: int = 5) -> Tuple[bool, Optional[int], int]:
    """Check allowance using Redis sliding window.

    Returns: (allowed, retry_after_seconds or None, remaining)
    """
    now_ms = int(time.time() * 1000)
    window_ms = int(window_seconds * 1000)

    # Burst gate first if configured
    if burst_limit and burst_limit > 0:
        try:
            burst_key = f"rlb:{key}"
            ok, _, retry_ms = await _eval_window(burst_key, burst_limit, burst_window_seconds * 1000, now_ms)
            if not ok:
                return False, int((retry_ms or 0) / 1000), 0
        except Exception:
            # On Redis error, continue to main gate (caller may have fallback)
            pass

    ok, remaining, retry_ms = await _eval_window(f"rl:{key}", limit, window_ms, now_ms)
    if ok:
        return True, None, int(remaining or 0)
    return False, int((retry_ms or 0) / 1000), 0


async def _eval_window(key: str, limit: int, window_ms: int, now_ms: int):
    res = await redis_client.eval(_LUA_SLIDING_WINDOW, keys=[key], args=[limit, window_ms, now_ms])
    # redis-py returns list of (int-compatible)
    return bool(int(res[0])), int(res[1]), int(res[2])


async def get_remaining(key: str, limit: int, window_seconds: int) -> Tuple[int, int]:
    """Return (remaining, reset_epoch_seconds) from Redis.

    Best-effort; if key absent, remaining==limit.
    """
    now_ms = int(time.time() * 1000)
    window_ms = int(window_seconds * 1000)
    zkey = f"rl:{key}"
    try:
        # Prune stale, then count
        await redis_client.zremrangebyscore(zkey, 0, now_ms - window_ms)
        count = await redis_client.zcard(zkey)
        remaining = max(0, int(limit) - int(count or 0))
        oldest = await redis_client.zrange(zkey, 0, 0, withscores=True)
        if oldest and len(oldest) > 0:
            reset_ms = int(oldest[0][1]) + window_ms
        else:
            reset_ms = now_ms + window_ms
        return remaining, int(reset_ms / 1000)
    except Exception:
        # On error, report unknown remaining
        return 0, int((now_ms + window_ms) / 1000)


async def is_key_blocked(key: str) -> bool:
    try:
        ttl = await redis_client.ttl(f"rlblock:{key}")
        return ttl is not None and ttl > 0
    except Exception:
        return False


async def block_key(key: str, seconds: int) -> None:
    try:
        await redis_client.setex(f"rlblock:{key}", seconds, "1")
    except Exception:
        pass
