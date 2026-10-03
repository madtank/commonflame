"""AccessRequest — invite-only waitlist gate (IP-protection lockdown Phase 2).

A single row per email address requesting access to aX. New (unknown) emails
arriving on the upstream human auth path are recorded as ``pending`` and Jacob is
emailed once with a one-click approve link. Status flips to ``approved`` when the
link is clicked; the user's next sign-in then provisions normally.

Design: docs/plans/2026-05-28-invite-only-waitlist-gate-design.md
"""
import uuid

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from . import Base


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Lookup key. Always stored lower-cased in code (no citext in this codebase).
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=True)
    github_username = Column(String, nullable=True)
    github_id = Column(String, nullable=True)
    # One of: pending | approved | denied
    status = Column(String, nullable=False, default="pending")
    emailed_at = Column(DateTime(timezone=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<AccessRequest(email='{self.email}', status='{self.status}')>"
