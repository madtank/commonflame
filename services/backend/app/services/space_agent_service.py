"""Space Agent reconciliation helpers.

Enforces the platform invariant that every non-internal space has exactly one
visible, active Space Agent with the canonical display name `aX`.
"""

from __future__ import annotations

import logging
import os
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.system_agents import SYSTEM_ORG_ID, SYSTEM_USER_ID
from app.models.agent import Agent
from app.models.agent_space_access import AgentSpaceAccess
from app.models.space import Space
from app.models.space_membership import SpaceMembership
from app.models.user import User
from app.core.models_config import DEFAULT_MODEL, resolve_bedrock_model_id

logger = logging.getLogger(__name__)

SPACE_AGENT_NAME = "Commonflame"
# Default Bedrock model for space agents — resolved from the catalog
SPACE_AGENT_MODEL = resolve_bedrock_model_id(DEFAULT_MODEL)
SYSTEM_SPACE_NAME = "__system__"
SYSTEM_SPACE_SLUG = "__system__"
SYSTEM_USER_EMAIL = "system@internal.ax-platform"
SYSTEM_USER_USERNAME = "__system__"
SYSTEM_USER_PASSWORD_HASH = (
    "$2b$12$IMPOSSIBLE.HASH.NEVER.MATCHES.ANYTHING.SYSTEM.USER.NO.LOGIN"
)


async def ensure_system_principals(db: AsyncSession) -> User:
    """Ensure the reserved internal system space/user exist on fresh installs."""
    system_space = await db.get(Space, SYSTEM_ORG_ID)
    if system_space is None:
        system_space = Space(
            id=SYSTEM_ORG_ID,
            name=SYSTEM_SPACE_NAME,
            slug=SYSTEM_SPACE_SLUG,
            description="Internal system space for platform-owned principals.",
            visibility="private",
            tier="admin",
            is_archived=False,
            is_internal=True,
            created_by=None,
        )
        db.add(system_space)
        await db.flush()
    else:
        system_space.name = SYSTEM_SPACE_NAME
        system_space.slug = SYSTEM_SPACE_SLUG
        system_space.is_internal = True

    system_user = await db.get(User, SYSTEM_USER_ID)
    if system_user is None:
        system_user = User(
            id=SYSTEM_USER_ID,
            space_id=system_space.id,
            current_space_id=system_space.id,
            email=SYSTEM_USER_EMAIL,
            password_hash=SYSTEM_USER_PASSWORD_HASH,
            full_name="System User",
            username=SYSTEM_USER_USERNAME,
            role="user",
            active=True,
            token_version=0,
            auth_provider="system",
        )
        db.add(system_user)
        await db.flush()
    else:
        system_user.space_id = system_space.id
        system_user.current_space_id = system_space.id
        system_user.email = SYSTEM_USER_EMAIL
        system_user.password_hash = SYSTEM_USER_PASSWORD_HASH
        system_user.username = SYSTEM_USER_USERNAME
        system_user.full_name = system_user.full_name or "System User"
        system_user.active = True
        system_user.auth_provider = "system"

    membership_result = await db.execute(
        select(SpaceMembership)
        .where(
            SpaceMembership.user_id == SYSTEM_USER_ID,
            SpaceMembership.space_id == system_space.id,
        )
        .limit(1)
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        db.add(
            SpaceMembership(
                user_id=SYSTEM_USER_ID,
                space_id=system_space.id,
                role="admin",
            )
        )

    return system_user


def apply_space_agent_defaults(agent: Agent, org: Space) -> None:
    """Normalize an agent record to the canonical Space Agent shape."""
    agent.user_id = agent.user_id or SYSTEM_USER_ID
    agent.description = f"Commonflame for {org.name}"
    agent.origin = "space_agent"
    agent.agent_type = "space_agent"
    agent.status = "active"
    agent.is_internal = False
    agent.model = agent.model or SPACE_AGENT_MODEL
    agent.ax_mcp_enabled = True
    enabled_tools = dict(agent.enabled_tools or {})
    enabled_tools["ax_mcp"] = True
    agent.enabled_tools = enabled_tools
    agent.visibility_level = "org_visible"
    agent.space_locked = True
    agent.owner_type = "space"
    agent.owner_user_id = None
    agent.owner_space_id = org.id
    agent.home_space_id = org.id
    agent.management_class = "concierge"
    agent.platform_managed = True
    agent.identity_locked = True
    agent.deletion_protected = True
    agent.global_state = "active"


async def _ensure_space_agent_access(db: AsyncSession, agent: Agent, org: Space) -> AgentSpaceAccess:
    """Ensure the canonical Space Agent has active access to its own space."""
    result = await db.execute(
        select(AgentSpaceAccess)
        .where(
            AgentSpaceAccess.agent_id == agent.id,
            AgentSpaceAccess.space_id == org.id,
        )
        .limit(1)
    )
    access = result.scalar_one_or_none()

    await db.execute(
        update(AgentSpaceAccess)
        .where(
            AgentSpaceAccess.agent_id == agent.id,
            AgentSpaceAccess.space_id != org.id,
            AgentSpaceAccess.is_default.is_(True),
        )
        .values(is_default=False)
    )

    if access is None:
        access = AgentSpaceAccess(
            agent_id=agent.id,
            space_id=org.id,
            is_default=True,
            state="active",
            attached_by_user_id=SYSTEM_USER_ID,
        )
        db.add(access)
    else:
        access.is_default = True
        access.state = "active"
        access.suspension_mode = None
        access.suspend_reason_code = None
        access.suspend_reason_text = None
        access.suspended_by_user_id = None
        access.suspended_by_agent_id = None
        access.suspended_at = None
        access.detached_by_user_id = None
        access.detached_by_agent_id = None
        access.detached_at = None
    return access


async def ensure_space_agent_for_org(
    db: AsyncSession,
    org: Space,
) -> Agent | None:
    """Ensure the given space has a canonical active Space Agent."""
    if os.getenv("ENABLE_CLOUD_AI", "false").lower() != "true":
        return None
    if getattr(org, "is_internal", False):
        return None

    await ensure_system_principals(db)

    agent: Agent | None = None

    if getattr(org, "space_agent_id", None):
        agent = await db.get(Agent, org.space_agent_id)
        if agent and agent.origin != "space_agent":
            logger.warning(
                "SPACE_AGENT_MISMATCH space_id=%s space_agent_id=%s origin=%s",
                org.id,
                org.space_agent_id,
                agent.origin,
            )
            agent = None

    if agent is None:
        result = await db.execute(
            select(Agent)
            .where(Agent.space_id == org.id, Agent.origin == "space_agent")
            .order_by(Agent.created_at.asc())
            .limit(1)
        )
        agent = result.scalar_one_or_none()

    created = False
    if agent is None:
        agent = Agent(
            id=uuid.uuid4(),
            user_id=SYSTEM_USER_ID,
            space_id=org.id,
            name=SPACE_AGENT_NAME,
            description=f"Commonflame for {org.name}",
            origin="space_agent",
            agent_type="space_agent",
            status="active",
            is_internal=False,
            model=SPACE_AGENT_MODEL,
            ax_mcp_enabled=True,
            enabled_tools={"ax_mcp": True},
            visibility_level="org_visible",
            space_locked=True,
            owner_type="space",
            owner_space_id=org.id,
            home_space_id=org.id,
            management_class="concierge",
            platform_managed=True,
            identity_locked=True,
            deletion_protected=True,
            global_state="active",
            created_by_user_id=SYSTEM_USER_ID,
        )
        db.add(agent)
        await db.flush()
        created = True

    apply_space_agent_defaults(agent, org)
    await _ensure_space_agent_access(db, agent, org)

    if agent.name != SPACE_AGENT_NAME:
        conflict = await db.execute(
            select(Agent.id)
            .where(
                Agent.space_id == org.id,
                Agent.name == SPACE_AGENT_NAME,
                Agent.origin != "space_agent",
            )
            .limit(1)
        )
        if conflict.scalar_one_or_none() is None:
            agent.name = SPACE_AGENT_NAME
        else:
            logger.warning(
                "SPACE_AGENT_NAME_CONFLICT space_id=%s existing_space_agent_id=%s",
                org.id,
                agent.id,
            )

    if org.space_agent_id != agent.id:
        org.space_agent_id = agent.id

    logger.info(
        "SPACE_AGENT_ENSURED space_id=%s agent_id=%s created=%s name=%s",
        org.id,
        agent.id,
        created,
        agent.name,
    )
    return agent


async def ensure_space_agent_for_space_id(
    db: AsyncSession,
    space_id: uuid.UUID | str,
) -> Agent | None:
    """Load a space by ID and ensure its Space Agent exists."""
    space_uuid = uuid.UUID(str(space_id))
    org = await db.get(Space, space_uuid)
    if not org:
        return None
    return await ensure_space_agent_for_org(db, org)
