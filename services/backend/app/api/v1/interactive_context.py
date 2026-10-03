"""Context Catalog API routes for governed interactive context artifacts."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.authorization import verify_space_actor_access
from app.core.database import set_rls_context
from app.core.interactive_context_actions import ActionPayloadValidationError, UnknownActionError
from app.core.rls import SecureSession, get_secure_session
from app.services.interactive_context_service import (
    ContextCatalogInvalidRequest,
    ContextCatalogNotFound,
    InteractiveContextService,
)
from app.services.redis_sse_broker import redis_sse_broker
from app.services.workspace_intelligence_service import WorkspaceIntelligenceService

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/spaces/{space_id}/context-catalog",
    tags=["context-catalog"],
)


class CreateContextCatalogRequest(BaseModel):
    artifact_type: str = Field(..., min_length=1, max_length=100)
    title: str = Field(..., min_length=1, max_length=200)
    artifact_kind: str = Field(..., min_length=1, max_length=80)
    action_set_id: str = Field(..., min_length=1, max_length=100)
    content_ref: str | None = None
    attachment_id: UUID | None = None
    sha256: str = Field(..., min_length=64, max_length=64)
    size_bytes: int = Field(..., ge=0)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    owner_id: str | None = Field(default=None, max_length=100)
    shelf: str | None = Field(default=None, max_length=80)
    pinned: bool = False
    description: str | None = None
    sandbox_policy: dict[str, Any] = Field(default_factory=dict)


class CreateCatalogFromContextRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=255)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    artifact_type: str | None = Field(default=None, min_length=1, max_length=100)
    artifact_kind: str | None = Field(default=None, min_length=1, max_length=80)
    action_set_id: str = Field(default="review.basic", min_length=1, max_length=100)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    owner_id: str | None = Field(default=None, max_length=100)
    shelf: str | None = Field(default=None, max_length=80)
    pinned: bool = False
    description: str | None = None
    sandbox_policy: dict[str, Any] = Field(default_factory=dict)


class ActionInvocationRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=100)
    base_artifact_version_id: UUID
    base_state_version_id: UUID
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    source_lane: str | None = Field(default=None, max_length=40)


class PatchProposalRequest(BaseModel):
    base_artifact_version_id: UUID
    base_state_version_id: UUID
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    patch_type: str = Field(..., min_length=1, max_length=80)
    payload: dict[str, Any]
    content_hash: str = Field(..., min_length=64, max_length=64)


async def _authorize_context_catalog_space(space_id: UUID, session: SecureSession) -> None:
    await verify_space_actor_access(
        session.db,
        user_id=session.user.id,
        space_id=space_id,
        is_agent=session.is_agent,
        agent_id=session.agent_id,
    )
    await set_rls_context(
        session.db,
        user_id=str(session.user.id),
        space_id=str(space_id),
        agent_id=session.agent_id,
    )


def _actor_from_session(session: SecureSession) -> SimpleNamespace:
    if session.is_agent and session.agent_id:
        return SimpleNamespace(type="agent", id=str(session.agent_id))
    return SimpleNamespace(type="human", id=str(session.user.id))


def _actor_ref(actor: SimpleNamespace) -> str:
    return f"{actor.type}:{actor.id}"


def _raise_for_service_error(exc: Exception) -> None:
    if isinstance(exc, ContextCatalogNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (ActionPayloadValidationError, ContextCatalogInvalidRequest, UnknownActionError)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise exc


async def _publish_catalog_event(space_id: UUID, event: str, payload: dict[str, Any]) -> None:
    try:
        await redis_sse_broker.publish(
            f"{space_id}:context",
            event,
            {
                "event": event,
                **payload,
            },
        )
    except Exception as exc:
        logger.warning("Failed to publish context catalog SSE event=%s space=%s: %s", event, space_id, exc)


async def _raise_conflict_with_event(space_id: UUID, catalog_entry_id: UUID, result: dict[str, Any]) -> None:
    if result.get("ok") is False and result.get("error") in {
        "stale_base_version",
        "idempotency_payload_mismatch",
    }:
        await _publish_catalog_event(
            space_id,
            "context_catalog_conflict",
            {
                "catalog_entry_id": str(catalog_entry_id),
                "error": result.get("error"),
                "current_artifact_version_id": result.get("current_artifact_version_id"),
                "current_state_version_id": result.get("current_state_version_id"),
            },
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result)


@router.post("")
async def create_catalog_entry(
    space_id: UUID,
    request: CreateContextCatalogRequest,
    session: SecureSession = Depends(get_secure_session),
) -> dict[str, Any]:
    await _authorize_context_catalog_space(space_id, session)
    actor = _actor_from_session(session)
    service = InteractiveContextService(session.db)
    try:
        result = await service.create_catalog_entry(
            space_id=space_id,
            artifact_type=request.artifact_type,
            title=request.title,
            artifact_kind=request.artifact_kind,
            action_set_id=request.action_set_id,
            content_ref=request.content_ref,
            attachment_id=request.attachment_id,
            sha256=request.sha256,
            size_bytes=request.size_bytes,
            initial_state=request.initial_state,
            created_by=_actor_ref(actor),
            owner_id=request.owner_id or _actor_ref(actor),
            shelf=request.shelf,
            pinned=request.pinned,
            description=request.description,
            sandbox_policy=request.sandbox_policy,
        )
        await _publish_catalog_event(
            space_id,
            "context_catalog_created",
            {
                "catalog_entry_id": result["id"],
                "artifact_type": result.get("artifact_type"),
                "title": result.get("title"),
                "current_artifact_version_id": result.get("current_artifact_version_id"),
                "current_state_version_id": result.get("current_state_version_id"),
            },
        )
        return result
    except Exception as exc:
        _raise_for_service_error(exc)
        raise


@router.post("/from-context")
async def create_catalog_entry_from_context(
    space_id: UUID,
    request: CreateCatalogFromContextRequest,
    session: SecureSession = Depends(get_secure_session),
) -> dict[str, Any]:
    await _authorize_context_catalog_space(space_id, session)
    context_record = await WorkspaceIntelligenceService(session.db).get_with_fallback(
        space_id=space_id,
        key=request.key,
    )
    if context_record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Context key not found: {request.key}")

    actor = _actor_from_session(session)
    service = InteractiveContextService(session.db)
    try:
        result = await service.create_catalog_entry_from_context(
            space_id=space_id,
            key=request.key,
            context_record=context_record,
            created_by=_actor_ref(actor),
            title=request.title,
            artifact_type=request.artifact_type,
            artifact_kind=request.artifact_kind,
            action_set_id=request.action_set_id,
            initial_state=request.initial_state,
            owner_id=request.owner_id or _actor_ref(actor),
            shelf=request.shelf,
            pinned=request.pinned,
            description=request.description,
            sandbox_policy=request.sandbox_policy,
        )
        await _publish_catalog_event(
            space_id,
            "context_catalog_created",
            {
                "catalog_entry_id": result["id"],
                "artifact_type": result.get("artifact_type"),
                "title": result.get("title"),
                "current_artifact_version_id": result.get("current_artifact_version_id"),
                "current_state_version_id": result.get("current_state_version_id"),
                "source_context_key": request.key,
            },
        )
        return result
    except Exception as exc:
        _raise_for_service_error(exc)
        raise


@router.get("")
async def list_catalog_entries(
    space_id: UUID,
    session: SecureSession = Depends(get_secure_session),
    include_content: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    artifact_type: str | None = None,
    shelf: str | None = None,
    pinned: bool | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    await _authorize_context_catalog_space(space_id, session)
    service = InteractiveContextService(session.db)
    try:
        result = await service.list_catalog_entries(
            space_id=space_id,
            limit=limit,
            offset=offset,
            artifact_type=artifact_type,
            shelf=shelf,
            pinned=pinned,
            status=status_filter,
            owner_id=owner_id,
        )
        if include_content:
            result["items"] = [
                await service.get_catalog_entry(
                    space_id=space_id,
                    catalog_entry_id=UUID(item["id"]),
                    include_content=True,
                )
                for item in result["items"]
            ]
        return result
    except Exception as exc:
        _raise_for_service_error(exc)
        raise


@router.get("/{catalog_entry_id}")
async def get_catalog_entry(
    space_id: UUID,
    catalog_entry_id: UUID,
    include_content: bool = Query(False),
    session: SecureSession = Depends(get_secure_session),
) -> dict[str, Any]:
    await _authorize_context_catalog_space(space_id, session)
    service = InteractiveContextService(session.db)
    try:
        return await service.get_catalog_entry(
            space_id=space_id,
            catalog_entry_id=catalog_entry_id,
            include_content=include_content,
        )
    except Exception as exc:
        _raise_for_service_error(exc)
        raise


@router.post("/{catalog_entry_id}/actions")
async def invoke_action(
    space_id: UUID,
    catalog_entry_id: UUID,
    request: ActionInvocationRequest,
    session: SecureSession = Depends(get_secure_session),
) -> dict[str, Any]:
    await _authorize_context_catalog_space(space_id, session)
    service = InteractiveContextService(session.db)
    try:
        result = await service.invoke_action(
            space_id=space_id,
            catalog_entry_id=catalog_entry_id,
            action_id=request.action_id,
            base_artifact_version_id=request.base_artifact_version_id,
            base_state_version_id=request.base_state_version_id,
            idempotency_key=request.idempotency_key,
            payload=request.payload,
            actor=_actor_from_session(session),
            source_lane=request.source_lane,
        )
        await _raise_conflict_with_event(space_id, catalog_entry_id, result)
        await _publish_catalog_event(
            space_id,
            "context_catalog_action_committed",
            {
                "catalog_entry_id": str(catalog_entry_id),
                "audit_event_id": result.get("audit_event_id"),
                "action_id": request.action_id,
                "new_artifact_version_id": result.get("new_artifact_version_id"),
                "new_state_version_id": result.get("new_state_version_id"),
                "status": result.get("status"),
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _raise_for_service_error(exc)
        raise


@router.post("/{catalog_entry_id}/patches")
async def propose_patch(
    space_id: UUID,
    catalog_entry_id: UUID,
    request: PatchProposalRequest,
    session: SecureSession = Depends(get_secure_session),
) -> dict[str, Any]:
    await _authorize_context_catalog_space(space_id, session)
    service = InteractiveContextService(session.db)
    try:
        result = await service.propose_patch(
            space_id=space_id,
            catalog_entry_id=catalog_entry_id,
            base_artifact_version_id=request.base_artifact_version_id,
            base_state_version_id=request.base_state_version_id,
            idempotency_key=request.idempotency_key,
            patch_type=request.patch_type,
            payload=request.payload,
            content_hash=request.content_hash,
            actor=_actor_from_session(session),
        )
        await _raise_conflict_with_event(space_id, catalog_entry_id, result)
        await _publish_catalog_event(
            space_id,
            "context_catalog_patch_proposed",
            {
                "catalog_entry_id": str(catalog_entry_id),
                "audit_event_id": result.get("audit_event_id"),
                "patch_id": result.get("patch_id"),
                "status": result.get("status"),
            },
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        _raise_for_service_error(exc)
        raise
