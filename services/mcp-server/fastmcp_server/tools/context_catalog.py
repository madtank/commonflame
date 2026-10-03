"""Governed Context Catalog handler.

NOT a standalone MCP tool. Per CTX-ARTIFACTS-002 the catalog is the governed
tier of `context`: `handle_catalog_action` is dispatched from the `context`
tool (actions create/act/patch, promote to=catalog, and tier=catalog reads).
Catalog entries are backend-owned, versioned, and action/audit aware.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Optional
from urllib.parse import quote

from fastmcp.tools.tool import ToolResult

from fastmcp_server.api_client import api_request_with_context
from fastmcp_server.mcp_ui import (
    build_notice,
    widget_tool_result,
)
from fastmcp_server.space_scoped_permissions import (
    blocked_reason,
    permissions_allow,
)

CatalogAction = Literal[
    "list", "get", "create", "create_from_context", "invoke_action", "propose_patch"
]


_HEAVY_ARTIFACT_FIELDS = {
    "content",
    "data",
    "html",
    "markdown",
    "pdf",
    "raw",
    "text",
    "value",
}


def _catalog_path(space_id: str, catalog_entry_id: str | None = None, suffix: str | None = None) -> str:
    path = f"/api/v1/spaces/{quote(str(space_id), safe='')}/context-catalog"
    if catalog_entry_id:
        path = f"{path}/{quote(str(catalog_entry_id), safe='')}"
    if suffix:
        path = f"{path}/{suffix.strip('/')}"
    return path


def _short_action_id(action_id: str) -> str:
    """Allow agents to pass either `approve` or `review.basic.approve`."""
    return action_id.rsplit(".", 1)[-1]


def _sha256_and_size(value: Any) -> tuple[str, int]:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _sha256_prefix(value: Any, length: int = 8) -> str:
    digest, _ = _sha256_and_size(value)
    return digest[:length]


def _strip_heavy_artifact_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_heavy_artifact_fields(item) for item in value]
    if not isinstance(value, dict):
        return value

    stripped: dict[str, Any] = {}
    for key, item in value.items():
        if key in _HEAVY_ARTIFACT_FIELDS:
            continue
        stripped[key] = _strip_heavy_artifact_fields(item)
    return stripped


def _extract_entries(result: dict[str, Any], *, include_content: bool) -> list[dict[str, Any]]:
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        raw_items = result.get("entries")
    if not isinstance(raw_items, list):
        raw_items = []

    entries = [item for item in raw_items if isinstance(item, dict)]
    if include_content:
        return entries
    return [_strip_heavy_artifact_fields(entry) for entry in entries]


def _extract_entry(result: dict[str, Any], *, include_content: bool) -> dict[str, Any]:
    raw_entry = result.get("entry") if isinstance(result.get("entry"), dict) else result
    if include_content:
        return dict(raw_entry)
    return _strip_heavy_artifact_fields(raw_entry)


def _normalize_available_actions(result: dict[str, Any]) -> list[dict[str, Any]]:
    actions = result.get("available_actions")
    if not isinstance(actions, list):
        actions = result.get("actions") if isinstance(result.get("actions"), list) else []

    normalized: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        projected = dict(action)
        canonical_label = projected.get("canonical_label")
        if "label" not in projected and isinstance(canonical_label, str):
            projected["label"] = canonical_label
        normalized.append(projected)
    return normalized


def _catalog_entry_key(entry: dict[str, Any]) -> str | None:
    for field in ("key", "id", "catalog_entry_id"):
        value = entry.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _catalog_context_item(entry: dict[str, Any]) -> dict[str, Any] | None:
    key = _catalog_entry_key(entry)
    if not key:
        return None
    title = entry.get("title") or entry.get("name") or key
    topic = entry.get("shelf") or entry.get("artifact_type") or "catalog"
    item: dict[str, Any] = {
        "key": key,
        "topic": str(topic),
        "subject": str(title),
        "summary": entry.get("description") or entry.get("summary") or str(title),
        "value": entry,
    }
    for field in ("created_at", "updated_at", "expires_at", "ttl", "pinned"):
        if entry.get(field) is not None:
            item[field] = entry[field]
    return item


def _catalog_context_items(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in entries:
        item = _catalog_context_item(entry)
        if item is not None:
            items.append(item)
    return items


def _catalog_result(
    result: dict[str, Any],
    action: str,
    *,
    include_content: bool = False,
    permission_bundle: dict[str, Any] | None = None,
    notice: dict[str, Any] | None = None,
) -> ToolResult:
    conflict = result.get("conflict") if isinstance(result.get("conflict"), dict) else None
    available_actions = _normalize_available_actions(result)

    if action == "list":
        entries = _extract_entries(result, include_content=include_content)
        items = _catalog_context_items(entries)
        data: dict[str, Any] = {
            "items": entries,
            "count": result.get("count", result.get("total", 0)),
            "include_content": include_content,
        }
        selected_key = None
    else:
        entry = _extract_entry(result, include_content=include_content)
        item = _catalog_context_item(entry)
        items = [item] if item is not None else []
        selected_key = item["key"] if item is not None else None
        data = {
            "entry": entry,
            "include_content": include_content,
        }

    structured: dict[str, Any] = {
        "kind": "context",
        "version": 1,
        "action": action,
        "count": result.get("count", result.get("total", len(items))),
        "items": items,
        "keys": [item["key"] for item in items],
        "state": {
            "action": action,
            "status": result.get("status", "ok"),
            "conflict": conflict,
        },
        "catalog": {
            "kind": "context_catalog",
            "version": 1,
            "data": data,
        },
        "catalog_actions": available_actions,
    }
    if selected_key:
        structured["selected_key"] = selected_key
    if notice:
        structured["notice"] = notice
    if permission_bundle:
        structured["space_context"] = permission_bundle.get("space_context")
        structured["permissions"] = permission_bundle.get("permissions")

    return widget_tool_result(
        "context",
        content=structured,
        structured_content=structured,
        meta={
            "ax_context_catalog_contract": "context_catalog.v1",
            "ax_context_catalog_raw_keys": sorted(
                key for key in result.keys() if isinstance(key, str)
            )[:20],
        },
    )


def _missing_space_result(action: str, permission_bundle: dict[str, Any] | None) -> ToolResult:
    return _catalog_result(
        {"status": "error", "error": "Missing active workspace context"},
        action,
        permission_bundle=permission_bundle,
        notice=build_notice(
            "Context Catalog requires an active workspace context.",
            severity="error",
            code="context_catalog_missing_space",
        ),
    )


def _write_blocked_result(action: str, permission_bundle: dict[str, Any]) -> ToolResult:
    return _catalog_result(
        {"status": "blocked"},
        action,
        permission_bundle=permission_bundle,
        notice=build_notice(
            blocked_reason(permission_bundle),
            severity="error",
            code="context_catalog_write_blocked",
        ),
    )


def _validation_error_result(message: str, action: str, permission_bundle: dict[str, Any]) -> ToolResult:
    return _catalog_result(
        {"status": "error", "error": message},
        action,
        permission_bundle=permission_bundle,
        notice=build_notice(
            message,
            severity="error",
            code="context_catalog_validation_error",
        ),
    )


async def handle_catalog_action(
    action: CatalogAction,
    *,
    ctx: dict[str, Any],
    permission_bundle: dict[str, Any],
    catalog_entry_id: Optional[str] = None,
    include_content: bool = False,
    context_key: Optional[str] = None,
    title: Optional[str] = None,
    description: Optional[str] = None,
    summary: Optional[str] = None,
    shelf: Optional[str] = None,
    artifact_type: Optional[str] = None,
    artifact_kind: Optional[str] = None,
    action_set_id: Optional[str] = None,
    content_ref: Optional[str] = None,
    sha256: Optional[str] = None,
    size_bytes: Optional[int] = None,
    artifact_value: Optional[Any] = None,
    state_value: Optional[Any] = None,
    pinned: Optional[bool] = None,
    status: Optional[str] = None,
    owner_id: Optional[str] = None,
    updated_after: Optional[str] = None,
    limit: Optional[int] = None,
    action_id: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    base_artifact_version_id: Optional[str] = None,
    base_state_version_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    source_lane: Optional[str] = None,
    patch_type: Optional[str] = None,
    content_hash: Optional[str] = None,
    patch: Optional[dict[str, Any]] = None,
) -> ToolResult:
    """Internal handler for governed Context Catalog actions.

    Dispatched from the `context` tool (CTX-ARTIFACTS-002) — NOT a standalone MCP
    tool. The caller resolves the operating space via `_ctx_with_permission_space`
    before dispatching, so an agent never needs to pass `space_id`.

    Internal action names: list, get, create, create_from_context,
    invoke_action, propose_patch.
    """
    if not ctx.get("space_id"):
        return _missing_space_result(action, permission_bundle)

    # Surface the public verb the agent actually used in error messages, not the
    # internal catalog action name (agents never type invoke_action/propose_patch).
    display = {
        "invoke_action": "act",
        "propose_patch": "patch",
        "create_from_context": "promote(to=catalog)",
    }.get(action, action)

    if action == "list":
        params: dict[str, Any] = {
            "include_content": include_content,
            "limit": limit if limit is not None else 50,
        }
        for key, value in {
            "shelf": shelf,
            "artifact_type": artifact_type,
            "pinned": pinned,
            "status": status,
            "owner_id": owner_id,
            "updated_after": updated_after,
        }.items():
            if value is not None:
                params[key] = value
        result = await api_request_with_context(
            ctx,
            "GET",
            _catalog_path(ctx["space_id"]),
            params=params,
        )
        return _catalog_result(
            result,
            "list",
            include_content=include_content,
            permission_bundle=permission_bundle,
        )

    if action == "get":
        if not catalog_entry_id:
            return _validation_error_result(f"'catalog_entry_id' required for {display} action", action, permission_bundle)
        result = await api_request_with_context(
            ctx,
            "GET",
            _catalog_path(ctx["space_id"], catalog_entry_id),
            params={"include_content": include_content},
        )
        return _catalog_result(
            result,
            "get",
            include_content=include_content,
            permission_bundle=permission_bundle,
        )

    if not (
        permissions_allow(permission_bundle, "can_create")
        or permissions_allow(permission_bundle, "can_update")
    ):
        return _write_blocked_result(action, permission_bundle)

    if action == "create_from_context":
        if not context_key:
            return _validation_error_result(
                f"'context_key' required for {display} action", action, permission_bundle
            )
        body: dict[str, Any] = {
            "key": context_key,
            "action_set_id": action_set_id or "review.basic",
        }
        resolved_description = description if description is not None else summary
        for key, value in {
            "title": title,
            "description": resolved_description,
            "artifact_type": artifact_type,
            "artifact_kind": artifact_kind,
            "shelf": shelf,
            "pinned": pinned,
            "owner_id": owner_id,
        }.items():
            if value is not None:
                body[key] = value
        if state_value is not None:
            body["initial_state"] = state_value
        result = await api_request_with_context(
            ctx,
            "POST",
            _catalog_path(ctx["space_id"], suffix="from-context"),
            json_data=body,
        )
        return _catalog_result(result, "create_from_context", include_content=False, permission_bundle=permission_bundle)

    if action == "create":
        if not title:
            return _validation_error_result(f"'title' required for {display} action", action, permission_bundle)
        if not artifact_type:
            return _validation_error_result(f"'artifact_type' required for {display} action", action, permission_bundle)
        resolved_kind = artifact_kind or artifact_type.rsplit(".", 1)[-1]
        resolved_action_set_id = action_set_id or "review.basic"
        resolved_sha256 = sha256
        resolved_size_bytes = size_bytes
        if artifact_value is not None and (not resolved_sha256 or resolved_size_bytes is None):
            resolved_sha256, resolved_size_bytes = _sha256_and_size(artifact_value)
        if not resolved_sha256 or resolved_size_bytes is None:
            return _validation_error_result(
                "'sha256' and 'size_bytes' required unless 'artifact_value' is provided",
                action,
                permission_bundle,
            )
        body: dict[str, Any] = {
            "artifact_type": artifact_type,
            "title": title,
            "artifact_kind": resolved_kind,
            "action_set_id": resolved_action_set_id,
            "sha256": resolved_sha256,
            "size_bytes": resolved_size_bytes,
        }
        if content_ref is not None:
            body["content_ref"] = content_ref
        resolved_description = description if description is not None else summary
        if resolved_description is not None:
            body["description"] = resolved_description
        if shelf is not None:
            body["shelf"] = shelf
        if pinned is not None:
            body["pinned"] = pinned
        if state_value is not None:
            body["initial_state"] = state_value
        result = await api_request_with_context(
            ctx,
            "POST",
            _catalog_path(ctx["space_id"]),
            json_data=body,
        )
        return _catalog_result(result, "create", include_content=True, permission_bundle=permission_bundle)

    if action == "invoke_action":
        if not catalog_entry_id:
            return _validation_error_result(
                f"'catalog_entry_id' required for {display} action", action, permission_bundle
            )
        if not action_id:
            return _validation_error_result(f"'action_id' required for {display} action", action, permission_bundle)
        if not base_artifact_version_id or not base_state_version_id:
            return _validation_error_result(
                f"'base_artifact_version_id' and 'base_state_version_id' required for {display} action",
                action,
                permission_bundle,
            )
        action_payload = payload or {}
        resolved_action_id = _short_action_id(action_id)
        body = {
            "action_id": resolved_action_id,
            "base_artifact_version_id": base_artifact_version_id,
            "base_state_version_id": base_state_version_id,
            "idempotency_key": idempotency_key
            or f"{resolved_action_id}:{base_state_version_id}:{_sha256_prefix(action_payload)}",
            "payload": action_payload,
        }
        if source_lane:
            # The backend validates this against the registered action definition;
            # MCP forwards it for explicit audit context, not authorization.
            body["source_lane"] = source_lane
        result = await api_request_with_context(
            ctx,
            "POST",
            _catalog_path(ctx["space_id"], catalog_entry_id, "actions"),
            json_data=body,
        )
        return _catalog_result(result, "invoke_action", include_content=True, permission_bundle=permission_bundle)

    if action == "propose_patch":
        if not catalog_entry_id:
            return _validation_error_result(
                f"'catalog_entry_id' required for {display} action", action, permission_bundle
            )
        if patch is None:
            return _validation_error_result(f"'patch' required for {display} action", action, permission_bundle)
        if not base_artifact_version_id or not base_state_version_id:
            return _validation_error_result(
                f"'base_artifact_version_id' and 'base_state_version_id' required for {display} action",
                action,
                permission_bundle,
            )
        resolved_patch_type = patch_type or str(patch.get("patch_type") or "generic_patch")
        patch_payload = patch.get("payload") if isinstance(patch.get("payload"), dict) else patch
        resolved_content_hash = content_hash or patch.get("content_hash")
        if not resolved_content_hash:
            resolved_content_hash, _ = _sha256_and_size(patch_payload)
        body = {
            "base_artifact_version_id": base_artifact_version_id,
            "base_state_version_id": base_state_version_id,
            "idempotency_key": idempotency_key
            or f"patch:{resolved_patch_type}:{base_state_version_id}:{resolved_content_hash[:8]}",
            "patch_type": resolved_patch_type,
            "payload": patch_payload,
            "content_hash": resolved_content_hash,
        }
        result = await api_request_with_context(
            ctx,
            "POST",
            _catalog_path(ctx["space_id"], catalog_entry_id, "patches"),
            json_data=body,
        )
        return _catalog_result(result, "propose_patch", include_content=True, permission_bundle=permission_bundle)

    return _validation_error_result(f"Unknown action: {action}", action, permission_bundle)
