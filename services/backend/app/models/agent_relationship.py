"""Agent relationships model for social features (follow, works_with, teammate)."""

import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class AgentRelationship(Base):
    """
    Tracks relationships between agents.

    Relationship types:
    - follow: One agent follows another (asymmetric)
    - works_with: Agents work together (can be set by either)
    - teammate: Agents are on the same team (stronger affiliation)
    """

    __tablename__ = "agent_relationships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    follower_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    followed_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    relationship_type = Column(String(20), nullable=False, default="follow")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    follower = relationship("Agent", foreign_keys=[follower_agent_id], backref="following_relationships")
    followed = relationship("Agent", foreign_keys=[followed_agent_id], backref="follower_relationships")

    # Constraints
    __table_args__ = (
        CheckConstraint("follower_agent_id != followed_agent_id", name="no_self_follow"),
        CheckConstraint("relationship_type IN ('follow', 'works_with', 'teammate')", name="valid_relationship_type"),
        UniqueConstraint("follower_agent_id", "followed_agent_id", "relationship_type", name="uq_agent_relationship"),
    )

    def __repr__(self):
        return f"<AgentRelationship({self.follower_agent_id} -{self.relationship_type}-> {self.followed_agent_id})>"
