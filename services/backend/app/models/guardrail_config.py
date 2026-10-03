"""
Guardrail Configuration Model
Manages database-driven security configurations for Portkey guardrails
"""
from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from . import Base


class GuardrailConfig(Base):
    __tablename__ = "guardrail_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)

    # Configuration Details
    config_name = Column(String(100), nullable=False)  # e.g., "prompt_injection_blocker"
    config_type = Column(String(50), nullable=False)   # e.g., "prompt_injection", "pii_detection"
    endpoint_pattern = Column(String(200), nullable=False)  # e.g., "/mcp/messages,/auth/messages"

    # Portkey Configuration (JSONB for flexibility)
    portkey_config = Column(JSON, nullable=False)  # Portkey-specific configuration
    enabled = Column(Boolean, default=True, nullable=False)

    # Rule Priority and Actions
    priority = Column(Integer, default=100, nullable=False)  # Higher number = higher priority
    action_on_violation = Column(String(20), default="log", nullable=False)  # log, deny, sanitize
    fallback_config = Column(JSON, nullable=True)  # Fallback configuration if primary fails

    # Timestamps and Audit
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    space = relationship("Space", back_populates="guardrail_configs", foreign_keys=[space_id])
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<GuardrailConfig(name='{self.config_name}', type='{self.config_type}', space_id='{self.space_id}')>"

    @property
    def endpoints(self):
        """Parse endpoint_pattern into a list of endpoints"""
        if not self.endpoint_pattern:
            return []
        return [ep.strip() for ep in self.endpoint_pattern.split(',') if ep.strip()]

    def matches_endpoint(self, endpoint_path: str) -> bool:
        """Check if this configuration applies to the given endpoint"""
        for pattern in self.endpoints:
            if pattern in endpoint_path:
                return True
        return False

    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "id": str(self.id),
            "space_id": str(self.space_id),
            "config_name": self.config_name,
            "config_type": self.config_type,
            "endpoint_pattern": self.endpoint_pattern,
            "endpoints": self.endpoints,
            "portkey_config": self.portkey_config,
            "enabled": self.enabled,
            "priority": self.priority,
            "action_on_violation": self.action_on_violation,
            "fallback_config": self.fallback_config,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": str(self.created_by) if self.created_by else None
        }
