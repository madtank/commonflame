"""
Workspace Intelligence API - The Vault Endpoints

Provides REST API for the Workspace Intelligence Vault:
- Promote ephemeral Redis context to permanent storage
- List and filter intelligence artifacts
- Retrieve artifacts with version history
- Delete artifacts

All endpoints enforce strict multi-tenant isolation via space_id.
"""

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import verify_space_actor_access
from app.core.jwt_verify import get_user_from_jwt_or_mcp
from app.core.rls import SecureSession, get_secure_session
from app.models.space import Space
from app.models.user import User
from app.models.workspace_intelligence import ArtifactType
from app.services.workspace_intelligence_service import WorkspaceIntelligenceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/spaces", tags=["workspace-intelligence"])


# --- Request/Response Models ---

class PromoteIntelligenceRequest(BaseModel):
    """Request body for promoting ephemeral context to the Vault."""
    key: str = Field(
        ...,
        description="The Redis context key to promote (e.g., 'research:mcp-oauth:2026-01-01')",
        min_length=1,
        max_length=255,
    )
    agent_id: Optional[str] = Field(
        None,
        description="Optional agent handle override",
        max_length=100,
    )
    artifact_type: Optional[str] = Field(
        None,
        description="Artifact type override (RESEARCH, CONVERSATION_INSIGHT, TASK_STATE, SYSTEM_VALIDATION)",
    )


class PromoteIntelligenceResponse(BaseModel):
    """Response after promoting to the Vault."""
    status: str  # "created" or "updated"
    id: str
    key: str
    space_id: str
    agent_id: str
    artifact_type: str
    version: int
    previous_version: Optional[int] = None
    access_url: str


class IntelligenceListItem(BaseModel):
    """Summary view of an intelligence artifact for listings."""
    id: str
    agent_id: str
    key: str
    artifact_type: str
    summary_snippet: Optional[str] = None
    version: int
    created_at: Optional[str] = None
    access_count: int


class IntelligenceListResponse(BaseModel):
    """Paginated list of intelligence artifacts."""
    items: list[IntelligenceListItem]
    total: int
    limit: int
    offset: int
    has_more: bool
    filters_applied: dict


class IntelligenceDetailResponse(BaseModel):
    """Full detail view of an intelligence artifact."""
    id: str
    space_id: str
    agent_id: str
    key: str
    artifact_type: str
    payload: Any  # Can be dict or string (for double-serialized data)
    summary_snippet: Optional[str] = None
    metadata: Optional[dict] = None
    version: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    access_count: int


class HistoryItem(BaseModel):
    """A historical version of an artifact."""
    id: str
    intelligence_id: str
    version: int
    artifact_type: str
    payload: dict
    summary_snippet: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: Optional[str] = None
    archived_at: Optional[str] = None


class DeleteIntelligenceResponse(BaseModel):
    """Response after deleting an artifact."""
    status: str
    key: str
    deleted: bool


# --- Helper Functions ---

async def validate_space_access(
    space_id: UUID,
    user: User,
    db: AsyncSession,
    *,
    is_agent: bool = False,
    agent_id: str | None = None,
) -> Space:
    """
    Validate strict multi-tenant access to the specified space.

    Security Boundary: No cross-space leakage allowed.
    """
    from sqlalchemy import select

    # Get the organization
    result = await db.execute(
        select(Space).where(Space.id == space_id)
    )
    org = result.scalar_one_or_none()

    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Space not found: {space_id}",
        )

    await verify_space_actor_access(
        db,
        user_id=user.id,
        space_id=space_id,
        is_agent=is_agent,
        agent_id=agent_id,
    )
    return org


# --- API Endpoints ---

@router.post(
    "/{space_id}/intelligence/promote",
    response_model=PromoteIntelligenceResponse,
    summary="Promote ephemeral context to the Vault",
    description="""
    Promotes an ephemeral Redis context entry to permanent storage in the
    Workspace Intelligence Vault.

    **Atomic Upsert with Versioning:**
    - If the key already exists, the current version is archived to history
    - Version is incremented automatically
    - Access count is reset on update

    **Returns:**
    - The permanent UUID for the artifact
    - The access_url for direct retrieval
    - Version information
    """,
)
async def promote_intelligence(
    space_id: UUID,
    request: PromoteIntelligenceRequest,
    current_user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Promote ephemeral Redis context to the Workspace Intelligence Vault."""
    await validate_space_access(
        space_id,
        current_user,
        session.db,
        is_agent=session.is_agent,
        agent_id=session.agent_id,
    )

    service = WorkspaceIntelligenceService(session.db)

    try:
        # Convert artifact_type string to enum if provided
        artifact_type = None
        if request.artifact_type:
            try:
                artifact_type = ArtifactType(request.artifact_type)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid artifact_type. Must be one of: {[t.value for t in ArtifactType]}",
                )

        result = await service.promote_intelligence(
            space_id=space_id,
            key=request.key,
            agent_id=request.agent_id,
            artifact_type=artifact_type,
        )
        return PromoteIntelligenceResponse(**result)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "/{space_id}/intelligence",
    response_model=IntelligenceListResponse,
    summary="List intelligence artifacts with deep filtering",
    description="""
    Returns a paginated list of intelligence artifacts for the space.

    **Filtering Options (for Sentinel):**
    - `artifact_type`: Filter by type (RESEARCH, CONVERSATION_INSIGHT, TASK_STATE, SYSTEM_VALIDATION)
    - `agent_id`: Filter by originating agent
    - `date_from` / `date_to`: Filter by creation date range

    Results are sorted by creation date (newest first).
    """,
)
async def list_intelligence(
    space_id: UUID,
    limit: int = Query(50, ge=1, le=100, description="Maximum entries to return"),
    offset: int = Query(0, ge=0, description="Number of entries to skip"),
    artifact_type: Optional[str] = Query(None, description="Filter by artifact type"),
    agent_id: Optional[str] = Query(None, description="Filter by agent handle"),
    date_from: Optional[datetime] = Query(None, description="Filter by created_at >= date"),
    date_to: Optional[datetime] = Query(None, description="Filter by created_at <= date"),
    current_user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """List intelligence artifacts with deep filtering."""
    await validate_space_access(
        space_id,
        current_user,
        session.db,
        is_agent=session.is_agent,
        agent_id=session.agent_id,
    )

    service = WorkspaceIntelligenceService(session.db)

    # Validate artifact_type if provided
    resolved_type = None
    if artifact_type:
        try:
            resolved_type = ArtifactType(artifact_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid artifact_type. Must be one of: {[t.value for t in ArtifactType]}",
            )

    result = await service.list_intelligence(
        space_id=space_id,
        limit=limit,
        offset=offset,
        artifact_type=resolved_type,
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
    )

    return IntelligenceListResponse(
        items=[IntelligenceListItem(**item) for item in result["items"]],
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
        has_more=result["has_more"],
        filters_applied=result["filters_applied"],
    )


@router.get(
    "/{space_id}/intelligence/{key:path}",
    response_model=IntelligenceDetailResponse,
    summary="Get a specific intelligence artifact",
    description="""
    Retrieves a specific intelligence artifact by key.

    **Access Tracking:**
    - Increments access_count for popularity metrics
    - Logs access in metadata for analytics
    """,
)
async def get_intelligence(
    space_id: UUID,
    key: str,
    current_user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Get a specific intelligence artifact."""
    await validate_space_access(
        space_id,
        current_user,
        session.db,
        is_agent=session.is_agent,
        agent_id=session.agent_id,
    )

    service = WorkspaceIntelligenceService(session.db)

    result = await service.get_intelligence(
        space_id=space_id,
        key_or_id=key,
        increment_access=True,
        log_access=False,  # Disabled to prevent write amplification - access_count is sufficient
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Intelligence artifact not found: {key}",
        )

    return IntelligenceDetailResponse(**result)


@router.get(
    "/{space_id}/intelligence/{key:path}/history",
    response_model=list[HistoryItem],
    summary="Get version history for an artifact",
    description="""
    Returns the version history for an intelligence artifact.

    Previous versions are archived when the artifact is updated.
    History is sorted by version number (descending).
    """,
)
async def get_intelligence_history(
    space_id: UUID,
    key: str,
    limit: int = Query(20, ge=1, le=50, description="Maximum versions to return"),
    current_user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Get version history for an artifact."""
    await validate_space_access(
        space_id,
        current_user,
        session.db,
        is_agent=session.is_agent,
        agent_id=session.agent_id,
    )

    service = WorkspaceIntelligenceService(session.db)

    history = await service.get_artifact_history(
        space_id=space_id,
        key=key,
        limit=limit,
    )

    return [HistoryItem(**item) for item in history]


@router.delete(
    "/{space_id}/intelligence/{key:path}",
    response_model=DeleteIntelligenceResponse,
    summary="Delete an intelligence artifact",
    description="""
    Permanently deletes an intelligence artifact and all its version history.

    **Warning:** This is irreversible.
    """,
)
async def delete_intelligence(
    space_id: UUID,
    key: str,
    current_user: User = Depends(get_user_from_jwt_or_mcp),
    session: SecureSession = Depends(get_secure_session),
):
    """Delete an intelligence artifact."""
    await validate_space_access(
        space_id,
        current_user,
        session.db,
        is_agent=session.is_agent,
        agent_id=session.agent_id,
    )

    service = WorkspaceIntelligenceService(session.db)

    deleted = await service.delete_intelligence(
        space_id=space_id,
        key_or_id=key,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Intelligence artifact not found: {key}",
        )

    return DeleteIntelligenceResponse(
        status="deleted",
        key=key,
        deleted=True,
    )
