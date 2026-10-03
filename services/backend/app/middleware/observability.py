"""
Observability Middleware - Request Classification and Structured Logging
Provides "who/what/why" context for every request
"""
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional, Dict, Any
import time
import json
import logging
from datetime import datetime
import ipaddress

logger = logging.getLogger("observability")

# GitHub webhook IPs (update as needed)
GITHUB_CIDRS = [
    "140.82.112.0/20",
    "143.55.64.0/20",
    "185.199.108.0/22",
    "192.30.252.0/22",
    "2606:50c0::/32",
    "2a0a:a440::/29"
]

# Uptime check user agents
UPTIME_CHECK_AGENTS = [
    "googlestackdrivermonitoring-uptimechecks",
    "uptimerobot",
    "pingdom",
    "statuscake"
]

class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Classify and log all requests with rich context"""

    def __init__(self, app):
        super().__init__(app)
        self.github_networks = [ipaddress.ip_network(cidr) for cidr in GITHUB_CIDRS]

    def classify_source(self, request: Request) -> str:
        """
        Classify request source for observability
        Returns: mcp | browser | github | uptime_check | health | unknown
        """
        # Check for MCP agent
        if request.headers.get("X-Agent-Name") or request.headers.get("Mcp-Session-Id"):
            return "mcp"

        # Check for browser webapp
        if request.headers.get("X-Client") == "webapp":
            return "browser"

        # Check path-based classification
        if request.url.path == "/health":
            return "health"

        if request.url.path.startswith("/obs/"):
            return "observability"

        # Check for uptime monitoring
        user_agent = request.headers.get("User-Agent", "").lower()
        for check_agent in UPTIME_CHECK_AGENTS:
            if check_agent in user_agent:
                return "uptime_check"

        # Check for GitHub webhooks
        client_ip = self.get_client_ip(request)
        if self.is_github_ip(client_ip):
            return "github"

        return "unknown"

    def get_client_ip(self, request: Request) -> str:
        """Extract real client IP from headers"""
        # Check for proxy headers
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fall back to direct connection
        if request.client:
            return request.client.host

        return "unknown"

    def is_github_ip(self, ip: str) -> bool:
        """Check if IP belongs to GitHub"""
        try:
            addr = ipaddress.ip_address(ip)
            for network in self.github_networks:
                if addr in network:
                    return True
        except ValueError:
            pass
        return False

    def get_user_agent_family(self, user_agent: str) -> str:
        """Normalize user agent to family"""
        ua_lower = user_agent.lower()

        # Common agent families
        if "mcp-remote" in ua_lower:
            return "mcp-remote"
        elif "chrome" in ua_lower:
            return "chrome"
        elif "firefox" in ua_lower:
            return "firefox"
        elif "safari" in ua_lower and "chrome" not in ua_lower:
            return "safari"
        elif "edge" in ua_lower:
            return "edge"
        elif "curl" in ua_lower:
            return "curl"
        elif "python" in ua_lower:
            return "python"
        elif "node" in ua_lower:
            return "node"

        return "other"

    async def dispatch(self, request: Request, call_next):
        """Process request with observability context"""
        start_time = time.time()

        # Classify the source
        source = self.classify_source(request)

        # Extract context
        client_ip = self.get_client_ip(request)
        user_agent = request.headers.get("User-Agent", "")
        user_agent_family = self.get_user_agent_family(user_agent)

        # Store in request state for downstream use
        request.state.source = source
        request.state.client_ip = client_ip
        request.state.user_agent_family = user_agent_family
        request.state.request_start = start_time

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Extract auth context if available
        agent_name = request.headers.get("X-Agent-Name", "")
        session_id = request.headers.get("Mcp-Session-Id", "")
        web_session = request.headers.get("X-Web-Session", "")

        # Check if rate limited
        rate_limited = response.status_code == 429
        retry_after = response.headers.get("Retry-After", "")
        rate_scope = response.headers.get("X-Rate-Limit-Scope", "")
        rate_key = response.headers.get("X-Rate-Limit-Key", "")

        # Build structured log entry
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "source": source,
            "client_ip": client_ip,
            "user_agent_family": user_agent_family,
            "route": request.url.path,
            "method": request.method,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "rate_limited": rate_limited,
        }

        # Add optional fields
        if agent_name:
            log_entry["agent_name"] = agent_name
        if session_id:
            log_entry["session_id"] = session_id[:8] + "..."  # Truncate for logs
        if web_session:
            log_entry["web_session"] = web_session
        if rate_limited:
            log_entry["retry_after"] = retry_after
            log_entry["rate_scope"] = rate_scope
            log_entry["rate_key"] = rate_key

        # Log based on status
        if response.status_code >= 500:
            logger.error(json.dumps(log_entry))
        elif response.status_code >= 400:
            logger.warning(json.dumps(log_entry))
        else:
            # Sample high-frequency endpoints
            if source in ["health", "observability"] and duration_ms < 100:
                # 1% sampling for fast health checks
                import random
                if random.random() < 0.01:
                    log_entry["sampled"] = True
                    logger.info(json.dumps(log_entry))
            else:
                logger.info(json.dumps(log_entry))

        # Add observability headers to response
        response.headers["X-Request-Source"] = source
        response.headers["X-Duration-Ms"] = str(duration_ms)

        return response

def setup_observability(app):
    """Setup observability middleware"""
    app.add_middleware(ObservabilityMiddleware)
    logger.info("Observability middleware configured")
