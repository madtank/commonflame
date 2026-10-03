from sqlalchemy import Column, String, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from . import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    current_space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"))  # Current active space context
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=True)  # Nullable for GitHub SSO users
    full_name = Column(String(100))
    username = Column(String(50), unique=True)
    role = Column(String(20), default="user")  # user, admin, agent_manager
    active = Column(Boolean, default=True)
    token_version = Column(Integer, default=0)  # for emergency token revocation
    violations_count = Column(Integer, default=0)  # count of guardrail violations

    # GitHub SSO fields
    github_id = Column(String(255), unique=True, nullable=True)  # GitHub user ID
    github_username = Column(String(255), nullable=True)  # GitHub username
    github_avatar_url = Column(String(500), nullable=True)  # GitHub profile picture
    auth_provider = Column(String(50), default="github")  # 'github' or 'local'
    last_login_at = Column(DateTime(timezone=True), nullable=True)  # throttled login tracking (lockdown rollback)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    space = relationship("Space", back_populates="users", foreign_keys=[space_id])
    current_space = relationship("Space", foreign_keys=[current_space_id], overlaps="current_users")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    agents = relationship("Agent", back_populates="user", foreign_keys="[Agent.user_id]", cascade="all, delete-orphan")
    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="user", cascade="all, delete-orphan")
    posted_tasks = relationship("Task", back_populates="posted_by_user", cascade="all, delete-orphan")
    guardrail_violations = relationship("GuardrailViolation", back_populates="user", foreign_keys="GuardrailViolation.user_id", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}', role='{self.role}')>"
