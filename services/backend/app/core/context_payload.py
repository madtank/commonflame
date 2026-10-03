"""Helpers for keeping shared context safe for Redis, SSE, and model prompts."""

from __future__ import annotations

import json
import os
import re
from typing import Any

CONTEXT_INLINE_MAX_BYTES_DEFAULT = 256 * 1024
CONTEXT_EVENT_MAX_BYTES_DEFAULT = 16 * 1024
CONTEXT_SUMMARY_VALUE_MAX_CHARS_DEFAULT = 12_000

_DATA_URL_RE = re.compile(r"^data:([^;,]+)?(?:;[^,]+)*,", re.IGNORECASE)


class ContextPayloadTooLarge(ValueError):
    """Raised when a context value is too large to store inline in Redis."""

    def __init__(self, *, size_bytes: int, max_bytes: int):
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"Context payload is {size_bytes} bytes, exceeding the inline Redis limit of {max_bytes} bytes"
        )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, value)


def context_inline_max_bytes() -> int:
    return _env_int("CONTEXT_INLINE_MAX_BYTES", CONTEXT_INLINE_MAX_BYTES_DEFAULT)


def context_event_max_bytes() -> int:
    return _env_int("CONTEXT_EVENT_MAX_BYTES", CONTEXT_EVENT_MAX_BYTES_DEFAULT)


def context_summary_value_max_chars() -> int:
    return _env_int("CONTEXT_SUMMARY_VALUE_MAX_CHARS", CONTEXT_SUMMARY_VALUE_MAX_CHARS_DEFAULT)


def json_size_bytes(value: Any) -> int:
    """Return compact JSON byte size and preserve json.dumps serializability semantics."""
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def ensure_context_inline_size(value: Any, *, max_bytes: int | None = None) -> int:
    """Validate that a context value is safe to keep inline in Redis."""
    limit = max_bytes or context_inline_max_bytes()
    size_bytes = json_size_bytes(value)
    if size_bytes > limit:
        raise ContextPayloadTooLarge(size_bytes=size_bytes, max_bytes=limit)
    return size_bytes


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[: max(0, max_chars - 3)].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip(" ,;:-") + "..."


def _data_url_marker(value: str) -> dict[str, Any] | None:
    match = _DATA_URL_RE.match(value)
    if not match:
        return None
    return {
        "_omitted": True,
        "reason": "data_url",
        "content_type": match.group(1) or "application/octet-stream",
        "size_bytes": len(value.encode("utf-8")),
    }


def _compact_value(
    value: Any,
    *,
    max_string_chars: int,
    max_items: int,
    max_depth: int,
    depth: int = 0,
) -> Any:
    if depth >= max_depth:
        return {"_omitted": True, "reason": "max_depth"}

    if isinstance(value, str):
        marker = _data_url_marker(value)
        if marker is not None:
            return marker
        return _truncate_text(value, max_string_chars)

    if isinstance(value, list):
        items = [
            _compact_value(
                item,
                max_string_chars=max_string_chars,
                max_items=max_items,
                max_depth=max_depth,
                depth=depth + 1,
            )
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            items.append({"_omitted": len(value) - max_items, "reason": "list_item_limit"})
        return items

    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                compact["_omitted"] = {"count": len(value) - max_items, "reason": "object_key_limit"}
                break
            compact[str(key)] = _compact_value(
                item,
                max_string_chars=max_string_chars,
                max_items=max_items,
                max_depth=max_depth,
                depth=depth + 1,
            )
        return compact

    return value


def compact_value_for_summary(value: Any, *, max_chars: int | None = None) -> Any:
    """Return a value preview safe to serialize into a model prompt."""
    limit = max_chars or context_summary_value_max_chars()
    return _compact_value(
        value,
        max_string_chars=min(4_000, limit),
        max_items=40,
        max_depth=6,
    )


def serialize_value_preview(value: Any, *, max_chars: int | None = None) -> str:
    """Serialize a compact value preview and enforce a final character budget."""
    limit = max_chars or context_summary_value_max_chars()
    compact = compact_value_for_summary(value, max_chars=limit)
    if isinstance(compact, str):
        text = compact
    else:
        try:
            text = json.dumps(compact, ensure_ascii=True, default=str)
        except Exception:
            text = str(compact)
    return _truncate_text(text, limit)


def _plain_text_preview(value: Any, *, max_chars: int = 512) -> str:
    text = serialize_value_preview(value, max_chars=max_chars)
    return re.sub(r"\s+", " ", text).strip()


def _omitted_value_marker(value: Any, *, key: str, size_bytes: int | None, reason: str) -> dict[str, Any]:
    return {
        "_omitted": True,
        "reason": reason,
        "size_bytes": size_bytes,
        "preview": _plain_text_preview(value),
        "value_ref": {"kind": "context", "key": key},
    }


def context_record_for_event(
    record: dict[str, Any],
    *,
    key: str,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Return a context record safe to store in Redis Streams / send over SSE."""
    limit = max_bytes or context_event_max_bytes()
    event_record = dict(record)
    if "value" in event_record:
        event_record["value"] = _compact_value(
            record.get("value"),
            max_string_chars=4_000,
            max_items=40,
            max_depth=6,
        )

    try:
        if json_size_bytes(event_record) <= limit:
            return event_record
    except (TypeError, ValueError):
        pass

    raw_value = record.get("value")
    size_bytes = record.get("value_size_bytes")
    if not isinstance(size_bytes, int):
        try:
            size_bytes = json_size_bytes(raw_value)
        except (TypeError, ValueError):
            size_bytes = None

    event_record["value"] = _omitted_value_marker(
        raw_value,
        key=key,
        size_bytes=size_bytes,
        reason="context_event_payload_limit",
    )

    try:
        if json_size_bytes(event_record) <= limit:
            return event_record
    except (TypeError, ValueError):
        pass

    # Last-resort guard so stream entries remain bounded even with unusual metadata.
    compact_record = {
        "value": _omitted_value_marker(
            raw_value,
            key=key,
            size_bytes=size_bytes,
            reason="context_event_payload_limit",
        ),
        "agent_name": record.get("agent_name"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "ttl": record.get("ttl"),
        "expires_at": record.get("expires_at"),
        "topic": record.get("topic"),
        "summary": record.get("summary"),
        "summary_model": record.get("summary_model"),
        "summary_generated_at": record.get("summary_generated_at"),
        "value_size_bytes": size_bytes,
    }
    return {key: value for key, value in compact_record.items() if value is not None}
