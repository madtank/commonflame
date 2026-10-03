"""
Production-ready logging configuration for aX Platform
Optimized for GCP Cloud Logging with cost and performance considerations
"""

import os
import logging
import json
import random
from typing import Dict, Any
from datetime import datetime

# Environment detection
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG" if ENVIRONMENT == "development" else "INFO")
LOG_SAMPLING_RATE = float(os.getenv("LOG_SAMPLING_RATE", "1.0" if ENVIRONMENT == "development" else "0.1"))
ENABLE_ACCESS_LOGS = os.getenv("ENABLE_ACCESS_LOGS", "true").lower() == "true"

# Noisy loggers to suppress in production
SUPPRESS_LOGGERS = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "uvicorn.access": logging.WARNING if not ENABLE_ACCESS_LOGS else logging.INFO,
    "uvicorn.error": logging.INFO,
    "asyncio": logging.WARNING,
    "watchfiles": logging.WARNING,
    "multipart": logging.WARNING,
}

class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging in GCP"""

    def format(self, record):
        """Format log record as JSON for GCP Cloud Logging"""
        log_obj = {
            "timestamp": datetime.utcnow().isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "environment": ENVIRONMENT,
        }

        # Add correlation_id from context (OBS-001)
        try:
            from app.middleware.correlation import get_correlation_id
            cid = get_correlation_id()
            if cid:
                log_obj["correlation_id"] = cid
        except Exception:
            pass

        # Add extra fields if present
        if hasattr(record, "correlation_id"):
            log_obj["correlation_id"] = record.correlation_id
        if hasattr(record, "user_id"):
            log_obj["user_id"] = record.user_id
        if hasattr(record, "space_id"):
            log_obj["space_id"] = record.space_id
        elif hasattr(record, "org_id"):
            log_obj["space_id"] = record.org_id
        if hasattr(record, "request_id"):
            log_obj["request_id"] = record.request_id
        if hasattr(record, "method"):
            log_obj["method"] = record.method
        if hasattr(record, "path"):
            log_obj["path"] = record.path
        if hasattr(record, "status_code"):
            log_obj["status_code"] = record.status_code
        if hasattr(record, "response_time_ms"):
            log_obj["response_time_ms"] = record.response_time_ms

        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


class SamplingFilter(logging.Filter):
    """Filter to sample logs based on configured rate"""

    def __init__(self, sample_rate: float = 1.0):
        super().__init__()
        self.sample_rate = sample_rate

    def filter(self, record):
        """Sample logs based on rate (1.0 = all, 0.1 = 10%)"""
        # Always log warnings and errors
        if record.levelno >= logging.WARNING:
            return True

        # Sample info and debug logs
        return random.random() < self.sample_rate


class HealthCheckFilter(logging.Filter):
    """Filter out health check and metrics endpoint logs"""

    EXCLUDED_PATHS = {
        "/health",
        "/metrics",
        "/",  # Root path health check
        "/favicon.ico",
        "/_ah/warmup",  # GCP warmup requests
    }

    def filter(self, record):
        """Exclude health check logs to reduce noise"""
        # Check if this is an access log with a path
        if hasattr(record, "path"):
            if record.path in self.EXCLUDED_PATHS:
                return False

        # Check message content for health checks
        msg = record.getMessage()
        for path in self.EXCLUDED_PATHS:
            if path in msg and ("GET" in msg or "POST" in msg):
                return False

        return True


def configure_logging():
    """Configure logging for production use"""

    # Set root logger level
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL))

    # Remove default handlers
    root_logger.handlers.clear()

    # Create structured handler for GCP
    if ENVIRONMENT == "production":
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
    else:
        # Human-readable format for development
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
        )

    # Add filters
    handler.addFilter(SamplingFilter(LOG_SAMPLING_RATE))
    handler.addFilter(HealthCheckFilter())

    root_logger.addHandler(handler)

    # Configure third-party logger levels
    for logger_name, level in SUPPRESS_LOGGERS.items():
        logging.getLogger(logger_name).setLevel(level)

    # Log startup configuration
    if ENVIRONMENT == "production":
        root_logger.info(
            f"Logging configured for production: level={LOG_LEVEL}, "
            f"sampling={LOG_SAMPLING_RATE}, access_logs={ENABLE_ACCESS_LOGS}"
        )


class LogContext:
    """Context manager for adding fields to all logs in a scope"""

    def __init__(self, **kwargs):
        self.fields = kwargs
        self.old_factory = None

    def __enter__(self):
        def factory(*args, **kwargs):
            record = logging.LogRecord(*args, **kwargs)
            for key, value in self.fields.items():
                setattr(record, key, value)
            return record

        self.old_factory = logging.getLogRecordFactory()
        logging.setLogRecordFactory(factory)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.old_factory:
            logging.setLogRecordFactory(self.old_factory)


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance"""
    logger = logging.getLogger(name)

    # Ensure logger uses root configuration
    logger.setLevel(getattr(logging, LOG_LEVEL))

    return logger


# Auto-configure on import if in production
if ENVIRONMENT == "production":
    configure_logging()
