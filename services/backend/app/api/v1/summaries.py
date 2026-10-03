"""Summary endpoints for agents, users, and space agent listings."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.database import get_db_session
from ...core.rls import SecureSession, get_secure_session
from ...models.user import User
from ...services.roster_service import RosterService
from ...models.message import Message
from ...core.jwt_verify import get_current_user_from_token

router = APIRouter(prefix="/api", tags=["summaries"])

class OwnerRef(BaseModel):
    id: Optional[str]
    name: Optional[str]
    handle: Optional[str]
    avatar: Optional[str] = None

class AgentSummary(BaseModel):
    id: str
    name: str
    avatar: Optional[str] = None
    owner: Optional[OwnerRef] = None
    last_active_at: Optional[str] = None
    status: Optional[str] = None
    visibility: Optional[str] = None

class UserSummary(BaseModel):
    id: str
    name: str
    handle: Optional[str] = None
    avatar: Optional[str] = None
    last_active_at: Optional[str] = None

class AgentListResponse(BaseModel):
    items: List[AgentSummary]
    next_page: Optional[int] = None

def _normalize_handle(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    if value.startswith("@"):  # drop leading @ for consistency
        return value[1:]
    return value

def _presence_to_summary(presence: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    if not presence:
        return {"last_active_at": None, "status": None}

    last_active = presence.get("last_active")
    status_hint = presence.get("status")
    status: Optional[str] = None
    if status_hint in {"active", "recent"}:
        status = "online"
    elif status_hint:
        status = "offline"

    return {
        "last_active_at": last_active,
        "status": status,
    }

async def _fetch_roster_entries(
    db: AsyncSession,
    space_id: UUID,
    viewer_id: UUID,
    *,
    entry_type: Optional[str] = None,
    limit: int = 500,
    offset: int = 0
) -> List[Dict[str, Any]]:
    service = RosterService(db, redis_client=None)
    await service.ensure_membership(space_id, viewer_id)

    roster_payload = await service.get_roster(
        space_id,
        viewer_id,
        entry_type=entry_type,
        limit=limit,
        offset=offset,
    )
    return roster_payload.get("items", [])

def _summarize_agent_entry(
    entry: Dict[str, Any]
) -> AgentSummary:
    owner = entry.get("owner_user") or {}
    presence_summary = _presence_to_summary(entry.get("presence"))
    return AgentSummary(
        id=entry["id"],
        name=entry.get("display_name") or entry.get("handle") or entry["id"],
        avatar=entry.get("avatar_url"),
        owner=OwnerRef(
            id=owner.get("id"),
            name=owner.get("display_name"),
            handle=_normalize_handle(owner.get("handle")),
            avatar=owner.get("avatar_url"),
        ) if owner else None,
        last_active_at=presence_summary["last_active_at"],
        status=presence_summary["status"],
        visibility=entry.get("visibility"),
    )

def _summarize_user_entry(entry: Dict[str, Any]) -> UserSummary:
    presence_summary = _presence_to_summary(entry.get("presence"))
    return UserSummary(
        id=entry["id"],
        name=entry.get("display_name") or entry.get("handle") or entry["id"],
        handle=_normalize_handle(entry.get("handle")),
        avatar=entry.get("avatar_url"),
        last_active_at=presence_summary["last_active_at"],
    )

def _parse_uuid(value: str, field: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid {field}") from None

@router.get("/agents/{agent_id}/summary", response_model=AgentSummary)
async def get_agent_summary(
    agent_id: str,
    space_id: str = Query(..., description="Workspace identifier"),
    session: SecureSession = Depends(get_secure_session)
):
    space_uuid = _parse_uuid(space_id, "space_id")
    agent_uuid = _parse_uuid(agent_id, "agent_id")
    viewer_uuid = UUID(str(session.user.id))

    # Fetch from roster data (read-only, cached per viewer)
    entries = await _fetch_roster_entries(session.db, space_uuid, viewer_uuid, entry_type="agent", limit=500)
    entry = next((e for e in entries if e.get("id") == str(agent_uuid)), None)

    if entry is None:
        # Fallback: rebuild and search (avoids pagination misses)
        service = RosterService(session.db, redis_client=None)
        await service.ensure_membership(space_uuid, viewer_uuid)
        all_entries = await service._build_roster(space_uuid)  # type: ignore[attr-defined]
        entry = next((e for e in all_entries if e.get("id") == str(agent_uuid) and e.get("type") == "agent"), None)

    if entry is None:
        raise HTTPException(status_code=404, detail="Agent not found in this space")

    visibility = entry.get("visibility") or "public"
    owner = entry.get("owner_user") or {}
    owner_id = owner.get("id")

    if visibility == "private" and owner_id and owner_id != str(session.user.id):
        # Return privacy stub
        return AgentSummary(
            id=str(agent_uuid),
            name="Private agent",
            avatar=None,
            owner=None,
            last_active_at=None,
            status=None,
            visibility="private",
        )

    summary = _summarize_agent_entry(entry)
    # Ensure visibility is always surfaced
    summary.visibility = visibility
    return summary

@router.get("/users/{user_id}/summary", response_model=UserSummary)
async def get_user_summary(
    user_id: str,
    space_id: str = Query(..., description="Workspace identifier"),
    session: SecureSession = Depends(get_secure_session)
):
    space_uuid = _parse_uuid(space_id, "space_id")
    target_user_uuid = _parse_uuid(user_id, "user_id")
    viewer_uuid = UUID(str(session.user.id))

    entries = await _fetch_roster_entries(session.db, space_uuid, viewer_uuid, entry_type="human", limit=500)
    entry = next((e for e in entries if e.get("id") == str(target_user_uuid)), None)

    if entry is None:
        service = RosterService(session.db, redis_client=None)
        await service.ensure_membership(space_uuid, viewer_uuid)
        all_entries = await service._build_roster(space_uuid)  # type: ignore[attr-defined]
        entry = next((e for e in all_entries if e.get("id") == str(target_user_uuid) and e.get("type") == "human"), None)

    if entry is None:
        raise HTTPException(status_code=404, detail="User not found in this space")

    return _summarize_user_entry(entry)

@router.get("/spaces/{space_id}/agents", response_model=AgentListResponse)
async def list_space_agents(
    space_id: str,
    scope: str = Query("others", regex="^(others|all)$"),
    q: Optional[str] = Query(None, description="Search query"),
    status: Optional[str] = Query(None, regex="^(online|offline)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=50),
    session: SecureSession = Depends(get_secure_session)
):
    space_uuid = _parse_uuid(space_id, "space_id")
    viewer_uuid = UUID(str(session.user.id))

    entries = await _fetch_roster_entries(session.db, space_uuid, viewer_uuid, entry_type="agent", limit=1000)

    viewer_id_str = str(session.user.id)
    filtered: List[AgentSummary] = []
    for entry in entries:
        owner = entry.get("owner_user") or {}
        owner_id = owner.get("id")
        visibility = entry.get("visibility") or "public"

        if scope == "others" and owner_id == viewer_id_str:
            continue
        if visibility == "private" and owner_id != viewer_id_str:
            continue

        summary = _summarize_agent_entry(entry)
        summary.visibility = visibility

        if q:
            query_lower = q.lower()
            name_match = summary.name.lower().find(query_lower) != -1
            handle_match = summary.owner and summary.owner.handle and summary.owner.handle.lower().find(query_lower) != -1
            if not (name_match or handle_match):
                continue

        if status and summary.status and summary.status != status:
            continue
        if status and summary.status is None:
            continue

        filtered.append(summary)

    # Simple pagination (in-memory slice)
    start = (page - 1) * page_size
    end = start + page_size
    sliced = filtered[start:end]
    next_page = page + 1 if end < len(filtered) else None

    return AgentListResponse(items=sliced, next_page=next_page)
