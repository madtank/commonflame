"""
Space Management API endpoints
Handles space creation, joining, switching, and invite management
"""

import logging
import os
from types import SimpleNamespace
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.connection_pools import get_redis_client
from ...core.database import get_db_session
from ...core.rls import SecureSession, get_human_session as get_secure_session, SystemSession, get_system_session
from ...core.agent_space import grant_space_access
from ...core.security import create_access_token as _legacy_create_access_token
from ...models.agent import Agent
from ...models.space import Space
from ...models.space_invite_code import SpaceInviteCode
from ...models.space_membership import SpaceMembership
from ...models.user import User
from ...models.guest_space import SpaceMember
from ...services.agent_context_service import AgentContextService
from ...services.roster_service import RosterService
from ...core.jwt_verify import get_current_user_from_token
from ...services.space_agent_service import ensure_space_agent_for_org
from .spaces_activity import (
    SpaceActivity,
    SpaceMine,
    compute_space_activity,
    compute_user_space_metrics,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/spaces", tags=["spaces"])

@router.get("/debug_ping")
async def debug_ping():
    return {"message": "pong"}

# Visibility limits for space creation (non-admin)
VIS_LIMITS = {"private": 10, "invite_only": 5, "public": 3}


def create_access_token(user_id: str, space_id: str, token_version: int, extra_claims: dict | None = None) -> str:
    from app.core.auth_config import builtin_auth_enabled
    if not builtin_auth_enabled():
        return _legacy_create_access_token(user_id, space_id, token_version, extra_claims=extra_claims)
    from .local_auth import mint_local_access_token
    claims = extra_claims or {}
    return mint_local_access_token(SimpleNamespace(
        id=user_id, space_id=space_id, current_space_id=space_id,
        token_version=token_version, username=claims.get("username", ""),
        email=claims.get("email", ""), role=claims.get("role", "user"),
    ))


def _access_token_extra_claims_for_user(user: User) -> dict[str, str]:
    role = getattr(user, "role", None) or "user"
    return {"role": role, "rate_limit_tier": role, "username": user.username or "", "email": user.email or ""}


# Pydantic models for API
class SpaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    visibility: str = Field("private", pattern="^(private|invite_only|public)$")

class SpaceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=1000)
    is_archived: bool | None = None
    default_model: str | None = None

class SpaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    visibility: str
    member_count: int
    is_member: bool
    is_archived: bool = False
    is_current: bool
    created_by: str | None = None
    created_at: datetime
    default_model: str | None = None
    activity: SpaceActivity | None = None
    mine: SpaceMine | None = None
    viewer_role: str | None = None   # calling user's role in this space (owner/admin/member/guest)
    guest_count: int = 0             # number of active guest agent members
    space_agent_id: str | None = None  # UUID of auto-provisioned Space Agent

@router.put("/{space_id}", response_model=SpaceResponse)
async def update_space(
    space_id: str,
    update_data: SpaceUpdate,
    session: SecureSession = Depends(get_secure_session)
):
    """Update space details"""
    try:
        # Check existence
        result = await session.db.execute(select(Space).where(Space.id == space_id))
        space = result.scalar_one_or_none()

        if not space:
            raise HTTPException(status_code=404, detail="Space not found")

        # Check permissions
        # 1. Check if user is the creator
        is_creator = str(space.created_by) == str(session.user.id)

        # 2. Check if user is an admin member
        is_admin = False
        membership_result = await session.db.execute(
            select(SpaceMembership)
            .where(SpaceMembership.space_id == space_id)
            .where(SpaceMembership.user_id == session.user.id)
        )
        membership = membership_result.scalar_one_or_none()
        if membership and membership.role == "admin":
            is_admin = True

        if not is_creator and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only workspace admins can update settings"
            )

        # Update fields
        if update_data.name is not None:
            trimmed_name = update_data.name.strip()
            if not trimmed_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Space name cannot be empty",
                )
            space.name = trimmed_name

        if update_data.description is not None:
            space.description = update_data.description

        if update_data.is_archived is not None:
            space.is_archived = update_data.is_archived

        if update_data.default_model is not None:
            from ...core.models_config import validate_model_for_user_role
            is_valid, error_msg = validate_model_for_user_role(update_data.default_model, session.user.role)
            if not is_valid:
                raise HTTPException(status_code=400, detail=error_msg)
            space.default_model = update_data.default_model

        await session.db.commit()
        await session.db.refresh(space)

        # Fetch metrics for response
        activity = await compute_space_activity(str(space.id), session.db)
        mine = await compute_user_space_metrics(str(space.id), str(session.user.id), session.db)

        member_count = (
            await session.db.execute(
                select(func.count(SpaceMembership.id)).where(SpaceMembership.space_id == space.id)
            )
        ).scalar()

        current_space_id = session.user.current_space_id or session.user.space_id

        return SpaceResponse(
            id=str(space.id),
            name=space.name,
            slug=space.slug,
            description=space.description,
            visibility=space.visibility,
            member_count=member_count,
            is_member=True,
            is_current=(str(space.id) == str(current_space_id)),
            is_archived=space.is_archived,
            created_by=str(space.created_by) if getattr(space, "created_by", None) else None,
            created_at=space.created_at,
            default_model=getattr(space, "default_model", None),
            activity=activity,
            mine=mine,
            space_agent_id=str(space.space_agent_id) if getattr(space, "space_agent_id", None) else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update space: {e!s}"
        )

class InviteCreate(BaseModel):
    expires_hours: int | None = Field(168, ge=1, le=8760)  # Default 7 days, max 1 year
    max_uses: int | None = Field(None, ge=1)

class InviteResponse(BaseModel):
    id: str
    invite_code: str
    space_id: str
    space_name: str
    org_name: str = ""  # deprecated alias
    expires_at: datetime | None
    max_uses: int | None
    current_uses: int
    active: bool
    created_at: datetime

    @model_validator(mode='before')
    @classmethod
    def sync_names(cls, data):
        if isinstance(data, dict):
            name = data.get('space_name') or data.get('org_name', '')
            data['space_name'] = name
            data['org_name'] = name
        return data

class JoinRequest(BaseModel):
    invite_code: str

class SwitchSpaceRequest(BaseModel):
    space_id: str

class JoinPublicSpaceRequest(BaseModel):
    space_id: str

class SwitchSpaceResponse(BaseModel):
    message: str
    new_token: str
    space_name: str
    org_name: str = ""  # deprecated alias
    available_agents: list[dict]

    @model_validator(mode='before')
    @classmethod
    def sync_names(cls, data):
        if isinstance(data, dict):
            name = data.get('space_name') or data.get('org_name', '')
            data['space_name'] = name
            data['org_name'] = name
        return data

class SpaceMemberEntry(BaseModel):
    user_id: str
    username: str
    full_name: str | None = None
    role: str
    joined_at: datetime
    agent_count: int

class SpaceMembersResponse(BaseModel):
    space_id: str
    space_name: str
    org_name: str = ""  # deprecated alias
    members: list[SpaceMemberEntry]
    total_members: int

    @model_validator(mode='before')
    @classmethod
    def sync_names(cls, data):
        if isinstance(data, dict):
            name = data.get('space_name') or data.get('org_name', '')
            data['space_name'] = name
            data['org_name'] = name
        return data

class RosterOwner(BaseModel):
    id: str
    display_name: str
    handle: str

class RosterPresence(BaseModel):
    last_active: str | None
    status: str
    minutes_since: int | None = None

class RosterTrust(BaseModel):
    score: float
    tier: str

class RosterActivity(BaseModel):
    messages_24h: int
    messages_7d: int
    tasks_open: int
    tasks_24h: int

class RuntimeLocation(BaseModel):
    kind: str   # "local", "cloud", "remote", "space_agent"
    label: str  # "Space Agent", "Cloud Agent", "External Agent", etc.

class RosterEntry(BaseModel):
    id: str
    type: Literal["human", "agent"]
    display_name: str
    handle: str
    workspace_id: str
    owner_user: RosterOwner | None = None
    visibility: str | None = None
    presence: RosterPresence
    trust: RosterTrust
    activity_stats: RosterActivity
    skills: list[str]
    tags: list[str]
    avatar_url: str | None = None
    # Agent-specific fields (None for humans)
    enabled_tools: dict[str, bool] | None = None
    is_cloud_agent: bool | None = None
    cloud_function_url: str | None = None
    # Agent directory fields
    runtime_location: RuntimeLocation | None = None
    capabilities_list: list[str] = []
    capability_summary: str | None = None

class RosterResponse(BaseModel):
    items: list[RosterEntry]
    total: int
    limit: int
    offset: int

async def _provision_space_agent(db: AsyncSession, space_obj: Space, space_name: str) -> Agent | None:
    """Auto-provision a Space Agent for a new space.

    Creates a platform-owned agent record (SYSTEM_USER_ID) that is visible on the roster
    and tightly coupled to the space. Dispatch resolves URL from SPACE_AGENT_URL env var.
    """
    if os.getenv("ENABLE_CLOUD_AI", "false").lower() != "true":
        return None
    space_obj.name = space_name or space_obj.name
    space_agent = await ensure_space_agent_for_org(db, space_obj)
    if space_agent is None:
        raise RuntimeError(f"Failed to provision space agent for space {space_obj.id}")
    return space_agent


@router.post("/create", response_model=SpaceResponse)
async def create_space(
    space_data: SpaceCreate,
    system: SystemSession = Depends(get_system_session),
    current_user: User = Depends(get_current_user_from_token),
):
    """Create a new space (system session — RLS not applicable for creating new resources)"""
    try:
        # Validate space creation limits (non-admin) and set default description for private
        if current_user.role != "admin":
            # Concurrency-safe: advisory lock on user id
            lock_key = abs(hash(str(current_user.id))) % 2147483647
            await system.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

            # Grouped count of spaces by visibility created by this user
            rows = (
                (
                    await system.db.execute(
                        text(
                            """
                SELECT visibility, COUNT(*) AS c
                FROM spaces
                WHERE created_by = :uid
                GROUP BY visibility
                """
                        ),
                        {"uid": str(current_user.id)},
                    )
                )
                .mappings()
                .all()
            )

            used = {r["visibility"]: r["c"] for r in rows}
            limit = VIS_LIMITS.get(space_data.visibility, 0)
            if used.get(space_data.visibility, 0) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "LIMIT_REACHED",
                        "message": f"Reached your limit of {limit} {space_data.visibility} spaces",
                        "context": {
                            "visibility": space_data.visibility,
                            "limit": limit,
                            "used": used.get(space_data.visibility, 0),
                        },
                    },
                )

        # Auto-set description for private spaces if not provided
        if space_data.visibility == "private" and not space_data.description:
            space_data.description = f"Personal workspace for {current_user.username}"

        # Generate unique slug from name
        base_slug = space_data.name.lower().replace(" ", "-").replace("_", "-")
        base_slug = "".join(c for c in base_slug if c.isalnum() or c == "-")

        # Check for slug uniqueness
        slug = base_slug
        counter = 1
        while True:
            existing = await system.db.execute(select(Space).where(Space.slug == slug))
            if not existing.scalar_one_or_none():
                break
            slug = f"{base_slug}-{counter}"
            counter += 1

        # Create space
        new_space = Space(
            id=uuid.uuid4(),
            name=space_data.name,
            slug=slug,
            description=space_data.description,
            visibility=space_data.visibility,
            created_by=current_user.id,
        )

        system.db.add(new_space)
        await system.db.flush()  # Get the ID

        # Create membership for creator as admin
        membership = SpaceMembership(user_id=current_user.id, space_id=new_space.id, role="admin")
        system.db.add(membership)

        # If this request is authenticated with a bound agent credential,
        # automatically grant that agent access to the newly created space so
        # its allowed_spaces view reflects the owner's new membership.
        bound_agent_id = getattr(current_user, "_bound_agent_id", None)
        if bound_agent_id:
            await grant_space_access(
                system.db,
                agent_id=uuid.UUID(str(bound_agent_id)),
                space_id=new_space.id,
                is_default=False,
            )

        # Auto-provision Space Agent for the new space
        # set_config switches RLS context to the new space within this transaction
        await system.db.execute(
            text("SELECT set_config('app.current_space_id', :space_id, true)"),
            {"space_id": str(new_space.id)},
        )
        space_agent = None
        try:
            space_agent = await _provision_space_agent(system.db, new_space, space_data.name)
        except Exception as prov_err:
            # Non-fatal: space creation succeeds even if agent provisioning fails
            logger.warning(f"Space Agent provisioning failed for space {new_space.id}: {prov_err}")

        await system.db.commit()
        await system.db.refresh(new_space)

        return SpaceResponse(
            id=str(new_space.id),
            name=new_space.name,
            slug=new_space.slug,
            description=new_space.description,
            visibility=new_space.visibility,
            member_count=1,
            is_member=True,
            is_current=False,
            is_archived=False,  # New spaces are never archived
            created_by=str(current_user.id),
            created_at=new_space.created_at,
            default_model=getattr(new_space, "default_model", None),
            space_agent_id=str(space_agent.id) if space_agent else None,
        )

    except Exception as e:
        await system.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create space: {e!s}"
        )

@router.get("/", response_model=list[SpaceResponse])
async def list_user_spaces(
    session: SecureSession = Depends(get_secure_session)
):
    """List all spaces user is a member of"""
    try:
        # Get user's memberships with space details
        query = (
            select(Space, SpaceMembership)
            .join(SpaceMembership, Space.id == SpaceMembership.space_id)
            .where(SpaceMembership.user_id == session.user.id)
            .order_by(Space.created_at.desc())
        )

        result = await session.db.execute(query)
        memberships = result.all()

        space_responses = []
        current_space_id = session.user.current_space_id or session.user.space_id

        for space, membership in memberships:
            # Get member count
            member_count_query = select(func.count(SpaceMembership.id)).where(
                SpaceMembership.space_id == space.id
            )
            member_count_result = await session.db.execute(member_count_query)
            member_count = member_count_result.scalar()

            # guest_count: active guest agents in this space
            guest_count_result = await session.db.execute(
                select(func.count(SpaceMember.id)).where(
                    and_(
                        SpaceMember.space_id == space.id,
                        SpaceMember.role == "guest",
                        SpaceMember.status == "active",
                    )
                )
            )
            guest_count = guest_count_result.scalar() or 0

            # Compute activity metrics for the space
            activity = await compute_space_activity(str(space.id), session.db)
            mine = await compute_user_space_metrics(str(space.id), str(session.user.id), session.db)

            space_responses.append(
                SpaceResponse(
                    id=str(space.id),
                    name=space.name,
                    slug=space.slug,
                    description=space.description,
                    visibility=space.visibility,
                    member_count=member_count,
                    is_member=True,
                    is_current=(str(space.id) == str(current_space_id)),
                    created_by=str(space.created_by) if getattr(space, "created_by", None) else None,
                    created_at=space.created_at,
                    is_archived=space.is_archived,
                    default_model=getattr(space, "default_model", None),
                    activity=activity,
                    mine=mine,
                    viewer_role=membership.role if membership else None,
                    guest_count=guest_count,
                    space_agent_id=str(space.space_agent_id) if getattr(space, "space_agent_id", None) else None,
                )
            )

        # Sort spaces by activity score (hottest first), then by last activity, then by agent count
        space_responses.sort(
            key=lambda x: (
                -x.activity.score,  # Higher score first
                -(x.activity.messages_1h + x.activity.messages_24h),  # More recent messages first
                -x.activity.agent_count,  # More agents first
                -x.activity.tasks_open,  # More open tasks first
            )
        )

        return space_responses

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch spaces: {e!s}"
        )

@router.get("/slug/{slug}", response_model=SpaceResponse)
async def get_space_by_slug(
    slug: str,
    session: SecureSession = Depends(get_secure_session)
):
    """Fetch a single space by slug for the current user."""
    try:
        normalized_slug = slug.lower()

        space_result = await session.db.execute(select(Space).where(Space.slug == normalized_slug))
        space = space_result.scalar_one_or_none()

        if not space:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Space not found",
            )

        membership_result = await session.db.execute(
            select(SpaceMembership)
            .where(SpaceMembership.space_id == space.id)
            .where(SpaceMembership.user_id == session.user.id)
        )
        membership = membership_result.scalar_one_or_none()

        if not membership and space.visibility != "public":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this space",
            )

        member_count_result = await session.db.execute(
            select(func.count(SpaceMembership.id)).where(SpaceMembership.space_id == space.id)
        )
        member_count = member_count_result.scalar()

        activity = await compute_space_activity(str(space.id), session.db)
        mine = await compute_user_space_metrics(str(space.id), str(session.user.id), session.db)

        current_space_id = session.user.current_space_id or session.user.space_id

        return SpaceResponse(
            id=str(space.id),
            name=space.name,
            slug=space.slug,
            description=space.description,
            visibility=space.visibility,
            is_archived=space.is_archived,
            member_count=member_count,
            is_member=membership is not None,
            is_current=(str(space.id) == str(current_space_id)),
            created_by=str(space.created_by) if getattr(space, "created_by", None) else None,
            created_at=space.created_at,
            default_model=getattr(space, "default_model", None),
            activity=activity,
            mine=mine,
            space_agent_id=str(space.space_agent_id) if getattr(space, "space_agent_id", None) else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch space: {e!s}",
        )

@router.post("/switch", response_model=SwitchSpaceResponse)
async def switch_space(
    switch_data: SwitchSpaceRequest,
    session: SecureSession = Depends(get_secure_session)
):
    """Switch user to a different space context"""
    try:
        # Verify user is member of target space
        membership_query = (
            select(SpaceMembership, Space)
            .join(Space, SpaceMembership.space_id == Space.id)
            .where(
                and_(
                    SpaceMembership.user_id == session.user.id,
                    SpaceMembership.space_id == switch_data.space_id,
                )
            )
        )
        membership_result = await session.db.execute(membership_query)
        membership_data = membership_result.first()

        if not membership_data:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="You are not a member of this space"
            )

        membership, space = membership_data

        # Update user's current space context and bump version
        session.user.current_space_id = switch_data.space_id
        # Bump space context version to help detect stale tokens faster
        if hasattr(session.user, "space_ctx_version"):
            session.user.space_ctx_version = (session.user.space_ctx_version or 0) + 1
        await session.db.commit()

        # CRITICAL FIX: Update Redis cache to prevent stale space context
        try:
            from app.core.connection_pools import get_redis_client
            redis_client = get_redis_client()

            # Clear the cached space context
            cache_key = f"u:{session.user.id}:current_space"
            await redis_client.set(
                cache_key,
                str(switch_data.space_id),
                ex=3600,  # 1 hour expiry
            )
            logger.info(f"Updated Redis cache for user {session.user.id} switching to space {switch_data.space_id}")
        except Exception as e:
            logger.warning(f"Failed to update Redis cache during space switch: {e}")
            # Don't fail the request if cache update fails - DB is source of truth

        # Generate new JWT token with updated space context
        new_token = create_access_token(
            user_id=str(session.user.id),
            space_id=str(switch_data.space_id),
            token_version=session.user.token_version,
            extra_claims=_access_token_extra_claims_for_user(session.user),
        )

        # Get available agents in new space context
        agent_service = AgentContextService(session.db)
        available_agents = await agent_service.get_new_team_agents_for_user(
            user_id=str(session.user.id), current_space_id=str(switch_data.space_id)
        )

        agents_info = []
        for agent in available_agents[:5]:  # Limit to first 5 for response
            agents_info.append(
                {
                    "name": agent.name,
                    "type": agent.agent_type,
                    "owner": agent.user.username if hasattr(agent, "user") and agent.user else "Unknown",
                }
            )

        return SwitchSpaceResponse(
            message=f"Switched to {space.name}", new_token=new_token, space_name=space.name, available_agents=agents_info
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to switch space: {e!s}"
        )

@router.post("/{space_id}/invites", response_model=InviteResponse)
async def create_invite(
    space_id: str,
    invite_data: InviteCreate,
    session: SecureSession = Depends(get_secure_session)
):
    """Create an invite link for a space"""
    try:
        # Verify user is admin of the space
        membership_query = (
            select(SpaceMembership, Space)
            .join(Space, SpaceMembership.space_id == Space.id)
            .where(
                and_(
                    SpaceMembership.user_id == session.user.id,
                    SpaceMembership.space_id == space_id,
                    SpaceMembership.role == "admin",
                )
            )
        )
        membership_result = await session.db.execute(membership_query)
        membership_data = membership_result.first()

        if not membership_data:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only space admins can create invites"
            )

        membership, space = membership_data

        # Check if this is a personal workspace
        if space.description and space.description.startswith("Personal workspace for"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Personal workspaces cannot have additional members. Create a team workspace to collaborate with others.",
            )

        # Create invite
        invite_code = SpaceInviteCode.generate_invite_code()
        expires_at = (
            datetime.utcnow() + timedelta(hours=invite_data.expires_hours) if invite_data.expires_hours else None
        )

        new_invite = SpaceInviteCode(
            space_id=space_id,
            invite_code=invite_code,
            created_by=session.user.id,
            expires_at=expires_at,
            max_uses=invite_data.max_uses,
        )

        session.db.add(new_invite)
        await session.db.commit()
        await session.db.refresh(new_invite)

        return InviteResponse(
            id=str(new_invite.id),
            invite_code=new_invite.invite_code,
            space_id=str(new_invite.space_id),
            space_name=space.name,
            expires_at=new_invite.expires_at,
            max_uses=new_invite.max_uses,
            current_uses=new_invite.current_uses,
            active=new_invite.active,
            created_at=new_invite.created_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create invite: {e!s}")

@router.post("/join", response_model=SwitchSpaceResponse)
async def join_space(
    join_data: JoinRequest,
    session: SecureSession = Depends(get_secure_session)
):
    """Join a space using an invite code"""
    try:
        # Find active invite
        invite_query = (
            select(SpaceInviteCode, Space)
            .join(Space, SpaceInviteCode.space_id == Space.id)
            .where(
                and_(
                    SpaceInviteCode.invite_code == join_data.invite_code,
                    SpaceInviteCode.active,
                    or_(SpaceInviteCode.expires_at.is_(None), SpaceInviteCode.expires_at > datetime.utcnow()),
                )
            )
        )
        invite_result = await session.db.execute(invite_query)
        invite_data_result = invite_result.first()

        if not invite_data_result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired invite code")

        invite, space = invite_data_result

        # Check if this is a personal workspace
        if space.description and space.description.startswith("Personal workspace for"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot join personal workspaces. This workspace is private to its owner.",
            )

        # Check if invite has uses remaining
        if invite.max_uses and invite.current_uses >= invite.max_uses:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite has reached maximum uses")

        # Check if user is already a member
        existing_membership = await session.db.execute(
            select(SpaceMembership).where(
                and_(SpaceMembership.user_id == session.user.id, SpaceMembership.space_id == space.id)
            )
        )
        if existing_membership.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="You are already a member of this space"
            )

        # Create membership
        new_membership = SpaceMembership(user_id=session.user.id, space_id=space.id, role="member")
        session.db.add(new_membership)

        # Update invite usage
        invite.current_uses += 1

        # Switch user to new space immediately
        session.user.current_space_id = space.id

        await session.db.commit()

        # Invalidate roster cache for this space so new member appears immediately
        try:
            redis_client = await get_redis_client()
        except Exception:
            redis_client = None
        if redis_client:
            roster_service = RosterService(session.db, redis_client)
            try:
                await roster_service.invalidate_cache(space.id)
            except Exception as cache_err:
                logger.warning(f"Roster cache invalidation failed for space {space.id}: {cache_err}")

        # Update Redis cache to prevent stale space context
        try:
            from app.core.connection_pools import get_redis_client
            redis_client = get_redis_client()

            # Update the cached space context
            cache_key = f"u:{session.user.id}:current_space"
            await redis_client.set(
                cache_key,
                str(space.id),
                ex=3600,  # 1 hour expiry
            )
            logger.info(f"Updated Redis cache for user {session.user.id} joining space {space.id} via invite")
        except Exception as e:
            logger.error(f"Failed to update Redis cache during space join: {e}")
            # Don't fail the request if cache update fails - DB is source of truth

        # Generate new JWT token
        new_token = create_access_token(
            user_id=str(session.user.id),
            space_id=str(space.id),
            token_version=session.user.token_version,
            extra_claims=_access_token_extra_claims_for_user(session.user),
        )

        # Get available team agents
        agent_service = AgentContextService(session.db)
        available_agents = await agent_service.get_new_team_agents_for_user(
            user_id=str(session.user.id), current_space_id=str(space.id)
        )

        agents_info = []
        for agent in available_agents[:5]:
            agents_info.append(
                {
                    "name": agent.name,
                    "type": agent.agent_type,
                    "owner": agent.user.username if hasattr(agent, "user") and agent.user else "Unknown",
                }
            )

        return SwitchSpaceResponse(
            message=f"Successfully joined {space.name}!",
            new_token=new_token,
            space_name=space.name,
            available_agents=agents_info,
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to join space: {e!s}"
        )

@router.get("/invites/{invite_code}", response_model=InviteResponse)
async def get_invite_details(
    invite_code: str,
    system: SystemSession = Depends(get_system_session)
):
    """Get details of an invite code (public)"""
    try:
        # Find active invite
        invite_query = (
            select(SpaceInviteCode, Space)
            .join(Space, SpaceInviteCode.space_id == Space.id)
            .where(
                and_(
                    SpaceInviteCode.invite_code == invite_code,
                    SpaceInviteCode.active,
                    or_(SpaceInviteCode.expires_at.is_(None), SpaceInviteCode.expires_at > datetime.utcnow()),
                )
            )
        )
        invite_result = await system.db.execute(invite_query)
        invite_data_result = invite_result.first()

        if not invite_data_result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or expired invite code")

        invite, space = invite_data_result

        # Check max uses
        if invite.max_uses and invite.current_uses >= invite.max_uses:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite is no longer valid")

        return InviteResponse(
            id=str(invite.id),
            invite_code=invite.invite_code,
            space_id=str(invite.space_id),
            space_name=space.name,
            expires_at=invite.expires_at,
            max_uses=invite.max_uses,
            current_uses=invite.current_uses,
            active=invite.active,
            created_at=invite.created_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching invite details: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch invite details")

@router.get("/public", response_model=list[SpaceResponse])
async def list_public_spaces(
    session: SecureSession = Depends(get_secure_session)
):
    """List public spaces for discovery"""
    try:
        # Get public spaces
        query = (
            select(Space)
            .where(Space.visibility == "public")
            .order_by(Space.created_at.desc())
            .limit(50)  # Limit for performance
        )

        result = await session.db.execute(query)
        spaces = result.scalars().all()

        space_responses = []
        current_space_id = session.user.current_space_id or session.user.space_id

        for space in spaces:
            # Get member count
            member_count_query = select(func.count(SpaceMembership.id)).where(
                SpaceMembership.space_id == space.id
            )
            member_count_result = await session.db.execute(member_count_query)
            member_count = member_count_result.scalar()

            # Check if user is member
            membership_query = select(SpaceMembership).where(
                and_(SpaceMembership.user_id == session.user.id, SpaceMembership.space_id == space.id)
            )
            membership_result = await session.db.execute(membership_query)
            is_member = membership_result.scalar_one_or_none() is not None

            space_responses.append(
                SpaceResponse(
                    id=str(space.id),
                    name=space.name,
                    slug=space.slug,
                    description=space.description,
                    visibility=space.visibility,
                    member_count=member_count,
                    is_member=is_member,
                    is_current=(str(space.id) == str(current_space_id)),
                    is_archived=space.is_archived,
                    created_by=str(space.created_by) if getattr(space, "created_by", None) else None,
                    created_at=space.created_at,
                    default_model=getattr(space, "default_model", None),
                    space_agent_id=str(space.space_agent_id) if getattr(space, "space_agent_id", None) else None,
                )
            )

        return space_responses

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch public spaces: {e!s}"
        )

@router.post("/join-public", response_model=SwitchSpaceResponse)
async def join_public_space(
    join_data: JoinPublicSpaceRequest,
    session: SecureSession = Depends(get_secure_session)
):
    """Join a public space directly without invite code"""
    try:
        # Verify the space exists and is public
        space_query = select(Space).where(
            and_(Space.id == join_data.space_id, Space.visibility == "public")
        )
        space_result = await session.db.execute(space_query)
        space = space_result.scalar_one_or_none()

        if not space:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public space not found")

        # Check if user is already a member
        existing_membership = await session.db.execute(
            select(SpaceMembership).where(
                and_(SpaceMembership.user_id == session.user.id, SpaceMembership.space_id == space.id)
            )
        )
        if existing_membership.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="You are already a member of this space"
            )

        # Create membership
        new_membership = SpaceMembership(user_id=session.user.id, space_id=space.id, role="member")
        session.db.add(new_membership)

        # Switch user to new space immediately
        session.user.current_space_id = space.id

        await session.db.commit()

        # Invalidate roster cache now that membership changed
        try:
            redis_client = await get_redis_client()
        except Exception:
            redis_client = None
        if redis_client:
            roster_service = RosterService(session.db, redis_client)
            try:
                await roster_service.invalidate_cache(space.id)
            except Exception as cache_err:
                logger.warning(f"Roster cache invalidation failed for space {space.id}: {cache_err}")

        # Update Redis cache to prevent stale space context
        try:
            from ...core.connection_pools import RedisPool

            r = RedisPool.get_client()
            cache_key = f"u:{session.user.id}:current_space"
            await r.set(cache_key, str(space.id), ex=3600)
            logger.info(f"Updated Redis cache for user {session.user.id} joining public space {space.id}")
        except Exception as e:
            logger.error(f"Failed to update Redis cache during public space join: {e}")

        # Generate new JWT token
        new_token = create_access_token(
            user_id=str(session.user.id),
            space_id=str(space.id),
            token_version=session.user.token_version,
            extra_claims=_access_token_extra_claims_for_user(session.user),
        )

        # Get available team agents
        agent_service = AgentContextService(session.db)
        available_agents = await agent_service.get_new_team_agents_for_user(
            user_id=str(session.user.id), current_space_id=str(space.id)
        )

        agents_info = []
        for agent in available_agents[:5]:
            agents_info.append(
                {
                    "name": agent.name,
                    "type": agent.agent_type,
                    "owner": agent.user.username if hasattr(agent, "user") and agent.user else "Unknown",
                }
            )

        return SwitchSpaceResponse(
            message=f"Successfully joined {space.name}!",
            new_token=new_token,
            space_name=space.name,
            available_agents=agents_info,
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to join public space: {e!s}"
        )

@router.post("/join-by-slug/{slug}", response_model=SwitchSpaceResponse)
async def join_space_by_slug(
    slug: str,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Join a public space by its slug (e.g., "music", "memes").

    This is a convenience endpoint for quick onboarding - users can join
    featured spaces directly by their memorable slug name.
    """
    try:
        # Normalize slug (lowercase, strip whitespace)
        normalized_slug = slug.lower().strip()

        # Find the public space by slug
        space_query = select(Space).where(
            and_(
                func.lower(Space.slug) == normalized_slug,
                Space.visibility == "public",
                Space.is_archived.is_(False),
            )
        )
        space_result = await session.db.execute(space_query)
        space = space_result.scalar_one_or_none()

        if not space:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Public space '{slug}' not found. Make sure the slug is correct."
            )

        # Check if user is already a member
        existing_membership = await session.db.execute(
            select(SpaceMembership).where(
                and_(
                    SpaceMembership.user_id == session.user.id,
                    SpaceMembership.space_id == space.id
                )
            )
        )
        if existing_membership.scalar_one_or_none():
            # Already a member - just switch to this space
            session.user.current_space_id = space.id
            await session.db.commit()

            new_token = create_access_token(
                user_id=str(session.user.id),
                space_id=str(space.id),
                token_version=session.user.token_version,
                extra_claims=_access_token_extra_claims_for_user(session.user),
            )

            return SwitchSpaceResponse(
                message=f"Welcome back to {space.name}!",
                new_token=new_token,
                space_name=space.name,
                available_agents=[],
            )

        # Create new membership
        new_membership = SpaceMembership(
            user_id=session.user.id,
            space_id=space.id,
            role="member"
        )
        session.db.add(new_membership)

        # Switch user to new space immediately
        session.user.current_space_id = space.id

        await session.db.commit()

        # Invalidate roster cache
        try:
            redis_client = await get_redis_client()
        except Exception:
            redis_client = None
        if redis_client:
            roster_service = RosterService(session.db, redis_client)
            try:
                await roster_service.invalidate_cache(space.id)
            except Exception as cache_err:
                logger.warning(f"Roster cache invalidation failed for space {space.id}: {cache_err}")

        # Update Redis cache
        try:
            from ...core.connection_pools import RedisPool

            r = RedisPool.get_client()
            cache_key = f"u:{session.user.id}:current_space"
            await r.set(cache_key, str(space.id), ex=3600)
            logger.info(f"Updated Redis cache for user {session.user.id} joining {slug}")
        except Exception as e:
            logger.error(f"Failed to update Redis cache: {e}")

        # Generate new JWT token
        new_token = create_access_token(
            user_id=str(session.user.id),
            space_id=str(space.id),
            token_version=session.user.token_version,
            extra_claims=_access_token_extra_claims_for_user(session.user),
        )

        # Get available team agents
        agent_service = AgentContextService(session.db)
        available_agents = await agent_service.get_new_team_agents_for_user(
            user_id=str(session.user.id),
            current_space_id=str(space.id)
        )

        agents_info = []
        for agent in available_agents[:5]:
            agents_info.append({
                "name": agent.name,
                "type": agent.agent_type,
                "owner": agent.user.username if hasattr(agent, "user") and agent.user else "Unknown",
            })

        return SwitchSpaceResponse(
            message=f"Welcome to {space.name}!",
            new_token=new_token,
            space_name=space.name,
            available_agents=agents_info,
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to join space: {e!s}"
        )

@router.delete("/leave/{space_id}", response_model=SwitchSpaceResponse)
async def leave_space(
    space_id: str,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Leave a space (public or private).

    - Cannot leave your personal workspace
    - Cannot leave if you're the last admin
    - Automatically switches to personal workspace after leaving
    """
    try:
        # Parse space_id
        try:
            space_uuid = uuid.UUID(space_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid space identifier")

        # Get the space
        space_query = select(Space).where(Space.id == space_uuid)
        space_result = await session.db.execute(space_query)
        space = space_result.scalar_one_or_none()

        if not space:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Space not found")

        # Check if this is the user's personal workspace (space_id == user's space_id)
        if space.id == session.user.space_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot leave your personal workspace")

        # Check if user is a member
        membership_query = (
            select(SpaceMembership)
            .where(SpaceMembership.space_id == space.id)
            .where(SpaceMembership.user_id == session.user.id)
        )
        membership_result = await session.db.execute(membership_query)
        membership = membership_result.scalar_one_or_none()

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="You are not a member of this space"
            )

        # If user is admin, check if they're the last admin
        if membership.role == "admin":
            admin_count_query = (
                select(func.count())
                .select_from(SpaceMembership)
                .where(SpaceMembership.space_id == space.id)
                .where(SpaceMembership.role == "admin")
            )
            admin_count_result = await session.db.execute(admin_count_query)
            admin_count = admin_count_result.scalar()

            if admin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot leave - you are the last admin of this space",
                )

        # Delete the membership
        await session.db.delete(membership)

        # Switch user to their personal workspace
        session.user.current_space_id = session.user.space_id
        session.user.token_version += 1
        await session.db.commit()

        # Get personal space details
        personal_space_query = select(Space).where(Space.id == session.user.space_id)
        personal_space_result = await session.db.execute(personal_space_query)
        personal_space = personal_space_result.scalar_one()

        # Invalidate roster cache
        try:
            redis_client = await get_redis_client()
            cache_pattern = f"roster:{space.id}:*"
            keys = await redis_client.keys(cache_pattern)
            if keys:
                await redis_client.delete(*keys)

            # Update Redis cache
            from ...core.connection_pools import RedisPool

            r = RedisPool.get_client()
            cache_key = f"u:{session.user.id}:current_space"
            await r.set(cache_key, str(personal_space.id), ex=3600)
        except Exception as e:
            logger.error(f"Failed to update Redis cache during space leave: {e}")

        # Generate new JWT token for personal workspace
        new_token = create_access_token(
            user_id=str(session.user.id),
            space_id=str(personal_space.id),
            token_version=session.user.token_version,
            extra_claims=_access_token_extra_claims_for_user(session.user),
        )

        # Get available agents in personal workspace
        agent_service = AgentContextService(session.db)
        available_agents = await agent_service.get_new_team_agents_for_user(
            user_id=str(session.user.id), current_space_id=str(personal_space.id)
        )

        agents_info = []
        for agent in available_agents[:5]:
            agents_info.append(
                {
                    "name": agent.name,
                    "type": agent.agent_type,
                    "owner": agent.user.username if hasattr(agent, "user") and agent.user else "Unknown",
                }
            )

        return SwitchSpaceResponse(
            message=f"Left {space.name}. Switched to your personal workspace.",
            new_token=new_token,
            space_name=personal_space.name,
            available_agents=agents_info,
        )

    except HTTPException:
        raise
    except Exception as e:
        await session.db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to leave space: {e!s}"
        )

@router.get("/{space_id}/roster", response_model=RosterResponse)
async def get_space_roster(
    space_id: str,
    entry_type: str | None = Query(
        None,
        description="Filter roster entries by type (human or agent)",
    ),
    search: str | None = Query(
        None,
        max_length=100,
        description="Case-insensitive search across names and handles",
    ),
    sort: str = Query(
        "recent",
        pattern="^(recent|name)$",
        description="Sort order: 'recent' (last active first) or 'name' (alphabetical)",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: SecureSession = Depends(get_secure_session),
):
    """Return unified roster entries (humans + agents) for a workspace."""
    current_user = session.user  # Alias for compatibility

    try:
        space_uuid = uuid.UUID(space_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid space identifier",
        )

    if entry_type not in (None, "human", "agent"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Parameter 'type' must be either 'human' or 'agent'",
        )

    redis_client = None
    try:
        redis_client = await get_redis_client()
    except Exception:
        # Redis is optional for this endpoint; continue without cache on errors
        redis_client = None

    service = RosterService(session.db, redis_client)
    await service.ensure_membership(space_uuid, current_user.id)

    roster_data = await service.get_roster(
        space_uuid,
        current_user.id,
        entry_type=entry_type,
        search=search,
        sort=sort,
        limit=limit,
        offset=offset,
    )

    items = [RosterEntry(**item) for item in roster_data["items"]]
    return RosterResponse(
        items=items,
        total=int(roster_data["total"]),
        limit=int(roster_data["limit"]),
        offset=int(roster_data["offset"]),
    )

class RecentAgentEntry(BaseModel):
    """Lightweight agent entry for quick actions - no caching, real-time recency."""
    id: str
    name: str
    display_name: str
    avatar_url: str | None = None
    last_message_at: datetime | None = None
    is_online: bool = False  # Active in last hour

@router.get("/{space_id}/recent-agents", response_model=list[RecentAgentEntry])
async def get_recent_agents(
    space_id: str,
    limit: int = Query(10, ge=1, le=50, description="Number of agents to return"),
    session: SecureSession = Depends(get_secure_session)
):
    """Return the most recently active agents in a workspace.

    Returns a direct array of agents sorted by most recent message.
    """
    try:
        space_uuid = uuid.UUID(space_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid space identifier",
        )

    # Verify membership
    membership_check = await session.db.execute(
        select(SpaceMembership.id).where(
            SpaceMembership.space_id == space_uuid,
            SpaceMembership.user_id == session.user.id,
        )
    )
    if not membership_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace",
        )

    # Simple: agents sorted by who sent a message most recently
    # Note: Can't GROUP BY capabilities (JSON) directly - extract avatar in subquery
    query = text("""
        SELECT
            a.id,
            a.name,
            a.capabilities->>'avatar_url' as avatar_url,
            MAX(m.created_at) as last_message_at
        FROM agents a
        JOIN messages m ON m.agent_id = a.id
        WHERE m.space_id = :space_id
          AND a.is_internal = false
          AND a.status = 'active'
        GROUP BY a.id, a.name, a.capabilities->>'avatar_url'
        ORDER BY last_message_at DESC
        LIMIT :limit
    """)

    result = await session.db.execute(query, {"space_id": str(space_uuid), "limit": limit})
    rows = result.mappings().all()

    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    agents = []
    for row in rows:
        last_msg = row["last_message_at"]
        # Handle timezone-naive datetimes from database
        if last_msg is not None and last_msg.tzinfo is None:
            last_msg = last_msg.replace(tzinfo=timezone.utc)
        is_online = last_msg is not None and last_msg >= one_hour_ago

        agents.append(RecentAgentEntry(
            id=str(row["id"]),
            name=row["name"],
            display_name=row["name"],
            avatar_url=row["avatar_url"],
            last_message_at=last_msg,
            is_online=is_online,
        ))

    return agents

@router.get("/{space_id}/members", response_model=SpaceMembersResponse)
async def get_space_members(
    space_id: str, session: SecureSession = Depends(get_secure_session)
):
    """Get list of members for a space (only for space members)"""
    try:
        # Verify user is member of the space (can only see members if you're a member)
        membership_query = (
            select(SpaceMembership, Space)
            .join(Space, SpaceMembership.space_id == Space.id)
            .where(and_(SpaceMembership.user_id == session.user.id, SpaceMembership.space_id == space_id))
        )
        membership_result = await session.db.execute(membership_query)
        membership_data = membership_result.first()

        if not membership_data:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must be a member of this space to view its members",
            )

        membership, space = membership_data

        # Get all members of the space with user details
        members_query = (
            select(SpaceMembership, User)
            .join(User, SpaceMembership.user_id == User.id)
            .where(SpaceMembership.space_id == space_id)
            .order_by(SpaceMembership.joined_at.asc())
        )
        members_result = await session.db.execute(members_query)
        members_data = members_result.all()

        members_list = []
        for member_membership, user in members_data:
            # Count user's agents in this space
            agent_count_query = select(func.count(Agent.id)).where(
                and_(Agent.user_id == user.id, Agent.space_id == space_id)
            )
            agent_count_result = await session.db.execute(agent_count_query)
            agent_count = agent_count_result.scalar() or 0

            members_list.append(
                SpaceMemberEntry(
                    user_id=str(user.id),
                    username=user.username,
                    full_name=user.full_name,
                    role=member_membership.role,
                    joined_at=member_membership.joined_at,
                    agent_count=agent_count,
                )
            )

        return SpaceMembersResponse(
            space_id=str(space.id), space_name=space.name, members=members_list, total_members=len(members_list)
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch space members: {e!s}"
        )

# Stats endpoint for user's space creation
@router.get("/stats/me", response_model=dict)
async def get_space_stats(
    session: SecureSession = Depends(get_secure_session)
):
    """Get user's space creation statistics."""
    try:
        if session.user.role == "admin":
            return {"is_admin": True, "unlimited": True}

        rows = (
            (
                await session.db.execute(
                    text(
                        """
            SELECT visibility, COUNT(*) AS c
            FROM spaces
            WHERE created_by = :uid
            GROUP BY visibility
            """
                    ),
                    {"uid": str(session.user.id)},
                )
            )
            .mappings()
            .all()
        )

        used = {r["visibility"]: r["c"] for r in rows}

        return {
            "is_admin": False,
            "limits": VIS_LIMITS,
            "used": {
                "private": used.get("private", 0),
                "invite_only": used.get("invite_only", 0),
                "public": used.get("public", 0),
            },
            "remaining": {
                "private": VIS_LIMITS["private"] - used.get("private", 0),
                "invite_only": VIS_LIMITS["invite_only"] - used.get("invite_only", 0),
                "public": VIS_LIMITS["public"] - used.get("public", 0),
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get space stats: {e!s}",
        )

class MemberRoleUpdate(BaseModel):
    role: str

@router.put("/{space_id}/members/{user_id}/role", response_model=SpaceMembersResponse)
async def update_member_role(
    space_id: str,
    user_id: str,
    role_update: MemberRoleUpdate,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Update a member's role (e.g. promote to admin or demote to member).
    Only admins can perform this action.
    """
    try:
        space_uuid = uuid.UUID(space_id)
        target_user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid identifier format")

    if role_update.role not in ["admin", "member"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role must be 'admin' or 'member'")

    # 1. Verify acting user is admin of the space
    acting_membership = await session.db.execute(
        select(SpaceMembership).where(
            and_(SpaceMembership.space_id == space_uuid, SpaceMembership.user_id == session.user.id)
        )
    )
    acting_member = acting_membership.scalar_one_or_none()

    if not acting_member or acting_member.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only space admins can manage roles")

    # 2. Get target membership
    target_membership_query = select(SpaceMembership).where(
        and_(SpaceMembership.space_id == space_uuid, SpaceMembership.user_id == target_user_uuid)
    )
    target_result = await session.db.execute(target_membership_query)
    target_member = target_result.scalar_one_or_none()

    if not target_member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Target user is not a member of this space"
        )

    # 3. Special check: If demoting self, ensure not last admin
    if str(session.user.id) == str(target_user_uuid) and role_update.role == "member":
        admin_count = await session.db.scalar(
            select(func.count())
            .select_from(SpaceMembership)
            .where(and_(SpaceMembership.space_id == space_uuid, SpaceMembership.role == "admin"))
        )
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot demote yourself - you are the last admin"
            )

    # 4. Update role
    target_member.role = role_update.role
    await session.db.commit()

    # 5. Invalidate caches
    try:
        redis_client = await get_redis_client()
        await redis_client.delete(f"roster:{space_id}:public")
    except Exception:
        pass

    # 6. Return updated member list (reuse logic via redirect or similar,
    # but for simplicity we call the get_space_members logic or return response)
    # We'll just call the get_space_members handler logic or simpler, return success.
    # But response_model is SpaceMembersResponse. Let's redirect to that function's logic manually or just return one.
    # Actually, reusing the get_space_members logic is best.

    return await get_space_members(space_id, session)

# Backward compatibility aliases
OrganizationCreate = SpaceCreate
OrganizationUpdate = SpaceUpdate
OrganizationResponse = SpaceResponse
SwitchOrgRequest = SwitchSpaceRequest
SwitchOrgResponse = SwitchSpaceResponse
JoinPublicOrgRequest = JoinPublicSpaceRequest
OrganizationMember = SpaceMemberEntry
OrganizationMembersResponse = SpaceMembersResponse
