import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class Space(Base):
    __tablename__ = "spaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    slug = Column(String(50), unique=True, nullable=False)
    description = Column(String(500))  # Space purpose and details
    visibility = Column(String(20), default="private")  # private, invite_only, public
    tier = Column(String(20), default="regular", nullable=False)  # regular, plus, admin (aliases: free, pro, enterprise)
    is_archived = Column(Boolean, default=False, nullable=False)
    is_internal = Column(Boolean, default=False, nullable=False)  # Internal system spaces (invisible)
    space_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)  # Default Space Agent for no-mention routing
    default_model = Column(String(100), nullable=True)  # User-chosen model for aX in this space
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)  # Space founder
    # AX-AGENT-MGMT-001: space-level agent management policy
    agent_policy = Column(JSONB, nullable=True, server_default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    users = relationship(
        "User", back_populates="space", foreign_keys="User.space_id", cascade="all, delete-orphan"
    )
    current_users = relationship("User", foreign_keys="User.current_space_id")
    creator = relationship("User", foreign_keys=[created_by])
    space_agent = relationship("Agent", foreign_keys=[space_agent_id], post_update=True)
    # Disambiguate Agent relationships: space_id vs pinned_to_space
    agents = relationship(
        "Agent",
        back_populates="space",
        cascade="all, delete-orphan",
        foreign_keys="Agent.space_id",
    )
    pinned_agents = relationship(
        "Agent",
        cascade="all, delete-orphan",
        foreign_keys="Agent.pinned_to_space",
        primaryjoin="Space.id==Agent.pinned_to_space",
        viewonly=True,
    )
    messages = relationship("Message", back_populates="space", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="space", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="space", cascade="all, delete-orphan")
    guardrail_violations = relationship(
        "GuardrailViolation", back_populates="space", cascade="all, delete-orphan"
    )
    guardrail_configs = relationship("GuardrailConfig", back_populates="space", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Space(id={self.id}, name='{self.name}', slug='{self.slug}')>"


# Backward compatibility alias
Organization = Space
