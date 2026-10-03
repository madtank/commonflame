"""
GCP Secret Manager Integration
Loads secrets securely from GCP Secret Manager for production
"""
import os
import logging
from typing import Optional, Dict, Any
from functools import lru_cache
import json

logger = logging.getLogger(__name__)

# Cache secrets for performance (5 minutes)
@lru_cache(maxsize=128)
def get_secret(secret_id: str, project_id: Optional[str] = None) -> str:
    """
    Retrieve a secret from GCP Secret Manager

    Args:
        secret_id: The secret ID in Secret Manager
        project_id: GCP project ID (defaults to environment)

    Returns:
        The secret value as a string
    """
    try:
        from google.cloud import secretmanager

        # Get project ID from environment if not provided
        if not project_id:
            project_id = os.getenv("GCP_PROJECT_ID", "pax-platform-prod")

        # Create the Secret Manager client
        client = secretmanager.SecretManagerServiceClient()

        # Build the resource name
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"

        # Access the secret version
        response = client.access_secret_version(request={"name": name})

        # Return the decoded payload
        return response.payload.data.decode("UTF-8")

    except ImportError:
        logger.warning("Google Cloud Secret Manager not installed, using environment variables")
        # Fallback to environment variables for local development
        return os.getenv(secret_id.upper().replace("-", "_"), "")
    except Exception as e:
        logger.error(f"Failed to retrieve secret {secret_id}: {e}")
        # Fallback to environment variable
        return os.getenv(secret_id.upper().replace("-", "_"), "")

def load_secrets_to_env():
    """
    Load all production secrets into environment variables
    Called once at application startup
    """
    if os.getenv("ENVIRONMENT") != "production":
        logger.info("Not in production, skipping GCP Secret Manager")
        return

    logger.info("Loading secrets from GCP Secret Manager...")

    # Map of environment variable to secret ID
    secret_mappings = {
        "DATABASE_URL": "db-connection-string-prod",
        "DB_PASSWORD": "db-password-prod",
        "REDIS_PASSWORD": "redis-password-prod",
        "JWT_SECRET_KEY": "jwt-secret-key-prod",
        "OAUTH_CLIENT_SECRET": "oauth-client-secret-prod",
        "GITHUB_CLIENT_ID": "github-client-id-prod",
        "GITHUB_CLIENT_SECRET": "github-client-secret-prod",
        "PORTKEY_API_KEY": "portkey-api-key-prod",
        "OPENAI_API_KEY": "openai-api-key-prod",
        "ENCRYPTION_KEY": "encryption-key-prod",
    }

    for env_var, secret_id in secret_mappings.items():
        if not os.getenv(env_var):  # Only load if not already set
            try:
                secret_value = get_secret(secret_id)
                if secret_value:
                    os.environ[env_var] = secret_value
                    logger.info(f"✅ Loaded {env_var} from Secret Manager")
                else:
                    logger.warning(f"⚠️  Secret {secret_id} is empty")
            except Exception as e:
                logger.error(f"❌ Failed to load {env_var}: {e}")

    # Special handling for database URL with password substitution
    if os.getenv("DATABASE_URL") and os.getenv("DB_PASSWORD"):
        db_url = os.getenv("DATABASE_URL")
        db_password = os.getenv("DB_PASSWORD")
        if "SECRET" in db_url:
            os.environ["DATABASE_URL"] = db_url.replace("SECRET", db_password)
            logger.info("✅ Injected database password into connection string")

    # Build Redis URL with password
    if os.getenv("REDIS_PASSWORD"):
        redis_host = os.getenv("REDIS_HOST", "redis-memorystore-endpoint")
        redis_port = os.getenv("REDIS_PORT", "6379")
        redis_password = os.getenv("REDIS_PASSWORD")
        os.environ["REDIS_URL"] = f"redis://default:{redis_password}@{redis_host}:{redis_port}"
        logger.info("✅ Built Redis URL with password")

    logger.info("Secret loading complete")

def get_secret_config() -> Dict[str, Any]:
    """
    Get all configuration from secrets as a dictionary
    Useful for passing to application constructors
    """
    config = {
        "database": {
            "url": os.getenv("DATABASE_URL"),
            "pool_size": int(os.getenv("DB_POOL_SIZE", "20")),
            "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
        },
        "redis": {
            "url": os.getenv("REDIS_URL"),
            "max_connections": int(os.getenv("REDIS_MAX_CONNECTIONS", "50")),
        },
        "jwt": {
            "secret_key": os.getenv("JWT_SECRET_KEY"),
            "algorithm": os.getenv("JWT_ALGORITHM", "RS256"),
            "access_token_expire_minutes": int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60")),
            "refresh_token_expire_days": int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7")),
        },
        "oauth": {
            "client_secret": os.getenv("OAUTH_CLIENT_SECRET"),
            "require_pkce": os.getenv("OAUTH_REQUIRE_PKCE", "true").lower() == "true",
            "require_agent_name": os.getenv("OAUTH_REQUIRE_AGENT_NAME", "false").lower() == "true",
        },
        "github": {
            "client_id": os.getenv("GITHUB_CLIENT_ID"),
            "client_secret": os.getenv("GITHUB_CLIENT_SECRET"),
        },
        "api_keys": {
            "portkey": os.getenv("PORTKEY_API_KEY"),
            "openai": os.getenv("OPENAI_API_KEY"),
        },
        "environment": {
            "name": os.getenv("ENVIRONMENT", "development"),
            "debug": os.getenv("DEBUG", "false").lower() == "true",
            "frontend_url": os.getenv("FRONTEND_URL", "https://paxai.app"),
            "api_url": os.getenv("API_URL", "https://paxai.app"),
        }
    }

    return config

def validate_secrets() -> bool:
    """
    Validate that all required secrets are loaded
    Returns True if all critical secrets are present
    """
    required_secrets = [
        "DATABASE_URL",
        "JWT_SECRET_KEY",
        "REDIS_URL",
    ]

    missing = []
    for secret in required_secrets:
        if not os.getenv(secret):
            missing.append(secret)

    if missing:
        logger.error(f"Missing required secrets: {', '.join(missing)}")
        return False

    logger.info("All required secrets validated successfully")
    return True

# Initialize secrets on module import for production
if os.getenv("ENVIRONMENT") == "production":
    try:
        load_secrets_to_env()
        if not validate_secrets():
            logger.error("Secret validation failed - some features may not work")
    except Exception as e:
        logger.error(f"Failed to initialize secrets: {e}")
        # Don't crash the app, but log the error
