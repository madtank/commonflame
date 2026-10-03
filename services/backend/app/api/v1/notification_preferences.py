"""
Notification Preferences API

Endpoints for managing email notification settings.
Separate from the existing notifications.py which handles mention listing.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import time as dt_time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.jwt_verify import get_current_user_from_token
from app.models.notification_preferences import NotificationPreferences

router = APIRouter(prefix="/api/notifications", tags=["notification-preferences"])


class NotificationPrefsResponse(BaseModel):
    email_enabled: bool = True
    email_on_mention: bool = True
    email_on_agent_error: bool = False
    email_on_task_complete: bool = False
    digest_mode: str = "immediate"
    digest_window_minutes: int = 5
    quiet_hours_enabled: bool = True
    quiet_hours_start: Optional[str] = "23:00"
    quiet_hours_end: Optional[str] = "08:00"
    timezone: Optional[str] = "America/Los_Angeles"
    max_emails_per_hour: int = 10


class NotificationPrefsUpdate(BaseModel):
    email_enabled: Optional[bool] = None
    email_on_mention: Optional[bool] = None
    email_on_agent_error: Optional[bool] = None
    email_on_task_complete: Optional[bool] = None
    digest_mode: Optional[str] = None
    quiet_hours_enabled: Optional[bool] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    timezone: Optional[str] = None
    max_emails_per_hour: Optional[int] = None


@router.get("/email-preferences", response_model=NotificationPrefsResponse)
async def get_email_preferences(
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user_from_token),
):
    """Get current user's email notification preferences."""
    result = await db.execute(
        select(NotificationPreferences).where(
            NotificationPreferences.user_id == current_user.id
        )
    )
    prefs = result.scalar_one_or_none()

    if not prefs:
        return NotificationPrefsResponse()

    return NotificationPrefsResponse(
        email_enabled=prefs.email_enabled,
        email_on_mention=prefs.email_on_mention,
        email_on_agent_error=prefs.email_on_agent_error,
        email_on_task_complete=prefs.email_on_task_complete,
        digest_mode=prefs.digest_mode,
        digest_window_minutes=prefs.digest_window_minutes,
        quiet_hours_enabled=prefs.quiet_hours_enabled,
        quiet_hours_start=str(prefs.quiet_hours_start) if prefs.quiet_hours_start else None,
        quiet_hours_end=str(prefs.quiet_hours_end) if prefs.quiet_hours_end else None,
        timezone=prefs.timezone,
        max_emails_per_hour=prefs.max_emails_per_hour,
    )


@router.patch("/email-preferences", response_model=NotificationPrefsResponse)
async def update_email_preferences(
    updates: NotificationPrefsUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(get_current_user_from_token),
):
    """Update email notification preferences. Only provided fields are changed."""
    result = await db.execute(
        select(NotificationPreferences).where(
            NotificationPreferences.user_id == current_user.id
        )
    )
    prefs = result.scalar_one_or_none()

    if not prefs:
        prefs = NotificationPreferences(user_id=current_user.id)
        db.add(prefs)

    update_data = updates.model_dump(exclude_unset=True)

    if "digest_mode" in update_data:
        valid_modes = {"immediate", "5min", "hourly", "daily"}
        if update_data["digest_mode"] not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"digest_mode must be one of: {', '.join(valid_modes)}",
            )

    for field in ("quiet_hours_start", "quiet_hours_end"):
        if field in update_data and update_data[field]:
            try:
                parts = update_data[field].split(":")
                update_data[field] = dt_time(int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                raise HTTPException(status_code=400, detail=f"{field} must be HH:MM format")

    for key, value in update_data.items():
        setattr(prefs, key, value)

    await db.commit()
    await db.refresh(prefs)

    return NotificationPrefsResponse(
        email_enabled=prefs.email_enabled,
        email_on_mention=prefs.email_on_mention,
        email_on_agent_error=prefs.email_on_agent_error,
        email_on_task_complete=prefs.email_on_task_complete,
        digest_mode=prefs.digest_mode,
        digest_window_minutes=prefs.digest_window_minutes,
        quiet_hours_enabled=prefs.quiet_hours_enabled,
        quiet_hours_start=str(prefs.quiet_hours_start) if prefs.quiet_hours_start else None,
        quiet_hours_end=str(prefs.quiet_hours_end) if prefs.quiet_hours_end else None,
        timezone=prefs.timezone,
        max_emails_per_hour=prefs.max_emails_per_hour,
    )
