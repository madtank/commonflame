"""
Admin User Outreach API Endpoints

Provides tools for managing user outreach campaigns and preventing
duplicate contacts across admin team members.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Dict, List, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, text, and_, or_, select, desc
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
import logging
import uuid

from ...core.database import get_db_session
from ...core.rls import AdminSession, get_admin_session
from ...models.user import User
from ...models.agent import Agent
from ...models.message import Message
from ...models.task import Task
from ...models.user_outreach import UserOutreach
from .admin import require_admin_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/outreach", tags=["admin-outreach"])


class OutreachRecordCreate(BaseModel):
    """Request body for creating outreach record"""
    contact_method: str = Field(..., description="How the user was contacted (email, phone, etc.)")
    notes: Optional[str] = Field(None, description="Optional notes about the contact")
    campaign: Optional[str] = Field(None, description="Campaign identifier or reason")
    contacted_at: Optional[datetime] = Field(None, description="When contact occurred (defaults to now)")


@router.get("/users")
async def get_users_for_outreach(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None, description="Search by username or email"),
    activity_filter: Optional[str] = Query(
        None,
        description="Filter by activity level: active, new, dormant, at_risk"
    ),
    contacted_filter: Optional[str] = Query(
        None,
        description="Filter by contact status: contacted, not_contacted, recently_contacted"
    ),
    sort_by: str = Query("created_at", description="Sort field: created_at, last_activity, agent_count, message_count")
) -> Dict[str, Any]:
    """
    Get users for outreach with activity metrics and contact history.

    Provides rich filtering and sorting to identify the right users
    for targeted outreach campaigns.
    """

    try:
        # Build base query
        query = select(User)

        # Apply search filter
        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    User.username.ilike(search_term),
                    User.email.ilike(search_term)
                )
            )

        # Get total count for pagination
        count_query = select(func.count()).select_from(query.subquery())
        count_result = await admin_session.db.execute(count_query)
        total = count_result.scalar() or 0

        # Get users with pagination (we'll filter by activity after enriching data)
        paginated_query = query.offset(offset).limit(limit * 2)  # Get extra to allow for filtering
        result = await admin_session.db.execute(paginated_query)
        users = result.scalars().all()

        # Enrich user data with metrics and outreach history
        user_data = []
        now = datetime.now()
        cutoff_7d = now - timedelta(days=7)
        cutoff_30d = now - timedelta(days=30)

        for user in users:
            # Get agent count
            agent_count_result = await admin_session.db.execute(
                select(func.count(Agent.id)).where(Agent.user_id == user.id)
            )
            agent_count = agent_count_result.scalar() or 0

            # Get message count and last message
            message_count_result = await admin_session.db.execute(
                select(func.count(Message.id), func.max(Message.created_at))
                .where(Message.user_id == user.id)
            )
            message_row = message_count_result.first()
            message_count = message_row[0] or 0 if message_row else 0
            last_message_at = message_row[1] if message_row else None

            # Get task count
            task_count_result = await admin_session.db.execute(
                select(func.count(Task.id)).where(Task.posted_by == user.id)
            )
            task_count = task_count_result.scalar() or 0

            # Calculate activity status
            activity_timestamps = []
            if last_message_at:
                activity_timestamps.append(last_message_at)
            if message_count > 0 or agent_count > 0:
                activity_timestamps.append(user.created_at)

            activity_status = "N/A"
            last_activity = None
            days_since_activity = 999

            if activity_timestamps:
                most_recent = max(activity_timestamps)
                last_activity = most_recent.isoformat() if most_recent else None
                if most_recent:
                    days_since_activity = (now - most_recent.replace(tzinfo=None)).days
                    if days_since_activity == 0:
                        activity_status = "active"
                    elif days_since_activity <= 7:
                        activity_status = "active"
                    elif days_since_activity <= 30:
                        activity_status = "active"
                    else:
                        activity_status = "dormant"

            # Check if user is new (created within last 7 days)
            days_since_creation = (now - user.created_at.replace(tzinfo=None)).days
            if days_since_creation <= 7:
                activity_status = "new"

            # Check if user is at risk (created >7 days ago but no activity)
            if days_since_creation > 7 and message_count == 0 and agent_count == 0:
                activity_status = "at_risk"

            # Get outreach history for this user
            outreach_result = await admin_session.db.execute(
                select(
                    UserOutreach.id,
                    UserOutreach.contact_method,
                    UserOutreach.contacted_at,
                    UserOutreach.notes,
                    UserOutreach.campaign,
                    User.username.label("contacted_by_username")
                )
                .join(User, UserOutreach.contacted_by_user_id == User.id)
                .where(UserOutreach.target_user_id == user.id)
                .order_by(desc(UserOutreach.contacted_at))
                .limit(5)
            )
            outreach_history = []
            last_contacted_at = None
            last_contacted_by = None
            times_contacted = 0

            for outreach_row in outreach_result.fetchall():
                times_contacted += 1
                if last_contacted_at is None:
                    last_contacted_at = outreach_row.contacted_at.isoformat()
                    last_contacted_by = outreach_row.contacted_by_username

                outreach_history.append({
                    "id": str(outreach_row.id),
                    "method": outreach_row.contact_method,
                    "contacted_at": outreach_row.contacted_at.isoformat(),
                    "contacted_by": outreach_row.contacted_by_username,
                    "notes": outreach_row.notes,
                    "campaign": outreach_row.campaign
                })

            # Calculate contact status
            contact_status = "not_contacted"
            if times_contacted > 0:
                contact_status = "contacted"
                # Check if contacted recently (within last 30 days)
                if last_contacted_at:
                    last_contact_dt = datetime.fromisoformat(last_contacted_at.replace('Z', '+00:00'))
                    if (now - last_contact_dt.replace(tzinfo=None)).days <= 30:
                        contact_status = "recently_contacted"

            # Apply activity filter
            if activity_filter and activity_status != activity_filter:
                continue

            # Apply contacted filter
            if contacted_filter and contact_status != contacted_filter:
                continue

            user_data.append({
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
                "role": user.role,
                "status": "active" if user.active else "inactive",
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_activity": last_activity,
                "activity_status": activity_status,
                "agent_count": agent_count,
                "message_count": message_count,
                "task_count": task_count,
                "days_since_activity": days_since_activity,
                "days_since_creation": days_since_creation,
                "outreach": {
                    "times_contacted": times_contacted,
                    "last_contacted_at": last_contacted_at,
                    "last_contacted_by": last_contacted_by,
                    "contact_status": contact_status,
                    "history": outreach_history
                }
            })

        # Sort user data
        if sort_by == "created_at":
            user_data.sort(key=lambda x: x["created_at"], reverse=True)
        elif sort_by == "last_activity":
            user_data.sort(key=lambda x: x["last_activity"] or "", reverse=True)
        elif sort_by == "agent_count":
            user_data.sort(key=lambda x: x["agent_count"], reverse=True)
        elif sort_by == "message_count":
            user_data.sort(key=lambda x: x["message_count"], reverse=True)

        # Limit to requested number
        user_data = user_data[:limit]

        logger.info(f"Outreach user list retrieved by {current_user.username}: {len(user_data)} users")

        return {
            "users": user_data,
            "total": len(user_data),
            "limit": limit,
            "offset": offset,
            "filters_applied": {
                "activity": activity_filter,
                "contacted": contacted_filter,
                "search": search
            }
        }

    except Exception as e:
        logger.error(f"Error getting outreach user list: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve users for outreach"
        )


@router.post("/users/{user_id}/contact")
async def record_user_contact(
    user_id: str,
    outreach_data: OutreachRecordCreate,
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role)
) -> Dict[str, Any]:
    """
    Record that an admin has contacted a user.

    Creates an outreach record that will be visible to all admins,
    preventing duplicate outreach and providing coordination.
    """

    try:
        # Validate user exists
        user_result = await admin_session.db.execute(
            select(User).where(User.id == user_id)
        )
        target_user = user_result.scalar_one_or_none()

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Check for recent contact (within last 24 hours) to warn admins
        recent_cutoff = datetime.now() - timedelta(hours=24)
        recent_contact_result = await admin_session.db.execute(
            select(UserOutreach, User.username)
            .join(User, UserOutreach.contacted_by_user_id == User.id)
            .where(
                and_(
                    UserOutreach.target_user_id == user_id,
                    UserOutreach.contacted_at > recent_cutoff
                )
            )
            .order_by(desc(UserOutreach.contacted_at))
            .limit(1)
        )
        recent_contact = recent_contact_result.first()

        warning = None
        if recent_contact:
            outreach, contacted_by = recent_contact
            warning = {
                "message": "This user was recently contacted",
                "contacted_at": outreach.contacted_at.isoformat(),
                "contacted_by": contacted_by,
                "method": outreach.contact_method,
                "hours_ago": int((datetime.now() - outreach.contacted_at.replace(tzinfo=None)).total_seconds() / 3600)
            }

        # Create outreach record
        outreach_record = UserOutreach(
            id=uuid.uuid4(),
            target_user_id=uuid.UUID(user_id),
            contacted_by_user_id=current_user.id,
            contact_method=outreach_data.contact_method,
            notes=outreach_data.notes,
            campaign=outreach_data.campaign,
            contacted_at=outreach_data.contacted_at or datetime.now()
        )

        admin_session.db.add(outreach_record)
        await admin_session.db.commit()
        await admin_session.db.refresh(outreach_record)

        logger.info(
            f"Outreach recorded by {current_user.username} to user {target_user.username} "
            f"via {outreach_data.contact_method}"
        )

        return {
            "success": True,
            "message": "Contact recorded successfully",
            "outreach_id": str(outreach_record.id),
            "user_id": user_id,
            "username": target_user.username,
            "contacted_at": outreach_record.contacted_at.isoformat(),
            "warning": warning
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording user contact: {e}", exc_info=True)
        await admin_session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record user contact"
        )


@router.get("/users/{user_id}/history")
async def get_user_outreach_history(
    user_id: str,
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role)
) -> Dict[str, Any]:
    """
    Get complete outreach history for a specific user.

    Shows all contacts made by all admins with timestamps and notes.
    """

    try:
        # Validate user exists
        user_result = await admin_session.db.execute(
            select(User).where(User.id == user_id)
        )
        target_user = user_result.scalar_one_or_none()

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Get all outreach records
        outreach_result = await admin_session.db.execute(
            select(
                UserOutreach,
                User.username.label("contacted_by_username"),
                User.email.label("contacted_by_email")
            )
            .join(User, UserOutreach.contacted_by_user_id == User.id)
            .where(UserOutreach.target_user_id == user_id)
            .order_by(desc(UserOutreach.contacted_at))
        )

        history = []
        for outreach, contacted_by_username, contacted_by_email in outreach_result.fetchall():
            history.append({
                "id": str(outreach.id),
                "contact_method": outreach.contact_method,
                "contacted_at": outreach.contacted_at.isoformat(),
                "contacted_by": {
                    "username": contacted_by_username,
                    "email": contacted_by_email
                },
                "notes": outreach.notes,
                "campaign": outreach.campaign,
                "created_at": outreach.created_at.isoformat()
            })

        logger.info(f"Outreach history retrieved by {current_user.username} for user {user_id}")

        return {
            "user": {
                "id": str(target_user.id),
                "username": target_user.username,
                "email": target_user.email
            },
            "total_contacts": len(history),
            "history": history
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting outreach history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve outreach history"
        )


@router.get("/stats")
async def get_outreach_stats(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role)
) -> Dict[str, Any]:
    """
    Get outreach statistics and insights.

    Provides overview of outreach efforts and user segmentation.
    """

    try:
        now = datetime.now()
        cutoff_7d = now - timedelta(days=7)
        cutoff_30d = now - timedelta(days=30)

        # Total users
        total_users_result = await admin_session.db.execute(select(func.count(User.id)))
        total_users = total_users_result.scalar() or 0

        # Users contacted (ever)
        contacted_users_result = await admin_session.db.execute(
            select(func.count(func.distinct(UserOutreach.target_user_id)))
        )
        contacted_users = contacted_users_result.scalar() or 0

        # Users contacted in last 7 days
        contacted_7d_result = await admin_session.db.execute(
            select(func.count(func.distinct(UserOutreach.target_user_id)))
            .where(UserOutreach.contacted_at > cutoff_7d)
        )
        contacted_7d = contacted_7d_result.scalar() or 0

        # Users contacted in last 30 days
        contacted_30d_result = await admin_session.db.execute(
            select(func.count(func.distinct(UserOutreach.target_user_id)))
            .where(UserOutreach.contacted_at > cutoff_30d)
        )
        contacted_30d = contacted_30d_result.scalar() or 0

        # New users (last 7 days)
        new_users_result = await admin_session.db.execute(
            select(func.count(User.id)).where(User.created_at > cutoff_7d)
        )
        new_users = new_users_result.scalar() or 0

        # At-risk users (no activity, not contacted)
        at_risk_users_result = await admin_session.db.execute(
            select(func.count(User.id))
            .where(
                and_(
                    User.created_at < cutoff_7d,
                    ~User.id.in_(select(Agent.user_id).where(Agent.user_id.isnot(None))),
                    ~User.id.in_(select(Message.user_id).where(Message.user_id.isnot(None))),
                    ~User.id.in_(select(UserOutreach.target_user_id))
                )
            )
        )
        at_risk_users = at_risk_users_result.scalar() or 0

        # Outreach by method
        method_stats_result = await admin_session.db.execute(
            select(
                UserOutreach.contact_method,
                func.count(UserOutreach.id).label("count")
            )
            .group_by(UserOutreach.contact_method)
            .order_by(desc(func.count(UserOutreach.id)))
        )
        method_stats = [
            {"method": row.contact_method, "count": row.count}
            for row in method_stats_result.fetchall()
        ]

        # Top outreach admins
        admin_stats_result = await admin_session.db.execute(
            select(
                User.username,
                func.count(UserOutreach.id).label("contacts_made")
            )
            .join(User, UserOutreach.contacted_by_user_id == User.id)
            .group_by(User.id, User.username)
            .order_by(desc(func.count(UserOutreach.id)))
            .limit(5)
        )
        top_admins = [
            {"username": row.username, "contacts_made": row.contacts_made}
            for row in admin_stats_result.fetchall()
        ]

        logger.info(f"Outreach stats retrieved by {current_user.username}")

        return {
            "overview": {
                "total_users": total_users,
                "contacted_users": contacted_users,
                "not_contacted": total_users - contacted_users,
                "contact_rate": round((contacted_users / total_users * 100), 2) if total_users > 0 else 0
            },
            "recent_activity": {
                "contacted_last_7d": contacted_7d,
                "contacted_last_30d": contacted_30d,
                "new_users_7d": new_users
            },
            "user_segments": {
                "at_risk_users": at_risk_users,
                "new_users": new_users
            },
            "outreach_methods": method_stats,
            "top_admins": top_admins,
            "generated_at": now.isoformat()
        }

    except Exception as e:
        logger.error(f"Error getting outreach stats: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve outreach stats"
        )
