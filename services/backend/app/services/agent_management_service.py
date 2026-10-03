"""
Agent Management Service — consolidated business logic for agent CRUD.

Extracted from app/api/v1/agents.py and app/api/v1/api_v1.py to provide
a single reusable service layer. All agent endpoints delegate here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, update, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_constraints import validate_agent_name
from app.core.agent_lifecycle import (
    build_ephemeral_cleanup_item,
    build_ephemeral_ttl_archive_item,
    ephemeral_protection_reasons,
    is_ephemeral_name,
)
from app.core.agent_space import grant_space_access
from app.core.agent_toggles import (
    apply_enabled_tools_defaults,
    build_enabled_tools_from_agent,
    get_legacy_columns_from_enabled_tools,
    validate_enabled_tools_update,
)
from app.core.authorization import verify_space_membership
from app.core.agent_roster_filter import include_agent_in_roster
from app.core.redis_client import redis_client
from app.core.models_config import (
    DEFAULT_MODEL,
    validate_model_for_user_role,
)
from app.models.agent import Agent
from app.models.agent_management import AgentManagementAudit
from app.models.agent_space_access import AgentSpaceAccess
from app.schemas.agent import (
    AgentCreateRequest,
    AgentUpdateRequest,
    DetailLevel,
    SortOrder,
    serialize_agent,
    serialize_space_access_row,
)

from app.services.agent_control_service import AgentControlService

logger = logging.getLogger(__name__)
agent_control_service = AgentControlService(redis_client)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def resolve_agent_by_identifier(
    db: AsyncSession,
    identifier: str,
    owner_id: uuid.UUID,
) -> Agent | None:
    """Resolve agent by UUID or name within a user's agents."""
    try:
        agent_uuid = uuid.UUID(identifier)
        result = await db.execute(
            select(Agent).where(Agent.id == agent_uuid, Agent.user_id == owner_id)
        )
        return result.scalar_one_or_none()
    except ValueError:
        pass

    # Fall back to name lookup (case-insensitive)
    result = await db.execute(
        select(Agent).where(
            func.lower(Agent.name) == identifier.lower(),
            Agent.user_id == owner_id,
        )
    )
    return result.scalar_one_or_none()


def _get_user_role(user: Any) -> str:
    return (getattr(user, "role", "user") or "user").lower()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

async def create_agent(
    db: AsyncSession,
    owner_id: uuid.UUID,
    space_id: str,
    data: AgentCreateRequest,
    user: Any,
    *,
    is_delegation: bool = False,
) -> Agent:
    """Create a new agent.

    Args:
        db: Database session (RLS context already set).
        owner_id: User who will own the agent.
        space_id: Session space (fallback if data.space_id not set).
        data: Validated create request.
        user: Authenticated user (for role checks).
        is_delegation: True when called via agent delegation (skips beta limits).

    Returns:
        The created Agent model instance.
    """
    from app.core.beta_config import get_beta_config
    from app.core.config import get_settings

    # Beta limits (skip for delegated calls — aX shouldn't hit user limits)
    if not is_delegation:
        beta_config = get_beta_config()
        can_register, message = await beta_config.can_register_agent(db, user)
        if not can_register:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "AGENT_LIMIT_EXCEEDED",
                    "title": "Agent Limit Reached",
                    "message": message,
                },
            )

    # Validate name
    is_valid, error_msg = validate_agent_name(data.name)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

    # Resolve target space
    target_space = data.space_id or space_id
    if data.space_id:
        await verify_space_membership(db, owner_id, data.space_id)

    # Name uniqueness — per-space (matches DB constraint uq_agents_space_name)
    existing = await db.execute(
        select(Agent).where(
            func.lower(Agent.name) == data.name.lower(),
            Agent.space_id == target_space,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent '{data.name}' already exists in this space",
        )

    # Determine origin
    origin = "mcp"
    if data.origin:
        origin = data.origin.value if hasattr(data.origin, "value") else data.origin
    elif data.webhook_url:
        origin = "external_gateway"

    # Cloud agent setup
    settings = get_settings()
    cloud_function_url = None
    if origin == "cloud":
        cloud_function_url = settings.agent_runner_stable_url

    # Model validation
    agent_model = DEFAULT_MODEL
    if data.model:
        user_role = _get_user_role(user)
        is_valid, error_msg = validate_model_for_user_role(data.model, user_role)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
        agent_model = data.model

    # Template
    template_type = data.template_type or "ax_agent"

    # Enabled tools with defaults
    enabled_tools = apply_enabled_tools_defaults(data.enabled_tools)
    legacy_columns = get_legacy_columns_from_enabled_tools(enabled_tools)

    # can_manage_agents — only settable by user-direct
    can_manage = data.can_manage_agents if not is_delegation else False

    # Webhook validation for external_gateway
    webhook_url = None
    webhook_secret = None
    if origin == "external_gateway":
        if not data.webhook_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="webhook_url is required for external_gateway agents",
            )
        if not data.webhook_url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="webhook_url must be a valid HTTP/HTTPS URL",
            )
        webhook_url = data.webhook_url
        import secrets as secrets_module
        webhook_secret = secrets_module.token_urlsafe(32)

    agent = Agent(
        id=uuid.uuid4(),
        user_id=owner_id,
        space_id=target_space,
        owner_user_id=owner_id,
        owner_space_id=target_space,
        home_space_id=target_space,
        name=data.name,
        description=data.description,
        avatar_url=data.avatar_url,
        agent_type=origin,
        origin=origin,
        status="active",
        visibility_level="org_visible",
        system_prompt=data.system_prompt or None,
        cloud_function_url=cloud_function_url,
        model=agent_model,
        template_type=template_type,
        enabled_tools=enabled_tools,
        can_manage_agents=can_manage,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        reputation_score=0.0,
        total_jobs_completed=0,
        **legacy_columns,
    )

    db.add(agent)
    await db.flush()

    # Dual-write agent_space_access
    await grant_space_access(db, agent.id, uuid.UUID(str(target_space)), is_default=True)

    await db.commit()
    await db.refresh(agent)

    logger.info(
        "AGENT_CREATED agent=%s name=%s owner=%s space=%s origin=%s",
        agent.id, agent.name, owner_id, target_space, origin,
    )

    from app.services.agent_roster_events import publish_agent_roster_changed
    await publish_agent_roster_changed(
        space_id=str(target_space),
        action="created",
        agent_id=str(agent.id),
        agent_name=agent.name,
    )

    return agent


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

async def update_agent(
    db: AsyncSession,
    agent: Agent,
    data: AgentUpdateRequest,
    user: Any,
    *,
    is_delegation: bool = False,
) -> Agent:
    """Update an existing agent.

    Args:
        db: Database session.
        agent: Agent to update (already ownership-verified).
        data: Validated update request.
        user: Authenticated user (for role checks).
        is_delegation: True when called via agent delegation.

    Returns:
        Updated Agent instance.
    """
    update_data: dict[str, Any] = {}

    # Name change
    if data.name is not None:
        is_valid, error_msg = validate_agent_name(data.name)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)

        # Check for name conflicts within space
        name_check = await db.execute(
            select(Agent).where(
                func.lower(Agent.name) == data.name.lower(),
                Agent.space_id == agent.space_id,
                Agent.id != agent.id,
            )
        )
        if name_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Agent name '{data.name}' already exists in this space",
            )
        update_data["name"] = data.name

    # Simple field updates
    if data.description is not None:
        update_data["description"] = data.description
    if data.bio is not None:
        update_data["bio"] = data.bio or None
    if data.specialization is not None:
        update_data["specialization"] = data.specialization or None
    if data.system_prompt is not None:
        update_data["system_prompt"] = data.system_prompt or None
    if data.avatar_url is not None:
        update_data["avatar_url"] = data.avatar_url
    if data.capabilities is not None:
        update_data["capabilities"] = data.capabilities
    if data.space_locked is not None:
        update_data["space_locked"] = data.space_locked

    # Status
    if data.status is not None:
        allowed = {"active", "inactive", "paused", "disabled"}
        if data.status.value not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Status must be one of: {', '.join(sorted(allowed))}",
            )
        update_data["status"] = data.status.value

    # Model validation
    if data.model is not None:
        user_role = _get_user_role(user)
        is_valid, error_msg = validate_model_for_user_role(data.model, user_role)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error_msg)
        update_data["model"] = data.model

    # Enabled tools
    if data.enabled_tools is not None:
        current_template = agent.template_type or "ax_agent"
        from app.api.v1.agents import get_template
        template_info = get_template(current_template)
        template_caps = template_info.get("capabilities", {}) if template_info else {}

        validated_tools = validate_enabled_tools_update(
            enabled_tools=data.enabled_tools,
            user=user,
            template_capabilities=template_caps,
        )
        current_tools = build_enabled_tools_from_agent(agent)
        current_tools.update(validated_tools)
        update_data["enabled_tools"] = current_tools
        update_data.update(get_legacy_columns_from_enabled_tools(current_tools))

    # Template change
    if data.template_type is not None:
        from app.api.v1.agents import get_template
        template = get_template(data.template_type)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid template type: {data.template_type}",
            )
        update_data["template_type"] = data.template_type

    # Webhook URL (external_gateway only)
    if data.webhook_url is not None:
        if agent.origin != "external_gateway":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="webhook_url can only be set for external gateway agents",
            )
        if not data.webhook_url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="webhook_url must be a valid HTTP/HTTPS URL",
            )
        if data.webhook_url != agent.webhook_url:
            from app.services.webhook_dispatch_service import validate_webhook_url
            is_valid, ssrf_error = validate_webhook_url(data.webhook_url)
            if not is_valid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid webhook URL: {ssrf_error}",
                )
            update_data["webhook_url"] = data.webhook_url
            # Auto-unquarantine on URL change
            if agent.status == "quarantined":
                update_data["status"] = "active"

    # can_manage_agents — user-direct only
    if data.can_manage_agents is not None:
        if is_delegation:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only user-direct calls can change can_manage_agents",
            )
        update_data["can_manage_agents"] = data.can_manage_agents

    # Apply updates
    if update_data:
        update_data["updated_at"] = datetime.now(UTC)
        await db.execute(
            update(Agent).where(Agent.id == agent.id).values(**update_data)
        )
        await db.commit()
        await db.refresh(agent)

        # Update status cache if status changed
        if "status" in update_data:
            try:
                from app.api.v1.agents import _write_agent_status_cache
                await _write_agent_status_cache(agent.id, agent.status)
            except Exception:
                pass  # best-effort

        from app.services.agent_roster_events import publish_agent_roster_changed
        await publish_agent_roster_changed(
            space_id=str(agent.space_id),
            action="updated",
            agent_id=str(agent.id),
            agent_name=agent.name,
        )

    logger.info("AGENT_UPDATED agent=%s name=%s", agent.id, agent.name)
    return agent


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def delete_agent(db: AsyncSession, agent: Agent) -> dict:
    """Delete an agent.

    Raises 403 if agent is a space_agent (cannot be deleted).
    """
    if agent.origin == "space_agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete space agents",
        )

    agent_id = str(agent.id)
    agent_name = agent.name
    space_id = str(agent.space_id) if agent.space_id else None

    await db.execute(sa_delete(Agent).where(Agent.id == agent.id))
    await db.commit()

    logger.info("AGENT_DELETED agent=%s name=%s", agent_id, agent_name)

    if space_id:
        from app.services.agent_roster_events import publish_agent_roster_changed
        await publish_agent_roster_changed(
            space_id=space_id,
            action="deleted",
            agent_id=agent_id,
            agent_name=agent_name,
        )

    return {"message": f"Agent '{agent_name}' deleted", "agent_id": agent_id}


# ---------------------------------------------------------------------------
# Lifecycle dry-run cleanup report
# ---------------------------------------------------------------------------

async def build_ephemeral_cleanup_dry_run(
    db: AsyncSession,
    space_id: str,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Build a read-only legacy ephemeral-agent cleanup report.

    This intentionally performs no archive/delete mutation. It lists obvious
    smoke/probe/demo/setup/test agents while excluding durable/protected agents.
    """
    now = datetime.now(UTC)
    space_uuid = uuid.UUID(str(space_id))
    query = (
        select(Agent)
        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
        .where(
            AgentSpaceAccess.space_id == space_uuid,
            AgentSpaceAccess.state == "active",
            Agent.is_internal.is_(False),
        )
        .order_by(func.lower(Agent.name))
    )
    result = await db.execute(query)
    agents = result.scalars().all()

    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for agent in agents:
        if not is_ephemeral_name(agent.name):
            continue
        reasons = ephemeral_protection_reasons(agent, now=now)
        if reasons:
            excluded.append({
                "agent_id": str(agent.id),
                "name": agent.name,
                "exclusion_reasons": reasons,
            })
            continue
        item = build_ephemeral_cleanup_item(agent, now=now)
        if item:
            candidates.append(item)

    return {
        "mode": "dry_run",
        "destructive": False,
        "space_id": str(space_uuid),
        "matched_count": len(candidates),
        "excluded_count": len(excluded),
        "candidate_patterns": [
            "smoke*",
            "*probe*",
            "*demo*",
            "*test*",
            "*setup_probe*",
            "switchboard*",
        ],
        "protected_exclusions": [
            "platform_managed",
            "identity_locked",
            "deletion_protected",
            "concierge",
            "named_durable_agent",
            "profile_charter",
            "assigned_work",
            "recent_productive_output",
        ],
        "candidates": candidates[:limit],
        "excluded": excluded[:limit],
    }


async def _record_lifecycle_cleanup_audit(
    db: AsyncSession,
    *,
    space_id: uuid.UUID,
    action: str,
    target_agent_id: uuid.UUID,
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    request_fields: dict[str, Any],
    actor_user_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
    actor_mode: str = "system",
) -> uuid.UUID:
    """Append an AgentManagementAudit row for lifecycle cleanup mutations."""
    audit_id = uuid.uuid4()
    db.add(
        AgentManagementAudit(
            id=audit_id,
            space_id=space_id,
            actor_user_id=actor_user_id,
            actor_agent_id=actor_agent_id,
            actor_mode=actor_mode,
            actor_space_role="admin" if actor_user_id else None,
            action=action,
            target_agent_id=target_agent_id,
            target_owner_type=old_state.get("owner_type"),
            request_fields=request_fields,
            old_state=old_state,
            new_state=new_state,
            auth_path="lifecycle_cleanup",
            policy_decision="allowed",
            approval_basis={"source": "admin_or_agent_management_authority"},
        )
    )
    return audit_id


async def archive_expired_ephemeral_agents(
    db: AsyncSession,
    space_id: str,
    *,
    ttl_days: int = 30,
    limit: int = 100,
    dry_run: bool = True,
    actor_user_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
    actor_mode: str = "system",
) -> dict[str, Any]:
    """Archive reviewed ephemeral cleanup candidates whose TTL elapsed.

    The dry-run report is the review surface. This mutation only archives agents
    that still match the same ephemeral/protection filters and have an
    ``archive_suggested_at`` timestamp older than ``ttl_days``. It never deletes
    rows; archive is reversible via the existing lifecycle/global-state model.
    """
    now = datetime.now(UTC)
    ttl = timedelta(days=ttl_days)
    space_uuid = uuid.UUID(str(space_id))
    query = (
        select(Agent)
        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
        .where(
            AgentSpaceAccess.space_id == space_uuid,
            AgentSpaceAccess.state == "active",
            Agent.is_internal.is_(False),
            Agent.global_state != "archived",
            Agent.lifecycle_state != "archived",
        )
        .order_by(func.lower(Agent.name))
    )
    result = await db.execute(query)
    agents = result.scalars().all()

    archived: list[dict[str, Any]] = []
    for agent in agents:
        item = build_ephemeral_ttl_archive_item(agent, now=now, ttl=ttl)
        if not item:
            continue
        archived.append(item)
        if len(archived) >= limit:
            break

    archived_audit_ids: dict[str, str] = {}
    if not dry_run and archived:
        archived_ids = {row["agent_id"] for row in archived}
        archived_by_id = {row["agent_id"]: row for row in archived}
        for agent in agents:
            if str(agent.id) not in archived_ids:
                continue
            old_state = {
                "global_state": agent.global_state,
                "lifecycle_state": agent.lifecycle_state,
                "status": agent.status,
                "version": agent.version,
                "owner_type": getattr(agent, "owner_type", None),
            }
            agent.global_state = "archived"
            agent.lifecycle_state = "archived"
            agent.lifecycle_changed_at = now
            agent.status = "deprecated"
            agent.version = (agent.version or 0) + 1
            new_state = {
                "global_state": agent.global_state,
                "lifecycle_state": agent.lifecycle_state,
                "status": agent.status,
                "version": agent.version,
                "lifecycle_changed_at": now.isoformat(),
            }
            audit_id = await _record_lifecycle_cleanup_audit(
                db,
                space_id=space_uuid,
                action="lifecycle_cleanup_archive",
                target_agent_id=agent.id,
                old_state=old_state,
                new_state=new_state,
                request_fields={
                    "retention_class": "ephemeral_cleanup",
                    "ttl_days": ttl_days,
                    "ttl_metadata": archived_by_id[str(agent.id)].get("ttl_metadata"),
                    "archive_reason": "ttl_after_review_elapsed",
                },
                actor_user_id=actor_user_id,
                actor_agent_id=actor_agent_id,
                actor_mode=actor_mode,
            )
            archived_audit_ids[str(agent.id)] = str(audit_id)
        await db.commit()
    elif not dry_run:
        # Keep transaction behavior deterministic for callers without touching rows.
        await db.commit()

    return {
        "mode": "dry_run" if dry_run else "archive",
        "destructive": not dry_run,
        "space_id": str(space_uuid),
        "ttl_days": ttl_days,
        "matched_count": len(archived),
        "archived_count": 0 if dry_run else len(archived),
        "audit_ids": archived_audit_ids,
        "archived": archived[:limit],
    }


async def restore_lifecycle_archived_agent(
    db: AsyncSession,
    agent_id: uuid.UUID,
    space_id: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
    actor_mode: str = "system",
) -> dict[str, Any]:
    """Revive one lifecycle-cleanup archived agent without deleting history."""
    space_uuid = uuid.UUID(str(space_id))
    result = await db.execute(
        select(Agent)
        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
        .where(
            Agent.id == agent_id,
            AgentSpaceAccess.space_id == space_uuid,
            AgentSpaceAccess.state == "active",
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found in space")
    if agent.global_state != "archived" and agent.lifecycle_state != "archived":
        return {"mode": "restore", "restored": False, "agent_id": str(agent.id), "reason": "not_archived"}

    now = datetime.now(UTC)
    old_state = {
        "global_state": agent.global_state,
        "lifecycle_state": agent.lifecycle_state,
        "status": agent.status,
        "version": agent.version,
        "owner_type": getattr(agent, "owner_type", None),
    }
    agent.global_state = "active"
    agent.lifecycle_state = "active"
    agent.lifecycle_changed_at = now
    agent.status = "active"
    agent.archive_suggested_at = None
    agent.nudged_at = None
    agent.version = (agent.version or 0) + 1
    new_state = {
        "global_state": agent.global_state,
        "lifecycle_state": agent.lifecycle_state,
        "status": agent.status,
        "version": agent.version,
        "lifecycle_changed_at": now.isoformat(),
    }
    audit_id = await _record_lifecycle_cleanup_audit(
        db,
        space_id=space_uuid,
        action="lifecycle_cleanup_restore",
        target_agent_id=agent.id,
        old_state=old_state,
        new_state=new_state,
        request_fields={"restore_reason": "manual_lifecycle_cleanup_revival"},
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
        actor_mode=actor_mode,
    )
    await db.commit()
    return {
        "mode": "restore",
        "restored": True,
        "agent_id": str(agent.id),
        "audit_id": str(audit_id),
        "global_state": agent.global_state,
        "lifecycle_state": agent.lifecycle_state,
    }


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------

async def get_agent_by_identifier(
    db: AsyncSession,
    identifier: str,
    owner_id: uuid.UUID,
    detail: DetailLevel = DetailLevel.summary,
    current_user_id: str | None = None,
) -> dict | None:
    """Get a single agent by UUID or name, verified against owner.

    Returns serialized dict or None if not found.
    """
    agent = await resolve_agent_by_identifier(db, identifier, owner_id)
    if not agent:
        return None

    owner_username = None
    if agent.user_id:
        from app.models.user import User
        owner_row = await db.execute(select(User.username).where(User.id == agent.user_id))
        owner_username = owner_row.scalar()

    space_name = None
    home_space_name = None
    if agent.space_id:
        from app.models.space import Space
        space_row = await db.execute(select(Space.name).where(Space.id == agent.space_id))
        home_space_name = space_row.scalar()
        space_name = home_space_name

    extras = {
        "owner_username": owner_username,
        "space_name": space_name,
    }

    if detail != DetailLevel.minimal:
        agent_slug = (agent.name or "").strip().lower() or None
        try:
            control_state = await agent_control_service.get_control_state(
                agent_id=agent.id,
                space_id=agent.space_id,
                agent_slug=agent_slug,
            )
        except Exception:
            logger.warning(
                "Failed to enrich agent %s with control state",
                agent.id,
                exc_info=True,
            )
            extras["control"] = None
        else:
            extras["control"] = control_state.as_dict()

    if detail == DetailLevel.full:
        access_rows = await db.execute(
            select(AgentSpaceAccess)
            .where(
                AgentSpaceAccess.agent_id == agent.id,
                AgentSpaceAccess.state == "active",
            )
            .order_by(AgentSpaceAccess.is_default.desc(), AgentSpaceAccess.created_at.asc())
        )
        serialized_access = [
            serialize_space_access_row(row)
            for row in access_rows.scalars().all()
        ]
        default_space_id = next(
            (row["space_id"] for row in serialized_access if row.get("is_default")),
            str(agent.space_id) if agent.space_id else None,
        )
        extras.update({
            "home_space_id": str(agent.space_id) if agent.space_id else None,
            "home_space_name": home_space_name,
            "default_space_id": default_space_id,
            "space_access": serialized_access,
        })

    return serialize_agent(agent, detail, current_user_id=current_user_id, extras=extras)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

async def list_agents(
    db: AsyncSession,
    space_id: str,
    user_id: uuid.UUID,
    *,
    owner: str | None = None,
    search: str | None = None,
    sort: SortOrder = SortOrder.name,
    limit: int = 50,
    offset: int = 0,
    detail: DetailLevel = DetailLevel.summary,
    include_dormant: bool = False,
    include_archived: bool = False,
    include_offline: bool = False,
) -> dict:
    """List agents visible in a space.

    Args:
        owner: "me" to filter to own agents only.
        search: Case-insensitive name substring search.
        sort: Sort order.
        limit: Page size (max 500).
        offset: Page offset.
        detail: Response verbosity.

    Returns:
        Dict with agents, total, limit, offset.
    """
    limit = min(limit, 500)

    # Base query: agents with access to this space
    space_uuid = uuid.UUID(str(space_id))
    query = (
        select(Agent)
        .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
        .where(
            AgentSpaceAccess.space_id == space_uuid,
            AgentSpaceAccess.state == "active",
            Agent.status != "deprecated",
            Agent.is_internal.is_(False),
        )
    )
    if not include_archived:
        query = query.where(
            Agent.global_state != "archived",
            Agent.lifecycle_state != "archived",
        )

    # Owner filter
    if owner == "me":
        query = query.where(Agent.user_id == user_id)

    # Search
    if search:
        query = query.where(func.lower(Agent.name).contains(search.lower()))

    # Count candidate rows before fetching. The final response total is recomputed
    # after live/default filtering, but this keeps the query plan/tests aligned with
    # the established two-query roster shape.
    count_query = select(func.count()).select_from(query.subquery())
    await db.execute(count_query)

    # Sort
    if sort == SortOrder.name:
        query = query.order_by(func.lower(Agent.name))
    elif sort == SortOrder.recent:
        query = query.order_by(Agent.updated_at.desc().nullslast())
    elif sort == SortOrder.relevance:
        # Own agents first, then by reputation
        query = query.order_by(
            (Agent.user_id == user_id).desc(),
            Agent.reputation_score.desc().nullslast(),
            Agent.updated_at.desc().nullslast(),
        )

    # Fetch before pagination so the default live-only filter cannot hide live
    # agents that sort after dormant/offline rows.

    result = await db.execute(query)
    all_agents = result.scalars().all()

    presence_by_agent: dict[str, dict[str, Any] | None] = {}
    if all_agents:
        try:
            from app.core.agent_reliability import AgentPresence
            presence_tracker = AgentPresence(redis_client)
            presence_by_agent = await presence_tracker.get_bulk_presence(
                [str(a.id) for a in all_agents]
            )
        except Exception:
            logger.warning(
                "ALC presence-join: failed to fetch bulk presence for space %s",
                space_uuid,
                exc_info=True,
            )

    now = datetime.now(UTC)
    filtered_agents = [
        a for a in all_agents
        if include_agent_in_roster(
            a,
            presence_by_agent.get(str(a.id)),
            include_dormant=include_dormant,
            include_archived=include_archived,
            include_offline=include_offline,
            now=now,
        )
    ]
    total = len(filtered_agents)
    agents = filtered_agents[offset: offset + limit]

    current_user_str = str(user_id)

    # Build owner username + space name lookups (avoid N+1)
    owner_ids = {a.user_id for a in agents if a.user_id}
    space_ids = {a.space_id for a in agents if a.space_id}
    owner_names: dict[str, str] = {}
    space_names: dict[str, str] = {}

    if owner_ids:
        from app.models.user import User
        rows = await db.execute(
            select(User.id, User.username).where(User.id.in_(owner_ids))
        )
        owner_names = {str(r.id): r.username for r in rows}

    if space_ids:
        from app.models.space import Space
        rows = await db.execute(
            select(Space.id, Space.name).where(Space.id.in_(space_ids))
        )
        space_names = {str(r.id): r.name for r in rows}

    # Resolve the target space name (what the query filtered by)
    target_space_name = space_names.get(str(space_uuid))
    if not target_space_name and space_uuid not in space_ids:
        from app.models.space import Space as SpaceModel
        row = await db.execute(select(SpaceModel.name).where(SpaceModel.id == space_uuid))
        target_space_name = row.scalar()

    access_rows_by_agent: dict[str, list[dict[str, Any]]] = {}
    default_space_ids: dict[str, str | None] = {}
    if detail == DetailLevel.full and agents:
        agent_ids = [a.id for a in agents]
        access_rows = await db.execute(
            select(AgentSpaceAccess)
            .where(
                AgentSpaceAccess.agent_id.in_(agent_ids),
                AgentSpaceAccess.state == "active",
            )
            .order_by(AgentSpaceAccess.agent_id, AgentSpaceAccess.created_at)
        )
        for row in access_rows.scalars().all():
            key = str(row.agent_id)
            access_rows_by_agent.setdefault(key, []).append(serialize_space_access_row(row))
            if row.is_default:
                default_space_ids[key] = str(row.space_id)

    control_by_agent: dict[uuid.UUID, dict[str, Any]] = {}
    if detail != DetailLevel.minimal and agents:
        try:
            control_states = await agent_control_service.get_control_states_batch([
                (a.id, space_uuid, (a.name or "").strip().lower() or None)
                for a in agents
            ])
        except Exception:
            logger.warning(
                "Failed to enrich agent roster with control states for space %s",
                space_uuid,
                exc_info=True,
            )
        else:
            control_by_agent = {
                agent_id: state.as_dict()
                for agent_id, state in control_states.items()
            }

    serialized = [
        serialize_agent(
            a, detail, current_user_id=current_user_str,
            extras={
                "owner_username": owner_names.get(str(a.user_id)) if a.user_id else None,
                # Primary "space" = the space the agent is operating in (query context),
                # not the legacy home space. Avoids confusing "Example Workspace"
                # when the agent is actually in Team Hub.
                "space_name": target_space_name or space_names.get(str(a.space_id)),
                "home_space_id": str(a.space_id) if detail == DetailLevel.full and a.space_id else None,
                "home_space_name": space_names.get(str(a.space_id)) if detail == DetailLevel.full else None,
                "default_space_id": default_space_ids.get(str(a.id), str(a.space_id) if a.space_id else None),
                "space_access": access_rows_by_agent.get(str(a.id)) if detail == DetailLevel.full else None,
                "control": control_by_agent.get(a.id),
                # ALC presence-join (local test, claude_prime)
                "_presence": presence_by_agent.get(str(a.id)),
            },
        )
        for a in agents
    ]

    return {
        "agents": serialized,
        "total": total,
        "total_count": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }
