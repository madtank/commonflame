"""User PAT (Personal Access Token) endpoints.

Credentials authenticate a principal (user), NOT an agent.
allowed_agent_ids is an optional narrowing restriction — it can only
further reduce access, not grant it. It is NOT a replacement for
ownership/assignment authorization.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ...core.credential_service import (
    VALID_SCOPES,
    create_credential,
    list_credentials,
    revoke_credential,
)
from ...core.rls import get_secure_session, SecureSession
from ...models.credential import Credential

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/keys", tags=["credentials"])

SecureSessionDep = Annotated[SecureSession, Depends(get_secure_session)]


# --- Schemas ---

class CreateKeyRequest(BaseModel):
    name: str | None = None
    scopes: list[str] | None = Field(default=None, description="Defaults to ['api:read', 'api:write']")
    agent_scope: str = Field(
        default="all",
        description="Agent targeting scope: 'all' (unrestricted), 'user' (no agents), 'agents' (only listed), 'unbound' (binds to first agent on use)",
    )
    allowed_agent_ids: list[str] | None = Field(
        default=None,
        description="Required when agent_scope='agents'. Agent IDs this key can target.",
    )
    agent_id: str | None = Field(
        default=None,
        description="Bind this credential to an agent. Token inherits agent's space policy.",
    )
    bound_agent_id: str | None = Field(
        default=None,
        description="Alias for agent_id (frontend compat).",
    )
    audience: str = Field(
        default="cli",
        pattern="^(cli|mcp|both)$",
        description="Target audience: 'cli' (API only), 'mcp' (MCP only), 'both' (API + MCP)",
    )


class KeyResponse(BaseModel):
    credential_id: str
    key_id: str
    token: str
    name: str | None
    scopes: list[str]
    created_at: str


class KeyMetadata(BaseModel):
    credential_id: str
    key_id: str
    name: str | None
    scopes: list[str]
    agent_scope: str
    allowed_agent_ids: list[str] | None
    last_used_at: str | None
    created_at: str
    revoked_at: str | None


# --- Helpers ---

async def _validate_agent_ids(session: SecureSession, agent_ids: list[str]) -> None:
    """Validate that all agent IDs belong to the caller's space."""
    if not agent_ids:
        return
    from ...models.agent import Agent
    from ...core.agent_space import agents_in_space_subquery
    result = await session.db.execute(
        select(Agent.id).where(
            Agent.id.in_([UUID(aid) for aid in agent_ids]),
            Agent.id.in_(agents_in_space_subquery(UUID(session.space_id))),
        )
    )
    found = {str(row[0]) for row in result.all()}
    missing = set(agent_ids) - found
    if missing:
        raise HTTPException(status_code=400, detail=f"Agent IDs not found in this space: {list(missing)}")


# --- Endpoints ---

@router.post("", status_code=201)
async def create_key(
    body: CreateKeyRequest,
    session: SecureSessionDep,
) -> KeyResponse:
    """Create a user PAT. The token is returned ONCE and cannot be retrieved later."""
    # Validate scopes against allowlist
    if body.scopes:
        invalid = set(body.scopes) - VALID_SCOPES
        if invalid:
            raise HTTPException(status_code=400, detail=f"Invalid scopes: {list(invalid)}. Allowed: {list(VALID_SCOPES)}")

    # Validate agent_scope
    valid_scopes = {"all", "user", "agents", "unbound"}
    if body.agent_scope not in valid_scopes:
        raise HTTPException(status_code=400, detail=f"Invalid agent_scope: {body.agent_scope!r}. Allowed: {sorted(valid_scopes)}")

    if body.agent_scope == "unbound" and body.allowed_agent_ids:
        raise HTTPException(status_code=400, detail="Unbound scope cannot have allowed_agent_ids — it binds on first use.")

    # Validate allowed_agent_ids belong to caller's space
    if body.agent_scope != "unbound":
        await _validate_agent_ids(session, body.allowed_agent_ids or [])

    # Validate bound agent exists in caller's space
    bound_agent_id = None
    effective_agent_id = body.agent_id or body.bound_agent_id
    if effective_agent_id:
        await _validate_agent_ids(session, [effective_agent_id])
        bound_agent_id = UUID(effective_agent_id)

    try:
        token, cred = await create_credential(
            session.db,
            space_id=UUID(session.space_id),
            principal_type="user",
            principal_id=session.user.id,
            credential_type="pat",
            name=body.name,
            scopes=body.scopes,
            agent_scope=body.agent_scope,
            allowed_agent_ids=body.allowed_agent_ids,
            bound_agent_id=bound_agent_id,
            created_by_principal_type="user",
            created_by_principal_id=session.user.id,
            audience=body.audience,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await session.db.commit()
    return KeyResponse(
        credential_id=str(cred.id),
        key_id=cred.key_id,
        token=token,
        name=cred.name,
        scopes=cred.scopes or [],
        created_at=cred.created_at.isoformat(),
    )


@router.get("")
async def list_keys(session: SecureSessionDep) -> list[KeyMetadata]:
    """List the current user's credentials (metadata only, no secrets)."""
    creds = await list_credentials(session.db, "user", session.user.id)
    return [
        KeyMetadata(
            credential_id=str(c.id),
            key_id=c.key_id,
            name=c.name,
            scopes=c.scopes or [],
            agent_scope=c.agent_scope or "all",
            allowed_agent_ids=[str(a) for a in c.allowed_agent_ids] if c.allowed_agent_ids else None,
            last_used_at=c.last_used_at.isoformat() if c.last_used_at else None,
            created_at=c.created_at.isoformat(),
            revoked_at=c.revoked_at.isoformat() if c.revoked_at else None,
        )
        for c in creds
    ]


@router.delete("/{credential_id}", status_code=204)
async def revoke_key(
    credential_id: str,
    session: SecureSessionDep,
):
    """Revoke a credential (soft delete via revoked_at). Only the owning user can revoke."""
    revoked = await revoke_credential(
        session.db, UUID(credential_id), principal_id=session.user.id,
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="Credential not found")
    await session.db.commit()


@router.post("/{credential_id}/rotate", status_code=201)
async def rotate_key(
    credential_id: str,
    session: SecureSessionDep,
) -> KeyResponse:
    """Revoke the old credential and create a new one with the same settings.

    Uses SELECT FOR UPDATE to prevent concurrent rotation race conditions.
    """
    # Lock the row to prevent concurrent rotation
    result = await session.db.execute(
        select(Credential)
        .where(
            Credential.id == UUID(credential_id),
            Credential.principal_id == session.user.id,  # ownership check
        )
        .with_for_update()
    )
    old_cred = result.scalar_one_or_none()
    if old_cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    if old_cred.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Credential already revoked")

    # Revoke old
    await revoke_credential(
        session.db, UUID(credential_id), principal_id=session.user.id,
    )

    # Create new with same settings
    token, new_cred = await create_credential(
        session.db,
        space_id=UUID(session.space_id),
        principal_type=old_cred.principal_type,
        principal_id=old_cred.principal_id,
        credential_type=old_cred.credential_type,
        name=old_cred.name,
        scopes=old_cred.scopes,
        agent_scope=old_cred.agent_scope or "all",
        allowed_agent_ids=old_cred.allowed_agent_ids,
        created_by_principal_type="user",
        created_by_principal_id=session.user.id,
    )
    await session.db.commit()
    return KeyResponse(
        credential_id=str(new_cred.id),
        key_id=new_cred.key_id,
        token=token,
        name=new_cred.name,
        scopes=new_cred.scopes or [],
        created_at=new_cred.created_at.isoformat(),
    )
