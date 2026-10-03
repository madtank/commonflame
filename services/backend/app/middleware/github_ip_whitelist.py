"""
GitHub IP Whitelist for Rate Limiting
Prevents rate limiting of GitHub's infrastructure IPs
"""

import ipaddress
from typing import List, Optional

# GitHub's official IP ranges (from https://api.github.com/meta)
# Rate limiting should be selective - only webhooks should bypass, not OAuth
GITHUB_IP_RANGES = [
    "192.30.252.0/22",
    "185.199.108.0/22",  # Includes 185.199.111.133 that we saw in logs
    "140.82.112.0/20",
    "143.55.64.0/20",
    "2606:50c0::/32",
    "2a0a:a440::/29",
]

# Parse IP ranges into network objects for efficient checking
GITHUB_NETWORKS = []
for range_str in GITHUB_IP_RANGES:
    try:
        GITHUB_NETWORKS.append(ipaddress.ip_network(range_str))
    except ValueError:
        pass  # Skip invalid ranges

def is_github_ip(ip_str: str) -> bool:
    """
    Check if an IP address belongs to GitHub's infrastructure

    Args:
        ip_str: IP address as string (e.g., "185.199.111.133")

    Returns:
        True if the IP belongs to GitHub, False otherwise
    """
    if not ip_str:
        return False

    try:
        ip = ipaddress.ip_address(ip_str)

        # Check if IP is in any GitHub network range
        for network in GITHUB_NETWORKS:
            if ip in network:
                return True

        # Also check for specific GitHub IPs we've seen
        known_github_ips = {
            "185.199.108.133",
            "185.199.109.133",
            "185.199.110.133",
            "185.199.111.133",  # The one causing issues
        }

        return ip_str in known_github_ips

    except ValueError:
        # Invalid IP address
        return False

def get_client_ip(request) -> Optional[str]:
    """
    Extract the real client IP from request headers
    Handles X-Forwarded-For and X-Real-IP headers

    Args:
        request: FastAPI Request object

    Returns:
        Client IP address or None
    """
    # Check X-Forwarded-For header (used by proxies/load balancers)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For can contain multiple IPs, take the first one
        return forwarded.split(",")[0].strip()

    # Check X-Real-IP header
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    # Fall back to direct client IP
    if request.client and request.client.host:
        return request.client.host

    return None

def should_skip_rate_limit(request) -> bool:
    """
    Determine if a request should skip rate limiting

    Args:
        request: FastAPI Request object

    Returns:
        True if rate limiting should be skipped, False otherwise
    """
    # Get the client IP
    client_ip = get_client_ip(request)

    if not client_ip:
        return False

    # Get the request path
    path = request.url.path

    # CRITICAL: Never skip rate limiting for OAuth endpoints
    # This prevents mcp-remote from hammering the API
    if path.startswith("/oauth/"):
        return False

    # Define paths that can be whitelisted for GitHub IPs
    WHITELISTED_PATHS = {"/github/webhook", "/api/github/events", "/webhooks/github"}

    # Check if it's a GitHub IP AND accessing a whitelisted path
    if is_github_ip(client_ip) and path in WHITELISTED_PATHS:
        return True

    # Check for localhost/development IPs (optional)
    if client_ip in ["127.0.0.1", "::1", "localhost"]:
        return True

    # Check for Docker network IPs (172.17.0.0/16 and 172.18.0.0/16)
    if client_ip.startswith("172.17.") or client_ip.startswith("172.18."):
        # Special handling for OAuth discovery endpoints - always allow
        if path.startswith("/.well-known/"):
            return True
        # For Docker network, be more permissive on MCP endpoints
        if path == "/mcp":
            return True

    # Always allow OAuth discovery endpoints regardless of IP
    # These are metadata endpoints that should never be rate limited
    if path.startswith("/.well-known/oauth"):
        return True

    return False

def should_apply_gentle_limits(request) -> bool:
    """
    Determine if gentler rate limits should be applied
    Used for OAuth discovery endpoints that MCP clients need

    Args:
        request: FastAPI Request object

    Returns:
        True if gentler limits should be used
    """
    path = request.url.path

    # OAuth discovery endpoints need gentler limits
    oauth_discovery_paths = [
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
        "/.well-known/openid-configuration",
        "/oauth/authorize",
        "/oauth/token",
    ]

    for discovery_path in oauth_discovery_paths:
        if path.startswith(discovery_path):
            return True

    return False
