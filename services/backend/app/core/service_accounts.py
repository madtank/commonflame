"""
Service Account Authentication (Future Implementation)

This module provides stubs for service-to-service authentication.
Currently NOT IMPLEMENTED - provides interface documentation only.

Future capabilities:
- API key authentication (sa_<id>_<secret> pattern)
- Service account lifecycle management
- Scope-based authorization for machine clients

Security notes:
- API keys should be stored as SHA-256 hashes only
- Keys should have expiration and rotation support
- All service account operations must be logged
"""

from uuid import UUID

from .actor import Actor


class ServiceAccountAuth:
    """
    Service account authentication utilities.

    Future implementation will support:
    - API key generation and validation
    - Scope-based access control
    - Token exchange for short-lived credentials
    """

    @staticmethod
    async def authenticate_api_key(api_key: str) -> Actor | None:
        """
        Authenticate a service account via API key.

        API key format: sa_<account_id>_<secret>

        Args:
            api_key: The API key to validate

        Returns:
            Actor with service account privileges, or None if invalid

        Raises:
            NotImplementedError: This feature is not yet implemented
        """
        raise NotImplementedError(
            "API key authentication is planned for future release. "
            "Currently, service operations use internal code paths only."
        )

    @staticmethod
    async def create_api_key(
        service_account_id: UUID,
        scopes: list[str],
        expires_in_days: int = 365,
    ) -> tuple[str, str]:
        """
        Create a new API key for a service account.

        Args:
            service_account_id: The service account UUID
            scopes: List of authorized scopes
            expires_in_days: Key validity period

        Returns:
            Tuple of (api_key, key_id) - key is only returned once!

        Raises:
            NotImplementedError: This feature is not yet implemented
        """
        raise NotImplementedError("API key creation is planned for future release.")

    @staticmethod
    async def revoke_api_key(key_id: str) -> bool:
        """
        Revoke an API key.

        Args:
            key_id: The key identifier to revoke

        Returns:
            True if revoked, False if not found

        Raises:
            NotImplementedError: This feature is not yet implemented
        """
        raise NotImplementedError("API key revocation is planned for future release.")


# API Key format documentation
API_KEY_FORMAT = """
Service Account API Key Format
==============================

Pattern: sa_<account_id>_<secret>

Components:
- sa_          : Fixed prefix identifying service account keys
- account_id   : First 8 chars of service account UUID
- secret       : 32 random bytes, base64url encoded

Example: sa_00000000_xK9mQ2pL7nR4vW8yB3cF6hJ1kM5oT0aE

Storage:
- Only SHA-256 hash of full key stored in database
- Original key shown once at creation, never again
- Key metadata (scopes, expiry) stored separately

Validation:
1. Parse prefix and account_id from key
2. Lookup service account by account_id prefix
3. Compare SHA-256(key) against stored hash
4. Verify scopes and expiration
5. Return Actor with appropriate capabilities
"""
