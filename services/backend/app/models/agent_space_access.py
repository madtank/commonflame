import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class AgentSpaceAccess(Base):
    """
    Agent-to-space attachment with per-space lifecycle state.

    AX-AGENT-MGMT-001:
    - state: 'active' | 'suspended' | 'detached'
    - suspension_mode: who/what suspended (manual_admin, manual_owner, autonomous_safeguard)
    - version: optimistic concurrency guard
    """
    __tablename__ = "agent_space_access"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    is_default = Column(Boolean, default=False, nullable=False)
    migration_flags = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # --- AX-AGENT-MGMT-001: Attachment state ---
    state = Column(String(20), nullable=False, server_default="active")  # active | suspended | detached
    suspension_mode = Column(String(30), nullable=True)  # manual_admin | manual_owner | autonomous_safeguard
    suspend_reason_code = Column(String(30), nullable=True)
    suspend_reason_text = Column(Text, nullable=True)

    # Attribution: who performed each lifecycle action
    attached_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    attached_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    attached_at = Column(DateTime(timezone=True), server_default=func.now())

    suspended_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    suspended_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    suspended_at = Column(DateTime(timezone=True), nullable=True)

    detached_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    detached_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    detached_at = Column(DateTime(timezone=True), nullable=True)

    version = Column(Integer, nullable=False, server_default="1")

    agent = relationship("Agent", back_populates="space_access", foreign_keys=[agent_id])

    __table_args__ = (
        UniqueConstraint("agent_id", "space_id", name="uq_agent_space"),
        Index("ix_agent_space_agent", "agent_id"),
        Index("ix_agent_space_space", "space_id"),
    )
