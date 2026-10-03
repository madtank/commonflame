import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from . import Base


class ContextCatalogEntry(Base):
    __tablename__ = "context_catalog_entries"
    __table_args__ = (
        Index("ix_context_catalog_entries_space_shelf", "space_id", "shelf"),
        Index("ix_context_catalog_entries_space_pinned", "space_id", "pinned"),
        Index("ix_context_catalog_entries_space_created_at", "space_id", "created_at"),
        Index("ix_context_catalog_entries_current_context_object_id", "current_context_object_id"),
        Index("ix_context_catalog_entries_current_artifact_version_id", "current_artifact_version_id"),
        Index("ix_context_catalog_entries_current_state_version_id", "current_state_version_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    artifact_type = Column(String(100), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(String(100), nullable=True)
    shelf = Column(String(80), nullable=True)
    current_context_object_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_objects.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_artifact_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_artifact_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    current_state_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_state_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    thumbnail_ref = Column(Text, nullable=True)
    pinned = Column(Boolean, nullable=False, default=False)
    status = Column(String(20), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    objects = relationship(
        "ContextObject",
        back_populates="catalog_entry",
        cascade="all, delete-orphan",
        foreign_keys="ContextObject.catalog_entry_id",
    )
    current_context_object = relationship(
        "ContextObject",
        foreign_keys=[current_context_object_id],
        post_update=True,
    )
    current_artifact_version = relationship(
        "ContextArtifactVersion",
        foreign_keys=[current_artifact_version_id],
        post_update=True,
    )
    current_state_version = relationship(
        "ContextStateVersion",
        foreign_keys=[current_state_version_id],
        post_update=True,
    )

    def __repr__(self):
        return f"<ContextCatalogEntry(id={self.id}, artifact_type='{self.artifact_type}', title='{self.title}')>"


class ContextObject(Base):
    __tablename__ = "context_objects"
    __table_args__ = (
        Index("ix_context_objects_catalog_entry_id", "catalog_entry_id"),
        Index("ix_context_objects_space_action_set", "space_id", "action_set_id"),
        Index("ix_context_objects_space_created_at", "space_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(80), nullable=False, default="interactive_context")
    artifact_kind = Column(String(80), nullable=False)
    action_set_id = Column(String(100), nullable=False)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    catalog_entry = relationship(
        "ContextCatalogEntry",
        back_populates="objects",
        foreign_keys=[catalog_entry_id],
    )
    artifact_versions = relationship(
        "ContextArtifactVersion",
        back_populates="context_object",
        cascade="all, delete-orphan",
        foreign_keys="ContextArtifactVersion.context_object_id",
    )
    state_versions = relationship(
        "ContextStateVersion",
        back_populates="context_object",
        cascade="all, delete-orphan",
        foreign_keys="ContextStateVersion.context_object_id",
    )
    patches = relationship(
        "ContextPatch",
        back_populates="context_object",
        cascade="all, delete-orphan",
        foreign_keys="ContextPatch.context_object_id",
    )
    audit_events = relationship(
        "ContextAuditEvent",
        back_populates="context_object",
        cascade="all, delete-orphan",
        foreign_keys="ContextAuditEvent.context_object_id",
    )

    def __repr__(self):
        return f"<ContextObject(id={self.id}, action_set_id='{self.action_set_id}')>"


class ContextArtifactVersion(Base):
    __tablename__ = "context_artifact_versions"
    __table_args__ = (
        UniqueConstraint("context_object_id", "sha256", name="uq_context_artifact_versions_object_sha256"),
        Index("ix_context_artifact_versions_catalog_entry_id", "catalog_entry_id"),
        Index("ix_context_artifact_versions_context_object_id", "context_object_id"),
        Index("ix_context_artifact_versions_space_created_at", "space_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    context_object_id = Column(UUID(as_uuid=True), ForeignKey("context_objects.id", ondelete="CASCADE"), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(80), nullable=False)
    content_ref = Column(Text, nullable=True)
    attachment_id = Column(UUID(as_uuid=True), ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sandbox_policy = Column(JSONB, nullable=True, default=dict)

    context_object = relationship(
        "ContextObject",
        back_populates="artifact_versions",
        foreign_keys=[context_object_id],
    )

    def __repr__(self):
        return f"<ContextArtifactVersion(id={self.id}, kind='{self.kind}', sha256='{self.sha256}')>"


class ContextStateVersion(Base):
    __tablename__ = "context_state_versions"
    __table_args__ = (
        UniqueConstraint("context_object_id", "version_number", name="uq_context_state_versions_object_version"),
        Index("ix_context_state_versions_catalog_entry_id", "catalog_entry_id"),
        Index("ix_context_state_versions_context_object_id", "context_object_id"),
        Index("ix_context_state_versions_space_created_at", "space_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    context_object_id = Column(UUID(as_uuid=True), ForeignKey("context_objects.id", ondelete="CASCADE"), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    data = Column(JSONB, nullable=False, default=dict)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    context_object = relationship(
        "ContextObject",
        back_populates="state_versions",
        foreign_keys=[context_object_id],
    )

    def __repr__(self):
        return f"<ContextStateVersion(id={self.id}, version_number={self.version_number})>"


class ContextPatch(Base):
    __tablename__ = "context_patches"
    __table_args__ = (
        Index("ix_context_patches_catalog_entry_id", "catalog_entry_id"),
        Index("ix_context_patches_context_object_id", "context_object_id"),
        Index("ix_context_patches_space_status_created_at", "space_id", "status", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    context_object_id = Column(UUID(as_uuid=True), ForeignKey("context_objects.id", ondelete="CASCADE"), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    base_artifact_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_artifact_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_state_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_state_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patch_type = Column(String(80), nullable=False)
    payload = Column(JSONB, nullable=False)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="proposed")
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    context_object = relationship(
        "ContextObject",
        back_populates="patches",
        foreign_keys=[context_object_id],
    )
    base_artifact_version = relationship("ContextArtifactVersion", foreign_keys=[base_artifact_version_id])
    base_state_version = relationship("ContextStateVersion", foreign_keys=[base_state_version_id])

    def __repr__(self):
        return f"<ContextPatch(id={self.id}, patch_type='{self.patch_type}', status='{self.status}')>"


class ContextAuditEvent(Base):
    __tablename__ = "context_audit_events"
    __table_args__ = (
        UniqueConstraint(
            "context_object_id",
            "actor_type",
            "actor_id",
            "action_id",
            "idempotency_key",
            name="uq_context_audit_events_actor_action_idempotency",
        ),
        Index("ix_context_audit_events_catalog_entry_id", "catalog_entry_id"),
        Index("ix_context_audit_events_context_object_id", "context_object_id"),
        Index("ix_context_audit_events_space_created_at", "space_id", "created_at"),
        Index("ix_context_audit_events_action", "action_set_id", "action_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_entry_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    context_object_id = Column(UUID(as_uuid=True), ForeignKey("context_objects.id", ondelete="CASCADE"), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    action_set_id = Column(String(100), nullable=False)
    action_id = Column(String(100), nullable=False)
    source_lane = Column(String(40), nullable=False)
    actor_type = Column(String(20), nullable=False)
    actor_id = Column(String(100), nullable=False)
    policy_decision = Column(String(40), nullable=False)
    base_artifact_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_artifact_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    base_state_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_state_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    result_artifact_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_artifact_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    result_state_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("context_state_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key = Column(String(128), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    context_object = relationship(
        "ContextObject",
        back_populates="audit_events",
        foreign_keys=[context_object_id],
    )
    base_artifact_version = relationship("ContextArtifactVersion", foreign_keys=[base_artifact_version_id])
    base_state_version = relationship("ContextStateVersion", foreign_keys=[base_state_version_id])
    result_artifact_version = relationship("ContextArtifactVersion", foreign_keys=[result_artifact_version_id])
    result_state_version = relationship("ContextStateVersion", foreign_keys=[result_state_version_id])

    def __repr__(self):
        return f"<ContextAuditEvent(id={self.id}, action='{self.action_set_id}.{self.action_id}')>"
