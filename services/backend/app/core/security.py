"""
JWT Authentication and Security utilities (legacy self-signed JWTs).

Cognito handles all new auth (AUTH-001). These remain for:
- Legacy JWT verification (rls.py, sse.py fallbacks during transition)
- Space-switch access tokens (organizations.py, guest_space.py)
- Password hashing (agent_keys.py)
- Refresh token revocation (logout endpoint)
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import hashlib
import logging

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, model_validator

from ..models.user import User
from ..models.refresh_token import RefreshToken
from .config import get_settings


logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT Configuration (loaded from environment)
settings = get_settings()
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_access_token_expire_minutes
REFRESH_TOKEN_EXPIRE_DAYS = settings.jwt_refresh_token_expire_days


class TokenPayload(BaseModel):
    """JWT token payload structure"""
    sub: str  # user_id
    space_id: str
    token_version: int
    exp: int
    iat: int
    type: str  # "access", "refresh", or "agent"
    agent_name: str | None = None
    agent_id: str | None = None

    @model_validator(mode='before')
    @classmethod
    def _compat_org_id(cls, data):
        """Backwards compat: map org_id -> space_id for in-flight tokens."""
        if isinstance(data, dict) and 'org_id' in data and 'space_id' not in data:
            data['space_id'] = data.pop('org_id')
        return data


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash password using bcrypt"""
    return pwd_context.hash(password)


def create_access_token(
    user_id: str,
    space_id: str,
    token_version: int,
    extra_claims: Optional[Dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
    context_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Create a self-signed access token (used for space-switch JWTs)."""
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))

    payload = {
        "sub": user_id,
        "space_id": space_id,
        "token_version": token_version,
        "exp": expire,
        "iat": now,
        "type": "access",
    }

    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[TokenPayload]:
    """Verify and decode a legacy self-signed JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_aud": False})

        # Check if it's an agent token (different format)
        is_agent_token = (
            "agent_name" in payload and "user_id" in payload and (
                payload.get("type") == "agent" or
                payload.get("grant_type") == "client_credentials" or
                str(payload.get("sub", "")).startswith("agent:")
            )
        )
        if is_agent_token:
            return TokenPayload(
                sub=payload["user_id"],
                space_id=payload.get("space_id") or payload.get("org_id", "00000000-0000-0000-0000-000000000000"),
                token_version=payload.get("token_version", 0),
                exp=payload["exp"],
                iat=payload["iat"],
                type="agent",
                agent_name=payload.get("agent_name"),
                agent_id=payload.get("agent_id"),
            )
        else:
            return TokenPayload(**payload)
    except JWTError:
        return None


def hash_token(token: str) -> str:
    """Hash token for database storage (refresh tokens)"""
    return hashlib.sha256(token.encode()).hexdigest()


def get_effective_space_id(user: User) -> str:
    """Get the effective space ID for a user."""
    if user.current_space_id:
        return str(user.current_space_id)
    return str(user.space_id)


async def revoke_refresh_token(db: AsyncSession, refresh_token: str) -> bool:
    """Revoke a refresh token and reset the user's current space to home (used by logout endpoint).

    Resetting current_space_id at logout ensures the next login lands on the user's
    home/personal space rather than whichever space they happened to be in last —
    which was sticky across logout and caused users to land on transient test spaces
    after sign-out/sign-in (see task 038b5100).
    """
    token_hash = hash_token(refresh_token)

    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .where(RefreshToken.revoked_at.is_(None))
    )

    db_token = result.scalar_one_or_none()
    if not db_token:
        return False

    db_token.revoked_at = datetime.utcnow()

    if db_token.user_id:
        user_result = await db.execute(
            select(User).where(User.id == db_token.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user and user.space_id and user.current_space_id != user.space_id:
            user.current_space_id = user.space_id

    await db.commit()
    return True
