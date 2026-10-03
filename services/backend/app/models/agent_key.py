"""Agent API Keys for headless (client_credentials) authentication.

SEP-1046 compliant. Each row represents a client_id/client_secret pair
that an agent can use for OAuth 2.1 client_credentials grant.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Text, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from . import Base


class AgentKey(Base):
    """
    Headless agent credentials for client_credentials OAuth grant.
    One agent can have multiple keys (for rotation without downtime).
    """
    __tablename__ = "agent_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id = Column(String(64), unique=True, nullable=False, index=True)
    client_secret_hash = Column(String(128), nullable=False)  # bcrypt
    name = Column(String(255), nullable=True)  # human-friendly label
    scopes = Column(Text, nullable=True, default="mcp:read mcp:write")
    is_active = Column(Boolean, default=True, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    # Relationships
    agent = relationship("Agent", backref="agent_keys", lazy="selectin")

    __table_args__ = (
        Index("ix_agent_keys_agent_active", "agent_id", "is_active"),
    )
