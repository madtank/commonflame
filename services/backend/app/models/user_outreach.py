"""
User Outreach Model

Tracks admin outreach to users for preventing duplicate contacts
and coordinating customer engagement across admin team members.
"""

from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from . import Base


class UserOutreach(Base):
    """
    Tracks when admins contact users for outreach purposes.

    Prevents duplicate/aggressive outreach and provides visibility
    to all admins about who has been contacted and when.
    """
    __tablename__ = "user_outreach"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="User being contacted"
    )
    contacted_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Admin who made contact"
    )
    contact_method = Column(
        String(50),
        nullable=False,
        comment="How user was contacted (email, phone, in-app, etc.)"
    )
    notes = Column(
        Text,
        nullable=True,
        comment="Optional notes about the outreach conversation"
    )
    campaign = Column(
        String(100),
        nullable=True,
        comment="Campaign identifier or reason for outreach"
    )
    contacted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the outreach occurred"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now()
    )

    # Relationships
    target_user = relationship(
        "User",
        foreign_keys=[target_user_id],
        backref="outreach_received"
    )
    contacted_by_user = relationship(
        "User",
        foreign_keys=[contacted_by_user_id],
        backref="outreach_made"
    )

    def __repr__(self):
        return f"<UserOutreach(target={self.target_user_id}, by={self.contacted_by_user_id}, method='{self.contact_method}')>"
