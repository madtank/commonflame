"""
Admin Analytics & Intelligence Endpoints

Provides BI-style insights and data export capabilities for administrators
"""
from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.responses import StreamingResponse
from typing import Dict, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, and_, or_
from datetime import datetime, timedelta, date
import logging
import csv
import io

from ...core.database import get_db_session
from ...core.rls import AdminSession, get_admin_session
from ...models.user import User
from ...models.agent import Agent
from ...models.message import Message
from ...models.task import Task
from ...models.space import Space
from .admin import require_admin_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin-analytics"])


@router.get("/analytics/intelligence")
async def get_platform_intelligence(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role)
) -> Dict[str, Any]:
    """
    Get comprehensive platform intelligence and insights

    Provides BI-style metrics for admins:
    - Growth trends
    - User engagement patterns
    - Agent adoption rates
    - Platform health indicators
    - Actionable insights
    """

    try:
        now = datetime.now()
        today = now.date()

        # === USER GROWTH METRICS ===

        # Total users
        total_users_result = await admin_session.db.execute(select(func.count(User.id)))
        total_users = total_users_result.scalar() or 0

        # Users by timeframe
        cutoffs = {
            "last_24h": now - timedelta(hours=24),
            "last_7d": now - timedelta(days=7),
            "last_30d": now - timedelta(days=30),
            "last_90d": now - timedelta(days=90)
        }

        growth_metrics = {}
        for period, cutoff in cutoffs.items():
            result = await admin_session.db.execute(
                select(func.count(User.id)).where(User.created_at > cutoff)
            )
            growth_metrics[period] = result.scalar() or 0

        # Growth rate calculation
        users_prev_30d_result = await admin_session.db.execute(
            select(func.count(User.id)).where(
                and_(
                    User.created_at <= cutoffs["last_30d"],
                    User.created_at > cutoffs["last_90d"]
                )
            )
        )
        users_prev_30d = users_prev_30d_result.scalar() or 0

        if users_prev_30d > 0:
            growth_rate = ((growth_metrics["last_30d"] - users_prev_30d) / users_prev_30d) * 100
        else:
            growth_rate = 100 if growth_metrics["last_30d"] > 0 else 0

        # === ENGAGEMENT METRICS ===

        # Users with agents
        users_with_agents_result = await admin_session.db.execute(
            select(func.count(func.distinct(Agent.user_id))).where(Agent.user_id.isnot(None))
        )
        users_with_agents = users_with_agents_result.scalar() or 0
        agent_adoption_rate = (users_with_agents / total_users * 100) if total_users > 0 else 0

        # Users with messages
        users_with_messages_result = await admin_session.db.execute(
            select(func.count(func.distinct(Message.user_id))).where(Message.user_id.isnot(None))
        )
        users_with_messages = users_with_messages_result.scalar() or 0
        messaging_adoption_rate = (users_with_messages / total_users * 100) if total_users > 0 else 0

        # Users with tasks
        users_with_tasks_result = await admin_session.db.execute(
            select(func.count(func.distinct(Task.posted_by))).where(Task.posted_by.isnot(None))
        )
        users_with_tasks = users_with_tasks_result.scalar() or 0
        task_adoption_rate = (users_with_tasks / total_users * 100) if total_users > 0 else 0

        # === ACTIVITY METRICS ===

        # Total platform activity
        total_messages_result = await admin_session.db.execute(select(func.count(Message.id)))
        total_messages = total_messages_result.scalar() or 0

        total_tasks_result = await admin_session.db.execute(select(func.count(Task.id)))
        total_tasks = total_tasks_result.scalar() or 0

        total_agents_result = await admin_session.db.execute(select(func.count(Agent.id)))
        total_agents = total_agents_result.scalar() or 0

        # Recent activity (last 24h)
        messages_24h_result = await admin_session.db.execute(
            select(func.count(Message.id)).where(Message.created_at > cutoffs["last_24h"])
        )
        messages_24h = messages_24h_result.scalar() or 0

        tasks_24h_result = await admin_session.db.execute(
            select(func.count(Task.id)).where(Task.created_at > cutoffs["last_24h"])
        )
        tasks_24h = tasks_24h_result.scalar() or 0

        # === TOP CONTRIBUTORS ===

        # Most active users by message count
        top_messagers_result = await admin_session.db.execute(
            select(
                User.username,
                func.count(Message.id).label("message_count")
            )
            .join(Message, User.id == Message.user_id)
            .group_by(User.id, User.username)
            .order_by(func.count(Message.id).desc())
            .limit(10)
        )
        top_messagers = [
            {"username": row.username, "count": row.message_count}
            for row in top_messagers_result.fetchall()
        ]

        # Most prolific agent creators
        top_agent_creators_result = await admin_session.db.execute(
            select(
                User.username,
                func.count(Agent.id).label("agent_count")
            )
            .join(Agent, User.id == Agent.user_id)
            .group_by(User.id, User.username)
            .order_by(func.count(Agent.id).desc())
            .limit(10)
        )
        top_agent_creators = [
            {"username": row.username, "count": row.agent_count}
            for row in top_agent_creators_result.fetchall()
        ]

        # === USER COHORTS ===

        # User cohort analysis by signup date
        cohorts = {}
        cohort_definitions = {
            "new_users": (cutoffs["last_7d"], "Last 7 days"),
            "recent_users": (cutoffs["last_30d"], "Last 30 days"),
            "established_users": (cutoffs["last_90d"], "Last 90 days"),
            "early_adopters": (datetime(2020, 1, 1), "All time")
        }

        for cohort_name, (cutoff, label) in cohort_definitions.items():
            cohort_count_result = await admin_session.db.execute(
                select(func.count(User.id)).where(User.created_at > cutoff)
            )
            cohorts[cohort_name] = {
                "label": label,
                "count": cohort_count_result.scalar() or 0
            }

        # === AT-RISK USERS ===

        # Users who signed up but never created agents or messages
        inactive_users_result = await admin_session.db.execute(
            select(User.id)
            .where(
                and_(
                    ~User.id.in_(select(Agent.user_id).where(Agent.user_id.isnot(None))),
                    ~User.id.in_(select(Message.user_id).where(Message.user_id.isnot(None))),
                    User.created_at < cutoffs["last_7d"]
                )
            )
        )
        at_risk_count = len(inactive_users_result.fetchall())

        # === POWER USERS ===

        # Users with >10 agents OR >100 messages
        power_users_result = await admin_session.db.execute(
            select(User.username)
            .join(Agent, User.id == Agent.user_id, isouter=True)
            .join(Message, User.id == Message.user_id, isouter=True)
            .group_by(User.id, User.username)
            .having(
                or_(
                    func.count(func.distinct(Agent.id)) > 10,
                    func.count(func.distinct(Message.id)) > 100
                )
            )
        )
        power_users = [row.username for row in power_users_result.fetchall()]

        # === DAILY ACTIVITY TREND (last 30 days) ===

        daily_activity = []
        for i in range(30):
            day = today - timedelta(days=i)
            day_start = datetime.combine(day, datetime.min.time())
            day_end = datetime.combine(day, datetime.max.time())

            messages_result = await admin_session.db.execute(
                select(func.count(Message.id)).where(
                    and_(
                        Message.created_at >= day_start,
                        Message.created_at <= day_end
                    )
                )
            )
            daily_messages = messages_result.scalar() or 0

            tasks_result = await admin_session.db.execute(
                select(func.count(Task.id)).where(
                    and_(
                        Task.created_at >= day_start,
                        Task.created_at <= day_end
                    )
                )
            )
            daily_tasks = tasks_result.scalar() or 0

            daily_activity.append({
                "date": day.isoformat(),
                "messages": daily_messages,
                "tasks": daily_tasks
            })

        daily_activity.reverse()  # Oldest to newest

        # === INSIGHTS & RECOMMENDATIONS ===

        insights = []

        # Growth insight
        if growth_rate > 20:
            insights.append({
                "type": "positive",
                "title": "Strong Growth",
                "description": f"User base grew {growth_rate:.1f}% in last 30 days",
                "action": "Consider scaling infrastructure"
            })
        elif growth_rate < 0:
            insights.append({
                "type": "warning",
                "title": "Negative Growth",
                "description": f"User base declined {abs(growth_rate):.1f}% in last 30 days",
                "action": "Review user retention strategies"
            })

        # Engagement insight
        if agent_adoption_rate < 30:
            insights.append({
                "type": "warning",
                "title": "Low Agent Adoption",
                "description": f"Only {agent_adoption_rate:.1f}% of users have created agents",
                "action": "Improve onboarding and agent creation flow"
            })

        # At-risk users insight
        if at_risk_count > 10:
            insights.append({
                "type": "warning",
                "title": "At-Risk Users",
                "description": f"{at_risk_count} users signed up but never engaged",
                "action": "Send re-engagement emails or offer support"
            })

        # Power users insight
        if len(power_users) > 0:
            insights.append({
                "type": "positive",
                "title": "Power Users",
                "description": f"{len(power_users)} highly engaged users driving platform activity",
                "action": "Consider reaching out for testimonials or case studies"
            })

        # Activity insight
        avg_daily_messages = sum(d["messages"] for d in daily_activity) / 30
        if avg_daily_messages < 10:
            insights.append({
                "type": "info",
                "title": "Low Daily Activity",
                "description": f"Average {avg_daily_messages:.1f} messages/day",
                "action": "Encourage more collaboration and communication"
            })

        logger.info(f"Platform intelligence generated by {current_user.username}")

        return {
            "growth": {
                "total_users": total_users,
                "new_users_24h": growth_metrics["last_24h"],
                "new_users_7d": growth_metrics["last_7d"],
                "new_users_30d": growth_metrics["last_30d"],
                "growth_rate_30d": round(growth_rate, 2)
            },
            "engagement": {
                "agent_adoption_rate": round(agent_adoption_rate, 2),
                "messaging_adoption_rate": round(messaging_adoption_rate, 2),
                "task_adoption_rate": round(task_adoption_rate, 2),
                "users_with_agents": users_with_agents,
                "users_with_messages": users_with_messages,
                "users_with_tasks": users_with_tasks
            },
            "activity": {
                "total_messages": total_messages,
                "total_tasks": total_tasks,
                "total_agents": total_agents,
                "messages_24h": messages_24h,
                "tasks_24h": tasks_24h,
                "avg_daily_messages": round(avg_daily_messages, 2)
            },
            "top_contributors": {
                "top_messagers": top_messagers,
                "top_agent_creators": top_agent_creators
            },
            "cohorts": cohorts,
            "user_segments": {
                "power_users": len(power_users),
                "at_risk_users": at_risk_count,
                "active_users": users_with_messages + users_with_agents
            },
            "daily_activity_trend": daily_activity,
            "insights": insights,
            "generated_at": now.isoformat()
        }

    except Exception as e:
        logger.error(f"Error generating platform intelligence: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate platform intelligence"
        )


@router.get("/users/export")
async def export_users_csv(
    admin_session: AdminSession = Depends(get_admin_session),
    current_user: User = Depends(require_admin_role)
):
    """
    Export all users to CSV with emails and key metrics

    Perfect for marketing campaigns and user analysis
    """

    try:
        # Get all users with their metrics
        result = await admin_session.db.execute(
            select(User).order_by(User.created_at.desc())
        )
        users = result.scalars().all()

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow([
            "Username",
            "Email",
            "Role",
            "Status",
            "Created At",
            "GitHub Username",
            "Auth Provider"
        ])

        # Write user data
        for user in users:
            writer.writerow([
                user.username,
                user.email,
                user.role,
                "active" if user.active else "inactive",
                user.created_at.isoformat() if user.created_at else "",
                user.github_username or "",
                user.auth_provider or "github"
            ])

        # Prepare response
        output.seek(0)

        logger.info(f"User export generated by {current_user.username}: {len(users)} users")

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=ax-users-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            }
        )

    except Exception as e:
        logger.error(f"Error exporting users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export users"
        )
