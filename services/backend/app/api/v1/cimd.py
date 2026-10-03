"""
Client ID Metadata Documents (CIMD) - MCP 2025-11-25 Spec

Implements client registration via metadata documents hosted at client-controlled URLs.
This replaces DCR as the preferred registration method for http-streamable transport.

Spec: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
Reference: https://client.dev/
"""

import json
import logging
import hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from urllib.parse import urlparse
import httpx
import redis.asyncio as redis
import os

logger = logging.getLogger(__name__)

# Redis client for caching
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_redis_client: Optional[redis.Redis] = None

# Cache TTL for client metadata (1 hour default)
CIMD_CACHE_TTL = int(os.getenv("CIMD_CACHE_TTL_SECONDS", "3600"))

# Required fields in CIMD metadata
REQUIRED_FIELDS = ["client_id", "redirect_uris"]
OPTIONAL_FIELDS = ["client_name", "client_uri", "logo_uri", "contacts", "tos_uri", "policy_uri"]


async def get_redis() -> redis.Redis:
    """Get or create Redis client for CIMD caching"""
    global _redis_client
    if not _redis_client:
        _redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def is_cimd_client_id(client_id: str) -> bool:
    """
    Check if client_id is a URL (CIMD) vs traditional ID.

    CIMD client_ids are HTTPS URLs pointing to metadata documents.
    Traditional client_ids are opaque strings.
    """
    if not client_id:
        return False
    try:
        parsed = urlparse(client_id)
        return parsed.scheme == "https" and parsed.netloc != ""
    except Exception:
        return False


def _cache_key(client_id: str) -> str:
    """Generate Redis cache key for client metadata"""
    # Hash long URLs to avoid key length issues
    url_hash = hashlib.sha256(client_id.encode()).hexdigest()[:16]
    return f"cimd:metadata:{url_hash}"


async def fetch_client_metadata(client_id: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """
    Fetch and validate client metadata from CIMD URL.

    Args:
        client_id: HTTPS URL pointing to client metadata JSON
        force_refresh: If True, bypass cache and fetch fresh

    Returns:
        Validated metadata dict, or None if invalid/unreachable
    """
    if not is_cimd_client_id(client_id):
        logger.warning(f"Not a CIMD client_id: {client_id}")
        return None

    redis_client = await get_redis()
    cache_key = _cache_key(client_id)

    # Check cache first (unless force refresh)
    if not force_refresh:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                logger.debug(f"CIMD cache hit for {client_id[:50]}...")
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"CIMD cache read error: {e}")

    # Fetch metadata from URL
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            logger.info(f"🔍 CIMD: Fetching metadata from {client_id}")
            response = await http_client.get(
                client_id,
                headers={"Accept": "application/json"},
                follow_redirects=True
            )
            response.raise_for_status()
            metadata = response.json()

    except httpx.TimeoutException:
        logger.error(f"CIMD fetch timeout: {client_id}")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"CIMD fetch HTTP error {e.response.status_code}: {client_id}")
        return None
    except json.JSONDecodeError:
        logger.error(f"CIMD invalid JSON: {client_id}")
        return None
    except Exception as e:
        logger.error(f"CIMD fetch error: {e}")
        return None

    # Validate metadata
    validation_error = validate_metadata(client_id, metadata)
    if validation_error:
        logger.error(f"CIMD validation failed: {validation_error}")
        return None

    # Cache valid metadata
    try:
        await redis_client.setex(cache_key, CIMD_CACHE_TTL, json.dumps(metadata))
        logger.info(f"✅ CIMD: Cached metadata for {metadata.get('client_name', client_id[:30])}")
    except Exception as e:
        logger.warning(f"CIMD cache write error: {e}")

    return metadata


def validate_metadata(source_url: str, metadata: Dict[str, Any]) -> Optional[str]:
    """
    Validate CIMD metadata document.

    Security checks:
    1. client_id must match the source URL (prevents impersonation)
    2. Required fields must be present
    3. redirect_uris must be valid URLs

    Returns:
        Error message if invalid, None if valid
    """
    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in metadata:
            return f"Missing required field: {field}"

    # CRITICAL: client_id must match source URL
    if metadata.get("client_id") != source_url:
        return f"client_id mismatch: document says '{metadata.get('client_id')}' but fetched from '{source_url}'"

    # Validate redirect_uris
    redirect_uris = metadata.get("redirect_uris", [])
    if not isinstance(redirect_uris, list) or len(redirect_uris) == 0:
        return "redirect_uris must be a non-empty array"

    for uri in redirect_uris:
        if not isinstance(uri, str):
            return f"Invalid redirect_uri type: {type(uri)}"
        try:
            parsed = urlparse(uri)
            # Allow http://localhost for development
            if parsed.scheme not in ["https", "http"]:
                return f"Invalid redirect_uri scheme: {uri}"
            if parsed.scheme == "http" and parsed.netloc not in ["localhost", "127.0.0.1"]:
                return f"HTTP only allowed for localhost: {uri}"
        except Exception:
            return f"Invalid redirect_uri: {uri}"

    # Optional: validate client_uri matches client_id domain
    client_uri = metadata.get("client_uri")
    if client_uri:
        try:
            source_domain = urlparse(source_url).netloc
            client_domain = urlparse(client_uri).netloc
            # Allow subdomains but warn on complete mismatch
            if not (client_domain == source_domain or
                    client_domain.endswith("." + source_domain) or
                    source_domain.endswith("." + client_domain)):
                logger.warning(f"CIMD: client_uri domain '{client_domain}' differs from source '{source_domain}'")
        except Exception:
            pass

    return None


async def validate_redirect_uri(client_id: str, redirect_uri: str) -> bool:
    """
    Validate that a redirect_uri is allowed for this client.

    For CIMD clients, checks against the fetched metadata.
    For traditional clients, falls back to existing DCR validation.
    """
    if not is_cimd_client_id(client_id):
        # Not a CIMD client, use existing validation
        return True  # Let DCR handle it

    metadata = await fetch_client_metadata(client_id)
    if not metadata:
        logger.warning(f"Cannot validate redirect_uri - no metadata for {client_id}")
        return False

    allowed_uris = metadata.get("redirect_uris", [])

    # Exact match required (per OAuth 2.1)
    if redirect_uri in allowed_uris:
        return True

    # Normalize and try again (handle trailing slashes)
    normalized_redirect = redirect_uri.rstrip("/")
    for allowed in allowed_uris:
        if normalized_redirect == allowed.rstrip("/"):
            return True

    logger.warning(f"CIMD: redirect_uri '{redirect_uri}' not in allowed list: {allowed_uris}")
    return False


async def get_client_display_info(client_id: str) -> Dict[str, str]:
    """
    Get display information for consent screens.

    Returns:
        Dict with client_name, client_uri, logo_uri (if available)
    """
    if not is_cimd_client_id(client_id):
        return {"client_name": client_id, "client_uri": None, "logo_uri": None}

    metadata = await fetch_client_metadata(client_id)
    if not metadata:
        return {"client_name": client_id, "client_uri": None, "logo_uri": None}

    return {
        "client_name": metadata.get("client_name", client_id),
        "client_uri": metadata.get("client_uri"),
        "logo_uri": metadata.get("logo_uri")
    }


async def invalidate_cache(client_id: str) -> bool:
    """Invalidate cached metadata for a client"""
    if not is_cimd_client_id(client_id):
        return False

    try:
        redis_client = await get_redis()
        cache_key = _cache_key(client_id)
        await redis_client.delete(cache_key)
        logger.info(f"CIMD: Invalidated cache for {client_id[:50]}...")
        return True
    except Exception as e:
        logger.error(f"CIMD cache invalidation error: {e}")
        return False
