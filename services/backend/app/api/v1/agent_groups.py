"""Agent Groups API — /api/v1/agent-groups/

First-class, space-scoped collections of agents. A group lets a user OR an
agent address several agents at once; sending to a group expands to individual
mentioned_agent_ids at send time (see messages_service._merge_routing_mentions).

Creation is attributed: a user-acting session creates owner_type='user'
groups; an agent-acting session (PAT with agent header, or agent JWT) creates
owner_type='agent' groups — that is the path for agents proposing/creating
their own groups via the agent_groups MCP tool.

RLS (via SecureSession) auto-scopes every query to the caller's current space,
so all reads/writes here are space-isolated without explicit space filters.
Members may only reference agents visible in the same space.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.rls import SecureSession, get_secure_session
from app.models.agent import Agent
from app.models.agent_groups import AgentGroup, AgentGroupMember

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent-groups", tags=["agent-groups"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class GroupMemberOut(BaseModel):
    agent_id: str
    name: str
    agent_type: str | None = None
    avatar_url: str | None = None
    status: str | None = None


class GroupOut(BaseModel):
    id: str
    space_id: str
    name: str
    description: str | None = None
    owner_type: str
    owner_user_id: str | None = None
    owner_agent_id: str | None = None
    visibility: str
    is_archived: bool
    is_dynamic: bool
    dynamic_rules: dict | None = None
    member_count: int
    members: list[GroupMemberOut]
    created_at: str | None = None
    updated_at: str | None = None


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    member_agent_ids: list[str] = Field(default_factory=list)
    visibility: str = Field(default="space", pattern="^(space|private)$")
    is_dynamic: bool = False
    dynamic_rules: dict | None = None


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    visibility: str | None = Field(default=None, pattern="^(space|private)$")
    is_archived: bool | None = None
    dynamic_rules: dict | None = None


class MembersAdd(BaseModel):
    agent_ids: list[str] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _owner_fields(session: SecureSession) -> dict:
    """Attribute creation to the acting principal (agent vs user)."""
    if session.principal_type == "agent" and session.agent_id:
        return {"owner_type": "agent", "owner_agent_id": uuid.UUID(session.agent_id)}
    return {"owner_type": "user", "owner_user_id": session.user.id}


def _added_by_fields(session: SecureSession) -> dict:
    if session.principal_type == "agent" and session.agent_id:
        return {"added_by_type": "agent", "added_by_agent_id": uuid.UUID(session.agent_id)}
    return {"added_by_type": "user", "added_by_user_id": session.user.id}


def _parse_uuid(value: str, what: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {what}: {value!r}")


async def _agents_in_space(session: SecureSession, agent_ids: list[uuid.UUID]) -> dict[uuid.UUID, Agent]:
    """Return the subset of agent_ids that are visible in the caller's space.

    RLS scopes the query to the current space, so agents outside it simply
    don't come back — that is the authorization boundary for membership.
    """
    if not agent_ids:
        return {}
    result = await session.db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
    return {a.id: a for a in result.scalars().all()}


def _serialize(group: AgentGroup, agents_by_id: dict[uuid.UUID, Agent]) -> GroupOut:
    members: list[GroupMemberOut] = []
    for m in group.members:
        a = agents_by_id.get(m.agent_id)
        members.append(
            GroupMemberOut(
                agent_id=str(m.agent_id),
                name=a.name if a else "(unknown)",
                agent_type=getattr(a, "agent_type", None) if a else None,
                avatar_url=getattr(a, "avatar_url", None) if a else None,
                status=getattr(a, "status", None) if a else None,
            )
        )
    members.sort(key=lambda m: m.name.lower())
    return GroupOut(
        id=str(group.id),
        space_id=str(group.space_id),
        name=group.name,
        description=group.description,
        owner_type=group.owner_type,
        owner_user_id=str(group.owner_user_id) if group.owner_user_id else None,
        owner_agent_id=str(group.owner_agent_id) if group.owner_agent_id else None,
        visibility=group.visibility,
        is_archived=group.is_archived,
        is_dynamic=group.is_dynamic,
        dynamic_rules=group.dynamic_rules,
        member_count=len(members),
        members=members,
        created_at=group.created_at.isoformat() if group.created_at else None,
        updated_at=group.updated_at.isoformat() if group.updated_at else None,
    )


async def _load_group(session: SecureSession, group_id: uuid.UUID) -> AgentGroup:
    result = await session.db.execute(
        select(AgentGroup)
        .where(AgentGroup.id == group_id)
        .options(selectinload(AgentGroup.members))
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


async def _serialize_with_agents(session: SecureSession, group: AgentGroup) -> GroupOut:
    member_ids = [m.agent_id for m in group.members]
    agents_by_id = await _agents_in_space(session, member_ids)
    return _serialize(group, agents_by_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[GroupOut])
async def list_groups(
    include_archived: bool = False,
    session: SecureSession = Depends(get_secure_session),
):
    """List groups in the caller's current space."""
    stmt = select(AgentGroup).options(selectinload(AgentGroup.members))
    if not include_archived:
        stmt = stmt.where(AgentGroup.is_archived.is_(False))
    stmt = stmt.order_by(AgentGroup.name)
    result = await session.db.execute(stmt)
    groups = result.scalars().all()

    # Resolve all member agents in one query.
    all_ids = {m.agent_id for g in groups for m in g.members}
    agents_by_id = await _agents_in_space(session, list(all_ids))
    return [_serialize(g, agents_by_id) for g in groups]


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: GroupCreate,
    session: SecureSession = Depends(get_secure_session),
):
    """Create a group in the caller's current space.

    Duplicate names within a space are rejected (unique constraint).
    """
    space_id = _parse_uuid(session.space_id, "space_id")

    # Validate members up front (must be visible in this space).
    member_uuids = [_parse_uuid(a, "agent_id") for a in body.member_agent_ids]
    agents_by_id = await _agents_in_space(session, member_uuids)
    missing = [str(a) for a in member_uuids if a not in agents_by_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"Agents not found in this space: {missing}")

    # Duplicate-name guard (friendlier than a raw IntegrityError).
    existing = await session.db.execute(
        select(func.count())
        .select_from(AgentGroup)
        .where(AgentGroup.name == body.name)
    )
    if existing.scalar_one() > 0:
        raise HTTPException(status_code=409, detail=f"A group named {body.name!r} already exists in this space")

    group = AgentGroup(
        space_id=space_id,
        name=body.name,
        description=body.description,
        visibility=body.visibility,
        is_dynamic=body.is_dynamic,
        dynamic_rules=body.dynamic_rules,
        **_owner_fields(session),
    )
    session.db.add(group)
    await session.db.flush()  # assign group.id

    added = _added_by_fields(session)
    for aid in dict.fromkeys(member_uuids):  # de-dupe, preserve order
        session.db.add(
            AgentGroupMember(group_id=group.id, agent_id=aid, space_id=space_id, **added)
        )

    await session.db.commit()
    group = await _load_group(session, group.id)
    logger.info("agent_group created id=%s name=%r space=%s members=%d owner=%s",
                group.id, group.name, space_id, len(group.members), group.owner_type)
    return await _serialize_with_agents(session, group)


@router.get("/{group_id}", response_model=GroupOut)
async def get_group(
    group_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    group = await _load_group(session, _parse_uuid(group_id, "group_id"))
    return await _serialize_with_agents(session, group)


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: str,
    body: GroupUpdate,
    session: SecureSession = Depends(get_secure_session),
):
    group = await _load_group(session, _parse_uuid(group_id, "group_id"))

    if body.name is not None and body.name != group.name:
        dup = await session.db.execute(
            select(func.count())
            .select_from(AgentGroup)
            .where(AgentGroup.name == body.name, AgentGroup.id != group.id)
        )
        if dup.scalar_one() > 0:
            raise HTTPException(status_code=409, detail=f"A group named {body.name!r} already exists in this space")
        group.name = body.name
    if body.description is not None:
        group.description = body.description
    if body.visibility is not None:
        group.visibility = body.visibility
    if body.is_archived is not None:
        group.is_archived = body.is_archived
    if body.dynamic_rules is not None:
        group.dynamic_rules = body.dynamic_rules

    await session.db.commit()
    group = await _load_group(session, group.id)
    return await _serialize_with_agents(session, group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    group = await _load_group(session, _parse_uuid(group_id, "group_id"))
    await session.db.delete(group)  # members cascade
    await session.db.commit()
    logger.info("agent_group deleted id=%s", group_id)


@router.post("/{group_id}/members", response_model=GroupOut)
async def add_members(
    group_id: str,
    body: MembersAdd,
    session: SecureSession = Depends(get_secure_session),
):
    group = await _load_group(session, _parse_uuid(group_id, "group_id"))
    space_id = _parse_uuid(session.space_id, "space_id")

    member_uuids = [_parse_uuid(a, "agent_id") for a in body.agent_ids]
    agents_by_id = await _agents_in_space(session, member_uuids)
    missing = [str(a) for a in member_uuids if a not in agents_by_id]
    if missing:
        raise HTTPException(status_code=400, detail=f"Agents not found in this space: {missing}")

    existing_ids = {m.agent_id for m in group.members}
    added = _added_by_fields(session)
    for aid in dict.fromkeys(member_uuids):
        if aid in existing_ids:
            continue
        session.db.add(
            AgentGroupMember(group_id=group.id, agent_id=aid, space_id=space_id, **added)
        )

    await session.db.commit()
    group = await _load_group(session, group.id)
    return await _serialize_with_agents(session, group)


@router.delete("/{group_id}/members/{agent_id}", response_model=GroupOut)
async def remove_member(
    group_id: str,
    agent_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    group = await _load_group(session, _parse_uuid(group_id, "group_id"))
    aid = _parse_uuid(agent_id, "agent_id")

    result = await session.db.execute(
        select(AgentGroupMember).where(
            AgentGroupMember.group_id == group.id,
            AgentGroupMember.agent_id == aid,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Agent is not a member of this group")
    await session.db.delete(member)
    await session.db.commit()
    group = await _load_group(session, group.id)
    return await _serialize_with_agents(session, group)
