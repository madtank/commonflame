"""Agent groups: named, space-scoped collections of agents.

A group lets a user (or an agent) address several agents at once. Sending a
message to a group expands to individual ``mentioned_agent_ids`` at send time,
so groups are purely a routing convenience layered on top of the existing
mention fan-out — no new delivery path.

Two creation sources are first-class:
  - user-created (owner_type='user')
  - agent-created / agent-proposed (owner_type='agent')  — agents can say
    "we should be a group" via the agent_groups MCP tool.

``is_dynamic`` + ``dynamic_rules`` reserve room for "smart groups" whose
membership is resolved from a filter (e.g. all agents with a given type) at
expansion time instead of being stored as explicit member rows.

Both tables are space-scoped and carry ``space_id`` so the standard
``_space_isolation`` RLS pattern applies directly (members denormalize
space_id to avoid a join in the policy).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class AgentGroup(Base):
    """A named collection of agents within a space."""

    __tablename__ = "agent_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)

    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)

    # Ownership / provenance: who created the group.
    owner_type = Column(String(10), nullable=False, server_default="user")  # user | agent | space
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    owner_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)

    visibility = Column(String(20), nullable=False, server_default="space")  # space | private
    is_archived = Column(Boolean, nullable=False, server_default="false")

    # Smart groups: membership resolved from dynamic_rules instead of explicit rows.
    is_dynamic = Column(Boolean, nullable=False, server_default="false")
    dynamic_rules = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    members = relationship(
        "AgentGroupMember",
        back_populates="group",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("space_id", "name", name="uq_agent_group_space_name"),
        CheckConstraint("owner_type IN ('user', 'agent', 'space')", name="ck_agent_group_owner_type"),
        CheckConstraint("visibility IN ('space', 'private')", name="ck_agent_group_visibility"),
        Index("ix_agent_group_space", "space_id"),
        Index("ix_agent_group_owner_user", "owner_user_id"),
        Index("ix_agent_group_owner_agent", "owner_agent_id"),
    )

    def __repr__(self):
        return f"<AgentGroup({self.name!r} space={self.space_id})>"


class AgentGroupMember(Base):
    """Membership of an agent in a group (explicit, non-dynamic membership)."""

    __tablename__ = "agent_group_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("agent_groups.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    # Denormalized for RLS (same space_isolation policy as the group / agents).
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)

    # Attribution: who added this agent.
    added_by_type = Column(String(10), nullable=True)  # user | agent | system
    added_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    added_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    group = relationship("AgentGroup", back_populates="members", foreign_keys=[group_id])

    __table_args__ = (
        UniqueConstraint("group_id", "agent_id", name="uq_agent_group_member"),
        CheckConstraint(
            "added_by_type IS NULL OR added_by_type IN ('user', 'agent', 'system')",
            name="ck_agent_group_member_added_by_type",
        ),
        Index("ix_agent_group_member_group", "group_id"),
        Index("ix_agent_group_member_agent", "agent_id"),
        Index("ix_agent_group_member_space", "space_id"),
    )

    def __repr__(self):
        return f"<AgentGroupMember(group={self.group_id} agent={self.agent_id})>"
