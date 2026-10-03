"""
Mention Model
Tracks @mentions in messages for notification delivery
"""

from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from . import Base


class Mention(Base):
    """
    Tracks agent mentions in messages.

    When an agent is @mentioned in a message, a record is created here.
    The MCP notification system uses this to deliver real-time notifications
    via the messages://inbox/{agent_id} resource subscription.
    """
    __tablename__ = "mentions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    mentioned_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    mentioning_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"))
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)

    # Tracking fields
    read_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    message = relationship("Message", backref="mentions")
    mentioned_agent = relationship("Agent", foreign_keys=[mentioned_agent_id], backref="received_mentions")
    mentioning_agent = relationship("Agent", foreign_keys=[mentioning_agent_id], backref="sent_mentions")
    space = relationship("Space", backref="mentions")

    # Indexes for performance
    __table_args__ = (
        Index('idx_mentions_agent_unread', 'mentioned_agent_id', 'read_at'),
        Index('idx_mentions_agent_created', 'mentioned_agent_id', 'created_at'),
        Index('idx_mentions_space_agent', 'space_id', 'mentioned_agent_id'),
    )

    def __repr__(self):
        return f"<Mention(id={self.id}, mentioned={self.mentioned_agent_id}, message={self.message_id})>"

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": str(self.id),
            "message_id": str(self.message_id),
            "mentioned_agent_id": str(self.mentioned_agent_id),
            "mentioning_agent_id": str(self.mentioning_agent_id) if self.mentioning_agent_id else None,
            "space_id": str(self.space_id),
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
