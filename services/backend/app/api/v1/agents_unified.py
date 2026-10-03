"""
Unified Agent Management API — /api/v1/agents/

Single API surface for all consumers: frontend, CLI, SDK, MCP.
Thin route handlers that delegate to the service layer.

IMPORTANT: Static paths (check-name, models, me, presence, heartbeat)
MUST be defined BEFORE the dynamic {identifier} path parameter routes.
FastAPI uses first-match routing.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.authorization import verify_space_actor_access, verify_space_membership
from app.core.rls import SecureSession, get_secure_session
from app.models.agent import Agent
from app.models.space_membership import SpaceMembership
from app.schemas.agent import (
    AgentCreateRequest,
    AgentUpdateRequest,
    DetailLevel,
    SortOrder,
    serialize_agent,
)
from app.services.agent_management_service import (
    archive_expired_ephemeral_agents,
    build_ephemeral_cleanup_dry_run,
    create_agent,
    delete_agent,
    get_agent_by_identifier,
    list_agents,
    resolve_agent_by_identifier,
    update_agent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents-v2"])


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

async def _authorize_agent_management(
    session: SecureSession,
    on_behalf_of: str | None = None,
) -> uuid.UUID:
    """Authorize agent management and return the owning user_id.

    Cases:
    1. User-direct (no agent context): return session.user.id
    2. Space agent (origin=space_agent) + X-On-Behalf-Of:
       verify target user is in same space, return target user_id
    3. Delegated agent (can_manage_agents, NOT space_agent):
       return session.user.id (PAT owner)
    4. Else: 403
    """
    if not session.agent_id:
        return session.user.id

    result = await session.db.execute(
        select(Agent).where(Agent.id == uuid.UUID(session.agent_id))
    )
    calling_agent = result.scalar_one_or_none()
    if not calling_agent:
        raise HTTPException(status_code=403, detail="Calling agent not found")

    # Space agent delegation via X-On-Behalf-Of
    if calling_agent.origin == "space_agent":
        if not on_behalf_of:
            raise HTTPException(
                status_code=403,
                detail="Space agents must specify X-On-Behalf-Of header",
            )
        try:
            target_user_id = uuid.UUID(on_behalf_of)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid X-On-Behalf-Of user ID")

        await verify_space_membership(session.db, target_user_id, session.space_id)

        logger.info(
            "AX_DELEGATION agent=%s on_behalf_of=%s space=%s",
            session.agent_id, on_behalf_of, session.space_id,
        )
        return target_user_id

    # Delegated agent with can_manage_agents
    if not calling_agent.can_manage_agents:
        raise HTTPException(
            status_code=403,
            detail="This agent does not have agent management permission",
        )
    return session.user.id


# ---------------------------------------------------------------------------
# Collection endpoints (no path params)
# ---------------------------------------------------------------------------

async def _require_lifecycle_archive_admin(session: SecureSession, space_id: str) -> None:
    """Require space-admin authority for destructive lifecycle cleanup.

    Read-only dry-run remains available to authorized space actors; archive runs
    are space-wide mutations and must not be reachable by ordinary members.
    """
    if session.agent_id:
        calling_agent = await session.db.execute(
            select(Agent).where(Agent.id == uuid.UUID(session.agent_id))
        )
        agent = calling_agent.scalar_one_or_none()
        if agent and agent.can_manage_agents:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent lifecycle archive requires agent-management authority",
        )

    membership = await session.db.execute(
        select(SpaceMembership.role).where(
            SpaceMembership.space_id == uuid.UUID(str(space_id)),
            SpaceMembership.user_id == session.user.id,
        )
    )
    if membership.scalar_one_or_none() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required to archive lifecycle cleanup candidates",
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent_endpoint(
    body: AgentCreateRequest,
    session: SecureSession = Depends(get_secure_session),
    x_on_behalf_of: str | None = Header(None, alias="X-On-Behalf-Of"),
    detail: DetailLevel = Query(DetailLevel.summary),
):
    """Create a new agent."""
    owner_id = await _authorize_agent_management(session, x_on_behalf_of)
    is_delegation = session.agent_id is not None

    agent = await create_agent(
        db=session.db,
        owner_id=owner_id,
        space_id=session.space_id,
        data=body,
        user=session.user,
        is_delegation=is_delegation,
    )
    return serialize_agent(agent, detail, current_user_id=str(session.user.id))


@router.get("")
async def list_agents_endpoint(
    session: SecureSession = Depends(get_secure_session),
    space_id: str | None = Query(None, description="Explicit space. Defaults to session space."),
    owner: str | None = Query(None, description="'me' for own agents only."),
    search: str | None = Query(None, description="Case-insensitive name search."),
    sort: SortOrder = Query(SortOrder.name),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    detail: DetailLevel = Query(DetailLevel.summary),
    include_dormant: bool = Query(False, description="Include idle/dormant offline agents."),
    include_archived: bool = Query(False, description="Include lifecycle/global archived agents."),
    include_offline: bool = Query(False, description="Include active offline agents beyond live-online default."),
    view_scope: Literal["relevant", "recommended", "online", "send_now", "all", "in_space"] = Query(
        "relevant",
        description=(
            "Roster scope. Default/relevant and online/send_now stay live-online biased; "
            "all/in_space returns the broad visible in-space roster without archived rows."
        ),
    ),
):
    """List agents visible in the current (or specified) space."""
    target_space = session.space_id
    if space_id:
        await verify_space_actor_access(
            session.db,
            user_id=session.user.id,
            space_id=space_id,
            is_agent=getattr(session, "is_agent", False),
            agent_id=getattr(session, "agent_id", None),
        )
        target_space = space_id

    scope = view_scope.lower()
    # Widget/MCP contract: the default stays compact/send-now-biased, but
    # explicit All/In-space must be an expansive visible roster. Preserve the
    # older boolean knobs as overrides for clients that already use them.
    if scope in {"all", "in_space"}:
        include_dormant = True
        include_offline = True
    elif scope in {"online", "send_now"}:
        include_dormant = False
        include_offline = False

    return await list_agents(
        db=session.db,
        space_id=target_space,
        user_id=session.user.id,
        owner=owner,
        search=search,
        sort=sort,
        limit=limit,
        offset=offset,
        detail=detail,
        include_dormant=include_dormant,
        include_archived=include_archived,
        include_offline=include_offline,
    )


# ---------------------------------------------------------------------------
# Static path endpoints — MUST come before {identifier} routes
# ---------------------------------------------------------------------------

@router.get("/check-name")
async def check_name_endpoint(
    name: str = Query(..., min_length=3, max_length=50),
    space_id: str | None = Query(None),
    session: SecureSession = Depends(get_secure_session),
):
    """Check if an agent name is available in the given space."""
    from app.core.agent_constraints import validate_agent_name

    is_valid, error_msg = validate_agent_name(name)
    if not is_valid:
        return {"available": False, "reason": error_msg}

    target_space = space_id or session.space_id
    existing = await session.db.execute(
        select(Agent).where(
            func.lower(Agent.name) == name.lower(),
            Agent.space_id == target_space,
        )
    )
    if existing.scalar_one_or_none():
        return {"available": False, "reason": "Name already taken in this space"}

    return {"available": True}


@router.get("/models")
async def list_models_endpoint(
    session: SecureSession = Depends(get_secure_session),
):
    """List available LLM models filtered by user role."""
    from app.core.models_config import get_models_for_user_role

    user_role = getattr(session.user, "role", "user") or "user"
    models = get_models_for_user_role(user_role)
    return {"models": models}


@router.get("/lifecycle/cleanup-dry-run")
async def lifecycle_cleanup_dry_run_endpoint(
    session: SecureSession = Depends(get_secure_session),
    space_id: str | None = Query(None, description="Explicit space. Defaults to session space."),
    limit: int = Query(100, ge=1, le=500),
):
    """Read-only report of legacy ephemeral agents that match cleanup patterns.

    No archive/delete mutation is performed; durable/protected exclusions are
    reported separately so operators can review before any future destructive run.
    """
    target_space = session.space_id
    if space_id:
        await verify_space_actor_access(
            session.db,
            user_id=session.user.id,
            space_id=space_id,
            is_agent=getattr(session, "is_agent", False),
            agent_id=getattr(session, "agent_id", None),
        )
        target_space = space_id
    return await build_ephemeral_cleanup_dry_run(session.db, target_space, limit=limit)


@router.post("/lifecycle/cleanup-archive-expired")
async def lifecycle_cleanup_archive_expired_endpoint(
    session: SecureSession = Depends(get_secure_session),
    space_id: str | None = Query(None, description="Explicit space. Defaults to session space."),
    ttl_days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=500),
    dry_run: bool = Query(True, description="Preview archive candidates without mutating rows."),
):
    """Archive reviewed legacy ephemeral agents whose TTL elapsed.

    This is the guarded destructive follow-up to cleanup-dry-run: dry_run defaults
    to true, and archive eligibility is recomputed at execution time from the
    same protection filters plus archive_suggested_at + ttl_days.
    """
    target_space = session.space_id
    if space_id:
        await verify_space_actor_access(
            session.db,
            user_id=session.user.id,
            space_id=space_id,
            is_agent=getattr(session, "is_agent", False),
            agent_id=getattr(session, "agent_id", None),
        )
        target_space = space_id
    if not dry_run:
        await _require_lifecycle_archive_admin(session, target_space)
    actor_agent_id = None
    if getattr(session, "agent_id", None):
        actor_agent_id = uuid.UUID(str(session.agent_id))
    actor_mode = "concierge_safeguard" if actor_agent_id else "space_admin_moderation"
    return await archive_expired_ephemeral_agents(
        session.db,
        target_space,
        ttl_days=ttl_days,
        limit=limit,
        dry_run=dry_run,
        actor_user_id=session.user.id,
        actor_agent_id=actor_agent_id,
        actor_mode=actor_mode,
    )


# ---------------------------------------------------------------------------
# /me alias — resolves authenticated agent from token/header
# MUST be before {identifier} to prevent "me" matching as a literal name
# ---------------------------------------------------------------------------

@router.get("/me")
async def get_agent_me_unified(
    request: Request,
    session: SecureSession = Depends(get_secure_session),
):
    """Resolve authenticated agent from token claims or X-Agent-Name header."""
    from app.core.agent_resolver import resolve_agent as _resolve_agent
    agent = await _resolve_agent(session.user, session.db, request)
    # Re-use the get_agent_by_identifier for consistent serialization
    result = await get_agent_by_identifier(
        db=session.db,
        identifier=str(agent.id),
        owner_id=session.user.id,
        detail=DetailLevel.full,
        current_user_id=str(session.user.id),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Could not resolve agent identity")
    return result


@router.get("/availability")
async def bulk_agent_availability_endpoint(
    space_id: str | None = Query(None),
    session: SecureSession = Depends(get_secure_session),
):
    """Expose the widget/SDK availability contract ahead of the dynamic identifier route."""
    from app.api.v1.api_v1 import bulk_agent_availability

    return await bulk_agent_availability(
        space_id=space_id,
        user=session.user,
        session=session,
    )


@router.get("/presence")
async def bulk_agent_presence_endpoint(
    space_id: str | None = Query(None),
    session: SecureSession = Depends(get_secure_session),
):
    """Expose the bulk presence contract ahead of the dynamic identifier route."""
    from app.api.v1.api_v1 import bulk_agent_presence

    return await bulk_agent_presence(
        space_id=space_id,
        user=session.user,
        session=session,
    )


# ---------------------------------------------------------------------------
# Space placement — set default space + pinned/locked
# ---------------------------------------------------------------------------

class SpacePlacementRequest(BaseModel):
    """Set an agent's default space and pinned status."""
    space_id: str
    pinned: bool = False


@router.post("/{identifier}/placement")
async def set_agent_placement(
    identifier: str,
    body: SpacePlacementRequest,
    session: SecureSession = Depends(get_secure_session),
):
    """Set an agent's default operating space and pinned status.

    - space_id: The space to set as the agent's default
    - pinned: If true, lock the agent to this space (cannot roam)

    Requires ownership of the agent and membership in the target space.
    """
    import uuid as uuid_mod
    from app.core.agent_space import grant_space_access
    from app.services.redis_sse_broker import publish_placement_changed

    # Resolve agent
    agent = await _resolve_owned_agent(session, identifier)

    # Capture the pre-mutation placement BEFORE grant_space_access promotes the
    # new default and BEFORE agent.space_id is overwritten below. This is the
    # OLD space stream key we signal so the live (still old-subscribed) gateway
    # connection can re-resolve + reconnect instantly.
    old_space = agent.space_id

    # Verify user is member of target space
    try:
        target_space = uuid_mod.UUID(body.space_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid space_id format") from exc
    await verify_space_membership(session.db, session.user.id, str(target_space))

    # Grant access to target space if not already granted, and set as default
    await grant_space_access(
        session.db,
        agent_id=agent.id,
        space_id=target_space,
        is_default=True,
    )

    # Update pinned/locked status
    agent.space_locked = body.pinned

    # Dual-write: keep legacy space_id in sync
    agent.space_id = target_space

    await session.db.commit()

    # Instant move: signal the live old-space SSE connection to re-resolve +
    # reconnect. Guarded/best-effort inside the helper (no-op when old is falsy
    # or old == new; broker swallows publish errors).
    if old_space and str(old_space) != str(target_space):
        await publish_placement_changed(
            old_space_id=old_space,
            new_space_id=target_space,
            agent_id=agent.id,
        )

    return {
        "status": "ok",
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "space_id": str(target_space),
        "pinned": body.pinned,
    }


async def _resolve_owned_agent(session: SecureSession, identifier: str):
    """Resolve agent by UUID or name, verify ownership."""
    import uuid as uuid_mod
    try:
        agent_uuid = uuid_mod.UUID(identifier)
        result = await session.db.execute(
            select(Agent).where(Agent.id == agent_uuid)
        )
    except ValueError:
        result = await session.db.execute(
            select(Agent).where(
                func.lower(Agent.name) == identifier.lower(),
                Agent.user_id == session.user.id,
            )
        )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{identifier}' not found")
    if agent.user_id != session.user.id:
        raise HTTPException(status_code=403, detail="You can only change placement for your own agents")
    return agent


# ---------------------------------------------------------------------------
# Dynamic {identifier} endpoints — AFTER static paths
# ---------------------------------------------------------------------------

@router.get("/{identifier}")
async def get_agent_endpoint(
    identifier: str,
    session: SecureSession = Depends(get_secure_session),
    detail: DetailLevel = Query(DetailLevel.summary),
):
    """Get an agent by UUID or name. Must be owned by the authenticated user."""
    result = await get_agent_by_identifier(
        db=session.db,
        identifier=identifier,
        owner_id=session.user.id,
        detail=detail,
        current_user_id=str(session.user.id),
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Agent '{identifier}' not found")
    return result


@router.put("/{identifier}")
async def update_agent_endpoint(
    identifier: str,
    body: AgentUpdateRequest,
    session: SecureSession = Depends(get_secure_session),
    x_on_behalf_of: str | None = Header(None, alias="X-On-Behalf-Of"),
    detail: DetailLevel = Query(DetailLevel.summary),
):
    """Update an agent. Must be owned by the authenticated user (or delegated)."""
    owner_id = await _authorize_agent_management(session, x_on_behalf_of)
    is_delegation = session.agent_id is not None

    agent = await resolve_agent_by_identifier(session.db, identifier, owner_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{identifier}' not found")

    updated = await update_agent(
        db=session.db,
        agent=agent,
        data=body,
        user=session.user,
        is_delegation=is_delegation,
    )
    return serialize_agent(updated, detail, current_user_id=str(session.user.id))


@router.delete("/{identifier}")
async def delete_agent_endpoint(
    identifier: str,
    session: SecureSession = Depends(get_secure_session),
    x_on_behalf_of: str | None = Header(None, alias="X-On-Behalf-Of"),
):
    """Delete an agent. Must be owned by the authenticated user (or delegated)."""
    owner_id = await _authorize_agent_management(session, x_on_behalf_of)

    agent = await resolve_agent_by_identifier(session.db, identifier, owner_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{identifier}' not found")

    return await delete_agent(session.db, agent)
