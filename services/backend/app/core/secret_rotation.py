"""
GCP Secret Manager auto-rotation and management
Handles both manual and automatic rotation strategies
"""
import os
import logging
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from google.cloud import secretmanager
from google.cloud import scheduler_v1
import json

logger = logging.getLogger(__name__)


class SecretRotationManager:
    """
    Manages secret rotation for the aX platform
    Supports both manual rotation and GCP's automatic rotation
    """

    def __init__(self, project_id: str = None):
        self.project_id = project_id or os.getenv("GCP_PROJECT_ID", "jax-platform-prod")
        self.client = secretmanager.SecretManagerServiceClient()
        self.scheduler_client = scheduler_v1.CloudSchedulerClient()

    def create_rotatable_secret(
        self,
        secret_id: str,
        initial_value: str,
        rotation_period_days: int = 30,
        labels: Dict[str, str] = None,
        auto_generate: bool = False
    ) -> str:
        """
        Create a secret with rotation configuration

        Args:
            secret_id: Unique identifier for the secret
            initial_value: Initial secret value (ignored if auto_generate=True)
            rotation_period_days: Days between rotations (30, 60, 90)
            labels: Metadata labels for the secret
            auto_generate: Whether to auto-generate cryptographically secure values

        Returns:
            Secret resource name
        """
        parent = f"projects/{self.project_id}"

        # Generate secure value if requested
        if auto_generate:
            initial_value = self._generate_secure_secret(secret_id)

        # Configure rotation
        rotation = None
        if rotation_period_days:
            # Configure rotation schedule
            rotation = secretmanager.Rotation(
                rotation_period=timedelta(days=rotation_period_days),
                next_rotation_time=datetime.utcnow() + timedelta(days=rotation_period_days)
            )

        # Create the secret
        secret = secretmanager.Secret(
            replication=secretmanager.Replication(
                automatic=secretmanager.Replication.Automatic()
            ),
            labels=labels or {},
            rotation=rotation,
            version_aliases={
                "stable": 1,  # Stable version alias
                "latest": 1   # Latest version alias
            }
        )

        try:
            # Create the secret
            response = self.client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": secret
                }
            )

            # Add the initial version
            version_response = self.client.add_secret_version(
                request={
                    "parent": response.name,
                    "payload": {"data": initial_value.encode("UTF-8")}
                }
            )

            logger.info(f"Created rotatable secret: {secret_id} with {rotation_period_days}-day rotation")
            return response.name

        except Exception as e:
            logger.error(f"Failed to create secret {secret_id}: {e}")
            raise

    def setup_rotation_schedule(
        self,
        secret_id: str,
        rotation_function_url: str,
        schedule: str = "0 3 1 * *"  # Monthly at 3 AM on the 1st
    ):
        """
        Set up Cloud Scheduler job for secret rotation

        Args:
            secret_id: Secret to rotate
            rotation_function_url: Cloud Function URL that handles rotation
            schedule: Cron schedule for rotation
        """
        parent = f"projects/{self.project_id}/locations/us-central1"
        job_id = f"rotate-secret-{secret_id}"

        job = scheduler_v1.Job(
            name=f"{parent}/jobs/{job_id}",
            description=f"Rotate {secret_id} secret",
            schedule=schedule,
            time_zone="UTC",
            http_target=scheduler_v1.HttpTarget(
                uri=rotation_function_url,
                http_method=scheduler_v1.HttpMethod.POST,
                body=json.dumps({
                    "secret_id": secret_id,
                    "project_id": self.project_id
                }).encode(),
                headers={
                    "Content-Type": "application/json"
                },
                oidc_token=scheduler_v1.OidcToken(
                    service_account_email=f"secret-rotation@{self.project_id}.iam.gserviceaccount.com"
                )
            )
        )

        try:
            response = self.scheduler_client.create_job(
                request={
                    "parent": parent,
                    "job": job
                }
            )
            logger.info(f"Created rotation schedule for {secret_id}: {schedule}")
            return response
        except Exception as e:
            logger.error(f"Failed to create rotation schedule: {e}")
            raise

    def rotate_secret(self, secret_id: str, new_value: str = None) -> str:
        """
        Manually rotate a secret

        Args:
            secret_id: Secret to rotate
            new_value: New secret value (auto-generated if not provided)

        Returns:
            New version number
        """
        secret_name = f"projects/{self.project_id}/secrets/{secret_id}"

        # Generate new value if not provided
        if not new_value:
            new_value = self._generate_secure_secret(secret_id)

        try:
            # Add new version
            version = self.client.add_secret_version(
                request={
                    "parent": secret_name,
                    "payload": {"data": new_value.encode("UTF-8")}
                }
            )

            # Update aliases
            self._update_version_aliases(secret_id, version.name)

            # Disable old versions (keep last 3 for rollback)
            self._cleanup_old_versions(secret_id, keep_last=3)

            logger.info(f"Rotated secret {secret_id} to version {version.name}")
            return version.name

        except Exception as e:
            logger.error(f"Failed to rotate secret {secret_id}: {e}")
            raise

    def _generate_secure_secret(self, secret_id: str) -> str:
        """Generate cryptographically secure secret based on type"""
        if "jwt" in secret_id.lower() or "token" in secret_id.lower():
            # 64-byte hex for JWT secrets
            return secrets.token_hex(64)
        elif "password" in secret_id.lower():
            # Strong password with special chars
            return secrets.token_urlsafe(32)
        elif "key" in secret_id.lower() or "secret" in secret_id.lower():
            # 32-byte hex for generic secrets
            return secrets.token_hex(32)
        else:
            # Default to URL-safe token
            return secrets.token_urlsafe(43)

    def _update_version_aliases(self, secret_id: str, new_version: str):
        """Update version aliases after rotation"""
        secret_name = f"projects/{self.project_id}/secrets/{secret_id}"

        # Extract version number from full name
        version_num = new_version.split("/")[-1]

        try:
            # Update the secret with new aliases
            self.client.update_secret(
                request={
                    "secret": {
                        "name": secret_name,
                        "version_aliases": {
                            "latest": int(version_num),
                            "stable": int(version_num) - 1 if int(version_num) > 1 else 1
                        }
                    },
                    "update_mask": {"paths": ["version_aliases"]}
                }
            )
        except Exception as e:
            logger.warning(f"Could not update aliases for {secret_id}: {e}")

    def _cleanup_old_versions(self, secret_id: str, keep_last: int = 3):
        """Disable old secret versions, keeping recent ones for rollback"""
        secret_name = f"projects/{self.project_id}/secrets/{secret_id}"

        try:
            # List all versions
            versions = list(self.client.list_secret_versions(
                request={"parent": secret_name}
            ))

            # Sort by create time (newest first)
            versions.sort(key=lambda v: v.create_time, reverse=True)

            # Disable old versions (keep the specified number)
            for version in versions[keep_last:]:
                if version.state == secretmanager.SecretVersion.State.ENABLED:
                    self.client.disable_secret_version(
                        request={"name": version.name}
                    )
                    logger.info(f"Disabled old version: {version.name}")

        except Exception as e:
            logger.warning(f"Could not cleanup old versions for {secret_id}: {e}")

    def validate_rotation_health(self, secret_id: str) -> Dict[str, Any]:
        """
        Check rotation health and configuration

        Returns:
            Health status including last rotation, next rotation, version count
        """
        secret_name = f"projects/{self.project_id}/secrets/{secret_id}"

        try:
            # Get secret metadata
            secret = self.client.get_secret(request={"name": secret_name})

            # Get version information
            versions = list(self.client.list_secret_versions(
                request={"parent": secret_name}
            ))

            enabled_versions = [v for v in versions if v.state == secretmanager.SecretVersion.State.ENABLED]

            # Calculate health metrics
            latest_version = max(versions, key=lambda v: v.create_time) if versions else None

            health = {
                "secret_id": secret_id,
                "rotation_configured": secret.rotation is not None if hasattr(secret, 'rotation') else False,
                "total_versions": len(versions),
                "enabled_versions": len(enabled_versions),
                "latest_version": latest_version.name if latest_version else None,
                "latest_version_created": latest_version.create_time.isoformat() if latest_version else None,
                "labels": dict(secret.labels) if secret.labels else {},
                "replication_policy": "automatic" if secret.replication.automatic else "user_managed",
                "health_status": "healthy" if enabled_versions else "unhealthy"
            }

            # Add rotation schedule if configured
            if hasattr(secret, 'rotation') and secret.rotation:
                health["rotation_period_days"] = secret.rotation.rotation_period.days
                health["next_rotation"] = secret.rotation.next_rotation_time.isoformat()

            return health

        except Exception as e:
            logger.error(f"Failed to check rotation health for {secret_id}: {e}")
            return {
                "secret_id": secret_id,
                "health_status": "error",
                "error": str(e)
            }


class SecretAccessCache:
    """
    Caches secret access to reduce Secret Manager API calls
    Implements TTL and automatic refresh
    """

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, secret_id: str) -> Optional[str]:
        """Get cached secret if not expired"""
        if secret_id in self._cache:
            entry = self._cache[secret_id]
            if datetime.utcnow() < entry["expires"]:
                return entry["value"]
            else:
                # Expired, remove from cache
                del self._cache[secret_id]
        return None

    def set(self, secret_id: str, value: str):
        """Cache a secret with TTL"""
        self._cache[secret_id] = {
            "value": value,
            "expires": datetime.utcnow() + timedelta(seconds=self.ttl_seconds),
            "accessed": datetime.utcnow()
        }

    def invalidate(self, secret_id: str = None):
        """Invalidate specific secret or entire cache"""
        if secret_id:
            self._cache.pop(secret_id, None)
        else:
            self._cache.clear()


# Global cache instance
_secret_cache = SecretAccessCache()


def get_secret_with_cache(secret_id: str, project_id: str = None) -> Optional[str]:
    """
    Get secret with caching to reduce API calls

    Args:
        secret_id: Secret identifier
        project_id: GCP project ID

    Returns:
        Secret value or None
    """
    # Check cache first
    cached = _secret_cache.get(secret_id)
    if cached:
        return cached

    # Fetch from Secret Manager
    try:
        project_id = project_id or os.getenv("GCP_PROJECT_ID", "jax-platform-prod")
        client = secretmanager.SecretManagerServiceClient()

        # Use 'latest' alias for current version
        secret_path = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": secret_path})
        value = response.payload.data.decode("UTF-8")

        # Cache the value
        _secret_cache.set(secret_id, value)

        return value

    except Exception as e:
        logger.error(f"Failed to get secret {secret_id}: {e}")
        return None


def invalidate_secret_cache(secret_id: str = None):
    """
    Invalidate cached secrets (call after rotation)

    Args:
        secret_id: Specific secret to invalidate, or None for all
    """
    _secret_cache.invalidate(secret_id)
    logger.info(f"Invalidated cache for: {secret_id or 'all secrets'}")
