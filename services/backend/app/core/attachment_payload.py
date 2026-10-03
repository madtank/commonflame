"""Attachment payload normalization for agent dispatch.

Message APIs have accumulated a few attachment field names over time
(`content_type` vs. `mime_type`, `size` vs. `size_bytes`). Agent runtimes should
receive one canonical shape so attachments are visible without every dispatch
path re-implementing that mapping.
"""

from __future__ import annotations

from typing import Any


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return str(value)
    return None


def _first_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def normalize_agent_attachment(raw: Any) -> dict[str, Any] | None:
    """Return the attachment shape expected by aX/agent runners.

    The `url` field is kept even when auth-protected. If the runtime cannot
    fetch bytes directly, it can still show filename/mime/context metadata.
    """
    if not isinstance(raw, dict):
        return None

    normalized: dict[str, Any] = {
        "url": _first_string(raw, "url", "file_url", "public_url", "download_url", "storage_path", "file_path") or "",
        "mime_type": _first_string(raw, "mime_type", "content_type", "file_type", "type") or "application/octet-stream",
    }

    for out_key, candidates in {
        "id": ("id", "attachment_id"),
        "filename": ("filename", "name", "display_name"),
        "context_key": ("context_key", "contextKey", "key"),
        "kind": ("kind",),
        "data": ("data", "base64", "base64_data"),
    }.items():
        value = _first_string(raw, *candidates)
        if value is not None:
            normalized[out_key] = value

    size_bytes = _first_int(raw, "size_bytes", "file_size", "size")
    if size_bytes is not None:
        normalized["size_bytes"] = size_bytes

    return normalized


def normalize_agent_attachments(raw_attachments: Any) -> list[dict[str, Any]]:
    """Normalize a list of attachment dictionaries for agent dispatch."""
    if not isinstance(raw_attachments, list):
        return []
    return [
        attachment
        for attachment in (normalize_agent_attachment(raw) for raw in raw_attachments)
        if attachment is not None
    ]


def attachments_from_message_metadata(message_metadata: Any) -> list[dict[str, Any]]:
    """Extract accepted attachments from message metadata for agent dispatch."""
    if not isinstance(message_metadata, dict):
        return []

    for key in ("accepted_attachments", "attachments"):
        attachments = normalize_agent_attachments(message_metadata.get(key))
        if attachments:
            return attachments

    context_uploads = normalize_agent_attachments(message_metadata.get("context_uploads"))
    if context_uploads:
        return context_uploads

    return []


def attachment_event_refs(raw_attachments: Any) -> list[dict[str, Any]]:
    """Return lightweight attachment refs safe for SSE/MCP message events."""
    refs: list[dict[str, Any]] = []
    for attachment in normalize_agent_attachments(raw_attachments):
        ref: dict[str, Any] = {}
        for source_key, target_key in (
            ("id", "id"),
            ("context_key", "context_key"),
            ("filename", "filename"),
            ("mime_type", "content_type"),
            ("size_bytes", "size_bytes"),
        ):
            value = attachment.get(source_key)
            if value is not None and value != "":
                ref[target_key] = value
        if ref:
            refs.append(ref)
    return refs


def attachment_event_refs_from_message_metadata(message_metadata: Any) -> list[dict[str, Any]]:
    """Extract lightweight attachment refs from message metadata for live events."""
    return attachment_event_refs(attachments_from_message_metadata(message_metadata))


def attachments_from_dispatch_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract attachments from a queued dispatch payload."""
    attachments = normalize_agent_attachments(payload.get("attachments"))
    if attachments:
        return attachments

    attachments = attachments_from_message_metadata(payload.get("message_metadata"))
    if attachments:
        return attachments

    return normalize_agent_attachments(payload.get("accepted_attachments"))
