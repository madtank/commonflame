"""Hashed, expiring capabilities for owner setup and invited accounts."""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from . import Base


class AccountInvite(Base):
    __tablename__ = "account_invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    kind = Column(String(16), nullable=False)  # owner_setup or sponsor
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
