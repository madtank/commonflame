"""Feedback service for agent message ratings (thumbs up/down)."""

import logging
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.message import Message
from ..models.message_feedback import MessageFeedback

logger = logging.getLogger(__name__)


class FeedbackService:
    """Submit and query user feedback on agent messages."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def submit_feedback(
        self,
        message_id: UUID,
        user_id: UUID,
        space_id: UUID,
        vote: int,
        comment: str | None = None,
    ) -> MessageFeedback:
        """Upsert feedback for a message. Returns the feedback row.

        Validates:
        - Message exists and belongs to org
        - Message has an agent_id (only agent messages get feedback)
        """
        # Fetch message and validate
        result = await self.db.execute(
            select(Message).where(Message.id == message_id, Message.space_id == space_id)
        )
        message = result.scalar_one_or_none()
        if not message:
            raise ValueError("Message not found")
        if not message.agent_id:
            raise ValueError("Feedback can only be submitted on agent messages")

        agent_id = message.agent_id

        # Upsert: insert or update on conflict
        stmt = pg_insert(MessageFeedback).values(
            message_id=message_id,
            user_id=user_id,
            space_id=space_id,
            agent_id=agent_id,
            vote=vote,
            comment=comment,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_feedback_message_user",
            set_={
                "vote": stmt.excluded.vote,
                "comment": stmt.excluded.comment,
                "updated_at": func.now(),
            },
        )
        await self.db.execute(stmt)
        await self.db.flush()

        # Fetch the upserted row
        result = await self.db.execute(
            select(MessageFeedback).where(
                MessageFeedback.message_id == message_id,
                MessageFeedback.user_id == user_id,
            )
        )
        feedback = result.scalar_one()

        # Recalculate agent's denormalized score
        await self.recalculate_agent_feedback(agent_id)

        return feedback

    async def get_message_feedback(
        self, message_id: UUID, user_id: UUID | None = None
    ) -> dict:
        """Get feedback summary for a message.

        Returns:
            {"thumbs_up": int, "thumbs_down": int, "user_vote": int | None}
        """
        result = await self.db.execute(
            select(
                func.count().filter(MessageFeedback.vote == 1).label("thumbs_up"),
                func.count().filter(MessageFeedback.vote == -1).label("thumbs_down"),
            ).where(MessageFeedback.message_id == message_id)
        )
        row = result.one()
        thumbs_up = row.thumbs_up or 0
        thumbs_down = row.thumbs_down or 0

        # Get current user's vote if provided
        user_vote = None
        if user_id:
            vote_result = await self.db.execute(
                select(MessageFeedback.vote).where(
                    MessageFeedback.message_id == message_id,
                    MessageFeedback.user_id == user_id,
                )
            )
            vote_row = vote_result.scalar_one_or_none()
            if vote_row is not None:
                user_vote = vote_row

        return {
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down,
            "user_vote": user_vote,
        }

    async def recalculate_agent_feedback(self, agent_id: UUID) -> None:
        """Recalculate agent's feedback_score and feedback_count from source data."""
        result = await self.db.execute(
            select(
                func.avg(MessageFeedback.vote).label("avg_vote"),
                func.count().label("total"),
            ).where(MessageFeedback.agent_id == agent_id)
        )
        row = result.one()
        avg_vote = float(row.avg_vote) if row.avg_vote is not None else None
        total = row.total or 0

        await self.db.execute(
            text(
                "UPDATE agents SET feedback_score = :score, feedback_count = :count WHERE id = :id"
            ),
            {"score": avg_vote, "count": total, "id": str(agent_id)},
        )
        await self.db.flush()

    async def get_feedback_for_messages(
        self, message_ids: list[UUID], user_id: UUID | None = None
    ) -> dict[UUID, dict]:
        """Batch fetch feedback summaries for multiple messages.

        Returns dict mapping message_id -> {"thumbs_up": int, "thumbs_down": int, "user_vote": int | None}
        """
        if not message_ids:
            return {}

        # Get counts per message
        result = await self.db.execute(
            select(
                MessageFeedback.message_id,
                func.count().filter(MessageFeedback.vote == 1).label("thumbs_up"),
                func.count().filter(MessageFeedback.vote == -1).label("thumbs_down"),
            )
            .where(MessageFeedback.message_id.in_(message_ids))
            .group_by(MessageFeedback.message_id)
        )

        summaries: dict[UUID, dict] = {}
        for row in result.all():
            summaries[row.message_id] = {
                "thumbs_up": row.thumbs_up or 0,
                "thumbs_down": row.thumbs_down or 0,
                "user_vote": None,
            }

        # Get user's votes if provided
        if user_id and summaries:
            vote_result = await self.db.execute(
                select(MessageFeedback.message_id, MessageFeedback.vote).where(
                    MessageFeedback.message_id.in_(list(summaries.keys())),
                    MessageFeedback.user_id == user_id,
                )
            )
            for row in vote_result.all():
                if row.message_id in summaries:
                    summaries[row.message_id]["user_vote"] = row.vote

        return summaries
