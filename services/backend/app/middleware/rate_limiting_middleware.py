"""
Rate Limiting and DDoS Protection Middleware
Comprehensive protection against abuse and resource exhaustion attacks
"""

import hashlib
import json
import logging
import os
import random
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Known dev/test accounts seeded via backend/scripts/seed_test_users.py and
# exposed in the frontend login shortcuts. Rate limiting these during local
# development makes it painful to switch personas while testing, so we exempt
# them when ENVIRONMENT=development/dev/local.
DEV_TEST_USER_EMAILS = {
    "admin@paxai.app",
    "test@example.com",
    "alice@example.com",
    "bob@example.com",
    "charlie@example.com",
    "admin2@paxai.app",
    "isolated@example.com",
    "security@example.com",
    "newuser_test@example.com",
}

# Import GitHub IP whitelist utilities
try:
    from app.middleware.github_ip_whitelist import (
        get_client_ip as get_real_client_ip,
        is_github_ip,
        should_apply_gentle_limits,
        should_skip_rate_limit,
    )
except ImportError:
    # Fallback if whitelist module not available
    def is_github_ip(ip):
        return False

    def get_real_client_ip(request):
        return None

    def should_skip_rate_limit(request):
        return False

    def should_apply_gentle_limits(request):
        return False


class RateLimitExceeded(HTTPException):
    """Custom exception for rate limit violations"""

    def __init__(self, retry_after: int | None = None):
        self.retry_after = retry_after
        super().__init__(status_code=429, detail="Rate limit exceeded")


def _get_cors_headers() -> dict[str, str]:
    """Get CORS headers for rate limit responses.

    CRITICAL: When middleware returns responses directly (like 429), it bypasses
    FastAPI's CORS middleware. We must manually add CORS headers to prevent
    browser CORS errors that cause 'net::ERR_FAILED' issues.
    """
    environment = os.getenv("ENVIRONMENT", "development").lower()

    if environment == "production":
        # Production: Allow only paxai.app domains
        return {
            "Access-Control-Allow-Origin": "https://paxai.app",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Agent-Name, X-Device-Id, Mcp-Session-Id",
            "Access-Control-Expose-Headers": "Retry-After, X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset, X-RateLimit-Key, X-RateLimit-Tier",
        }
    else:
        # Development: Allow localhost origins
        return {
            "Access-Control-Allow-Origin": "http://localhost:3000",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Agent-Name, X-Device-Id, Mcp-Session-Id",
            "Access-Control-Expose-Headers": "Retry-After, X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset, X-RateLimit-Key, X-RateLimit-Tier",
        }


class RateLimitStore:
    """In-memory store for rate limiting (Redis recommended for production)"""

    def __init__(self):
        self.requests: dict[str, deque] = defaultdict(deque)
        self.blocked_ips: dict[str, float] = {}  # IP -> unblock_time
        self.blocked_keys: dict[str, float] = {}  # rate-limit key -> unblock_time (per agent/session)
        self.blocked_key_reasons: dict[str, str] = {}  # reason for key block (e.g., mcp_misconfiguration)
        self.suspicious_patterns: dict[str, int] = defaultdict(int)
        self.agent_violations: dict[str, deque] = defaultdict(deque)  # Track agent-related security violations
        # Rolling error windows per key (for circuit breaker)
        self.errors_by_key: dict[str, deque] = defaultdict(deque)

    def cleanup_old_requests(self, key: str, window_seconds: int):
        """Remove requests older than the time window"""
        current_time = time.time()
        cutoff_time = current_time - window_seconds

        while self.requests[key] and self.requests[key][0] < cutoff_time:
            self.requests[key].popleft()

    def add_request(self, key: str) -> int:
        """Add a request and return current count"""
        current_time = time.time()
        self.requests[key].append(current_time)
        return len(self.requests[key])

    def is_blocked(self, ip: str) -> bool:
        """Check if IP is temporarily blocked"""
        if ip in self.blocked_ips:
            if time.time() < self.blocked_ips[ip]:
                return True
            else:
                # Unblock expired IPs
                del self.blocked_ips[ip]
        return False

    def block_ip(self, ip: str, duration_seconds: int):
        """Temporarily block an IP"""
        self.blocked_ips[ip] = time.time() + duration_seconds
        logger.warning(f"Blocked IP {ip} for {duration_seconds} seconds due to rate limit violations")

    def is_key_blocked(self, key: str) -> bool:
        """Check if a specific rate-limit key is blocked"""
        until = self.blocked_keys.get(key)
        if until and time.time() < until:
            return True
        if until:
            # expired; cleanup
            self.blocked_keys.pop(key, None)
        return False

    def block_key(self, key: str, duration_seconds: int):
        """Temporarily block a specific key (agent/session) without affecting the whole user/IP"""
        self.blocked_keys[key] = time.time() + duration_seconds
        logger.warning(f"Blocked key {key} for {duration_seconds}s due to repeated violations")

    def add_error(self, key: str, window_seconds: int = 10) -> int:
        """Record an error for the key and return current error count in the window"""
        now = time.time()
        q = self.errors_by_key[key]
        q.append(now)
        cutoff = now - window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        return len(q)

    def record_suspicious_pattern(self, pattern: str):
        """Record suspicious request patterns"""
        self.suspicious_patterns[pattern] += 1
        if self.suspicious_patterns[pattern] > 100:  # Alert threshold
            logger.error(f"High frequency suspicious pattern detected: {pattern}")

    def record_agent_violation(self, user_id: str, violation_type: str, agent_name: str | None = None):
        """Record agent-related security violations for rate limiting"""
        key = f"{user_id}:{violation_type}"
        current_time = time.time()
        self.agent_violations[key].append(current_time)

        # Clean old violations (1 hour window)
        cutoff = current_time - 3600
        while self.agent_violations[key] and self.agent_violations[key][0] < cutoff:
            self.agent_violations[key].popleft()

        # Count violations
        violation_count = len(self.agent_violations[key])

        # Apply progressive rate limiting based on violation count
        if violation_count >= 10:  # 10 violations in 1 hour = 1 hour block
            self.block_ip(user_id, 3600)
            logger.critical(
                f"🚨 AGENT ABUSE: Blocking user {user_id} for 1 hour - {violation_count} {violation_type} violations"
            )
            if agent_name:
                logger.critical(f"   Agent involved: {agent_name}")
        elif violation_count >= 5:  # 5 violations = 10 minute block
            self.block_ip(user_id, 600)
            logger.error(
                f"⚠️ AGENT WARNING: Blocking user {user_id} for 10 minutes - {violation_count} {violation_type} violations"
            )
        elif violation_count >= 3:  # 3 violations = 1 minute block
            self.block_ip(user_id, 60)
            logger.warning(
                f"⚡ AGENT NOTICE: Blocking user {user_id} for 1 minute - {violation_count} {violation_type} violations"
            )

        return violation_count


# Global rate limit store
rate_limit_store = RateLimitStore()


class RateLimitConfig:
    """Configuration for different endpoint rate limits"""

    def __init__(self):
        # Define rate limits per endpoint pattern
        self.endpoint_limits = {
            # Critical endpoints - very restrictive
            "/admin": {"requests": 10, "window": 60, "burst": 2},
            "/api/v1/admin": {"requests": 20, "window": 60, "burst": 5},
            # Authentication endpoints - moderate restrictions
            # NOTE: Our auth router is mounted at '/auth', not '/api/v1/auth'
            "/auth/local/login": {"requests": 5, "window": 60, "burst": 2},
            "/auth/local/refresh": {"requests": 40, "window": 60, "burst": 15},
            "/auth/login": {"requests": 5, "window": 60, "burst": 2},
            "/auth/register": {"requests": 3, "window": 300, "burst": 1},
            # HARDENING: Increased refresh limits to handle multi-tab/component refresh coordination
            # The singleflight pattern in frontend should reduce actual requests, but we allow
            # headroom for edge cases (network retries, tab coordination delays, SSE reconnects)
            "/auth/refresh": {"requests": 40, "window": 60, "burst": 15},
            "/auth/me": {"requests": 60, "window": 60, "burst": 20},  # Higher burst for space switching
            # Search endpoints - needs DDoS protection
            "/search": {"requests": 30, "window": 60, "burst": 10},
            "/api/v1/search": {"requests": 30, "window": 60, "burst": 10},
            # Message endpoints - higher burst for space switching
            "/auth/messages": {"requests": 60, "window": 60, "burst": 20},
            "/mcp/messages": {"requests": 60, "window": 60, "burst": 20},
            # Task endpoints - higher burst for space switching
            "/api/v1/tasks": {"requests": 60, "window": 60, "burst": 20},
            "/mcp/tasks": {"requests": 60, "window": 60, "burst": 20},
            # Organization endpoints - higher burst for space switching
            "/api/organizations": {"requests": 60, "window": 60, "burst": 25},
            "/api/organizations/switch": {"requests": 30, "window": 60, "burst": 15},
            # Widget resolve - high burst for chat replay (many inline MCP widgets per transcript)
            "/api/v1/messages/widgets/resolve": {"requests": 300, "window": 60, "burst": 60},
            # Public email-generating contact forms: keep intentionally low so
            # unauthenticated clients cannot produce a large SES/admin-email burst.
            "/api/v1/public/request-demo": {"requests": 5, "window": 300, "burst": 2},
            "/api/v1/request-demo": {"requests": 5, "window": 300, "burst": 2},
            # General API endpoints - moderate limits
            "/api": {"requests": 100, "window": 60, "burst": 30},
            # Increased for cloud agents: tool history adds ~3 calls/turn, agents make 10-15 calls/invocation
            "/mcp": {"requests": 400, "window": 60, "burst": 80},
            # Health check - allow monitoring
            "/health": {"requests": 100, "window": 60, "burst": 20},
            "/metrics": {"requests": 50, "window": 60, "burst": 10},
            # OAuth endpoints - production-secure defaults (use env overrides for dev)
            "/.well-known/oauth-authorization-server": {"requests": 30, "window": 60, "burst": 10},
            "/.well-known/oauth-protected-resource": {"requests": 30, "window": 60, "burst": 10},
            "/oauth/register": {"requests": 20, "window": 60, "burst": 5},
            "/oauth/authorize": {"requests": 10, "window": 60, "burst": 3},  # Strict for production security
            "/oauth/callback": {"requests": 10, "window": 60, "burst": 3},  # NEW: Strict callback limits
            "/oauth/token": {"requests": 60, "window": 60, "burst": 20},  # Token exchange needs higher throughput
            # Invite-only waitlist approve link — clicked from email, token IS the
            # auth; throttle to blunt token-guessing / abuse.
            "/auth/access/approve": {"requests": 10, "window": 60, "burst": 3},
        }

        # Global fallback limits
        self.default_limit = {"requests": 1000, "window": 60, "burst": 200}

        # Paid users get more API headroom, but no role receives unlimited traffic.
        # Sensitive auth/OAuth endpoints stay fixed to their base security limits.
        self.tier_aliases = {
            "guest": "guest",
            "free": "regular",
            "regular": "regular",
            "user": "regular",
            "plus": "plus",
            "pro": "plus",
            "premium": "plus",
            "paid": "plus",
            "agent_manager": "admin",
            "admin": "admin",
            "super_admin": "admin",
            "enterprise": "admin",
        }
        self.tier_multipliers = {
            "regular": 1.0,
            "guest": 1.0,
            "plus": 2.0,
            "admin": 2.0,
        }
        self.max_tier_requests = int(os.getenv("RATE_LIMIT_MAX_REQUESTS_PER_WINDOW", "2000") or 2000)
        self.max_tier_burst = int(os.getenv("RATE_LIMIT_MAX_BURST", "400") or 400)
        self.tier_fixed_limit_prefixes = (
            "/admin",
            "/api/v1/admin",
            "/auth/login",
            "/auth/register",
            "/oauth/register",
            "/oauth/authorize",
            "/oauth/callback",
            "/.well-known/",
        )

        # Aggressive blocking thresholds (relaxed for debugging)
        self.block_thresholds = {
            "high_frequency": {"violations": 50, "duration": 60},  # 50 violations = 1min block
            "extreme_frequency": {"violations": 100, "duration": 120},  # 100 violations = 2min block
            "persistent_abuse": {"violations": 200, "duration": 180},  # 200 violations = 3min block
        }


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """Advanced rate limiting middleware with DDoS protection"""

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self.enabled = enabled
        self.config = RateLimitConfig()
        self.violation_counts: dict[str, int] = defaultdict(int)

        # Track 429 surge per key
        self.surge_counter: dict[str, int] = defaultdict(int)
        self.surge_window_start = time.time()

        # DDoS detection patterns
        # SECURITY: /auth/login and /auth/register are still protected against brute force
        # NUANCED: /auth/refresh, /oauth/*, SSE endpoints excluded (legitimate 401s)
        # This prevents blocking users with expired tokens while still defending against attacks
        self.ddos_patterns = [
            {"name": "rapid_requests", "threshold": 100, "window": 10},
            {"name": "burst_requests", "threshold": 50, "window": 5},
            {"name": "sustained_load", "threshold": 500, "window": 60},
        ]

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request through rate limiting"""

        if not self.enabled:
            return await call_next(request)

        # Generate request ID for tracking
        request_id = str(uuid.uuid4())
        environment = os.getenv("ENVIRONMENT", "development").lower()

        # Dev-only: bypass RL for admin routes (authenticated admins only, enforced by endpoint)
        try:
            if environment in ("development", "dev", "local") and request.url.path.startswith("/admin/"):
                return await call_next(request)
        except Exception:
            pass

        # Dev-only: allow rapid switching between seeded test accounts without
        # hitting rate limits on the /auth/login and /auth/register endpoints.
        if environment in ("development", "dev", "local") and request.url.path in ("/auth/login", "/auth/register"):
            # Always allow OPTIONS preflight requests for CORS
            if request.method == "OPTIONS":
                logger.debug(f"Skipping rate limit for OPTIONS preflight request to {request.url.path}")
                return await call_next(request)

            # For POST requests, check if it's a dev test user
            if request.method == "POST":
                try:
                    body_bytes = await request.body()
                    if body_bytes:
                        # Preserve body for downstream consumers
                        request._body = body_bytes
                        data = json.loads(body_bytes.decode("utf-8"))
                    else:
                        data = {}
                except json.JSONDecodeError:
                    data = {}

                email = (data.get("email") or "").lower()
                if email in DEV_TEST_USER_EMAILS:
                    logger.debug(f"Skipping rate limit for dev test user {request.url.path}: {email}")
                    return await call_next(request)

        # Extract rich attribution data
        auth_header = request.headers.get("Authorization", "")
        token_hash8 = hashlib.md5(auth_header.encode()).hexdigest()[:8] if auth_header else None

        # Extract JWT claims if available
        jwt_claims = {}
        if auth_header.startswith("Bearer "):
            try:
                import jwt

                token = auth_header.replace("Bearer ", "")
                jwt_claims = jwt.decode(token, options={"verify_signature": False})
            except Exception:
                pass

        # Build attribution data
        attribution = {
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat(),
            "ip": self._get_client_ip(request),
            "path": request.url.path,
            "method": request.method,
            "agent_name": request.headers.get("X-Agent-Name"),
            "client_id": request.query_params.get("client_id"),
            "user_id": jwt_claims.get("sub"),
            "space_id": jwt_claims.get("space_id") or jwt_claims.get("org_id"),
            "session_id": request.headers.get("Mcp-Session-Id"),
            "token_hash8": token_hash8,
            "user_agent": request.headers.get("User-Agent", "")[:100],
        }
        # These claims are decoded without signature verification and are only
        # safe for attribution. Never scale limits from them; forged roles would
        # otherwise receive paid/admin headroom before auth rejects the request.
        rate_limit_tier = self._extract_rate_limit_tier(
            jwt_claims, claims_verified=False
        )
        attribution["rate_limit_tier"] = rate_limit_tier

        # Check if this request should skip rate limiting entirely (GitHub IPs, etc.)
        if not request.url.path.startswith("/auth/local/") and should_skip_rate_limit(request):
            attribution["rate_limit_skipped"] = True
            logger.info(f"REQUEST_ATTRIBUTION: {json.dumps(attribution)}")
            return await call_next(request)

        client_ip = self._get_client_ip(request)

        # Derive a robust, environment-aware rate limit key
        request_key = None
        try:
            if request.url.path.startswith("/auth/local/"):
                request_key = f"ip:{client_ip}"
            elif environment == "production":
                # In production, prefer hierarchical identifiers with agent/user/client parts
                from app.middleware.production_rate_limit_fix import get_hierarchical_rate_limit_key

                request_key = get_hierarchical_rate_limit_key(request)
            else:
                # In development/local, use the richer local identifier helper
                from app.middleware.local_rate_limit_fix import get_client_identifier

                request_key = get_client_identifier(request)
        except Exception:
            # Fallback to internal hierarchical generator
            request_key = self._generate_hierarchical_key(request, attribution)

        # Normalize raw IP keys so they are always prefixed as ip:<addr>
        known_prefixes = (
            "agent:",
            "user:",
            "client:",
            "token:",
            "sid:",
            "ip:",
            "org:",
            "api_key:",
            "oauth:",
            "session:",
        )
        if not isinstance(request_key, str) or not request_key:
            request_key = f"ip:{client_ip}"
        elif not request_key.startswith(known_prefixes):
            # Handles cases like raw IPv4/IPv6 (e.g., 2601:...)
            request_key = f"ip:{request_key}"

        # Scope-isolate MCP vs API buckets even when using helper-derived keys
        request_key = self._apply_scope_namespace(request_key, request.url.path)

        attribution["rate_limit_key"] = request_key
        attribution["rate_limit_method"] = "hierarchical" if not request_key.startswith("ip:") else "ip"

        # Middleware failures should fail open, but downstream endpoint errors
        # must propagate normally. Otherwise real API failures get converted
        # into empty 200 responses, which breaks MCP clients and hides bugs.
        try:
            # Check if IP is blocked
            if rate_limit_store.is_blocked(client_ip):
                return self._create_blocked_response()

            # Check if this specific key is blocked (circuit breaker for bad agent/session)
            # Skip breaker enforcement entirely for /auth/* and SSE routes
            is_safe_path = request.url.path.startswith("/auth") or request.url.path.startswith("/api/sse")
            if not is_safe_path:
                # Prefer Redis-shared block if available
                try:
                    from app.core.redis_rate_limiter import is_key_blocked as _redis_key_blocked

                    if await _redis_key_blocked(request_key):
                        return self._create_key_blocked_response(request_key)
                except Exception:
                    pass
                if rate_limit_store.is_key_blocked(request_key):
                    return self._create_key_blocked_response(request_key)

            # Check for DDoS patterns
            # NUANCED APPROACH to endpoint protection:
            # - /auth/login, /auth/register: PROTECTED (prevent brute force attacks)
            # - /auth/refresh: EXCLUDED (expired tokens are legitimate, not attacks)
            # - /mcp/sse, /api/sse: EXCLUDED (use query params, 401s expected on reconnect)
            # - /mcp/agents/*: EXCLUDED (agent studio connections can burst legitimately)
            # - /oauth/*: EXCLUDED (external redirects can cause 401s)
            # - localhost/Docker IPs: EXCLUDED (development/testing)

            is_safe_auth_path = (
                request.url.path.startswith("/auth/refresh")
                or request.url.path.startswith("/oauth")
                or request.url.path.startswith("/mcp/sse")
                or request.url.path.startswith("/api/sse")
                or request.url.path.startswith("/mcp/agents/")
                or request.url.path.startswith("/mcp")
            )

            # Exempt localhost and RFC1918 private IPs from DDoS detection
            # RFC1918 private ranges: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
            is_local_ip = (
                client_ip.startswith("127.")  # Localhost
                or client_ip.startswith("10.")  # RFC1918: 10.0.0.0/8
                or client_ip.startswith("192.168.")  # RFC1918: 192.168.0.0/16
                or client_ip == "localhost"
                or client_ip.startswith("::1")  # IPv6 localhost
                or self._is_docker_bridge_ip(client_ip)  # Docker bridge: 172.16.0.0/12
            )

            if not is_safe_auth_path and not is_local_ip and self._detect_ddos_patterns(client_ip):
                rate_limit_store.block_ip(client_ip, 600)  # 10 minute block
                return self._create_ddos_response()

            # Apply rate limiting
            limit_config = self._get_rate_limit_config(request.url.path)

            # Check environment for appropriate overrides
            environment = os.getenv("ENVIRONMENT", "development").lower()

            # Apply production overrides when in production
            if environment == "production":
                try:
                    from app.middleware.production_rate_limit_fix import (
                        PRODUCTION_OVERRIDES,
                        should_apply_production_overrides,
                    )

                    if should_apply_production_overrides(request):
                        for pattern, override in PRODUCTION_OVERRIDES.items():
                            if request.url.path.startswith(pattern):
                                limit_config = override
                                logger.debug(
                                    f"Applying production rate limit overrides for {request.url.path}: {override}"
                                )
                                break
                except ImportError:
                    logger.warning("Production rate limit overrides not available")

            # Apply relaxed local overrides for OAuth/MCP flows when appropriate (dev only)
            else:
                try:
                    from app.middleware.local_rate_limit_fix import LOCAL_DEV_LIMITS, should_use_relaxed_limits

                    if not request.url.path.startswith("/auth/local/") and should_use_relaxed_limits(request):
                        for pattern, override in LOCAL_DEV_LIMITS.items():
                            if request.url.path.startswith(pattern):
                                limit_config = override
                                logger.debug(f"Applying relaxed local rate limits for {request.url.path}: {override}")
                                break
                except Exception:
                    # If overrides aren't available, continue with standard config
                    pass

            limit_config = self._apply_tier_limits(limit_config, rate_limit_tier, request.url.path)
            is_allowed, retry_after = await self._check_rate_limit(
                request_key, limit_config, client_ip, request.url.path
            )

            if not is_allowed:
                # Track surge
                self._track_surge(request_key)
                attribution["status"] = 429
                attribution["rate_limit_exceeded"] = True
                attribution["retry_after"] = retry_after
                logger.info(f"REQUEST_ATTRIBUTION: {json.dumps(attribution)}")

                # Check for surge condition
                if self.surge_counter[request_key] > 10:
                    logger.critical(f"🔥 SURGE key={request_key} 429={self.surge_counter[request_key]}/min")

                # Escalate to per-key block if repeated RL violations
                vio = self.violation_counts.get(request_key, 0)
                resp = self._create_rate_limit_response(retry_after, request_key)
                # Do not escalate on /auth/* or SSE, and never escalate IP-only keys
                may_escalate = (
                    (not request.url.path.startswith("/auth"))
                    and (not request.url.path.startswith("/api/sse"))
                    and (not request_key.startswith("ip:"))
                )
                if may_escalate:
                    if vio >= 6:  # after ~6 RL hits, block for 2 minutes
                        try:
                            from app.core.redis_rate_limiter import block_key as _redis_block

                            await _redis_block(request_key, 120)
                        except Exception:
                            pass
                        rate_limit_store.block_key(request_key, 120)
                        rate_limit_store.blocked_key_reasons[request_key] = "rate_limit_excess"
                        try:
                            from app.core.redis_client import redis_client

                            await redis_client.publish("obs:rate:block_key", f"{request_key}:120")
                        except Exception:
                            pass
                    elif vio >= 4:
                        try:
                            from app.core.redis_rate_limiter import block_key as _redis_block

                            await _redis_block(request_key, 30)
                        except Exception:
                            pass
                        rate_limit_store.block_key(request_key, 30)
                        rate_limit_store.blocked_key_reasons[request_key] = "rate_limit_excess"
                        try:
                            from app.core.redis_client import redis_client

                            await redis_client.publish("obs:rate:block_key", f"{request_key}:30")
                        except Exception:
                            pass
                return resp
        except Exception:
            logger.exception("Rate limiting middleware preflight error")
            return await call_next(request)

        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time

        try:
            # Add attribution with response status
            attribution["status"] = response.status_code
            attribution["process_time_ms"] = int(process_time * 1000)

            # Track 429s from downstream
            if response.status_code == 429:
                self._track_surge(request_key)
                if self.surge_counter[request_key] > 10:
                    logger.critical(f"🔥 DOWNSTREAM SURGE key={request_key} 429={self.surge_counter[request_key]}/min")

            # Circuit breaker for likely misconfiguration on MCP/OAuth: many 4xx quickly
            is_mcp_or_oauth = request.url.path.startswith("/mcp") or request.url.path.startswith("/oauth")
            is_error_status = response.status_code in (400, 401, 403, 422, 429)
            if is_mcp_or_oauth and is_error_status:
                # Window/threshold configurable
                mc_window = int(os.getenv("MCP_MISCONFIG_WINDOW_SECONDS", "10") or 10)
                mc_thresh = int(os.getenv("MCP_MISCONFIG_ERRORS_THRESHOLD", "8") or 8)
                cnt = rate_limit_store.add_error(request_key, window_seconds=mc_window)
                # Never block raw IP keys
                if cnt > mc_thresh and not request_key.startswith("ip:"):
                    # TTL based on environment (shorter in dev)
                    is_dev = os.getenv("ENVIRONMENT", "development").lower() in ("development", "dev", "local")
                    ttl = int(os.getenv("MCP_MISCONFIG_BLOCK_TTL_SECONDS", "30" if is_dev else "120"))
                    rate_limit_store.block_key(request_key, ttl)
                    # tag reason
                    rate_limit_store.blocked_key_reasons[request_key] = "mcp_misconfiguration"

            # Log attribution
            logger.info(f"REQUEST_ATTRIBUTION: {json.dumps(attribution)}")

            # Add rate limiting headers
            self._add_rate_limit_headers(response, request_key, limit_config)

            # Log slow requests (potential DoS attacks)
            if process_time > 5.0:
                logger.warning(f"Slow request from {client_ip}: {request.url.path} took {process_time:.2f}s")
        except Exception:
            logger.exception("Rate limiting middleware post-response error")

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Extract client identifier - uses hierarchical identification"""
        if request.url.path.startswith("/auth/local/"):
            # Only Uvicorn-validated proxy headers may alter request.client.
            # Agent handles, JWT claims and arbitrary X-Forwarded-For values
            # never select a password-auth rate-limit bucket.
            return request.client.host if request.client else "unknown"

        # Check environment
        environment = os.getenv("ENVIRONMENT", "development").lower()

        # Use production fix for production environment
        if environment == "production":
            try:
                from app.middleware.production_rate_limit_fix import get_production_client_identifier

                return get_production_client_identifier(request)
            except ImportError:
                logger.warning("Production rate limit fix not available, falling back to IP-based")

        # Use local development fix for dev/local environments
        else:
            try:
                from app.middleware.local_rate_limit_fix import get_client_identifier

                return get_client_identifier(request)
            except ImportError:
                pass

        # Original IP-based logic as fallback
        forwarded_ip = request.headers.get("x-forwarded-for")
        if forwarded_ip:
            return forwarded_ip.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip

        return request.client.host if request.client else "unknown"

    def _generate_hierarchical_key(self, request: Request, attribution: dict) -> str:
        """Generate hierarchical composite rate-limit key

        Priority order (most specific to least specific):
        1. agent:{agent_name}:user:{user_id}:token:{token_hash8}
        2. agent:{agent_name}:user:{user_id}
        3. user:{user_id}:client:{client_id}
        4. token:{token_hash8}
        5. ip:{ip}
        """
        components = []

        # Build hierarchical key from most specific identifiers
        if attribution.get("agent_name"):
            components.append(f"agent:{attribution['agent_name']}")

        if attribution.get("user_id"):
            components.append(f"user:{attribution['user_id']}")

        if attribution.get("client_id"):
            components.append(f"client:{attribution['client_id']}")

        if attribution.get("token_hash8"):
            components.append(f"token:{attribution['token_hash8']}")

        if attribution.get("session_id"):
            components.append(f"sid:{attribution['session_id']}")

        # Use composite key if we have identifiers, otherwise fall back to IP
        base_key = ":".join(components) if components else f"ip:{attribution.get('ip', 'unknown')}"

        # SCOPE ISOLATION: Separate MCP traffic from API traffic
        # This prevents heavy MCP usage (testing/agents) from blinding the frontend (API)
        if request.url.path.startswith("/mcp"):
            return f"mcp:{base_key}"
        elif request.url.path.startswith("/api"):
            return f"api:{base_key}"

        return base_key

    def _apply_scope_namespace(self, key: str | None, path: str) -> str | None:
        """Namespace keys so MCP traffic cannot exhaust API buckets and vice versa."""
        if not key:
            return key
        if key.startswith("mcp:") or key.startswith("api:"):
            return key
        if path.startswith("/mcp"):
            return f"mcp:{key}"
        if path.startswith("/api"):
            return f"api:{key}"
        return key

    def _generate_request_key(self, request: Request, client_ip: str) -> str:
        """Generate a unique key for rate limiting (legacy method for compatibility)"""
        # Include endpoint, method, and IP
        endpoint = request.url.path
        method = request.method

        # Create a hash for consistent key generation
        key_data = f"{client_ip}:{method}:{endpoint}"
        return hashlib.md5(key_data.encode()).hexdigest()

    def _get_rate_limit_config(self, path: str) -> dict:
        """Get rate limit configuration for the endpoint"""
        # Check for exact matches first
        for pattern, config in self.config.endpoint_limits.items():
            if path.startswith(pattern):
                return config

        # In development/local, allow relaxed limits from local_rate_limit_fix
        try:
            environment = os.getenv("ENVIRONMENT", "development").lower()
            if environment in ("development", "dev", "local"):
                from app.middleware.local_rate_limit_fix import LOCAL_DEV_LIMITS

                for pattern, config in LOCAL_DEV_LIMITS.items():
                    if path.startswith(pattern):
                        return config
        except Exception:
            # Fall through to default limits if overrides unavailable
            pass

        # Return default configuration
        return self.config.default_limit

    def _normalize_rate_limit_tier(self, raw_tier: str | None) -> str:
        """Normalize auth roles/plans into limiter tiers."""
        if not raw_tier:
            return "regular"
        value = str(raw_tier).strip().lower()
        if not value:
            return "regular"
        return self.config.tier_aliases.get(value, "regular")

    def _extract_rate_limit_tier(
        self, jwt_claims: dict, *, claims_verified: bool = False
    ) -> str:
        """Extract a non-spoofable tier signal from decoded token claims.

        The request path currently has only unsigned decoded JWT claims for
        attribution. Tier scaling is allowed only when a caller supplies claims
        from a signature-verified auth context.
        """
        if not claims_verified or not jwt_claims:
            return "regular"

        for claim_name in (
            "rate_limit_tier",
            "role",
            "user_role",
            "custom:role",
            "tier",
            "custom:tier",
            "plan",
            "subscription_plan",
            "custom:plan",
        ):
            tier = jwt_claims.get(claim_name)
            if tier:
                return self._normalize_rate_limit_tier(tier)

        token_class = str(jwt_claims.get("token_class") or "").lower()
        if token_class == "user_admin":
            return "admin"

        return "regular"

    def _apply_tier_limits(self, config: dict, tier: str | None, path: str) -> dict:
        """Return endpoint limits adjusted for paid/admin tiers with hard caps."""
        adjusted = dict(config)
        normalized_tier = self._normalize_rate_limit_tier(tier)
        adjusted["tier"] = normalized_tier

        if normalized_tier in ("regular", "guest"):
            return adjusted

        if path.startswith(self.config.tier_fixed_limit_prefixes):
            adjusted["tier_multiplier"] = 1.0
            return adjusted

        multiplier = self.config.tier_multipliers.get(normalized_tier, 1.0)
        if multiplier <= 1:
            adjusted["tier_multiplier"] = 1.0
            return adjusted

        base_requests = int(config["requests"])
        base_burst = int(config.get("burst", max(1, base_requests // 5)))
        adjusted["base_requests"] = base_requests
        adjusted["base_burst"] = base_burst
        adjusted["requests"] = min(int(base_requests * multiplier), self.config.max_tier_requests)
        adjusted["burst"] = min(int(base_burst * multiplier), adjusted["requests"], self.config.max_tier_burst)
        adjusted["tier_multiplier"] = multiplier
        return adjusted

    async def _check_rate_limit(
        self, request_key: str, config: dict, client_ip: str, path: str
    ) -> tuple[bool, int | None]:
        """Check if request is within rate limits"""
        window_seconds = config["window"]
        max_requests = config["requests"]
        burst_limit = config.get("burst", max_requests // 5)

        # Prefer Redis-backed limiter when available
        try:
            from app.core.redis_rate_limiter import check_allow as _redis_allow

            allowed, retry_after, _remaining = await _redis_allow(
                request_key, max_requests, window_seconds, burst_limit=burst_limit
            )
            if not allowed:
                self._record_violation(client_ip, path)
                return False, retry_after or 1
            return True, None
        except Exception:
            # Fallback to in-memory if Redis not available
            pass

        # Cleanup old requests
        rate_limit_store.cleanup_old_requests(request_key, window_seconds)

        # Check burst protection (very short window)
        burst_key = f"{request_key}:burst"
        rate_limit_store.cleanup_old_requests(burst_key, 5)  # 5 second burst window
        burst_count = rate_limit_store.add_request(burst_key)

        if burst_count > burst_limit:
            logger.warning(f"Burst limit exceeded for {client_ip}: {burst_count}/{burst_limit}")
            self._record_violation(client_ip, path)
            return False, 5  # Short retry time for burst

        # Check main rate limit
        current_count = rate_limit_store.add_request(request_key)

        if current_count > max_requests:
            logger.warning(f"Rate limit exceeded for {client_ip}: {current_count}/{max_requests}")
            self._record_violation(client_ip, path)
            retry_after = window_seconds - (time.time() % window_seconds)
            return False, int(retry_after)

        return True, None

    def _record_violation(self, client_ip: str, path: str):
        """Record rate limit violation and apply escalating blocks.

        Behavior by environment:
        - production: Never IP-block for routine RL violations (only DDoS path blocks).
        - development: IP-blocking is disabled by default to avoid disruptive local testing.
          A short TTL block can be enabled via DEV_IP_BLOCK_ENABLED=true with
          DEV_IP_BLOCK_TTL_SECONDS (default 30s). Docker subnet (172.18.0.0/16) is
          always exempt in development.
        """
        self.violation_counts[client_ip] += 1
        violations = self.violation_counts[client_ip]

        environment = os.getenv("ENVIRONMENT", "development").lower()

        # Never IP block in production for routine RL violations
        if environment == "production":
            logger.debug(f"RL violation (no IP block in prod): ip={client_ip} path={path} count={violations}")
            return

        # Development / non-prod behavior
        # Exempt Docker subnet to prevent blocking local test runners
        if client_ip.startswith("172.18."):
            logger.debug(f"RL violation (docker subnet exempt in dev): ip={client_ip} path={path} count={violations}")
            return

        # Allow opt-in short TTL blocks in development
        dev_ip_block_enabled = os.getenv("DEV_IP_BLOCK_ENABLED", "false").lower() in ("1", "true", "yes", "on")
        dev_ip_block_ttl = int(os.getenv("DEV_IP_BLOCK_TTL_SECONDS", "30") or 30)

        if not dev_ip_block_enabled:
            # Only log in dev by default; no IP block
            logger.debug(f"RL violation (dev no-block): ip={client_ip} path={path} count={violations}")
            return

        # Apply a gentle short TTL block in dev when enabled
        for _threshold_name, threshold_config in self.config.block_thresholds.items():
            if violations >= threshold_config["violations"]:
                rate_limit_store.block_ip(client_ip, dev_ip_block_ttl)
                logger.error(f"Applied DEV short block ({dev_ip_block_ttl}s) to {client_ip}: {violations} violations")
                break

    def _is_docker_bridge_ip(self, client_ip: str) -> bool:
        """
        Check if IP is in Docker bridge range (172.16.0.0/12)
        RFC1918 reserves 172.16.0.0 - 172.31.255.255 for private networks
        """
        try:
            parts = client_ip.split(".")
            if len(parts) != 4 or parts[0] != "172":
                return False
            second_octet = int(parts[1])
            # 172.16.0.0/12 means second octet must be 16-31
            return 16 <= second_octet <= 31
        except (ValueError, IndexError):
            return False

    def _detect_ddos_patterns(self, client_ip: str) -> bool:
        """Detect DDoS attack patterns"""
        for pattern in self.ddos_patterns:
            pattern_key = f"{client_ip}:ddos:{pattern['name']}"
            window = pattern["window"]
            threshold = pattern["threshold"]

            # Cleanup old requests for this pattern
            rate_limit_store.cleanup_old_requests(pattern_key, window)

            # Add current request
            count = rate_limit_store.add_request(pattern_key)

            if count > threshold:
                logger.critical(f"DDoS pattern '{pattern['name']}' detected from {client_ip}: {count}/{threshold}")
                rate_limit_store.record_suspicious_pattern(f"ddos:{pattern['name']}:{client_ip}")
                return True

        return False

    def _add_rate_limit_headers(self, response: Response, request_key: str, config: dict):
        """Add rate limiting headers to response"""
        window_seconds = config["window"]
        max_requests = config["requests"]
        # Calculate remaining requests from in-memory tracker (best effort)
        rate_limit_store.cleanup_old_requests(request_key, window_seconds)
        current_count = len(rate_limit_store.requests[request_key])
        remaining = max(0, max_requests - current_count)
        # Approximate reset time (window from now). For Redis path this may differ slightly.
        reset_time = int(time.time() + window_seconds)

        # Add standard rate limiting headers
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)
        response.headers["X-RateLimit-Window"] = str(window_seconds)
        # Add the key for debugging (helps identify rate limit buckets)
        response.headers["X-RateLimit-Key"] = request_key
        response.headers["X-RateLimit-Tier"] = str(config.get("tier", "regular"))

    def _track_surge(self, key: str):
        """Track surge events per key"""
        current_time = time.time()
        # Reset counter every minute
        if current_time - self.surge_window_start > 60:
            self.surge_counter.clear()
            self.surge_window_start = current_time
        self.surge_counter[key] += 1

    def _create_rate_limit_response(self, retry_after: int | None = None, key: str | None = None) -> JSONResponse:
        """Create rate limit exceeded response with exponential backoff"""
        if retry_after is None:
            # Calculate exponential backoff with jitter
            violations = self.violation_counts.get(key, 0) if key else 0
            base_penalty = 5  # 5 seconds base
            max_penalty = 600  # 10 minutes max
            penalty = min(max_penalty, base_penalty * (2**violations))
            # Add ±20% jitter
            retry_after = int(penalty * (0.8 + random.random() * 0.4))

        # Track violation
        if key:
            self.violation_counts[key] += 1

        next_allowed = datetime.utcnow() + timedelta(seconds=retry_after)

        content = {
            "error": "rate_limited",
            "key": key or "unknown",
            "retry_after": retry_after,
            "next_allowed_at": next_allowed.isoformat() + "Z",
            "message": f"Rate limit exceeded. Please wait {retry_after} seconds before retrying.",
        }

        # CRITICAL: Include CORS headers to prevent browser 'net::ERR_FAILED' errors
        headers = {
            "Retry-After": str(retry_after),
            "X-RateLimit-Key": key or "unknown",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int(time.time() + retry_after)),
            "Cache-Control": "no-store",
            **_get_cors_headers(),
        }

        return JSONResponse(status_code=429, content=content, headers=headers)

    def _create_blocked_response(self) -> JSONResponse:
        """Create blocked IP response"""
        return JSONResponse(
            status_code=429,
            content={
                "error": "IP temporarily blocked",
                "message": "Your IP has been temporarily blocked due to excessive requests.",
                "type": "ip_blocked",
            },
            headers=_get_cors_headers(),
        )

    def _create_ddos_response(self) -> JSONResponse:
        """Create DDoS detection response"""
        return JSONResponse(
            status_code=429,
            content={
                "error": "DDoS pattern detected",
                "message": "Suspicious traffic pattern detected. Access temporarily restricted.",
                "type": "ddos_protection",
            },
            headers=_get_cors_headers(),
        )

    def _create_key_blocked_response(self, key: str) -> JSONResponse:
        """Create response when a specific agent/session key is blocked."""
        # Remaining block time for UX; approximate from store
        remaining = 60
        try:
            until = rate_limit_store.blocked_keys.get(key)
            if until:
                remaining = max(1, int(until - time.time()))
        except Exception:
            pass
        reason = None
        try:
            reason = rate_limit_store.blocked_key_reasons.get(key)
        except Exception:
            reason = None
        # Helpful message for MCP misconfiguration
        message = "This agent connection is temporarily blocked due to repeated errors. Other activity is unaffected."
        extra: dict[str, str] = {}
        if reason == "mcp_misconfiguration":
            message = (
                "MCP requests look misconfigured (repeated 4xx). Pausing this agent key briefly to protect the API. "
                "Check your MCP config (server URL, auth token, X-Agent-Name) and try again."
            )
            extra = {
                "docs": "AGENTS.md#e2e-dev-relax-mode-local",
                "config_files": "~/.codex/config.toml, .mcp.json",
            }
        # CRITICAL: Include CORS headers to prevent browser 'net::ERR_FAILED' errors
        return JSONResponse(
            status_code=429,
            content={
                "error": "client_blocked",
                "message": message,
                "type": "agent_circuit_breaker",
                "key": key,
                "retry_after": remaining,
                "reason": reason,
                **extra,
            },
            headers={
                "Retry-After": str(remaining),
                "X-RateLimit-Key": key,
                "X-RateLimit-Remaining": "0",
                **_get_cors_headers(),
            },
        )


def create_rate_limiting_middleware(enabled: bool = True) -> RateLimitingMiddleware:
    """Factory function to create rate limiting middleware"""
    return lambda app: RateLimitingMiddleware(app, enabled=enabled)


# Health check for rate limiting system
async def get_rate_limiting_stats() -> dict:
    """Get current rate limiting statistics"""
    return {
        "active_request_keys": len(rate_limit_store.requests),
        "blocked_ips": len(rate_limit_store.blocked_ips),
        "suspicious_patterns": dict(rate_limit_store.suspicious_patterns),
        "total_requests_tracked": sum(len(requests) for requests in rate_limit_store.requests.values()),
    }
