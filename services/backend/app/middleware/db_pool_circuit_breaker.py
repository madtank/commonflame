"""
DB Pool Circuit Breaker Middleware

Purpose: Fail fast with 503 when the SQLAlchemy async engine's pool is saturated,
to prevent pile-ups, long tail latencies, and cascading retries under load.

Configuration via env vars:
- DB_POOL_CIRCUIT_ENABLED (default: true in production, false otherwise)
- DB_POOL_CIRCUIT_THRESHOLD (float 0..1, default: 0.85)
- DB_POOL_CIRCUIT_RETRY_AFTER (seconds, default: 10)
- DB_POOL_CIRCUIT_PATHS (CSV of paths to guard; default:
  '/api/v1/messages,/mcp/messages,/mcp_tools/messages')

Only applies to "hot" write endpoints (POST) by default. Health/metrics/auth are excluded.
"""
# @ax:tag area=backend component=db_pool_circuit_breaker tech=fastapi,sqlalchemy guide=backend/AGENT.md
from __future__ import annotations

import os
from typing import Callable, Awaitable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import engine


class DBPoolCircuitBreakerMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, enabled: bool | None = None,
                 threshold: float | None = None,
                 retry_after_seconds: int | None = None,
                 protected_paths: list[str] | None = None):
        super().__init__(app)
        settings = get_settings()

        # Resolve enable flag with sensible defaults
        env_enabled = os.getenv("DB_POOL_CIRCUIT_ENABLED")
        if enabled is None:
            if env_enabled is not None:
                enabled = env_enabled.lower() == "true"
            else:
                enabled = (settings.environment == "production")

        self.enabled = enabled
        self.threshold = threshold if threshold is not None else float(os.getenv("DB_POOL_CIRCUIT_THRESHOLD", "0.85"))
        self.retry_after = retry_after_seconds if retry_after_seconds is not None else int(os.getenv("DB_POOL_CIRCUIT_RETRY_AFTER", "10"))

        default_paths = \
            os.getenv("DB_POOL_CIRCUIT_PATHS", "/api/v1/messages,/mcp/messages,/mcp_tools/messages")
        self.protected_paths = protected_paths if protected_paths is not None else [p.strip() for p in default_paths.split(",") if p.strip()]

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled:
            return await call_next(request)

        # Only guard hot write endpoints to minimize false trips
        if request.method != "POST":
            return await call_next(request)

        path = request.url.path
        if not any(path.startswith(p) for p in self.protected_paths):
            return await call_next(request)

        try:
            pool = engine.pool
            size = getattr(pool, "size", lambda: None)() or 0
            checked_out = getattr(pool, "checkedout", lambda: None)() or 0
            overflow = getattr(pool, "overflow", lambda: None)() or 0
            total_capacity = size + max(overflow, 0)

            utilization = (checked_out / size) if size > 0 else 0.0
            # If size==0 (e.g., SQLite/null pool), don't trip
            if size > 0 and utilization >= self.threshold:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "type": "db_pool_circuit_breaker",
                        "detail": "Database connection pool is saturated. Please retry shortly.",
                        "pool": {
                            "size": size,
                            "checked_out": checked_out,
                            "overflow": overflow,
                            "utilization": round(utilization, 3),
                            "threshold": self.threshold,
                        }
                    },
                    headers={"Retry-After": str(self.retry_after)}
                )
        except Exception:
            # Fail-open on metrics collection issues
            pass

        return await call_next(request)


def should_enable_in_env() -> bool:
    settings = get_settings()
    env = settings.environment
    if env == "production":
        return True
    return os.getenv("DB_POOL_CIRCUIT_ENABLED", "false").lower() == "true"
