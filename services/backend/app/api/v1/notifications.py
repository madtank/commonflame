"""
Notifications API endpoint
Stage 1: Returns mentions for user's agents in current space
"""

import logging
import uuid
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...core.database import get_db_session
from ...core.rls import SecureSession, get_secure_session
from ...core.redis_client import redis_client as core_redis_client
from ...models.agent import Agent
from ...models.mention import Mention
from ...models.space import Space
from ...models.user import User
from ...services.notifications_service import NotificationsService
from ...core.jwt_verify import get_current_user_from_token

router = APIRouter(prefix="/notifications", tags=["notifications"])

logger = logging.getLogger(__name__)

# Constants
NOTIFICATION_KIND_MENTION = "mention"

def _matches_user_handle(agent, user_handle: str) -> bool:
    """Check if agent name matches user handle (case-insensitive)."""
    if not agent:
        return False
    agent_name = getattr(agent, "name", None) or ""
    return agent_name.strip().lower() == user_handle

class NotificationItem(BaseModel):
    id: str
    message: str
    space_id: str
    space_name: str
    message_id: str
    timestamp: str
    read: bool
    mentioned_agent: str | None = None

class NotificationsResponse(BaseModel):
    unread_count: int
    notifications: list[NotificationItem]

@router.get("", response_model=NotificationsResponse)
async def get_notifications(
    session: SecureSession = Depends(get_secure_session),

    limit: int = 20
):
    """
    Get notifications (mentions) for the current user's agents in their current space.
    Stage 1: Current space only.
    """
    try:
        space_id = session.space_id
        if not space_id:
            return NotificationsResponse(unread_count=0, notifications=[])

        # Get user's agents in current org
        agents_result = await session.db.execute(
            select(Agent).where(and_(Agent.user_id == session.user.id, Agent.space_id == space_id))
        )
        user_agents = agents_result.scalars().all()
        agent_ids = [agent.id for agent in user_agents]

        # Check Space Visibility
        org = await session.db.get(Space, space_id)
        is_private_space = org and org.visibility == "private"

        # Agent mentions (DB)
        # In private spaces, only show mentions that target the user's handle.
        # Concretely: if the user has an agent with the same handle as their username (common in dev),
        # only show mentions for that one agent; suppress all other agent mentions.
        bell_agent_ids = agent_ids
        user_handle = (session.user.username or "").strip().lower()
        if is_private_space and user_handle:
            bell_agent_ids = [agent.id for agent in user_agents if _matches_user_handle(agent, user_handle)]

        total_agent_unread = 0
        agent_mentions = []
        if bell_agent_ids:
            mention_filters = [
                Mention.mentioned_agent_id.in_(bell_agent_ids),
                Mention.space_id == space_id,
                Mention.read_at.is_(None),
            ]

            # Total unread count (ignoring limit)
            count_result = await session.db.execute(select(func.count(Mention.id)).where(and_(*mention_filters)))
            total_agent_unread = count_result.scalar() or 0

            # Get unread mentions for user's agents (DB)
            mentions_result = await session.db.execute(
                select(Mention)
                .options(selectinload(Mention.message), selectinload(Mention.mentioned_agent))
                .where(and_(*mention_filters))
                .order_by(Mention.created_at.desc())
                .limit(limit)
            )
            agent_mentions = mentions_result.scalars().all()

            # Defensive: ensure private-space filtering can't regress due to query changes.
            if is_private_space and user_handle:
                agent_mentions = [
                    mention for mention in agent_mentions if _matches_user_handle(mention.mentioned_agent, user_handle)
                ]

        # --- USER NOTIFICATIONS (REDIS) ---
        # Best-effort: never fail the endpoint if Redis is unavailable.
        user_items: list[NotificationItem] = []
        user_unread_count = 0
        try:
            svc = NotificationsService(session.db, core_redis_client)
            user_notifs_data = await svc.list_notifications(
                agent_id=session.user.id,  # Treat user_id as key for now
                owner_user_id=session.user.id,
                space_id=space_id,
                kinds=[NOTIFICATION_KIND_MENTION] if is_private_space else None,
                limit=limit,
            )

            filtered_user_items = user_notifs_data.get("items", [])
            if is_private_space:
                filtered_user_items = [
                    item
                    for item in filtered_user_items
                    if (item.get("kind") or "").lower() == NOTIFICATION_KIND_MENTION
                ]

            # NOTE: stats.unseen_count is currently computed without kinds/space filtering.
            # For private spaces, adjust it so the bell count matches what the user sees.
            user_unread_count = user_notifs_data.get("stats", {}).get("unseen_count", 0)
            if is_private_space and core_redis_client:
                try:
                    # Bug 3 fix: previously read from the unscoped key, which returned
                    # no results post-notification-scoping. Must use the space-scoped
                    # key that matches what _store_event now writes.
                    try:
                        space_uuid_for_key = uuid.UUID(str(space_id)) if space_id else None
                    except ValueError:
                        space_uuid_for_key = None
                    unseen_ids = await core_redis_client.zrange(
                        NotificationsService._unseen_key(session.user.id, space_uuid_for_key),
                        0,
                        -1,
                    )
                    if unseen_ids:
                        pipe = core_redis_client.pipeline()
                        for event_id in unseen_ids:
                            pipe.hgetall(NotificationsService._event_hash_key(event_id))
                        metas = await pipe.execute()
                        filtered_unseen = 0
                        for meta in metas:
                            # P2 fix: Skip if hash was deleted between zrange and hgetall
                            if not meta:
                                continue
                            if (meta.get("kind") or "").lower() != NOTIFICATION_KIND_MENTION:
                                continue
                            if meta.get("space_id") != str(space_id):
                                continue
                            filtered_unseen += 1
                        user_unread_count = filtered_unseen
                except Exception as exc:
                    logger.warning(
                        "Failed to filter unseen Redis notifications for user=%s org=%s: %s",
                        session.user.id,
                        space_id,
                        exc,
                    )

            for item in filtered_user_items:
                msg_content = (
                    item.get("message", {}).get("content", "") if item.get("message") else (item.get("content") or "")
                )
                user_items.append(
                    NotificationItem(
                        id=item["id"],
                        message=msg_content[:200],
                        space_id=str(item.get("space_id") or space_id),
                        space_name="Current Space",
                        message_id=str(item.get("message", {}).get("id") or ""),
                        timestamp=item["created_at"],
                        read=False,  # Redis "unseen" implies unread
                        mentioned_agent=session.user.username,
                    )
                )
        except Exception as exc:
            logger.warning("Failed to load Redis notifications: %s", exc)

        # Merge and Sort
        notifications = []

        # Add Agent mentions
        for mention in agent_mentions:
            msg = mention.message
            notifications.append(
                NotificationItem(
                    id=str(mention.id),
                    message=msg.content[:200] if msg and msg.content else "",
                    space_id=str(mention.space_id),
                    space_name="Current Space",
                    message_id=str(mention.message_id) if mention.message_id else "",
                    timestamp=mention.created_at.isoformat() if mention.created_at else "",
                    read=mention.read_at is not None,
                    mentioned_agent=mention.mentioned_agent.name if mention.mentioned_agent else None,
                )
            )

        # Add User mentions
        notifications.extend(user_items)

        # Sort combined list by timestamp desc
        notifications.sort(key=lambda x: x.timestamp, reverse=True)

        # Slice to limit
        notifications = notifications[:limit]

        # Total unread
        unread_count = total_agent_unread + user_unread_count

        return NotificationsResponse(unread_count=unread_count, notifications=notifications)

    except Exception as e:
        logger.error(f"Error fetching notifications: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch notifications: {e!s}")

@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Mark a notification (mention) as read.
    """
    from datetime import datetime
    from uuid import UUID

    try:
        # Parse notification_id as UUID
        try:
            mention_uuid = UUID(notification_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid notification ID format")

        # Get the mention
        mention_result = await session.db.execute(
            select(Mention).options(selectinload(Mention.mentioned_agent)).where(Mention.id == mention_uuid)
        )
        mention = mention_result.scalar_one_or_none()

        if not mention:
            raise HTTPException(status_code=404, detail="Notification not found")

        # Verify the mention belongs to one of the user's agents
        agent_result = await session.db.execute(
            select(Agent).where(and_(Agent.id == mention.mentioned_agent_id, Agent.user_id == session.user.id))
        )
        agent = agent_result.scalar_one_or_none()

        if not agent:
            raise HTTPException(status_code=403, detail="Not authorized to mark this notification as read")

        # Mark as read
        mention.read_at = datetime.now(UTC)
        await session.db.commit()

        return {"success": True, "notification_id": notification_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking notification as read: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to mark notification as read: {e!s}")

class BatchReadRequest(BaseModel):
    notification_ids: list[str]

@router.post("/read-batch")
async def mark_notifications_read_batch(
    request: BatchReadRequest,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Mark multiple notifications as read in a single transaction.
    """
    from datetime import datetime
    from uuid import UUID

    try:
        # 1. Parse all IDs
        try:
            mention_uuids = [UUID(nid) for nid in request.notification_ids]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid notification ID format in list")

        if not mention_uuids:
            return {"success": True, "count": 0}

        # 2. Get user's agent IDs (to verify ownership)
        space_id = session.space_id if session.space_id else None
        agents_query = select(Agent.id).where(Agent.user_id == session.user.id)
        if space_id:
            # Optionally restrict to current org context, but for 'read' it might be safer
            # to allow reading any notification user owns.
            # Given the fetch is scoped to org, we should probably verify org too if we want to be strict,
            # but simple ownership check is sufficient for 'read'.
            pass

        user_agent_ids_result = await session.db.execute(agents_query)
        user_agent_ids = user_agent_ids_result.scalars().all()

        if not user_agent_ids:
            return {"success": True, "count": 0}

        # 3. Update Mentions that match IDs AND belong to one of user's agents
        # Using simple UPDATE statement for efficiency
        from sqlalchemy import update

        stmt = (
            update(Mention)
            .where(
                and_(
                    Mention.id.in_(mention_uuids),
                    Mention.mentioned_agent_id.in_(user_agent_ids),
                    Mention.read_at.is_(None),
                )
            )
            .values(read_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )

        result = await session.db.execute(stmt)
        await session.db.commit()

        return {"success": True, "count": result.rowcount}

    except Exception as e:
        logger.error(f"Error marking notifications batch as read: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to batch mark read: {e!s}")

@router.post("/read-all")
async def mark_all_notifications_read(
    all_spaces: bool = Query(False, description="Clear notifications across ALL spaces, not just current"),
    session: SecureSession = Depends(get_secure_session)
):
    """
    Mark ALL unread notifications for the current user's agents as read.

    By default, only clears notifications in the current space.
    Use ?all_spaces=true to clear notifications across ALL spaces.
    """
    from datetime import datetime

    try:
        # Get org context with proper fallback
        space_id = session.space_id

        rowcount = 0

        if all_spaces:
            # Clear ALL notifications for user's agents across all spaces
            # 1. Get ALL agent IDs for user (no org filter)
            agents_result = await session.db.execute(
                select(Agent.id).where(Agent.user_id == session.user.id)
            )
            agent_ids = agents_result.scalars().all()

            if agent_ids:
                # 2. Update all unread mentions for these agents (no org filter)
                stmt = (
                    update(Mention)
                    .where(
                        and_(
                            Mention.mentioned_agent_id.in_(agent_ids),
                            Mention.read_at.is_(None),
                        )
                    )
                    .values(read_at=datetime.now(UTC))
                    .execution_options(synchronize_session=False)
                )
                result = await session.db.execute(stmt)
                await session.db.commit()
                rowcount = result.rowcount

            # 3. Clear ALL Redis notifications for user across all spaces.
            #
            # Bug 2 fix: previously this only touched the legacy unscoped key
            # (notif:unseen:{user_id}). Post-notification-scoping, new
            # notifications live under notif:unseen:{space_id}:{user_id}, so
            # the old code found nothing and the bell count stayed non-zero.
            #
            # We SCAN for every scoped key matching this user, delete them,
            # and also delete the legacy unscoped keys for backward compat.
            # last_seen is left alone (informational cursor, not load-bearing).
            try:
                if core_redis_client:
                    async def _delete_matching(pattern: str) -> None:
                        cursor = 0
                        while True:
                            cursor, keys = await core_redis_client.scan(
                                cursor, match=pattern, count=100
                            )
                            if keys:
                                await core_redis_client.delete(*keys)
                            if cursor == 0:
                                break

                    # Delete every scoped unseen + count key for this user
                    await _delete_matching(f"notif:unseen:*:{session.user.id}")
                    await _delete_matching(f"notif:count:*:{session.user.id}")
                    # Also delete legacy unscoped keys (backward compat)
                    await core_redis_client.delete(
                        NotificationsService._unseen_key(session.user.id),
                        NotificationsService._count_key(session.user.id),
                    )
                    logger.info(
                        "Cleared all Redis notifications across all spaces for user=%s",
                        session.user.id,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to clear all-spaces Redis notifications: %s", exc
                )

        else:
            # Original behavior: only current space
            if not space_id:
                return {"success": True, "count": 0}

            agents_result = await session.db.execute(
                select(Agent.id).where(and_(Agent.user_id == session.user.id, Agent.space_id == space_id))
            )
            agent_ids = agents_result.scalars().all()

            if agent_ids:
                # 2. Update all unread mentions for these agents
                stmt = (
                    update(Mention)
                    .where(
                        and_(
                            Mention.mentioned_agent_id.in_(agent_ids),
                            Mention.space_id == space_id,
                            Mention.read_at.is_(None),
                        )
                    )
                    .values(read_at=datetime.now(UTC))
                    .execution_options(synchronize_session=False)
                )

                result = await session.db.execute(stmt)
                await session.db.commit()
                rowcount = result.rowcount

            # 3. Mark user notifications as read in Redis (best-effort)
            # P1 FIX: Use direct Redis delete for reliable clearing instead of score-based acknowledge
            try:
                if core_redis_client:
                    # Bug 4 fix: admin sessions have space_id == "SYSTEM" (a
                    # literal, not a UUID). uuid.UUID() would raise ValueError,
                    # which used to be silently swallowed by the outer try —
                    # the endpoint returned {"success": True} while doing
                    # nothing. Explicitly handle non-UUID space_id here.
                    try:
                        space_uuid = uuid.UUID(str(space_id)) if space_id else None
                    except ValueError:
                        logger.info(
                            "Non-UUID space_id (%s) for user=%s — skipping "
                            "per-space Redis clear (probably admin SYSTEM session)",
                            space_id,
                            session.user.id,
                        )
                        space_uuid = None

                    if space_uuid is not None:
                        unseen_key = NotificationsService._unseen_key(session.user.id, space_uuid)
                        count_key = NotificationsService._count_key(session.user.id, space_uuid)

                        # Nuclear clear: delete the entire unseen set and reset count
                        await core_redis_client.delete(unseen_key)
                        await core_redis_client.set(count_key, 0, ex=86400)  # 24h TTL

                        logger.info(
                            "Cleared all Redis notifications for user=%s in space=%s",
                            session.user.id,
                            space_id,
                        )
            except Exception as exc:
                logger.warning("Failed to clear Redis notifications: %s", exc)

        return {"success": True, "count": rowcount, "all_spaces": all_spaces}

    except Exception as e:
        logger.error(f"Error marking all notifications as read: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to mark all read: {e!s}")
