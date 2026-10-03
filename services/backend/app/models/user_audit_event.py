import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from . import Base


class UserAuditEvent(Base):
    """Admin-visible audit trail for user signup and platform touch events."""

    __tablename__ = "user_audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(80), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    tool_name = Column(String(120), nullable=True)
    resource_type = Column(String(80), nullable=True)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    source = Column(String(80), nullable=False, server_default="backend")
    metadata_json = Column(JSONB, nullable=False, server_default="{}")
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_user_audit_events_user_created", "user_id", "created_at"),
        Index("idx_user_audit_events_space_created", "space_id", "created_at"),
        Index("idx_user_audit_events_type_created", "event_type", "created_at"),
        Index("idx_user_audit_events_tool_created", "tool_name", "created_at"),
    )
