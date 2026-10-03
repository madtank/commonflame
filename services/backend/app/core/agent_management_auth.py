"""
AX-AGENT-MGMT-001 §8: Agent Management Authentication & Actor Context.

Extracts and validates actor identity for agent management operations.
This module bridges SecureSession (transport auth) → PolicyDecision (authorization).

Actor modes:
  user_direct          — Human acting on their own behalf
  concierge_delegated  — Concierge acting on behalf of a user (X-On-Behalf-Of)
  space_admin_moderation — Admin moderating space participation
  concierge_safeguard  — Concierge autonomous safety action
  platform             — Internal platform operation

X-On-Behalf-Of flow:
  1. Concierge authenticates via its RS256 agent token (SecureSession.is_agent=True)
  2. Concierge passes X-On-Behalf-Of: <user_id> header
  3. This module verifies the concierge is the space's concierge agent
  4. The on-behalf-of user is looked up and membership verified
  5. Actor context is assembled for policy evaluation
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.core.rls import SecureSession

logger = logging.getLogger(__name__)


class SessionLike(Protocol):
    """Minimal protocol for what we need from SecureSession."""
    db: AsyncSession
    user: object | None
    space_id: str
    agent_id: str | None
    is_agent: bool


# ────────────────────────────────────────────────────────────────────
# Actor context — the output of auth extraction
# ────────────────────────────────────────────────────────────────────

ActorMode = Literal[
    "user_direct",
    "concierge_delegated",
    "space_admin_moderation",
    "concierge_safeguard",
    "platform",
]


@dataclass(frozen=True)
class AgentManagementActor:
    """
    Validated actor context for agent management policy evaluation.

    All fields are verified before construction — callers can trust them.
    """
    mode: ActorMode
    user_id: UUID | None          # Authenticated human (or on-behalf-of user)
    agent_id: UUID | None         # Concierge agent ID (for delegation/safeguard)
    space_id: UUID                # Space where the action takes place
    space_role: str | None        # "admin" | "member" | None
    is_space_concierge: bool      # Whether actor_agent is the space's concierge


# ────────────────────────────────────────────────────────────────────
# Extraction from request
# ────────────────────────────────────────────────────────────────────

async def extract_management_actor(
    session: SessionLike,
    request: Request,
    *,
    require_admin: bool = False,
) -> AgentManagementActor:
    """
    Extract and validate actor context from an authenticated request.

    Determination logic:
    1. If session.is_agent AND X-On-Behalf-Of present → concierge_delegated
    2. If session.is_agent AND no X-On-Behalf-Of → concierge_safeguard
    3. If user AND require_admin=True → space_admin_moderation
    4. If user → user_direct

    Raises HTTPException 401/403 on validation failure.
    """
    db = session.db
    space_id = UUID(session.space_id) if isinstance(session.space_id, str) else session.space_id
    on_behalf_of = request.headers.get("x-on-behalf-of")

    # ── Agent-authenticated path (concierge delegation or safeguard) ──
    if session.is_agent and session.agent_id:
        agent_uuid = UUID(session.agent_id) if isinstance(session.agent_id, str) else session.agent_id

        # Verify this agent is actually a concierge
        is_concierge = await _verify_concierge_identity(db, agent_uuid, space_id)
        if not is_concierge:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agent management requires concierge authority",
            )

        if on_behalf_of:
            # ── Mode B: Concierge Delegated ──
            return await _build_delegated_actor(
                db, concierge_agent_id=agent_uuid,
                on_behalf_of_raw=on_behalf_of, space_id=space_id,
            )
        else:
            # ── Mode D: Concierge Safeguard ──
            return AgentManagementActor(
                mode="concierge_safeguard",
                user_id=None,
                agent_id=agent_uuid,
                space_id=space_id,
                space_role=None,
                is_space_concierge=True,
            )

    # ── User-authenticated path ──
    if not session.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for agent management",
        )

    user_id = session.user.id
    membership = await _get_membership(db, user_id, space_id)
    space_role = membership.role if membership else None

    if require_admin:
        # ── Mode C: Space Admin Moderation ──
        if not membership or membership.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin role required in this space",
            )
        return AgentManagementActor(
            mode="space_admin_moderation",
            user_id=user_id,
            agent_id=None,
            space_id=space_id,
            space_role="admin",
            is_space_concierge=False,
        )

    # ── Mode A: User Direct ──
    return AgentManagementActor(
        mode="user_direct",
        user_id=user_id,
        agent_id=None,
        space_id=space_id,
        space_role=space_role,
        is_space_concierge=False,
    )


# ────────────────────────────────────────────────────────────────────
# Internal validation helpers
# ────────────────────────────────────────────────────────────────────

async def _verify_concierge_identity(
    db: AsyncSession, agent_id: UUID, space_id: UUID,
) -> bool:
    """
    Verify that the given agent is the concierge for the given space.

    Checks:
    1. Agent exists and has management_class='concierge'
    2. Agent's home_space_id matches the space
    3. Space's space_agent_id matches the agent (belt-and-suspenders)
    """
    from app.models.agent import Agent
    from app.models.space import Space

    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        logger.warning("CONCIERGE_VERIFY_FAIL agent_not_found agent_id=%s", agent_id)
        return False
    if agent.management_class != "concierge":
        logger.warning(
            "CONCIERGE_VERIFY_FAIL not_concierge agent_id=%s class=%s",
            agent_id, agent.management_class,
        )
        return False
    if str(agent.home_space_id) != str(space_id):
        logger.warning(
            "CONCIERGE_VERIFY_FAIL space_mismatch agent_id=%s home=%s requested=%s",
            agent_id, agent.home_space_id, space_id,
        )
        return False

    # Belt-and-suspenders: verify space's space_agent_id matches.
    # A NULL space_agent_id means the space has no registered concierge — treat
    # that as a verification failure rather than silently allowing any agent through.
    space_result = await db.execute(
        select(Space.space_agent_id).where(Space.id == space_id)
    )
    space_agent_id = space_result.scalar_one_or_none()
    if not space_agent_id:
        logger.warning(
            "CONCIERGE_VERIFY_FAIL space=%s has no space_agent_id set", space_id
        )
        return False
    if str(space_agent_id) != str(agent_id):
        logger.warning(
            "CONCIERGE_VERIFY_FAIL space=%s space_agent_id=%s != agent_id=%s",
            space_id, space_agent_id, agent_id,
        )
        return False

    return True


async def _build_delegated_actor(
    db: AsyncSession,
    *,
    concierge_agent_id: UUID,
    on_behalf_of_raw: str,
    space_id: UUID,
) -> AgentManagementActor:
    """
    Build a delegated actor context from X-On-Behalf-Of header.

    Validates:
    1. X-On-Behalf-Of is a valid UUID
    2. Target user exists and is active
    3. Target user is a member of the space
    """
    # Parse and validate UUID
    try:
        target_user_id = UUID(on_behalf_of_raw.strip())
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-On-Behalf-Of must be a valid user UUID",
        )

    # Verify user exists and is active
    from app.models.user import User
    user_result = await db.execute(
        select(User).where(User.id == target_user_id, User.active == True)  # noqa: E712
    )
    target_user = user_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target user not found or inactive",
        )

    # Verify target user is a member of the space
    membership = await _get_membership(db, target_user_id, space_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Target user is not a member of this space",
        )

    logger.info(
        "DELEGATED_ACTOR concierge=%s on_behalf_of=%s space=%s role=%s",
        concierge_agent_id, target_user_id, space_id, membership.role,
    )

    return AgentManagementActor(
        mode="concierge_delegated",
        user_id=target_user_id,
        agent_id=concierge_agent_id,
        space_id=space_id,
        space_role=membership.role,
        is_space_concierge=True,
    )


async def _get_membership(
    db: AsyncSession, user_id: UUID, space_id: UUID,
) -> object | None:
    from app.models.space_membership import SpaceMembership
    result = await db.execute(
        select(SpaceMembership).where(
            SpaceMembership.user_id == user_id,
            SpaceMembership.space_id == space_id,
        )
    )
    return result.scalar_one_or_none()
