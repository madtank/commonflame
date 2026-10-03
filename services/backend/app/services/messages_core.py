"""
Core message operations: validation, emoji detection, and helper utilities.
Extracted from messages_service.py for better modularity.
"""

from __future__ import annotations

import re
import uuid
from typing import Optional, List, Dict
from uuid import UUID

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from opentelemetry import trace

from ..models.message import Message
from .message_visibility import exclude_ui_only_no_reply_clause
from .mentions_service import MentionsService


class MessagesCoreHelper:
    """Core helper functions for message operations."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._tracer = trace.get_tracer("ax.services.messages.core")

    async def validate_parent(
        self, *, space_id: UUID, channel: str, parent_id: Optional[str]
    ) -> Optional[UUID]:
        """Validate parent message exists in same org/channel."""
        if not parent_id:
            return None

        try:
            # Handle both string and UUID objects
            if isinstance(parent_id, uuid.UUID):
                pid = parent_id
            else:
                pid = uuid.UUID(str(parent_id))
        except (ValueError, AttributeError):
            raise ValueError("Invalid parent message ID format")

        res = await self.db.execute(
            select(Message).where(
                and_(
                    Message.id == pid,
                    Message.space_id == space_id,
                    Message.channel == channel
                )
            )
        )
        parent_msg = res.scalar_one_or_none()
        if not parent_msg:
            raise ValueError("Parent message not found or not in same channel")

        # SMART REDIRECT: If someone tries to react to a reaction,
        # just apply it to the original message instead. In addition to the
        # stored type/metadata, also detect legacy reactions by emoji-only content
        is_parent_reaction = (
            parent_msg.message_type == "reaction"
            or (getattr(parent_msg, "message_metadata", None) and parent_msg.message_metadata.get("is_reaction"))
        )
        # Fallback detection for historical data: emoji-only parent content
        try:
            if not is_parent_reaction and parent_msg.content:
                is_parent_reaction = await self.is_emoji_reaction(parent_msg.content)
        except Exception:
            pass
        if is_parent_reaction:
            if parent_msg.parent_id:
                # Use the reaction's parent message instead
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"🔄 REDIRECT: Redirecting reaction from {pid} to original message {parent_msg.parent_id}")
                return parent_msg.parent_id
            else:
                # Shouldn't happen - reactions should always have a parent
                raise ValueError("Cannot react to a reaction that has no parent")

        return pid

    async def extract_mentions(self, content: str) -> List[str]:
        """Extract @mentions from message content (delegates to MentionsService for consistency)."""
        return list(MentionsService.parse_mentions(content))

    async def is_emoji_reaction(self, content: str) -> bool:
        """Check if message is purely emoji reaction."""
        # Simple emoji detection - can be enhanced
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # flags
            "]+",
            flags=re.UNICODE
        )
        clean_content = content.strip()
        return bool(emoji_pattern.fullmatch(clean_content))

    @staticmethod
    def extract_emojis(content: str) -> List[str]:
        """Extract individual emojis from text."""
        if not content:
            return []
        emoji_pattern = re.compile(
            r'[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U0001F1E0-\U0001F1FF]'
        )
        return emoji_pattern.findall(content)

    async def get_reply_counts_batch(
        self, *, space_id: UUID, channel: str, message_ids: List[UUID]
    ) -> Dict[str, int]:
        """
        Get reply counts for multiple messages in a single query.
        This eliminates per-message loops and consolidates counting logic.

        Returns dict mapping message_id (as string) to reply count.
        """
        if not message_ids:
            return {}

        with self._tracer.start_as_current_span(
            "messages.reply_count",
            attributes={
                "ax.space_id": str(space_id),
                "ax.channel": channel,
                "ax.message_count": len(message_ids),
            },
        ) as span:
            # Query for all replies grouped by parent_id
            query = (
                select(
                    Message.parent_id,
                    func.count(Message.id).label('reply_count')
                )
                .where(
                    and_(
                        Message.parent_id.in_(message_ids),
                        Message.space_id == space_id,
                        Message.channel == channel,
                        Message.message_type != "reaction",
                        exclude_ui_only_no_reply_clause(),
                    )
                )
                .group_by(Message.parent_id)
            )

            result = await self.db.execute(query)
            reply_counts = {str(parent_id): count for parent_id, count in result.all()}

            span.set_attribute("ax.replies.count", sum(reply_counts.values()))

            return reply_counts
