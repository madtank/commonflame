"""Credential management endpoints (AUTH-SPEC-001 §8).

These endpoints require user_admin JWTs with specific scopes.
API-first: everything programmable, same as AWS IAM model.

Routes:
  POST /credentials/agent-pat  — Issue agent-bound PAT
  POST /credentials/enrollment — Issue enrollment (unbound) PAT
  DELETE /credentials/{id}     — Revoke a PAT
  GET /credentials             — List credentials
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.admin_auth import AdminPrincipal, require_scope
from ...core.credential_service import create_credential, revoke_credential, list_credentials
from ...core.database import get_db_session
from ...models.agent import Agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/credentials", tags=["credential-management"])
VALID_CREDENTIAL_AUDIENCES = {"cli", "mcp", "both"}


async def _check_rate_limit(action: str, user_id: str, limit: int, window: int) -> None:
    """Check rate limit via Redis. §12.2: PAT issuance 20/hr, agent creation 10/hr."""
    try:
        from ...core.redis_client import redis_client
        if not redis_client:
            return
        key = f"rate:{action}:{user_id}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, window)
        if count > limit:
            raise HTTPException(status_code=429, detail={
                "error": "rate_limited",
                "message": f"Rate limit exceeded: {limit} {action} per {window // 60} minutes",
            })
    except HTTPException:
        raise
    except Exception:
        pass  # Redis failure shouldn't block operations


# --- Schemas ---

class CreateAgentPatRequest(BaseModel):
    """Issue an agent-bound PAT."""
    agent_id: str = Field(..., description="Agent UUID to bind to")
    name: str | None = Field(default=None, description="Human-readable label")
    expires_in_days: int = Field(default=90, description="PAT lifetime in days")
    audience: str = Field(default="cli", description="Target audience: cli, mcp, or both")


class CreateEnrollmentPatRequest(BaseModel):
    """Issue an enrollment (unbound) PAT that binds on first exchange."""
    name: str | None = Field(default=None, description="Human-readable label")
    expires_in_hours: int = Field(default=1, description="Enrollment window in hours (default 1)")
    audience: str = Field(default="cli", description="Target audience: cli, mcp, or both")


class CredentialResponse(BaseModel):
    credential_id: str
    key_id: str
    token: str  # plaintext, shown once
    name: str | None
    bound_agent_id: str | None
    lifecycle_state: str
    audience: str
    expires_at: str | None
    created_at: str


class CredentialMetadata(BaseModel):
    credential_id: str
    key_id: str
    name: str | None
    bound_agent_id: str | None
    agent_scope: str
    audience: str
    lifecycle_state: str
    expires_at: str | None
    last_used_at: str | None
    created_at: str
    revoked_at: str | None


# --- Endpoints ---

@router.post("/agent-pat", status_code=201, response_model=CredentialResponse)
async def issue_agent_pat(
    body: CreateAgentPatRequest,
    principal: AdminPrincipal = Depends(require_scope("credentials.issue.agent")),
    db: AsyncSession = Depends(get_db_session),
):
    """Issue an agent-bound PAT (axp_a_). Requires credentials.issue.agent scope."""
    if body.audience not in VALID_CREDENTIAL_AUDIENCES:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_audience",
            "message": "audience must be one of: cli, mcp, both",
        })

    # Rate limit: 20 PAT issuances per hour per user (§12.2)
    await _check_rate_limit("pat_issuance", principal.user_id, limit=20, window=3600)

    # Verify agent exists and belongs to the caller's space
    await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
    try:
        result = await db.execute(
            select(Agent).where(
                Agent.id == UUID(body.agent_id),
                Agent.space_id == principal.user.space_id,
            )
        )
        agent = result.scalar_one_or_none()
    finally:
        await db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))

    if not agent:
        raise HTTPException(status_code=404, detail={"error": "agent_not_found", "message": f"Agent {body.agent_id} not found in your space"})

    expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    token, cred = await create_credential(
        db,
        space_id=agent.space_id,
        principal_type="user",
        principal_id=principal.user.id,
        credential_type="pat",
        name=body.name or f"agent-pat-{agent.name}",
        agent_scope="agents",
        allowed_agent_ids=[body.agent_id],
        bound_agent_id=UUID(body.agent_id),
        created_by_principal_type="user",
        created_by_principal_id=principal.user.id,
        expires_at=expires_at,
        audience=body.audience,
    )
    await db.commit()

    logger.info(
        "CREDENTIAL_ISSUED type=agent_pat agent=%s owner=%s credential=%s",
        body.agent_id, principal.user_id, cred.id,
    )

    return CredentialResponse(
        credential_id=str(cred.id),
        key_id=cred.key_id,
        token=token,
        name=cred.name,
        bound_agent_id=str(cred.bound_agent_id) if cred.bound_agent_id else None,
        lifecycle_state=cred.lifecycle_state,
        audience=cred.audience,
        expires_at=expires_at.isoformat(),
        created_at=cred.created_at.isoformat(),
    )


@router.post("/enrollment", status_code=201, response_model=CredentialResponse)
async def issue_enrollment_pat(
    body: CreateEnrollmentPatRequest,
    principal: AdminPrincipal = Depends(require_scope("credentials.issue.agent")),
    db: AsyncSession = Depends(get_db_session),
):
    """Issue an enrollment PAT (unbound, binds on first exchange). Requires credentials.issue.agent scope."""
    if body.audience not in VALID_CREDENTIAL_AUDIENCES:
        raise HTTPException(status_code=400, detail={
            "error": "invalid_audience",
            "message": "audience must be one of: cli, mcp, both",
        })

    # Rate limit: 20 PAT issuances per hour per user (§12.2)
    await _check_rate_limit("pat_issuance", principal.user_id, limit=20, window=3600)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)

    token, cred = await create_credential(
        db,
        space_id=principal.user.space_id,
        principal_type="user",
        principal_id=principal.user.id,
        credential_type="pat",
        name=body.name or "enrollment-token",
        agent_scope="unbound",
        created_by_principal_type="user",
        created_by_principal_id=principal.user.id,
        expires_at=expires_at,
        audience=body.audience,
    )
    # Set lifecycle_state to enrollment
    cred.lifecycle_state = "enrollment"
    await db.flush()
    await db.commit()

    logger.info(
        "CREDENTIAL_ISSUED type=enrollment owner=%s credential=%s expires=%s",
        principal.user_id, cred.id, expires_at.isoformat(),
    )

    return CredentialResponse(
        credential_id=str(cred.id),
        key_id=cred.key_id,
        token=token,
        name=cred.name,
        bound_agent_id=None,
        lifecycle_state="enrollment",
        audience=cred.audience,
        expires_at=expires_at.isoformat(),
        created_at=cred.created_at.isoformat(),
    )


@router.delete("/{credential_id}")
async def revoke_pat(
    credential_id: str,
    principal: AdminPrincipal = Depends(require_scope("credentials.revoke")),
    db: AsyncSession = Depends(get_db_session),
):
    """Revoke a PAT. Requires credentials.revoke scope."""
    await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
    try:
        revoked = await revoke_credential(
            db, UUID(credential_id),
            principal_id=principal.user.id,
        )
    finally:
        await db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))

    if not revoked:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Credential not found or not owned by you"})
    await db.commit()

    logger.info("CREDENTIAL_REVOKED credential=%s by=%s", credential_id, principal.user_id)
    return {"status": "revoked", "credential_id": credential_id}


@router.get("", response_model=list[CredentialMetadata])
async def list_user_credentials(
    principal: AdminPrincipal = Depends(require_scope("credentials.issue.agent")),
    db: AsyncSession = Depends(get_db_session),
):
    """List all credentials owned by the authenticated user."""
    await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
    try:
        creds = await list_credentials(db, "user", principal.user.id)
    finally:
        await db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))

    return [
        CredentialMetadata(
            credential_id=str(c.id),
            key_id=c.key_id,
            name=c.name,
            bound_agent_id=str(c.bound_agent_id) if c.bound_agent_id else None,
            agent_scope=c.agent_scope or "all",
            audience=c.audience or "cli",
            lifecycle_state=c.lifecycle_state or "active",
            expires_at=c.expires_at.isoformat() if c.expires_at else None,
            last_used_at=c.last_used_at.isoformat() if c.last_used_at else None,
            created_at=c.created_at.isoformat(),
            revoked_at=c.revoked_at.isoformat() if c.revoked_at else None,
        )
        for c in creds
    ]
