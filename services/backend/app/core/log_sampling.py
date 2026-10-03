"""
Log sampling utilities for high-frequency events
Reduces log volume and costs while maintaining observability
"""

import logging
import random
import time
import functools
from typing import Callable, Dict, Any, Optional
from collections import defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class LogSampler:
    """Intelligent log sampling based on frequency and importance"""

    def __init__(self):
        self.event_counts = defaultdict(lambda: {"count": 0, "last_logged": 0})
        self.sampling_rates = {}

    def should_log(self, event_type: str, base_rate: float = 0.1,
                   burst_threshold: int = 100, burst_window: int = 60) -> bool:
        """
        Determine if an event should be logged based on sampling rules

        Args:
            event_type: Type of event being logged
            base_rate: Base sampling rate (0.0 to 1.0)
            burst_threshold: Number of events in window before reducing rate
            burst_window: Time window in seconds for burst detection

        Returns:
            True if event should be logged
        """
        now = time.time()
        event_info = self.event_counts[event_type]

        # Reset counter if outside window
        if now - event_info["last_logged"] > burst_window:
            event_info["count"] = 0

        event_info["count"] += 1

        # Always log the first few events
        if event_info["count"] <= 5:
            event_info["last_logged"] = now
            return True

        # Reduce rate during bursts
        if event_info["count"] > burst_threshold:
            # Log only 1% during bursts
            effective_rate = base_rate * 0.1
        else:
            effective_rate = base_rate

        # Sample based on effective rate
        if random.random() < effective_rate:
            event_info["last_logged"] = now
            return True

        return False

    def get_stats(self) -> Dict[str, Any]:
        """Get sampling statistics"""
        return {
            event: {
                "count": info["count"],
                "last_logged": datetime.fromtimestamp(info["last_logged"]).isoformat()
                if info["last_logged"] > 0 else None
            }
            for event, info in self.event_counts.items()
        }


# Global sampler instance
log_sampler = LogSampler()


def sample_logs(event_type: str, rate: float = 0.1):
    """
    Decorator to sample logs for high-frequency functions

    Usage:
        @sample_logs("api_request", rate=0.1)
        async def handle_request():
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            should_log = log_sampler.should_log(event_type, rate)

            if should_log:
                start_time = time.time()

            try:
                result = await func(*args, **kwargs)

                if should_log:
                    duration = (time.time() - start_time) * 1000
                    logger.info(
                        f"{event_type} completed",
                        extra={
                            "event_type": event_type,
                            "duration_ms": duration,
                            "sampled": True
                        }
                    )

                return result

            except Exception as e:
                # Always log errors
                logger.error(
                    f"{event_type} failed: {str(e)}",
                    extra={
                        "event_type": event_type,
                        "error": str(e),
                        "sampled": should_log
                    }
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            should_log = log_sampler.should_log(event_type, rate)

            if should_log:
                start_time = time.time()

            try:
                result = func(*args, **kwargs)

                if should_log:
                    duration = (time.time() - start_time) * 1000
                    logger.info(
                        f"{event_type} completed",
                        extra={
                            "event_type": event_type,
                            "duration_ms": duration,
                            "sampled": True
                        }
                    )

                return result

            except Exception as e:
                # Always log errors
                logger.error(
                    f"{event_type} failed: {str(e)}",
                    extra={
                        "event_type": event_type,
                        "error": str(e),
                        "sampled": should_log
                    }
                )
                raise

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


class RateLimitedLogger:
    """Logger that limits message frequency"""

    def __init__(self, name: str, max_messages: int = 10, window: int = 60):
        """
        Args:
            name: Logger name
            max_messages: Maximum messages allowed in window
            window: Time window in seconds
        """
        self.logger = logging.getLogger(name)
        self.max_messages = max_messages
        self.window = window
        self.message_times = defaultdict(list)

    def _should_log(self, key: str) -> bool:
        """Check if message should be logged based on rate limit"""
        now = time.time()
        times = self.message_times[key]

        # Remove old entries outside window
        self.message_times[key] = [
            t for t in times if now - t < self.window
        ]

        # Check if under limit
        if len(self.message_times[key]) < self.max_messages:
            self.message_times[key].append(now)
            return True

        return False

    def info(self, message: str, *args, **kwargs):
        """Rate-limited info logging"""
        if self._should_log(message):
            self.logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        """Rate-limited warning logging"""
        if self._should_log(message):
            self.logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        """Always log errors (no rate limit)"""
        self.logger.error(message, *args, **kwargs)


class MetricsLogger:
    """Efficient metrics logging with batching"""

    def __init__(self, flush_interval: int = 60):
        self.metrics = defaultdict(list)
        self.flush_interval = flush_interval
        self.last_flush = time.time()

    def record(self, metric_name: str, value: float, tags: Dict[str, str] = None):
        """Record a metric value"""
        self.metrics[metric_name].append({
            "value": value,
            "tags": tags or {},
            "timestamp": time.time()
        })

        # Auto-flush if needed
        if time.time() - self.last_flush > self.flush_interval:
            self.flush()

    def flush(self):
        """Flush metrics to logs"""
        if not self.metrics:
            return

        for metric_name, values in self.metrics.items():
            # Calculate aggregates
            metric_values = [v["value"] for v in values]

            summary = {
                "metric": metric_name,
                "count": len(values),
                "min": min(metric_values),
                "max": max(metric_values),
                "avg": sum(metric_values) / len(metric_values),
                "sum": sum(metric_values),
            }

            logger.info(f"Metrics summary: {metric_name}", extra=summary)

        # Clear metrics
        self.metrics.clear()
        self.last_flush = time.time()


# Global instances
rate_limited_logger = RateLimitedLogger("rate_limited", max_messages=10, window=60)
metrics_logger = MetricsLogger(flush_interval=60)
