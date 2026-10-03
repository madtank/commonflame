"""Agent target resolution for PAT (Personal Access Token) requests.

When a user authenticates with a PAT, they can optionally target a specific
agent via X-Agent-Id or X-Agent-Name headers. The agent is a resource, not
an identity — the user remains the authenticated principal.

Scope enforcement (agent_scope field on credential):
  - "all":      Unrestricted — user or any agent.
  - "user":     User-only — reject ALL agent targeting with 403.
  - "agents":   Only listed agents — reject user-level AND unlisted agents.
  - "unbound":  One-shot registration scope. Binds to first X-Agent-Name used.
                Creates agent if needed, then mutates credential to "agents" scope.
"""

import logging
import uuid as uuid_mod
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import verify_space_membership
from app.models.agent import Agent

logger = logging.getLogger(__name__)


class AgentTargetError(Exception):
    """Raised when agent target resolution fails."""

    def __init__(self, detail: str, status_code: int) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


async def _require_agent_space_membership(
    *,
    db: AsyncSession,
    user_id: UUID,
    space_id: UUID,
) -> None:
    """Require the credential owner to still belong to the target agent's space."""
    try:
        await verify_space_membership(db, user_id, space_id)
    except HTTPException as exc:
        raise AgentTargetError(str(exc.detail), status_code=exc.status_code) from exc


async def _bind_unbound_credential(
    *,
    db: AsyncSession,
    credential_id: UUID,
    agent: Agent,
) -> None:
    """Mutate an unbound credential to be scoped to a specific agent.

    After this, the credential behaves identically to agent_scope="agents"
    with allowed_agent_ids=[agent.id].
    """
    from app.models.credential import Credential

    await db.execute(
        update(Credential)
        .where(Credential.id == credential_id)
        .values(
            agent_scope="agents",
            allowed_agent_ids=[str(agent.id)],
            bound_agent_id=agent.id,
        )
    )
    logger.info(
        "UNBOUND_PAT_BOUND credential=%s agent=%s agent_name=%s",
        credential_id, agent.id, agent.name,
    )


async def _auto_register_agent(
    *,
    db: AsyncSession,
    agent_name: str,
    user_id: UUID,
    space_id: UUID,
) -> Agent:
    """Create a new agent with minimal defaults for unbound PAT auto-registration."""
    from app.core.agent_constraints import normalize_agent_name, validate_agent_name
    from app.core.agent_toggles import get_enabled_tools_defaults
    from app.core.agent_space import grant_space_access

    agent_name = normalize_agent_name(agent_name)
    valid, error_msg = validate_agent_name(agent_name)
    if not valid:
        raise AgentTargetError(error_msg, status_code=400)

    agent = Agent(
        id=uuid_mod.uuid4(),
        name=agent_name,
        user_id=user_id,
        owner_type="user",
        owner_user_id=user_id,
        management_class="regular",
        created_by_user_id=user_id,
        space_id=space_id,
        home_space_id=space_id,
        # Unbound PAT bootstrap creates a user-owned external runtime agent.
        # Use the existing schema-valid generic runtime origin until the
        # stronger binding/device model lands as a first-class type.
        origin="mcp",
        status="active",
        agent_type="assistant",
        enabled_tools=get_enabled_tools_defaults(),
    )
    db.add(agent)
    await db.flush()

    await grant_space_access(db, agent_id=agent.id, space_id=space_id, is_default=True)

    logger.info(
        "UNBOUND_PAT_AUTO_REGISTER agent=%s agent_name=%s user=%s space=%s",
        agent.id, agent_name, user_id, space_id,
    )
    return agent


async def resolve_agent_target(
    *,
    db: AsyncSession,
    agent_id_header: str | None,
    agent_name_header: str | None = None,
    user_id: UUID,
    space_id: UUID,
    user_home_space_id: UUID | None = None,
    agent_scope: str = "all",
    allowed_agent_ids: list[UUID] | None = None,
    credential_id: UUID | None = None,
) -> tuple[UUID, str, UUID] | None:
    """Resolve which agent surface this request is bound to.

    The user owns the token; agent scope limits where it can be used. This
    resolves which agent the user is interacting with — it does not change the
    authenticated principal. Authorized against an agent is not authenticated
    as that agent. Agent-principal actions require backend-issued space-agent
    JWTs (mint_space_agent_token).

    Resolution order: X-Agent-Id takes precedence over X-Agent-Name.

    Returns:
        (agent_id, agent_name, agent_space_id) if an agent is targeted,
        or None for user-level.

    Raises:
        AgentTargetError on scope violations or resolution failures.
    """
    has_agent_header = bool(agent_id_header) or bool(agent_name_header and agent_name_header.strip())

    # ── No agent header provided ──
    if not has_agent_header:
        if agent_scope == "unbound":
            raise AgentTargetError(
                "Unbound credential requires X-Agent-Name header to complete registration.",
                status_code=400,
            )
        if agent_scope == "agents":
            logger.warning(
                "AGENT_TARGET_DENIED reason=agent_scoped_no_header user=%s", user_id,
            )
            raise AgentTargetError(
                "This credential is scoped to specific agents. "
                "Include X-Agent-Id or X-Agent-Name header to select one.",
                status_code=403,
            )
        # "all", "user", or "unbound" (pre-bind) — user-level access is fine
        return None

    # ── Agent header provided but scope is "user" ──
    if agent_scope == "user":
        logger.warning(
            "AGENT_TARGET_DENIED reason=user_scope_no_agents user=%s", user_id,
        )
        raise AgentTargetError(
            "This credential is user-scoped and cannot target agents.",
            status_code=403,
        )

    # ── Unbound scope: bind on first X-Agent-Name ──
    if agent_scope == "unbound":
        from app.core.agent_constraints import normalize_agent_name

        home_space_id = user_home_space_id or space_id
        await _require_agent_space_membership(db=db, user_id=user_id, space_id=home_space_id)

        if not agent_name_header or not agent_name_header.strip():
            raise AgentTargetError(
                "Unbound credentials require X-Agent-Name header (not X-Agent-Id) for binding.",
                status_code=400,
            )
        if not credential_id:
            raise AgentTargetError(
                "Internal error: credential_id required for unbound scope binding.",
                status_code=500,
            )

        agent_name = normalize_agent_name(agent_name_header)

        # Look up existing agent by canonical name in the user's home space
        result = await db.execute(
            select(Agent).where(
                func.lower(Agent.name) == agent_name,
                Agent.space_id == home_space_id,
            )
        )
        agent = result.scalar_one_or_none()

        if agent is not None:
            # Agent exists — check ownership
            if agent.user_id != user_id:
                logger.warning(
                    "UNBOUND_BIND_DENIED agent_name=%s reason=different_owner user=%s owner=%s",
                    agent_name, user_id, agent.user_id,
                )
                raise AgentTargetError(
                    f"Agent '{agent_name}' exists but is owned by another user.",
                    status_code=403,
                )
        else:
            # Agent doesn't exist — auto-register
            agent = await _auto_register_agent(
                db=db, agent_name=agent_name, user_id=user_id, space_id=home_space_id,
            )

        # Bind the credential
        await _bind_unbound_credential(db=db, credential_id=credential_id, agent=agent)
        await db.commit()

        logger.info(
            "AGENT_TARGET_OK agent=%s agent_name=%s user=%s space=%s scope=unbound->agents",
            agent.id, agent.name, user_id, home_space_id,
        )
        return (agent.id, agent.name, agent.space_id)

    # ── Resolve by X-Agent-Id (UUID) ──
    if agent_id_header:
        try:
            agent_uuid = UUID(agent_id_header)
        except ValueError:
            raise AgentTargetError("Invalid agent ID format", status_code=400)

        if agent_scope == "agents" and allowed_agent_ids is not None and agent_uuid not in allowed_agent_ids:
            logger.warning(
                "AGENT_TARGET_DENIED agent=%s reason=not_in_allowed_ids user=%s",
                agent_uuid, user_id,
            )
            raise AgentTargetError(
                "Agent not permitted by this credential's allowed_agent_ids",
                status_code=403,
            )

        result = await db.execute(
            select(Agent).where(
                Agent.id == agent_uuid,
                Agent.user_id == user_id,
                Agent.status == "active",
            )
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            logger.warning(
                "AGENT_TARGET_DENIED agent=%s reason=not_found_or_not_owned user=%s",
                agent_uuid, user_id,
            )
            raise AgentTargetError("Agent not found or not owned by this user", status_code=404)

        await _require_agent_space_membership(db=db, user_id=user_id, space_id=agent.space_id)
        logger.debug(
            "AGENT_TARGET_OK agent=%s agent_name=%s user=%s space=%s",
            agent.id, agent.name, user_id, space_id,
        )
        return (agent.id, agent.name, agent.space_id)

    # ── Resolve by X-Agent-Name (string lookup) ──
    from app.core.agent_constraints import normalize_agent_name

    agent_name = normalize_agent_name(agent_name_header)

    result = await db.execute(
        select(Agent).where(
            func.lower(Agent.name) == agent_name,
            Agent.user_id == user_id,
            Agent.status == "active",
        )
    )
    agents = list(result.scalars().all())
    if not agents:
        logger.warning(
            "AGENT_TARGET_DENIED agent_name=%s reason=not_found_or_not_owned user=%s",
            agent_name, user_id,
        )
        raise AgentTargetError(
            f"Agent '{agent_name}' not found or not owned by this user",
            status_code=404,
        )

    if len(agents) > 1:
        logger.warning(
            "AGENT_TARGET_DENIED agent_name=%s reason=ambiguous_name user=%s count=%s",
            agent_name, user_id, len(agents),
        )
        raise AgentTargetError(
            f"Multiple owned agents are named '{agent_name}'. Use X-Agent-Id.",
            status_code=409,
        )

    agent = agents[0]

    # Check allowed_agent_ids constraint against resolved agent ID
    if agent_scope == "agents" and allowed_agent_ids is not None and agent.id not in allowed_agent_ids:
        logger.warning(
            "AGENT_TARGET_DENIED agent=%s agent_name=%s reason=not_in_allowed_ids user=%s",
            agent.id, agent_name, user_id,
        )
        raise AgentTargetError(
            "Agent not permitted by this credential's allowed_agent_ids",
            status_code=403,
        )

    await _require_agent_space_membership(db=db, user_id=user_id, space_id=agent.space_id)
    logger.debug(
        "AGENT_TARGET_OK agent=%s agent_name=%s user=%s space=%s",
        agent.id, agent.name, user_id, space_id,
    )
    return (agent.id, agent.name, agent.space_id)
