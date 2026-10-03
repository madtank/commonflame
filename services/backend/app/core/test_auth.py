"""
Test Authentication Helper
Provides secure local authentication for E2E testing
ONLY enabled in development environment
"""
import os
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class TestAuthManager:
    """Manages test authentication for local development"""

    def __init__(self):
        self.environment = os.getenv("ENVIRONMENT", "development")
        self.enable_test_auth = os.getenv("ENABLE_TEST_AUTH", "false").lower() == "true"

    @property
    def is_enabled(self) -> bool:
        """Check if test auth is enabled"""
        # ONLY allow in development environment
        if self.environment == "production":
            logger.warning("⚠️ Attempted to use test auth in production - BLOCKED")
            return False

        return self.enable_test_auth

    def get_test_users(self) -> dict:
        """Get test user credentials (only in dev)"""
        if not self.is_enabled:
            return {}

        return {
            "admin": {
                "email": "admin@paxai.app",
                "password": "admin123",
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            },
            "testuser": {
                "email": "test@example.com",
                "password": "testpass123",
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            }
        }

    def verify_test_password(self, email: str, password: str) -> bool:
        """Verify test user password (bypasses bcrypt in test mode)"""
        if not self.is_enabled:
            return False

        test_users = self.get_test_users()
        for user_data in test_users.values():
            if user_data["email"] == email and user_data["password"] == password:
                logger.info(f"✅ Test auth successful for {email}")
                return True

        return False

    def log_security_check(self):
        """Log security status for audit"""
        if self.is_enabled:
            logger.info(f"""
            🧪 TEST AUTHENTICATION STATUS:
            - Environment: {self.environment}
            - Test Auth Enabled: {self.enable_test_auth}
            - Production Safe: {self.environment != 'production'}
            - Time: {datetime.utcnow()}
            ⚠️  TEST AUTH IS ACTIVE - DO NOT USE IN PRODUCTION
            """)
        else:
            logger.info(f"🔒 Test auth disabled (env: {self.environment})")

# Singleton instance
test_auth_manager = TestAuthManager()
