"""
Guardrail Violations Model
Tracks security violations detected by Portkey guardrails
"""
from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from . import Base


class GuardrailViolation(Base):
    __tablename__ = "guardrail_violations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)

    # Violation Details
    violation_type = Column(String(50), nullable=False)  # prompt_injection, pii_detected, content_filter, etc.
    severity = Column(String(20), nullable=False, default="medium")  # low, medium, high, critical
    description = Column(Text, nullable=False)

    # Context Information
    endpoint = Column(String(100), nullable=False)  # /auth/messages, /api/tasks, etc.
    method = Column(String(10), nullable=False)  # POST, GET, etc.
    content_type = Column(String(20), nullable=False)  # input, output

    # Original Content (for audit/analysis)
    original_content = Column(Text, nullable=True)  # Store original content that triggered violation
    sanitized_content = Column(Text, nullable=True)  # Store sanitized version if applicable

    # Portkey Response Data
    portkey_response = Column(JSON, nullable=True)  # Full Portkey guardrail response
    portkey_status_code = Column(Integer, nullable=True)  # 246, 446, etc.

    # Detection Metadata
    detection_rules = Column(JSON, nullable=True)  # Which specific rules were triggered
    confidence_score = Column(Integer, nullable=True)  # 0-100 confidence in detection

    # Admin Actions
    resolved = Column(Boolean, default=False)
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Additional Security Metadata
    ip_address = Column(String(45), nullable=True)  # IPv4/IPv6 support
    user_agent = Column(String(500), nullable=True)
    session_id = Column(String(255), nullable=True)

    # Multi-tenant Security
    tenant_isolation_level = Column(String(20), default="organization")  # organization, user, agent
    cross_tenant_risk = Column(Boolean, default=False)  # Flag for potential cross-tenant violations

    # Relationships
    space = relationship("Space", back_populates="guardrail_violations")
    user = relationship("User", back_populates="guardrail_violations", foreign_keys=[user_id])
    agent = relationship("Agent", back_populates="guardrail_violations")
    resolved_by_user = relationship("User", foreign_keys=[resolved_by])

    def __repr__(self):
        return f"<GuardrailViolation(id={self.id}, type='{self.violation_type}', severity='{self.severity}')>"

    @property
    def is_high_risk(self) -> bool:
        """Check if this is a high-risk violation requiring immediate attention"""
        return self.severity in ['high', 'critical'] or self.cross_tenant_risk

    @property
    def days_unresolved(self) -> int:
        """Calculate days since violation was detected"""
        from datetime import datetime, timezone
        if self.resolved:
            return 0
        return (datetime.now(timezone.utc) - self.created_at.replace(tzinfo=timezone.utc)).days

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            "id": str(self.id),
            "violation_type": self.violation_type,
            "severity": self.severity,
            "description": self.description,
            "endpoint": self.endpoint,
            "method": self.method,
            "content_type": self.content_type,
            "resolved": self.resolved,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "days_unresolved": self.days_unresolved,
            "is_high_risk": self.is_high_risk,
            "user_id": str(self.user_id),
            "agent_id": str(self.agent_id) if self.agent_id else None,
            "confidence_score": self.confidence_score,
            "cross_tenant_risk": self.cross_tenant_risk
        }
