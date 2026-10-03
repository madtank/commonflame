"""
Redis client (async) for MCP server components.
Provides a shared `redis_client` using settings.redis_url.
"""
# @ax:tag area=backend component=redis_client tech=redis guide=backend/AGENT.md
import os
from redis import asyncio as redis  # redis-py >= 4.x
from .config import settings

_url = settings.redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.Redis.from_url(_url, decode_responses=True)
