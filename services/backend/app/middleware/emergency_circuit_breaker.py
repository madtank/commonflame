"""
Emergency Circuit Breaker Middleware
Stops request floods at the VERY BEGINNING of the request pipeline
This runs BEFORE authentication, BEFORE rate limiting, BEFORE everything
"""

import time
import logging
from typing import Dict, Set
from collections import defaultdict, deque
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class EmergencyCircuitBreaker(BaseHTTPMiddleware):
    """
    Emergency circuit breaker that runs FIRST in the middleware chain
    Blocks requests at the earliest possible point to prevent DDOS
    """

    def __init__(
        self,
        app,
        requests_per_second: int = 5,
        burst_size: int = 10,
        block_duration: int = 60,
        enable_logging: bool = True
    ):
        """
        Initialize the emergency circuit breaker

        Args:
            app: FastAPI application
            requests_per_second: Max requests per second per client
            burst_size: Max burst size
            block_duration: How long to block a client after violation
            enable_logging: Whether to log blocked requests
        """
        super().__init__(app)
        self.requests_per_second = requests_per_second
        self.burst_size = burst_size
        self.block_duration = block_duration
        self.enable_logging = enable_logging

        # Track request times per client
        self.request_times: Dict[str, deque] = defaultdict(lambda: deque(maxlen=burst_size))

        # Track blocked clients
        self.blocked_until: Dict[str, float] = {}

        # Track violation counts for exponential backoff
        self.violation_counts: Dict[str, int] = defaultdict(int)

        # Track unique patterns to detect attacks
        self.pattern_counts: Dict[str, int] = defaultdict(int)

        logger.info(
            f"🛡️ EMERGENCY CIRCUIT BREAKER ACTIVATED: "
            f"{requests_per_second} req/s, burst={burst_size}, block={block_duration}s"
        )

    def get_client_id(self, request: Request) -> str:
        """Get unique client identifier"""
        # Try to get real IP from headers (for proxies)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = request.client.host if request.client else "unknown"

        # Add user agent to differentiate clients behind same IP
        user_agent = request.headers.get("User-Agent", "unknown")[:50]

        # Add endpoint to track per-endpoint flooding
        endpoint = f"{request.method}:{request.url.path}"

        return f"{client_ip}|{user_agent}|{endpoint}"

    async def dispatch(self, request: Request, call_next):
        """Process request through circuit breaker"""
        current_time = time.time()
        client_id = self.get_client_id(request)

        # Extract just the IP for blocking (block all endpoints for that IP)
        client_ip = client_id.split("|")[0]

        # Check if client is blocked
        if client_ip in self.blocked_until:
            if current_time < self.blocked_until[client_ip]:
                remaining = int(self.blocked_until[client_ip] - current_time)

                if self.enable_logging:
                    logger.warning(
                        f"🚫 CIRCUIT BREAKER: Blocked {client_ip} "
                        f"for {remaining}s more (violations: {self.violation_counts[client_ip]})"
                    )

                return JSONResponse(
                    content={
                        "error": "circuit_breaker_triggered",
                        "message": "Too many requests. Circuit breaker activated.",
                        "retry_after": remaining
                    },
                    status_code=429,
                    headers={
                        "Retry-After": str(remaining),
                        "X-Circuit-Breaker": "triggered",
                        "Cache-Control": "no-store"
                    }
                )
            else:
                # Block expired, remove it
                del self.blocked_until[client_ip]
                # Reset violations after successful block
                self.violation_counts[client_ip] = max(0, self.violation_counts[client_ip] - 1)

        # Track request time
        request_times = self.request_times[client_id]
        request_times.append(current_time)

        # Check if violating rate limit
        if len(request_times) >= self.burst_size:
            # Check time window of requests
            time_window = current_time - request_times[0]

            if time_window < 1.0:  # All burst requests within 1 second
                # VIOLATION: Too many requests too fast
                self.violation_counts[client_ip] += 1

                # Exponential backoff based on violations
                block_duration = min(
                    self.block_duration * (2 ** (self.violation_counts[client_ip] - 1)),
                    600  # Max 10 minutes
                )

                self.blocked_until[client_ip] = current_time + block_duration

                logger.critical(
                    f"⚡ CIRCUIT BREAKER TRIPPED: {client_ip} "
                    f"sent {len(request_times)} requests in {time_window:.2f}s. "
                    f"Blocked for {block_duration}s (violation #{self.violation_counts[client_ip]})"
                )

                # Clear request times to prevent memory growth
                request_times.clear()

                return JSONResponse(
                    content={
                        "error": "circuit_breaker_triggered",
                        "message": f"Rate limit severely exceeded. Blocked for {int(block_duration)} seconds.",
                        "retry_after": int(block_duration)
                    },
                    status_code=429,
                    headers={
                        "Retry-After": str(int(block_duration)),
                        "X-Circuit-Breaker": "triggered",
                        "X-Violation-Count": str(self.violation_counts[client_ip])
                    }
                )

        # Check pattern-based attacks (same endpoint being hammered)
        endpoint_pattern = f"{request.method}:{request.url.path}"
        pattern_key = f"{client_ip}:{endpoint_pattern}:{int(current_time)}"
        self.pattern_counts[pattern_key] += 1

        if self.pattern_counts[pattern_key] > 10:
            # Same endpoint hit more than 10 times in 1 second
            logger.warning(f"🎯 Pattern attack detected: {client_ip} hitting {endpoint_pattern}")
            self.blocked_until[client_ip] = current_time + 60

            return JSONResponse(
                content={
                    "error": "pattern_attack_detected",
                    "message": "Suspicious request pattern detected.",
                    "retry_after": 60
                },
                status_code=429,
                headers={"Retry-After": "60"}
            )

        # Clean old pattern counts periodically
        if len(self.pattern_counts) > 1000:
            cutoff = int(current_time) - 10
            self.pattern_counts = {
                k: v for k, v in self.pattern_counts.items()
                if int(k.split(":")[-1]) > cutoff
            }

        # Request allowed, continue
        try:
            response = await call_next(request)

            # If we're getting 429s from downstream, track it
            if response.status_code == 429:
                self.violation_counts[client_ip] += 0.5  # Half violation for downstream 429

                if self.violation_counts[client_ip] > 3:
                    # Too many downstream 429s, block the client
                    self.blocked_until[client_ip] = current_time + 30
                    logger.warning(f"🔁 Blocking {client_ip} due to repeated downstream 429s")

            return response

        except Exception as e:
            logger.error(f"Error in circuit breaker: {e}")
            # On error, allow request through
            return await call_next(request)

    def get_stats(self) -> Dict:
        """Get circuit breaker statistics"""
        current_time = time.time()
        return {
            "blocked_clients": len(self.blocked_until),
            "active_clients": len(self.request_times),
            "total_violations": sum(self.violation_counts.values()),
            "blocked_ips": list(self.blocked_until.keys()),
            "top_violators": sorted(
                self.violation_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
        }
