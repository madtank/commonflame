"""Password-backed, self-hosted browser sessions; OAuth remains the agent lane."""
from datetime import datetime, timedelta, timezone
import os
import secrets
import time
import uuid
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
import jwt
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.ax_jwt import _get_signing_key, _KEY_ID, _ALGORITHM, get_issuer
from app.core.auth_config import builtin_auth_enabled, local_browser_setup_enabled, registration_mode
from app.core.jwt_verify import _resolve_admin_human_user_from_bearer_token
from app.core.rls import SystemSession, get_system_session
from app.core.security import get_effective_space_id, hash_token
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.models.account_invite import AccountInvite
from app.models.space import Space
from app.models.space_membership import SpaceMembership

router = APIRouter(prefix="/auth/local", tags=["local authentication"])
password_hasher = PasswordHasher()
_DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(24))
COOKIE_NAME = "waystation_refresh"
ACCESS_TTL = 900
REFRESH_TTL_DAYS = 7


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=512)


def _assert_local_mode():
    if not builtin_auth_enabled():
        raise HTTPException(status_code=404, detail="Built-in authentication is disabled")


def _check_origin(request: Request):
    """Reject cross-site cookie actions; never trust an arbitrary forwarded host."""
    expected = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != expected:
        raise HTTPException(status_code=403, detail="Origin is not allowed")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site session action rejected")


def verify_local_password(password: str, password_hash: str | None) -> bool:
    try:
        return password_hasher.verify(password_hash or _DUMMY_HASH, password)
    except (InvalidHashError, VerificationError):
        return False


def _cookie_secure() -> bool:
    return urlparse(os.getenv("FRONTEND_URL", "http://localhost:3000")).scheme == "https"


def mint_local_access_token(user: User) -> str:
    private_key, _ = _get_signing_key()
    now = int(time.time())
    return jwt.encode({
        "iss": get_issuer(), "sub": str(user.id), "user_id": str(user.id),
        "typ": "local-user", "token_class": "user_access",
        "aud": ["ax-api", os.getenv("AX_MCP_RESOURCE_URL", "http://localhost:3000/mcp")],
        "space_id": get_effective_space_id(user), "username": user.username,
        "email": user.email, "role": user.role or "user",
        "token_version": user.token_version or 0,
        "iat": now, "exp": now + ACCESS_TTL, "jti": str(uuid.uuid4()),
    }, private_key, algorithm=_ALGORITHM, headers={"kid": _KEY_ID})


async def _issue_pair(user: User, response: Response, system: SystemSession):
    refresh_token = secrets.token_urlsafe(48)
    system.db.add(RefreshToken(
        user_id=user.id, token_hash=hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
    ))
    response.set_cookie(COOKIE_NAME, refresh_token, max_age=REFRESH_TTL_DAYS * 86400,
                        httponly=True, secure=_cookie_secure(), samesite="strict",
                        path="/auth/local")
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_token": mint_local_access_token(user), "token_type": "Bearer",
        "expires_in": ACCESS_TTL, "space_id": get_effective_space_id(user),
        "user": {"id": str(user.id), "email": user.email, "username": user.username,
                 "full_name": user.full_name or user.username, "role": user.role or "user"},
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response,
                system: SystemSession = Depends(get_system_session)):
    _assert_local_mode()
    _check_origin(request)
    result = await system.db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    valid = verify_local_password(body.password, user.password_hash if user else None)
    if not user or not valid or not user.active or user.auth_provider not in {"builtin", "local"}:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user.last_login_at = datetime.now(timezone.utc)
    pair = await _issue_pair(user, response, system)
    await system.db.commit()
    return pair


@router.post("/refresh")
async def refresh(request: Request, response: Response,
                  system: SystemSession = Depends(get_system_session)):
    _assert_local_mode()
    _check_origin(request)
    value = request.cookies.get(COOKIE_NAME)
    if not value:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    result = await system.db.execute(select(RefreshToken).where(
        RefreshToken.token_hash == hash_token(value)).with_for_update())
    row = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if not row or row.revoked_at or row.expires_at <= now:
        raise HTTPException(status_code=401, detail="Session expired; sign in again")
    user = await system.db.get(User, row.user_id)
    if not user or not user.active or user.auth_provider not in {"builtin", "local"}:
        raise HTTPException(status_code=401, detail="Session is no longer active")
    row.revoked_at = now
    pair = await _issue_pair(user, response, system)
    await system.db.commit()
    return pair


@router.post("/logout")
async def logout(request: Request, response: Response,
                 system: SystemSession = Depends(get_system_session)):
    _assert_local_mode()
    _check_origin(request)
    value = request.cookies.get(COOKIE_NAME)
    if value:
        result = await system.db.execute(select(RefreshToken).where(
            RefreshToken.token_hash == hash_token(value)).with_for_update())
        row = result.scalar_one_or_none()
        if row and not row.revoked_at:
            row.revoked_at = datetime.now(timezone.utc)
            await system.db.commit()
    response.delete_cookie(COOKIE_NAME, path="/auth/local", secure=_cookie_secure(),
                           httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "signed_out"}


class AccountRequest(BaseModel):
    token: str | None = Field(default=None, min_length=16, max_length=256)
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]+$")
    password: str = Field(min_length=15, max_length=512)
    full_name: str | None = Field(default=None, max_length=100)


class InviteRequest(BaseModel):
    expires_in_hours: int = Field(default=24, ge=1, le=168)


async def has_builtin_accounts(db) -> bool:
    # Disabled accounts still count: disabling the owner must never reopen setup.
    result = await db.execute(select(User.id).where(User.auth_provider.in_(["builtin", "local"])).limit(1))
    return result.scalar_one_or_none() is not None


async def require_workspace_admin(db, user: User, space_id: uuid.UUID):
    result = await db.execute(select(SpaceMembership).where(
        SpaceMembership.user_id == user.id, SpaceMembership.space_id == space_id))
    membership = result.scalar_one_or_none()
    if not membership or membership.role != "admin":
        raise HTTPException(status_code=403, detail="Workspace admin permission required")


@router.get("/status")
async def account_status(response: Response, system: SystemSession = Depends(get_system_session)):
    _assert_local_mode()
    response.headers["Cache-Control"] = "no-store"
    return {"auth_mode": "builtin", "setup_required": not await has_builtin_accounts(system.db),
            "setup_flow": "browser" if local_browser_setup_enabled() else "token",
            "signup": registration_mode()}


async def _save_account(body: AccountRequest, response: Response, system: SystemSession,
                        *, space_id: uuid.UUID | None = None, invite=None):
    """New accounts own a private workspace; invitations grant member access only."""
    exists = await system.db.execute(select(User.id).where(User.username == body.username))
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username is unavailable")
    new_workspace = space_id is None
    space_id = space_id or uuid.uuid4()
    user = User(id=uuid.uuid4(), space_id=space_id, current_space_id=space_id,
                username=body.username, email=f"{body.username}@waystation.local",
                full_name=body.full_name or body.username, role="user", auth_provider="builtin",
                password_hash=password_hasher.hash(body.password), active=True, token_version=0)
    try:
        if new_workspace:
            space = Space(id=space_id, name=f"{body.username}'s Workspace",
                          slug=f"{body.username.lower()[:40]}-{str(space_id)[:8]}", visibility="private")
            system.db.add(space)
            await system.db.flush()
        system.db.add(user)
        await system.db.flush()
        system.db.add(SpaceMembership(user_id=user.id, space_id=space_id,
                                      role="admin" if new_workspace else "member"))
        if new_workspace:
            space.created_by = user.id
        if invite is not None:
            invite.consumed_at = datetime.now(timezone.utc)
        await system.db.flush()
        pair = await _issue_pair(user, response, system)
        await system.db.commit()
        return pair
    except IntegrityError as exc:
        await system.db.rollback()
        raise HTTPException(status_code=409, detail="Username is unavailable") from exc


async def _create_invited_account(body: AccountRequest, kind: str, request: Request,
                                  response: Response, system: SystemSession):
    _assert_local_mode()
    _check_origin(request)
    now = datetime.now(timezone.utc)
    if kind == "owner_setup":
        # One global transaction lock makes owner creation and the operator CLI
        # agree; a public first-admin race cannot choose the owner.
        await system.db.execute(text("SELECT pg_advisory_xact_lock(840220261003)"))
        if await has_builtin_accounts(system.db):
            raise HTTPException(status_code=409, detail="Owner setup is already complete")
        if not body.token and local_browser_setup_enabled():
            return await _save_account(body, response, system)
    if not body.token:
        raise HTTPException(status_code=400, detail="An operator setup token or workspace invitation is required")
    result = await system.db.execute(select(AccountInvite).where(
        AccountInvite.token_hash == hash_token(body.token), AccountInvite.kind == kind).with_for_update())
    invite = result.scalar_one_or_none()
    if not invite or invite.consumed_at or invite.expires_at <= now:
        raise HTTPException(status_code=400, detail="Setup or invitation token is invalid or expired")
    if kind == "sponsor":
        creator = await system.db.get(User, invite.created_by)
        if not creator or not creator.active or creator.auth_provider not in {"builtin", "local"}:
            raise HTTPException(status_code=400, detail="Invitation is no longer active")
        await require_workspace_admin(system.db, creator, invite.space_id)
    return await _save_account(body, response, system,
                               space_id=invite.space_id if kind == "sponsor" else None, invite=invite)


@router.post("/setup")
async def setup_owner(body: AccountRequest, request: Request, response: Response,
                      system: SystemSession = Depends(get_system_session)):
    return await _create_invited_account(body, "owner_setup", request, response, system)


@router.post("/signup")
async def signup_invited(body: AccountRequest, request: Request, response: Response,
                         system: SystemSession = Depends(get_system_session)):
    _assert_local_mode()
    _check_origin(request)
    mode = registration_mode()
    if mode == "closed":
        raise HTTPException(status_code=403, detail="Account registration is closed on this Waystation")
    if body.token:
        return await _create_invited_account(body, "sponsor", request, response, system)
    if mode != "open":
        raise HTTPException(status_code=403, detail="A workspace invitation is required on this Waystation")
    if not await has_builtin_accounts(system.db):
        raise HTTPException(status_code=409, detail="Create the owner account first")
    return await _save_account(body, response, system)


@router.post("/invites", status_code=201)
async def create_invite(body: InviteRequest, request: Request,
                        system: SystemSession = Depends(get_system_session)):
    _assert_local_mode()
    _check_origin(request)
    if registration_mode() == "closed":
        raise HTTPException(status_code=403, detail="Account registration is closed on this Waystation")
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Human sign-in required")
    user = await _resolve_admin_human_user_from_bearer_token(auth[7:].strip(), system.db)
    if user.auth_provider not in {"builtin", "local"}:
        raise HTTPException(status_code=403, detail="Human sign-in required")
    space_id = uuid.UUID(get_effective_space_id(user))
    await require_workspace_admin(system.db, user, space_id)
    value = "invite_" + secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=body.expires_in_hours)
    system.db.add(AccountInvite(id=uuid.uuid4(), token_hash=hash_token(value), kind="sponsor",
                               created_by=user.id, space_id=space_id, expires_at=expires))
    await system.db.commit()
    return JSONResponse({"token": value, "expires_at": expires.isoformat()}, status_code=201,
                        headers={"Cache-Control": "no-store"})


@router.get("/invites")
async def invite_permission(request: Request, system: SystemSession = Depends(get_system_session)):
    _assert_local_mode()
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Human sign-in required")
    user = await _resolve_admin_human_user_from_bearer_token(auth[7:].strip(), system.db)
    if registration_mode() == "closed":
        return {"can_invite": False}
    try:
        await require_workspace_admin(system.db, user, uuid.UUID(get_effective_space_id(user)))
    except HTTPException as exc:
        if exc.status_code == 403:
            return {"can_invite": False}
        raise
    return {"can_invite": True}
