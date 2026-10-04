"""Provider-independent audience guards for Commonflame-signed tokens."""
import os
from fastapi import Depends, HTTPException, Request
from .jwt_verify import _claim_values, _decode_backend_token, oauth2_scheme


async def require_frontend_audience(request: Request, token: str = Depends(oauth2_scheme)) -> dict:
    claims = _decode_backend_token(token)
    if "ax-api" not in _claim_values(claims.get("aud")) or claims.get("agent_id"):
        raise HTTPException(status_code=403, detail="Human API session required")
    return claims


async def require_mcp_audience(request: Request, token: str = Depends(oauth2_scheme)) -> dict:
    claims = _decode_backend_token(token)
    resource = os.getenv("AX_MCP_RESOURCE_URL", "http://localhost:3000/mcp")
    if resource not in _claim_values(claims.get("aud")):
        raise HTTPException(status_code=403, detail="Token not authorized for MCP")
    return claims
