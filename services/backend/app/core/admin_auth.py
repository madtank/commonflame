"""Management endpoint authentication (AUTH-SPEC-001 §7, §8).

Management routes (/credentials/*, /agents/manage/*, /delegations/*) require
user_admin JWTs with specific scopes. This module provides FastAPI dependencies
that enforce these requirements.

Design: API-first. Everything that can be done in the UI can be done via API.
user_admin tokens enable programmatic agent lifecycle management.
"""
import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from jose import jwt as jose_jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .ax_jwt import get_jwks
from .database import get_db_session
from ..models.user import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdminPrincipal:
    """Authenticated user_admin principal for management operations."""
    user_id: str
    user: User
    token_class: str
    scopes: set[str]
    src_credential_id: str


async def _resolve_admin_jwt(request: Request, db: AsyncSession) -> AdminPrincipal:
    """Validate a user_admin JWT from the Authorization header.

    Only accepts backend-issued JWTs with token_class=user_admin.
    Cognito JWTs, PATs, and other token types are rejected.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing_token", "message": "Authorization header required"})

    token = auth_header[7:].strip()

    # Reject PATs immediately
    if token.startswith("axp_"):
        raise HTTPException(status_code=403, detail={
            "error": "admin_required",
            "message": "Management endpoints require a user_admin JWT. Exchange your PAT first.",
        })

    # Decode as backend-issued JWT
    try:
        unverified = jose_jwt.get_unverified_claims(token)
        if unverified.get("iss") != "ax-backend":
            raise HTTPException(status_code=403, detail={
                "error": "admin_required",
                "message": "Management endpoints require a backend-issued user_admin JWT.",
            })

        jwks = get_jwks()
        headers = jose_jwt.get_unverified_headers(token)
        kid = headers.get("kid")
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = k
                break
        if not key:
            raise HTTPException(status_code=401, detail={"error": "invalid_token", "message": "Token key not found"})

        claims = jose_jwt.decode(
            token, key, algorithms=["RS256"],
            issuer="ax-backend",
            options={"verify_aud": False},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail={"error": "invalid_token", "message": f"Token validation failed: {e}"})

    # Enforce token_class = user_admin
    token_class = claims.get("token_class")
    if token_class != "user_admin":
        raise HTTPException(status_code=403, detail={
            "error": "admin_required",
            "message": f"Management endpoints require user_admin token, got {token_class}.",
        })

    # Parse scopes
    scope_str = claims.get("scope", "")
    scopes = set(scope_str.split()) if scope_str else set()

    # Resolve user
    owner_user_id = claims.get("owner_user_id")
    if not owner_user_id:
        raise HTTPException(status_code=401, detail={"error": "invalid_token", "message": "Missing owner_user_id"})

    import uuid
    try:
        user_uuid = uuid.UUID(owner_user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail={"error": "invalid_token", "message": "Invalid owner_user_id"})

    result = await db.execute(select(User).where(User.id == user_uuid, User.active))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail={"error": "invalid_token", "message": "User not found"})

    return AdminPrincipal(
        user_id=owner_user_id,
        user=user,
        token_class=token_class,
        scopes=scopes,
        src_credential_id=claims.get("src_credential_id", ""),
    )


def require_scope(*required_scopes: str):
    """Dependency factory: require specific scopes on the user_admin JWT."""
    async def _check(
        request: Request,
        db: AsyncSession = Depends(get_db_session),
    ) -> AdminPrincipal:
        principal = await _resolve_admin_jwt(request, db)
        missing = set(required_scopes) - principal.scopes
        if missing:
            raise HTTPException(status_code=403, detail={
                "error": "insufficient_scope",
                "message": f"Missing required scopes: {sorted(missing)}",
                "required": sorted(required_scopes),
                "granted": sorted(principal.scopes),
            })
        return principal
    return _check
