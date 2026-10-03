"""
Rate Limit Response Helper
Provides rich headers and consistent 429 responses
"""
from fastapi import Response
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
import time

def rate_limited_response(
    scope: str,
    key: str,
    limit: str,
    retry_after: int,
    jsonrpc_id: Optional[Any] = None,
    remaining: int = 0
) -> JSONResponse:
    """
    Create a rich 429 response with informative headers

    Args:
        scope: Rate limit scope (initialize|session|oauth)
        key: The rate limit key that was exceeded
        limit: The limit that was hit (e.g., "120/min")
        retry_after: Seconds until retry is allowed
        jsonrpc_id: JSON-RPC request ID if applicable
        remaining: Requests remaining (usually 0 when limited)
    """
    reset_epoch = int(time.time()) + retry_after

    headers = {
        "Retry-After": str(retry_after),
        "X-Rate-Limit-Scope": scope,
        "X-Rate-Limit-Key": key,
        "X-Rate-Limit-Limit": limit,
        "X-Rate-Limit-Remaining": str(remaining),
        "X-Rate-Limit-Reset": str(reset_epoch),
    }

    # Different response format for JSON-RPC vs REST
    if jsonrpc_id is not None:
        # JSON-RPC error response
        body = {
            "jsonrpc": "2.0",
            "error": {
                "code": -32000,
                "message": "Rate limit exceeded",
                "data": {
                    "scope": scope,
                    "retry_after": retry_after,
                    "limit": limit,
                    "reset": reset_epoch
                }
            },
            "id": jsonrpc_id
        }
    else:
        # REST API error response
        body = {
            "error": "rate_limit_exceeded",
            "message": f"Rate limit exceeded for {scope}",
            "scope": scope,
            "retry_after": retry_after,
            "limit": limit,
            "reset": reset_epoch
        }

    # Record the 429 event in Redis for observability
    try:
        from app.core.redis_client import redis_client
        import asyncio

        # Fire and forget - don't block on this
        async def record_429():
            await redis_client.setex(
                f"obs:last_429:{key}",
                3600,  # Keep for 1 hour
                time.time()
            )
            # Publish event for real-time monitoring
            await redis_client.publish(
                "obs:rate:blocked",
                f"{scope}:{key}:{retry_after}"
            )

        asyncio.create_task(record_429())
    except Exception:
        pass  # Don't fail the response if telemetry fails

    return JSONResponse(
        body,
        status_code=429,
        headers=headers
    )

def get_rate_limit_scope(endpoint: str, method: str = "") -> str:
    """
    Determine rate limit scope based on endpoint and method

    Returns: initialize | session | oauth | browser | observability
    """
    # Observability endpoints get special treatment
    if endpoint.startswith("/obs/"):
        return "observability"

    # Health checks
    if endpoint == "/health":
        return "health"

    # OAuth endpoints
    if endpoint.startswith("/auth/") or endpoint.startswith("/oauth/"):
        return "oauth"

    # MCP endpoints
    if endpoint.startswith("/mcp"):
        if method == "initialize":
            return "initialize"
        else:
            return "session"

    # Browser/webapp endpoints
    if endpoint.startswith("/api/"):
        return "browser"

    return "unknown"

def calculate_rate_limit(scope: str, is_dev: bool = False) -> Dict[str, Any]:
    """
    Get rate limit configuration for a scope

    Returns dict with:
        - requests_per_minute
        - burst_capacity
        - description
    """
    # Development gets more generous limits
    multiplier = 2 if is_dev else 1

    limits = {
        "initialize": {
            "requests_per_minute": 10 * multiplier,
            "burst_capacity": 5,
            "description": f"{10 * multiplier}/min per user"
        },
        "session": {
            "requests_per_minute": 120 * multiplier,
            "burst_capacity": 20,
            "description": f"{120 * multiplier}/min per session"
        },
        "oauth": {
            "requests_per_minute": 20 * multiplier,
            "burst_capacity": 10,
            "description": f"{20 * multiplier}/min per IP"
        },
        "browser": {
            "requests_per_minute": 60 * multiplier,
            "burst_capacity": 15,
            "description": f"{60 * multiplier}/min per user"
        },
        "observability": {
            "requests_per_minute": 120,  # Always generous
            "burst_capacity": 30,
            "description": "120/min per user"
        },
        "health": {
            "requests_per_minute": 600,  # Very high for monitoring
            "burst_capacity": 100,
            "description": "600/min per IP"
        },
        "unknown": {
            "requests_per_minute": 30,
            "burst_capacity": 10,
            "description": "30/min default"
        }
    }

    return limits.get(scope, limits["unknown"])
