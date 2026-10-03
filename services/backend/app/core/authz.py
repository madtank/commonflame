"""Audience/scope authorization guards (AUTH-001).

Simple FastAPI dependencies to restrict endpoints by Cognito client audience.
"""
from fastapi import Depends, HTTPException, Request

from .auth_config import FRONTEND_AUDIENCE, MCP_AUDIENCE
from .jwt_verify import get_current_user_from_token, oauth2_scheme, _decode_token


async def require_frontend_audience(request: Request, token: str = Depends(oauth2_scheme)) -> dict:
    """Dependency that ensures the JWT was issued for the frontend client."""
    claims = await _decode_token(token)
    aud = claims.get("aud") or claims.get("client_id")
    if aud != FRONTEND_AUDIENCE:
        raise HTTPException(status_code=403, detail="Token not authorized for frontend access")
    return claims


async def require_mcp_audience(request: Request, token: str = Depends(oauth2_scheme)) -> dict:
    """Dependency that ensures the JWT was issued for the MCP client."""
    claims = await _decode_token(token)
    aud = claims.get("aud") or claims.get("client_id")
    if aud != MCP_AUDIENCE:
        raise HTTPException(status_code=403, detail="Token not authorized for MCP access")
    return claims
