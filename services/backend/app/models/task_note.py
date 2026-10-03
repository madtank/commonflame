from sqlalchemy import Column, String, DateTime, ForeignKey, Text, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from . import Base


class TaskNote(Base):
    __tablename__ = "task_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    author_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    author_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True)
    note = Column(Text, nullable=False)
    note_type = Column(String(20), default="general")  # general, progress, issue, solution
    visibility = Column(String(20), default="public")  # public, private, team
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    task = relationship("Task", back_populates="notes")
    space = relationship("Space")
    author_user = relationship("User", foreign_keys=[author_user_id])
    author_agent = relationship("Agent", foreign_keys=[author_agent_id])

    # Constraints
    __table_args__ = (
        CheckConstraint('(author_user_id IS NOT NULL) OR (author_agent_id IS NOT NULL)', name='author_check'),
    )

    @property
    def author_name(self):
        """Get the name of the author (agent first, then user fallback)"""
        if self.author_agent:
            return self.author_agent.name
        elif self.author_user:
            return self.author_user.username or self.author_user.full_name or self.author_user.email
        return "Unknown"

    @property
    def author_type(self):
        """Get the type of author (agent first, then user fallback)"""
        return "agent" if self.author_agent_id else "user"

    def __repr__(self):
        return f"<TaskNote(id={self.id}, task_id={self.task_id}, author_type='{self.author_type}', note_type='{self.note_type}')>"
