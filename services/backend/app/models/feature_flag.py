from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from . import Base


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    flag_name = Column(String(100), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    enabled = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("flag_name", "space_id", "user_id", name="uq_flag_space_user"),
        Index("ix_feature_flags_flag_name", "flag_name"),
        Index("ix_feature_flags_space_id", "space_id"),
        Index("ix_feature_flags_user_id", "user_id"),
    )

    def __repr__(self):
        return f"<FeatureFlag(flag={self.flag_name}, space={self.space_id}, user={self.user_id}, enabled={self.enabled})>"
