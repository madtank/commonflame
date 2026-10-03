from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
import secrets

from . import Base


class SpaceInviteCode(Base):
    __tablename__ = "space_invite_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    invite_code = Column(String(12), unique=True, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime(timezone=True))
    max_uses = Column(Integer)  # NULL = unlimited
    current_uses = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    space = relationship("Space")
    creator = relationship("User")

    @staticmethod
    def generate_invite_code() -> str:
        """Generate a unique 12-character invite code"""
        chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # Exclude confusing chars
        return ''.join(secrets.choice(chars) for _ in range(12))

    def __repr__(self):
        return f"<SpaceInviteCode(code='{self.invite_code}', space_id={self.space_id}, active={self.active})>"


# Backward compatibility alias
OrganizationInvite = SpaceInviteCode
