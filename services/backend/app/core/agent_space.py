"""Agent-space access helpers.

Central module for agent-space queries. Replaces scattered
or_(Agent.space_id == X, Agent.pinned_to_space == X) patterns.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_space_access import AgentSpaceAccess


def agents_in_space_subquery(space_id: UUID):
    """Subquery returning agent_ids with active access to a space.

    Only returns agents with state='active' in agent_space_access.
    Suspended or detached agents are excluded.
    """
    return select(AgentSpaceAccess.agent_id).where(
        and_(
            AgentSpaceAccess.space_id == space_id,
            AgentSpaceAccess.state == "active",
        )
    )


async def agent_has_space_access(db: AsyncSession, agent_id: UUID, space_id: UUID) -> bool:
    """Point check: does this agent have access to this space?"""
    result = await db.execute(
        select(AgentSpaceAccess.id).where(
            and_(
                AgentSpaceAccess.agent_id == agent_id,
                AgentSpaceAccess.space_id == space_id,
            )
        )
    )
    return result.scalar_one_or_none() is not None


async def grant_space_access(
    db: AsyncSession,
    agent_id: UUID,
    space_id: UUID,
    is_default: bool = False,
) -> AgentSpaceAccess:
    """Grant agent access to a space. Idempotent on the row itself.

    If the row already exists and is_default=True is requested,
    promotes the existing row to default (unsetting any prior default).
    """
    result = await db.execute(
        select(AgentSpaceAccess).where(
            and_(
                AgentSpaceAccess.agent_id == agent_id,
                AgentSpaceAccess.space_id == space_id,
            )
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        if is_default and not existing.is_default:
            # Promote existing row to default
            await db.execute(
                update(AgentSpaceAccess)
                .where(
                    and_(
                        AgentSpaceAccess.agent_id == agent_id,
                        AgentSpaceAccess.is_default.is_(True),
                    )
                )
                .values(is_default=False)
            )
            existing.is_default = True
            await db.flush()
        return existing

    # New row — if setting as default, unset any existing default first
    if is_default:
        await db.execute(
            update(AgentSpaceAccess)
            .where(
                and_(
                    AgentSpaceAccess.agent_id == agent_id,
                    AgentSpaceAccess.is_default.is_(True),
                )
            )
            .values(is_default=False)
        )

    row = AgentSpaceAccess(
        agent_id=agent_id,
        space_id=space_id,
        is_default=is_default,
    )
    db.add(row)
    await db.flush()
    return row


async def revoke_space_access(db: AsyncSession, agent_id: UUID, space_id: UUID) -> None:
    """Remove access. Raises 400 if it's the agent's last space.

    If the removed row was the default and other rows remain,
    reassigns default to the oldest remaining row.
    """
    # Find the row to remove
    result = await db.execute(
        select(AgentSpaceAccess).where(
            and_(
                AgentSpaceAccess.agent_id == agent_id,
                AgentSpaceAccess.space_id == space_id,
            )
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        return  # Nothing to revoke

    # Count current spaces
    all_result = await db.execute(
        select(AgentSpaceAccess)
        .where(AgentSpaceAccess.agent_id == agent_id)
        .order_by(AgentSpaceAccess.created_at.asc())
    )
    all_rows = list(all_result.scalars().all())

    if len(all_rows) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot revoke last space access. Agent must have at least one space.",
        )

    was_default = target.is_default

    await db.execute(
        delete(AgentSpaceAccess).where(AgentSpaceAccess.id == target.id)
    )

    # If deleted row was the default, reassign to oldest remaining
    if was_default:
        remaining = [r for r in all_rows if r.id != target.id]
        if remaining:
            await db.execute(
                update(AgentSpaceAccess)
                .where(AgentSpaceAccess.id == remaining[0].id)
                .values(is_default=True)
            )

    await db.flush()


async def set_default_space(db: AsyncSession, agent_id: UUID, space_id: UUID) -> None:
    """Set one space as default. Unsets previous default. Raises 404 if no access."""
    result = await db.execute(
        select(AgentSpaceAccess).where(
            and_(
                AgentSpaceAccess.agent_id == agent_id,
                AgentSpaceAccess.space_id == space_id,
            )
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Agent does not have access to this space.",
        )

    # Unset previous default
    await db.execute(
        update(AgentSpaceAccess)
        .where(
            and_(
                AgentSpaceAccess.agent_id == agent_id,
                AgentSpaceAccess.is_default.is_(True),
            )
        )
        .values(is_default=False)
    )

    # Set new default
    await db.execute(
        update(AgentSpaceAccess)
        .where(AgentSpaceAccess.id == row.id)
        .values(is_default=True)
    )
    await db.flush()


async def get_agent_spaces(db: AsyncSession, agent_id: UUID) -> list[AgentSpaceAccess]:
    """Get all spaces for an agent (subject to RLS — only spaces current user can see)."""
    result = await db.execute(
        select(AgentSpaceAccess).where(AgentSpaceAccess.agent_id == agent_id)
    )
    return list(result.scalars().all())


async def get_default_space(db: AsyncSession, agent_id: UUID) -> UUID | None:
    """Get agent's default space_id, or None."""
    result = await db.execute(
        select(AgentSpaceAccess.space_id).where(
            and_(
                AgentSpaceAccess.agent_id == agent_id,
                AgentSpaceAccess.is_default.is_(True),
            )
        )
    )
    row = result.scalar_one_or_none()
    return row if row else None


async def resolve_agent_space(
    db: AsyncSession,
    agent_id: UUID,
    explicit_space_id: UUID | None,
) -> UUID:
    """Resolve which space an agent action targets.

    1. Explicit space_id if provided (must be in agent's allowed spaces)
    2. If agent has exactly one allowed space, use that
    3. Agent's default space if set
    4. Error: space_id required

    This helper is ONLY for agent-targeted flows. It does NOT change
    the global _resolve_space_id() behavior.
    """
    if explicit_space_id is not None:
        has_access = await agent_has_space_access(db, agent_id, explicit_space_id)
        if not has_access:
            raise HTTPException(
                status_code=403,
                detail="Agent does not have access to the specified space.",
            )
        return explicit_space_id

    spaces = await get_agent_spaces(db, agent_id)

    if len(spaces) == 0:
        raise HTTPException(
            status_code=404,
            detail="Agent has no space access configured.",
        )

    if len(spaces) == 1:
        return spaces[0].space_id

    # Multiple spaces — try default
    for s in spaces:
        if s.is_default:
            return s.space_id

    raise HTTPException(
        status_code=400,
        detail="Agent has access to multiple spaces. Provide an explicit space_id.",
    )
