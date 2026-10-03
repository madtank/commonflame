"""
AX-AGENT-MGMT-001 §9: Agent Management Gateway.

Single entry point for all agent management operations.
Wires: auth extraction → policy evaluation → audit recording.

Every mutating agent management path MUST go through authorize_agent_management().
This function returns a ManagementAuthorization if allowed, or raises HTTPException.

Usage:
    from app.core.agent_management_gateway import authorize_agent_management

    @router.post("/agents")
    async def create_agent(session: SecureSession, request: Request, ...):
        auth = await authorize_agent_management(
            session=session, request=request,
            action="create", fields=body.dict(),
        )
        # auth.decision.allowed is guaranteed True here
        # auth.actor has validated identity
        # audit entry already written
        ...
"""
from __future__ import annotations

import logging
import uuid as uuid_mod
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_management_auth import (
    AgentManagementActor,
    SessionLike,
    extract_management_actor,
)
from app.core.agent_management_policy import (
    PolicyDecision,
    evaluate_agent_management_policy,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManagementAuthorization:
    """Result of a successful authorization check."""
    actor: AgentManagementActor
    decision: PolicyDecision
    correlation_id: UUID
    audit_id: UUID  # ID of the audit entry written


async def authorize_agent_management(
    *,
    session: SessionLike,
    request: Request,
    action: str,
    target_agent_id: UUID | None = None,
    target_space_id: UUID | None = None,
    source_space_id: UUID | None = None,
    destination_space_id: UUID | None = None,
    fields: dict | None = None,
    proposal_id: UUID | None = None,
    require_admin: bool = False,
    correlation_id: UUID | None = None,
) -> ManagementAuthorization:
    """
    Authorize an agent management action.

    Flow:
    1. Extract actor from request (auth + delegation validation)
    2. Evaluate policy (central policy engine)
    3. Write audit entry (always, for both allowed and denied)
    4. If denied: raise HTTPException with detail
    5. If requires_proposal: raise HTTPException 202 with proposal info
    6. If allowed: return ManagementAuthorization

    This is the ONLY function external code should call for agent management auth.
    """
    corr_id = correlation_id or uuid_mod.uuid4()

    # 1. Extract actor
    actor = await extract_management_actor(
        session, request, require_admin=require_admin,
    )

    # 2. Evaluate policy
    decision = await evaluate_agent_management_policy(
        session.db,
        actor_user_id=actor.user_id,
        actor_agent_id=actor.agent_id,
        actor_mode=actor.mode,
        actor_space_id=actor.space_id,
        action=action,
        target_agent_id=target_agent_id,
        target_space_id=target_space_id,
        source_space_id=source_space_id,
        destination_space_id=destination_space_id,
        fields=fields,
        proposal_id=proposal_id,
    )

    # 3. Write audit entry (always — both allowed and denied)
    audit_id = await _write_audit(
        db=session.db,
        actor=actor,
        action=action,
        target_agent_id=target_agent_id,
        fields=fields,
        decision=decision,
        correlation_id=corr_id,
        request=request,
        proposal_id=proposal_id,
    )

    # 4. Handle decision
    if decision.requires_proposal:
        logger.info(
            "MGMT_PROPOSAL_REQUIRED action=%s actor=%s/%s target=%s corr=%s reason=%s",
            action, actor.mode, actor.user_id or actor.agent_id,
            target_agent_id, corr_id, decision.reason,
        )
        # Commit audit before raising so denied/proposal rows survive the rollback
        await session.db.commit()
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail={
                "status": "proposal_required",
                "reason": decision.reason,
                "required_approvals": decision.required_approvals,
                "correlation_id": str(corr_id),
            },
        )

    if not decision.allowed:
        logger.warning(
            "MGMT_DENIED action=%s actor=%s/%s target=%s corr=%s reason=%s",
            action, actor.mode, actor.user_id or actor.agent_id,
            target_agent_id, corr_id, decision.reason,
        )
        # Commit audit before raising so the denial row survives the rollback
        await session.db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=decision.reason,
        )

    logger.info(
        "MGMT_ALLOWED action=%s actor=%s/%s target=%s corr=%s",
        action, actor.mode, actor.user_id or actor.agent_id,
        target_agent_id, corr_id,
    )

    return ManagementAuthorization(
        actor=actor,
        decision=decision,
        correlation_id=corr_id,
        audit_id=audit_id,
    )


# ────────────────────────────────────────────────────────────────────
# Audit recording
# ────────────────────────────────────────────────────────────────────

async def _write_audit(
    *,
    db: AsyncSession,
    actor: AgentManagementActor,
    action: str,
    target_agent_id: UUID | None,
    fields: dict | None,
    decision: PolicyDecision,
    correlation_id: UUID,
    request: Request,
    proposal_id: UUID | None,
) -> UUID:
    """
    Write an immutable audit entry for a management action.

    Written transactionally with the same session — if the caller's
    transaction rolls back, the audit entry rolls back too.
    For denied actions, the caller should still commit (or use a
    separate session).
    """
    from app.models.agent_management import AgentManagementAudit

    audit_id = uuid_mod.uuid4()

    # Extract client IP (X-Forwarded-For or direct)
    ip_address = _extract_client_ip(request)

    audit = AgentManagementAudit(
        id=audit_id,
        space_id=actor.space_id,
        actor_user_id=actor.user_id,
        actor_agent_id=actor.agent_id,
        actor_mode=actor.mode,
        actor_space_role=actor.space_role,
        action=action,
        target_agent_id=target_agent_id,
        proposal_id=proposal_id,
        request_fields=fields,
        policy_version="v1",
        auth_path=_determine_auth_path(request),
        policy_decision="allowed" if decision.allowed else "denied",
        denial_reason=decision.reason if not decision.allowed else None,
        correlation_id=correlation_id,
        ip_address=ip_address,
    )
    db.add(audit)
    # Flush so the audit row gets an ID and is visible within the current
    # transaction.  For denied/proposal outcomes the caller commits immediately
    # after so the row survives the outer rollback.
    await db.flush()
    return audit_id


def _extract_client_ip(request: Request) -> str | None:
    """Extract client IP, preferring X-Forwarded-For (GCP Cloud Run sets this)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First IP in chain is the client
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _determine_auth_path(request: Request) -> str:
    """Classify the authentication path used for this request."""
    auth_header = request.headers.get("authorization", "")
    if "axp_" in auth_header:
        return "pat"
    # Check for agent token markers
    on_behalf_of = request.headers.get("x-on-behalf-of")
    if on_behalf_of:
        return "concierge_delegation"
    api_key = request.headers.get("x-api-key")
    if api_key:
        return "internal_api_key"
    return "bearer_token"
