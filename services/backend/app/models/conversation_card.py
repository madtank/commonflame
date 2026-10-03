"""ConversationCard -- thread-level summary and metadata for conversation cards."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from . import Base


class ConversationCard(Base):
    __tablename__ = "conversation_cards"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    root_message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    channel = Column(String(50), nullable=False, default="main")

    summary = Column(Text, nullable=True)
    previous_summary = Column(Text, nullable=True)
    message_count = Column(Integer, nullable=False, default=1)
    participants = Column(JSONB, nullable=False, default=list)
    status = Column(String(20), nullable=False, default="active")
    card_metadata = Column("metadata", JSONB, nullable=True)

    last_activity_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    space = relationship("Space", lazy="select")
    root_message = relationship("Message", lazy="select")
