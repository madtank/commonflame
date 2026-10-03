"""Notification service powering the MCP bell experience without schema changes."""

# @ax:tag area=backend component=notifications_service tech=fastapi,redis guide=backend/AGENT.md

from __future__ import annotations

import logging
import re
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    import redis.asyncio as redis
    from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agent import Agent
from ..models.message import Message
from ..models.space_membership import SpaceMembership
from ..models.task import Task
from ..models.user import User
from ..utils.ulid import generate_ulid

DEFAULT_KINDS: Sequence[str] = ("mention", "assignment", "task_reminder", "task_completed", "task_blocked")
EVENT_TTL_SECONDS = 30 * 24 * 3600  # 30 days


class NotificationsService:
    """Encapsulates notification creation, listing, and acknowledgement using Redis."""

    def __init__(
        self,
        db: AsyncSession,
        redis_client: redis.Redis | None = None,
        *,
        ttl_seconds: int = EVENT_TTL_SECONDS,
    ) -> None:
        self.db = db
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds
        self.logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Redis key helpers
    #
    # All keys are scoped by space_id to prevent cross-space notification
    # bleed.  The event hash key is globally unique (keyed by ULID) but
    # the sorted-set indexes are per-agent-per-space.
    # ------------------------------------------------------------------

    @staticmethod
    def _events_key(agent_id: uuid.UUID, space_id: uuid.UUID | None = None) -> str:
        if space_id:
            return f"notif:events:{space_id}:{agent_id}"
        return f"notif:events:{agent_id}"

    @staticmethod
    def _unseen_key(agent_id: uuid.UUID, space_id: uuid.UUID | None = None) -> str:
        if space_id:
            return f"notif:unseen:{space_id}:{agent_id}"
        return f"notif:unseen:{agent_id}"

    @staticmethod
    def _event_hash_key(event_id: str) -> str:
        return f"notif:event:{event_id}"

    @staticmethod
    def _count_key(agent_id: uuid.UUID, space_id: uuid.UUID | None = None) -> str:
        if space_id:
            return f"notif:count:{space_id}:{agent_id}"
        return f"notif:count:{agent_id}"

    @staticmethod
    def _last_seen_key(agent_id: uuid.UUID, space_id: uuid.UUID | None = None) -> str:
        if space_id:
            return f"notif:last_seen:{space_id}:{agent_id}"
        return f"notif:last_seen:{agent_id}"

    # ------------------------------------------------------------------
    # Creation helpers
    # ------------------------------------------------------------------

    async def record_mentions(
        self,
        *,
        space_id: uuid.UUID,
        message: Message,
        mentioned_handles: Iterable[str],
        actor_agent_id: uuid.UUID | None,
    ) -> list[dict[str, str]]:
        """Create notifications for mentioned agents/users using Redis storage and database."""

        handles = {handle.lower().strip() for handle in mentioned_handles if handle}
        if not handles:
            return []

        entity_map = await self._resolve_handles(space_id=space_id, handles=handles)
        self.logger.debug(f"_resolve_handles for {handles} in {space_id} returned: {entity_map.keys()}")
        created_events: list[dict[str, str]] = []

        # Store mentions in database for persistent tracking
        try:
            from ..services.mentions_service import MentionsService

            mentions_service = MentionsService(self.db)

            for entity in entity_map.values():
                if not isinstance(entity, Agent):
                    continue

                # Create mention record in database (Agents only)
                try:
                    await mentions_service.create_mention(
                        message_id=message.id,
                        mentioned_agent_id=entity.id,
                        mentioning_agent_id=actor_agent_id,
                        space_id=space_id,
                    )
                    self.logger.debug(f"Created database mention for agent {entity.name}")
                except Exception as e:
                    self.logger.warning(f"Failed to store mention in database: {e}")

            # Commit database changes
            await self.db.commit()
        except Exception as e:
            self.logger.error(f"Failed to process database mentions: {e}")
            # Continue with Redis notifications even if database fails

        # IMPORTANT: Only create alerts for User mentions, not Agent mentions
        # Agent-to-agent mentions should not alert the user (reduces noise)
        # Database mentions above are still stored for tracking/history
        for entity in entity_map.values():
            # Skip creating alerts for Agent mentions - only alert on @user mentions
            if isinstance(entity, Agent):
                self.logger.debug(f"Skipping alert for agent mention: {entity.name}")
                continue

            # Only create alerts for User entities (explicit type check for safety)
            if not isinstance(entity, User):
                self.logger.warning(f"Unexpected entity type in mention resolution: {type(entity)}")
                continue

            # Create notification/alert for User mentions only
            event = await self._store_event(
                target=entity,
                kind="mention",
                space_id=space_id,
                message_id=message.id,
                task_id=None,
                actor_agent_id=actor_agent_id,
                created_at=getattr(message, "created_at", None),
            )
            if event:
                created_events.append(event)
                self.logger.debug(f"Created alert for user mention: {getattr(entity, 'username', entity.id)}")

        return created_events

    async def _send_mcp_mention_notification(
        self,
        *,
        agent: Agent,
        message: Message,
        space_id: uuid.UUID,
        actor_agent_id: uuid.UUID | None,
    ) -> None:
        """Send MCP push notification for a mention."""
        # MCP Modular service has been removed
        # This is a stub to prevent import errors until notification system is refactored
        self.logger.info(f"Skipping MCP notification for agent {agent.name} (mcp_modular removed)")
        return

    async def record_assignment(
        self,
        *,
        space_id: uuid.UUID,
        task: Task,
        assignee: Agent | None,
        actor_agent_id: uuid.UUID | None,
        message_id: uuid.UUID | None = None,
    ) -> dict[str, str] | None:
        """Create an assignment notification for the given agent."""

        if assignee is None:
            return None

        return await self._store_event(
            target=assignee,
            kind="assignment",
            space_id=space_id,
            message_id=message_id,
            task_id=task.id,
            actor_agent_id=actor_agent_id,
            created_at=getattr(task, "updated_at", None) or getattr(task, "created_at", None),
        )

    async def record_task_reminder(
        self,
        *,
        space_id: uuid.UUID,
        task: Task,
        assignee: Agent | User | None,
        message_id: uuid.UUID | None = None,
    ) -> dict[str, str] | None:
        """Create a due-task reminder notification for an assigned active task."""

        if assignee is None:
            return None

        return await self._store_event(
            target=assignee,
            kind="task_reminder",
            space_id=space_id,
            message_id=message_id,
            task_id=task.id,
            actor_agent_id=None,
            created_at=datetime.now(UTC),
        )

    async def record_task_completed(
        self,
        *,
        space_id: uuid.UUID,
        task: Task,
        target: Agent | User,
    ) -> dict[str, str] | None:
        """Create a completion notification for the task creator/owner."""

        return await self._store_event(
            target=target,
            kind="task_completed",
            space_id=space_id,
            message_id=None,
            task_id=task.id,
            actor_agent_id=task.assigned_agent_id,
            created_at=getattr(task, "completed_at", None) or datetime.now(UTC),
        )

    async def record_task_blocked(
        self,
        *,
        space_id: uuid.UUID,
        task: Task,
        target: Agent | User,
    ) -> dict[str, str] | None:
        """Create a blocked-task notification for task owner activity streams."""

        return await self._store_event(
            target=target,
            kind="task_blocked",
            space_id=space_id,
            message_id=None,
            task_id=task.id,
            actor_agent_id=task.assigned_agent_id,
            created_at=getattr(task, "updated_at", None) or datetime.now(UTC),
        )

    async def _store_event(
        self,
        *,
        target: Agent | User,
        kind: str,
        space_id: uuid.UUID,
        message_id: uuid.UUID | None,
        task_id: uuid.UUID | None,
        actor_agent_id: uuid.UUID | None,
        created_at: datetime | None,
    ) -> dict[str, str] | None:
        """Persist an event to Redis (if available) and return its payload."""

        created_at = created_at or datetime.now(UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        event_id = generate_ulid()
        payload: dict[str, str] = {
            "id": event_id,
            "kind": kind,
            "space_id": str(space_id),
            "created_at": created_at.isoformat(),
        }

        if isinstance(target, Agent) and target.user_id:
            payload["owner_user_id"] = str(target.user_id)
        elif isinstance(target, User):
            payload["owner_user_id"] = str(target.id)

        if message_id:
            payload["message_id"] = str(message_id)
        if task_id:
            payload["task_id"] = str(task_id)
        if actor_agent_id:
            payload["actor_agent_id"] = str(actor_agent_id)

        if not self.redis:
            return payload

        score = created_at.timestamp()
        target_id = target.id
        events_key = self._events_key(target_id, space_id)
        unseen_key = self._unseen_key(target_id, space_id)
        hash_key = self._event_hash_key(event_id)
        count_key = self._count_key(target_id, space_id)

        mapping = {k: v for k, v in payload.items() if v is not None}
        if isinstance(target, Agent):
            mapping["agent_id"] = str(target.id)
        else:
            mapping["user_id"] = str(target.id)

        pipe = self.redis.pipeline()
        pipe.hset(hash_key, mapping=mapping)
        pipe.expire(hash_key, self.ttl_seconds)
        pipe.zadd(events_key, {event_id: score})
        pipe.expire(events_key, self.ttl_seconds)
        pipe.zadd(unseen_key, {event_id: score})
        pipe.expire(unseen_key, self.ttl_seconds)
        pipe.incr(count_key)
        pipe.expire(count_key, self.ttl_seconds)

        try:
            await pipe.execute()
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.warning("Failed to persist notification event %s: %s", event_id, exc)

        return payload

    async def _resolve_handles(
        self,
        *,
        space_id: uuid.UUID,
        handles: Iterable[str],
    ) -> dict[str, Agent | User]:
        """Map mention handles to agents/users scoped to a space."""

        normalized_handles = {h for h in handles if h}
        if not normalized_handles:
            return {}

        # Performance optimization: Fetch agents whose normalized names could match handles
        # Uses PostgreSQL regexp_replace to strip non-alphanumeric chars (same as _candidate_handles)
        from sqlalchemy import func, literal, or_

        # Build filters that match the collapsed/underscored name transformations
        name_filters = []
        for handle in normalized_handles:
            # Match agents where regexp_replace(lower(name), '[^a-z0-9]', '', 'g') = handle
            # This replicates the "collapsed" logic from _candidate_handles
            name_filters.append(
                func.regexp_replace(func.lower(Agent.name), literal("[^a-z0-9]"), literal(""), literal("g"))
                == handle.lower()
            )
            # Also allow exact match on the name (case-insensitive) for handles with underscores
            name_filters.append(func.lower(Agent.name) == handle.lower())

        agents_result = await self.db.execute(
            select(Agent).where(Agent.space_id == space_id, or_(*name_filters) if name_filters else True)
        )
        agents = agents_result.scalars().all()

        # Fetch Users (org-scoped via membership; also helps satisfy RLS in production)
        user_filters = []
        for handle in normalized_handles:
            user_filters.append(func.lower(User.username) == handle.lower())

        users_result = await self.db.execute(
            select(User)
            .join(SpaceMembership, SpaceMembership.user_id == User.id)
            .where(
                SpaceMembership.space_id == space_id,
                or_(*user_filters) if user_filters else True,
            )
        )
        users = users_result.scalars().all()
        user_by_username = {(u.username or "").strip().lower(): u for u in users if getattr(u, "username", None)}

        handle_map: dict[str, Agent | User] = {}
        uuid_matches: list[uuid.UUID] = []
        for token in normalized_handles:
            try:
                uuid_matches.append(uuid.UUID(token))
            except ValueError:
                continue

        uuid_index: dict[uuid.UUID, Agent | User] = {}
        if uuid_matches:
            uuid_agents = await self.db.execute(select(Agent).where(Agent.id.in_(uuid_matches)))
            for agent in uuid_agents.scalars().all():
                uuid_index[agent.id] = agent

            uuid_users = await self.db.execute(
                select(User)
                .join(SpaceMembership, SpaceMembership.user_id == User.id)
                .where(SpaceMembership.space_id == space_id, User.id.in_(uuid_matches))
            )
            for user in uuid_users.scalars().all():
                uuid_index[user.id] = user

        for token in normalized_handles:
            try:
                token_uuid = uuid.UUID(token)
                entity = uuid_index.get(token_uuid)
                if entity:
                    handle_map[token] = entity
                continue
            except ValueError:
                pass

            user = user_by_username.get(token.strip().lower())
            if user:
                handle_map[token] = user
                continue

            for agent in agents:
                for candidate in self._candidate_handles(agent):
                    if token == candidate:
                        handle_map[token] = agent
                        break
                if token in handle_map:
                    break

        return handle_map

    @staticmethod
    def _candidate_handles(agent: Agent) -> Sequence[str]:
        values = [agent.name or ""]
        unique_handles = set()
        for value in values:
            if not value:
                continue
            collapsed = re.sub(r"[^a-z0-9]", "", value.lower())
            underscored = re.sub(r"[^a-z0-9]", "_", value.lower())
            if collapsed:
                unique_handles.add(collapsed)
            underscored = underscored.strip("_")
            if underscored:
                unique_handles.add(underscored)
        return tuple(unique_handles)

    # ------------------------------------------------------------------
    # Listing helpers
    # ------------------------------------------------------------------

    async def list_notifications(
        self,
        *,
        agent_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        kinds: Sequence[str] | None = None,
        since: datetime | None = None,
        since_id: str | None = None,
        space_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        """Return ordered notifications with unseen stats."""

        kinds = tuple((k or "").lower() for k in (kinds or DEFAULT_KINDS))
        limit = max(1, min(limit or 50, 100))

        if not self.redis:
            unseen = await self.get_unseen_count(agent_id=agent_id, owner_user_id=owner_user_id, space_id=space_id)
            return {
                "items": [],
                "next_cursor": None,
                "stats": {"unseen_count": unseen},
            }

        fetch_amount = min(limit * 4, 400)
        min_score = since.timestamp() if since else "-inf"

        raw_events = await self.redis.zrevrangebyscore(
            self._events_key(agent_id, space_id),
            "+inf",
            min_score,
            start=0,
            num=fetch_amount,
            withscores=True,
        )

        if not raw_events:
            unseen = await self.get_unseen_count(agent_id=agent_id, owner_user_id=owner_user_id, space_id=space_id)
            return {
                "items": [],
                "next_cursor": None,
                "stats": {"unseen_count": unseen},
            }

        # Load metadata for candidates via pipeline
        meta_pipe = self.redis.pipeline()
        event_candidates: list[tuple[str, float]] = []
        for raw_id, score in raw_events:
            event_id = raw_id if isinstance(raw_id, str) else raw_id.decode("utf-8")
            if since_id and event_id == since_id:
                continue
            meta_pipe.hgetall(self._event_hash_key(event_id))
            event_candidates.append((event_id, float(score)))

        meta_results = await meta_pipe.execute()

        filtered_events: list[dict[str, object]] = []
        for (event_id, score), meta in zip(event_candidates, meta_results, strict=False):
            if not meta:
                continue
            event = self._hydrate_event(event_id, score, meta)
            if since and event["created_at"] < since:
                continue
            if kinds and event["kind"] not in kinds:
                continue
            if space_id and event.get("space_id") and uuid.UUID(event["space_id"]) != space_id:
                continue
            filtered_events.append(event)
            if len(filtered_events) >= limit:
                break

        if not filtered_events:
            unseen = await self.get_unseen_count(agent_id=agent_id, owner_user_id=owner_user_id, space_id=space_id)
            return {
                "items": [],
                "next_cursor": None,
                "stats": {"unseen_count": unseen},
            }

        enriched = await self._enrich_events(filtered_events)
        has_more = len(filtered_events) == limit and len(event_candidates) > len(filtered_events)
        next_cursor = filtered_events[-1]["id"] if has_more else None

        unseen = await self.get_unseen_count(agent_id=agent_id, owner_user_id=owner_user_id, space_id=space_id)
        return {
            "items": enriched,
            "next_cursor": next_cursor,
            "stats": {"unseen_count": unseen},
        }

    def _hydrate_event(
        self,
        event_id: str,
        score: float,
        meta: dict[str, str],
    ) -> dict[str, object]:
        """Normalize metadata fetched from Redis."""

        created_at: datetime | None = None
        if meta.get("created_at"):
            try:
                created_at = datetime.fromisoformat(meta["created_at"])
            except ValueError:
                created_at = None
        if created_at is None:
            created_at = datetime.fromtimestamp(score, tz=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        payload: dict[str, object] = {
            "id": event_id,
            "kind": (meta.get("kind") or "mention").lower(),
            "created_at": created_at,
        }
        for field in ("space_id", "message_id", "task_id", "actor_agent_id", "owner_user_id"):
            value = meta.get(field)
            if value:
                payload[field] = value
        return payload

    async def _enrich_events(self, events: list[dict[str, object]]) -> list[dict[str, object]]:
        """Fetch related models to populate message/task details."""

        message_ids: list[uuid.UUID] = []
        task_ids: list[uuid.UUID] = []
        actor_ids: list[uuid.UUID] = []

        for event in events:
            message_id = event.get("message_id")
            if message_id:
                with suppress(ValueError):
                    message_ids.append(uuid.UUID(str(message_id)))

            task_id = event.get("task_id")
            if task_id:
                with suppress(ValueError):
                    task_ids.append(uuid.UUID(str(task_id)))

            actor_agent_id = event.get("actor_agent_id")
            if actor_agent_id:
                with suppress(ValueError):
                    actor_ids.append(uuid.UUID(str(actor_agent_id)))

        message_map: dict[str, Message] = {}
        if message_ids:
            stmt = (
                select(Message)
                .options(selectinload(Message.agent), selectinload(Message.user))
                .where(Message.id.in_(message_ids))
            )
            result = await self.db.execute(stmt)
            for message in result.scalars().all():
                message_map[str(message.id)] = message

        task_map: dict[str, Task] = {}
        if task_ids:
            stmt = select(Task).where(Task.id.in_(task_ids))
            result = await self.db.execute(stmt)
            for task in result.scalars().all():
                task_map[str(task.id)] = task

        actor_map: dict[str, Agent] = {}
        if actor_ids:
            stmt = select(Agent).where(Agent.id.in_(actor_ids))
            result = await self.db.execute(stmt)
            for agent in result.scalars().all():
                actor_map[str(agent.id)] = agent

        serialized: list[dict[str, object]] = []
        for event in events:
            created_at = event["created_at"]
            created_iso = (
                created_at.astimezone(UTC).isoformat() if isinstance(created_at, datetime) else str(created_at)
            )
            payload: dict[str, object] = {
                "id": event["id"],
                "kind": event["kind"],
                "created_at": created_iso,
                "space_id": event.get("space_id"),
            }

            actor_id = event.get("actor_agent_id")
            if actor_id and actor_map.get(str(actor_id)):
                actor = actor_map[str(actor_id)]
                payload["actor_agent"] = {
                    "id": str(actor.id),
                    "name": actor.name,
                }

            message_id = event.get("message_id")
            if message_id and message_map.get(str(message_id)):
                message = message_map[str(message_id)]
                payload["message"] = {
                    "id": str(message.id),
                    "content": (message.content or "")[:160],
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                }
                if event.get("space_id"):
                    payload["deep_link"] = f"ax://spaces/{event['space_id']}/messages/{message.id}"
            task_id = event.get("task_id")
            if task_id and task_map.get(str(task_id)):
                task = task_map[str(task_id)]
                metadata = task.task_metadata if isinstance(getattr(task, "task_metadata", None), dict) else {}
                blocked_reason = metadata.get("blocked_reason")
                completed_reason = metadata.get("completed_reason") or metadata.get("completion_notes")
                payload["task"] = {
                    "id": str(task.id),
                    "task_display_id": f"task_{task.task_number:06d}" if getattr(task, "task_number", None) else "task_legacy",
                    "title": task.title,
                    "status": task.status,
                    "work_status": getattr(task, "work_status", None),
                    "queue_state": getattr(task, "queue_state", None),
                    "priority": task.priority,
                    "blocked_reason": blocked_reason,
                    "completed_reason": completed_reason,
                }
                if event["kind"] == "task_reminder":
                    payload["content"] = f"Task reminder: {task.title}"
                elif event["kind"] == "task_blocked":
                    payload["content"] = f"Task blocked: {task.title}" + (f" — {blocked_reason}" if blocked_reason else "")
                elif event["kind"] == "task_completed":
                    payload["content"] = f"Task completed: {task.title}" + (f" — {completed_reason}" if completed_reason else "")
                if event.get("space_id"):
                    deep_link = f"ax://spaces/{event['space_id']}/tasks/{task.id}"
                    payload["deep_link"] = deep_link
                    open_action = {
                        "type": "open_task",
                        "label": "Open task",
                        "task_id": str(task.id),
                        "deep_link": deep_link,
                    }
                    payload["action"] = open_action
                    payload["actions"] = [open_action]

            serialized.append(payload)

        # Preserve ordering from the input list
        return serialized

    # ------------------------------------------------------------------
    # Acknowledgement helpers
    # ------------------------------------------------------------------

    async def get_unseen_count(
        self,
        *,
        agent_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        space_id: uuid.UUID | None = None,
    ) -> int:
        """Compute unseen count using Redis when possible."""

        if not self.redis:
            return 0

        try:
            value = await self.redis.get(self._count_key(agent_id, space_id))
            if value is not None:
                return int(value)
        except Exception:  # pragma: no cover - defensive fallback
            pass

        try:
            count = await self.redis.zcard(self._unseen_key(agent_id, space_id))
            await self.redis.set(self._count_key(agent_id, space_id), count, ex=self.ttl_seconds)
            return int(count)
        except Exception:
            return 0

    async def acknowledge(
        self,
        *,
        agent_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        up_to_id: str,
        space_id: uuid.UUID | None = None,
    ) -> int:
        """Mark notifications up to a cursor as seen."""

        if not self.redis:
            return 0

        events_key = self._events_key(agent_id, space_id)
        unseen_key = self._unseen_key(agent_id, space_id)

        score = await self.redis.zscore(events_key, up_to_id)
        if score is None:
            return await self.get_unseen_count(agent_id=agent_id, owner_user_id=owner_user_id, space_id=space_id)

        ids_to_clear = await self.redis.zrangebyscore(unseen_key, "-inf", score)
        if ids_to_clear:
            try:
                await self.redis.zrem(unseen_key, *ids_to_clear)
            except Exception as exc:  # pragma: no cover - defensive logging
                self.logger.warning("Failed to clear unseen notifications for %s: %s", agent_id, exc)

        with suppress(Exception):
            await self.redis.set(self._last_seen_key(agent_id, space_id), up_to_id, ex=self.ttl_seconds)

        count = await self.redis.zcard(unseen_key)
        await self.redis.set(self._count_key(agent_id, space_id), count, ex=self.ttl_seconds)
        return int(count)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------


def parse_since_window(value: str | None) -> timedelta | None:
    """Convert a since string (1h/24h/7d/30d) into a timedelta."""

    if not value:
        return timedelta(days=30)

    normalized = value.strip().lower()
    lookup = {
        "1h": timedelta(hours=1),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "all": None,
    }
    return lookup.get(normalized, timedelta(days=30))
