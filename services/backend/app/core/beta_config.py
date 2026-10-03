"""
Beta Configuration for The operator's Minimal Gamified Beta
Invite-only access and agent limits implementation
"""

import logging
import os
from typing import ClassVar

from redis import exceptions as redis_exceptions
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agent import Agent
from ..models.user import User
from .connection_pools import RedisPool

logger = logging.getLogger(__name__)


class BetaConfig:
    """Beta configuration and validation"""

    # The operator's Beta Settings
    INVITE_ONLY: bool = os.getenv("INVITE_ONLY", "false").lower() == "true"
    MAX_USERS: int = int(os.getenv("MAX_USERS", "200"))  # Updated for beta launch
    AGENT_LIMIT_DEFAULT: int = int(os.getenv("AGENT_LIMIT_DEFAULT", "5"))
    AGENT_LIMIT_PLUS: int = int(os.getenv("AGENT_LIMIT_PLUS", "50"))
    AGENT_LIMIT_ADMIN: str = os.getenv("AGENT_LIMIT_ADMIN", "unlimited")
    ADMIN_UNLIMITED: bool = os.getenv("ADMIN_UNLIMITED", "true").lower() == "true"

    # Invite Codes - Simplified to 5 memorable codes for beta
    VALID_INVITE_CODES: ClassVar[set[str]] = set(
        os.getenv("VALID_INVITE_CODES", "").split(",")
    )

    # Feature Flags
    BETA_MODE: bool = os.getenv("BETA_MODE", "false").lower() == "true"
    ENABLE_GAMIFICATION: bool = os.getenv("ENABLE_GAMIFICATION", "false").lower() == "true"
    ENABLE_CREDITS: bool = os.getenv("ENABLE_CREDITS", "false").lower() == "true"
    ENABLE_LEADERBOARD: bool = os.getenv("ENABLE_LEADERBOARD", "false").lower() == "true"

    @classmethod
    def is_valid_invite_code(cls, code: str) -> bool:
        """Check if invite code is valid"""
        if not cls.INVITE_ONLY:
            return True  # No restriction if invite-only is disabled
        return code.strip().upper() in {c.upper() for c in cls.VALID_INVITE_CODES}

    @classmethod
    async def can_register_user(cls, db: AsyncSession) -> bool:
        """Check if new user registration is allowed"""
        if not cls.INVITE_ONLY:
            return True

        # Count current users
        result = await db.execute(select(func.count(User.id)).where(User.active.is_(True)))
        user_count = result.scalar() or 0

        return user_count < cls.MAX_USERS

    @classmethod
    async def can_register_agent(cls, db: AsyncSession, user: User) -> tuple[bool, str]:
        """Check if user can register another agent"""
        # Admin has unlimited agents
        if cls.ADMIN_UNLIMITED and user.role == "admin":
            return True, "Admin has unlimited agent slots"

        # Determine per-role agent limit
        agent_limit = cls.AGENT_LIMIT_DEFAULT
        if (user.role or "").lower() == "plus":
            agent_limit = cls.AGENT_LIMIT_PLUS

            # Allow dynamic override via admin settings (Redis)
            key = "system:settings:agent_limit_plus"
            try:
                raw_value = await RedisPool.execute("get", key)
                if raw_value is not None:
                    value_str = raw_value.decode() if isinstance(raw_value, bytes) else str(raw_value)
                    parsed = int(value_str)
                    if parsed > 0:
                        agent_limit = parsed
            except (
                ValueError,
                redis_exceptions.ConnectionError,
                redis_exceptions.TimeoutError,
                redis_exceptions.RedisError,
            ) as e:
                logger.warning(f"Redis unavailable for plus agent limit override: {e}")

        # Count user's current active agents
        result = await db.execute(
            select(func.count(Agent.id)).where(Agent.user_id == user.id).where(Agent.status == "active")
        )
        agent_count = result.scalar() or 0

        if agent_count >= agent_limit:
            return (
                False,
                f"Agent limit reached ({agent_count}/{agent_limit}). The operator can configure AGENT_LIMIT_DEFAULT.",
            )

        return True, f"Agent slot available ({agent_count}/{agent_limit})"

    @classmethod
    def get_beta_status(cls) -> dict:
        """Get current beta configuration status"""
        return {
            "beta_mode": cls.BETA_MODE,
            "invite_only": cls.INVITE_ONLY,
            "max_users": cls.MAX_USERS,
            "agent_limit_default": cls.AGENT_LIMIT_DEFAULT,
            "admin_unlimited": cls.ADMIN_UNLIMITED,
            "gamification_enabled": cls.ENABLE_GAMIFICATION,
            "credits_enabled": cls.ENABLE_CREDITS,
            "leaderboard_enabled": cls.ENABLE_LEADERBOARD,
            "invite_codes_count": len(cls.VALID_INVITE_CODES),
        }


# Global beta config instance
beta_config = BetaConfig()


def get_beta_config() -> BetaConfig:
    """Get beta configuration"""
    return beta_config
