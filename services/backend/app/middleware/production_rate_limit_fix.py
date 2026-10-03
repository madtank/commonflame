"""
Production Rate Limiting Fix for Shared IP Networks

Problem: Multiple users on the same network (e.g., business partners in same office)
share the same external IP and get rate limited together.

Solution: Use hierarchical rate limiting based on authentication tokens and user IDs
instead of just IP addresses in production.
"""

import os
import hashlib
import json
import base64
from typing import Optional
from starlette.requests import Request


def get_production_client_identifier(request: Request) -> str:
    """
    Get a unique client identifier for production rate limiting.
    This ensures users on shared networks aren't incorrectly rate limited together.

    Priority:
    1. User ID + Agent Name (from token)
    2. Token hash (unique per session)
    3. IP address (fallback only)
    """
    # First try to get authenticated user info
    auth_header = request.headers.get('authorization', '')
    agent_name_hdr = request.headers.get('x-agent-name')
    client_inst_hdr = request.headers.get('x-client-instance')

    if auth_header.startswith('Bearer '):
        token = auth_header[7:]

        # For OAuth opaque tokens (axat_*, axrt_*)
        if token.startswith(('axat_', 'axrt_')):
            # Use token hash as unique identifier
            token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]

            # Try to get user info from Redis cache or token resolution
            # This would need to be integrated with oauth_token_resolver
            return f"oauth_token:{token_hash}"

        # For JWT tokens (legacy, being phased out)
        elif token.count('.') == 2:
            try:
                # Decode JWT payload
                parts = token.split('.')
                payload = parts[1]
                # Add padding if needed
                padding = 4 - (len(payload) % 4)
                if padding != 4:
                    payload += '=' * padding

                decoded = base64.urlsafe_b64decode(payload)
                claims = json.loads(decoded)

                # Build hierarchical identifier
                user_id = claims.get('sub') or claims.get('user_id')
                agent_name = claims.get('agent_name') or agent_name_hdr
                space_id = claims.get('space_id') or claims.get('org_id')
                client_inst = client_inst_hdr

                if user_id:
                    # Best case: user-specific rate limiting
                    parts = [f"user:{user_id[:8]}"]
                    if agent_name:
                        parts.append(f"agent:{agent_name}")
                    if client_inst:
                        parts.append(f"client:{client_inst[:8]}")
                    return ":".join(parts)
                elif space_id:
                    # Space-level rate limiting
                    return f"space:{space_id[:8]}"
            except Exception as e:
                # If JWT decode fails, fall back to token hash
                token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
                return f"token:{token_hash}"

    # Check for API key authentication
    api_key = request.headers.get('x-api-key')
    if api_key:
        # API keys get their own rate limit bucket
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        return f"api_key:{key_hash}"

    # Last resort: use IP address (but this is what we're trying to avoid)
    client_ip = request.headers.get('x-forwarded-for', '').split(',')[0].strip()
    if not client_ip:
        client_ip = request.headers.get('x-real-ip', '')
    if not client_ip and hasattr(request.client, 'host'):
        client_ip = request.client.host

    return f"ip:{client_ip}" if client_ip else "ip:unknown"


def should_apply_production_overrides(request: Request) -> bool:
    """
    Determine if production rate limit overrides should be applied.

    Returns True if:
    - Environment is production
    - Request has authentication (to enable user-specific limits)
    """
    # Only apply in production
    if os.getenv('ENVIRONMENT', '').lower() != 'production':
        return False

    # Check if request has authentication
    has_auth = bool(
        request.headers.get('authorization') or
        request.headers.get('x-api-key')
    )

    return has_auth


# Production-specific rate limit overrides
PRODUCTION_OVERRIDES = {
    # Increased for cloud agents: tool history adds ~3 calls/turn, agents make 10-15 calls/invocation
    '/mcp': {
        'requests': 400,
        'window': 60,
        'burst': 80
    },
    # OAuth endpoints - strict production limits for security (order matters: specific first)
    '/oauth/authorize': {
        'requests': 10,   # Strict: Authorization initiation
        'window': 60,
        'burst': 3
    },
    '/oauth/callback': {
        'requests': 10,   # Strict: GitHub callback handling
        'window': 60,
        'burst': 3
    },
    '/oauth': {
        'requests': 30,   # Fallback for other OAuth endpoints
        'window': 60,
        'burst': 10
    },
    '/api/v1/messages': {
        'requests': 200,  # Higher limit for message operations
        'window': 60,
        'burst': 50
    },
    '/api/v1/tasks': {
        'requests': 100,  # Task operations
        'window': 60,
        'burst': 20
    }
}


def get_hierarchical_rate_limit_key(request: Request) -> str:
    """
    Build a hierarchical rate limit key for production.

    Hierarchy:
    1. agent:name:user:id:client:instance - Most specific
    2. user:id:client:instance
    3. user:id
    4. org:id (fallback)
    5. ip:address (last resort)
    """
    # Get authentication headers
    auth_header = request.headers.get('authorization', '')
    agent_name = request.headers.get('x-agent-name')
    client_instance = request.headers.get('x-client-instance')

    # Try to extract user info from token
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]

        # Handle OAuth opaque tokens
        if token.startswith(('axat_', 'axrt_')):
            # Would need Redis lookup to get user info
            # For now, use token hash
            token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
            parts = [f"token:{token_hash}"]
            if agent_name:
                parts.insert(0, f"agent:{agent_name}")
            if client_instance:
                parts.append(f"client:{client_instance[:8]}")
            return ":".join(parts)

        # Handle JWT tokens
        elif token.count('.') == 2:
            try:
                parts = token.split('.')
                payload = parts[1]
                padding = 4 - (len(payload) % 4)
                if padding != 4:
                    payload += '=' * padding

                decoded = base64.urlsafe_b64decode(payload)
                claims = json.loads(decoded)

                user_id = claims.get('sub') or claims.get('user_id')
                if user_id:
                    key_parts = []
                    if agent_name:
                        key_parts.append(f"agent:{agent_name}")
                    key_parts.append(f"user:{user_id[:8]}")
                    if client_instance:
                        key_parts.append(f"client:{client_instance[:8]}")
                    return ":".join(key_parts)
            except:
                pass

    # Fallback to IP-based
    return get_production_client_identifier(request)
