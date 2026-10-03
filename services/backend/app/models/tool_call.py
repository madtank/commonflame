import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from . import Base


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tool_call_id = Column(String, nullable=False, unique=True, index=True)
    tool_name = Column(String, nullable=False)
    tool_action = Column(String, nullable=True)
    resource_uri = Column(String, nullable=True)
    arguments_hash = Column(String, nullable=True)
    kind = Column(String, nullable=True)
    arguments = Column(JSONB, nullable=True)
    status = Column(String, nullable=False, default="success")
    duration_ms = Column(Integer, nullable=True)
    agent_name = Column(String, nullable=True)
    agent_id = Column(UUID(as_uuid=True), nullable=True)
    space_id = Column(UUID(as_uuid=True), nullable=True)
    message_id = Column(UUID(as_uuid=True), nullable=True)
    correlation_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_tool_calls_space_created", "space_id", "created_at"),
        Index("idx_tool_calls_agent", "agent_id", "created_at"),
        Index("idx_tool_calls_correlation", "correlation_id"),
    )
