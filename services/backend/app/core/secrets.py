"""
GCP Secret Manager integration for production secrets
With caching and rotation support
"""
import os
import logging
from typing import Optional
from datetime import datetime, timedelta
from urllib.parse import quote

logger = logging.getLogger(__name__)
_legacy_jwt_enabled = os.getenv("ENABLE_LEGACY_JWT", "false").lower() == "true"
_DB_COMPONENT_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USERNAME", "DB_PASSWORD")


class SecretCache:
    """Simple in-memory cache for secrets with TTL"""
    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._cache = {}

    def get(self, key: str) -> Optional[str]:
        if key in self._cache:
            value, expires = self._cache[key]
            if datetime.utcnow() < expires:
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value: str):
        expires = datetime.utcnow() + timedelta(seconds=self.ttl_seconds)
        self._cache[key] = (value, expires)

    def invalidate(self, key: str = None):
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()


# Global cache instance (5 minute TTL)
_secret_cache = SecretCache(ttl_seconds=300)


def get_secret(secret_name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Retrieve a secret from GCP Secret Manager or environment variable
    With caching to reduce API calls

    Args:
        secret_name: Name of the secret in GCP Secret Manager
        default: Default value if secret not found

    Returns:
        Secret value or default
    """
    # First check environment variable (for local dev)
    env_var_name = secret_name.upper().replace("-", "_")
    env_value = os.getenv(env_var_name)
    if env_value:
        return env_value

    # Check if we should use Secret Manager
    use_secret_manager = os.getenv("USE_SECRET_MANAGER", "false").lower() == "true"
    if not use_secret_manager:
        return default

    # Check cache
    cached_value = _secret_cache.get(secret_name)
    if cached_value:
        logger.debug(f"Using cached secret: {secret_name}")
        return cached_value

    try:
        from google.cloud import secretmanager

        project_id = os.getenv("GCP_PROJECT_ID", "jax-platform-prod")
        client = secretmanager.SecretManagerServiceClient()

        # Use version alias for better rotation support
        # 'latest' always points to newest version
        secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"

        # Access the secret
        response = client.access_secret_version(request={"name": secret_path})
        secret_value = response.payload.data.decode("UTF-8")

        # Cache the value
        _secret_cache.set(secret_name, secret_value)

        logger.info(f"Successfully retrieved secret: {secret_name}")
        return secret_value

    except ImportError:
        logger.warning("google-cloud-secret-manager not installed, using environment variables")
        return default
    except Exception as e:
        logger.error(f"Error retrieving secret {secret_name}: {e}")
        return default


def invalidate_secret_cache(secret_name: str = None):
    """
    Invalidate cached secrets (useful after rotation)

    Args:
        secret_name: Specific secret to invalidate, or None for all
    """
    _secret_cache.invalidate(secret_name)
    logger.info(f"Invalidated secret cache for: {secret_name or 'all'}")


def _build_database_url_from_components(async_driver: bool = True) -> Optional[str]:
    """Compose a Postgres URL from ECS-style split DB_* environment variables."""
    if any(not os.getenv(key) for key in _DB_COMPONENT_KEYS):
        return None

    scheme = "postgresql+asyncpg" if async_driver else "postgresql"
    username = quote(os.environ["DB_USERNAME"], safe="")
    password = quote(os.environ["DB_PASSWORD"], safe="")
    host = os.environ["DB_HOST"]
    port = os.environ["DB_PORT"]
    database = os.environ["DB_NAME"]
    ssl_query = "ssl=require" if async_driver else "sslmode=require"
    return f"{scheme}://{username}:{password}@{host}:{port}/{database}?{ssl_query}"


def get_database_url() -> str:
    """Get async database URL from Secret Manager, environment, or split DB_* vars."""
    env_value = os.getenv("DATABASE_URL")
    if env_value:
        return env_value

    secret_value = get_secret("database-url")
    if secret_value:
        return secret_value

    return _build_database_url_from_components(async_driver=True) or ""


def get_mcp_database_url() -> str:
    """Get sync database URL for Alembic from environment or the async DB contract."""
    env_value = os.getenv("MCP_DATABASE_URL")
    if env_value:
        return env_value

    split_env_value = _build_database_url_from_components(async_driver=False)
    if split_env_value:
        return split_env_value

    database_url = get_database_url()
    if not database_url:
        return ""

    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace(
            "postgresql+asyncpg://",
            "postgresql://",
            1,
        ).replace("ssl=require", "sslmode=require")
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql://", 1)
    return database_url


def get_jwt_secret() -> str:
    """Get legacy JWT secret from Secret Manager or environment"""
    default_value = os.getenv("JWT_SECRET_KEY", "")
    if not default_value:
        from pathlib import Path
        secret_file = Path(os.getenv("SESSION_SECRET_FILE", "/run/keys/session.secret"))
        if secret_file.exists():
            default_value = secret_file.read_text().strip()
        elif os.getenv("ENVIRONMENT") == "test":
            default_value = "test-only-session-secret"
        else:
            raise RuntimeError("Persistent session key missing; run the container entrypoint")

    if not _legacy_jwt_enabled:
        # Legacy JWT support disabled; avoid fetching managed secret unnecessarily
        return default_value

    return get_secret("jwt-secret-key", default_value)


def get_redis_password() -> Optional[str]:
    """Get Redis password from Secret Manager or environment"""
    return get_secret("redis-password", os.getenv("REDIS_PASSWORD"))


def get_github_client_secret() -> str:
    """Get GitHub client secret from Secret Manager or environment"""
    return get_secret(
        "github-client-secret",
        os.getenv("GITHUB_CLIENT_SECRET", "")
    )


def get_session_secret() -> str:
    """Get session secret from Secret Manager or environment"""
    return get_secret(
        "session-secret-key",
        os.getenv("SESSION_SECRET_KEY", "default-session-secret")
    )
