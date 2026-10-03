"""
Local Development Rate Limiting Fix

Problem: All local MCP agents share the same IP (localhost), causing false rate limiting.
Solution: Use client_id or agent_name for rate limiting in development mode.
"""

import os
from typing import Optional
from starlette.requests import Request

def get_client_identifier(request: Request) -> str:
    """
    Get a unique client identifier for rate limiting.
    Uses OAuth token resolver for proper user/agent identification.
    """
    # Get the actual client IP first (for fallback)
    client_ip = get_client_ip_fallback(request)

    # Check if this is a special IP that needs client-specific identification
    is_development = os.getenv("ENVIRONMENT", "").lower() in ["development", "dev", "local"]
    is_github_ip = "185.199" in client_ip or "140.82" in client_ip
    is_docker_ip = client_ip.startswith("172.18.")
    should_use_client_id = is_development or is_github_ip or is_docker_ip

    if should_use_client_id:
        # Try to extract client identifier from various sources

        # 1. Get agent_name from header (HIGHEST PRIORITY)
        # This allows clients to specify which agent they're acting as
        # The user controls their agent tokens, so sharing between agents is their choice
        agent_name = request.headers.get('x-agent-name')

        # Optional per-client instance identifier to distinguish terminals/processes
        client_instance = request.headers.get('x-client-instance')

        # 2. Get user_id from OAuth token
        # CURRENT: We decode JWTs (because OAuth returns JWTs, not opaque tokens)
        # FUTURE: We'll look up opaque tokens in Redis
        auth_header = request.headers.get('authorization', '')
        user_id = None

        if auth_header.startswith('Bearer '):
            token = auth_header[7:]

            # Try to decode as JWT (current implementation)
            # TODO: When we migrate to proper OAuth 2.1, this should be a Redis lookup
            if token.count('.') == 2:  # Looks like a JWT
                try:
                    import base64
                    import json
                    parts = token.split('.')
                    payload = parts[1]
                    # Add padding if needed
                    padding = 4 - (len(payload) % 4)
                    if padding != 4:
                        payload += '=' * padding

                    decoded = base64.urlsafe_b64decode(payload)
                    claims = json.loads(decoded)

                    # Get user_id from token
                    user_id = claims.get('sub') or claims.get('user_id')

                    # If no agent_name from header, try token (backward compat)
                    if not agent_name:
                        agent_name = claims.get('agent_name')

                except Exception:
                    pass

            # Prefer the richest identifier available
            if agent_name and user_id and client_instance:
                return f"agent:{agent_name}:user:{str(user_id)[:8]}:client:{client_instance[:8]}"
            if agent_name and user_id:
                return f"agent:{agent_name}:user:{str(user_id)[:8]}"
            if agent_name and client_instance:
                return f"agent:{agent_name}:client:{client_instance[:8]}"
            if client_instance and user_id:
                return f"client:{client_instance[:8]}:user:{str(user_id)[:8]}"
            # OK: just agent (no user context)
            elif agent_name:
                return f"agent:{agent_name}"
            # Fallback: use token hash
            import hashlib
            token_hash = hashlib.md5(token.encode()).hexdigest()[:8]
            return f"token:{token_hash}"

        # 2b. Header/query-only identification (no token yet)
        # Also check query params for agent_name and client_instance
        qp_agent = None
        qp_inst = None
        try:
            if hasattr(request, 'query_params'):
                qp_agent = request.query_params.get('agent_name')
                qp_inst = request.query_params.get('client_instance')
        except Exception:
            pass

        # Use headers if available, else query params
        final_agent = agent_name or qp_agent
        final_instance = client_instance or qp_inst

        if final_agent and final_instance:
            return f"agent:{final_agent}:client:{final_instance[:8]}"
        if final_agent:
            return f"agent:{final_agent}"
        if final_instance:
            return f"client:{final_instance[:8]}"

        # 3. Check OAuth client_id in query params (for OAuth flows)
        if hasattr(request, 'query_params'):
            client_id = request.query_params.get('client_id')
            if client_id:
                return f"client:{client_id}"

        # 4. For /oauth/register, try to extract client_name from JSON body
        if request.url.path == "/oauth/register" and request.method == "POST":
            try:
                if hasattr(request, '_body') and request._body:
                    import json
                    body_data = json.loads(request._body)
                    client_name = body_data.get('client_name')
                    if client_name:
                        return f"client:{client_name}"
            except:
                pass

        # 5. Check form data for client_id (POST /oauth/token)
        if request.method == "POST" and hasattr(request, '_body'):
            try:
                # Parse form data for client_id
                body = request._body
                if body and b'client_id=' in body:
                    # Extract client_id from form data
                    parts = body.decode('utf-8').split('&')
                    for part in parts:
                        if part.startswith('client_id='):
                            client_id = part.split('=')[1]
                            return f"client:{client_id}"
            except:
                pass

    # Fallback to IP-based identification (production behavior)
    return get_client_ip_fallback(request)


def get_client_ip_fallback(request: Request) -> str:
    """Original IP-based client identification"""
    # Check for forwarded IP headers
    forwarded_ip = request.headers.get("x-forwarded-for")
    if forwarded_ip:
        return forwarded_ip.split(",")[0].strip()

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip

    # Fallback to direct connection IP
    return request.client.host if request.client else "unknown"


def should_use_relaxed_limits(request: Request) -> bool:
    """
    Determine if relaxed rate limits should be applied.
    CRITICAL: Only relax limits in development environment, NEVER in production.
    GitHub IPs do NOT get relaxed limits - they just get better client identification.
    """
    # ONLY relax in development environment
    is_development = os.getenv("ENVIRONMENT", "").lower() in ["development", "dev", "local"]

    if not is_development:
        return False  # NEVER relax limits in production, even for GitHub IPs

    # TEMP: Dev-only bypass for OAuth endpoints to unblock connections
    path = request.url.path
    if path.startswith("/oauth/"):
        return True  # TEMPORARY: dev-only to unblock OAuth during testing

    # In development, apply relaxed limits for agent requests
    if request.headers.get('x-agent-name') or request.headers.get('x-client-instance'):
        return True

    # In development, apply relaxed limits for authenticated frontend requests
    # This prevents the frontend from being rate-limited when agents (on same IP) flood the system
    if request.headers.get('authorization', '').startswith('Bearer '):
        return True

    # In development, check if this is an OAuth or MCP request that needs relaxed limits
    oauth_paths = ['/.well-known/', '/mcp/']

    return any(path.startswith(p) for p in oauth_paths)


# Rate limit overrides for local development
LOCAL_DEV_LIMITS = {
    # OAuth flows (dev-relaxed for heavy local testing)
    "/oauth/register": {"requests": 60, "window": 60, "burst": 20},
    "/oauth/token": {"requests": 100, "window": 60, "burst": 30},
    "/oauth/authorize": {"requests": 60, "window": 60, "burst": 20},   # Relaxed for dev testing
    "/oauth/callback": {"requests": 60, "window": 60, "burst": 20},    # NEW: Relaxed callback for dev
    "/oauth/refresh": {"requests": 60, "window": 60, "burst": 10},

    # Auth (JWT endpoints) — relaxed in dev for test automation
    "/auth/register": {"requests": 60, "window": 60, "burst": 30},
    "/auth/login": {"requests": 120, "window": 60, "burst": 40},
    "/auth/messages": {"requests": 300, "window": 60, "burst": 100},

    # Discovery endpoints
    "/.well-known/oauth-authorization-server": {"requests": 200, "window": 60, "burst": 50},
    "/.well-known/oauth-protected-resource": {"requests": 200, "window": 60, "burst": 50},
    "/.well-known": {"requests": 200, "window": 60, "burst": 50},

    # MCP endpoints (dev-high throughput)
    "/mcp/messages": {"requests": 500, "window": 60, "burst": 100},
    "/mcp/tasks": {"requests": 500, "window": 60, "burst": 100},
    "/mcp/search": {"requests": 500, "window": 60, "burst": 100},
    "/mcp": {"requests": 500, "window": 60, "burst": 100},

    # SSE endpoints
    "/api/sse/messages": {"requests": 100, "window": 60, "burst": 50},
    "/api/sse": {"requests": 100, "window": 60, "burst": 50},

    # API v1 endpoints — frontend widget enrichment fires per-message bursts
    "/api/v1/messages/widgets": {"requests": 300, "window": 60, "burst": 60},
    "/api/v1/tool-calls": {"requests": 300, "window": 60, "burst": 60},
    "/api/v1/messages": {"requests": 300, "window": 60, "burst": 100},
    "/api/v1": {"requests": 300, "window": 60, "burst": 100},
}
