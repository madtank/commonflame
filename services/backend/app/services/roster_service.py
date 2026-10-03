"""Roster service for unified human/agent visibility across REST and MCP."""

# @ax:tag area=backend component=roster_service tech=fastapi,postgres,redis guide=backend/AGENT.md

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from redis import exceptions as redis_exceptions
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.agent import Agent
from ..models.space_membership import SpaceMembership
from ..models.user import User
from ..core.agent_toggles import build_enabled_tools_from_agent


def _compute_runtime_location(agent) -> dict:
    """Compute runtime location metadata for an agent based on its origin."""
    origin_map = {
        "space_agent": ("space_agent", "Space Agent"),
        "cloud": ("cloud", "Cloud Agent"),
        "external_gateway": ("remote", "External Agent"),
        "agentcore": ("cloud", "Bedrock AgentCore"),
        "mcp": ("local", "MCP Agent"),
    }
    kind, label = origin_map.get(agent.origin or "cloud", ("cloud", "Cloud Agent"))
    return {"kind": kind, "label": label}


class RosterService:
    """Build and cache roster entries for a space (organization)."""

    CACHE_TTL_SECONDS = 60

    def __init__(self, db: AsyncSession, redis_client) -> None:
        self.db = db
        self.redis = redis_client

    async def ensure_membership(self, space_id: UUID, user_id: UUID) -> None:
        """Ensure the viewer belongs to the organization before showing roster data."""

        result = await self.db.execute(
            select(SpaceMembership.id).where(
                SpaceMembership.space_id == space_id,
                SpaceMembership.user_id == user_id,
            )
        )
        if not result.scalar_one_or_none():
            from fastapi import HTTPException, status  # Local import to avoid cycles

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this workspace",
            )

    async def get_roster(
        self,
        space_id: UUID,
        viewer_id: UUID,
        *,
        entry_type: str | None = None,
        search: str | None = None,
        sort: str = "recent",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return roster entries with optional filtering/pagination.

        Args:
            sort: 'recent' for last active first, 'name' for alphabetical
        """

        cache_key = self._cache_key(space_id, viewer_id)
        entries: list[dict[str, Any]]

        if self.redis:
            try:
                # Add retry logic for transient Redis connection errors
                cached = await self.redis.get(cache_key)
                if cached:
                    try:
                        entries = json.loads(cached)
                    except json.JSONDecodeError:
                        entries = await self._build_roster(space_id)
                else:
                    entries = await self._build_roster(space_id)
                    await self.redis.set(cache_key, json.dumps(entries), ex=self.CACHE_TTL_SECONDS)
            except (AttributeError, redis_exceptions.ConnectionError, redis_exceptions.RedisError) as e:
                # Handle redis-py 5.2.0 _connected AttributeError and Redis connection errors
                # Fallback to direct database query without caching
                logging.warning(f"Redis error (fallback to DB): {type(e).__name__}: {e}")
                entries = await self._build_roster(space_id)
        else:
            entries = await self._build_roster(space_id)

        filtered = self._apply_filters(entries, entry_type=entry_type, search=search, sort=sort)
        total = len(filtered)

        sliced = filtered[offset : offset + limit]

        return {
            "items": sliced,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def invalidate_cache(self, space_id: UUID) -> None:
        """Invalidate cached roster entries for an organization."""

        if not self.redis:
            return

        pattern = f"roster:{space_id}:*"
        keys = [key.decode("utf-8") if isinstance(key, bytes) else key for key in await self.redis.keys(pattern)]
        if keys:
            await self.redis.delete(*keys)

    def _cache_key(self, space_id: UUID, viewer_id: UUID) -> str:
        return f"roster:{space_id}:{viewer_id}"

    def _apply_filters(
        self,
        entries: Iterable[dict[str, Any]],
        *,
        entry_type: str | None = None,
        search: str | None = None,
        sort: str = "recent",
    ) -> list[dict[str, Any]]:
        """Filter and sort roster entries.

        Args:
            sort: 'recent' for last active first (most relevant), 'name' for alphabetical
        """

        filtered = list(entries)

        if entry_type in {"human", "agent"}:
            filtered = [entry for entry in filtered if entry["type"] == entry_type]

        if search:
            query = search.lower().strip()
            filtered = [
                entry
                for entry in filtered
                if query in entry.get("display_name", "").lower()
                or query in entry.get("handle", "").lower()
                or (
                    entry.get("owner_user", {})
                    and query
                    in " ".join(
                        filter(
                            None,
                            [
                                entry["owner_user"].get("display_name"),
                                entry["owner_user"].get("handle"),
                            ],
                        )
                    ).lower()
                )
            ]

        # Sort based on preference
        if sort == "recent":
            # Sort by last_active descending (most recent first)
            # Entries without activity go to the end with fallback date
            filtered.sort(
                key=lambda item: item.get("last_active") or "1970-01-01T00:00:00",
                reverse=True
            )
        else:
            # Alphabetical sort by display name then handle
            filtered.sort(key=lambda item: (item.get("display_name", "").lower(), item.get("handle", "").lower()))

        return filtered

    async def _build_roster(self, space_id: UUID) -> list[dict[str, Any]]:
        """Build roster entries from database statistics."""

        users = await self._fetch_users(space_id)
        agents = await self._fetch_agents(space_id)

        now = datetime.now(UTC)

        user_message_stats = await self._fetch_message_stats(space_id, for_agents=False)
        agent_message_stats = await self._fetch_message_stats(space_id, for_agents=True)

        user_task_stats = await self._fetch_task_stats(space_id, for_agents=False)
        agent_task_stats = await self._fetch_task_stats(space_id, for_agents=True)

        # Fetch AI intelligence scores for agents
        agent_intelligence_stats = await self._fetch_intelligence_stats(space_id)

        entries: list[dict[str, Any]] = []

        for user in users:
            stats_msg = user_message_stats.get(user.id)
            stats_task = user_task_stats.get(user.id)

            last_active = self._compute_last_active(stats_msg, stats_task)
            entries.append(
                {
                    "id": str(user.id),
                    "type": "human",
                    "display_name": user.username or user.full_name,
                    "handle": f"@{user.username}" if user.username else str(user.email),
                    "workspace_id": str(space_id),
                    "owner_user": None,
                    "visibility": None,
                    "presence": self._presence_payload(last_active, now),
                    "trust": self._trust_payload(None),
                    "activity_stats": self._activity_payload(stats_msg, stats_task),
                    "skills": [],
                    "tags": [],
                    "avatar_url": getattr(user, "github_avatar_url", None),
                }
            )

        for agent in agents:
            stats_msg = agent_message_stats.get(agent.id)
            stats_task = agent_task_stats.get(agent.id)
            stats_intel = agent_intelligence_stats.get(agent.id)

            last_active = self._compute_last_active(stats_msg, stats_task)
            capabilities = agent.capabilities if isinstance(agent.capabilities, dict) else {}
            skills = list({skill for skill in capabilities.get("skills", []) if isinstance(skill, str)})
            tags = list({tag for tag in capabilities.get("tags", []) if isinstance(tag, str)})

            owner = agent.user
            owner_payload = None
            if owner:
                owner_payload = {
                    "id": str(owner.id),
                    "display_name": owner.username or owner.full_name,
                    "handle": f"@{owner.username}" if owner.username else owner.email,
                }

            entries.append(
                {
                    "id": str(agent.id),
                    "type": "agent",
                    "display_name": agent.name,
                    "handle": agent.name,
                    "workspace_id": str(space_id),
                    "owner_user": owner_payload,
                    "visibility": agent.visibility_level,
                    "presence": self._presence_payload(last_active, now),
                    "trust": self._trust_payload(agent.reputation_score, stats_intel),
                    "activity_stats": self._activity_payload(stats_msg, stats_task),
                    "skills": skills,
                    "tags": tags,
                    "avatar_url": capabilities.get("avatar_url"),
                    # Tool toggles - use helper from toggle registry (DRY)
                    "enabled_tools": build_enabled_tools_from_agent(agent),
                    # Cloud agent flags
                    "is_cloud_agent": bool(agent.cloud_function_url),
                    "cloud_function_url": agent.cloud_function_url,
                    # Agent directory fields
                    "runtime_location": _compute_runtime_location(agent),
                    "capabilities_list": list((build_enabled_tools_from_agent(agent) or {}).keys()),
                    "capability_summary": (agent.description or "")[:200] or None,
                }
            )

        return entries

    async def _fetch_users(self, space_id: UUID) -> list[User]:
        result = await self.db.execute(select(User).where(User.space_id == space_id).order_by(User.username))
        return result.scalars().all()

    async def _fetch_agents(self, space_id: UUID) -> list[Agent]:
        result = await self.db.execute(
            select(Agent)
            .where(Agent.space_id == space_id)
            .where(Agent.is_internal.is_(False))  # Exclude internal system agents
            .options(selectinload(Agent.user))
            .order_by(Agent.name)
        )
        return result.scalars().all()

    async def _fetch_message_stats(self, space_id: UUID, *, for_agents: bool) -> dict[UUID, dict[str, Any]]:
        """Aggregate message activity for users or agents."""

        id_column = "agent_id" if for_agents else "user_id"
        query = text(
            f"""
            SELECT {id_column} AS actor_id,
                   COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS messages_24h,
                   COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS messages_7d,
                   MAX(created_at) AS last_message_at
            FROM messages
            WHERE space_id = :space_id
              AND {id_column} IS NOT NULL
            GROUP BY {id_column}
            """
        )

        rows = await self.db.execute(query, {"space_id": str(space_id)})
        stats: dict[UUID, dict[str, Any]] = {}
        for row in rows.mappings():
            actor_id = row["actor_id"]
            if actor_id is None:
                continue
            stats[actor_id] = {
                "messages_24h": int(row["messages_24h"] or 0),
                "messages_7d": int(row["messages_7d"] or 0),
                "last_message_at": row["last_message_at"],
            }
        return stats

    async def _fetch_task_stats(self, space_id: UUID, *, for_agents: bool) -> dict[UUID, dict[str, Any]]:
        """Aggregate task activity for users or agents."""

        id_column = "assigned_agent_id" if for_agents else "posted_by"
        query = text(
            f"""
            SELECT {id_column} AS actor_id,
                   COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS tasks_24h,
                   COUNT(*) FILTER (
                       WHERE work_status IN ('not_started','in_progress')
                   ) AS tasks_open,
                   MAX(updated_at) AS last_task_at
            FROM tasks
            WHERE space_id = :space_id
              AND {id_column} IS NOT NULL
            GROUP BY {id_column}
            """
        )

        rows = await self.db.execute(query, {"space_id": str(space_id)})
        stats: dict[UUID, dict[str, Any]] = {}
        for row in rows.mappings():
            actor_id = row["actor_id"]
            if actor_id is None:
                continue
            stats[actor_id] = {
                "tasks_24h": int(row["tasks_24h"] or 0),
                "tasks_open": int(row["tasks_open"] or 0),
                "last_task_at": row["last_task_at"],
            }
        return stats

    async def _fetch_intelligence_stats(self, space_id: UUID) -> dict[UUID, dict[str, Any]]:
        """Aggregate AI intelligence scores + human feedback for agents.

        Returns average quality, spam, toxicity scores, feedback data,
        and a blended trust score that weights human feedback as volume grows.
        """
        query = text(
            """
            SELECT m.agent_id,
                   COUNT(mi.message_id) AS messages_analyzed,
                   AVG(mi.quality_score) AS avg_quality_score,
                   AVG(mi.spam_score) AS avg_spam_score,
                   AVG(mi.toxicity_score) AS avg_toxicity_score,
                   a.feedback_score,
                   a.feedback_count
            FROM messages m
            INNER JOIN message_intelligence mi ON mi.message_id = m.id
            LEFT JOIN agents a ON a.id = m.agent_id
            WHERE m.space_id = :space_id
              AND m.agent_id IS NOT NULL
            GROUP BY m.agent_id, a.feedback_score, a.feedback_count
            """
        )

        rows = await self.db.execute(query, {"space_id": str(space_id)})
        stats: dict[UUID, dict[str, Any]] = {}
        for row in rows.mappings():
            agent_id = row["agent_id"]
            if agent_id is None:
                continue

            quality = float(row["avg_quality_score"] or 0)
            spam = float(row["avg_spam_score"] or 0)
            toxicity = float(row["avg_toxicity_score"] or 0)

            # AI-only trust score
            ai_trust = quality * 0.5 + (1 - spam) * 0.25 + (1 - toxicity) * 0.25

            # Blend with human feedback when available
            feedback_score = float(row["feedback_score"]) if row["feedback_score"] is not None else None
            feedback_count = int(row["feedback_count"] or 0)

            if feedback_score is not None and feedback_count > 0:
                human_trust = (feedback_score + 1) / 2  # normalize [-1,+1] → [0,1]
                feedback_weight = min(feedback_count / 50, 0.4)
                ai_weight = 1.0 - feedback_weight
                trust_score = ai_trust * ai_weight + human_trust * feedback_weight
            else:
                trust_score = ai_trust

            # Fetch feedback up/down counts for display
            feedback_up = 0
            feedback_down = 0
            if feedback_count > 0 and feedback_score is not None:
                # Derive from avg and count: avg = (up - down) / total, up + down = total
                # up = total * (avg + 1) / 2
                feedback_up = round(feedback_count * (feedback_score + 1) / 2)
                feedback_down = feedback_count - feedback_up

            stats[agent_id] = {
                "messages_analyzed": int(row["messages_analyzed"] or 0),
                "avg_quality_score": round(quality, 3) if row["avg_quality_score"] is not None else None,
                "avg_spam_score": round(spam, 3) if row["avg_spam_score"] is not None else None,
                "avg_toxicity_score": round(toxicity, 3) if row["avg_toxicity_score"] is not None else None,
                "trust_score": round(trust_score, 3),
                "human_feedback": {
                    "score": round(feedback_score, 4) if feedback_score is not None else None,
                    "thumbs_up": feedback_up,
                    "thumbs_down": feedback_down,
                    "total": feedback_count,
                },
            }
        return stats

    def _compute_last_active(
        self,
        msg_stats: dict[str, Any] | None,
        task_stats: dict[str, Any] | None,
    ) -> datetime | None:
        """Determine most recent activity timestamp across messages/tasks."""

        timestamps = []
        if msg_stats and msg_stats.get("last_message_at"):
            timestamps.append(msg_stats["last_message_at"])
        if task_stats and task_stats.get("last_task_at"):
            timestamps.append(task_stats["last_task_at"])

        if not timestamps:
            return None

        return max(ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC) for ts in timestamps)

    def _presence_payload(self, last_active: datetime | None, now: datetime) -> dict[str, Any]:
        if not last_active:
            return {"last_active": None, "status": "new"}

        delta = now - last_active
        minutes = int(delta.total_seconds() // 60)

        if delta.total_seconds() <= 300:
            status = "active"
        elif delta.total_seconds() <= 3600:
            status = "recent"
        elif delta.total_seconds() <= 86400:
            status = "quiet"
        else:
            status = "offline"

        return {
            "last_active": last_active.isoformat(),
            "status": status,
            "minutes_since": minutes,
        }

    def _trust_payload(
        self,
        reputation_score: Any | None,
        intel_stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build trust payload with reputation and AI intelligence scores.

        Args:
            reputation_score: Legacy reputation score (0-5 scale)
            intel_stats: AI intelligence aggregates from message_intelligence table
        """
        # Legacy reputation score (0-5 scale)
        if isinstance(reputation_score, Decimal) or isinstance(reputation_score, (int, float)):
            rep_value = float(reputation_score)
        else:
            rep_value = 0.0

        # Determine tier from reputation
        if rep_value >= 4.5:
            tier = "platinum"
        elif rep_value >= 4.0:
            tier = "gold"
        elif rep_value >= 3.0:
            tier = "silver"
        elif rep_value > 0:
            tier = "bronze"
        else:
            tier = "new"

        # Build response with AI intelligence scores if available
        result: dict[str, Any] = {
            "score": round(rep_value, 2),
            "tier": tier,
        }

        if intel_stats:
            # AI-computed trust score (0-1 scale), blended with human feedback
            result["trust_score"] = intel_stats.get("trust_score")
            # Detailed breakdown
            result["avg_quality_score"] = intel_stats.get("avg_quality_score")
            result["avg_spam_score"] = intel_stats.get("avg_spam_score")
            result["avg_toxicity_score"] = intel_stats.get("avg_toxicity_score")
            result["messages_analyzed"] = intel_stats.get("messages_analyzed", 0)
            # Human feedback data
            result["human_feedback"] = intel_stats.get("human_feedback")
        else:
            # No intelligence data yet
            result["trust_score"] = None
            result["avg_quality_score"] = None
            result["avg_spam_score"] = None
            result["avg_toxicity_score"] = None
            result["messages_analyzed"] = 0
            result["human_feedback"] = None

        return result

    def _activity_payload(
        self,
        msg_stats: dict[str, Any] | None,
        task_stats: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "messages_24h": int(msg_stats.get("messages_24h", 0)) if msg_stats else 0,
            "messages_7d": int(msg_stats.get("messages_7d", 0)) if msg_stats else 0,
            "tasks_open": int(task_stats.get("tasks_open", 0)) if task_stats else 0,
            "tasks_24h": int(task_stats.get("tasks_24h", 0)) if task_stats else 0,
        }
