import enum
import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class ArtifactType(str, enum.Enum):
    """
    Types of intelligence artifacts stored in the Workspace Intelligence Vault.

    - RESEARCH: Web search results, documentation lookups, API explorations
    - CONVERSATION_INSIGHT: Key insights extracted from agent conversations
    - TASK_STATE: Saved task context, decisions, and progress snapshots
    - SYSTEM_VALIDATION: Security checks, compliance validations, health reports
    """
    RESEARCH = "RESEARCH"
    CONVERSATION_INSIGHT = "CONVERSATION_INSIGHT"
    TASK_STATE = "TASK_STATE"
    SYSTEM_VALIDATION = "SYSTEM_VALIDATION"


class WorkspaceIntelligence(Base):
    """
    The Workspace Intelligence Vault - permanent storage for promoted agent artifacts.

    This is the "Living Artifact" model where data can be refined over time.
    Each update increments the version and archives the previous state.

    Key Features:
    - Multi-tenant isolation via space_id
    - Artifact type classification for Sentinel filtering
    - Version tracking for history/audit
    - Summary snippets for fast UI previews
    - Access counting for popularity metrics
    """
    __tablename__ = "workspace_intelligence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(100), nullable=False)
    key = Column(String(255), nullable=False, index=True)

    # Artifact classification for Sentinel filtering
    artifact_type = Column(
        Enum(ArtifactType, name='artifact_type_enum', create_type=False),
        nullable=False,
        default=ArtifactType.RESEARCH
    )

    # Core data storage
    payload = Column(JSONB, nullable=False)  # Full raw result for "Full Context"
    summary_snippet = Column(Text, nullable=True)  # 200-char auto-generated preview
    artifact_metadata = Column("metadata", JSONB, nullable=True)  # Confidence, tools, token costs

    # Versioning for Living Artifact model
    version = Column(Integer, nullable=False, default=1)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Usage tracking
    access_count = Column(Integer, server_default="0", nullable=False)

    # Relationships
    space = relationship("Space", backref="workspace_intelligence")
    history = relationship("WorkspaceIntelligenceHistory", back_populates="intelligence", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<WorkspaceIntelligence(id={self.id}, key='{self.key}', type={self.artifact_type.value}, v{self.version})>"


class WorkspaceIntelligenceHistory(Base):
    """
    Archive table for previous versions of workspace intelligence artifacts.

    When an artifact is updated, the previous version is archived here,
    enabling full audit trail and rollback capabilities.
    """
    __tablename__ = "workspace_intelligence_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    intelligence_id = Column(UUID(as_uuid=True), ForeignKey("workspace_intelligence.id", ondelete="CASCADE"), nullable=False, index=True)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(String(100), nullable=False)
    key = Column(String(255), nullable=False)

    artifact_type = Column(
        Enum(ArtifactType, name='artifact_type_enum', create_type=False),
        nullable=False
    )

    payload = Column(JSONB, nullable=False)
    summary_snippet = Column(Text, nullable=True)
    artifact_metadata = Column("metadata", JSONB, nullable=True)
    version = Column(Integer, nullable=False)

    # Original creation time of this version
    created_at = Column(DateTime(timezone=True), nullable=False)
    # When this version was archived
    archived_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    intelligence = relationship("WorkspaceIntelligence", back_populates="history")

    def __repr__(self):
        return f"<WorkspaceIntelligenceHistory(key='{self.key}', v{self.version}, archived={self.archived_at})>"
