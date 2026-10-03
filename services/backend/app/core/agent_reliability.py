"""
Agent Reliability Framework
Provides typed error categories and Redis-backed agent status store
for cloud agent observability and graceful degradation.

Usage:
    from app.core.agent_reliability import (
        AgentError, ErrorCategory, AgentStatusStore
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional, Dict, Any
from uuid import UUID

logger = logging.getLogger(__name__)


# =============================================================================
# ERROR FRAMEWORK
# =============================================================================

class ErrorCategory(str, Enum):
    """
    Categorized error types for cloud agents.
    Agents and UI can use these to provide context-aware responses.
    """
    # Rate limiting / throttling
    RATE_LIMITED = "rate_limited"           # API rate limit hit
    QUOTA_EXCEEDED = "quota_exceeded"       # Daily/monthly quota exhausted

    # Authentication / Authorization
    AUTH_ERROR = "auth_error"               # Token expired, invalid credentials
    PERMISSION_DENIED = "permission_denied" # Lacks required permissions

    # Tool / Resource availability
    TOOL_UNAVAILABLE = "tool_unavailable"   # MCP tool/server down
    RESOURCE_UNAVAILABLE = "resource_unavailable"  # External API down

    # Execution errors
    TIMEOUT = "timeout"                     # Request/execution timeout
    INTERNAL_ERROR = "internal_error"       # Unexpected error
    VALIDATION_ERROR = "validation_error"   # Bad input/payload

    # Agent-specific
    AGENT_PAUSED = "agent_paused"           # Agent explicitly paused
    AGENT_DISABLED = "agent_disabled"       # Agent disabled by admin
    AGENT_CRASHED = "agent_crashed"         # Agent process died unexpectedly


@dataclass
class AgentError:
    """
    Structured error for agent operations.
    Includes recovery info for graceful degradation.
    """
    category: ErrorCategory
    message: str                            # Internal message (for logs)
    user_message: str                       # User-facing message
    recoverable: bool = True                # Can retry?
    retry_after: Optional[datetime] = None  # When to retry (if recoverable)

    # Correlation for observability
    trace_id: Optional[str] = None
    message_id: Optional[str] = None
    agent_id: Optional[str] = None

    # Additional context
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for Redis/JSON storage."""
        data = asdict(self)
        data["category"] = self.category.value
        if self.retry_after:
            data["retry_after"] = self.retry_after.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentError":
        """Deserialize from Redis/JSON."""
        data["category"] = ErrorCategory(data["category"])
        if data.get("retry_after"):
            data["retry_after"] = datetime.fromisoformat(data["retry_after"])
        return cls(**data)

    @property
    def retry_after_seconds(self) -> Optional[int]:
        """Seconds until retry is allowed."""
        if not self.retry_after:
            return None
        delta = self.retry_after - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds()))


# Factory functions for common errors
def rate_limited_error(
    retry_after_seconds: int = 60,
    agent_id: Optional[str] = None,
    scope: str = "api"
) -> AgentError:
    """Create a rate limit error with retry info."""
    return AgentError(
        category=ErrorCategory.RATE_LIMITED,
        message=f"Rate limited on {scope}",
        user_message=f"I'm cooling down. Try again in {retry_after_seconds}s.",
        recoverable=True,
        retry_after=datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds),
        agent_id=agent_id,
        metadata={"scope": scope, "retry_seconds": retry_after_seconds}
    )


def tool_unavailable_error(
    tool_name: str,
    agent_id: Optional[str] = None
) -> AgentError:
    """Create a tool unavailable error."""
    return AgentError(
        category=ErrorCategory.TOOL_UNAVAILABLE,
        message=f"Tool {tool_name} is unavailable",
        user_message=f"I can't reach the {tool_name} tool right now, but I can still help with other things.",
        recoverable=True,
        retry_after=datetime.now(timezone.utc) + timedelta(minutes=5),
        agent_id=agent_id,
        metadata={"tool": tool_name}
    )


def timeout_error(
    operation: str,
    timeout_seconds: int,
    agent_id: Optional[str] = None
) -> AgentError:
    """Create a timeout error."""
    return AgentError(
        category=ErrorCategory.TIMEOUT,
        message=f"Timeout after {timeout_seconds}s on {operation}",
        user_message=f"That took too long. Want me to try again?",
        recoverable=True,
        agent_id=agent_id,
        metadata={"operation": operation, "timeout_seconds": timeout_seconds}
    )


# =============================================================================
# AGENT STATUS STORE
# =============================================================================

class AgentStatus(str, Enum):
    """Agent operational states for UI display."""
    ACTIVE = "active"               # Normal operation
    PROCESSING = "processing"       # Currently handling a request
    RATE_LIMITED = "rate_limited"   # Hit rate limit, cooling down
    DEGRADED = "degraded"           # Partial functionality
    PAUSED = "paused"               # Explicitly paused by user/system
    ERROR = "error"                 # In error state
    OFFLINE = "offline"             # Not responding


@dataclass
class AgentStatusRecord:
    """
    Agent status record stored in Redis.
    UI can poll/subscribe for real-time status updates.
    """
    agent_id: str
    status: AgentStatus
    since: datetime                         # When status changed
    expires_at: Optional[datetime] = None   # Auto-clear after this time
    reason: Optional[str] = None            # Human-readable reason
    error: Optional[AgentError] = None      # Associated error (if any)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "since": self.since.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reason": self.reason,
            "error": self.error.to_dict() if self.error else None,
            "metadata": self.metadata,
        }
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentStatusRecord":
        return cls(
            agent_id=data["agent_id"],
            status=AgentStatus(data["status"]),
            since=datetime.fromisoformat(data["since"]),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            reason=data.get("reason"),
            error=AgentError.from_dict(data["error"]) if data.get("error") else None,
            metadata=data.get("metadata", {}),
        )


class AgentStatusStore:
    """
    Redis-backed agent status store.
    Platform writes status when it detects issues.
    Agents can read their own status on startup/recovery.
    UI polls/subscribes for real-time indicators.

    Redis Key Structure:
        ax:agent-status:{agent_id} -> JSON(AgentStatusRecord)
        ax:agent-status:index:{space_id} -> SET of agent_ids with status

    TTLs:
        - processing: 2 min (auto-clears if agent crashes)
        - rate_limited: retry_after + 1 min buffer
        - degraded: 10 min
        - error: 10 min
        - paused: no TTL (manual clear required)
    """

    # Default TTLs by status type (seconds)
    DEFAULT_TTLS = {
        AgentStatus.PROCESSING: 120,        # 2 min
        AgentStatus.RATE_LIMITED: 300,      # 5 min (overridden by retry_after)
        AgentStatus.DEGRADED: 600,          # 10 min
        AgentStatus.ERROR: 600,             # 10 min
        AgentStatus.PAUSED: None,           # No TTL
        AgentStatus.OFFLINE: 300,           # 5 min
        AgentStatus.ACTIVE: 60,             # 1 min (heartbeat refresh)
    }

    def __init__(self, redis_client):
        self.redis = redis_client

    def _key(self, agent_id: str) -> str:
        return f"ax:agent-status:{agent_id}"

    def _index_key(self, space_id: str) -> str:
        return f"ax:agent-status:index:{space_id}"

    async def set_status(
        self,
        agent_id: str,
        status: AgentStatus,
        space_id: Optional[str] = None,
        reason: Optional[str] = None,
        error: Optional[AgentError] = None,
        ttl_override: Optional[int] = None,
    ) -> AgentStatusRecord:
        """
        Set agent status in Redis.
        Returns the created status record.
        """
        now = datetime.now(timezone.utc)

        # Calculate TTL
        ttl = ttl_override
        if ttl is None:
            if error and error.retry_after:
                # Use retry_after + 1 min buffer
                ttl = error.retry_after_seconds + 60 if error.retry_after_seconds else 300
            else:
                ttl = self.DEFAULT_TTLS.get(status)

        expires_at = now + timedelta(seconds=ttl) if ttl else None

        record = AgentStatusRecord(
            agent_id=agent_id,
            status=status,
            since=now,
            expires_at=expires_at,
            reason=reason,
            error=error,
        )

        key = self._key(agent_id)
        data = json.dumps(record.to_dict())

        if ttl:
            await self.redis.setex(key, ttl, data)
        else:
            await self.redis.set(key, data)

        # Update space index (for listing agents with status in a space)
        if space_id:
            index_key = self._index_key(space_id)
            await self.redis.sadd(index_key, agent_id)
            # Keep index alive as long as any agent has status
            await self.redis.expire(index_key, max(ttl or 3600, 3600))

        # Publish status change event for real-time subscribers
        await self._publish_status_change(record)

        logger.info(
            f"Agent status set: {agent_id} -> {status.value}",
            extra={"agent_id": agent_id, "status": status.value, "ttl": ttl}
        )

        return record

    async def get_status(self, agent_id: str) -> Optional[AgentStatusRecord]:
        """Get current agent status. Returns None if no status (= active)."""
        key = self._key(agent_id)
        data = await self.redis.get(key)
        if not data:
            return None
        return AgentStatusRecord.from_dict(json.loads(data))

    async def clear_status(self, agent_id: str, space_id: Optional[str] = None) -> bool:
        """Clear agent status (returns to active)."""
        key = self._key(agent_id)
        deleted = await self.redis.delete(key)

        if space_id:
            index_key = self._index_key(space_id)
            await self.redis.srem(index_key, agent_id)

        if deleted:
            # Publish cleared event
            await self._publish_status_change(AgentStatusRecord(
                agent_id=agent_id,
                status=AgentStatus.ACTIVE,
                since=datetime.now(timezone.utc),
            ))

        return bool(deleted)

    async def list_space_statuses(self, space_id: str) -> list[AgentStatusRecord]:
        """List all agents with non-active status in a space."""
        index_key = self._index_key(space_id)
        agent_ids = await self.redis.smembers(index_key)

        records = []
        for agent_id in agent_ids:
            record = await self.get_status(agent_id)
            if record:
                records.append(record)
            else:
                # Clean up stale index entry
                await self.redis.srem(index_key, agent_id)

        return records

    async def mark_processing(
        self,
        agent_id: str,
        message_id: str,
        space_id: Optional[str] = None,
    ) -> AgentStatusRecord:
        """Mark agent as processing a message (with 2 min TTL)."""
        return await self.set_status(
            agent_id=agent_id,
            status=AgentStatus.PROCESSING,
            space_id=space_id,
            reason=f"Processing message {message_id[:8]}...",
        )

    async def mark_rate_limited(
        self,
        agent_id: str,
        retry_after_seconds: int,
        space_id: Optional[str] = None,
        scope: str = "api",
    ) -> AgentStatusRecord:
        """Mark agent as rate limited."""
        error = rate_limited_error(retry_after_seconds, agent_id, scope)
        return await self.set_status(
            agent_id=agent_id,
            status=AgentStatus.RATE_LIMITED,
            space_id=space_id,
            reason=error.user_message,
            error=error,
        )

    async def mark_error(
        self,
        agent_id: str,
        error: AgentError,
        space_id: Optional[str] = None,
    ) -> AgentStatusRecord:
        """Mark agent as in error state."""
        return await self.set_status(
            agent_id=agent_id,
            status=AgentStatus.ERROR,
            space_id=space_id,
            reason=error.user_message,
            error=error,
        )

    async def _publish_status_change(self, record: AgentStatusRecord) -> None:
        """Publish status change to Redis pub/sub for real-time updates."""
        try:
            channel = f"ax:agent-status:changes:{record.agent_id}"
            await self.redis.publish(channel, json.dumps(record.to_dict()))
        except Exception as e:
            logger.warning(f"Failed to publish status change: {e}")


# =============================================================================
# AGENT PRESENCE
# =============================================================================


class AgentPresence:
    """
    Redis-backed presence tracking for CLI agents connected via SSE.

    Redis Key: ax:presence:{agent_id} -> JSON with connected, connected_at, last_heartbeat, space_id
    TTL: 30s, refreshed on each SSE heartbeat (every 15s). Auto-expires on disconnect.
    """

    def __init__(self, redis_client):
        self.redis = redis_client

    def _key(self, agent_id: str) -> str:
        return f"ax:presence:{agent_id}"

    async def is_online(self, agent_id: str) -> bool:
        """Check if an agent is currently connected via SSE."""
        return bool(await self.redis.exists(self._key(agent_id)))

    async def get_presence(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get structured presence data for an agent."""
        data = await self.redis.get(self._key(agent_id))
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None

    async def get_bulk_presence(self, agent_ids: list[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """Get presence for multiple agents in one call."""
        if not agent_ids:
            return {}
        pipe = self.redis.pipeline()
        for aid in agent_ids:
            pipe.get(self._key(aid))
        results = await pipe.execute()
        presence = {}
        for aid, raw in zip(agent_ids, results):
            if raw:
                try:
                    presence[aid] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    presence[aid] = None
            else:
                presence[aid] = None
        return presence


# =============================================================================
# OBSERVABILITY HELPERS
# =============================================================================

@dataclass
class CorrelationContext:
    """
    Correlation IDs for tracing a request through the system.
    Pass this through the call chain for observability.
    """
    trace_id: str               # Unique per request chain
    message_id: Optional[str] = None
    agent_id: Optional[str] = None
    space_id: Optional[str] = None

    # Timing breakdowns
    queue_time_ms: Optional[float] = None
    processing_time_ms: Optional[float] = None
    tool_call_time_ms: Optional[float] = None

    def to_log_extra(self) -> Dict[str, Any]:
        """Return as dict for structured logging extra={}."""
        return {
            "trace_id": self.trace_id,
            "message_id": self.message_id,
            "agent_id": self.agent_id,
            "space_id": self.space_id,
            "queue_time_ms": self.queue_time_ms,
            "processing_time_ms": self.processing_time_ms,
            "tool_call_time_ms": self.tool_call_time_ms,
        }
