"""Service layer for governed interactive context catalog objects."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.interactive_context_actions import (
    get_action_definition,
    list_action_definitions,
    serialize_host_action,
    validate_action_payload,
)
from app.models.interactive_context import (
    ContextArtifactVersion,
    ContextAuditEvent,
    ContextCatalogEntry,
    ContextObject,
    ContextPatch,
    ContextStateVersion,
)


class ContextCatalogNotFound(LookupError):
    """Raised when a context catalog object cannot be found in the requested space."""


class ContextCatalogInvalidRequest(ValueError):
    """Raised when a context catalog request is structurally invalid."""


@dataclass(frozen=True)
class _CurrentContext:
    entry: ContextCatalogEntry
    context_object: ContextObject
    artifact_version: ContextArtifactVersion
    state_version: ContextStateVersion


class InteractiveContextService:
    """Transport-agnostic operations for Context Catalog V1."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_catalog_entry(
        self,
        *,
        space_id: UUID,
        artifact_type: str,
        title: str,
        artifact_kind: str,
        action_set_id: str,
        content_ref: str | None,
        sha256: str,
        size_bytes: int,
        initial_state: dict[str, Any] | None = None,
        created_by: str | None = None,
        owner_id: str | None = None,
        shelf: str | None = None,
        pinned: bool = False,
        description: str | None = None,
        attachment_id: UUID | None = None,
        sandbox_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a catalog entry plus its initial object, artifact, and state versions."""
        self._validate_hash(sha256, field_name="sha256")
        list_action_definitions(action_set_id)

        catalog_entry_id = uuid4()
        context_object_id = uuid4()
        artifact_version_id = uuid4()
        state_version_id = uuid4()

        entry = ContextCatalogEntry(
            id=catalog_entry_id,
            space_id=space_id,
            artifact_type=artifact_type,
            title=title,
            description=description,
            owner_id=owner_id,
            shelf=shelf,
            pinned=pinned,
            status="active",
        )
        self.db.add(entry)
        await self.db.flush()

        context_object = ContextObject(
            id=context_object_id,
            catalog_entry_id=catalog_entry_id,
            space_id=space_id,
            kind="interactive_context",
            artifact_kind=artifact_kind,
            action_set_id=action_set_id,
            created_by=created_by,
        )
        artifact_version = ContextArtifactVersion(
            id=artifact_version_id,
            catalog_entry_id=catalog_entry_id,
            context_object_id=context_object_id,
            space_id=space_id,
            kind=artifact_kind,
            content_ref=content_ref,
            attachment_id=attachment_id,
            sha256=sha256,
            size_bytes=size_bytes,
            created_by=created_by,
            sandbox_policy=sandbox_policy or {},
        )
        state_version = ContextStateVersion(
            id=state_version_id,
            catalog_entry_id=catalog_entry_id,
            context_object_id=context_object_id,
            space_id=space_id,
            version_number=1,
            data=initial_state or {},
            created_by=created_by,
        )
        self.db.add(context_object)
        self.db.add(artifact_version)
        self.db.add(state_version)
        await self.db.flush()

        entry.current_context_object_id = context_object_id
        entry.current_artifact_version_id = artifact_version_id
        entry.current_state_version_id = state_version_id

        await self.db.commit()
        await self.db.refresh(entry)

        return self._serialize_catalog_metadata(
            entry,
            available_actions=self._available_actions(action_set_id),
        )

    async def create_catalog_entry_from_context(
        self,
        *,
        space_id: UUID,
        key: str,
        context_record: dict[str, Any],
        created_by: str | None,
        title: str | None = None,
        artifact_type: str | None = None,
        artifact_kind: str | None = None,
        action_set_id: str = "review.basic",
        initial_state: dict[str, Any] | None = None,
        owner_id: str | None = None,
        shelf: str | None = None,
        pinned: bool = False,
        description: str | None = None,
        sandbox_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bridge an existing Redis/Vault context object into the governed catalog."""
        payload, wrapper = self._unwrap_context_payload(context_record.get("value"))
        source = str(context_record.get("source") or "context")
        resolved_kind = artifact_kind or self._infer_artifact_kind(payload)
        resolved_type = artifact_type or f"review.{resolved_kind}"
        canonical = self._canonical_bytes(payload)

        state = {
            "review_status": "pending",
            "annotations": [],
            "source_context_key": key,
            "source": source,
        }
        if initial_state:
            state.update(initial_state)

        return await self.create_catalog_entry(
            space_id=space_id,
            artifact_type=resolved_type,
            title=title or self._infer_title(key, payload, wrapper),
            artifact_kind=resolved_kind,
            action_set_id=action_set_id,
            content_ref=f"context://{source}/{space_id}/{key}",
            sha256=hashlib.sha256(canonical).hexdigest(),
            size_bytes=len(canonical),
            initial_state=state,
            created_by=created_by,
            owner_id=owner_id or created_by,
            shelf=shelf,
            pinned=pinned,
            description=description,
            sandbox_policy=sandbox_policy or self._default_sandbox_policy(resolved_kind),
        )

    async def list_catalog_entries(
        self,
        *,
        space_id: UUID,
        limit: int = 50,
        offset: int = 0,
        artifact_type: str | None = None,
        shelf: str | None = None,
        pinned: bool | None = None,
        status: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """List catalog entries as metadata only."""
        conditions = [ContextCatalogEntry.space_id == space_id]
        if artifact_type:
            conditions.append(ContextCatalogEntry.artifact_type == artifact_type)
        if shelf:
            conditions.append(ContextCatalogEntry.shelf == shelf)
        if pinned is not None:
            conditions.append(ContextCatalogEntry.pinned == pinned)
        if status:
            conditions.append(ContextCatalogEntry.status == status)
        if owner_id:
            conditions.append(ContextCatalogEntry.owner_id == owner_id)

        limit = min(max(1, limit), 100)
        base_query = select(ContextCatalogEntry).where(and_(*conditions))
        count_stmt = select(func.count()).select_from(base_query.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = base_query.order_by(ContextCatalogEntry.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        return {
            "items": [self._serialize_catalog_metadata(entry) for entry in entries],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(entries) < total,
        }

    async def get_catalog_entry(
        self,
        *,
        space_id: UUID,
        catalog_entry_id: UUID,
        include_content: bool = False,
    ) -> dict[str, Any]:
        current = await self._load_current(space_id=space_id, catalog_entry_id=catalog_entry_id)
        return self._serialize_catalog_detail(current, include_content=include_content)

    async def invoke_action(
        self,
        *,
        space_id: UUID,
        catalog_entry_id: UUID,
        action_id: str,
        base_artifact_version_id: UUID,
        base_state_version_id: UUID,
        idempotency_key: str,
        payload: dict[str, Any] | None,
        actor: SimpleNamespace,
        source_lane: str | None = None,
    ) -> dict[str, Any]:
        current = await self._load_current(space_id=space_id, catalog_entry_id=catalog_entry_id, lock_entry=True)
        action = get_action_definition(current.context_object.action_set_id, action_id)
        requested_source_lane = source_lane or action.source_lane
        if requested_source_lane != action.source_lane:
            raise ContextCatalogInvalidRequest(
                f"Action {current.context_object.action_set_id}.{action_id} must use source lane {action.source_lane}"
            )

        validated_payload = validate_action_payload(
            current.context_object.action_set_id,
            action_id,
            payload or {},
        )
        payload_hash = self._payload_hash(validated_payload)
        actor_type, actor_id = self._actor_parts(actor)

        existing_audit = await self._get_idempotent_audit(
            context_object_id=current.context_object.id,
            actor_type=actor_type,
            actor_id=actor_id,
            action_id=action_id,
            idempotency_key=idempotency_key,
        )
        if existing_audit is not None:
            if existing_audit.payload_hash != payload_hash:
                return self._idempotency_payload_mismatch(existing_audit)
            return self._serialize_idempotent_action_result(existing_audit)

        conflict = self._version_conflict(
            current=current,
            base_artifact_version_id=base_artifact_version_id,
            base_state_version_id=base_state_version_id,
        )
        if conflict is not None:
            return conflict

        new_state_data, status = self._apply_action_state_transition(
            action_id=action_id,
            current_state=current.state_version.data or {},
            payload=validated_payload,
        )
        new_state = ContextStateVersion(
            id=uuid4(),
            catalog_entry_id=current.entry.id,
            context_object_id=current.context_object.id,
            space_id=space_id,
            version_number=(current.state_version.version_number or 0) + 1,
            data=new_state_data,
            created_by=actor_id,
        )
        self.db.add(new_state)
        await self.db.flush()

        current.entry.current_state_version_id = new_state.id
        audit = ContextAuditEvent(
            id=uuid4(),
            catalog_entry_id=current.entry.id,
            context_object_id=current.context_object.id,
            space_id=space_id,
            action_set_id=current.context_object.action_set_id,
            action_id=action_id,
            source_lane=action.source_lane,
            actor_type=actor_type,
            actor_id=actor_id,
            policy_decision="allowed",
            base_artifact_version_id=current.artifact_version.id,
            base_state_version_id=current.state_version.id,
            result_artifact_version_id=None,
            result_state_version_id=new_state.id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            payload=validated_payload,
        )
        self.db.add(audit)
        await self.db.commit()

        return {
            "ok": True,
            "audit_event_id": str(audit.id),
            "new_state_version_id": str(new_state.id),
            "new_artifact_version_id": None,
            "status": status,
        }

    async def propose_patch(
        self,
        *,
        space_id: UUID,
        catalog_entry_id: UUID,
        base_artifact_version_id: UUID,
        base_state_version_id: UUID,
        idempotency_key: str,
        patch_type: str,
        payload: dict[str, Any],
        content_hash: str,
        actor: SimpleNamespace,
    ) -> dict[str, Any]:
        current = await self._load_current(space_id=space_id, catalog_entry_id=catalog_entry_id, lock_entry=True)
        actor_type, actor_id = self._actor_parts(actor)
        audit_payload = {"patch_type": patch_type, "payload": payload, "content_hash": content_hash}
        payload_hash = self._payload_hash(audit_payload)
        existing_audit = await self._get_idempotent_audit(
            context_object_id=current.context_object.id,
            actor_type=actor_type,
            actor_id=actor_id,
            action_id="propose",
            idempotency_key=idempotency_key,
        )
        if existing_audit is not None:
            if existing_audit.payload_hash != payload_hash:
                return self._idempotency_payload_mismatch(existing_audit)
            return self._serialize_idempotent_patch_result(existing_audit)

        conflict = self._version_conflict(
            current=current,
            base_artifact_version_id=base_artifact_version_id,
            base_state_version_id=base_state_version_id,
        )
        if conflict is not None:
            return conflict

        self._validate_hash(content_hash, field_name="content_hash")
        patch = ContextPatch(
            id=uuid4(),
            catalog_entry_id=current.entry.id,
            context_object_id=current.context_object.id,
            space_id=space_id,
            base_artifact_version_id=base_artifact_version_id,
            base_state_version_id=base_state_version_id,
            patch_type=patch_type,
            payload=payload,
            content_hash=content_hash,
            status="proposed",
            created_by=actor_id,
        )
        audit = ContextAuditEvent(
            id=uuid4(),
            catalog_entry_id=current.entry.id,
            context_object_id=current.context_object.id,
            space_id=space_id,
            action_set_id="context.patch",
            action_id="propose",
            source_lane="in_world_proposal",
            actor_type=actor_type,
            actor_id=actor_id,
            policy_decision="allowed",
            base_artifact_version_id=base_artifact_version_id,
            base_state_version_id=base_state_version_id,
            result_artifact_version_id=None,
            result_state_version_id=None,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            payload={"patch_id": str(patch.id), **audit_payload},
        )
        self.db.add(patch)
        self.db.add(audit)
        await self.db.commit()

        return {
            "ok": True,
            "patch_id": str(patch.id),
            "audit_event_id": str(audit.id),
            "status": "proposed",
        }

    async def _load_current(
        self,
        *,
        space_id: UUID,
        catalog_entry_id: UUID,
        lock_entry: bool = False,
    ) -> _CurrentContext:
        entry_stmt = select(ContextCatalogEntry).where(
            ContextCatalogEntry.space_id == space_id,
            ContextCatalogEntry.id == catalog_entry_id,
        )
        if lock_entry:
            entry_stmt = entry_stmt.with_for_update()
        entry_result = await self.db.execute(entry_stmt)
        entry = entry_result.scalar_one_or_none()
        if entry is None:
            raise ContextCatalogNotFound(f"Catalog entry not found: {catalog_entry_id}")

        context_object = await self._get_required(
            ContextObject,
            entry.current_context_object_id,
            "current context object",
        )
        artifact_version = await self._get_required(
            ContextArtifactVersion,
            entry.current_artifact_version_id,
            "current artifact version",
        )
        state_version = await self._get_required(
            ContextStateVersion,
            entry.current_state_version_id,
            "current state version",
        )
        return _CurrentContext(entry, context_object, artifact_version, state_version)

    async def _get_required(self, model: type, object_id: UUID | None, label: str):
        if object_id is None:
            raise ContextCatalogNotFound(f"Catalog entry has no {label}")
        result = await self.db.execute(select(model).where(model.id == object_id))
        value = result.scalar_one_or_none()
        if value is None:
            raise ContextCatalogNotFound(f"Catalog entry {label} not found: {object_id}")
        return value

    async def _get_idempotent_audit(
        self,
        *,
        context_object_id: UUID,
        actor_type: str,
        actor_id: str,
        action_id: str,
        idempotency_key: str,
    ) -> ContextAuditEvent | None:
        result = await self.db.execute(
            select(ContextAuditEvent).where(
                ContextAuditEvent.context_object_id == context_object_id,
                ContextAuditEvent.actor_type == actor_type,
                ContextAuditEvent.actor_id == actor_id,
                ContextAuditEvent.action_id == action_id,
                ContextAuditEvent.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    def _version_conflict(
        self,
        *,
        current: _CurrentContext,
        base_artifact_version_id: UUID,
        base_state_version_id: UUID,
    ) -> dict[str, Any] | None:
        if (
            base_artifact_version_id == current.artifact_version.id
            and base_state_version_id == current.state_version.id
        ):
            return None
        return {
            "ok": False,
            "error": "stale_base_version",
            "catalog_entry_id": str(current.entry.id),
            "base_artifact_version_id": str(base_artifact_version_id),
            "current_artifact_version_id": str(current.artifact_version.id),
            "base_state_version_id": str(base_state_version_id),
            "current_state_version_id": str(current.state_version.id),
            "suggested_resolution": "refresh_and_reapply",
        }

    def _apply_action_state_transition(
        self,
        *,
        action_id: str,
        current_state: dict[str, Any],
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        data = deepcopy(current_state)
        annotations = list(data.get("annotations") or [])

        if action_id == "approve":
            data["review_status"] = "approved"
            status = "approved"
        elif action_id == "reject":
            data["review_status"] = "rejected"
            status = "rejected"
        elif action_id == "request_changes":
            data["review_status"] = "changes_requested"
            annotations.append({"type": "request_changes", **payload})
            status = "changes_requested"
        elif action_id == "comment":
            annotations.append({"type": "comment", **payload})
            status = str(data.get("review_status") or "commented")
        elif action_id == "ok":
            data["acknowledged"] = True
            status = "acknowledged"
        elif action_id == "cancel":
            data["review_status"] = "cancelled"
            status = "cancelled"
        elif action_id == "place_mark":
            board = data.setdefault("board", [[None, None, None], [None, None, None], [None, None, None]])
            row = payload["row"]
            col = payload["col"]
            if board[row][col] is not None:
                raise ContextCatalogInvalidRequest("Tic-tac-toe square is already occupied")
            board[row][col] = payload["mark"]
            data["last_move"] = payload
            status = "move_accepted"
        elif action_id == "new_game":
            data = {"board": [[None, None, None], [None, None, None], [None, None, None]], "status": "active"}
            annotations = []
            status = "new_game"
        elif action_id == "resign":
            data["status"] = "resigned"
            status = "resigned"
        else:
            status = "committed"

        if annotations:
            data["annotations"] = annotations
        return data, status

    def _serialize_catalog_detail(self, current: _CurrentContext, *, include_content: bool) -> dict[str, Any]:
        artifact = {
            "id": str(current.artifact_version.id),
            "kind": current.artifact_version.kind,
            "attachment_id": str(current.artifact_version.attachment_id)
            if current.artifact_version.attachment_id
            else None,
            "sha256": current.artifact_version.sha256,
            "size_bytes": current.artifact_version.size_bytes,
            "created_by": current.artifact_version.created_by,
            "created_at": self._iso(current.artifact_version.created_at),
        }
        if include_content:
            artifact["content_ref"] = current.artifact_version.content_ref
            artifact["sandbox_policy"] = current.artifact_version.sandbox_policy

        return {
            **self._serialize_catalog_metadata(
                current.entry,
                available_actions=self._available_actions(current.context_object.action_set_id),
            ),
            "context_object": {
                "id": str(current.context_object.id),
                "kind": current.context_object.kind,
                "artifact_kind": current.context_object.artifact_kind,
                "action_set_id": current.context_object.action_set_id,
                "created_by": current.context_object.created_by,
                "created_at": self._iso(current.context_object.created_at),
            },
            "artifact": artifact,
            "state": {
                "id": str(current.state_version.id),
                "version_number": current.state_version.version_number,
                "data": current.state_version.data,
                "created_by": current.state_version.created_by,
                "created_at": self._iso(current.state_version.created_at),
            },
        }

    def _serialize_catalog_metadata(
        self,
        entry: ContextCatalogEntry,
        *,
        available_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "id": str(entry.id),
            "space_id": str(entry.space_id),
            "artifact_type": entry.artifact_type,
            "title": entry.title,
            "description": entry.description,
            "owner_id": entry.owner_id,
            "shelf": entry.shelf,
            "current_context_object_id": str(entry.current_context_object_id)
            if entry.current_context_object_id
            else None,
            "current_artifact_version_id": str(entry.current_artifact_version_id)
            if entry.current_artifact_version_id
            else None,
            "current_state_version_id": str(entry.current_state_version_id)
            if entry.current_state_version_id
            else None,
            "thumbnail_ref": entry.thumbnail_ref,
            "pinned": bool(entry.pinned),
            "status": entry.status,
            "created_at": self._iso(entry.created_at),
            "updated_at": self._iso(entry.updated_at),
        }
        if available_actions is not None:
            metadata["available_actions"] = available_actions
        return metadata

    def _available_actions(self, action_set_id: str) -> list[dict[str, Any]]:
        return [serialize_host_action(action) for action in list_action_definitions(action_set_id)]

    def _serialize_idempotent_action_result(self, audit: ContextAuditEvent) -> dict[str, Any]:
        return {
            "ok": True,
            "idempotent": True,
            "audit_event_id": str(audit.id),
            "new_state_version_id": str(audit.result_state_version_id) if audit.result_state_version_id else None,
            "new_artifact_version_id": str(audit.result_artifact_version_id)
            if audit.result_artifact_version_id
            else None,
            "status": "already_committed",
        }

    def _serialize_idempotent_patch_result(self, audit: ContextAuditEvent) -> dict[str, Any]:
        return {
            "ok": True,
            "idempotent": True,
            "audit_event_id": str(audit.id),
            "patch_id": audit.payload.get("patch_id") if audit.payload else None,
            "status": "already_proposed",
        }

    def _idempotency_payload_mismatch(self, audit: ContextAuditEvent) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "idempotency_payload_mismatch",
            "audit_event_id": str(audit.id),
            "idempotency_key": audit.idempotency_key,
        }

    def _actor_parts(self, actor: SimpleNamespace) -> tuple[str, str]:
        actor_type = str(getattr(actor, "type", None) or "unknown")
        actor_id = str(getattr(actor, "id", None) or "unknown")
        return actor_type, actor_id

    def _payload_hash(self, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _canonical_bytes(self, value: Any) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")

    def _unwrap_context_payload(self, value: Any) -> tuple[Any, dict[str, Any]]:
        if isinstance(value, dict) and "value" in value and any(
            key in value for key in ("agent_name", "created_at", "updated_at", "ttl", "expires_at", "topic")
        ):
            return value["value"], value
        return value, {}

    def _infer_artifact_kind(self, value: Any) -> str:
        if isinstance(value, dict):
            declared_type = str(
                value.get("type")
                or value.get("kind")
                or value.get("artifact_type")
                or value.get("content_type")
                or ""
            ).lower()
            if declared_type in {"html", "text/html"} or "html" in value:
                return "html"
            if declared_type in {"pdf", "application/pdf"} or "pdf" in value:
                return "pdf"
        if isinstance(value, str) and value.lstrip().lower().startswith("<!doctype html"):
            return "html"
        return "json"

    def _infer_title(self, key: str, value: Any, wrapper: dict[str, Any]) -> str:
        if isinstance(value, dict):
            title = value.get("title") or value.get("name")
            if isinstance(title, str) and title.strip():
                return title.strip()[:200]
        summary = wrapper.get("summary") or wrapper.get("summary_snippet")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()[:200]
        return key[:200]

    def _default_sandbox_policy(self, artifact_kind: str) -> dict[str, Any]:
        return {
            "network": False,
            "trusted_host_actions": artifact_kind in {"html", "pdf"},
        }

    def _validate_hash(self, value: str, *, field_name: str) -> None:
        if len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value):
            raise ContextCatalogInvalidRequest(f"{field_name} must be a 64-character hex sha256")

    def _iso(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None
