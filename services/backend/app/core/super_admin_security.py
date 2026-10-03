"""
Super Admin Security Dependency

CRITICAL SECURITY MODULE
This module enforces the highest level of access control for dangerous features.

Security requirements:
1. User must have super_admin role
2. BETA_MODE must be enabled (prevents accidental production use)
3. Request must be from authenticated session
4. Audit logging for all super admin operations

Usage:
    @router.post("/dangerous-operation")
    async def dangerous_op(
        super_admin: User = Depends(require_super_admin)
    ):
        # Only super admins with BETA_MODE=true can reach here
        ...
"""

import logging
from datetime import datetime

from fastapi import Depends, HTTPException, Request, status

from ..api.v1.auth import get_current_user_from_token
from ..core.beta_config import get_beta_config
from ..models.user import User

logger = logging.getLogger(__name__)


class SuperAdminAuditLog:
    """Audit logger for super admin operations"""

    @staticmethod
    def log_access(
        user: User | None,
        endpoint: str,
        action: str,
        request: Request | None,
        success: bool = True,
        error: str | None = None,
    ):
        """Log super admin access (handles None user/request for unauthenticated attempts)"""
        # Handle unauthenticated requests gracefully
        user_id = str(user.id) if user else "unauthenticated"
        username = user.username if user else "anonymous"
        role = user.role if user else "none"

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "username": username,
            "role": role,
            "endpoint": endpoint,
            "action": action,
            "success": success,
            "ip_address": request.client.host if (request and request.client) else "unknown",
            "user_agent": request.headers.get("user-agent", "unknown") if request else "unknown",
        }

        if error:
            log_entry["error"] = error

        if success:
            logger.warning(f"🔴 SUPER ADMIN ACCESS: {username} ({role}) " f"accessed {endpoint} - {action}")
        else:
            logger.error(
                f"🚨 SUPER ADMIN ACCESS DENIED: {username} ({role}) "
                f"attempted {endpoint} - {action} - ERROR: {error}"
            )

        # In production, you'd also write to a dedicated audit table
        return log_entry


async def require_super_admin(
    current_user: User = Depends(get_current_user_from_token), request: Request = None
) -> User:
    """
    Dependency that requires super_admin role + BETA_MODE

    This is the MOST RESTRICTIVE access control in the system.
    Use ONLY for features that could cause significant harm if misused:
    - AI-powered automated messaging
    - Bulk user operations
    - System-wide configuration changes
    - Data export/deletion

    Raises:
        HTTPException 403: If user lacks super_admin role
        HTTPException 403: If BETA_MODE is not enabled
        HTTPException 403: If any other security check fails

    Returns:
        User: The authenticated super admin user

    Example:
        @router.post("/ai/generate")
        async def ai_generate(
            super_admin: User = Depends(require_super_admin)
        ):
            # Only super admins can reach here
            pass
    """

    # Check 1: User must be authenticated
    if not current_user:
        SuperAdminAuditLog.log_access(
            current_user,
            request.url.path if request else "unknown",
            "authentication_failed",
            request,
            success=False,
            error="No authenticated user",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    # Check 2: User must have super_admin role
    if current_user.role != "super_admin":
        SuperAdminAuditLog.log_access(
            current_user,
            request.url.path if request else "unknown",
            "insufficient_role",
            request,
            success=False,
            error=f"User has role '{current_user.role}', requires 'super_admin'",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "Super admin access required",
                "user_role": current_user.role,
                "required_role": "super_admin",
                "message": "This feature requires super admin privileges. Contact system administrator.",
            },
        )

    # Check 3: BETA_MODE must be enabled (safety switch)
    beta_config = get_beta_config()
    if not beta_config.BETA_MODE:
        SuperAdminAuditLog.log_access(
            current_user,
            request.url.path if request else "unknown",
            "beta_mode_disabled",
            request,
            success=False,
            error="BETA_MODE is not enabled",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "Feature not available",
                "message": "This feature requires BETA_MODE=true. Enable beta mode to access AI features.",
                "beta_mode": False,
            },
        )

    # Check 4: User must be active
    if not current_user.active:
        SuperAdminAuditLog.log_access(
            current_user,
            request.url.path if request else "unknown",
            "inactive_user",
            request,
            success=False,
            error="User account is inactive",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive")

    # All checks passed - log successful access
    SuperAdminAuditLog.log_access(
        current_user, request.url.path if request else "unknown", "access_granted", request, success=True
    )

    return current_user


async def require_admin_or_super_admin(current_user: User = Depends(get_current_user_from_token)) -> User:
    """
    Less restrictive: Requires admin, agent_manager, or super_admin

    Use for standard admin features that don't require super admin.
    """
    if current_user.role not in ["admin", "agent_manager", "super_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def check_super_admin_role(user: User) -> bool:
    """
    Utility function to check if user is super admin

    Use in business logic when you need to branch behavior
    based on super admin status.
    """
    return user.role == "super_admin"


def get_user_privilege_level(user: User) -> str:
    """
    Get human-readable privilege level

    Returns:
        "super_admin", "admin", "agent_manager", or "user"
    """
    role_hierarchy = {
        "super_admin": "Super Administrator (Highest Privilege)",
        "admin": "Administrator",
        "agent_manager": "Agent Manager",
        "plus": "Plus User (Paid Tier)",
        "user": "Regular User",
    }
    return role_hierarchy.get(user.role, "Unknown")


# Export audit log class for use in endpoints
__all__ = [
    "SuperAdminAuditLog",
    "check_super_admin_role",
    "get_user_privilege_level",
    "require_admin_or_super_admin",
    "require_super_admin",
]
