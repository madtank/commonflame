"""
Shared agent resolution from MCP token claims.

The unified /api/v1 surface and related auth helpers resolve an Agent from
the bearer token's agent_id / agent_name claims here so identity resolution
stays consistent across API callers.
"""

import logging
import os
import uuid

from fastapi import HTTPException, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.agent_constraints import validate_agent_name
from ..core.redis_client import redis_client
from ..core.agent_space import grant_space_access
from ..core.agent_toggles import get_enabled_tools_defaults
from ..models.agent import Agent
from ..models.user import User
from ..services.agent_control_service import AgentControlService

logger = logging.getLogger(__name__)
agent_control_service = AgentControlService(redis_client)


def _effective_space_id(user: User) -> str:
    """Get the effective space_id from the authenticated user."""
    return str(
        getattr(user, "_effective_space_id", None)
        or getattr(user, "current_space_id", None)
        or user.space_id
    )


def _effective_space_uuid(user: User) -> uuid.UUID:
    """Get the effective space_id as a UUID."""
    value = (
        getattr(user, "_effective_space_id", None)
        or getattr(user, "current_space_id", None)
        or user.space_id
    )
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


async def resolve_agent(
    user: User,
    db: AsyncSession,
    request: Request | None = None,
) -> Agent:
    """Resolve the agent associated with the current MCP token.

    MCP tokens carry agent_id/agent_name in claims (set on user._effective_*).
    Falls back to X-Agent-Name header (for upstream JWTs that lack agent claims).
    If AUTO_REGISTER_AGENTS is enabled and the agent doesn't exist, creates it.
    """
    header_agent_id = request.headers.get("X-Agent-Id") if request else None
    header_agent_name = request.headers.get("X-Agent-Name") if request else None

    agent_id = getattr(user, "_agent_id", None) or header_agent_id
    agent_name = getattr(user, "_agent_name", None) or header_agent_name
    effective_org = _effective_space_id(user)
    effective_space_uuid = _effective_space_uuid(user)

    if isinstance(agent_name, str):
        agent_name = agent_name.strip()
    if isinstance(agent_id, str):
        agent_id = agent_id.strip()
        if agent_id:
            try:
                agent_id = uuid.UUID(agent_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid X-Agent-Id header") from exc
        else:
            agent_id = None

    is_space_agent_principal = (
        getattr(user, "_principal_type", None) == "agent"
        or getattr(user, "_principal_agent_id", None) is not None
    )
    is_header_targeted_user_flow = (
        not is_space_agent_principal
        and bool(header_agent_name)
        and getattr(user, "_agent_id", None) is None
    )

    async def _validate_runtime_state(agent: Agent) -> Agent:
        if (agent.status or "").lower() != "active":
            raise HTTPException(
                status_code=403,
                detail=f"Agent '{agent.name}' is not active",
            )

        control_state = await agent_control_service.get_control_state(
            agent_id=agent.id,
            space_id=agent.space_id,
            agent_slug=(agent.name or "").strip().lower() or None,
        )
        if control_state.is_disabled:
            raise HTTPException(
                status_code=403,
                detail=control_state.disabled_reason or f"Agent '{agent.name}' is disabled",
            )
        return agent

    async def _auto_register_header_agent(agent_name_value: str) -> Agent:
        valid, error_msg = validate_agent_name(agent_name_value)
        if not valid:
            raise HTTPException(status_code=400, detail=error_msg)

        from app.core.agent_space import agents_in_space_subquery
        existing_result = await db.execute(
            select(Agent).where(
                and_(
                    Agent.name == agent_name_value,
                    Agent.id.in_(agents_in_space_subquery(effective_org)),
                )
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            if existing.user_id != user.id:
                raise HTTPException(
                    status_code=403,
                    detail=f"Agent '{agent_name_value}' exists but is owned by another user.",
                )
            return await _validate_runtime_state(existing)

        agent = Agent(
            id=uuid.uuid4(),
            name=agent_name_value,
            user_id=user.id,
            space_id=effective_space_uuid,
            owner_type="user",
            owner_user_id=user.id,
            owner_space_id=None,
            home_space_id=effective_space_uuid,
            management_class="regular",
            created_by_user_id=user.id,
            origin="mcp",
            status="active",
            agent_type="assistant",
            enabled_tools=get_enabled_tools_defaults(),
        )
        db.add(agent)
        await db.flush()
        await grant_space_access(db, agent_id=agent.id, space_id=effective_space_uuid, is_default=True)
        await db.commit()
        await db.refresh(agent)
        logger.info(
            "Auto-registered MCP header-targeted agent: name=%s, user=%s, org=%s",
            agent_name_value, user.id, effective_org,
        )
        return await _validate_runtime_state(agent)

    from app.core.agent_space import agents_in_space_subquery
    if agent_id:
        filters = [
            Agent.id == agent_id,
            Agent.id.in_(agents_in_space_subquery(effective_org)),
        ]
        if not is_space_agent_principal:
            filters.append(Agent.user_id == user.id)
        result = await db.execute(select(Agent).where(and_(*filters)))
        agent = result.scalar_one_or_none()
        if agent:
            return await _validate_runtime_state(agent)

    if agent_name:
        filters = [
            Agent.name == agent_name,
            Agent.id.in_(agents_in_space_subquery(effective_org)),
        ]
        if not is_space_agent_principal:
            filters.append(Agent.user_id == user.id)
        result = await db.execute(select(Agent).where(and_(*filters)))
        agent = result.scalar_one_or_none()
        if agent:
            return await _validate_runtime_state(agent)

        if (
            is_header_targeted_user_flow
            or os.getenv("AUTO_REGISTER_AGENTS", "").lower() in ("true", "1", "yes")
        ):
            return await _auto_register_header_agent(agent_name)

    raise HTTPException(
        status_code=400,
        detail="Could not resolve agent identity from token. "
               "Ensure the token includes agent_id or send X-Agent-Name.",
    )
