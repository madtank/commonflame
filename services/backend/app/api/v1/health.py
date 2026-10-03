"""Health endpoints."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.models.agent import Agent
from app.core.config import get_settings

router = APIRouter(prefix="/api/v1", tags=["health"])
settings = get_settings()


@router.get("/health")
async def basic_health_check() -> dict[str, Any]:
    return {"status": "healthy"}


@router.get("/health/detailed")
async def detailed_health_check(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    app_start_time = getattr(request.app.state, "app_start_time", None)
    if app_start_time is None:
        raise HTTPException(status_code=503, detail="App start time unavailable")

    if app_start_time.tzinfo is None:
        app_start_time = app_start_time.replace(tzinfo=timezone.utc)

    uptime_seconds = max((datetime.now(timezone.utc) - app_start_time).total_seconds(), 0.0)
    agent_count = await db.scalar(
        select(func.count()).select_from(Agent).where(Agent.status == "active")
    )

    return {
        "uptime_seconds": uptime_seconds,
        "version": settings.app_version,
        "agent_count": agent_count or 0,
    }
