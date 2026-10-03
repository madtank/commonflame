"""
Redis-backed token deny-list for OAuth 2.1 token revocation.

Allows immediate revocation of access tokens and refresh tokens before expiry.
Uses Redis SET with TTL to automatically clean up expired tokens.

Key format: deny:token:{jti}
TTL: Set to token's remaining lifetime (exp - now)

Usage:
    # Revoke a token
    await add_to_deny_list(jti="abc123", expires_at=1234567890)

    # Check if token is revoked
    is_revoked = await is_token_denied(jti="abc123")
"""
from __future__ import annotations

import time
from typing import Optional
from .redis_client import redis_client


async def add_to_deny_list(jti: str, expires_at: int) -> bool:
    """
    Add a token JTI to the deny-list with TTL matching token expiry.

    Args:
        jti: JWT token ID (unique identifier from token claims)
        expires_at: Unix timestamp when token expires (from 'exp' claim)

    Returns:
        True if successfully added, False if Redis error
    """
    try:
        now = int(time.time())
        ttl_seconds = max(1, expires_at - now)  # Minimum 1 second TTL

        key = f"deny:token:{jti}"

        # Store token with TTL - value doesn't matter, presence is the signal
        await redis_client.setex(key, ttl_seconds, "revoked")

        return True
    except Exception as e:
        # Log error but don't crash - security degradation is acceptable
        # (tokens will still expire naturally)
        print(f"[WARN] Failed to add token to deny-list: {e}")
        return False


async def is_token_denied(jti: str) -> bool:
    """
    Check if a token JTI is in the deny-list (revoked).

    Args:
        jti: JWT token ID to check

    Returns:
        True if token is revoked, False if valid or Redis error
    """
    try:
        key = f"deny:token:{jti}"
        exists = await redis_client.exists(key)
        return bool(exists)
    except Exception as e:
        # On Redis failure, default to allowing (fail-open for availability)
        # Production should have monitoring alerts for Redis failures
        print(f"[WARN] Failed to check deny-list: {e}")
        return False


async def remove_from_deny_list(jti: str) -> bool:
    """
    Remove a token from the deny-list (un-revoke).

    Rarely used - mainly for testing or accidental revocations.

    Args:
        jti: JWT token ID to remove

    Returns:
        True if removed, False if not found or error
    """
    try:
        key = f"deny:token:{jti}"
        deleted = await redis_client.delete(key)
        return bool(deleted)
    except Exception as e:
        print(f"[WARN] Failed to remove token from deny-list: {e}")
        return False


async def get_deny_list_stats() -> dict:
    """
    Get statistics about the deny-list (for monitoring/debugging).

    Returns:
        Dictionary with:
        - total_denied: Number of currently denied tokens
        - redis_available: Whether Redis connection is working
    """
    try:
        # Count keys matching deny:token:* pattern
        # Note: KEYS command is O(N) but acceptable for monitoring
        # Production could use SCAN for large deny-lists
        pattern = "deny:token:*"
        keys = await redis_client.keys(pattern)

        return {
            "total_denied": len(keys),
            "redis_available": True
        }
    except Exception as e:
        return {
            "total_denied": 0,
            "redis_available": False,
            "error": str(e)
        }


async def bulk_deny_tokens(token_jtis: list[tuple[str, int]]) -> int:
    """
    Add multiple tokens to deny-list in a single operation.

    Useful for revoking all tokens for a user/agent at once.

    Args:
        token_jtis: List of (jti, expires_at) tuples

    Returns:
        Number of tokens successfully added
    """
    success_count = 0

    try:
        # Use pipeline for atomic batch operation
        pipe = redis_client.pipeline()
        now = int(time.time())

        for jti, expires_at in token_jtis:
            ttl_seconds = max(1, expires_at - now)
            key = f"deny:token:{jti}"
            pipe.setex(key, ttl_seconds, "revoked")

        results = await pipe.execute()
        success_count = sum(1 for r in results if r)

    except Exception as e:
        print(f"[WARN] Failed to bulk deny tokens: {e}")

    return success_count
