"""Fleet-wide stop/backoff control API.

Owner/admin surface for reading and toggling the emergency stop and reminder
silence controls. Mutations require an explicit reason and return full readback;
callers must still hold merge/deploy/live-rollout approval separately.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from app.core.redis_client import redis_client
from app.core.rls import SecureSession, get_secure_session
from app.services.fleet_control_service import FleetControlService

router = APIRouter(prefix="/api/v1/fleet-control", tags=["fleet-control"])
fleet_control_service = FleetControlService(redis_client)


class FleetControlStateResponse(BaseModel):
    emergency_stop: bool = False
    reminder_silence: bool = False
    reason: str | None = None
    actor_id: str | None = None
    actor_type: str | None = None
    updated_at: str | None = None
    transition_id: str | None = None
    audit: list[dict] = Field(default_factory=list)


class FleetControlUpdateRequest(BaseModel):
    emergency_stop: bool | None = None
    reminder_silence: bool | None = None
    reason: str = Field(..., min_length=3, max_length=240)
    transition_id: str | None = Field(None, max_length=120)

    @model_validator(mode="after")
    def require_requested_transition(self):
        if self.emergency_stop is None and self.reminder_silence is None:
            raise ValueError("At least one fleet-control field must be provided")
        return self


class FleetControlEnforcementResponse(BaseModel):
    blocked: bool
    surface: Literal["agent_communication", "reminders"]
    reason: str | None = None
    emergency_stop: bool = False
    reminder_silence: bool = False
    actor_id: str | None = None
    actor_type: str | None = None
    updated_at: str | None = None
    transition_id: str | None = None


def _actor_type(session: SecureSession) -> str:
    return "agent" if getattr(session, "agent_id", None) else "user"


def _actor_id(session: SecureSession) -> str:
    return str(getattr(session, "agent_id", None) or session.user.id)


def _is_human_admin(session: SecureSession) -> bool:
    """Fleet-control mutations require a human admin, not an agent principal."""

    if getattr(session, "is_agent", False) or getattr(session, "agent_id", None):
        return False
    if str(getattr(session, "principal_type", "user") or "user").lower() == "agent":
        return False
    return str(getattr(session.user, "role", "") or "").lower() == "admin"


@router.get("", response_model=FleetControlStateResponse)
async def get_fleet_control_state(session: SecureSession = Depends(get_secure_session)):
    """Return current fleet emergency-stop/reminder-silence state."""

    return FleetControlStateResponse(**(await fleet_control_service.get_state()).as_dict())


@router.patch("", response_model=FleetControlStateResponse)
async def update_fleet_control_state(
    body: FleetControlUpdateRequest,
    session: SecureSession = Depends(get_secure_session),
):
    """Update fleet stop/silence controls with explicit audit readback."""

    if not _is_human_admin(session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fleet control updates require human admin privileges",
        )
    try:
        state = await fleet_control_service.update_state(
            emergency_stop=body.emergency_stop,
            reminder_silence=body.reminder_silence,
            reason=body.reason,
            actor_id=_actor_id(session),
            actor_type=_actor_type(session),
            transition_id=body.transition_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return FleetControlStateResponse(**state.as_dict())


@router.get("/enforcement/{surface}", response_model=FleetControlEnforcementResponse)
async def get_fleet_control_enforcement(
    surface: Literal["agent_communication", "reminders"],
    session: SecureSession = Depends(get_secure_session),
):
    """Return explicit enforcement readback for dispatch/reminder callers."""

    return FleetControlEnforcementResponse(
        **await fleet_control_service.enforcement_readback(surface=surface)
    )
