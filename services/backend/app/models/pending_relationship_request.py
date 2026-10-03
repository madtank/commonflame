"""Pending relationship request model for invite/accept workflow.

teammate and works_with relationships require acceptance before becoming active.
This provides security for cross-user agent relationships.
"""

import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class PendingRelationshipRequest(Base):
    """
    Tracks pending relationship requests between agents.

    Security feature: teammate/works_with relationships require acceptance
    before becoming active, especially for cross-user agents.

    Status flow:
    - pending: Request sent, awaiting target's response
    - accepted: Target accepted, relationship created in agent_relationships
    - rejected: Target rejected, no relationship created
    - expired: Request expired after 30 days without response
    """

    __tablename__ = "pending_relationship_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requester_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    target_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    relationship_type = Column(String(20), nullable=False)  # 'works_with' or 'teammate'
    status = Column(String(20), nullable=False, default="pending")
    message = Column(Text, nullable=True)  # Optional message from requester
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    requester = relationship("Agent", foreign_keys=[requester_agent_id], backref="outgoing_requests")
    target = relationship("Agent", foreign_keys=[target_agent_id], backref="incoming_requests")

    # Constraints
    __table_args__ = (
        CheckConstraint("requester_agent_id != target_agent_id", name="no_self_request"),
        CheckConstraint("relationship_type IN ('works_with', 'teammate')", name="valid_request_relationship_type"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected', 'expired')", name="valid_request_status"),
        UniqueConstraint("requester_agent_id", "target_agent_id", "relationship_type", name="uq_pending_request"),
    )

    def __repr__(self):
        return f"<PendingRelationshipRequest({self.requester_agent_id} -{self.relationship_type}-> {self.target_agent_id} [{self.status}])>"

    @property
    def is_pending(self) -> bool:
        """Check if request is still pending."""
        return self.status == "pending"

    @property
    def is_expired(self) -> bool:
        """Check if request has expired."""
        from datetime import UTC, datetime

        if self.status == "expired":
            return True
        return bool(self.expires_at and datetime.now(UTC) > self.expires_at)
