"""
Temporary middleware to bridge legacy `/mcp/*` browser requests while the
frontend migrates fully to the API SSE endpoints.

Responsibilities:
- Promote `?token=` query parameters into `Authorization: Bearer ...` headers so
  EventSource clients (which cannot set custom headers) pass authentication.
- Ensure all error responses include the required CORS headers, so browsers see
  the real HTTP status instead of opaque CORS failures.

This middleware should be removed once the frontend no longer calls `/mcp/*`.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import HTTPException as FastAPIHTTPException
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class MCPTokenQueryAdapterMiddleware(BaseHTTPMiddleware):
    """Promote `token` query params to bearer headers and guarantee CORS."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        self._inject_bearer_header(request)

        try:
            response = await call_next(request)
        except (FastAPIHTTPException, StarletteHTTPException) as exc:
            detail = getattr(exc, "detail", None)
            status_code = getattr(exc, "status_code", 500)
            headers = getattr(exc, "headers", None) or {}
            response = JSONResponse({"detail": detail}, status_code=status_code, headers=headers)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unhandled exception in MCP token adapter middleware: %s", exc)
            response = JSONResponse({"detail": "Server error"}, status_code=500)

        self._ensure_cors_headers(request, response)
        return response

    def _inject_bearer_header(self, request: Request) -> None:
        """If `/mcp/*` request lacks Authorization, promote `token` query param."""
        if not request.url.path.startswith("/mcp/"):
            return

        headers = MutableHeaders(scope=request.scope)
        if any(key.lower() == "authorization" for key in headers.keys()):
            return

        token: Optional[str] = request.query_params.get("token")
        if not token:
            return

        headers["authorization"] = f"Bearer {token}"
        # Mark that the header was synthesized for downstream logging/rate limiting
        headers.setdefault("x-ax-token-source", "query-param")
        logger.debug("Injected bearer header for /mcp/* request based on query token.")

    def _ensure_cors_headers(self, request: Request, response: Response) -> None:
        """
        Emit CORS headers for /mcp/* paths only, respecting the CORS allowlist.

        SECURITY: Only echoes origin if it's in the configured allowlist to prevent
        bypassing CORSMiddleware's security boundaries.
        """
        # Only apply custom CORS logic to /mcp/* paths (this middleware's purpose)
        if not request.url.path.startswith("/mcp/"):
            return

        origin: Optional[str] = request.headers.get("origin")
        headers = response.headers

        # Get allowed origins from settings
        allowed_origins = settings.cors_origins

        if origin:
            # SECURITY: Only echo origin if it's in the allowlist
            if origin in allowed_origins or "*" in allowed_origins:
                headers.setdefault("access-control-allow-origin", origin)
                headers.setdefault("access-control-allow-credentials", "true")
                headers.setdefault("vary", "Origin")
            else:
                # Origin not allowed - don't set CORS headers (will fail in browser)
                logger.warning(f"Blocked CORS request from unauthorized origin: {origin}")
                return
        else:
            # EventSource without credentials - allow any origin without credentials
            # This is safe because without credentials, can't access sensitive data
            headers.setdefault("access-control-allow-origin", "*")
            if headers.get("access-control-allow-credentials") == "true":
                headers.pop("access-control-allow-credentials")

        headers.setdefault("access-control-allow-methods", "*")
        headers.setdefault("access-control-allow-headers", "*")
