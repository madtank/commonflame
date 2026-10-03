"""Conversation Card Service — manages thread-level cards with rolling summaries.

Card lifecycle:
1. Root message sent → card created with initial summary
2. Reply arrives → card updated: message_count++, participants updated,
   summary incrementally re-summarized (previous_summary + new message)
3. Thread goes quiet → status stays "active" until explicitly archived

Summary strategy ("summary of summaries"):
- First message: summarize the message directly
- Each subsequent message: summarize(previous_summary + new_message_summary)
- This is O(1) per message, not O(n) re-reading the whole thread
"""
import logging
import uuid as uuid_module
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import AsyncSessionLocal
from app.models.conversation_card import ConversationCard
from app.services.message_intelligence import summarize_message

logger = logging.getLogger(__name__)


def serialize_card(
    card: ConversationCard,
    *,
    include_extended: bool = True,
    include_event_aliases: bool = False,
) -> dict:
    """Serialize a conversation card for API responses and SSE events."""
    payload = {
        "id": str(card.id),
        "root_message_id": str(card.root_message_id),
        "channel": card.channel,
        "summary": card.summary,
        "message_count": card.message_count,
        "participants": card.participants,
        "status": card.status,
        "metadata": card.card_metadata,
        "last_activity_at": card.last_activity_at.isoformat() if card.last_activity_at else None,
    }
    if include_extended:
        payload.update({
            "created_at": card.created_at.isoformat() if card.created_at else None,
            "updated_at": card.updated_at.isoformat() if card.updated_at else None,
        })
    if include_event_aliases:
        payload["card_id"] = payload["id"]
    return payload


async def publish_card_updated(space_id: str, card: ConversationCard) -> None:
    """Publish the current card state on the conversation-card SSE channel."""
    from app.services.redis_sse_broker import redis_sse_broker

    await redis_sse_broker.publish(
        space_id=space_id,
        event="conversation_card_updated",
        data=serialize_card(card, include_extended=True, include_event_aliases=True),
    )


async def _incremental_summarize(
    previous_summary: Optional[str],
    new_content: str,
) -> Optional[str]:
    """Build rolling summary: summarize(previous_summary + new content).

    First message: just summarize the content.
    Subsequent: merge previous summary with new content into updated summary.
    """
    if not previous_summary:
        return await summarize_message(new_content)

    merged = (
        f"Previous thread summary: {previous_summary}\n\n"
        f"New message: {new_content}\n\n"
        f"Write an updated 1-2 sentence summary of this conversation thread."
    )
    return await summarize_message(merged)


def _make_participant(
    user_id: Optional[str],
    agent_id: Optional[str],
    display_name: str,
    sender_type: str,
) -> dict:
    """Create a participant entry for the card."""
    return {
        "id": agent_id or user_id or "unknown",
        "name": display_name,
        "type": sender_type,
    }


async def get_or_create_card(
    *,
    db: AsyncSession,
    root_message_id: str,
    space_id: str,
    channel: str = "main",
    sender_display_name: str = "",
    sender_type: str = "human",
    sender_id: str = "",
) -> ConversationCard:
    """Get existing card for a thread root, or create one."""
    root_uuid = uuid_module.UUID(root_message_id)

    result = await db.execute(
        select(ConversationCard).where(
            ConversationCard.root_message_id == root_uuid
        )
    )
    card = result.scalar_one_or_none()

    if card:
        return card

    participant = _make_participant(
        user_id=sender_id if sender_type == "human" else None,
        agent_id=sender_id if sender_type == "agent" else None,
        display_name=sender_display_name,
        sender_type=sender_type,
    )

    card = ConversationCard(
        space_id=uuid_module.UUID(space_id),
        root_message_id=root_uuid,
        channel=channel,
        message_count=1,
        participants=[participant],
        status="active",
        last_activity_at=datetime.now(timezone.utc),
    )
    db.add(card)
    await db.flush()
    logger.info("CONV_CARD_CREATED root_message_id=%s space_id=%s", root_message_id, space_id)
    return card


async def update_card_on_new_message(
    *,
    root_message_id: str,
    space_id: str,
    new_content: str,
    sender_display_name: str = "",
    sender_type: str = "human",
    sender_id: str = "",
    channel: str = "main",
) -> Optional[str]:
    """Update a conversation card when a new message arrives in the thread.

    Called as a background task from messages_notifications.py.
    Returns the new summary, or None on failure.
    """
    try:
        async with AsyncSessionLocal() as session:
            card = await get_or_create_card(
                db=session,
                root_message_id=root_message_id,
                space_id=space_id,
                channel=channel,
                sender_display_name=sender_display_name,
                sender_type=sender_type,
                sender_id=sender_id,
            )

            # Increment message count
            card.message_count = (card.message_count or 1) + 1
            card.last_activity_at = datetime.now(timezone.utc)
            card.updated_at = datetime.now(timezone.utc)

            # Add participant if new
            participant = _make_participant(
                user_id=sender_id if sender_type == "human" else None,
                agent_id=sender_id if sender_type == "agent" else None,
                display_name=sender_display_name,
                sender_type=sender_type,
            )
            existing_ids = {p.get("id") for p in (card.participants or [])}
            if participant["id"] not in existing_ids:
                participants = list(card.participants or [])
                participants.append(participant)
                card.participants = participants
                flag_modified(card, "participants")

            # Incremental re-summarization
            new_summary = await _incremental_summarize(
                previous_summary=card.summary,
                new_content=new_content,
            )
            if new_summary:
                card.previous_summary = card.summary
                card.summary = new_summary

            await session.commit()

            # Broadcast SSE
            await publish_card_updated(space_id, card)

            logger.info(
                "CONV_CARD_UPDATED root_message_id=%s count=%d",
                root_message_id, card.message_count,
            )
            return new_summary

    except Exception as e:
        logger.warning("CONV_CARD_UPDATE_ERROR root=%s error=%s", root_message_id, e)
        return None
