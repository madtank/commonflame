from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from . import Base


class SpaceMembership(Base):
    __tablename__ = "space_memberships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), default="member")  # member, admin
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    default_route_to = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    # AX-AGENT-MGMT-001: per-member delegation policy for concierge actions
    ax_delegation_policy = Column(JSONB, nullable=True)

    # Ensure one membership per user per space
    __table_args__ = (UniqueConstraint('user_id', 'space_id', name='unique_user_space_membership'),)

    # Relationships
    user = relationship("User")
    space = relationship("Space")

    def __repr__(self):
        return f"<SpaceMembership(user_id={self.user_id}, space_id={self.space_id}, role='{self.role}')>"


# Backward compatibility alias
OrganizationMembership = SpaceMembership
