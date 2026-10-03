"""
Guest Space Access API

Endpoints for invite-based guest access to spaces.

Spec: docs/guest-space-access-spec.md

Routes:
  POST   /api/v1/spaces/{space_id}/invites                        Create invite (owner/admin)
  GET    /api/v1/spaces/{space_id}/invites                        List invites + usage stats
  DELETE /api/v1/spaces/{space_id}/invites/{invite_id}            Revoke invite (owner/admin)
  POST   /api/v1/invites/{token}/redeem                           Guest redeems invite
  DELETE /api/v1/spaces/{space_id}/members/{agent_id}             Remove member (owner/admin)
  GET    /api/v1/spaces/{space_id}/channels                       List channels with guest visibility settings
  PATCH  /api/v1/spaces/{space_id}/channels/{channel_name}        Set guest_accessible flag (upsert)

Security notes:
  - token_hash: SHA-256 of opaque plaintext; plaintext returned once on creation, never stored
  - use_count: atomically incremented via SELECT FOR UPDATE inside a transaction
  - Guest JWTs: 10-minute TTL hard cap (see §7 of spec)
  - Live membership check: enforced at middleware layer (§8) — not duplicated here
  - Revocation: application-layer middleware required; RLS alone does not enforce it (see spec §8 gap note)
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.database import get_db_session
from ...core.rls import SecureSession, get_secure_session
from ...core.security import create_access_token
from ...models.agent import Agent
from ...models.guest_space import SpaceChannelSetting, SpaceInvite, SpaceMember
from ...models.space import Space
from ...models.space_membership import SpaceMembership

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["guest-space"])


# ---------------------------------------------------------------------------
# Dependency: require_active_guest_membership
# ---------------------------------------------------------------------------
async def require_active_guest_membership(
    space_id: str,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
) -> SpaceMember:
    """Gate dependency for guest-accessible routes.

    Enforces revocation at application layer (spec §8). RLS alone does not
    catch mid-session revocations — this check runs on every protected request
    so revocation takes effect at the next API call (bounded by JWT TTL = 10 min).

    Usage:
        @router.get("/spaces/{space_id}/...")
        async def my_endpoint(
            ...,
            _: SpaceMember = Depends(require_active_guest_membership),
        ):
    """
    result = await db.execute(
        select(SpaceMember).where(
            and_(
                SpaceMember.space_id == UUID(space_id),
                SpaceMember.agent_id == UUID(_caller_id(session)),
                SpaceMember.status == "active",
            )
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "membership_revoked",
                "message": "Guest membership is not active for this space",
            },
        )
    return member


def _caller_id(session: SecureSession) -> str:
    """Return the authenticated caller's UUID string regardless of token type.

    SecureSession.user_id does not exist. Use this helper everywhere:
    - Agent tokens (client_credentials): session.agent_id
    - User tokens (password/code): str(session.user.id)
    """
    if session.is_agent:
        return session.agent_id
    return str(session.user.id)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GUEST_JWT_TTL_MINUTES = 10  # Hard cap per spec §7


def _hash_token(plaintext: str) -> str:
    """SHA-256 hash of invite token. Only the hash is stored."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


async def _assert_space_admin(
    space_id: str,
    session: SecureSession,
    db: AsyncSession,
) -> Space:
    """Raise 403 if caller is not owner/admin of space. Returns the space."""
    space = await db.get(Space, UUID(space_id))
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    # Check membership role: must be owner or admin
    result = await db.execute(
        select(SpaceMember).where(
            and_(
                SpaceMember.space_id == UUID(space_id),
                SpaceMember.agent_id == UUID(_caller_id(session)),
                SpaceMember.status == "active",
            )
        )
    )
    membership = result.scalar_one_or_none()

    # Also accept owner via organizations table (created_by)
    is_creator = str(space.created_by) == _caller_id(session) if hasattr(space, "created_by") else False

    if not is_creator and (not membership or membership.role not in ("owner", "admin")):
        raise HTTPException(status_code=403, detail="Must be space owner or admin")

    return space


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateInviteRequest(BaseModel):
    expires_at: Optional[datetime] = Field(None, description="Expiry timestamp (UTC). Null = no expiry.")
    max_uses: Optional[int] = Field(None, ge=1, description="Maximum redemptions. Null = unlimited.")
    note: Optional[str] = Field(None, max_length=255, description="Human-readable label for the invite table.")


class InviteResponse(BaseModel):
    id: str
    space_id: str
    created_by: str
    expires_at: Optional[datetime]
    max_uses: Optional[int]
    use_count: int
    note: Optional[str]
    revoked_at: Optional[datetime]
    created_at: datetime
    # plaintext_token is only set on creation — never returned again
    plaintext_token: Optional[str] = None

    @classmethod
    def from_orm(cls, invite: SpaceInvite, plaintext_token: Optional[str] = None) -> "InviteResponse":
        return cls(
            id=str(invite.id),
            space_id=str(invite.space_id),
            created_by=str(invite.created_by),
            expires_at=invite.expires_at,
            max_uses=invite.max_uses,
            use_count=invite.use_count,
            note=invite.note,
            revoked_at=invite.revoked_at,
            created_at=invite.created_at,
            plaintext_token=plaintext_token,
        )


class RedeemInviteRequest(BaseModel):
    """Agent identity is taken from the Bearer token — no body needed, but kept for extensibility."""
    pass


class RedeemInviteResponse(BaseModel):
    space_id: str
    agent_id: str
    role: str
    # OAuth-style token wrapper — compatible with both MCP clients and browser localStorage pattern
    access_token: str
    token_type: str = "bearer"
    expires_in: int = GUEST_JWT_TTL_MINUTES * 60  # seconds


class MemberResponse(BaseModel):
    id: str
    space_id: str
    agent_id: str
    role: str
    rate_limit_tier: str
    status: str
    invite_id: Optional[str]
    joined_at: datetime
    revoked_at: Optional[datetime]


# ---------------------------------------------------------------------------
# POST /api/v1/spaces/{space_id}/invites — Create invite
# ---------------------------------------------------------------------------

@router.post(
    "/spaces/{space_id}/invites",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a guest invite for a space (owner/admin only)",
)
async def create_invite(
    space_id: str,
    body: CreateInviteRequest,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    await _assert_space_admin(space_id, session, db)

    plaintext = secrets.token_urlsafe(32)
    token_hash = _hash_token(plaintext)

    invite = SpaceInvite(
        space_id=UUID(space_id),
        created_by=UUID(_caller_id(session)),
        token_hash=token_hash,
        expires_at=body.expires_at,
        max_uses=body.max_uses,
        note=body.note,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    logger.info(
        "Guest invite created",
        extra={"space_id": space_id, "invite_id": str(invite.id), "creator": _caller_id(session)},
    )

    return InviteResponse.from_orm(invite, plaintext_token=plaintext)


# ---------------------------------------------------------------------------
# GET /api/v1/spaces/{space_id}/invites — List invites
# ---------------------------------------------------------------------------

@router.get(
    "/spaces/{space_id}/invites",
    response_model=list[InviteResponse],
    summary="List all invites for a space (owner/admin only)",
)
async def list_invites(
    space_id: str,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    await _assert_space_admin(space_id, session, db)

    result = await db.execute(
        select(SpaceInvite)
        .where(SpaceInvite.space_id == UUID(space_id))
        .order_by(SpaceInvite.created_at.desc())
    )
    invites = result.scalars().all()

    # Never expose plaintext_token on list — only on creation
    return [InviteResponse.from_orm(inv) for inv in invites]


# ---------------------------------------------------------------------------
# DELETE /api/v1/spaces/{space_id}/invites/{invite_id} — Revoke invite
# ---------------------------------------------------------------------------

@router.delete(
    "/spaces/{space_id}/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a guest invite (owner/admin only)",
)
async def revoke_invite(
    space_id: str,
    invite_id: str,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    await _assert_space_admin(space_id, session, db)

    invite = await db.get(SpaceInvite, UUID(invite_id))
    if not invite or str(invite.space_id) != space_id:
        raise HTTPException(status_code=404, detail="Invite not found")

    if invite.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Invite is already revoked")

    invite.revoked_at = datetime.now(timezone.utc)

    # Cascade revocation to all SpaceMember rows created from this invite (spec §8)
    revoked_now = datetime.now(timezone.utc)
    await db.execute(
        update(SpaceMember)
        .where(
            and_(
                SpaceMember.invite_id == invite.id,
                SpaceMember.status == "active",
            )
        )
        .values(status="revoked", revoked_at=revoked_now)
    )

    await db.commit()

    logger.info(
        "Guest invite revoked",
        extra={"space_id": space_id, "invite_id": invite_id, "revoker": _caller_id(session)},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/invites/{token}/redeem — Guest redeems invite
# ---------------------------------------------------------------------------

@router.post(
    "/invites/{token}/redeem",
    response_model=RedeemInviteResponse,
    summary="Redeem an invite token to join a space as a guest",
)
async def redeem_invite(
    token: str,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    token_hash = _hash_token(token)

    # SELECT FOR UPDATE — atomic use_count enforcement to prevent TOCTOU races
    result = await db.execute(
        select(SpaceInvite)
        .where(SpaceInvite.token_hash == token_hash)
        .with_for_update()
    )
    invite = result.scalar_one_or_none()

    if not invite:
        raise HTTPException(status_code=404, detail="Invalid invite token")

    if not invite.is_valid:
        raise HTTPException(
            status_code=410,
            detail="Invite is expired, revoked, or exhausted",
        )

    # Idempotent: if caller is already a member, return their current membership
    existing = await db.execute(
        select(SpaceMember).where(
            and_(
                SpaceMember.space_id == invite.space_id,
                SpaceMember.agent_id == UUID(_caller_id(session)),
            )
        )
    )
    existing_member = existing.scalar_one_or_none()
    if existing_member:
        if existing_member.status == "revoked":
            raise HTTPException(status_code=403, detail="Your membership in this space has been revoked")
        raise HTTPException(status_code=409, detail="Already a member of this space")

    # Add guest membership
    member = SpaceMember(
        space_id=invite.space_id,
        agent_id=UUID(_caller_id(session)),
        role="guest",
        rate_limit_tier="guest",
        status="active",
        invite_id=invite.id,
    )
    db.add(member)

    # Increment use_count atomically (we hold the FOR UPDATE lock)
    invite.use_count += 1

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Concurrent same-agent redeem race — both passed the "already a member?" check
        # before either committed. The unique constraint caught the second one. Return 409.
        raise HTTPException(status_code=409, detail="Already a member of this space")
    await db.refresh(member)

    # Issue short-lived guest JWT (10-minute TTL — spec §7 hard cap)
    # token_version=1: agent tokens don't use User.token_version; use 1 as base
    guest_token = create_access_token(
        user_id=_caller_id(session),
        space_id=str(invite.space_id),
        token_version=1,
        extra_claims={
            "agent_id": _caller_id(session),
            "role": "guest",
            "rate_limit_tier": "guest",
            "scope": "guest",
        },
        expires_delta=timedelta(minutes=GUEST_JWT_TTL_MINUTES),
    )

    logger.info(
        "Guest invite redeemed",
        extra={
            "space_id": str(invite.space_id),
            "agent_id": _caller_id(session),
            "invite_id": str(invite.id),
            "use_count": invite.use_count,
        },
    )

    return RedeemInviteResponse(
        space_id=str(invite.space_id),
        agent_id=_caller_id(session),
        role="guest",
        access_token=guest_token,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/spaces/{space_id}/members/{agent_id} — Remove member
# ---------------------------------------------------------------------------

@router.delete(
    "/spaces/{space_id}/members/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member from a space (owner/admin only)",
)
async def remove_member(
    space_id: str,
    agent_id: str,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    await _assert_space_admin(space_id, session, db)

    result = await db.execute(
        select(SpaceMember).where(
            and_(
                SpaceMember.space_id == UUID(space_id),
                SpaceMember.agent_id == UUID(agent_id),
            )
        )
    )
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found in this space")

    if member.status == "revoked":
        raise HTTPException(status_code=409, detail="Member is already revoked")

    member.status = "revoked"
    member.revoked_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(
        "Space member removed",
        extra={"space_id": space_id, "agent_id": agent_id, "removed_by": _caller_id(session)},
    )


# ---------------------------------------------------------------------------
# Channel Settings schemas
# ---------------------------------------------------------------------------

class ChannelSettingResponse(BaseModel):
    channel_name: str
    guest_accessible: bool
    updated_at: Optional[datetime]


class PatchChannelSettingRequest(BaseModel):
    guest_accessible: bool = Field(..., description="Whether this channel is visible and accessible to guests.")


# ---------------------------------------------------------------------------
# GET /api/v1/spaces/{space_id}/channels — List channel guest-visibility settings
# ---------------------------------------------------------------------------

@router.get(
    "/spaces/{space_id}/channels",
    response_model=list[ChannelSettingResponse],
    summary="List all channels and their guest-accessibility settings (owner/admin only)",
)
async def list_channel_settings(
    space_id: str,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    await _assert_space_admin(space_id, session, db)

    result = await db.execute(
        select(SpaceChannelSetting)
        .where(SpaceChannelSetting.space_id == UUID(space_id))
        .order_by(SpaceChannelSetting.channel_name)
    )
    settings = result.scalars().all()

    return [
        ChannelSettingResponse(
            channel_name=s.channel_name,
            guest_accessible=s.guest_accessible,
            updated_at=s.updated_at,
        )
        for s in settings
    ]


# ---------------------------------------------------------------------------
# PATCH /api/v1/spaces/{space_id}/channels/{channel_name} — Toggle guest_accessible (upsert)
# ---------------------------------------------------------------------------

@router.patch(
    "/spaces/{space_id}/channels/{channel_name}",
    response_model=ChannelSettingResponse,
    summary="Set guest_accessible flag for a channel (upsert). Owner/admin only.",
)
async def patch_channel_setting(
    space_id: str,
    channel_name: str,
    body: PatchChannelSettingRequest,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    await _assert_space_admin(space_id, session, db)

    if len(channel_name) > 50:
        raise HTTPException(status_code=422, detail="channel_name must be ≤ 50 characters")

    result = await db.execute(
        select(SpaceChannelSetting).where(
            and_(
                SpaceChannelSetting.space_id == UUID(space_id),
                SpaceChannelSetting.channel_name == channel_name,
            )
        )
    )
    setting = result.scalar_one_or_none()

    if setting is None:
        # Create row (upsert: channel may not have an explicit setting yet)
        setting = SpaceChannelSetting(
            space_id=UUID(space_id),
            channel_name=channel_name,
            guest_accessible=body.guest_accessible,
        )
        db.add(setting)
    else:
        setting.guest_accessible = body.guest_accessible
        setting.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(setting)

    logger.info(
        "Channel guest setting updated",
        extra={
            "space_id": space_id,
            "channel_name": channel_name,
            "guest_accessible": body.guest_accessible,
            "actor": _caller_id(session),
        },
    )

    return ChannelSettingResponse(
        channel_name=setting.channel_name,
        guest_accessible=setting.guest_accessible,
        updated_at=setting.updated_at,
    )


# ---------------------------------------------------------------------------
# Space guest settings schemas
# ---------------------------------------------------------------------------

class PatchSpaceGuestSettingsRequest(BaseModel):
    show_member_list_to_guests: Optional[bool] = Field(
        None,
        description="When true, guests can see the space member list. Default: false.",
    )


class SpaceGuestSettingsResponse(BaseModel):
    space_id: str
    show_member_list_to_guests: bool


# ---------------------------------------------------------------------------
# PATCH /api/v1/spaces/{space_id} — Update space-level guest settings
# ---------------------------------------------------------------------------

@router.patch(
    "/spaces/{space_id}",
    response_model=SpaceGuestSettingsResponse,
    summary="Update space-level guest settings (owner/admin only)",
)
async def patch_space_guest_settings(
    space_id: str,
    body: PatchSpaceGuestSettingsRequest,
    session: SecureSession = Depends(get_secure_session),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Update guest-visibility settings for a space.

    Currently supports:
      - **show_member_list_to_guests**: when true, guests can see who else is in the space.

    Owner/admin only. Partial update — only provided fields are changed.
    """
    space = await _assert_space_admin(space_id, session, db)

    if body.show_member_list_to_guests is not None:
        await db.execute(
            update(Space)
            .where(Space.id == UUID(space_id))
            .values(show_member_list_to_guests=body.show_member_list_to_guests)
        )
        await db.commit()
        await db.refresh(space)

    logger.info(
        "Space guest settings updated",
        extra={
            "space_id": space_id,
            "show_member_list_to_guests": body.show_member_list_to_guests,
            "actor": _caller_id(session),
        },
    )

    return SpaceGuestSettingsResponse(
        space_id=space_id,
        show_member_list_to_guests=getattr(space, 'show_member_list_to_guests', False),
    )
