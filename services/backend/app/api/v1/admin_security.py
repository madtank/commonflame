"""
Admin Security Management Endpoints
For monitoring and managing rate limits and security violations
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from datetime import datetime, timedelta

from app.core.database import get_db_session
from app.core.rls import AdminSession, get_admin_session
from app.core.jwt_verify import get_admin_user_from_token
from app.models.user import User
from app.middleware.rate_limiting_middleware import rate_limit_store
from app.core.async_logging import get_recent_security_violations

router = APIRouter(prefix="/admin/security", tags=["admin-security"])

def require_admin(current_user: User = Depends(get_admin_user_from_token)) -> User:
    """Require admin role for access"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


@router.get("/blocked-users")
async def get_blocked_users(
    admin_user: User = Depends(require_admin)
):
    """Get list of currently blocked users/IPs"""
    current_time = datetime.now().timestamp()

    blocked_list = []
    for ip, unblock_time in rate_limit_store.blocked_ips.items():
        time_remaining = unblock_time - current_time
        if time_remaining > 0:
            blocked_list.append({
                "identifier": ip,
                "unblock_time": datetime.fromtimestamp(unblock_time).isoformat(),
                "seconds_remaining": int(time_remaining),
                "type": "rate_limit"
            })

    return {
        "blocked_count": len(blocked_list),
        "blocked_users": blocked_list
    }


@router.get("/agent-violations")
async def get_agent_violations(
    admin_user: User = Depends(require_admin),
    hours: int = 24
):
    """Get recent agent-related security violations"""
    current_time = datetime.now().timestamp()
    cutoff_time = current_time - (hours * 3600)

    violations = []
    for key, timestamps in rate_limit_store.agent_violations.items():
        # Filter to recent violations
        recent = [t for t in timestamps if t > cutoff_time]
        if recent:
            user_id, violation_type = key.split(":", 1)
            violations.append({
                "user_id": user_id,
                "violation_type": violation_type,
                "count": len(recent),
                "first_violation": datetime.fromtimestamp(min(recent)).isoformat(),
                "last_violation": datetime.fromtimestamp(max(recent)).isoformat()
            })

    # Sort by count descending
    violations.sort(key=lambda x: x["count"], reverse=True)

    return {
        "hours_window": hours,
        "total_violations": sum(v["count"] for v in violations),
        "unique_users": len(set(v["user_id"] for v in violations)),
        "violations": violations
    }


@router.post("/unblock-user")
async def unblock_user(
    identifier: str,
    admin_user: User = Depends(require_admin)
):
    """Manually unblock a user/IP"""
    if identifier in rate_limit_store.blocked_ips:
        del rate_limit_store.blocked_ips[identifier]

        # Log admin action
        print(f"🔓 Admin {admin_user.username} unblocked {identifier}")

        return {
            "success": True,
            "message": f"Successfully unblocked {identifier}",
            "unblocked_by": admin_user.username,
            "timestamp": datetime.now().isoformat()
        }
    else:
        return {
            "success": False,
            "message": f"{identifier} was not blocked"
        }


@router.post("/clear-violations")
async def clear_violations(
    user_id: Optional[str] = None,
    admin_user: User = Depends(require_admin)
):
    """Clear violation history for a user or all users"""
    if user_id:
        # Clear specific user's violations
        keys_to_clear = [k for k in rate_limit_store.agent_violations.keys() if k.startswith(f"{user_id}:")]
        for key in keys_to_clear:
            del rate_limit_store.agent_violations[key]

        message = f"Cleared violations for user {user_id}"
    else:
        # Clear all violations
        rate_limit_store.agent_violations.clear()
        message = "Cleared all violation history"

    print(f"🧹 Admin {admin_user.username}: {message}")

    return {
        "success": True,
        "message": message,
        "cleared_by": admin_user.username,
        "timestamp": datetime.now().isoformat()
    }


@router.get("/rate-limit-stats")
async def get_rate_limit_stats(
    admin_user: User = Depends(require_admin)
):
    """Get overall rate limiting statistics"""
    return {
        "active_request_keys": len(rate_limit_store.requests),
        "blocked_ips_count": len(rate_limit_store.blocked_ips),
        "suspicious_patterns": dict(rate_limit_store.suspicious_patterns),
        "agent_violation_users": len(set(k.split(":")[0] for k in rate_limit_store.agent_violations.keys())),
        "total_tracked_requests": sum(len(requests) for requests in rate_limit_store.requests.values())
    }


@router.get("/security-dashboard")
async def security_dashboard(
    admin_user: User = Depends(require_admin),
    admin_session: AdminSession = Depends(get_admin_session)
):
    """Comprehensive security dashboard data"""

    # Get agent statistics
    agent_stats = await admin_session.db.execute("""
        SELECT
            COUNT(DISTINCT a.id) as total_agents,
            COUNT(DISTINCT a.user_id) as users_with_agents,
            COUNT(DISTINCT CASE WHEN a.created_at > NOW() - INTERVAL '24 hours' THEN a.id END) as new_agents_24h
        FROM agents a
    """)
    stats = agent_stats.first()

    # Get current blocks
    current_blocks = await get_blocked_users(admin_user)

    # Get recent violations
    recent_violations = await get_agent_violations(admin_user, hours=1)

    # Get rate limit stats
    rate_stats = await get_rate_limit_stats(admin_user)

    return {
        "timestamp": datetime.now().isoformat(),
        "agent_stats": {
            "total_agents": stats.total_agents,
            "users_with_agents": stats.users_with_agents,
            "new_agents_24h": stats.new_agents_24h
        },
        "security_status": {
            "blocked_users": current_blocks["blocked_count"],
            "recent_violations_1h": recent_violations["total_violations"],
            "unique_violators_1h": recent_violations["unique_users"]
        },
        "rate_limiting": rate_stats,
        "health": {
            "status": "healthy" if current_blocks["blocked_count"] < 10 else "elevated",
            "threat_level": "low" if recent_violations["total_violations"] < 5 else "medium" if recent_violations["total_violations"] < 20 else "high"
        }
    }
