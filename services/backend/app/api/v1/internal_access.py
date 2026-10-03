"""Internal one-click access-approval endpoint (invite-only waitlist gate).

GET /auth/access/approve?token=<signed>

NOT behind require_internal_auth: this is clicked from an email link in a browser,
so the signed HS256 token IS the authentication. Idempotent.

Design: docs/plans/2026-05-28-invite-only-waitlist-gate-design.md
"""
import html
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_approval import set_request_status, verify_approve_token
from app.core.database import get_db_session
from app.models.access_request import AccessRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["access-approval"])


def _html(body: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        content=(
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>aX access</title></head>"
            f"<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;'>{body}</body>"
            "</html>"
        ),
        status_code=status_code,
    )


@router.get("/access/approve", response_class=HTMLResponse)
async def approve_access(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Verify the signed token and flip the matching AccessRequest to approved."""
    email = verify_approve_token(token)
    if not email:
        logger.info("ACCESS_APPROVE_BAD_TOKEN")
        return _html("<h2>⚠️ Invalid or expired approval link.</h2>", 400)

    result = await db.execute(select(AccessRequest).where(AccessRequest.email == email))
    req = result.scalar_one_or_none()

    if req is None:
        # Token is valid but no request row — record one (pending) so the shared
        # helper can flip it to approved (and re-enable a matching disabled user).
        # Idempotent and safe (token was admin-issued).
        req = AccessRequest(email=email, status="pending")
        db.add(req)
        await db.flush()
        await set_request_status(db, req, "approved", decided_by="email_link")
        logger.info("ACCESS_APPROVE_CREATED email=%s", email)
        return _html(f"<h2>✅ Approved — {html.escape(email)} can now sign in.</h2>", 200)

    if req.status != "approved":
        await set_request_status(db, req, "approved", decided_by="email_link")
        logger.info("ACCESS_APPROVE_FLIPPED email=%s", email)

    return _html(f"<h2>✅ Approved — {html.escape(email)} can now sign in.</h2>", 200)
