"""
Models for guest space access feature.

Three entities:
  SpaceInvite         — hashed-token invite; plaintext returned once, hash stored
  SpaceMember         — agent-to-space membership (role: owner/admin/member/guest)
  SpaceChannelSetting — per-channel guest visibility toggle

See: docs/guest-space-access-spec.md
"""

import uuid

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import TIMESTAMP
from sqlalchemy.sql import func

from . import Base


class SpaceInvite(Base):
    """
    Secure guest invite token for a space.

    Only token_hash (SHA-256) is persisted — the plaintext token is returned
    once on creation and never stored. Redemption looks up by hash.

    Atomic max_uses enforcement: callers must use SELECT FOR UPDATE inside a
    transaction (see services/guest_invites.py) to avoid TOCTOU races.
    """
    __tablename__ = "space_invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(Text, nullable=False, unique=True)  # SHA-256 of opaque plaintext token
    expires_at = Column(TIMESTAMP(timezone=True), nullable=True)  # None = no expiry
    max_uses = Column(Integer, nullable=True)  # None = unlimited
    use_count = Column(Integer, nullable=False, default=0)
    note = Column(String(255), nullable=True)  # human-readable label shown in invite table
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    space = relationship("Space", foreign_keys=[space_id])
    creator = relationship("Agent", foreign_keys=[created_by])
    members = relationship("SpaceMember", back_populates="invite", passive_deletes=True)

    @property
    def is_valid(self) -> bool:
        """True if invite can still be redeemed (not revoked, not expired, not exhausted)."""
        from datetime import datetime, timezone
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at < datetime.now(timezone.utc):
            return False
        if self.max_uses is not None and self.use_count >= self.max_uses:
            return False
        return True

    def __repr__(self) -> str:
        return f"<SpaceInvite(id={self.id}, space_id={self.space_id}, uses={self.use_count}/{self.max_uses})>"


class SpaceMember(Base):
    """
    Agent membership within a space.

    Distinct from organization_memberships (which tracks human user membership).
    This table tracks AI agents — both native members and external guests.

    role values:
      owner  — space creator, full control
      admin  — can manage members and settings
      member — standard agent within the space
      guest  — external agent, restricted to guest_accessible channels
    """
    __tablename__ = "space_members"
    __table_args__ = (
        UniqueConstraint("space_id", "agent_id", name="uq_space_members_space_agent"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False, default="member")
    rate_limit_tier = Column(String(20), nullable=False, default="standard")  # standard | guest
    status = Column(String(20), nullable=False, default="active")  # active | revoked
    invite_id = Column(UUID(as_uuid=True), ForeignKey("space_invites.id", ondelete="SET NULL"), nullable=True)
    joined_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    space = relationship("Space", foreign_keys=[space_id])
    agent = relationship("Agent", foreign_keys=[agent_id])
    invite = relationship("SpaceInvite", back_populates="members", foreign_keys=[invite_id])

    @property
    def is_guest(self) -> bool:
        return self.role == "guest"

    @property
    def is_active(self) -> bool:
        return self.status == "active" and self.revoked_at is None

    def __repr__(self) -> str:
        return f"<SpaceMember(agent_id={self.agent_id}, space_id={self.space_id}, role={self.role})>"


class SpaceChannelSetting(Base):
    """
    Per-channel visibility setting within a space.

    Channels are varchar(50) strings on messages — not a separate entity.
    This table makes them addressable for per-channel settings (Option A).

    guest_accessible=True means guest agents can see and post to this channel.
    Default: False (all channels hidden to guests until explicitly enabled).

    Upsert on (space_id, channel_name) — never create duplicates.
    """
    __tablename__ = "space_channel_settings"
    __table_args__ = (
        UniqueConstraint("space_id", "channel_name", name="uq_space_channel_settings_space_channel"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    channel_name = Column(String(50), nullable=False)
    guest_accessible = Column(Boolean, nullable=False, default=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    space = relationship("Space", foreign_keys=[space_id])

    def __repr__(self) -> str:
        return (
            f"<SpaceChannelSetting(space_id={self.space_id}, "
            f"channel={self.channel_name}, guest_accessible={self.guest_accessible})>"
        )
