"""
Notification preferences per user.
Controls email delivery for mentions, digests, and quiet hours.
"""
import uuid
from datetime import time as dt_time

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class NotificationPreferences(Base):
    __tablename__ = "notification_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Email notifications
    email_enabled = Column(Boolean, default=True, nullable=False)
    email_on_mention = Column(Boolean, default=True, nullable=False)  # @user mentions
    email_on_agent_error = Column(Boolean, default=False, nullable=False)  # Agent dispatch failures
    email_on_task_complete = Column(Boolean, default=False, nullable=False)  # Task completions

    # Digest settings
    digest_mode = Column(
        String(20), default="immediate", nullable=False
    )  # immediate | 5min | hourly | daily
    digest_window_minutes = Column(Integer, default=5, nullable=False)  # Batch window for digest

    # Quiet hours (user's local time)
    quiet_hours_enabled = Column(Boolean, default=True, nullable=False)
    quiet_hours_start = Column(Time, default=dt_time(23, 0), nullable=True)  # 11 PM
    quiet_hours_end = Column(Time, default=dt_time(8, 0), nullable=True)  # 8 AM
    timezone = Column(String(50), default="America/Los_Angeles", nullable=True)

    # Rate limiting
    max_emails_per_hour = Column(Integer, default=10, nullable=False)
    emails_sent_this_hour = Column(Integer, default=0, nullable=False)
    hour_reset_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", backref="notification_preferences")

    def __repr__(self):
        return f"<NotificationPreferences(user_id={self.user_id}, email={self.email_enabled}, digest={self.digest_mode})>"
