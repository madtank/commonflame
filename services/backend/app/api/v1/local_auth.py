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
import jwt
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.ax_jwt import _get_signing_key, _KEY_ID, _ISSUER, _ALGORITHM
from app.core.rls import SystemSession, get_system_session
from app.core.security import get_effective_space_id, hash_token
from app.models.refresh_token import RefreshToken
from app.models.user import User

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
    if os.getenv("AUTH_MODE", "local").lower() != "local":
        raise HTTPException(status_code=404, detail="Local authentication is disabled")


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
        "iss": _ISSUER, "sub": str(user.id), "user_id": str(user.id),
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
    if not user or not valid or not user.active or user.auth_provider != "local":
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
    if not user or not user.active or user.auth_provider != "local":
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
