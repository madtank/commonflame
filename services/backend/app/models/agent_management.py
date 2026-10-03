"""
AX-AGENT-MGMT-001: Agent management authorization models.

Tables: proposals, approvals, audit, outbox, space overrides.
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from . import Base


class AgentSpaceOverride(Base):
    """Space-local presentation/policy overlays for an attached agent."""
    __tablename__ = "agent_space_overrides"

    agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True)
    display_name_override = Column(Text, nullable=True)
    tool_allowlist_override = Column(JSONB, nullable=True)
    routing_priority_override = Column(Integer, nullable=True)
    visibility_override = Column(JSONB, nullable=True)
    set_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentManagementProposal(Base):
    """
    Pending management action requiring human approval.

    Lifecycle: pending → partially_approved → approved → executed
                                            → rejected / cancelled / expired
    """
    __tablename__ = "agent_management_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    proposal_type = Column(String(50), nullable=False)
    status = Column(String(30), nullable=False, server_default="pending")
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    target_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    target_owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    target_space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    source_space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    destination_space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    approval_requirements = Column(JSONB, nullable=False)
    proposed_payload = Column(JSONB, nullable=False)
    payload_hash = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    idempotency_key = Column(Text, nullable=True, unique=True)
    version = Column(Integer, nullable=False, server_default="1")


class AgentManagementApproval(Base):
    """One approval/rejection decision on a proposal."""
    __tablename__ = "agent_management_approvals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proposal_id = Column(UUID(as_uuid=True), ForeignKey("agent_management_proposals.id", ondelete="CASCADE"), nullable=False)
    approver_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    approver_basis = Column(String(20), nullable=False)  # owner | space_admin | platform
    decision = Column(String(20), nullable=False)  # approved | rejected
    approval_patch = Column(JSONB, nullable=True)
    approved_payload_hash = Column(Text, nullable=False)
    decided_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AgentManagementAudit(Base):
    """Immutable audit log for all management actions (allowed and denied)."""
    __tablename__ = "agent_management_audit"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    actor_mode = Column(String(40), nullable=False)
    actor_space_role = Column(String(20), nullable=True)
    action = Column(String(50), nullable=False)
    target_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    target_owner_type = Column(String(10), nullable=True)
    proposal_id = Column(UUID(as_uuid=True), nullable=True)
    request_fields = Column(JSONB, nullable=True)
    old_state = Column(JSONB, nullable=True)
    new_state = Column(JSONB, nullable=True)
    policy_version = Column(String(20), nullable=False, server_default="v1")
    auth_path = Column(String(50), nullable=False)
    approval_basis = Column(JSONB, nullable=True)
    policy_decision = Column(String(10), nullable=False)  # allowed | denied
    denial_reason = Column(Text, nullable=True)
    correlation_id = Column(UUID(as_uuid=True), nullable=True)
    ip_address = Column(String(45), nullable=True)


class AgentManagementOutbox(Base):
    """Transactional outbox for reliable event publication."""
    __tablename__ = "agent_management_outbox"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    event_type = Column(String(50), nullable=False)
    payload = Column(JSONB, nullable=False)
    space_id = Column(UUID(as_uuid=True), nullable=False)
    correlation_id = Column(UUID(as_uuid=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, server_default="0")
