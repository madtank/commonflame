"""
Async Logging Infrastructure for aX Platform
High-performance, non-blocking logging with cost safeguards and intelligent sampling
"""

import asyncio
import json
import logging
import time
import random
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
import os

# Optional psutil for system monitoring (graceful fallback)
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class LogLevel(Enum):
    """Log level priorities for sampling decisions"""
    CRITICAL = 100  # Always log - security violations, errors
    HIGH = 75      # Usually log - first-time events, warnings
    MEDIUM = 50    # Sample - routine operations
    LOW = 25       # Sample heavily - debug info


class LogEventType(Enum):
    """Standardized log event types for MCP operations"""
    # Security Events (always logged)
    SECURITY_VIOLATION = "security_violation"
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"

    # MCP Tool Events
    MCP_REQUEST_START = "mcp_request_start"
    MCP_REQUEST_COMPLETE = "mcp_request_complete"
    MCP_TOOL_INVOKE = "mcp_tool_invoke"
    MCP_TOOL_COMPLETE = "mcp_tool_complete"

    # Business Events
    TASK_CREATED = "task_created"
    TASK_ASSIGNED = "task_assigned"
    MESSAGE_SENT = "message_sent"
    SEARCH_PERFORMED = "search_performed"

    # System Events
    PERFORMANCE_ALERT = "performance_alert"
    COST_ALERT = "cost_alert"
    ERROR = "error"


@dataclass
class LogEntry:
    """Structured log entry with comprehensive context"""
    timestamp: float
    event_type: LogEventType
    level: LogLevel
    agent_name: str
    user_id: str
    space_id: str

    # Request context
    request_id: Optional[str] = None
    endpoint: Optional[str] = None

    # Performance data
    duration_ms: Optional[float] = None
    memory_mb: Optional[float] = None

    # Business context
    tool_name: Optional[str] = None
    task_id: Optional[str] = None
    message_id: Optional[str] = None

    # Additional metadata
    metadata: Optional[Dict[str, Any]] = None

    def to_json(self) -> str:
        """Convert to JSON for structured logging"""
        return json.dumps({
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "level": self.level.value,
            "agent_name": self.agent_name,
            "user_id": self.user_id,
            "space_id": self.space_id,
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "duration_ms": self.duration_ms,
            "memory_mb": self.memory_mb,
            "tool_name": self.tool_name,
            "task_id": self.task_id,
            "message_id": self.message_id,
            "metadata": self.metadata or {}
        })


class LogSampler:
    """Intelligent sampling to manage log volume and costs"""

    def __init__(self):
        self.daily_log_count = 0
        self.daily_cost_estimate = 0.0
        self.last_reset = datetime.now().date()

        # Cost and volume limits
        self.max_daily_cost = float(os.getenv("MAX_DAILY_LOG_COST", "10.0"))  # $10/day default
        self.max_daily_entries = int(os.getenv("MAX_DAILY_LOG_ENTRIES", "100000"))  # 100k/day default

    def should_log(self, entry: LogEntry) -> bool:
        """Intelligent sampling decision based on multiple factors"""
        # Reset daily counters
        today = datetime.now().date()
        if today > self.last_reset:
            self.daily_log_count = 0
            self.daily_cost_estimate = 0.0
            self.last_reset = today

        # Always log critical events
        if entry.level == LogLevel.CRITICAL:
            return True

        # Check daily limits
        if self.daily_log_count >= self.max_daily_entries:
            return entry.level == LogLevel.CRITICAL

        if self.daily_cost_estimate >= self.max_daily_cost:
            return entry.level == LogLevel.CRITICAL

        # Adaptive sampling based on system load (if psutil available)
        if PSUTIL_AVAILABLE:
            try:
                system_load = psutil.cpu_percent(interval=0.1)
                memory_percent = psutil.virtual_memory().percent

                # Reduce sampling under high load
                if system_load > 80 or memory_percent > 85:
                    sample_rate = 0.1  # 10% sampling under high load
                elif system_load > 60 or memory_percent > 70:
                    sample_rate = 0.3  # 30% sampling under medium load
                else:
                    sample_rate = 0.7  # 70% sampling normal load
            except Exception:
                # Fallback to moderate sampling if psutil fails
                sample_rate = 0.5
        else:
            # Default sampling without system monitoring
            sample_rate = 0.5  # 50% sampling when no system monitoring

        # Adjust sample rate by log level
        level_multiplier = {
            LogLevel.HIGH: 1.5,
            LogLevel.MEDIUM: 1.0,
            LogLevel.LOW: 0.5
        }.get(entry.level, 1.0)

        final_sample_rate = min(1.0, sample_rate * level_multiplier)

        return random.random() < final_sample_rate

    def record_log(self, entry: LogEntry):
        """Record that a log was written (for cost tracking)"""
        self.daily_log_count += 1
        # Estimate cost: ~$0.50 per GB, ~20KB average per entry
        entry_cost = (20 * 1024) / (1024**3) * 0.50  # ~$0.00001 per entry
        self.daily_cost_estimate += entry_cost


class AsyncMCPLogger:
    """High-performance async logging for MCP operations"""

    def __init__(self, max_queue_size: int = 10000):
        self.log_queue = asyncio.Queue(maxsize=max_queue_size)
        self.sampler = LogSampler()
        self.background_task = None
        self.logger = logging.getLogger("mcp_async_logger")
        self.is_running = False

        # Performance monitoring
        self.total_logs_queued = 0
        self.total_logs_dropped = 0
        self.total_logs_written = 0

    async def start(self):
        """Start the background logging processor"""
        if self.background_task is None:
            self.is_running = True
            self.background_task = asyncio.create_task(self._process_log_queue())
            self.logger.info("🚀 Async MCP Logger started")

    async def stop(self):
        """Gracefully stop the background processor"""
        self.is_running = False
        if self.background_task:
            await self.background_task
            self.logger.info("🛑 Async MCP Logger stopped")

    async def log_event(self,
                       event_type: LogEventType,
                       level: LogLevel,
                       agent_name: str,
                       user_id: str,
                       space_id: str,
                       **kwargs) -> bool:
        """
        Queue a log event for async processing
        Returns True if queued, False if dropped
        """
        entry = LogEntry(
            timestamp=time.time(),
            event_type=event_type,
            level=level,
            agent_name=agent_name,
            user_id=user_id,
            space_id=space_id,
            **kwargs
        )

        # Intelligent sampling decision
        if not self.sampler.should_log(entry):
            return False

        try:
            # Non-blocking queue operation
            self.log_queue.put_nowait(entry)
            self.total_logs_queued += 1
            return True
        except asyncio.QueueFull:
            # Graceful degradation - drop oldest entries to make room
            try:
                dropped_entry = self.log_queue.get_nowait()
                self.total_logs_dropped += 1
                self.log_queue.put_nowait(entry)
                self.total_logs_queued += 1

                # Log queue overflow as critical event
                if random.random() < 0.01:  # 1% sampling to avoid spam
                    self.logger.warning(f"🚨 Log queue overflow - dropped entry: {dropped_entry.event_type.value}")

                return True
            except asyncio.QueueEmpty:
                # Queue was somehow empty, just add the entry
                self.log_queue.put_nowait(entry)
                self.total_logs_queued += 1
                return True

    async def _process_log_queue(self):
        """Background task to process queued log entries"""
        batch_size = 100
        batch_timeout = 1.0  # Process batch every 1 second

        while self.is_running or not self.log_queue.empty():
            batch = []
            batch_start = time.time()

            # Collect batch of log entries
            while (len(batch) < batch_size and
                   (time.time() - batch_start) < batch_timeout and
                   not self.log_queue.empty()):
                try:
                    entry = await asyncio.wait_for(self.log_queue.get(), timeout=0.1)
                    batch.append(entry)
                except asyncio.TimeoutError:
                    break

            # Process batch if we have entries
            if batch:
                await self._write_log_batch(batch)

            # Small delay to prevent CPU spinning
            await asyncio.sleep(0.01)

    async def _write_log_batch(self, batch: List[LogEntry]):
        """Write batch of log entries to storage"""
        try:
            # Write to structured logger (existing infrastructure)
            for entry in batch:
                # Write to Python logger with structured JSON
                log_data = json.loads(entry.to_json())
                self.logger.info(entry.to_json())

                # Send to intelligence pipeline for analysis
                try:
                    from app.core.intelligence_pipeline import process_log_for_intelligence
                    await process_log_for_intelligence(log_data)
                except Exception as intel_error:
                    # Don't fail logging if intelligence processing fails
                    self.logger.debug(f"Intelligence processing error: {intel_error}")

                # Update sampling statistics
                self.sampler.record_log(entry)
                self.total_logs_written += 1

        except Exception as e:
            self.logger.error(f"❌ Error writing log batch: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get logging performance statistics"""
        return {
            "queue_size": self.log_queue.qsize(),
            "total_queued": self.total_logs_queued,
            "total_dropped": self.total_logs_dropped,
            "total_written": self.total_logs_written,
            "daily_count": self.sampler.daily_log_count,
            "daily_cost_estimate": round(self.sampler.daily_cost_estimate, 4),
            "cost_limit": self.sampler.max_daily_cost,
            "is_running": self.is_running
        }


# Global async logger instance
async_mcp_logger = AsyncMCPLogger()


# Convenience functions for common log events
async def log_mcp_request_start(agent_name: str, user_id: str, space_id: str,
                               endpoint: str, tool_name: str = None,
                               request_id: str = None) -> bool:
    """Log MCP request start"""
    return await async_mcp_logger.log_event(
        event_type=LogEventType.MCP_REQUEST_START,
        level=LogLevel.MEDIUM,
        agent_name=agent_name,
        user_id=user_id,
        space_id=space_id,
        endpoint=endpoint,
        tool_name=tool_name,
        request_id=request_id
    )


async def log_mcp_request_complete(agent_name: str, user_id: str, space_id: str,
                                  endpoint: str, duration_ms: float,
                                  tool_name: str = None, request_id: str = None,
                                  success: bool = True) -> bool:
    """Log MCP request completion"""
    level = LogLevel.MEDIUM if success else LogLevel.HIGH
    return await async_mcp_logger.log_event(
        event_type=LogEventType.MCP_REQUEST_COMPLETE,
        level=level,
        agent_name=agent_name,
        user_id=user_id,
        space_id=space_id,
        endpoint=endpoint,
        tool_name=tool_name,
        duration_ms=duration_ms,
        request_id=request_id,
        metadata={"success": success}
    )


async def log_security_violation(agent_name: str, user_id: str, space_id: str,
                                violation_type: str, description: str,
                                endpoint: str = None) -> bool:
    """Log security violation (always logged)"""
    return await async_mcp_logger.log_event(
        event_type=LogEventType.SECURITY_VIOLATION,
        level=LogLevel.CRITICAL,
        agent_name=agent_name,
        user_id=user_id,
        space_id=space_id,
        endpoint=endpoint,
        metadata={
            "violation_type": violation_type,
            "description": description
        }
    )


async def log_business_event(event_type: LogEventType, agent_name: str,
                           user_id: str, space_id: str, **kwargs) -> bool:
    """Log business events (tasks, messages, searches)"""
    level = LogLevel.HIGH if event_type in [
        LogEventType.TASK_CREATED,
        LogEventType.MESSAGE_SENT
    ] else LogLevel.MEDIUM

    return await async_mcp_logger.log_event(
        event_type=event_type,
        level=level,
        agent_name=agent_name,
        user_id=user_id,
        space_id=space_id,
        **kwargs
    )


async def get_recent_security_violations(limit: int = 100):
    """Get recent security violations from the log buffer

    Returns a list of recent security violation events for admin monitoring.
    This is used by the admin security endpoints to track violations.
    """
    violations = []
    # Access the event buffer if available
    if hasattr(async_mcp_logger, 'event_buffer'):
        for event in async_mcp_logger.event_buffer:
            if event.get("event_type") == LogEventType.SECURITY_VIOLATION:
                violations.append(event)
                if len(violations) >= limit:
                    break
    return violations
