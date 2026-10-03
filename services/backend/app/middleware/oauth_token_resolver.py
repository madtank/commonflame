"""
OAuth Token Resolver Middleware

Resolves user and agent information from OAuth tokens.
Handles both current JWT implementation and future opaque token migration.
"""

import os
import json
import base64
import hashlib
import logging
import time
from typing import Dict, Optional, Tuple
from starlette.requests import Request
import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Redis connection for token lookups
REDIS_CLIENT = None

async def get_redis_client():
    """Get or create Redis client for token lookups"""
    global REDIS_CLIENT
    if not REDIS_CLIENT:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        REDIS_CLIENT = await redis.from_url(redis_url)
    return REDIS_CLIENT


async def resolve_token_identity(request: Request) -> Dict[str, Optional[str]]:
    """
    Resolve user and agent identity from OAuth token.

    Returns dict with:
    - user_id: The authenticated user
    - agent_name: The agent name (from header or token)
    - token_hash: Hash of the token for rate limiting
    - method: How we identified them (jwt/redis/header)
    """

    identity = {
        "user_id": None,
        "agent_name": None,
        "token_hash": None,
        "method": "none"
    }

    # Extract token from Authorization header
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return identity

    token = auth_header[7:]
    identity["token_hash"] = hashlib.md5(token.encode()).hexdigest()[:8]

    # STEP 1: Try to get agent_name from header (highest priority)
    # This allows clients to specify which agent they're acting as
    agent_name = (
        request.headers.get("x-agent-name") or
        request.headers.get("X-Agent-Name") or
        request.headers.get("agent-name")
    )
    if agent_name:
        identity["agent_name"] = agent_name
        logger.debug(f"Got agent_name from header: {agent_name}")

    # STEP 2: Resolve user_id from token

    # CURRENT IMPLEMENTATION: Try to decode as JWT
    # TODO: Remove this when we migrate to opaque tokens
    if token.count('.') == 2:  # Looks like a JWT
        try:
            parts = token.split('.')
            payload = parts[1]
            # Add padding if needed
            padding = 4 - (len(payload) % 4)
            if padding != 4:
                payload += '=' * padding

            decoded = base64.urlsafe_b64decode(payload)
            claims = json.loads(decoded)

            # Extract user_id from JWT
            identity["user_id"] = claims.get("sub") or claims.get("user_id")

            # If no agent_name from header, try to get from JWT
            if not identity["agent_name"]:
                identity["agent_name"] = claims.get("agent_name")

            identity["method"] = "jwt"
            logger.debug(f"Resolved from JWT: user={identity['user_id']}, agent={identity['agent_name']}")
            return identity

        except Exception as e:
            logger.debug(f"JWT decode failed (might be opaque token): {e}")

    # FUTURE IMPLEMENTATION: Look up opaque token in Redis
    # This is how OAuth 2.1 should work with opaque tokens
    try:
        redis_client = await get_redis_client()

        # Look up token session in Redis
        # Key format: oauth:access:{token} -> JSON with user_id, agent_name, etc.
        # Also check oauth:agent_hint:{token} for the real agent name
        session_key = f"oauth:access:{token}"
        session_data = await redis_client.get(session_key)

        if session_data:
            session = json.loads(session_data)
            identity["user_id"] = session.get("user_id")

            # If no agent_name from header, resolve from Redis
            if not identity["agent_name"]:
                # Priority: agent_hint (real agent) > agent_name from session
                agent_hint = await redis_client.get(f"oauth:agent_hint:{token}")
                if agent_hint:
                    identity["agent_name"] = agent_hint
                else:
                    session_agent = session.get("agent_name")
                    # Skip placeholder names
                    if session_agent and session_agent not in ["oauth_user", "anonymous_agent"]:
                        identity["agent_name"] = session_agent

            identity["method"] = "redis"
            logger.debug(f"Resolved from Redis: user={identity['user_id']}, agent={identity['agent_name']}")
            return identity

    except Exception as e:
        logger.debug(f"Redis lookup failed: {e}")

    # If we couldn't resolve the token, log it
    logger.warning(f"Could not resolve token identity for {identity['token_hash']}")
    return identity


def create_rate_limit_key(identity: Dict[str, Optional[str]], client_ip: str) -> str:
    """
    Create a rate limit key from resolved identity.

    Priority order:
    1. agent:{name}:user:{id} - Best, unique per agent+user
    2. user:{id}:token:{hash} - Good, unique per user+token
    3. agent:{name}:token:{hash} - OK, unique per agent+token
    4. token:{hash} - Fallback, unique per token
    5. ip:{address} - Last resort
    """

    agent_name = identity.get("agent_name")
    user_id = identity.get("user_id")
    token_hash = identity.get("token_hash")

    # Best case: both agent and user
    if agent_name and user_id:
        return f"agent:{agent_name}:user:{user_id[:8]}"

    # Good: user with token
    if user_id and token_hash:
        return f"user:{user_id[:8]}:token:{token_hash}"

    # OK: agent with token
    if agent_name and token_hash:
        return f"agent:{agent_name}:token:{token_hash}"

    # Fallback: just token
    if token_hash:
        return f"token:{token_hash}"

    # Last resort: IP
    return f"ip:{client_ip}"


# Example of how to store OAuth sessions in Redis (for future migration)
async def store_oauth_session(token: str, user_id: str, agent_name: Optional[str] = None, expires_in: int = 900):
    """
    Store OAuth token session in Redis.
    This is how we SHOULD handle OAuth 2.1 opaque tokens.

    Args:
        token: The opaque access token
        user_id: The authenticated user
        agent_name: Optional agent name
        expires_in: Token lifetime in seconds (default 15 minutes)
    """
    redis_client = await get_redis_client()

    session_data = {
        "user_id": user_id,
        "agent_name": agent_name,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + expires_in
    }

    # Store with TTL matching token expiration
    session_key = f"oauth:token:{token}"
    await redis_client.setex(
        session_key,
        expires_in,
        json.dumps(session_data)
    )

    logger.info(f"Stored OAuth session for user {user_id}, agent {agent_name}")


# Example of proper OAuth token generation (for future migration)
def generate_opaque_token() -> str:
    """
    Generate a proper OAuth 2.1 opaque access token.
    This is what we SHOULD use instead of JWTs.
    """
    import secrets
    # Generate a cryptographically secure random token
    # 32 bytes = 256 bits of entropy
    return secrets.token_urlsafe(32)
