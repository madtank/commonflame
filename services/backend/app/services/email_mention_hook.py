"""
Email Mention Hook

Integrates email notifications into the existing mention detection flow.
Called from messages_notifications.py after mentions are parsed.

This is the glue between mention detection (already exists) and email delivery (new).
"""

import logging
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.agent import Agent
from app.services.email_notification_service import EmailNotificationService

logger = logging.getLogger(__name__)


async def check_user_mentions_and_notify(
    db: AsyncSession,
    redis,
    mentions: List[str],
    author_name: str,
    message_content: str,
    space_id: UUID,
    space_name: str = "",
    message_id: Optional[UUID] = None,
) -> int:
    """
    Check if any @mentions refer to users (not agents) and send email notifications.

    The existing MentionsService handles agent mentions and dispatching.
    This function handles the case where a user's handle is @mentioned,
    which triggers an email notification.

    Args:
        mentions: List of @mentioned handles (without @ prefix)
        author_name: Who sent the message
        message_content: The full message text
        space_id: Organization/space ID
        space_name: Display name of the space
        message_id: UUID of the triggering message

    Returns:
        Number of email notifications sent/queued
    """
    if not mentions:
        return 0

    sent_count = 0
    service = EmailNotificationService(db, redis)

    for handle in mentions:
        # Look up as user first (users can be @mentioned too)
        result = await db.execute(
            select(User).where(User.username == handle)
        )
        user = result.scalar_one_or_none()

        if not user:
            # Not a user handle — it's an agent mention, handled elsewhere
            continue

        # Don't notify if the user is the one who sent the message
        # (e.g., user types their own name)
        if user.username == author_name:
            continue

        try:
            success = await service.notify_user_mention(
                user_id=user.id,
                mentioned_by=author_name,
                message_content=message_content,
                space_name=space_name,
                space_id=space_id,
                message_id=message_id,
            )
            if success:
                sent_count += 1
                logger.info(
                    f"📧 Email notification sent/queued for @{handle} "
                    f"(mentioned by {author_name})"
                )
        except Exception as e:
            logger.error(f"Failed to send email notification to @{handle}: {e}")

    return sent_count
