"""Unified credentials for principal-based auth (user PATs, future system secrets)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID

from . import Base


class Credential(Base):
    __tablename__ = "credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(
        UUID(as_uuid=True),
        ForeignKey("spaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    principal_type = Column(String(20), nullable=False)  # 'user' | 'system_account'
    principal_id = Column(UUID(as_uuid=True), nullable=False)
    credential_type = Column(String(20), nullable=False)  # 'pat' | 'service_secret'
    key_id = Column(String(16), nullable=False, unique=True, index=True)
    secret_hash = Column(String(255), nullable=False)  # Argon2id
    name = Column(String(255), nullable=True)
    scopes = Column(JSON, default=["api:read", "api:write"])
    agent_scope = Column(String(10), nullable=False, default="all")  # 'all' | 'user' | 'agents'
    allowed_agent_ids = Column(JSON, nullable=True)
    bound_agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_principal_type = Column(String(20), nullable=True)
    created_by_principal_id = Column(UUID(as_uuid=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    lifecycle_state = Column(String(32), nullable=False, default="active")  # enrollment | active | revoked | expired
    audience = Column(String(20), nullable=False, default="cli")  # cli | mcp | both
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
