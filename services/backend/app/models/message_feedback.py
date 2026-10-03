import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, SmallInteger, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class MessageFeedback(Base):
    __tablename__ = "message_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=False)
    vote = Column(SmallInteger, nullable=False)  # +1 or -1
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
        CheckConstraint("vote IN (-1, 1)", name="ck_feedback_vote_range"),
        Index("idx_feedback_agent_space", "agent_id", "space_id"),
    )

    # Relationships
    message = relationship("Message")
    user = relationship("User")
    agent = relationship("Agent")

    def __repr__(self):
        return f"<MessageFeedback(id={self.id}, message_id={self.message_id}, vote={self.vote})>"
