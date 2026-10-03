"""Agent management endpoints (AUTH-SPEC-001 §8).

Programmatic agent lifecycle management via user_admin JWTs.
API-first: create agents, manage status, list — all via API.

Routes:
  POST /agents/manage/create  — Create agent
  GET /agents/manage/list     — List owned agents
  PATCH /agents/manage/{id}   — Update agent
"""
import logging
import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.admin_auth import AdminPrincipal, require_scope
from ...core.database import get_db_session
from ...models.agent import Agent
from ...models.agent_space_access import AgentSpaceAccess

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents/manage", tags=["agent-management"])


# --- Schemas ---

class CreateAgentRequest(BaseModel):
    name: str = Field(..., description="Agent name (unique within space)")
    description: str | None = Field(default=None)
    system_prompt: str | None = Field(default=None)
    model: str | None = Field(default=None)
    space_id: str | None = Field(default=None, description="Space to create in (defaults to owner's home space)")


class AgentResponse(BaseModel):
    id: str
    name: str
    status: str
    space_id: str
    description: str | None
    model: str | None
    origin: str | None
    created_at: str


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    status: str | None = None


# --- Endpoints ---

@router.post("/create", status_code=201, response_model=AgentResponse)
async def create_agent(
    body: CreateAgentRequest,
    principal: AdminPrincipal = Depends(require_scope("agents.create")),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new agent. Requires agents.create scope."""
    # Rate limit: 10 agent creations per hour per user (§12.2)
    from .credentials import _check_rate_limit
    await _check_rate_limit("agent_creation", principal.user_id, limit=10, window=3600)

    from uuid import UUID
    from app.core.authorization import verify_space_membership
    space_id = UUID(body.space_id) if body.space_id else principal.user.space_id

    # Verify caller has access to the target space before creating an agent there
    # Run in privileged mode since space_memberships may have RLS policies
    await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
    try:
        await verify_space_membership(db, principal.user.id, space_id)
    except Exception:
        await db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))
        raise

    agent_id = uuid_mod.uuid4()

    try:
        # Check name uniqueness in space
        existing = await db.execute(
            select(Agent).where(Agent.name == body.name, Agent.space_id == space_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail={
                "error": "agent_exists",
                "message": f"Agent '{body.name}' already exists in this space",
            })

        agent = Agent(
            id=agent_id,
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            model=body.model,
            space_id=space_id,
            user_id=principal.user.id,
            owner_user_id=principal.user.id,
            owner_space_id=space_id,
            home_space_id=space_id,
            created_by_user_id=principal.user.id,
            status="active",
            agent_type="mcp",
            origin="mcp",
        )
        db.add(agent)
        await db.flush()

        # Grant default space access
        space_access = AgentSpaceAccess(
            agent_id=agent_id,
            space_id=space_id,
            is_default=True,
            state="active",
            attached_by_user_id=principal.user.id,
        )
        db.add(space_access)
        await db.flush()
    finally:
        await db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))

    await db.commit()

    logger.info(
        "AGENT_CREATED agent=%s name=%s space=%s owner=%s",
        agent_id, body.name, space_id, principal.user_id,
    )

    return AgentResponse(
        id=str(agent_id),
        name=body.name,
        status="active",
        space_id=str(space_id),
        description=body.description,
        model=body.model,
        origin="mcp",
        created_at=agent.created_at.isoformat() if agent.created_at else "",
    )


@router.get("/list", response_model=list[AgentResponse])
async def list_agents(
    principal: AdminPrincipal = Depends(require_scope("agents.list", "agents.create")),
    db: AsyncSession = Depends(get_db_session),
):
    """List agents owned by the authenticated user."""
    await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
    try:
        result = await db.execute(
            select(Agent).where(Agent.owner_user_id == principal.user.id).order_by(Agent.created_at.desc())
        )
        agents = result.scalars().all()
    finally:
        await db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))

    return [
        AgentResponse(
            id=str(a.id),
            name=a.name,
            status=a.status or "active",
            space_id=str(a.space_id),
            description=a.description,
            model=a.model,
            origin=a.origin,
            created_at=a.created_at.isoformat() if a.created_at else "",
        )
        for a in agents
    ]


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    principal: AdminPrincipal = Depends(require_scope("agents.create")),
    db: AsyncSession = Depends(get_db_session),
):
    """Update an agent. Requires agents.create scope."""
    from uuid import UUID

    await db.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))
    try:
        result = await db.execute(
            select(Agent).where(Agent.id == UUID(agent_id), Agent.owner_user_id == principal.user.id)
        )
        agent = result.scalar_one_or_none()
    finally:
        await db.execute(text("SELECT set_config('app.is_privileged', 'false', true)"))

    if not agent:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Agent not found or not owned by you"})

    if body.name is not None:
        agent.name = body.name
    if body.description is not None:
        agent.description = body.description
    if body.system_prompt is not None:
        agent.system_prompt = body.system_prompt
    if body.model is not None:
        agent.model = body.model
    if body.status is not None:
        agent.status = body.status

    await db.commit()

    logger.info("AGENT_UPDATED agent=%s by=%s", agent_id, principal.user_id)

    return AgentResponse(
        id=str(agent.id),
        name=agent.name,
        status=agent.status or "active",
        space_id=str(agent.space_id),
        description=agent.description,
        model=agent.model,
        origin=agent.origin,
        created_at=agent.created_at.isoformat() if agent.created_at else "",
    )
