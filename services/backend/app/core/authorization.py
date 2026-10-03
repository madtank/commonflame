"""Space authorization helpers — defense-in-depth membership checks."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agent_space_access import AgentSpaceAccess
from ..models.space import Space
from ..models.space_membership import SpaceMembership


async def verify_space_membership(
    db: AsyncSession,
    user_id: UUID,
    space_id: str | UUID,
) -> None:
    """Raise 403 if *user_id* is not a member of *space_id*."""
    is_member = await db.scalar(
        select(
            exists().where(
                SpaceMembership.user_id == user_id,
                SpaceMembership.space_id == UUID(str(space_id)),
            )
        )
    )
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not a member of space {space_id}",
        )


async def verify_space_actor_access(
    db: AsyncSession,
    *,
    user_id: UUID,
    space_id: str | UUID,
    is_agent: bool = False,
    agent_id: str | UUID | None = None,
) -> None:
    """Raise 403 unless the current actor can operate in *space_id*.

    User principals are authorized by human space membership. Agent principals
    are authorized by active agent-space access, with `spaces.space_agent_id`
    accepted as a belt-and-suspenders fallback for registered space agents.
    """
    if not is_agent:
        await verify_space_membership(db, user_id, space_id)
        return

    if not agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent is not attached to space {space_id}",
        )

    try:
        space_uuid = UUID(str(space_id))
        agent_uuid = UUID(str(agent_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid agent or space identifier",
        ) from exc

    has_active_access = await db.scalar(
        select(
            exists().where(
                AgentSpaceAccess.agent_id == agent_uuid,
                AgentSpaceAccess.space_id == space_uuid,
                AgentSpaceAccess.state == "active",
            )
        )
    )
    if has_active_access:
        return

    is_registered_space_agent = await db.scalar(
        select(
            exists().where(
                Space.id == space_uuid,
                Space.space_agent_id == agent_uuid,
            )
        )
    )
    if is_registered_space_agent:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Agent is not attached to space {space_id}",
    )
