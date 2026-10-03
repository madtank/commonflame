"""
User Settings API — GET/PATCH /api/v1/settings

Provides per-user, per-org preferences. Settings are auto-created on first
GET with sensible defaults (no separate POST needed).

Uses SecureSession for RLS-ready auth + org scoping.
"""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ...core.rls import SecureSessionDep
from ...models.user_settings import UserSettings

router = APIRouter(prefix="/settings", tags=["settings"])


# --- Schemas ---

class UserSettingsResponse(BaseModel):
    """Response schema for user settings."""
    id: str
    email_notifications: bool
    mention_notifications: bool
    task_notifications: bool
    ai_suggestions_enabled: bool
    ai_auto_summarize: bool
    theme: str
    compact_mode: bool
    custom: dict

    class Config:
        from_attributes = True


class UserSettingsPatch(BaseModel):
    """Partial update schema — all fields optional."""
    email_notifications: Optional[bool] = None
    mention_notifications: Optional[bool] = None
    task_notifications: Optional[bool] = None
    ai_suggestions_enabled: Optional[bool] = None
    ai_auto_summarize: Optional[bool] = None
    theme: Optional[str] = Field(None, pattern=r"^(system|light|dark)$")
    compact_mode: Optional[bool] = None
    custom: Optional[dict] = None

    @field_validator("custom")
    @classmethod
    def custom_must_not_be_none(cls, v):
        """custom can be omitted (not sent) but cannot be explicitly null."""
        if v is None:
            raise ValueError("custom cannot be null; omit the field or send an object")
        return v


# --- Helpers ---

async def _get_or_create_settings(session) -> UserSettings:
    """
    Get existing settings or create defaults for user+org pair.
    Uses INSERT ... ON CONFLICT to avoid race conditions from concurrent requests.
    """
    result = await session.db.execute(
        select(UserSettings).where(
            UserSettings.user_id == session.user.id,
            UserSettings.space_id == session.space_id,
        )
    )
    settings = result.scalar_one_or_none()

    if settings is None:
        # Use ON CONFLICT DO NOTHING to handle concurrent inserts safely
        stmt = pg_insert(UserSettings).values(
            user_id=session.user.id,
            space_id=session.space_id,
        ).on_conflict_do_nothing(
            index_elements=["user_id", "space_id"]
        )
        await session.db.execute(stmt)
        await session.db.flush()

        # Re-fetch — either our insert or the concurrent one succeeded
        result = await session.db.execute(
            select(UserSettings).where(
                UserSettings.user_id == session.user.id,
                UserSettings.space_id == session.space_id,
            )
        )
        settings = result.scalar_one()

    return settings


# --- Endpoints ---

@router.get("", response_model=UserSettingsResponse)
async def get_settings(session: SecureSessionDep):
    """
    Get current user's settings for the active space.
    Creates default settings if none exist yet.
    """
    settings = await _get_or_create_settings(session)
    await session.db.commit()
    return UserSettingsResponse(
        id=str(settings.id),
        email_notifications=settings.email_notifications,
        mention_notifications=settings.mention_notifications,
        task_notifications=settings.task_notifications,
        ai_suggestions_enabled=settings.ai_suggestions_enabled,
        ai_auto_summarize=settings.ai_auto_summarize,
        theme=settings.theme,
        compact_mode=settings.compact_mode,
        custom=settings.custom or {},
    )


@router.patch("", response_model=UserSettingsResponse)
async def update_settings(patch: UserSettingsPatch, session: SecureSessionDep):
    """
    Partially update current user's settings.
    Only provided fields are updated; others remain unchanged.
    """
    settings = await _get_or_create_settings(session)

    update_data = patch.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    for field, value in update_data.items():
        setattr(settings, field, value)

    await session.db.commit()
    await session.db.refresh(settings)

    return UserSettingsResponse(
        id=str(settings.id),
        email_notifications=settings.email_notifications,
        mention_notifications=settings.mention_notifications,
        task_notifications=settings.task_notifications,
        ai_suggestions_enabled=settings.ai_suggestions_enabled,
        ai_auto_summarize=settings.ai_auto_summarize,
        theme=settings.theme,
        compact_mode=settings.compact_mode,
        custom=settings.custom or {},
    )
