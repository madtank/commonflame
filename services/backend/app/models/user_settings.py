"""
User Settings model — per-user, per-space preferences.

v1 uses typed columns for frontend type safety.
JSONB extension field reserved for future custom settings.
"""
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from . import Base


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "space_id", name="uq_user_settings_user_space"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False, index=True)

    # Notification preferences
    email_notifications = Column(Boolean, default=True, nullable=False)
    mention_notifications = Column(Boolean, default=True, nullable=False)
    task_notifications = Column(Boolean, default=True, nullable=False)

    # AI preferences
    ai_suggestions_enabled = Column(Boolean, default=True, nullable=False)
    ai_auto_summarize = Column(Boolean, default=False, nullable=False)

    # Display preferences
    theme = Column(String(20), default="system", nullable=False)  # system, light, dark
    compact_mode = Column(Boolean, default=False, nullable=False)

    # Extension point for future settings without migrations
    custom = Column(JSONB, default=dict, nullable=False, server_default="{}")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", backref="settings")
    space = relationship("Space")
