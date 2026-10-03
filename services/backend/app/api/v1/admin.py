"""
Admin API endpoints for user management and platform monitoring
Enhanced with real database connectivity for admin dashboard
"""

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis import exceptions as redis_exceptions
from sqlalchemy import Integer, and_, func, or_, select
from sqlalchemy.orm import joinedload

from ...core.access_approval import VALID_REQUEST_STATUSES, set_request_status
from ...core.lockdown_rollback import run_lockdown_rollback
from ...core.beta_config import get_beta_config
from ...core.config import settings
from ...core.connection_pools import RedisPool
from ...core.rls import AdminSession, get_admin_session
from ...models.access_request import AccessRequest
from ...models.agent import Agent
from ...models.guardrail_violation import GuardrailViolation
from ...models.space import Space
from ...models.space_membership import SpaceMembership
from ...models.tool_call import ToolCall
from ...models.user import User
from ...models.user_audit_event import UserAuditEvent
from ...services.user_audit import email_domain
from ...core.jwt_verify import get_admin_user_from_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def require_admin_role(current_user: User = Depends(get_admin_user_from_token)) -> User:
    """Require admin privileges for endpoints"""
    if current_user.role not in ["admin", "agent_manager", "super_admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


@router.get("/stats")
async def get_admin_stats(
    admin_session: AdminSession = Depends(get_admin_session), current_user: User = Depends(require_admin_role)
) -> dict[str, Any]:
    """Get system-wide statistics for admin dashboard"""

    try:
        # Get system stats
        stats = {}

        # Total users
        result = await admin_session.db.execute(select(func.count(User.id)))
        stats["total_users"] = result.scalar() or 0

        # Active users (use created_at as proxy since last_login doesn't exist)
        active_cutoff = datetime.now() - timedelta(days=30)
        result = await admin_session.db.execute(
            select(func.count(User.id)).where(and_(User.active.is_(True), User.created_at > active_cutoff))
        )
        stats["active_users"] = result.scalar() or 0

        # Total agents
        result = await admin_session.db.execute(select(func.count(Agent.id)))
        stats["total_agents"] = result.scalar() or 0

        # Active agents (updated within last 7 days)
        result = await admin_session.db.execute(select(func.count(Agent.id)).where(Agent.updated_at > active_cutoff))
        stats["active_agents"] = result.scalar() or 0

        # Recent signups (last 7 days)
        signup_cutoff = datetime.now() - timedelta(days=7)
        result = await admin_session.db.execute(select(func.count(User.id)).where(User.created_at > signup_cutoff))
        stats["recent_signups"] = result.scalar() or 0

        # Guardrail violations count - temporarily disabled to fix admin dashboard
        # TODO: Re-enable after debugging database connection issue
        try:
            result = await admin_session.db.execute(
                select(func.count(GuardrailViolation.id)).where(GuardrailViolation.space_id == current_user.space_id)
            )
            stats["guardrail_violations"] = result.scalar() or 0
        except Exception as e:
            logger.warning(f"Could not fetch guardrail violations count: {e}")
            stats["guardrail_violations"] = 0

        logger.info(f"Admin stats retrieved by {current_user.username}: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Error getting admin stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve admin statistics"
        )


@router.get("/users")
async def get_all_users(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    limit: int = Query(100, ge=1, le=500),  # Increased to 100 default, 500 max for admin
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    role_filter: str | None = Query(None),
    account_status: str | None = Query(None, alias="status", description="active | inactive"),
    created_within_days: int | None = Query(None, ge=1, le=3650, description="only users created in the last N days"),
    sort_by: str = Query("created", description="created | username | email | role | status"),
    sort_dir: str = Query("desc", description="asc | desc"),
) -> dict[str, Any]:
    """Get all users for user management with pagination and filtering"""

    try:
        # Build base query
        query = select(User)

        # Apply search filter
        if search:
            search_term = f"%{search}%"
            query = query.where(or_(User.username.ilike(search_term), User.email.ilike(search_term)))

        # Apply role filter
        if role_filter:
            query = query.where(User.role == role_filter)

        # Apply account status filter (enable/disable state). Note: param is
        # aliased to ``account_status`` so it doesn't shadow ``fastapi.status``
        # (used by the except blocks for HTTP_500).
        if account_status == "active":
            query = query.where(User.active.is_(True))
        elif account_status == "inactive":
            query = query.where(User.active.is_(False))

        # Apply "newly created" / creation-date window filter
        if created_within_days:
            cutoff = datetime.now() - timedelta(days=created_within_days)
            query = query.where(User.created_at >= cutoff)

        # Get total count for pagination (after filters)
        count_query = select(func.count()).select_from(query.subquery())
        count_result = await admin_session.db.execute(count_query)
        total = count_result.scalar() or 0

        # Column-header sort. Default = created desc (newest first). Only
        # DB-backed columns are sortable (activity is derived post-query).
        _sort_cols = {
            "created": User.created_at,
            "username": User.username,
            "email": User.email,
            "role": User.role,
            "status": User.active,
        }
        sort_col = _sort_cols.get(sort_by, User.created_at)
        order_col = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
        query = query.order_by(order_col)

        # Get users with pagination
        paginated_query = query.offset(offset).limit(limit)
        result = await admin_session.db.execute(paginated_query)
        users = result.scalars().all()

        # Get detailed user information including agent counts and message activity
        user_data = []
        for user in users:
            # Get agent count for this user
            agent_count_result = await admin_session.db.execute(select(func.count(Agent.id)).where(Agent.user_id == user.id))
            agent_count = agent_count_result.scalar() or 0

            # Get space membership count for this user
            from ...models.space_membership import SpaceMembership

            org_count_result = await admin_session.db.execute(
                select(func.count(SpaceMembership.id)).where(SpaceMembership.user_id == user.id)
            )
            org_count = org_count_result.scalar() or 0

            # Get message count for this user (user messages + their agents' messages)
            from ...models.message import Message

            # Get all agent IDs for this user
            user_agent_ids_result = await admin_session.db.execute(select(Agent.id).where(Agent.user_id == user.id))
            user_agent_ids = [row[0] for row in user_agent_ids_result.fetchall()]

            # Count messages from user OR their agents
            message_count_result = await admin_session.db.execute(
                select(func.count(Message.id)).where(
                    or_(Message.user_id == user.id, Message.agent_id.in_(user_agent_ids) if user_agent_ids else False)
                )
            )
            message_count = message_count_result.scalar() or 0

            # Get task count for this user (tasks created by user + their agents)
            from ...models.task import Task

            task_count_result = await admin_session.db.execute(
                select(func.count(Task.id)).where(
                    or_(
                        Task.posted_by == user.id,
                        Task.posted_by_agent_id.in_(user_agent_ids) if user_agent_ids else False,
                        Task.assigned_agent_id.in_(user_agent_ids) if user_agent_ids else False,
                    )
                )
            )
            task_count = task_count_result.scalar() or 0

            # Get last message timestamp for activity tracking
            last_message_result = await admin_session.db.execute(
                select(func.max(Message.created_at)).where(Message.user_id == user.id)
            )
            last_message_at = last_message_result.scalar()

            # Calculate activity status based on actual user activity (not account status)
            last_activity = None
            activity_status = "N/A"  # Default for users with no activity

            # Use the most recent of user creation, agent updates, or messages
            activity_timestamps = []
            if last_message_at:
                activity_timestamps.append(last_message_at)

            # Only include creation date if user has actually done something (has messages or agents)
            if message_count > 0 or agent_count > 0:
                activity_timestamps.append(user.created_at)

            if activity_timestamps:
                most_recent = max(activity_timestamps)
                last_activity = most_recent.isoformat() if most_recent else None

                if most_recent:
                    days_since_activity = (datetime.now() - most_recent.replace(tzinfo=None)).days
                    if days_since_activity == 0:
                        activity_status = "Active Today"
                    elif days_since_activity <= 1:
                        activity_status = "Active Recently"
                    elif days_since_activity <= 7:
                        activity_status = "Active This Week"
                    elif days_since_activity <= 30:
                        activity_status = "Active This Month"
                    else:
                        activity_status = "Dormant"

            user_data.append(
                {
                    "id": str(user.id),
                    "username": user.username,
                    "email": user.email,
                    "role": user.role,
                    "status": "active" if user.active else "inactive",
                    "violations_count": user.violations_count,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "last_activity": last_activity,
                    "last_message_at": last_message_at.isoformat() if last_message_at else None,
                    "agent_count": agent_count,
                    "message_count": message_count,
                    "task_count": task_count,
                    "org_count": org_count,
                    "activity_status": activity_status,
                }
            )

        logger.info(f"Admin user list retrieved by {current_user.username}: {len(user_data)} users")

        return {
            "users": user_data,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }

    except Exception as e:
        logger.error(f"Error getting user list: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve user list")


@router.get("/users/{user_id}/audit-events")
async def get_user_audit_events(
    user_id: str,
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Admin-visible audit trail for a user's signup, space, and tool touches."""

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user id") from None

    user_result = await admin_session.db.execute(select(User).where(User.id == user_uuid))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    count_result = await admin_session.db.execute(
        select(func.count()).select_from(
            select(UserAuditEvent.id).where(UserAuditEvent.user_id == user_uuid).subquery()
        )
    )
    total = count_result.scalar() or 0
    events_result = await admin_session.db.execute(
        select(UserAuditEvent)
        .where(UserAuditEvent.user_id == user_uuid)
        .order_by(UserAuditEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    events = events_result.scalars().all()

    memberships_result = await admin_session.db.execute(
        select(SpaceMembership.space_id, Space.name, SpaceMembership.role)
        .join(Space, Space.id == SpaceMembership.space_id)
        .where(SpaceMembership.user_id == user_uuid)
        .order_by(Space.name.asc())
    )
    spaces = [
        {"space_id": str(space_id), "name": name, "role": role}
        for space_id, name, role in memberships_result.all()
    ]

    agents_result = await admin_session.db.execute(select(Agent.id).where(Agent.user_id == user_uuid))
    agent_ids = [row[0] for row in agents_result.all()]
    tools: list[dict[str, Any]] = []
    if agent_ids:
        tools_result = await admin_session.db.execute(
            select(ToolCall.tool_name, func.count(ToolCall.id), func.max(ToolCall.created_at))
            .where(ToolCall.agent_id.in_(agent_ids))
            .group_by(ToolCall.tool_name)
            .order_by(func.max(ToolCall.created_at).desc())
            .limit(100)
        )
        tools = [
            {"tool_name": tool_name, "count": count, "last_touched_at": _iso(last_touched_at)}
            for tool_name, count, last_touched_at in tools_result.all()
        ]

    logger.info(
        "Admin user audit retrieved by %s for user=%s events=%s",
        current_user.username,
        user_uuid,
        len(events),
    )
    return {
        "user": {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "email_domain": email_domain(user.email),
            "created_at": _iso(user.created_at),
            "status": "active" if user.active else "inactive",
        },
        "events": [
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "created_at": _iso(event.created_at),
                "space_id": str(event.space_id) if event.space_id else None,
                "tool_name": event.tool_name,
                "resource_type": event.resource_type,
                "resource_id": str(event.resource_id) if event.resource_id else None,
                "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
                "actor_agent_id": str(event.actor_agent_id) if event.actor_agent_id else None,
                "source": event.source,
                "metadata": event.metadata_json or {},
            }
            for event in events
        ],
        "spaces_touched": spaces,
        "tools_touched": tools,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


@router.get("/cloud-usage")
async def get_cloud_agent_usage(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """
    Surface cloud agent daily usage for admins to spot abuse quickly.

    Data source: CloudAgentLimiter Redis keys (ax:cloud-agent:user-<uid>:<org>:daily)
    """
    redis = None
    try:
        redis = RedisPool.get_client()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Redis unavailable for cloud usage: {exc}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Usage data unavailable (Redis)")

    daily_limit = settings.cloud_agent_per_user_daily_limit or 0
    entries: list[dict[str, Any]] = []
    totals: defaultdict[str, int] = defaultdict(int)

    # Scan Redis for daily user keys
    try:
        async for key in redis.scan_iter(match="ax:cloud-agent:user-*:*:daily"):
            key_str = key if isinstance(key, str) else key.decode()
            parts = key_str.split(":")
            if len(parts) < 5:
                continue

            user_part = parts[2]  # user-<uuid>
            org_part = parts[3]
            if not user_part.startswith("user-"):
                continue

            user_id = user_part.replace("user-", "", 1)
            space_id = org_part

            raw_count = await RedisPool.execute("get", key_str)
            count = int(raw_count) if raw_count is not None else 0
            ttl = await RedisPool.execute("ttl", key_str)

            totals[user_id] += count
            # Per-user override (best effort)
            override_key = f"ax:cloud-agent:limit:user:{user_id}"
            raw_override = await RedisPool.execute("get", override_key)
            override_limit = int(raw_override) if raw_override is not None else None

            effective_limit = override_limit or daily_limit
            blocked = bool(effective_limit and count >= effective_limit)
            remaining = max(0, effective_limit - count) if effective_limit else None

            entries.append(
                {
                    "user_id": user_id,
                    "space_id": space_id,
                    "count": count,
                    "remaining": remaining,
                    "limit": effective_limit,
                    "override_limit": override_limit,
                    "ttl_seconds": ttl,
                    "blocked": blocked,
                    "key": key_str,
                }
            )

        # Sort by highest usage and cap result set
        entries.sort(key=lambda e: e.get("count", 0), reverse=True)
        entries = entries[:limit]

        totals_list = []
        for uid, total in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]:
            raw_override = await RedisPool.execute("get", f"ax:cloud-agent:limit:user:{uid}")
            override_limit = int(raw_override) if raw_override is not None else None
            effective_limit = override_limit or daily_limit
            totals_list.append(
                {
                    "user_id": uid,
                    "total": total,
                    "blocked": bool(effective_limit and total >= effective_limit),
                    "remaining": max(0, effective_limit - total) if effective_limit else None,
                    "limit": effective_limit,
                    "override_limit": override_limit,
                }
            )

        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "limit_per_user_daily": daily_limit,
            "entries": entries,
            "totals": totals_list,
        }
    except Exception as exc:
        logger.error(f"Failed to load cloud usage: {exc}")
        # If Redis fails during iteration, it's a service availability issue
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Usage data unavailable: {exc!s}")


@router.post("/cloud-usage/limit")
async def set_cloud_agent_limit(
    payload: dict[str, Any],
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
) -> dict[str, Any]:
    """
    Set or clear a per-user daily cloud agent limit override.
    payload: { "user_id": str, "daily_limit": int | null }
    """
    user_id = payload.get("user_id")
    daily_limit = payload.get("daily_limit")

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")

    if daily_limit is not None:
        try:
            daily_limit = int(daily_limit)
        except Exception:
            raise HTTPException(status_code=400, detail="daily_limit must be an integer")
        if daily_limit <= 0:
            daily_limit = None  # treat non-positive as clear

    redis = None
    try:
        redis = RedisPool.get_client()
    except Exception as exc:
        logger.error(f"Redis unavailable for limit update: {exc}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis unavailable")

    key = f"ax:cloud-agent:limit:user:{user_id}"
    try:
        if daily_limit is None:
            await redis.delete(key)
            action = "cleared"
            effective = settings.cloud_agent_per_user_daily_limit
        else:
            # Guardrail upper bound to prevent accidental unlimited
            safe_limit = min(daily_limit, 1000)
            await redis.set(key, safe_limit)
            action = "set"
            effective = safe_limit

        logger.info(
            "CLOUD_AGENT_LIMIT_UPDATE",
            extra={
                "user_id": user_id,
                "requested": daily_limit,
                "effective": effective,
                "action": action,
                "actor_admin": current_user.username,
            },
        )

        return {
            "user_id": user_id,
            "effective_daily_limit": effective,
            "action": action,
            "default_limit": settings.cloud_agent_per_user_daily_limit,
        }
    except Exception as exc:
        logger.error(f"Failed to update cloud agent limit: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update limit")


@router.get("/users/{user_id}")
async def get_user_details(
    user_id: str, admin_session: AdminSession = Depends(get_admin_session), current_user: User = Depends(require_admin_role)
) -> dict[str, Any]:
    """Get detailed information about a specific user including their agents"""

    try:
        # Get user
        result = await admin_session.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Get user's agents with details
        result = await admin_session.db.execute(select(Agent).where(Agent.user_id == user_id))
        agents = result.scalars().all()

        agent_data = []
        for agent in agents:
            agent_data.append(
                {
                    "id": str(agent.id),
                    "name": agent.name,
                    "agent_type": agent.agent_type,
                    "status": agent.status,
                    "created_at": agent.created_at.isoformat() if agent.created_at else None,
                    "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
                    "description": agent.description,
                }
            )

        # Calculate activity status based on created_at (since last_login doesn't exist)
        activity_status = "Active" if user.active else "Inactive"
        if user.created_at:
            days_since_creation = (datetime.now() - user.created_at.replace(tzinfo=None)).days
            if days_since_creation <= 1:
                activity_status = "New User"
            elif days_since_creation <= 7:
                activity_status = "Recent"

        user_details = {
            "user": {
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
                "role": user.role,
                "status": "active" if user.active else "inactive",
                "violations_count": user.violations_count,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login": None,  # Field doesn't exist in current schema
                "updated_at": user.updated_at.isoformat() if user.updated_at else None,
                "activity_status": activity_status,
            },
            "agents": agent_data,
            "total_agents": len(agent_data),
            "summary": {
                "agent_count": len(agent_data),
                "active_agents": len([a for a in agent_data if a["status"] == "active"]),
                "recent_activity": user.updated_at.isoformat() if user.updated_at else None,
            },
        }

        logger.info(f"Admin user details retrieved by {current_user.username} for user {user_id}")
        return user_details

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user details for {user_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve user details")


@router.post("/users/bulk-create-orgs")
async def bulk_create_personal_spaces(
    admin_session: AdminSession = Depends(get_admin_session), current_user: User = Depends(require_admin_role)
) -> dict[str, Any]:
    """Create personal spaces for all users who don't have any space membership"""

    try:
        from ...models.space_membership import SpaceMembership

        # Find users without any space membership OR with null space_id
        users_without_orgs_query = select(User).where(
            or_(~User.id.in_(select(SpaceMembership.user_id)), User.space_id.is_(None))
        )
        result = await admin_session.db.execute(users_without_orgs_query)
        users_without_orgs = result.scalars().all()

        if not users_without_orgs:
            return {"success": True, "message": "No users found without spaces", "users_processed": 0}

        created_orgs = []
        failed_users = []

        for user in users_without_orgs:
            try:
                # Check if user already has an space membership
                existing_membership = await admin_session.db.execute(
                    select(SpaceMembership).where(SpaceMembership.user_id == user.id).limit(1)
                )
                existing_membership_record = existing_membership.scalar_one_or_none()

                if existing_membership_record:
                    # User has membership but null space_id - just update the user's current org
                    space_id = existing_membership_record.space_id
                    await admin_session.db.execute(
                        """
                        UPDATE users SET space_id = $1, updated_at = $2 WHERE id = $3
                    """,
                        space_id,
                        datetime.now(),
                        user.id,
                    )

                    # Get the org name for logging
                    org_result = await admin_session.db.execute(select(Space.name).where(Space.id == space_id))
                    org_name = org_result.scalar() or "Unknown Space"

                    created_orgs.append(
                        {
                            "user_id": str(user.id),
                            "username": user.username,
                            "space_id": str(space_id),
                            "org_name": org_name,
                            "action": "updated_org_id",
                        }
                    )

                    logger.info(f"Updated space_id for user {user.username}: {space_id}")

                else:
                    # Create personal space
                    space_id = str(uuid.uuid4())
                    org_slug = f"{user.username}-personal"

                    # Create organization
                    await admin_session.db.execute(
                        """
                        INSERT INTO spaces (id, name, slug, description, visibility, created_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                        space_id,
                        f"{user.username}'s Space",
                        org_slug,
                        f"Personal space for {user.username}",
                        "private",
                        datetime.now(),
                        datetime.now(),
                    )

                    # Add user as admin member
                    membership_id = str(uuid.uuid4())
                    await admin_session.db.execute(
                        """
                        INSERT INTO space_memberships (id, space_id, user_id, role, joined_at)
                        VALUES ($1, $2, $3, $4, $5)
                    """,
                        membership_id,
                        space_id,
                        user.id,
                        "admin",
                        datetime.now(),
                    )

                    # Update user's current org
                    await admin_session.db.execute(
                        """
                        UPDATE users SET space_id = $1, updated_at = $2 WHERE id = $3
                    """,
                        space_id,
                        datetime.now(),
                        user.id,
                    )

                    created_orgs.append(
                        {
                            "user_id": str(user.id),
                            "username": user.username,
                            "space_id": space_id,
                            "org_name": f"{user.username}'s Space",
                            "action": "created_new_org",
                        }
                    )

                    logger.info(f"Created personal org for user {user.username}: {space_id}")

            except Exception as e:
                logger.error(f"Failed to create org for user {user.username}: {e}")
                failed_users.append({"user_id": str(user.id), "username": user.username, "error": str(e)})

        # Commit all changes
        await admin_session.db.commit()

        logger.info(
            f"Bulk org creation completed by {current_user.username}: {len(created_orgs)} created, {len(failed_users)} failed"
        )

        return {
            "success": True,
            "message": f"Created {len(created_orgs)} personal spaces",
            "users_processed": len(users_without_orgs),
            "organizations_created": len(created_orgs),
            "failed_users": len(failed_users),
            "created_orgs": created_orgs,
            "failures": failed_users if failed_users else None,
        }

    except Exception as e:
        logger.error(f"Error in bulk org creation: {e}")
        await admin_session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create organizations: {e!s}"
        )


@router.get("/database/health")
async def get_database_health_check(
    admin_session: AdminSession = Depends(get_admin_session), current_user: User = Depends(require_admin_role)
) -> dict[str, Any]:
    """Check for known database issues and inconsistencies"""

    try:
        from sqlalchemy import text

        issues = []

        # Check for users with null current_space_id but have memberships
        users_null_org_id = await admin_session.db.execute(
            text("""
            SELECT u.id, u.username, COUNT(om.id) as membership_count
            FROM users u
            LEFT JOIN space_memberships om ON u.id = om.user_id
            WHERE u.current_space_id IS NULL
            GROUP BY u.id, u.username
            HAVING COUNT(om.id) > 0
            ORDER BY u.username
        """)
        )

        null_org_users = users_null_org_id.fetchall()
        if null_org_users:
            issues.append(
                {
                    "type": "null_current_space_id",
                    "severity": "warning",
                    "title": "Users with null current_space_id",
                    "description": f"{len(null_org_users)} users have space memberships but null current_space_id",
                    "count": len(null_org_users),
                    "users": [
                        {"username": user.username, "membership_count": user.membership_count}
                        for user in null_org_users
                    ],
                    "fix_action": "Run bulk organization assignment to fix current_space_id references",
                }
            )

        # Check for users without any space membership
        users_no_membership = await admin_session.db.execute(
            text("""
            SELECT u.id, u.username
            FROM users u
            WHERE u.id NOT IN (
                SELECT DISTINCT user_id
                FROM space_memberships
                WHERE user_id IS NOT NULL
            )
            ORDER BY u.username
        """)
        )

        no_membership_users = users_no_membership.fetchall()
        if no_membership_users:
            issues.append(
                {
                    "type": "no_organization_membership",
                    "severity": "error",
                    "title": "Users without space membership",
                    "description": f"{len(no_membership_users)} users have no space membership at all",
                    "count": len(no_membership_users),
                    "users": [{"username": user.username} for user in no_membership_users],
                    "fix_action": "Run bulk organization assignment to create personal spaces",
                }
            )

        # Check for orphaned space memberships (user doesn't exist)
        orphaned_memberships = await admin_session.db.execute(
            text("""
            SELECT om.id, om.user_id, o.name as org_name
            FROM space_memberships om
            JOIN spaces o ON om.space_id = o.id
            WHERE om.user_id NOT IN (
                SELECT id FROM users
            )
            ORDER BY o.name
        """)
        )

        orphaned = orphaned_memberships.fetchall()
        if orphaned:
            issues.append(
                {
                    "type": "orphaned_memberships",
                    "severity": "warning",
                    "title": "Orphaned space memberships",
                    "description": f"{len(orphaned)} space memberships reference non-existent users",
                    "count": len(orphaned),
                    "memberships": [
                        {"membership_id": str(m.id), "user_id": str(m.user_id), "org_name": m.org_name}
                        for m in orphaned
                    ],
                    "fix_action": "Delete orphaned membership records",
                }
            )

        # Check for users with invalid current_space_id references
        invalid_org_refs = await admin_session.db.execute("""
            SELECT u.id, u.username, u.current_space_id
            FROM users u
            WHERE u.current_space_id IS NOT NULL
            AND u.current_space_id NOT IN (
                SELECT id FROM spaces
            )
            ORDER BY u.username
        """)

        invalid_refs = invalid_org_refs.fetchall()
        if invalid_refs:
            issues.append(
                {
                    "type": "invalid_org_references",
                    "severity": "error",
                    "title": "Invalid organization references",
                    "description": f"{len(invalid_refs)} users reference non-existent organizations",
                    "count": len(invalid_refs),
                    "users": [
                        {"username": user.username, "invalid_current_space_id": str(user.current_space_id)}
                        for user in invalid_refs
                    ],
                    "fix_action": "Fix or clear invalid organization references",
                }
            )

        # Overall health status
        if not issues:
            health_status = "healthy"
            summary = "No database issues detected"
        elif any(issue["severity"] == "error" for issue in issues):
            health_status = "error"
            summary = f"Critical issues found: {len([i for i in issues if i['severity'] == 'error'])} errors, {len([i for i in issues if i['severity'] == 'warning'])} warnings"
        else:
            health_status = "warning"
            summary = f"Minor issues found: {len(issues)} warnings"

        logger.info(f"Database health check by {current_user.username}: {health_status} - {len(issues)} issues")

        return {
            "health_status": health_status,
            "summary": summary,
            "issues_count": len(issues),
            "issues": issues,
            "checked_at": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Error in database health check: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to check database health: {e!s}"
        )


@router.patch("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    status_update: dict[str, bool],  # {"is_active": true/false}
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
) -> dict[str, Any]:
    """Enable or disable a user account"""

    try:
        # Get user
        result = await admin_session.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Don't allow disabling own account
        if user_id == str(current_user.id) and not status_update.get("is_active", True):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot disable your own account")

        # Update status
        if "is_active" in status_update:
            user.active = status_update["is_active"]
            user.updated_at = datetime.now()

            await admin_session.db.commit()
            await admin_session.db.refresh(user)

            action = "enabled" if user.active else "disabled"
            logger.info(f"User {user.username} {action} by admin {current_user.username}")

            return {
                "success": True,
                "user_id": user_id,
                "username": user.username,
                "is_active": user.active,
                "message": f"User {action} successfully",
            }
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status update data")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user status for {user_id}: {e}")
        await admin_session.db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update user status")


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    role_update: dict[str, str],  # {"role": "admin|agent_manager|user"}
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
) -> dict[str, Any]:
    """Change a user's role"""

    try:
        # Get user
        result = await admin_session.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Validate new role (ordered by privilege: lowest to highest)
        valid_roles = ["user", "plus", "agent_manager", "admin", "super_admin"]
        new_role = role_update.get("role")
        if new_role not in valid_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid role. Must be one of: {valid_roles}"
            )

        # Don't allow removing own admin role
        if user_id == str(current_user.id) and current_user.role == "admin" and new_role != "admin":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change your own admin role")

        # Update role
        old_role = user.role
        user.role = new_role
        user.updated_at = datetime.now()

        await admin_session.db.commit()
        await admin_session.db.refresh(user)

        logger.info(f"User {user.username} role changed from {old_role} to {new_role} by admin {current_user.username}")

        return {
            "success": True,
            "user_id": user_id,
            "username": user.username,
            "old_role": old_role,
            "new_role": new_role,
            "message": f"User role changed from {old_role} to {new_role}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user role for {user_id}: {e}")
        await admin_session.db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update user role")


@router.get("/activity")
async def get_recent_activity(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    limit: int = Query(20, ge=1, le=100),
    hours: int = Query(24, ge=1, le=168),  # Max 1 week
) -> dict[str, Any]:
    """Get recent user activity for monitoring"""

    try:
        cutoff_time = datetime.now() - timedelta(hours=hours)

        # Get recent user activities (using various timestamps as proxies)
        activities = []

        # Recent user registrations
        result = await admin_session.db.execute(
            select(User).where(User.created_at > cutoff_time).order_by(User.created_at.desc()).limit(limit // 3)
        )
        recent_users = result.scalars().all()

        for user in recent_users:
            activities.append(
                {
                    "user_id": str(user.id),
                    "username": user.username,
                    "activity_type": "user_registration",
                    "description": f"New user registered: {user.username}",
                    "timestamp": user.created_at.isoformat(),
                }
            )

        # Recent agent creations
        result = await admin_session.db.execute(
            select(Agent).where(Agent.created_at > cutoff_time).order_by(Agent.created_at.desc()).limit(limit // 3)
        )
        recent_agents = result.scalars().all()

        for agent in recent_agents:
            # Get user for this agent
            if agent.user_id:
                user_result = await admin_session.db.execute(select(User).where(User.id == agent.user_id))
                user = user_result.scalar_one_or_none()
            else:
                user = None

            activities.append(
                {
                    "user_id": str(agent.user_id) if agent.user_id else None,
                    "username": user.username if user else "Unknown",
                    "activity_type": "agent_creation",
                    "description": f"Created agent: {agent.name}",
                    "timestamp": agent.created_at.isoformat(),
                }
            )

        # Recent user activity (use created_at for new users as proxy)
        result = await admin_session.db.execute(
            select(User).where(User.created_at > cutoff_time).order_by(User.created_at.desc()).limit(limit // 3)
        )
        recent_users_activity = result.scalars().all()

        for user in recent_users_activity:
            activities.append(
                {
                    "user_id": str(user.id),
                    "username": user.username,
                    "activity_type": "user_activity",
                    "description": f"User activity: {user.username}",
                    "timestamp": user.created_at.isoformat(),
                }
            )

        # Sort all activities by timestamp (most recent first)
        activities.sort(key=lambda x: x["timestamp"], reverse=True)

        # Limit to requested number
        activities = activities[:limit]

        logger.info(f"Admin activity feed retrieved by {current_user.username}: {len(activities)} activities")

        return {
            "activities": activities,
            "total": len(activities),
            "period_hours": hours,
            "generated_at": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting recent activity: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve recent activity"
        )


@router.get("/violations")
async def get_guardrail_violations(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    limit: int = Query(20, ge=1, le=100),
    resolved: bool | None = Query(None),
) -> dict[str, Any]:
    """Get guardrail violations for monitoring and management"""

    try:
        # Build query for violations - Admins see ALL organizations
        query = select(GuardrailViolation).options(
            joinedload(GuardrailViolation.user), joinedload(GuardrailViolation.organization)
        )

        # Filter by resolved status if specified
        if resolved is not None:
            query = query.where(GuardrailViolation.resolved == resolved)

        # Order by most recent first and apply limit
        query = query.order_by(GuardrailViolation.created_at.desc()).limit(limit)

        result = await admin_session.db.execute(query)
        violations_data = result.scalars().all()

        # Format violations data
        violations = []
        for v in violations_data:
            violations.append(
                {
                    "id": str(v.id),
                    "space_id": str(v.space_id),
                    "org_name": v.organization.name if v.organization else "Unknown Space",
                    "user_id": str(v.user_id),
                    "username": v.user.username if v.user else "Unknown User",
                    "violation_type": v.violation_type,
                    "severity": v.severity,
                    "description": v.description,
                    "endpoint": v.endpoint,
                    "method": v.method,
                    "content_type": v.content_type,
                    "original_content": v.original_content[:200] + "..."
                    if len(v.original_content or "") > 200
                    else v.original_content,
                    "sanitized_content": v.sanitized_content,
                    "confidence_score": v.confidence_score,
                    "cross_tenant_risk": v.cross_tenant_risk,
                    "resolved": v.resolved,
                    "timestamp": v.created_at.isoformat() if v.created_at else None,
                }
            )

        # Get total count
        total_query = select(func.count(GuardrailViolation.id))
        if resolved is not None:
            total_query = total_query.where(GuardrailViolation.resolved == resolved)

        total_result = await admin_session.db.execute(total_query)
        total_count = total_result.scalar() or 0

        # Get unresolved count
        unresolved_result = await admin_session.db.execute(
            select(func.count(GuardrailViolation.id)).where(GuardrailViolation.resolved.is_(False))
        )
        unresolved_count = unresolved_result.scalar() or 0

        logger.info(
            f"Admin violations retrieved by {current_user.username}: {len(violations)} of {total_count} violations"
        )

        return {
            "violations": violations,
            "total": total_count,
            "unresolved": unresolved_count,
            "showing": len(violations),
            "generated_at": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting guardrail violations: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve guardrail violations"
        )


@router.post("/violations/{violation_id}/resolve")
async def resolve_violation(
    violation_id: str, admin_session: AdminSession = Depends(get_admin_session), current_user: User = Depends(require_admin_role)
) -> dict[str, Any]:
    """Mark a guardrail violation as resolved"""

    try:
        # Find and update the violation
        result = await admin_session.db.execute(
            select(GuardrailViolation).where(
                and_(GuardrailViolation.id == violation_id, GuardrailViolation.space_id == current_user.space_id)
            )
        )
        violation = result.scalar_one_or_none()

        if not violation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Violation not found")

        violation.resolved = True
        violation.resolved_at = datetime.now()
        violation.resolved_by = current_user.id

        await admin_session.db.commit()

        logger.info(f"Violation {violation_id} resolved by {current_user.username}")

        return {"success": True, "message": "Violation marked as resolved", "violation_id": violation_id}

    except Exception as e:
        logger.error(f"Error resolving violation {violation_id}: {e}")
        await admin_session.db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to resolve violation")


@router.get("/portkey/status")
async def get_portkey_status(
    admin_session: AdminSession = Depends(get_admin_session), current_user: User = Depends(require_admin_role)
) -> dict[str, Any]:
    """Get Portkey gateway status and configuration"""

    try:
        import os

        import aiohttp

        portkey_url = os.getenv("PORTKEY_URL", "http://localhost:8787")

        # Check if Portkey is accessible
        try:
            async with aiohttp.ClientSession() as session, session.get(f"{portkey_url}/health", timeout=5) as response:
                if response.status == 200:
                    portkey_status = "healthy"
                    portkey_response = await response.json()
                else:
                    portkey_status = "unhealthy"
                    portkey_response = {"error": f"HTTP {response.status}"}
        except Exception as e:
            portkey_status = "unavailable"
            portkey_response = {"error": str(e)}

        # Get guardrails configuration
        from ...services.portkey_guardrails import guardrails_service

        # Get total guardrail configurations from database for this organization
        try:
            from ..models.guardrail_config import GuardrailConfig

            total_configs = await admin_session.db.execute(
                select(func.count(GuardrailConfig.id)).where(
                    GuardrailConfig.space_id == current_user.space_id, GuardrailConfig.enabled.is_(True)
                )
            )
            total_guardrails = total_configs.scalar() or 0
        except Exception:
            total_guardrails = 0

        guardrails_config = {
            "total_guardrails": total_guardrails,
            "database_driven": True,
            "service_url": guardrails_service.portkey_url,
        }

        return {
            "portkey_status": portkey_status,
            "portkey_url": portkey_url,
            "portkey_response": portkey_response,
            "guardrails_config": guardrails_config,
            "generated_at": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting Portkey status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve Portkey status"
        )


@router.get("/organizations")
async def get_organization_monitoring(
    admin_session: AdminSession = Depends(get_admin_session), current_user: User = Depends(require_admin_role)
) -> dict[str, Any]:
    """Monitor organization assignments and health for beta management"""

    try:
        from ...models.space_membership import SpaceMembership

        # Get all organizations with member counts and details
        org_result = await admin_session.db.execute(select(Space).order_by(Space.created_at.desc()))
        organizations = org_result.scalars().all()

        org_data = []
        total_members = 0
        users_without_orgs = 0

        for org in organizations:
            # Get member count and details for this organization
            member_result = await admin_session.db.execute(
                select(func.count(SpaceMembership.id)).where(SpaceMembership.space_id == org.id)
            )
            member_count = member_result.scalar() or 0
            total_members += member_count

            # Get admin count for this organization
            admin_result = await admin_session.db.execute(
                select(func.count(SpaceMembership.id)).where(
                    and_(SpaceMembership.space_id == org.id, SpaceMembership.role == "admin")
                )
            )
            admin_count = admin_result.scalar() or 0

            # Get agent count for this organization
            agent_result = await admin_session.db.execute(select(func.count(Agent.id)).where(Agent.space_id == org.id))
            agent_count = agent_result.scalar() or 0

            # Get owner information (first admin or creator)
            owner_result = await admin_session.db.execute(
                select(User.username, User.email)
                .join(SpaceMembership, User.id == SpaceMembership.user_id)
                .where(and_(SpaceMembership.space_id == org.id, SpaceMembership.role == "admin"))
                .order_by(SpaceMembership.joined_at.asc())
                .limit(1)
            )
            owner_row = owner_result.first()
            owner_username = owner_row.username if owner_row else "Unknown"
            owner_email = owner_row.email if owner_row else ""

            # Get message count for this organization
            from ...models.message import Message

            message_result = await admin_session.db.execute(select(func.count(Message.id)).where(Message.space_id == org.id))
            message_count = message_result.scalar() or 0

            # Get task count for this organization
            from ...models.task import Task

            task_result = await admin_session.db.execute(select(func.count(Task.id)).where(Task.space_id == org.id))
            task_count = task_result.scalar() or 0

            org_data.append(
                {
                    "id": str(org.id),
                    "name": org.name,
                    "slug": org.slug,
                    "visibility": org.visibility,
                    "member_count": member_count,
                    "admin_count": admin_count,
                    "agent_count": agent_count,
                    "owner_username": owner_username,
                    "owner_email": owner_email,
                    "message_count": message_count,
                    "task_count": task_count,
                    "created_at": org.created_at.isoformat() if org.created_at else None,
                    "description": org.description,
                }
            )

        # Check for users without organization assignments (critical security check)
        users_no_org_result = await admin_session.db.execute(select(func.count(User.id)).where(User.space_id.is_(None)))
        users_without_orgs = users_no_org_result.scalar() or 0

        # Get users with invalid organization references
        users_invalid_org_result = await admin_session.db.execute(
            select(func.count(User.id)).where(and_(User.space_id.isnot(None), ~User.space_id.in_(select(Space.id))))
        )
        users_invalid_orgs = users_invalid_org_result.scalar() or 0

        # Calculate organization health metrics for beta
        total_messages = sum(org_data[i]["message_count"] for i in range(len(org_data)))
        total_tasks = sum(org_data[i]["task_count"] for i in range(len(org_data)))

        org_health = {
            "total_organizations": len(organizations),
            "total_members": total_members,
            "total_messages": total_messages,
            "total_tasks": total_tasks,
            "users_without_orgs": users_without_orgs,
            "users_invalid_orgs": users_invalid_orgs,
            "health_status": "healthy" if (users_without_orgs == 0 and users_invalid_orgs == 0) else "warning",
            "personal_orgs": len([org for org in org_data if org["member_count"] == 1]),
            "multi_member_orgs": len([org for org in org_data if org["member_count"] > 1]),
        }

        logger.info(f"Admin organization monitoring retrieved by {current_user.username}")

        return {
            "organizations": org_data,
            "health": org_health,
            "beta_insights": {
                "most_active_org": max(org_data, key=lambda x: x["agent_count"]) if org_data else None,
                "largest_org": max(org_data, key=lambda x: x["member_count"]) if org_data else None,
                "newest_org": max(org_data, key=lambda x: x["created_at"]) if org_data else None,
            },
            "generated_at": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting organization monitoring: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve organization monitoring data"
        )


# Legacy endpoint for compatibility
@router.get("/team/posts")
async def get_team_posts(admin_session: AdminSession = Depends(get_admin_session), current_user: User = Depends(require_admin_role)):
    """Legacy endpoint for team posts - redirects to activity feed"""
    return {"redirect": "/api/admin/activity", "message": "This endpoint has been moved to /api/admin/activity"}


# =============================================================================
# System Settings Endpoints
# =============================================================================


@router.get("/settings")
async def get_system_settings(current_user: User = Depends(require_admin_role)) -> dict[str, Any]:
    """
    Get dynamic system settings.
    Currently supports: cloud_agent_creation_enabled, agent_limit_plus
    """
    # Use RedisPool.execute() for automatic retry on parser errors
    key = "system:settings:cloud_agent_creation_enabled"
    try:
        raw_value = await RedisPool.execute("get", key)

        # If Redis key exists, it overrides env var. Otherwise use env var default.
        if raw_value is not None:
            # Handle both bytes (from some Redis clients) and str (from others)
            value_str = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
            is_enabled = value_str.lower() == "true"
            source = "dynamic"
        else:
            is_enabled = settings.cloud_agent_creation_enabled
            source = "environment"
    except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError, redis_exceptions.RedisError) as redis_exc:
        # Only catch Redis-related errors, let other errors propagate as 500
        logger.warning(f"Redis unavailable for settings check: {redis_exc}")
        is_enabled = settings.cloud_agent_creation_enabled
        source = "environment (fallback)"

    # Plus agent limit (numeric, dynamic override)
    beta_config = get_beta_config()
    plus_key = "system:settings:agent_limit_plus"
    try:
        raw_plus = await RedisPool.execute("get", plus_key)
        if raw_plus is not None:
            value_str = raw_plus.decode() if isinstance(raw_plus, bytes) else str(raw_plus)
            parsed = int(value_str)
            plus_limit = parsed if parsed > 0 else beta_config.AGENT_LIMIT_PLUS
            plus_source = "dynamic"
        else:
            plus_limit = beta_config.AGENT_LIMIT_PLUS
            plus_source = "environment"
    except (
        ValueError,
        redis_exceptions.ConnectionError,
        redis_exceptions.TimeoutError,
        redis_exceptions.RedisError,
    ) as redis_exc:
        logger.warning(f"Redis unavailable for plus limit check: {redis_exc}")
        plus_limit = beta_config.AGENT_LIMIT_PLUS
        plus_source = "environment (fallback)"

    return {
        "cloud_agent_creation_enabled": is_enabled,
        "source": source,
        "default": settings.cloud_agent_creation_enabled,
        "agent_limit_plus": plus_limit,
        "agent_limit_plus_source": plus_source,
        "agent_limit_plus_default": beta_config.AGENT_LIMIT_PLUS,
    }


@router.patch("/settings")
async def update_system_settings(
    payload: dict[str, Any], current_user: User = Depends(require_admin_role)
) -> dict[str, Any]:
    """
    Update dynamic system settings.
    payload: { "cloud_agent_creation_enabled": boolean, "agent_limit_plus": number|null }
    """
    try:
        key = "system:settings:cloud_agent_creation_enabled"
        plus_key = "system:settings:agent_limit_plus"

        updates = {}

        if "cloud_agent_creation_enabled" in payload:
            value = str(payload["cloud_agent_creation_enabled"]).lower()
            await RedisPool.execute("set", key, value)
            updates["cloud_agent_creation_enabled"] = value == "true"

            logger.info(f"System setting 'cloud_agent_creation_enabled' updated to {value} by {current_user.username}")

        if "agent_limit_plus" in payload:
            raw_limit = payload["agent_limit_plus"]
            if raw_limit is None:
                await RedisPool.execute("delete", plus_key)
                updates["agent_limit_plus"] = None
                logger.info(f"System setting 'agent_limit_plus' cleared by {current_user.username}")
            else:
                try:
                    parsed_limit = int(raw_limit)
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="agent_limit_plus must be a positive integer or null",
                    )
                if parsed_limit <= 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="agent_limit_plus must be a positive integer",
                    )
                if parsed_limit > 1000:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="agent_limit_plus cannot exceed 1000",
                    )
                await RedisPool.execute("set", plus_key, str(parsed_limit))
                updates["agent_limit_plus"] = parsed_limit
                logger.info(f"System setting 'agent_limit_plus' updated to {parsed_limit} by {current_user.username}")

        return {"success": True, "updates": updates}

    except Exception as e:
        logger.error(f"Error updating system settings: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update system settings"
        )


# =============================================================================
# AI Intelligence Flagged Issues Endpoints
# =============================================================================


@router.get("/ai-flagged-issues")
async def get_ai_flagged_issues(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    limit: int = Query(50, ge=1, le=200),
    min_score: float = Query(0.5, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """
    Get messages flagged by AI intelligence for admin review.
    Returns messages with high spam/toxicity scores or low quality scores.

    Thresholds (using min_score parameter):
    - High spam: spam_score > min_score
    - High toxicity: toxicity_score > min_score
    - Low quality: quality_score < (1 - min_score)
    """
    from ...models.message import Message
    from ...models.message_intelligence import MessageIntelligence

    try:
        # Calculate thresholds
        spam_threshold = min_score
        toxicity_threshold = min_score
        quality_threshold = 1 - min_score  # e.g., min_score=0.5 -> quality < 0.5

        # Query flagged messages with intelligence data (including security analysis)
        # Use LEFT OUTER JOINs to get sender/org info in single query (avoids N+1)
        query = (
            select(
                MessageIntelligence.message_id,
                MessageIntelligence.spam_score,
                MessageIntelligence.toxicity_score,
                MessageIntelligence.quality_score,
                MessageIntelligence.security_risk,
                MessageIntelligence.security_type,
                MessageIntelligence.security_reason,
                MessageIntelligence.processed_at,
                Message.content,
                Message.user_id,
                Message.agent_id,
                Message.space_id,
                Agent.name.label("agent_name"),
                User.username.label("user_name"),
                Space.name.label("org_name"),
            )
            .join(Message, MessageIntelligence.message_id == Message.id)
            .outerjoin(Agent, Message.agent_id == Agent.id)
            .outerjoin(User, Message.user_id == User.id)
            .outerjoin(Space, Message.space_id == Space.id)
            .where(
                or_(
                    MessageIntelligence.spam_score > spam_threshold,
                    MessageIntelligence.toxicity_score > toxicity_threshold,
                    MessageIntelligence.quality_score < quality_threshold,
                    MessageIntelligence.security_risk >= 0.4,  # Security threats (>= 0.4)
                )
            )
            .order_by(MessageIntelligence.processed_at.desc())
            .limit(limit)
        )

        result = await admin_session.db.execute(query)
        flagged_rows = result.all()

        # Build response (sender/org info already joined)
        issues = []
        for row in flagged_rows:
            # Determine sender from joined data
            if row.agent_name:
                sender_name = row.agent_name
                sender_type = "agent"
            elif row.user_name:
                sender_name = row.user_name
                sender_type = "user"
            else:
                sender_name = "Unknown"
                sender_type = "unknown"

            issues.append(
                {
                    "message_id": str(row.message_id),
                    "content": row.content,  # Return full content, frontend will truncate
                    "sender_name": sender_name,
                    "sender_type": sender_type,
                    "org_name": row.org_name or "Unknown",
                    "spam_score": row.spam_score or 0.0,
                    "toxicity_score": row.toxicity_score or 0.0,
                    "quality_score": row.quality_score or 0.0,
                    "security_risk": row.security_risk or 0.0,
                    "security_type": row.security_type or "",
                    "security_reason": row.security_reason or "",
                    "processed_at": row.processed_at.isoformat() if row.processed_at else None,
                }
            )

        # Calculate stats (including security threats)
        stats_query = select(
            func.count(MessageIntelligence.message_id).label("total_processed"),
            func.sum(func.cast(MessageIntelligence.spam_score > spam_threshold, Integer)).label("high_spam"),
            func.sum(func.cast(MessageIntelligence.toxicity_score > toxicity_threshold, Integer)).label(
                "high_toxicity"
            ),
            func.sum(func.cast(MessageIntelligence.quality_score < quality_threshold, Integer)).label("low_quality"),
            func.sum(func.cast(MessageIntelligence.security_risk >= 0.4, Integer)).label("security_threats"),
        )
        stats_result = await admin_session.db.execute(stats_query)
        stats_row = stats_result.first()

        stats = {
            "total_processed": stats_row.total_processed or 0,
            "high_spam": stats_row.high_spam or 0,
            "high_toxicity": stats_row.high_toxicity or 0,
            "low_quality": stats_row.low_quality or 0,
            "security_threats": stats_row.security_threats or 0,
        }

        logger.info(
            f"Admin AI flagged issues retrieved by {current_user.username}: "
            f"{len(issues)} issues, {stats['total_processed']} total processed"
        )

        return {
            "issues": issues,
            "total": len(issues),
            "stats": stats,
            "thresholds": {
                "spam": spam_threshold,
                "toxicity": toxicity_threshold,
                "quality": quality_threshold,
            },
            "generated_at": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting AI flagged issues: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve AI flagged issues"
        )


@router.post("/ai-flagged-issues/{message_id}/dismiss")
async def dismiss_ai_flagged_issue(
    message_id: str,
    payload: dict[str, Any],
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
) -> dict[str, Any]:
    """
    Dismiss an AI flagged issue with a reason.
    TODO: Implement proper dismiss tracking in database.
    """
    reason = payload.get("reason", "")

    # For now, just log the dismissal - full implementation would track in DB
    logger.info(f"Admin {current_user.username} dismissed AI flagged message {message_id}: {reason}")

    return {
        "success": True,
        "message_id": message_id,
        "dismissed": True,
        "dismiss_reason": reason,
        "dismissed_by": current_user.username,
        "dismissed_at": datetime.now().isoformat(),
    }


# =============================================================================
# Access Requests (invite-only waitlist gate — admin console)
# =============================================================================


def _serialize_access_request(req: AccessRequest) -> dict[str, Any]:
    """Serialize an AccessRequest row for the admin console."""
    return {
        "id": str(req.id),
        "email": req.email,
        "full_name": req.full_name,
        "github_username": req.github_username,
        "github_id": req.github_id,
        "status": req.status,
        "emailed_at": req.emailed_at.isoformat() if req.emailed_at else None,
        "decided_at": req.decided_at.isoformat() if req.decided_at else None,
        "decided_by": req.decided_by,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "updated_at": req.updated_at.isoformat() if req.updated_at else None,
    }


async def _load_access_request(admin_session: AdminSession, request_id: str) -> AccessRequest:
    """Load an AccessRequest by id; 404 on missing or malformed id."""
    try:
        req_uuid = uuid.UUID(str(request_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found")

    result = await admin_session.db.execute(select(AccessRequest).where(AccessRequest.id == req_uuid))
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access request not found")
    return req


@router.get("/access-requests")
async def list_access_requests(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    status_filter: str = Query("pending", alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List access requests, newest first, with per-status counts.

    `status` is one of pending|approved|denied|all (default pending).
    """
    normalized = (status_filter or "pending").strip().lower()
    if normalized not in (*VALID_REQUEST_STATUSES, "all"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Must be one of: {[*VALID_REQUEST_STATUSES, 'all']}",
        )

    try:
        query = select(AccessRequest)
        if normalized != "all":
            query = query.where(AccessRequest.status == normalized)

        # Total matching the current filter (for pagination)
        count_query = select(func.count()).select_from(query.subquery())
        total = (await admin_session.db.execute(count_query)).scalar() or 0

        # Page of rows, newest first
        page_query = query.order_by(AccessRequest.created_at.desc()).offset(offset).limit(limit)
        rows = (await admin_session.db.execute(page_query)).scalars().all()
        requests = [_serialize_access_request(r) for r in rows]

        # Per-status counts (always across all rows, independent of filter)
        counts_result = await admin_session.db.execute(
            select(AccessRequest.status, func.count(AccessRequest.id)).group_by(AccessRequest.status)
        )
        counts = {"pending": 0, "approved": 0, "denied": 0}
        grand_total = 0
        for row_status, row_count in counts_result.all():
            grand_total += row_count or 0
            if row_status in counts:
                counts[row_status] = row_count or 0

        logger.info(
            f"Admin access-requests listed by {current_user.username}: "
            f"status={normalized}, {len(requests)} of {total}"
        )

        return {
            "requests": requests,
            "status": normalized,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
            "counts": counts,
            "total_all": grand_total,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing access requests: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve access requests"
        )


async def _decide_access_request(
    admin_session: AdminSession,
    current_user: User,
    request_id: str,
    new_status: str,
) -> dict[str, Any]:
    """Shared body for approve/deny/reset transitions."""
    req = await _load_access_request(admin_session, request_id)
    try:
        reactivated = await set_request_status(
            admin_session.db, req, new_status, decided_by=current_user.username
        )
        logger.info(
            f"Access request {req.id} ({req.email}) set to {new_status} by admin "
            f"{current_user.username} (reactivated_user={reactivated})"
        )
        return {
            "success": True,
            "id": str(req.id),
            "email": req.email,
            "status": req.status,
            "reactivated_user": reactivated,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting access request {request_id} to {new_status}: {e}")
        await admin_session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update access request"
        )


@router.post("/access-requests/{request_id}/approve")
async def approve_access_request(
    request_id: str,
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
) -> dict[str, Any]:
    """Approve an access request (and re-enable a matching disabled user)."""
    return await _decide_access_request(admin_session, current_user, request_id, "approved")


@router.post("/access-requests/{request_id}/deny")
async def deny_access_request(
    request_id: str,
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
) -> dict[str, Any]:
    """Deny an access request (blocked at the gate)."""
    return await _decide_access_request(admin_session, current_user, request_id, "denied")


@router.post("/access-requests/{request_id}/reset")
async def reset_access_request(
    request_id: str,
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
) -> dict[str, Any]:
    """Reset an access request back to pending (re-opens for re-evaluation)."""
    return await _decide_access_request(admin_session, current_user, request_id, "pending")


@router.post("/access-requests/rollback-lockdown")
async def rollback_lockdown(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
) -> dict[str, Any]:
    """One-shot release of users locked out by the invite-only gate.

    Reactivates every app-level-disabled user except those whose email
    matches a denied access request, and approves all pending requests.
    Idempotent: a second run returns zeros. Returns the counts.
    """
    try:
        counts = await run_lockdown_rollback(
            admin_session.db,
            decided_by=current_user.username,
            actor_user_id=current_user.id,
        )
        await admin_session.db.commit()
        logger.info(
            f"Lockdown rollback run by admin {current_user.username}: "
            f"users_reactivated={counts['users_reactivated']}, "
            f"requests_approved={counts['requests_approved']}"
        )
        return counts
    except Exception as e:
        logger.error(f"Error running lockdown rollback: {e}")
        await admin_session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run lockdown rollback",
        )


# Cap on each list section of the activity report; totals are reported
# separately so the admin UI can show "showing first N of M".
ACTIVITY_REPORT_MAX_ROWS = 200


@router.get("/activity-report")
async def get_activity_report(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    days: int = Query(7, ge=1, le=365),
) -> dict[str, Any]:
    """On-demand platform activity report for admins.

    Summarizes the last `days` days: new signups (from the user audit trail),
    login event count, recently active users, dormant users (no login within
    `dormant_alert_days`), and pending access requests. Synthetic
    `@github.local` accounts are excluded from both user lists.

    Each list is capped at ACTIVITY_REPORT_MAX_ROWS; the user lists carry
    companion `*_total` counts so the UI can indicate truncation.
    """
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        dormant_days = getattr(settings, "dormant_alert_days", 14)
        dormant_cutoff = now - timedelta(days=dormant_days)
        not_synthetic = ~User.email.like("%@github.local")

        # New signups in period (audit trail), newest first
        signups_result = await admin_session.db.execute(
            select(UserAuditEvent)
            .where(UserAuditEvent.event_type == "user.created", UserAuditEvent.created_at >= cutoff)
            .order_by(UserAuditEvent.created_at.desc())
            .limit(ACTIVITY_REPORT_MAX_ROWS)
        )
        new_signups = []
        for event in signups_result.scalars().all():
            meta = event.metadata_json or {}
            new_signups.append(
                {
                    "username": meta.get("username"),
                    "email": meta.get("email"),
                    "auth_provider": meta.get("auth_provider"),
                    "created_at": _iso(event.created_at),
                }
            )

        # Login events in period
        login_events = (
            await admin_session.db.execute(
                select(func.count(UserAuditEvent.id)).where(
                    UserAuditEvent.event_type == "user.login", UserAuditEvent.created_at >= cutoff
                )
            )
        ).scalar() or 0

        # Active users: logged in within the period, most recent first
        active_filters = (User.active.is_(True), not_synthetic, User.last_login_at >= cutoff)
        active_result = await admin_session.db.execute(
            select(User)
            .where(*active_filters)
            .order_by(User.last_login_at.desc())
            .limit(ACTIVITY_REPORT_MAX_ROWS)
        )
        active_users = [
            {"username": u.username, "email": u.email, "last_login_at": _iso(u.last_login_at)}
            for u in active_result.scalars().all()
        ]
        active_users_total = (
            await admin_session.db.execute(select(func.count(User.id)).where(*active_filters))
        ).scalar() or 0

        # Dormant users: never logged in, or not since dormant_alert_days.
        # Never-logged-in first, then oldest logins (portable null-first ordering).
        dormant_filters = (
            User.active.is_(True),
            not_synthetic,
            or_(User.last_login_at.is_(None), User.last_login_at < dormant_cutoff),
        )
        dormant_result = await admin_session.db.execute(
            select(User)
            .where(*dormant_filters)
            .order_by(User.last_login_at.is_(None).desc(), User.last_login_at.asc())
            .limit(ACTIVITY_REPORT_MAX_ROWS)
        )
        dormant_users = [
            {"username": u.username, "email": u.email, "last_login_at": _iso(u.last_login_at)}
            for u in dormant_result.scalars().all()
        ]
        dormant_users_total = (
            await admin_session.db.execute(select(func.count(User.id)).where(*dormant_filters))
        ).scalar() or 0

        # Pending access requests (point-in-time, not period-scoped)
        pending_access_requests = (
            await admin_session.db.execute(
                select(func.count(AccessRequest.id)).where(AccessRequest.status == "pending")
            )
        ).scalar() or 0

        logger.info(
            f"Admin activity report generated by {current_user.username}: days={days}, "
            f"signups={len(new_signups)}, logins={login_events}, active={active_users_total}, "
            f"dormant={dormant_users_total}, pending_requests={pending_access_requests}"
        )

        return {
            "period_days": days,
            "new_signups": new_signups,
            "login_events": login_events,
            "active_users": active_users,
            "active_users_total": active_users_total,
            "dormant_users": dormant_users,
            "dormant_users_total": dormant_users_total,
            "dormant_days": dormant_days,
            "pending_access_requests": pending_access_requests,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating activity report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate activity report"
        )
