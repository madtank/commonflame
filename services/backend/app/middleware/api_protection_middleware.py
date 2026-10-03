"""
API Protection Middleware

Prevents API hammering and detects abuse patterns in production.
Provides real-time alerts and automatic blocking of problematic clients.
"""

import time
import json
import logging
from typing import Dict, Optional, Tuple
from collections import defaultdict, deque
from datetime import datetime, timedelta
from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import asyncio
import hashlib

logger = logging.getLogger(__name__)


class APIProtectionMiddleware(BaseHTTPMiddleware):
    """
    Comprehensive API protection against hammering and abuse.

    Features:
    - Detects rapid token refresh patterns (hammering)
    - Identifies stuck retry loops
    - Blocks abusive clients temporarily
    - Sends alerts for production monitoring
    - Tracks per-endpoint metrics
    """

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self.redis = redis_client

        # In-memory tracking (fallback if Redis unavailable)
        self.request_history = defaultdict(lambda: deque(maxlen=100))
        self.blocked_clients = {}  # client_id -> unblock_timestamp
        self.alert_history = deque(maxlen=50)  # Recent alerts

        # Protection thresholds
        self.thresholds = {
            # OAuth token endpoints (most critical)
            "/oauth/token": {
                "requests_per_minute": 10,  # Normal: 4 (15-min tokens)
                "burst_limit": 3,
                "block_duration": 600,  # 10 minutes
                "alert_threshold": 5
            },
            "/oauth/refresh": {
                "requests_per_minute": 10,
                "burst_limit": 3,
                "block_duration": 600,
                "alert_threshold": 5
            },

            # MCP endpoints
            "/mcp/messages": {
                "requests_per_minute": 60,
                "burst_limit": 10,
                "block_duration": 300,
                "alert_threshold": 30
            },

            # Default for other endpoints
            "default": {
                "requests_per_minute": 100,
                "burst_limit": 20,
                "block_duration": 60,
                "alert_threshold": 50
            }
        }

        # Metrics tracking
        self.metrics = defaultdict(lambda: {
            "total_requests": 0,
            "blocked_requests": 0,
            "alerts_sent": 0,
            "unique_clients": set()
        })

    def _get_client_identifier(self, request: Request) -> str:
        """Generate unique client identifier from request

        CRITICAL FIX: Use agent/client identification instead of IP
        This prevents all GitHub-proxied requests from being treated as one client
        """
        # Priority 1: Use X-Agent-Name header (most specific)
        agent_name = request.headers.get("X-Agent-Name")
        if agent_name:
            return f"agent:{agent_name}"

        # Priority 2: Use client_id from query params or form data
        client_id = None
        if hasattr(request, 'query_params'):
            client_id = request.query_params.get('client_id')

        if not client_id and request.method == "POST":
            # Try to get from form data (for OAuth token endpoint)
            try:
                if hasattr(request, '_body') and request._body:
                    body = request._body.decode('utf-8')
                    if 'client_id=' in body:
                        for part in body.split('&'):
                            if part.startswith('client_id='):
                                client_id = part.split('=')[1]
                                break
            except:
                pass

        if client_id:
            return f"client:{client_id}"

        # Priority 3: Use JWT subject if available
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                # Simple JWT decode without verification (just for ID)
                import base64
                token_parts = auth_header[7:].split(".")
                if len(token_parts) >= 2:
                    payload = base64.b64decode(token_parts[1] + "==")
                    payload_data = json.loads(payload)
                    jwt_sub = payload_data.get("sub", "")
                    if jwt_sub:
                        return f"user:{jwt_sub[:16]}"
            except:
                pass

        # Priority 4: Use token hash as identifier
        if auth_header:
            token_hash = hashlib.md5(auth_header.encode()).hexdigest()[:8]
            return f"token:{token_hash}"

        # Last resort: Use IP (but this is problematic for GitHub IPs)
        ip = request.client.host if request.client else "unknown"
        return f"ip:{ip}"

    def _check_for_hammering(self, client_id: str, endpoint: str) -> Tuple[bool, Optional[str]]:
        """
        Check if client is hammering the API.
        Returns (is_hammering, reason)
        """
        now = time.time()
        history = self.request_history[f"{client_id}:{endpoint}"]

        # Get thresholds for this endpoint
        limits = self.thresholds.get(endpoint, self.thresholds["default"])

        # Count recent requests
        one_minute_ago = now - 60
        recent_requests = [t for t in history if t > one_minute_ago]

        # Check for hammering patterns
        if len(recent_requests) > limits["requests_per_minute"]:
            return True, f"Exceeded {limits['requests_per_minute']} requests/minute (got {len(recent_requests)})"

        # Check for burst patterns (multiple requests in 1 second)
        if len(recent_requests) >= 3:
            time_diffs = [recent_requests[i+1] - recent_requests[i]
                         for i in range(len(recent_requests)-1)]
            rapid_requests = sum(1 for diff in time_diffs if diff < 1.0)
            if rapid_requests >= limits["burst_limit"]:
                return True, f"Rapid burst detected: {rapid_requests} requests within 1 second"

        # Check for retry loops (exact same timing pattern)
        if len(history) >= 10:
            recent_10 = list(history)[-10:]
            intervals = [recent_10[i+1] - recent_10[i] for i in range(9)]
            # If intervals are very consistent (±0.1s), it's likely automated
            if all(abs(intervals[i] - intervals[0]) < 0.1 for i in range(1, len(intervals))):
                return True, "Detected automated retry loop pattern"

        return False, None

    async def _send_alert(self, client_id: str, endpoint: str, reason: str):
        """Send alert for production monitoring"""
        alert = {
            "timestamp": datetime.utcnow().isoformat(),
            "client_id": client_id,
            "endpoint": endpoint,
            "reason": reason,
            "severity": "HIGH" if "token" in endpoint else "MEDIUM"
        }

        # Store in history
        self.alert_history.append(alert)

        # Log for monitoring systems
        logger.error(f"🚨 API HAMMERING DETECTED: {json.dumps(alert)}")

        # If Redis available, publish to monitoring channel
        if self.redis:
            try:
                await self.redis.publish("api:alerts:hammering", json.dumps(alert))
            except:
                pass

        # Update metrics
        self.metrics[endpoint]["alerts_sent"] += 1

    async def dispatch(self, request: Request, call_next):
        """Process request with protection checks"""

        # Skip health checks and static files
        if request.url.path in ["/health", "/docs", "/openapi.json"]:
            return await call_next(request)

        client_id = self._get_client_identifier(request)
        endpoint = request.url.path
        now = time.time()

        # Check if client is blocked
        if client_id in self.blocked_clients:
            unblock_time = self.blocked_clients[client_id]
            if now < unblock_time:
                remaining = int(unblock_time - now)
                logger.warning(f"🛑 Blocked client {client_id} attempted request to {endpoint}")
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "too_many_requests",
                        "message": "Client temporarily blocked due to excessive requests",
                        "retry_after": remaining
                    },
                    headers={"Retry-After": str(remaining)}
                )
            else:
                # Unblock expired
                del self.blocked_clients[client_id]

        # Track request
        request_key = f"{client_id}:{endpoint}"
        self.request_history[request_key].append(now)

        # Update metrics
        self.metrics[endpoint]["total_requests"] += 1
        self.metrics[endpoint]["unique_clients"].add(client_id)

        # Check for hammering
        is_hammering, reason = self._check_for_hammering(client_id, endpoint)

        if is_hammering:
            # Get block duration for this endpoint
            limits = self.thresholds.get(endpoint, self.thresholds["default"])
            block_duration = limits["block_duration"]

            # Block the client
            self.blocked_clients[client_id] = now + block_duration
            self.metrics[endpoint]["blocked_requests"] += 1

            # Send alert
            await self._send_alert(client_id, endpoint, reason)

            logger.error(f"🔨 API Hammering: {client_id} on {endpoint} - {reason}")

            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"Rate limit exceeded: {reason}",
                    "retry_after": block_duration,
                    "help": "Your client appears to be hammering the API. Please check your token refresh logic."
                },
                headers={"Retry-After": str(block_duration)}
            )

        # Process request normally
        response = await call_next(request)

        # Add rate limit headers for client awareness
        if endpoint in ["/oauth/token", "/oauth/refresh"]:
            limits = self.thresholds.get(endpoint, self.thresholds["default"])
            recent = len([t for t in self.request_history[request_key] if t > now - 60])
            response.headers["X-RateLimit-Limit"] = str(limits["requests_per_minute"])
            response.headers["X-RateLimit-Remaining"] = str(max(0, limits["requests_per_minute"] - recent))
            response.headers["X-RateLimit-Reset"] = str(int(now + 60))

        return response

    def get_metrics_summary(self) -> dict:
        """Get current protection metrics for monitoring"""
        return {
            "blocked_clients": len(self.blocked_clients),
            "recent_alerts": len(self.alert_history),
            "endpoint_metrics": {
                endpoint: {
                    "total_requests": data["total_requests"],
                    "blocked_requests": data["blocked_requests"],
                    "alerts_sent": data["alerts_sent"],
                    "unique_clients": len(data["unique_clients"])
                }
                for endpoint, data in self.metrics.items()
            },
            "top_hammering_endpoints": sorted(
                [(ep, data["blocked_requests"]) for ep, data in self.metrics.items()],
                key=lambda x: x[1],
                reverse=True
            )[:5]
        }
