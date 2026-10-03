from sqlalchemy import Column, DateTime, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from . import Base


class MessageIntelligence(Base):
    __tablename__ = "message_intelligence"

    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    # Summary stored in messages.ai_summary
    spam_score = Column(Float, nullable=True)  # 0.0 to 1.0
    toxicity_score = Column(Float, nullable=True)  # 0.0 to 1.0
    quality_score = Column(Float, nullable=True)  # 0.0 to 1.0

    # Security analysis (dual columns - use security_risk/security_type for new data)
    security_score = Column(Float, nullable=True)  # Legacy
    security_category = Column(String(50), nullable=True)  # Legacy
    security_risk = Column(Float, nullable=True)  # 0.0 to 1.0 (1.0 = high threat)
    security_type = Column(String(50), nullable=True)  # prompt_injection, social_engineering, credential_phishing, etc.
    security_reason = Column(String(500), nullable=True)  # Brief explanation of security concern

    provider_metadata = Column(JSON, nullable=True)  # Stores model info, tokens, etc.

    processed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships - One-to-one: message_id is primary key, so only one record per message
    message = relationship("Message", backref=backref("intelligence_data", uselist=False))
