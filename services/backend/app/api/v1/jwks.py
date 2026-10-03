"""
JWKS endpoint — serves the public key for aX JWT verification.

MCP server fetches this to validate Space Agent tokens without
needing the private key or Redis access.

Spec: specs/AX-MCP-AUTH-001/spec.md
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...core.ax_jwt import get_jwks

router = APIRouter(tags=["jwks"])


@router.get("/.well-known/jwks.json")
async def jwks_endpoint():
    """
    JSON Web Key Set endpoint.

    Returns the public key(s) used to verify aX-issued JWTs.
    Cache-Control set to 5 minutes (standard for JWKS).
    """
    return JSONResponse(
        content=get_jwks(),
        headers={"Cache-Control": "public, max-age=300"},
    )
