"""OAuth Authorization Server state for MCP/client authentication."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.sql import func

from . import Base


class OAuthClient(Base):
    """Dynamically registered OAuth client metadata."""

    __tablename__ = "oauth_clients"

    client_id = Column(String(255), primary_key=True, nullable=False)
    client_secret = Column(String(255), nullable=True)
    client_name = Column(String(255), nullable=False)
    redirect_uris = Column(ARRAY(Text), nullable=False)
    grant_types = Column(ARRAY(Text), nullable=False)
    response_types = Column(ARRAY(Text), nullable=False)
    token_endpoint_auth_method = Column(String(50), nullable=False, default="none")
    scope = Column(Text, nullable=True)
    registration_access_token = Column(String(255), nullable=True, unique=True)
    registration_client_uri = Column(String(500), nullable=True)
    client_metadata = Column("metadata", JSONB, nullable=True, default=dict)
    is_trusted = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class OAuthAuthorizationCode(Base):
    """Short-lived authorization code for PKCE code exchange."""

    __tablename__ = "oauth_authorization_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_hash = Column(String(64), unique=True, nullable=False, index=True)
    client_id = Column(String(255), nullable=False, index=True)
    redirect_uri = Column(Text, nullable=False)
    scope = Column(Text, nullable=False)
    resource = Column(Text, nullable=False)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    authorized_space_id = Column(UUID(as_uuid=True), nullable=True)
    code_challenge = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OAuthDeviceCode(Base):
    """Device authorization state for headless MCP clients."""

    __tablename__ = "oauth_device_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_code_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_code_hash = Column(String(64), unique=True, nullable=False, index=True)
    client_id = Column(String(255), nullable=False, index=True)
    scope = Column(Text, nullable=True)
    resource = Column(Text, nullable=True)
    owner_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    authorized_space_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OAuthRefreshToken(Base):
    """Rotating refresh grant for OAuth-authenticated MCP clients."""

    __tablename__ = "oauth_refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    refresh_token_hash = Column(String(64), unique=True, nullable=False, index=True)
    client_id = Column(String(255), nullable=False, index=True)
    owner_user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    authorized_space_id = Column(UUID(as_uuid=True), nullable=True)
    agent_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    scope = Column(Text, nullable=False)
    resource = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    rotated_from_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "ix_oauth_refresh_tokens_active",
            "client_id",
            "owner_user_id",
            "revoked_at",
        ),
    )
