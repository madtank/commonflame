"""
AX-AGENT-MGMT-001 §12: Central Agent Management Policy Engine.

All mutating requests flow through evaluate_agent_management_policy().
This is the sole authorization authority for agent management.

No prompt text, agent reasoning, or UI affordance can bypass this engine.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_space_access import AgentSpaceAccess
from app.models.space_membership import SpaceMembership
from app.core.agent_mgmt_utils import classify_fields

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ActorMode = Literal[
    "user_direct",
    "concierge_delegated",
    "space_admin_moderation",
    "concierge_safeguard",
    "platform",
]

Action = Literal[
    "create",
    "update",
    "disable_global",
    "archive",
    "suspend_in_space",
    "resume_in_space",
    "detach_from_space",
    "attach_to_space",
    "update_space_overrides",
    "pause_concierge",
    "resume_concierge",
]


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    requires_proposal: bool = False
    required_approvals: list[str] = field(default_factory=list)

    @staticmethod
    def allow(reason: str = "allowed") -> PolicyDecision:
        return PolicyDecision(allowed=True, reason=reason)

    @staticmethod
    def deny(reason: str) -> PolicyDecision:
        return PolicyDecision(allowed=False, reason=reason)

    @staticmethod
    def needs_proposal(reason: str, approvals: list[str]) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            reason=reason,
            requires_proposal=True,
            required_approvals=approvals,
        )


# Default delegation policy for members
DEFAULT_DELEGATION_POLICY = {
    "allow_basic_create": True,
    "allow_basic_update": True,
    "allow_sensitive_update": False,
    "allow_global_disable": False,
}


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------

async def evaluate_agent_management_policy(
    db: AsyncSession,
    *,
    actor_user_id: UUID | None,
    actor_agent_id: UUID | None,
    actor_mode: ActorMode,
    actor_space_id: UUID | None,
    action: str,
    target_agent_id: UUID | None,
    target_space_id: UUID | None = None,
    source_space_id: UUID | None = None,
    destination_space_id: UUID | None = None,
    fields: dict | None = None,
    proposal_id: UUID | None = None,
) -> PolicyDecision:
    """
    Central policy evaluation for all agent management actions.

    This function is the sole authorization authority. It validates:
    - authenticated actor identity
    - concierge identity and bound space
    - user membership in the bound space
    - admin role where required
    - ownership class of target object
    - home-space rules for space-owned agents
    - attachment visibility in current space
    - delegation policy for the target user
    - field sensitivity class
    """
    space_id = actor_space_id or target_space_id

    # ---------------------------------------------------------------
    # 1. Load target agent (if operating on existing agent)
    # ---------------------------------------------------------------
    target_agent: Agent | None = None
    if target_agent_id:
        result = await db.execute(select(Agent).where(Agent.id == target_agent_id))
        target_agent = result.scalar_one_or_none()
        if not target_agent:
            return PolicyDecision.deny("Target agent not found")

    # ---------------------------------------------------------------
    # 2. Mode-specific validation
    # ---------------------------------------------------------------
    if actor_mode == "user_direct":
        return await _evaluate_user_direct(
            db, actor_user_id=actor_user_id, action=action,
            target_agent=target_agent, space_id=space_id, fields=fields,
        )

    elif actor_mode == "concierge_delegated":
        return await _evaluate_concierge_delegated(
            db, actor_user_id=actor_user_id, actor_agent_id=actor_agent_id,
            action=action, target_agent=target_agent, space_id=space_id,
            fields=fields,
        )

    elif actor_mode == "space_admin_moderation":
        return await _evaluate_space_admin_moderation(
            db, actor_user_id=actor_user_id, actor_agent_id=actor_agent_id,
            action=action, target_agent=target_agent, space_id=space_id,
            fields=fields,
        )

    elif actor_mode == "concierge_safeguard":
        return await _evaluate_concierge_safeguard(
            db, actor_agent_id=actor_agent_id, action=action,
            target_agent=target_agent, space_id=space_id,
        )

    elif actor_mode == "platform":
        return PolicyDecision.allow("Platform authority")

    return PolicyDecision.deny(f"Unknown actor mode: {actor_mode}")


# ---------------------------------------------------------------------------
# Mode A: User-Direct
# ---------------------------------------------------------------------------

async def _evaluate_user_direct(
    db: AsyncSession,
    *,
    actor_user_id: UUID | None,
    action: str,
    target_agent: Agent | None,
    space_id: UUID | None,
    fields: dict | None,
) -> PolicyDecision:
    if not actor_user_id:
        return PolicyDecision.deny("User identity required")

    if action == "create":
        # User can create in spaces they're a member of
        if space_id:
            membership = await _get_membership(db, actor_user_id, space_id)
            if not membership:
                return PolicyDecision.deny("User is not a member of this space")
        return PolicyDecision.allow("User-direct create")

    if not target_agent:
        return PolicyDecision.deny("Target agent required")

    # User-owned: must be the owner
    if target_agent.owner_type == "user":
        if target_agent.owner_user_id != actor_user_id:
            return PolicyDecision.deny("Not the owner of this agent")
    elif target_agent.owner_type == "space":
        # Space-owned: only admins (routed through space_admin_moderation mode)
        return PolicyDecision.deny("Space-owned agents require admin authority")
    elif target_agent.owner_type == "platform":
        return PolicyDecision.deny("Platform agents cannot be modified by users")

    # Concierge protection
    if target_agent.management_class == "concierge":
        if action in ("archive", "disable_global", "update"):
            return PolicyDecision.deny("Cannot modify concierge agent via user-direct")

    if action == "update":
        return PolicyDecision.allow("Owner update")
    elif action == "disable_global":
        return PolicyDecision.allow("Owner disable")
    elif action == "archive":
        if target_agent.deletion_protected:
            return PolicyDecision.deny("Agent is deletion-protected")
        return PolicyDecision.allow("Owner archive")
    elif action in ("suspend_in_space", "resume_in_space", "detach_from_space"):
        return PolicyDecision.allow(f"Owner {action}")
    elif action == "attach_to_space":
        if not space_id:
            return PolicyDecision.deny("space_id required for attach")
        return PolicyDecision.allow("Owner attach")

    return PolicyDecision.deny(f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# Mode B: Concierge Delegation
# ---------------------------------------------------------------------------

async def _evaluate_concierge_delegated(
    db: AsyncSession,
    *,
    actor_user_id: UUID | None,
    actor_agent_id: UUID | None,
    action: str,
    target_agent: Agent | None,
    space_id: UUID | None,
    fields: dict | None,
) -> PolicyDecision:
    if not actor_user_id:
        return PolicyDecision.deny("X-On-Behalf-Of user required for delegation")
    if not actor_agent_id:
        return PolicyDecision.deny("Concierge identity required")
    if not space_id:
        return PolicyDecision.deny("Space context required for delegation")

    # Verify concierge is actually a concierge
    concierge_result = await db.execute(select(Agent).where(Agent.id == actor_agent_id))
    concierge = concierge_result.scalar_one_or_none()
    if not concierge or concierge.management_class != "concierge":
        return PolicyDecision.deny("Actor is not a concierge agent")
    if str(concierge.home_space_id) != str(space_id):
        return PolicyDecision.deny("Concierge acting outside its bound space")

    # Verify target user is a member
    membership = await _get_membership(db, actor_user_id, space_id)
    if not membership:
        return PolicyDecision.deny("User is not a member of this space")

    # Check delegation policy
    delegation_policy = membership.ax_delegation_policy or DEFAULT_DELEGATION_POLICY

    # Delegation cannot delete or archive
    if action == "archive":
        return PolicyDecision.deny("Delegation cannot delete/archive agents — use disable")

    # Delegation cannot set can_manage_agents
    if fields and fields.get("can_manage_agents"):
        return PolicyDecision.deny("Delegation cannot grant management capabilities")

    if action == "create":
        if not delegation_policy.get("allow_basic_create", True):
            return PolicyDecision.deny("User has opted out of delegated agent creation")
        return PolicyDecision.needs_proposal(
            "Delegated create requires owner approval",
            ["owner"],
        )

    if not target_agent:
        return PolicyDecision.deny("Target agent required")

    # Delegated actions must target user-owned agents owned by the target user
    if target_agent.owner_type != "user":
        return PolicyDecision.deny("Delegation can only act on user-owned agents")
    if target_agent.owner_user_id != actor_user_id:
        return PolicyDecision.deny("Delegation target mismatch — agent not owned by target user")

    if action == "update":
        if not fields:
            return PolicyDecision.deny("No fields specified for update")
        cosmetic, behavior, identity = classify_fields(fields)
        if identity:
            return PolicyDecision.deny(f"Cannot modify identity fields: {identity}")
        if behavior:
            return PolicyDecision.needs_proposal(
                "Behavior-changing update requires owner approval",
                ["owner"],
            )
        # Cosmetic-only
        if not delegation_policy.get("allow_basic_update", True):
            return PolicyDecision.deny("User has opted out of delegated basic updates")
        return PolicyDecision.allow("Cosmetic delegated update")

    elif action == "disable_global":
        return PolicyDecision.needs_proposal(
            "Global disable requires owner approval",
            ["owner"],
        )

    elif action in ("suspend_in_space", "resume_in_space", "detach_from_space"):
        # Delegated user needs same authority as the user would have
        return PolicyDecision.allow(f"Delegated {action}")

    return PolicyDecision.deny(f"Unknown delegated action: {action}")


# ---------------------------------------------------------------------------
# Mode C: Space Admin Moderation
# ---------------------------------------------------------------------------

async def _evaluate_space_admin_moderation(
    db: AsyncSession,
    *,
    actor_user_id: UUID | None,
    actor_agent_id: UUID | None,
    action: str,
    target_agent: Agent | None,
    space_id: UUID | None,
    fields: dict | None,
) -> PolicyDecision:
    if not actor_user_id:
        return PolicyDecision.deny("Admin identity required")
    if not space_id:
        return PolicyDecision.deny("Space context required")

    # Verify admin role
    membership = await _get_membership(db, actor_user_id, space_id)
    if not membership or membership.role != "admin":
        return PolicyDecision.deny("Requires admin role in space")

    if action == "create":
        # Admin can create space-owned agents
        return PolicyDecision.allow("Admin create space-owned agent")

    if action in ("pause_concierge", "resume_concierge"):
        return PolicyDecision.allow(f"Admin {action}")

    if not target_agent:
        return PolicyDecision.deny("Target agent required")

    # Protect concierge from suspension/detach
    if target_agent.management_class == "concierge":
        if action in ("suspend_in_space", "detach_from_space", "archive"):
            return PolicyDecision.deny("Cannot suspend/detach/archive the space concierge")

    if target_agent.owner_type == "space":
        # Admin has full CRUD on space-owned agents
        if action in ("update", "disable_global", "archive",
                       "suspend_in_space", "resume_in_space", "detach_from_space",
                       "update_space_overrides"):
            return PolicyDecision.allow(f"Admin {action} on space-owned agent")

    elif target_agent.owner_type == "user":
        # Admin can moderate participation but NOT global definition
        if action in ("suspend_in_space", "resume_in_space", "detach_from_space",
                       "update_space_overrides"):
            return PolicyDecision.allow(f"Admin {action} on user-owned agent in space")

        if action == "update":
            return PolicyDecision.deny(
                "Cannot modify user-owned agent definition — use space overrides"
            )
        if action == "disable_global":
            return PolicyDecision.deny(
                "Cannot globally disable user-owned agents — use suspend_in_space"
            )
        if action == "archive":
            return PolicyDecision.deny("Cannot archive user-owned agents")

    elif target_agent.owner_type == "platform":
        return PolicyDecision.deny("Platform agents cannot be modified by admins")

    return PolicyDecision.deny(f"Unknown admin action: {action}")


# ---------------------------------------------------------------------------
# Mode D: Autonomous Concierge Safeguard
# ---------------------------------------------------------------------------

async def _evaluate_concierge_safeguard(
    db: AsyncSession,
    *,
    actor_agent_id: UUID | None,
    action: str,
    target_agent: Agent | None,
    space_id: UUID | None,
) -> PolicyDecision:
    if not actor_agent_id:
        return PolicyDecision.deny("Concierge identity required")

    # Verify concierge
    concierge_result = await db.execute(select(Agent).where(Agent.id == actor_agent_id))
    concierge = concierge_result.scalar_one_or_none()
    if not concierge or concierge.management_class != "concierge":
        return PolicyDecision.deny("Actor is not a concierge agent")

    # Safeguard mode only allows suspend_in_space
    if action != "suspend_in_space":
        return PolicyDecision.deny(
            f"Autonomous safeguard may only suspend — not {action}"
        )

    if not target_agent:
        return PolicyDecision.deny("Target agent required")

    # Cannot suspend itself
    if target_agent.management_class == "concierge":
        return PolicyDecision.deny("Concierge cannot suspend itself")

    # Must be in the concierge's bound space — space_id is required; a None
    # space_id would allow the safeguard to bypass the home-space check entirely.
    if not space_id:
        return PolicyDecision.deny("space_id required for safeguard action")
    if str(concierge.home_space_id) != str(space_id):
        return PolicyDecision.deny("Safeguard action outside bound space")

    return PolicyDecision.allow("Autonomous safeguard suspend")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_membership(
    db: AsyncSession, user_id: UUID, space_id: UUID
) -> SpaceMembership | None:
    result = await db.execute(
        select(SpaceMembership).where(
            SpaceMembership.user_id == user_id,
            SpaceMembership.space_id == space_id,
        )
    )
    return result.scalar_one_or_none()
