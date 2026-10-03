"""
MCP Auth Token Caching

Provides cached token minting for cloud agents to reduce Redis churn.
All tokens are RS256-signed JWTs verified by the MCP server via JWKS.

Note: The cache TTL is set to the JWT TTL (not sliding) because the JWT's
exp claim is immutable. Extending Redis TTL past JWT exp causes the MCP
server to reject expired tokens.
"""

import json
import logging
import secrets as secrets_module
import time

from .redis_client import redis_client

logger = logging.getLogger(__name__)

# TTL for cached tokens — matches JWT ttl_seconds
MCP_TOKEN_CACHE_TTL = 1800  # 30 minutes

# Re-mint when less than this many seconds remain on the JWT
_REFRESH_MARGIN_SECONDS = 120  # 2 minutes


def _jwt_is_near_expiry(access_token: str) -> bool:
    """Check if a JWT is expired or about to expire (within margin)."""
    try:
        import base64
        # Decode JWT payload without verifying (we just need exp)
        parts = access_token.split(".")
        if len(parts) != 3:
            return True
        # Add padding
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp", 0)
        return time.time() >= (exp - _REFRESH_MARGIN_SECONDS)
    except Exception:
        return True


async def get_or_mint_mcp_token(
    agent_id: str,
    agent_name: str,
    user_id: str,
    space_id: str,
) -> dict | None:
    """
    Get cached MCP auth token or mint a new one.

    Uses RS256-signed JWTs (same as Space Agent) verified by the MCP server
    via the backend's JWKS endpoint. Cached in Redis with fixed TTL that
    matches the JWT expiry.

    Cache key: mcp_token_cache:{agent_id}:{space_id}

    Returns:
        {"access_token": "<signed_jwt>", "expires_in": 1800} or None if minting fails
    """
    cache_key = f"mcp_token_cache:{agent_id}:{space_id}"

    try:
        # Check for existing cached JWT
        cached = await redis_client.get(cache_key)
        if cached:
            token_data = json.loads(cached) if isinstance(cached, str) else json.loads(cached.decode())
            access_token = token_data.get("access_token")

            if access_token and not access_token.startswith("axat_"):
                # Verify the JWT hasn't expired (don't trust Redis TTL alone)
                if not _jwt_is_near_expiry(access_token):
                    logger.info(f"Reusing cached JWT mcp_auth for {agent_name}")
                    return {"access_token": access_token, "expires_in": MCP_TOKEN_CACHE_TTL}
                else:
                    logger.info(f"Cached JWT for {agent_name} near expiry, re-minting")
                    await redis_client.delete(cache_key)
            else:
                # Stale opaque token — flush and re-mint as JWT
                await redis_client.delete(cache_key)

        # Mint RS256-signed JWT (same path as Space Agent)
        from .ax_jwt import mint_ax_jwt

        tools_allowed = ["messages", "tasks", "context", "search", "whoami", "agents", "spaces"]

        access_token = mint_ax_jwt(
            agent_id=agent_id,
            agent_name=agent_name,
            space_id=space_id,
            tools_allowed=tools_allowed,
            ttl_seconds=MCP_TOKEN_CACHE_TTL,
        )

        # Cache the JWT for reuse
        await redis_client.setex(
            cache_key,
            MCP_TOKEN_CACHE_TTL,
            json.dumps({"access_token": access_token})
        )

        logger.info(f"Minted RS256 JWT mcp_auth for {agent_name} (TTL={MCP_TOKEN_CACHE_TTL}s)")
        return {"access_token": access_token, "expires_in": MCP_TOKEN_CACHE_TTL}

    except Exception as e:
        logger.error(f"Failed to get/mint mcp_auth for {agent_name}: {e}")
        return None


# Space Agent token TTL - 60 minutes (longer-lived for session continuity)
SPACE_AGENT_TOKEN_TTL = 3600  # 60 minutes


async def mint_space_agent_token(
    agent_id: str,
    agent_name: str,
    space_id: str,
    user_id: str,
    enabled_tools: list[str] | None = None,
    extra_claims: dict | None = None,
) -> dict | None:
    """
    Mint an RS256-signed JWT for Space Agent (aX) to authenticate with MCP server.

    Uses asymmetric signing — backend holds private key, MCP server verifies
    via JWKS endpoint at /.well-known/jwks.json. No Redis dependency for auth.

    Spec: specs/AX-MCP-AUTH-001/spec.md

    Returns:
        {"access_token": "<signed_jwt>", "token_type": "Bearer", "expires_in": 900}
        or None if minting fails
    """
    tools_allowed = enabled_tools or [
        "messages", "tasks", "context", "search", "whoami", "agents",
    ]

    try:
        from .ax_jwt import mint_ax_jwt

        token = mint_ax_jwt(
            agent_id=agent_id,
            agent_name=agent_name,
            space_id=space_id,
            tools_allowed=tools_allowed,
            ttl_seconds=SPACE_AGENT_TOKEN_TTL,
            extra_claims=extra_claims,
        )

        logger.info(
            f"Minted signed JWT for {agent_name} "
            f"(space_id={space_id}, TTL={SPACE_AGENT_TOKEN_TTL}s)"
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": SPACE_AGENT_TOKEN_TTL,
        }

    except Exception as e:
        logger.error(f"Failed to mint space_agent JWT for {agent_name}: {e}")
        return None


# Enclave token TTL - 15 minutes (short-lived, rotate fast, fail fast)
# External agents must call whoami(action='refresh_token') before expiry
ENCLAVE_TOKEN_TTL = 900  # 15 minutes - security first


async def mint_enclave_token(
    agent_id: str,
    agent_name: str,
    owner_id: str,
    dispatch_id: str,
    pinned_to_space: str | None = None,
) -> dict | None:
    """
    Mint an enclave-scoped token for external agents (webhook dispatch).

    Enclave tokens have restricted permissions compared to cloud agent tokens:
    - messages: send/read messages
    - tasks: read/update tasks
    - context: read/write shared context
    - whoami: identity and token refresh

    Scoping:
    - Scoped to owner (user) + agent
    - Non-pinned agents can access any space the owner has access to
    - Pinned agents are restricted to their pinned space

    Returns:
        {"access_token": "axat_...", "expires_in": 1800} or None if minting fails
    """
    try:
        access_token = f"axat_{secrets_module.token_urlsafe(32)}"

        # Token metadata for MCP server validation
        # Enclave scope is more restrictive than full mcp:read mcp:write
        token_meta = {
            "agent_name": agent_name,
            "owner_id": owner_id,  # Owner instead of user_id for clarity
            "agent_id": agent_id,
            "dispatch_id": dispatch_id,  # For audit trail
            "client_id": f"external-agent-{agent_name}",
            "scope": "enclave:messages enclave:tasks enclave:context enclave:whoami",
            "token_source": "external_dispatch",
            "token_type": "enclave",  # Distinguishes from cloud agent tokens
        }

        # If agent is pinned, restrict to that space only
        if pinned_to_space:
            token_meta["pinned_to_space"] = pinned_to_space
            token_meta["scope_restriction"] = "pinned_space_only"

        # Store in oauth:access for MCP server validation
        await redis_client.setex(
            f"oauth:access:{access_token}",
            ENCLAVE_TOKEN_TTL,
            json.dumps(token_meta)
        )

        logger.info(
            f"🔐 Minted enclave token for external agent {agent_name} "
            f"(dispatch_id={dispatch_id}, TTL={ENCLAVE_TOKEN_TTL}s)"
        )
        return {"access_token": access_token, "expires_in": ENCLAVE_TOKEN_TTL}

    except Exception as e:
        logger.error(f"❌ Failed to mint enclave token for {agent_name}: {e}")
        return None


async def refresh_enclave_token(current_token: str) -> dict | None:
    """
    Refresh an enclave token by minting a new one with same scope.

    Used by external agents via whoami(action='refresh_token').
    Returns a new token with fresh 30-min TTL.
    """
    try:
        # Get current token metadata
        oauth_key = f"oauth:access:{current_token}"
        cached = await redis_client.get(oauth_key)

        if not cached:
            logger.warning("Cannot refresh - token not found or expired")
            return None

        token_meta = json.loads(cached) if isinstance(cached, str) else json.loads(cached.decode())

        # Only allow refresh for enclave tokens
        if token_meta.get("token_type") != "enclave":
            logger.warning("Cannot refresh - not an enclave token")
            return None

        # Mint new token with same scope
        return await mint_enclave_token(
            agent_id=token_meta["agent_id"],
            agent_name=token_meta["agent_name"],
            owner_id=token_meta["owner_id"],
            dispatch_id=token_meta["dispatch_id"],
            pinned_to_space=token_meta.get("pinned_to_space") or token_meta.get("pinned_to_org"),
        )

    except Exception as e:
        logger.error(f"❌ Failed to refresh enclave token: {e}")
        return None


async def validate_token(access_token: str) -> dict | None:
    """
    Validate any MCP token (cloud agent or enclave) and return its metadata.

    Works for ALL token types - cloud agents, external agents, any future type.
    This is the unified validation for the progress endpoint.

    Returns token metadata dict or None if invalid/expired.
    """
    try:
        if not access_token or not access_token.startswith("axat_"):
            return None

        # Look up token in Redis
        cached = await redis_client.get(f"oauth:access:{access_token}")
        if not cached:
            return None

        token_meta = json.loads(cached) if isinstance(cached, str) else json.loads(cached.decode())
        return token_meta

    except Exception as e:
        logger.error(f"❌ Token validation failed: {e}")
        return None


# ─── DELEGATE-001: User-Delegated Token for Home Space ───

async def mint_delegated_token(
    agent_id: str,
    agent_name: str,
    user_id: str,
    space_id: str,
    db,
    extra_claims: dict | None = None,
) -> dict | None:
    """Mint a user-delegated JWT for aX in the user's home space.

    Only mints if ALL conditions are true:
    1. Space is owned by the user (created_by == user_id)
    2. Space is personal (is_personal or description starts with "Personal workspace for")
    3. Agent is the space agent for this space

    Returns short-lived (5 min) JWT with delegation claims, or None if conditions not met.
    NOT cached — fresh per dispatch for security.
    """
    from sqlalchemy import select
    from app.models.space import Space

    try:
        result = await db.execute(select(Space).where(Space.id == space_id))
        space = result.scalar_one_or_none()
        if not space:
            logger.warning(f"DELEGATE_MINT_REJECTED space_not_found space_id={space_id}")
            return None

        # Verify ownership
        if str(space.created_by) != str(user_id):
            logger.warning(f"DELEGATE_MINT_REJECTED not_owner space_id={space_id} user_id={user_id} created_by={space.created_by}")
            return None

        # Verify personal space
        is_personal = getattr(space, "is_personal", False) or (
            space.description and space.description.startswith("Personal workspace for")
        )
        if not is_personal:
            logger.warning(f"DELEGATE_MINT_REJECTED not_personal space_id={space_id}")
            return None

        # Verify agent is the designated space agent — require it to be set
        if not space.space_agent_id:
            logger.warning(f"DELEGATE_MINT_REJECTED no_space_agent_configured space_id={space_id}")
            return None
        if str(space.space_agent_id) != str(agent_id):
            logger.warning(f"DELEGATE_MINT_REJECTED not_space_agent agent_id={agent_id} space_agent_id={space.space_agent_id}")
            return None

        # Mint delegated JWT — short TTL, no caching
        from .ax_jwt import mint_ax_jwt

        tools_allowed = ["messages", "tasks", "context", "search", "whoami", "agents", "spaces"]

        delegated_claims = {
            "delegation_mode": "home_space",
            "delegated_for": str(user_id),
            "delegated_space_owner": True,
        }
        if extra_claims:
            delegated_claims.update(extra_claims)

        token = mint_ax_jwt(
            agent_id=agent_id,
            agent_name=agent_name,
            space_id=str(space_id),
            tools_allowed=tools_allowed,
            ttl_seconds=300,  # 5 minutes — short for security
            extra_claims=delegated_claims,
        )

        logger.info(
            f"DELEGATE_MINT_OK agent={agent_name} user={user_id} "
            f"space={space_id} ttl=300s"
        )
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

    except Exception as e:
        logger.error(f"DELEGATE_MINT_ERROR agent={agent_name} user={user_id}: {e}")
        return None
