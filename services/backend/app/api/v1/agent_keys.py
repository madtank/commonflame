"""
Agent Keys API — provision and manage headless client credentials (SEP-1046).

POST   /api/v1/agents/{agent_id}/keys       — create a new key pair
GET    /api/v1/agents/{agent_id}/keys       — list keys (secrets are NOT returned)
DELETE /api/v1/agents/{agent_id}/keys/{key_id} — revoke a key
"""

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, select

from ...core.rls import SecureSession, get_secure_session
from ...core.security import get_password_hash
from ...models.agent import Agent
from ...models.agent_key import AgentKey

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agents", tags=["agent-keys"])


# ─── Schemas ─────────────────────────────────────────────

class CreateKeyRequest(BaseModel):
    label: Optional[str] = None
    scopes: Optional[str] = "mcp:read mcp:write"

class CreateKeyResponse(BaseModel):
    """Returned ONCE at creation time — client_secret is never shown again."""
    key_id: str
    client_id: str
    client_secret: str  # plaintext, shown only once
    label: Optional[str]
    scopes: str
    created_at: str

class KeyInfo(BaseModel):
    key_id: str
    client_id: str
    label: Optional[str]
    scopes: Optional[str]
    is_active: bool
    last_used_at: Optional[str]
    created_at: str
    expires_at: Optional[str]


# ─── Helpers ─────────────────────────────────────────────

def _generate_client_id() -> str:
    """ax_<32 hex chars> — globally unique, easy to grep in logs."""
    return f"ax_{secrets.token_hex(16)}"

def _generate_client_secret() -> str:
    """64-char URL-safe secret."""
    return secrets.token_urlsafe(48)


async def _verify_agent_ownership(
    session: SecureSession, agent_id: uuid.UUID
) -> Agent:
    """Load an agent and verify the caller owns it. Returns the Agent or raises."""
    result = await session.db.execute(
        select(Agent).where(and_(Agent.id == agent_id, Agent.user_id == session.user.id))
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


# ─── Endpoints ───────────────────────────────────────────

@router.post("/{agent_id}/keys", response_model=CreateKeyResponse, status_code=201)
async def create_agent_key(
    agent_id: uuid.UUID,
    body: CreateKeyRequest,
    session: SecureSession = Depends(get_secure_session),
):
    """Create a new client_id / client_secret pair for an agent."""
    agent = await _verify_agent_ownership(session, agent_id)

    # Enforce one active key per agent (rotation path should be used for replacement).
    existing_active = await session.db.execute(
        select(AgentKey).where(
            AgentKey.agent_id == agent_id,
            AgentKey.is_active == True,
        )
    )
    if existing_active.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active key already exists for this agent. Use rotate endpoint.",
        )

    client_id = _generate_client_id()
    client_secret = _generate_client_secret()
    hashed = get_password_hash(client_secret)

    key = AgentKey(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=agent.user_id,
        client_id=client_id,
        client_secret_hash=hashed,
        name=body.label,
        scopes=body.scopes or "mcp:read mcp:write",
    )
    session.db.add(key)
    await session.db.commit()
    await session.db.refresh(key)

    logger.info(f"Created agent key {client_id[:12]}… for agent {agent.name}")

    return CreateKeyResponse(
        key_id=str(key.id),
        client_id=client_id,
        client_secret=client_secret,  # shown once
        label=key.name,
        scopes=key.scopes or "mcp:read mcp:write",
        created_at=key.created_at.isoformat(),
    )


@router.get("/{agent_id}/keys", response_model=list[KeyInfo])
async def list_agent_keys(
    agent_id: uuid.UUID,
    session: SecureSession = Depends(get_secure_session),
):
    """List all keys for an agent (secrets are NOT included)."""
    await _verify_agent_ownership(session, agent_id)
    result = await session.db.execute(
        select(AgentKey).where(AgentKey.agent_id == agent_id).order_by(AgentKey.created_at.desc())
    )
    keys = result.scalars().all()

    return [
        KeyInfo(
            key_id=str(k.id),
            client_id=k.client_id,
            label=k.name,
            scopes=k.scopes,
            is_active=k.is_active,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            created_at=k.created_at.isoformat(),
            expires_at=None,  # expires_at not yet in schema
        )
        for k in keys
    ]


@router.post("/{agent_id}/keys/{key_id}/rotate", response_model=CreateKeyResponse)
async def rotate_agent_key(
    agent_id: uuid.UUID,
    key_id: uuid.UUID,
    session: SecureSession = Depends(get_secure_session),
):
    """Rotate credentials for an existing key.

    Generates a new client_id + client_secret, invalidates the old key atomically,
    and returns the new credentials once. Old key is immediately dead — no grace period.
    """
    await _verify_agent_ownership(session, agent_id)
    result = await session.db.execute(
        select(AgentKey).where(
            AgentKey.id == key_id,
            AgentKey.agent_id == agent_id,
            AgentKey.is_active == True,
        )
    )
    old_key = result.scalar_one_or_none()
    if not old_key:
        raise HTTPException(status_code=404, detail="Key not found or already inactive")

    # Generate new credentials
    new_client_id = _generate_client_id()
    new_client_secret = _generate_client_secret()
    new_hashed = get_password_hash(new_client_secret)

    # Atomically invalidate old key and create new one
    old_key.is_active = False

    new_key = AgentKey(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=old_key.user_id,
        client_id=new_client_id,
        client_secret_hash=new_hashed,
        name=old_key.name,
        scopes=old_key.scopes or "mcp:read mcp:write",
    )
    session.db.add(new_key)
    await session.db.commit()
    await session.db.refresh(new_key)

    logger.info(
        f"Rotated agent key: old={old_key.client_id[:12]}… → new={new_client_id[:12]}… "
        f"for agent {agent_id}"
    )

    return CreateKeyResponse(
        key_id=str(new_key.id),
        client_id=new_client_id,
        client_secret=new_client_secret,  # shown once
        label=new_key.name,
        scopes=new_key.scopes or "mcp:read mcp:write",
        created_at=new_key.created_at.isoformat(),
    )


@router.delete("/{agent_id}/keys/{key_id}", status_code=204)
async def revoke_agent_key(
    agent_id: uuid.UUID,
    key_id: uuid.UUID,
    session: SecureSession = Depends(get_secure_session),
):
    """Revoke (soft-delete) an agent key."""
    await _verify_agent_ownership(session, agent_id)
    result = await session.db.execute(
        select(AgentKey).where(
            AgentKey.id == key_id,
            AgentKey.agent_id == agent_id,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    key.is_active = False
    await session.db.commit()
    logger.info(f"Revoked agent key {key.client_id[:12]}…")
