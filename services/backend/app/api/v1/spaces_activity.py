"""
Space Activity Helper Module
Provides activity metrics computation for spaces
"""

import logging

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SpaceActivity(BaseModel):
    """Activity metrics for a space"""

    score: int = Field(0, ge=0, le=100, description="Activity score 0-100")
    state: str = Field("fresh", pattern="^(hot|active|warming|fresh|ice|night|busy)$")
    # Messages
    messages_5m: int = Field(0, ge=0)
    messages_1h: int = Field(0, ge=0)
    messages_6h: int = Field(0, ge=0)
    messages_24h: int = Field(0, ge=0)
    messages_7d: int = Field(0, ge=0)
    messages_30d: int = Field(0, ge=0)
    messages_total: int = Field(0, ge=0)
    threads_active_1h: int = Field(0, ge=0)
    # People & Agents
    active_users_30m: int = Field(0, ge=0)
    active_agents_24h: int = Field(0, ge=0)
    agents_active_24h: int = Field(0, ge=0)
    agent_count: int = Field(0, ge=0)
    user_count: int = Field(0, ge=0)
    new_members_7d: int = Field(0, ge=0)
    agent_runs_1h: int = Field(0, ge=0)
    agent_runs_24h: int = Field(0, ge=0)
    agent_failures_24h: int = Field(0, ge=0)
    # Tasks
    tasks_open: int = Field(0, ge=0)
    tasks_created_24h: int = Field(0, ge=0)
    tasks_closed_24h: int = Field(0, ge=0)
    tasks_total: int = Field(0, ge=0)
    # Momentum & Health
    streak_days_active: int = Field(0, ge=0)
    trend_slope: float = Field(0.0, description="Messages per hour trend")
    reactivated_7d: bool = Field(False, description="Was cold, now warm")
    last_activity_at: str | None = Field(None, description="ISO timestamp of last activity")
    first_activity_at: str | None = Field(None, description="ISO timestamp of first activity")
    engagement_ratio: float = Field(0.0, description="Messages per active user ratio")


# Backward compatibility alias
OrganizationActivity = SpaceActivity


class SpaceMine(BaseModel):
    """User-specific metrics for a space"""

    unread: int = Field(0, ge=0)
    mentions_24h: int = Field(0, ge=0)
    messages_since_last_seen: int = Field(0, ge=0)
    tasks_posted_by_me_open: int = Field(0, ge=0)
    tasks_assigned_to_my_agents_open: int = Field(0, ge=0)


# Backward compatibility alias
OrganizationMine = SpaceMine


async def compute_space_activity(space_id: str, db: AsyncSession) -> SpaceActivity:
    """Compute activity metrics for a space"""
    try:
        query_messages = """
            SELECT
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '5 minutes' THEN 1 END) as messages_5m,
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '1 hour' THEN 1 END) as messages_1h,
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '6 hours' THEN 1 END) as messages_6h,
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as messages_24h,
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '7 days' THEN 1 END) as messages_7d,
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '30 days' THEN 1 END) as messages_30d,
                COUNT(*) as messages_total,
                COUNT(DISTINCT parent_id) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour' AND parent_id IS NOT NULL) as threads_active_1h,
                MAX(created_at) as last_message_at
            FROM messages
            WHERE space_id = :space_id
        """

        query_users = """
            WITH member_users AS (
                SELECT user_id FROM space_memberships WHERE space_id = :space_id
                UNION
                SELECT created_by AS user_id FROM spaces WHERE id = :space_id AND visibility = 'private'
            )
            SELECT COUNT(DISTINCT u.user_id) AS active_users_30m
            FROM member_users u
            LEFT JOIN messages m ON m.user_id = u.user_id AND m.space_id = :space_id
                                 AND m.created_at >= NOW() - INTERVAL '30 minutes'
            LEFT JOIN tasks t ON t.posted_by = u.user_id AND t.space_id = :space_id
                              AND GREATEST(t.created_at, COALESCE(t.updated_at, t.created_at))
                                  >= NOW() - INTERVAL '30 minutes'
            WHERE m.id IS NOT NULL OR t.id IS NOT NULL
        """

        query_active_agents = """
            WITH member_users AS (
                SELECT user_id FROM space_memberships WHERE space_id = :space_id
                UNION
                SELECT created_by AS user_id FROM spaces WHERE id = :space_id AND visibility = 'private'
            )
            SELECT COUNT(DISTINCT m.agent_id) AS active_agents_24h
            FROM messages m
            JOIN agents a ON a.id = m.agent_id
            WHERE m.space_id = :space_id
              AND m.created_at >= NOW() - INTERVAL '24 hours'
              AND a.user_id IN (SELECT user_id FROM member_users)
        """

        query_agents = """
            SELECT COUNT(DISTINCT a.id) AS agent_count
            FROM agents a
            JOIN agent_space_access asa ON asa.agent_id = a.id AND asa.space_id = :space_id
            WHERE a.status = 'active'
        """

        query_user_presence = """
            SELECT COUNT(DISTINCT user_id) as user_count
            FROM (
                SELECT user_id FROM space_memberships WHERE space_id = :space_id
                UNION
                SELECT created_by as user_id FROM spaces WHERE id = :space_id AND visibility = 'private'
            ) users
        """

        query_members = """
            SELECT
                COUNT(CASE WHEN joined_at >= NOW() - INTERVAL '7 days' THEN 1 END) as new_members_7d
            FROM space_memberships
            WHERE space_id = :space_id
        """

        query_tasks = """
            SELECT
                COUNT(CASE WHEN work_status IN ('not_started', 'in_progress') THEN 1 END) as tasks_open,
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as tasks_created_24h,
                COUNT(CASE WHEN work_status = 'completed' AND updated_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as tasks_closed_24h,
                COUNT(*) as tasks_total,
                MAX(updated_at) as last_task_at
            FROM tasks
            WHERE space_id = :space_id
        """

        query_streak = """
            WITH daily_activity AS (
                SELECT DATE(created_at) as activity_date
                FROM messages
                WHERE space_id = :space_id
                  AND created_at >= NOW() - INTERVAL '30 days'
                GROUP BY DATE(created_at)
                ORDER BY activity_date DESC
            ),
            streaks AS (
                SELECT
                    activity_date,
                    activity_date - (ROW_NUMBER() OVER (ORDER BY activity_date DESC))::int * INTERVAL '1 day' as streak_group
                FROM daily_activity
            )
            SELECT COUNT(DISTINCT activity_date) as streak_days
            FROM streaks
            WHERE streak_group = (SELECT MAX(streak_group) FROM streaks)
        """

        query_first_activity = """
            SELECT MIN(earliest) as first_activity_at
            FROM (
                SELECT MIN(created_at) as earliest FROM messages WHERE space_id = :space_id
                UNION ALL
                SELECT MIN(created_at) as earliest FROM tasks WHERE space_id = :space_id
            ) first_times
        """

        # Execute queries
        msg_result = await db.execute(text(query_messages), {"space_id": space_id})
        msg_data = msg_result.first()

        user_result = await db.execute(text(query_users), {"space_id": space_id})
        user_data = user_result.first()

        active_agents_result = await db.execute(text(query_active_agents), {"space_id": space_id})
        active_agents_data = active_agents_result.first()

        agent_result = await db.execute(text(query_agents), {"space_id": space_id})
        agent_data = agent_result.first()

        presence_result = await db.execute(text(query_user_presence), {"space_id": space_id})
        presence_data = presence_result.first()

        members_result = await db.execute(text(query_members), {"space_id": space_id})
        members_data = members_result.first()

        task_result = await db.execute(text(query_tasks), {"space_id": space_id})
        task_data = task_result.first()

        streak_result = await db.execute(text(query_streak), {"space_id": space_id})
        streak_data = streak_result.first()

        first_activity_result = await db.execute(text(query_first_activity), {"space_id": space_id})
        first_activity_data = first_activity_result.first()

        # Extract all values with defaults
        messages_5m = msg_data.messages_5m if msg_data else 0
        messages_1h = msg_data.messages_1h if msg_data else 0
        messages_6h = msg_data.messages_6h if msg_data else 0
        messages_24h = msg_data.messages_24h if msg_data else 0
        messages_7d = msg_data.messages_7d if msg_data else 0
        messages_30d = msg_data.messages_30d if msg_data else 0
        messages_total = msg_data.messages_total if msg_data else 0
        threads_active_1h = msg_data.threads_active_1h if msg_data else 0
        last_message_at = msg_data.last_message_at if msg_data else None

        active_users_30m = user_data.active_users_30m if user_data else 0
        active_agents_24h = active_agents_data.active_agents_24h if active_agents_data else 0
        agents_active_24h = active_agents_24h

        agent_count = agent_data.agent_count if agent_data else 0
        user_count = presence_data.user_count if presence_data else 0
        new_members_7d = members_data.new_members_7d if members_data else 0

        tasks_open = task_data.tasks_open if task_data else 0
        tasks_created_24h = task_data.tasks_created_24h if task_data else 0
        tasks_closed_24h = task_data.tasks_closed_24h if task_data else 0
        tasks_total = task_data.tasks_total if task_data else 0
        last_task_at = task_data.last_task_at if task_data else None

        streak_days_active = streak_data.streak_days if streak_data and streak_data.streak_days else 0
        first_activity_at = first_activity_data.first_activity_at if first_activity_data else None

        engagement_ratio = messages_24h / max(active_users_30m, 1) if active_users_30m > 0 else 0.0

        last_activity = None
        if last_message_at and last_task_at:
            last_activity = max(last_message_at, last_task_at)
        elif last_message_at:
            last_activity = last_message_at
        elif last_task_at:
            last_activity = last_task_at

        def normalize(value, max_val):
            return min(1.0, value / max_val) if max_val > 0 else 0

        score_components = [
            normalize(messages_1h, 1) * 0.25,
            normalize(messages_6h, 2) * 0.20,
            normalize(messages_24h, 5) * 0.15,
            normalize(agent_count, 3) * 0.15,
            normalize(active_users_30m + active_agents_24h, 2) * 0.10,
            normalize(tasks_open + tasks_created_24h, 2) * 0.10,
            normalize(user_count, 2) * 0.05,
        ]

        activity_score = int(sum(score_components) * 100)

        if agent_count > 0:
            activity_score = max(activity_score, 20)
        if messages_24h > 0:
            activity_score = max(activity_score, 30)
        if messages_1h > 0:
            activity_score = max(activity_score, 60)

        if messages_1h > 0 and active_users_30m == 0:
            state = "night"
        elif tasks_created_24h > 2 or (agent_count > 2 and messages_1h > 1):
            state = "busy"
        elif messages_30d == 0:
            state = "fresh"
        elif messages_30d > 0 and messages_7d == 0:
            state = "ice"
        elif activity_score >= 70 or messages_1h > 2:
            state = "hot"
        elif activity_score >= 40 or messages_6h > 2:
            state = "active"
        elif activity_score >= 10 or messages_24h > 0:
            state = "warming"
        else:
            state = "fresh"

        return SpaceActivity(
            score=activity_score,
            state=state,
            messages_5m=messages_5m,
            messages_1h=messages_1h,
            messages_6h=messages_6h,
            messages_24h=messages_24h,
            messages_7d=messages_7d,
            messages_30d=messages_30d,
            messages_total=messages_total,
            threads_active_1h=threads_active_1h,
            active_users_30m=active_users_30m,
            active_agents_24h=active_agents_24h,
            agents_active_24h=agents_active_24h,
            agent_count=agent_count,
            user_count=user_count,
            new_members_7d=new_members_7d,
            agent_runs_1h=0,
            agent_runs_24h=0,
            agent_failures_24h=0,
            tasks_open=tasks_open,
            tasks_created_24h=tasks_created_24h,
            tasks_closed_24h=tasks_closed_24h,
            tasks_total=tasks_total,
            streak_days_active=streak_days_active,
            trend_slope=0.0,
            reactivated_7d=False,
            last_activity_at=last_activity.isoformat() if last_activity else None,
            first_activity_at=first_activity_at.isoformat() if first_activity_at else None,
            engagement_ratio=round(engagement_ratio, 2),
        )

    except Exception as e:
        logger.error(f"Error computing space activity: {e}")
        return SpaceActivity()


# Backward compatibility alias
compute_organization_activity = compute_space_activity


async def compute_user_space_metrics(space_id: str, user_id: str, db: AsyncSession) -> SpaceMine:
    """Compute user-specific metrics for a space"""
    try:
        query_mentions = """
            SELECT
                COUNT(CASE WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 1 END) as mentions_24h
            FROM messages
            WHERE space_id = :space_id
              AND content ILIKE '%@' || (SELECT username FROM users WHERE id = :user_id) || '%'
        """

        query_tasks = """
            SELECT
                COUNT(CASE WHEN t.posted_by = :user_id AND t.work_status IN ('not_started', 'in_progress') THEN 1 END) as tasks_posted_by_me,
                COUNT(CASE WHEN a.user_id = :user_id AND t.assigned_agent_id = a.id AND t.work_status IN ('not_started', 'in_progress') THEN 1 END) as tasks_assigned_to_my_agents
            FROM tasks t
            LEFT JOIN agents a ON t.assigned_agent_id = a.id
            WHERE t.space_id = :space_id
        """

        mentions_result = await db.execute(text(query_mentions), {"space_id": space_id, "user_id": user_id})
        mentions_data = mentions_result.first()

        tasks_result = await db.execute(text(query_tasks), {"space_id": space_id, "user_id": user_id})
        tasks_data = tasks_result.first()

        return SpaceMine(
            unread=0,
            mentions_24h=mentions_data.mentions_24h if mentions_data else 0,
            messages_since_last_seen=0,
            tasks_posted_by_me_open=tasks_data.tasks_posted_by_me if tasks_data else 0,
            tasks_assigned_to_my_agents_open=tasks_data.tasks_assigned_to_my_agents if tasks_data else 0,
        )

    except Exception as e:
        logger.error(f"Error computing user space metrics: {e}")
        return SpaceMine()


# Backward compatibility alias
compute_user_organization_metrics = compute_user_space_metrics
