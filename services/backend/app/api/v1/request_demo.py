"""Public request-demo contact endpoint.

This is intentionally separate from the invite-only access gate: demo interest
submissions should notify the operator but must not mutate AccessRequest rows or change
waitlist/CLA behavior.
"""

import asyncio
import logging
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from ...core.ses_client import send_request_demo_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["public"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RequestDemoSubmission(BaseModel):
    """Payload accepted from the public Request demo form."""

    email: Annotated[str, Field(min_length=3, max_length=320)]
    full_name: Annotated[str | None, Field(max_length=200)] = None
    company: Annotated[str | None, Field(max_length=200)] = None
    role: Annotated[str | None, Field(max_length=200)] = None
    interest: Annotated[str, Field(min_length=1, max_length=4000)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _EMAIL_RE.fullmatch(normalized):
            raise ValueError("Invalid email address")
        return normalized

    @field_validator("full_name", "company", "role", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped or None

    @field_validator("interest")
    @classmethod
    def normalize_interest(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Interest is required")
        return stripped


class RequestDemoResponse(BaseModel):
    ok: bool = True


async def _send_request_demo_notification_off_loop(
    submission: RequestDemoSubmission,
) -> bool:
    """Run the synchronous boto3 SES call off the event loop."""

    return await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: send_request_demo_notification(
            requester_email=submission.email,
            full_name=submission.full_name,
            company=submission.company,
            role=submission.role,
            interest=submission.interest,
        ),
    )


@router.post(
    "/request-demo",
    response_model=RequestDemoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_demo(submission: RequestDemoSubmission) -> RequestDemoResponse:
    """Accept public demo-interest submissions and notify the operator via SES.

    Email delivery is part of the contract for this front-door flow: do not show
    a false-success UI if SES/config is unavailable. The backend logs the failed
    send and returns a service-unavailable response that the frontend renders as
    an actionable support fallback.
    """

    try:
        sent = await _send_request_demo_notification_off_loop(submission)
    except Exception as exc:  # noqa: BLE001 — no silent/false-success front-door flow
        logger.exception(
            "REQUEST_DEMO_EMAIL_EXCEPTION requester=%s error=%s surfacing_failure=true",
            submission.email,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Demo request email could not be sent. Please try again or email "
                "the deployment operator."
            ),
        ) from exc

    if sent:
        logger.info("REQUEST_DEMO_EMAIL_SENT requester=%s", submission.email)
        return RequestDemoResponse(ok=True)

    logger.error(
        "REQUEST_DEMO_EMAIL_NOT_SENT requester=%s surfacing_failure=true",
        submission.email,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Demo request email could not be sent. Please try again or email "
            "the deployment operator."
        ),
    )


# Compatibility alias for frontend clients that namespace public surfaces.
@router.post(
    "/public/request-demo",
    response_model=RequestDemoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def public_request_demo(submission: RequestDemoSubmission) -> RequestDemoResponse:
    return await request_demo(submission)
