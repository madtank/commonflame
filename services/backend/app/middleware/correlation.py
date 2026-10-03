"""
Correlation ID Middleware (OBS-001)

Reads or generates X-Correlation-Id for every request.
Stores it in a contextvars.ContextVar so any code in the request
lifecycle can access it without threading it through parameters.

The correlation_id is:
- Returned in the response header X-Correlation-Id
- Available via get_correlation_id() for logging, SSE, dispatch
- Propagated to dispatch queue headers and SSE events
"""

import re
import uuid
from contextvars import ContextVar
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable — accessible from any async code in the request scope
correlation_id_var: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)

HEADER_NAME = "X-Correlation-Id"

# Validate correlation IDs: UUID format or alphanumeric+hyphens, max 64 chars
_VALID_CID_RE = re.compile(r"^[0-9a-zA-Z\-]{1,64}$")


def get_correlation_id() -> Optional[str]:
    """Get the current request's correlation ID. Returns None outside request scope."""
    return correlation_id_var.get()


def get_or_create_correlation_id() -> str:
    """Get existing correlation ID or create a new one."""
    cid = correlation_id_var.get()
    if cid:
        return cid
    return str(uuid.uuid4())


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware that ensures every request has a correlation ID.

    If the incoming request has X-Correlation-Id, use it.
    Otherwise, generate a new UUID.
    Sets it in contextvars and adds it to the response header.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Read from request header, validate format, or generate new
        raw_cid = request.headers.get(HEADER_NAME)
        cid = raw_cid if raw_cid and _VALID_CID_RE.match(raw_cid) else str(uuid.uuid4())

        # Store in contextvar for the duration of this request
        token = correlation_id_var.set(cid)

        try:
            response = await call_next(request)
            response.headers[HEADER_NAME] = cid
            return response
        finally:
            correlation_id_var.reset(token)
