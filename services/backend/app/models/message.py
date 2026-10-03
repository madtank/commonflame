import uuid

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID, TSVECTOR
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from . import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)  # Nullable: agent-authored messages have user_id=None
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"))
    content = Column(Text, nullable=False)
    channel = Column(String(50), default="main")
    message_type = Column(String(20), default="message")  # message, response, reply, task
    parent_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"))

    # Message API v2 features
    cursor_position = Column(
        BigInteger, server_default=func.extract("epoch", func.now())
    )  # for cursor-based pagination
    read_state = Column(String(20), default="delivered")  # delivered, ack, read
    token_budget = Column(Integer)  # for LLM agents
    message_metadata = Column("metadata", JSON)  # standardized envelope data (metadata is reserved)
    ai_summary = Column(Text, nullable=True)  # AI-generated summary (one-time, cached)
    summarized_at = Column(DateTime(timezone=True), nullable=True)  # When ai_summary was generated

    # Semantic search (Crystal Prism)
    embedding = Column(Vector(768), nullable=True)  # pgvector embedding for semantic search
    search_vector = Column(TSVECTOR, nullable=True)  # Full-text search vector

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    space = relationship("Space", back_populates="messages")
    user = relationship("User", back_populates="messages")
    agent = relationship("Agent", back_populates="messages")
    parent = relationship("Message", remote_side=[id], backref="replies")
    attachments = relationship("Attachment", back_populates="message")

    def __repr__(self):
        return f"<Message(id={self.id}, channel='{self.channel}', type='{self.message_type}', cursor={self.cursor_position})>"
