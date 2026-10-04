"""Context tool for FastMCP server.

Provides ephemeral key-value store for agent coordination.
All operations route through backend API (ROUTER-001).
Uses CurrentAccessToken() DI to forward backend-issued JWTs.
"""

import asyncio
import json
import logging
import re
from typing import Annotated, Any, Literal, Optional
from urllib.parse import quote

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.server.auth import AccessToken
from fastmcp.tools import ToolResult
from starlette.requests import Request

from fastmcp_server.api_client import (
    api_request,
    extract_agent_context,
    user_request_space_params,
    user_request_space_payload,
)
from fastmcp_server.mcp_ui import (
    bounded_write_annotations,
    build_notice,
    tool_app_config,
    tool_meta,
    tool_output_schema,
    widget_tool_result,
)
from fastmcp_server.space_scoped_permissions import (
    blocked_reason,
    permissions_allow,
    resolve_space_scoped_permissions,
)
from fastmcp_server.tools.context_catalog import handle_catalog_action

logger = logging.getLogger(__name__)
JsonValue = str | int | float | bool | dict[str, Any] | list[Any] | None
_CONTEXT_SUMMARY_MAX_LEN = 96
# Keep in sync with the context() action Literal; these actions mutate context state.
_CONTEXT_TERMINATE_DELETE_CONCURRENCY = 8
_CONTEXT_TERMINATE_MAX_LIMIT = 100
# Hard cap on items returned in a list-style widget result. The backend does not
# reliably honor the limit param, so the MCP layer enforces a ceiling to keep the
# response under model token limits. `count` still reports the true total; narrow
# with prefix/topic or read a single key with get.
_CONTEXT_LIST_MAX_ITEMS = 50
# Hard ceiling for an explicitly requested list page. Default stays 50 (keeps
# agent calls lean); the explorer UI requests more to browse a whole space.
# Safe to raise because list rows are metadata-only (value bodies omitted).
_CONTEXT_LIST_HARD_MAX = 200
# Write actions subject to the agent space-switch guard. Includes the governed
# catalog writes (create/act/patch) so a mismatched space_id is rejected with
# context_space_switch_required rather than silently written to the current space.
# `promote` covers its to=catalog variant since it is already listed here.
_CONTEXT_WRITE_ACTIONS = frozenset(
    {"set", "delete", "approve", "decline", "terminate", "promote", "create", "act", "patch"}
)
# Mirrored in the Context MCP app widget so legacy payloads render the same
# before and after the server-side normalizer is deployed.
_DECLARED_UPLOAD_CONTENT_TYPES = {
    "audio": "audio/*",
    "csv": "text/csv",
    "file": "application/octet-stream",
    "image": "image/*",
    "json": "application/json",
    "markdown": "text/markdown",
    "md": "text/markdown",
    "pdf": "application/pdf",
    "svg": "image/svg+xml",
    "text": "text/plain",
    "txt": "text/plain",
    "video": "video/*",
    "xml": "application/xml",
}

# Maintenance guard: tests assert advertised form parameters stay aligned with
# context()'s live input schema. Add new public parameters to the relevant
# action form below, or explicitly document why they are intentionally omitted.
_CONTEXT_CATALOG_PARAMETERS: tuple[str, ...] = (
    "tier", "to", "catalog_entry_id", "include_content", "context_key",
    "title", "description", "summary", "shelf", "artifact_type",
    "artifact_kind", "action_set_id", "content_ref", "sha256",
    "size_bytes", "artifact_value", "state_value", "pinned", "status",
    "owner_id", "updated_after", "limit", "action_id", "payload",
    "base_artifact_version_id", "base_state_version_id", "idempotency_key",
    "source_lane", "patch_type", "content_hash", "patch",
)

_CONTEXT_SIMPLE_ACTION_PARAMETERS: dict[str, tuple[str, ...]] = {
    "get": ("action", "key"),
    "set": ("action", "key", "value", "ttl", "topic", "content_type", "render_as"),
    "list": ("action", "prefix", "topic", "agent_id", "limit", "offset"),
    "delete": ("action", "key"),
    "approve": ("action", "key"),
    "decline": ("action", "key"),
    "terminate": ("action", "keys", "key", "prefix", "topic", "limit"),
    "promote": ("action", "key", "to"),
}
_CONTEXT_SIMPLE_ACTION_REQUIRED: dict[str, list[str]] = {
    "get": ["action", "key"],
    "set": ["action", "key", "value"],
    "list": ["action"],
    "delete": ["action", "key"],
    "approve": ["action", "key"],
    "decline": ["action", "key"],
    "terminate": ["action"],
    "promote": ["action", "key"],
}

_CONTEXT_GOVERNED_ACTION_PARAMETERS: dict[str, tuple[str, ...]] = {
    "create": (
        "action", "title", "artifact_type", "artifact_value", "value",
        "content_ref", "sha256", "size_bytes", "state_value",
        "description", "summary", "shelf", "artifact_kind",
        "action_set_id", "pinned", "status",
    ),
    "act": (
        "action", "catalog_entry_id", "action_id", "payload",
        "base_artifact_version_id", "base_state_version_id", "idempotency_key",
    ),
    "patch": (
        "action", "catalog_entry_id", "patch", "base_artifact_version_id",
        "base_state_version_id", "source_lane", "patch_type", "content_hash",
        "idempotency_key",
    ),
}
_CONTEXT_GOVERNED_ACTION_REQUIRED: dict[str, list[str]] = {
    "create": ["action", "title", "artifact_type"],
    "act": [
        "action", "catalog_entry_id", "action_id",
        "base_artifact_version_id", "base_state_version_id",
    ],
    "patch": [
        "action", "catalog_entry_id", "patch",
        "base_artifact_version_id", "base_state_version_id",
    ],
}

_CONTEXT_CATALOG_READ_PARAMETERS: dict[str, tuple[str, ...]] = {
    "get": ("action", "tier", "catalog_entry_id", "include_content"),
    "list": (
        "action", "tier", "limit", "status", "owner_id",
        "updated_after", "include_content",
    ),
}
_CONTEXT_CATALOG_READ_REQUIRED: dict[str, list[str]] = {
    "get": ["action", "tier", "catalog_entry_id"],
    "list": ["action", "tier"],
}
_CONTEXT_CATALOG_PROMOTE_PARAMETERS: tuple[str, ...] = (
    "action", "key", "context_key", "to", "title", "artifact_type", "artifact_kind",
    "action_set_id", "state_value", "description", "summary", "shelf", "pinned",
)


def _context_action_forms() -> dict[str, Any]:
    """Return host-facing action-specific context forms.

    MCP inputSchema is a superset because FastMCP exposes one callable per
    platform primitive. This metadata lets widgets/clients default to economical
    key-value forms and reveal governed catalog/version fields only on demand.
    """
    simple_actions = {
        action: {
            "mode": "simple_key_value",
            "parameters": list(parameters),
            "required": list(_CONTEXT_SIMPLE_ACTION_REQUIRED[action]),
        }
        for action, parameters in _CONTEXT_SIMPLE_ACTION_PARAMETERS.items()
    }

    governed_actions = {
        action: {
            "mode": "governed_catalog",
            "parameters": list(parameters),
            "required": list(_CONTEXT_GOVERNED_ACTION_REQUIRED[action]),
        }
        for action, parameters in _CONTEXT_GOVERNED_ACTION_PARAMETERS.items()
    }

    return {
        "ax/actionForms": {
            "version": 1,  # Bump for breaking action names, modes, or required-list changes.
            "default_mode": "simple_key_value",
            "summary": (
                "Default context forms are simple key-value operations. "
                'Governed catalog fields are hidden until tier="catalog", '
                'to="catalog", or create/act/patch is selected.'
            ),
            "simple_parameters": sorted(
                {
                    parameter
                    for parameters in _CONTEXT_SIMPLE_ACTION_PARAMETERS.values()
                    for parameter in parameters
                }
            ),
            "catalog_parameters": sorted(_CONTEXT_CATALOG_PARAMETERS),
            "catalog_mode_triggers": {
                "tier": "catalog",
                "to": "catalog",
                "actions": ["create", "act", "patch"],
            },
            "actions": {
                **simple_actions,
                "get_catalog": {
                    "action": "get",
                    "alias_of": "get",
                    "mode": "governed_catalog",
                    "parameters": list(_CONTEXT_CATALOG_READ_PARAMETERS["get"]),
                    "required": list(_CONTEXT_CATALOG_READ_REQUIRED["get"]),
                },
                "list_catalog": {
                    "action": "list",
                    "alias_of": "list",
                    "mode": "governed_catalog",
                    "parameters": list(_CONTEXT_CATALOG_READ_PARAMETERS["list"]),
                    "required": list(_CONTEXT_CATALOG_READ_REQUIRED["list"]),
                },
                "promote_catalog": {
                    "action": "promote",
                    "alias_of": "promote",
                    "mode": "governed_catalog",
                    "parameters": list(_CONTEXT_CATALOG_PROMOTE_PARAMETERS),
                    "required": ["action", "key", "to"],
                },
                **governed_actions,
            },
        }
    }

# HTML aliases stay out of _DECLARED_UPLOAD_CONTENT_TYPES because legacy
# structured artifacts use {"type":"html","html":...}; declaring html globally
# would make the file-upload normalizer consume those before body extraction.
_RENDER_AS_CONTENT_TYPES = {
    "html": "text/html",
    "htm": "text/html",
}
_NORMALIZE_ALIAS_RE = re.compile(r"[\s-]+")
_DOCTYPE_HTML_RE = re.compile(r"<!doctype\s+html(?:\s|>)")
# Keep sniffing bounded; agents may save full HTML documents here.
_INLINE_RENDER_PREFIX_SCAN_CHARS = 200
_HTML_ROOT_RE = re.compile(r"<html(?:\s|>)")
_CONTEXT_KEY_SEGMENT_RE = re.compile(r"[:/\\]+")
_FILENAME_CONTENT_TYPES = {
    ".avif": "image/avif",
    ".csv": "text/csv",
    ".gif": "image/gif",
    ".htm": "text/html",
    ".html": "text/html",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".json": "application/json",
    ".log": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".xml": "application/xml",
}
_FILENAME_CONTENT_TYPE_SUFFIXES = tuple(
    sorted(_FILENAME_CONTENT_TYPES, key=len, reverse=True)
)
_CONTENT_TYPE_EXTENSIONS = {
    "application/json": ".json",
    "application/xml": ".xml",
    "image/svg+xml": ".svg",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/xml": ".xml",
}
_STRUCTURED_CONTENT_VALUE_KEYS = ("html", "content", "body", "text", "data")
_GENERIC_STRUCTURED_CONTENT_VALUE_KEYS = tuple(
    key for key in _STRUCTURED_CONTENT_VALUE_KEYS if key != "html"
)
# These keys are control fields or alternate inline-body carriers. When one
# body carrier is promoted into file_upload.content, remove the others so
# legacy structured HTML fields do not leak into the normalized upload payload.
_CONTEXT_SET_FILE_UPLOAD_DRAIN_KEYS = frozenset(
    {
        "type",
        "kind",
        "artifact_type",
        "content_type",
        "contentType",
        *_STRUCTURED_CONTENT_VALUE_KEYS,
    }
)
# Alias keys are removed once normalized. Canonical output keys
# (filename/content_type/url/size/attachment_id) and descriptive metadata such
# as name/title stay in the value so callers do not lose provenance.
_FILE_UPLOAD_ALIAS_KEYS = frozenset(
    {
        "kind",
        "artifact_type",
        "artifact kind",
        "file_name",
        "file name",
        "fileName",
        "content type",
        "contentType",
        "mime_type",
        "mime type",
        "mimeType",
        "resource_mime_type",
        "resource mime type",
        "resourceMimeType",
        "media_type",
        "media type",
        "mediaType",
        "file_type",
        "file type",
        "fileType",
        "href",
        "url",
        "download_url",
        "download url",
        "downloadUrl",
        "file_url",
        "file url",
        "fileUrl",
        "resource_url",
        "resource url",
        "resourceUrl",
        "src",
        "attachment id",
        "attachmentId",
        "content base64",
        "content_base64",
        "contentBase64",
        "data base64",
        "data_base64",
        "dataBase64",
        "data url",
        "data_url",
        "dataUrl",
        "size_bytes",
        "size bytes",
        "sizeBytes",
        "byte_size",
        "byte size",
        "byteSize",
        "bytes",
    }
)
# Keep this explicit-metadata set in sync with the Context Explorer widget's
# hasExplicitUploadMetadata list; tests/test_widget_call_contract.py enforces
# that contract. It is intentionally narrower than _FILE_UPLOAD_ALIAS_KEYS so
# plain JSON API payloads with generic url/size fields still render as JSON
# documents.
_EXPLICIT_FILE_UPLOAD_METADATA_KEYS = frozenset(
    {
        "attachment id",
        "attachment_id",
        "attachmentId",
        "base64",
        "content base64",
        "content type",
        "content_base64",
        "content_type",
        "contentBase64",
        "contentType",
        "data base64",
        "data url",
        "data_base64",
        "data_url",
        "dataBase64",
        "dataUrl",
        "download url",
        "download_url",
        "downloadUrl",
        "file name",
        "file type",
        "file url",
        "file_name",
        "file_type",
        "file_url",
        "fileName",
        "fileType",
        "fileUrl",
        "filename",
        "media type",
        "media_type",
        "mediaType",
        "mime type",
        "mime_type",
        "mimeType",
        "resource mime type",
        "resource url",
        "resource_mime_type",
        "resource_url",
        "resourceMimeType",
        "resourceUrl",
    }
)


_CONTEXT_RESULT_META_KEYS = {
    "action",
    "count",
    "detail",
    "entries",
    "error",
    "items",
    "keys",
    "limit",
    "message",
    "offset",
    "status",
}

_CONTEXT_VALUE_META_KEYS = {
    "agent_name",
    "content_type",
    "created_at",
    "expires_at",
    "source",
    "storage",
    "summary",
    "summary_snippet",
    "topic",
    "ttl",
    "updated_at",
    "value_preview",
    "value_omitted",
    "value_size",
    "value_type",
}


def _looks_like_wrapped_value(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if "value" not in value and "value_preview" not in value:
        return False
    return any(key in value for key in _CONTEXT_VALUE_META_KEYS)


def _normalize_context_item(raw: dict[str, Any], *, default_key: str | None = None) -> dict[str, Any]:
    key = raw.get("key") if isinstance(raw.get("key"), str) else default_key
    item: dict[str, Any] = {"key": key or ""}

    payload = raw
    if _looks_like_wrapped_value(raw.get("value")):
        payload = raw["value"]

    if _looks_like_wrapped_value(payload):
        if "value" in payload:
            item["value"] = payload.get("value")
        if "value_preview" in payload:
            item["value_preview"] = payload.get("value_preview")
    elif "value" in raw:
        item["value"] = raw.get("value")

    for field in _CONTEXT_VALUE_META_KEYS:
        if field in payload:
            item[field] = payload.get(field)
        elif field in raw:
            item[field] = raw.get(field)

    if "status" in raw:
        item["status"] = raw.get("status")

    if isinstance(raw.get("file_upload"), dict):
        item["file_upload"] = raw["file_upload"]

    value = item.get("value")
    if isinstance(value, dict):
        subject = value.get("subject")
        if isinstance(subject, str) and subject.strip():
            item["subject"] = subject.strip()

        note = value.get("note")
        if isinstance(note, str) and note.strip():
            item["note_preview"] = note.strip()

        summary = value.get("summary")
        if isinstance(summary, str) and summary.strip():
            item["summary"] = summary.strip()

    if not item.get("updated_at") and item.get("created_at"):
        item["updated_at"] = item["created_at"]

    if _is_durable_context_item(item):
        item.pop("expires_at", None)
        item.pop("ttl", None)

    normalized_upload = _normalize_file_upload_value(
        item.get("value")
    ) or _normalize_file_upload_from_context_key(item.get("key"), item.get("value"))
    if normalized_upload:
        item["value"] = normalized_upload
        item["file_upload"] = {
            "filename": normalized_upload.get("filename"),
            "content_type": normalized_upload.get("content_type"),
            "size": normalized_upload.get("size"),
            "url": normalized_upload.get("url"),
            "attachment_id": normalized_upload.get("attachment_id"),
        }
        if "content" in normalized_upload:
            item["file_content"] = normalized_upload["content"]

    # If normalization did not consume the value, keep support for older JSON
    # string uploads that used exactly type=file_upload.
    value = item.get("value")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict) and parsed.get("type") == "file_upload":
                # Surface file metadata at item level for easy agent access
                item["file_upload"] = {
                    "filename": parsed.get("filename"),
                    "content_type": parsed.get("content_type"),
                    "size": parsed.get("size"),
                    "url": parsed.get("url"),
                    "attachment_id": parsed.get("attachment_id"),
                }
                # If content was inlined by CLI, surface it directly
                if "content" in parsed:
                    item["file_content"] = parsed["content"]
        except (json.JSONDecodeError, TypeError):
            pass

    summary = _derive_context_summary(item)
    if summary:
        item["summary"] = summary

    return item


def _strip_markdown_summary(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(^|\n)\s{0,3}#{1,6}\s*", " ", text)
    text = re.sub(r"(^|\n)\s*[-*+]\s+", " ", text)
    text = re.sub(r"(^|\n)\s*\d+\.\s+", " ", text)
    text = re.sub(r"[*_~>#]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate_summary(text: str, *, limit: int = _CONTEXT_SUMMARY_MAX_LEN) -> str:
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip(" ,;:-") + "..."


def _humanize_key(key: str) -> str:
    cleaned = re.sub(r"[_:/-]+", " ", key).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _derive_summary_from_value(value: Any, *, key: str | None = None) -> str | None:
    if value is None:
        return _humanize_key(key or "") or None

    if isinstance(value, dict):
        for field in ("summary", "subject", "title", "name", "note"):
            candidate = value.get(field)
            if isinstance(candidate, str) and candidate.strip():
                cleaned = _strip_markdown_summary(candidate)
                if cleaned:
                    return _truncate_summary(cleaned)
        try:
            cleaned = _strip_markdown_summary(json.dumps(value, ensure_ascii=True))
        except Exception:
            cleaned = _strip_markdown_summary(str(value))
    elif isinstance(value, list):
        if value and all(isinstance(entry, str) for entry in value[:4]):
            cleaned = _strip_markdown_summary(", ".join(value[:4]))
        else:
            cleaned = f"{len(value)} items"
    else:
        cleaned = _strip_markdown_summary(value)

    if not cleaned:
        return _humanize_key(key or "") or None

    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
    preferred = first_sentence or cleaned
    return _truncate_summary(preferred)


def _derive_context_summary(item: dict[str, Any]) -> str | None:
    for field in ("summary", "summary_snippet", "value_preview", "note_preview", "subject"):
        candidate = item.get(field)
        if isinstance(candidate, str) and candidate.strip():
            cleaned = _strip_markdown_summary(candidate)
            if cleaned:
                return _truncate_summary(cleaned)
    if "value" in item:
        return _derive_summary_from_value(item.get("value"), key=item.get("key"))
    key = item.get("key")
    if isinstance(key, str) and key.strip():
        return _truncate_summary(_humanize_key(key))
    return None


def _string_field(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _number_field(value: dict[str, Any], *keys: str) -> int | float | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return candidate
        if isinstance(candidate, str) and candidate.strip():
            try:
                parsed = float(candidate.strip())
            except ValueError:
                continue
            if parsed < 0:
                continue
            return int(parsed) if parsed.is_integer() else parsed
    return None


def _context_entry_label(count: int) -> str:
    return "context entry" if count == 1 else "context entries"


def _declared_upload_content_type(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    if "/" in cleaned:
        return cleaned
    normalized = _NORMALIZE_ALIAS_RE.sub("_", cleaned)
    return _DECLARED_UPLOAD_CONTENT_TYPES.get(normalized)


def _is_durable_context_item(item: dict[str, Any]) -> bool:
    storage = str(item.get("storage") or "").strip().lower()
    key = str(item.get("key") or "").strip().lower()
    return storage in {"vault", "intelligence", "workspace_intelligence"} or key.startswith("vault/")


def _content_type_from_name_or_url(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        cleaned = value.strip().lower().split("?", 1)[0].split("#", 1)[0]
        for suffix, content_type in _FILENAME_CONTENT_TYPES.items():
            if cleaned.endswith(suffix):
                return content_type
    return None


def _clean_content_type(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower().split(";", 1)[0].strip()
    return cleaned or None


def _resolve_content_type_hint(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    normalized = _NORMALIZE_ALIAS_RE.sub("_", cleaned.lower())
    if normalized in _RENDER_AS_CONTENT_TYPES:
        return _RENDER_AS_CONTENT_TYPES[normalized]
    declared = _declared_upload_content_type(cleaned)
    if declared:
        return _clean_content_type(declared)
    return None


def _sniff_inline_render_content_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    prefix = value[:_INLINE_RENDER_PREFIX_SCAN_CHARS].lstrip().lower()
    # match() intentionally anchors inference to the first non-whitespace token.
    is_doctype_html = _DOCTYPE_HTML_RE.match(prefix) is not None
    is_html_root = _HTML_ROOT_RE.match(prefix) is not None
    if is_doctype_html or is_html_root:
        return "text/html"
    return None


def _filename_from_context_key(key: str | None) -> str | None:
    if not key:
        return None
    for token in reversed(_CONTEXT_KEY_SEGMENT_RE.split(key)):
        candidate = token.strip().split("?", 1)[0].split("#", 1)[0]
        if candidate and _content_type_from_name_or_url(candidate):
            return candidate
    return None


def _context_key_has_known_extension(key: str | None) -> bool:
    return _filename_from_context_key(key) is not None


def _renderable_filename_for_context_key(key: str | None, content_type: str) -> str:
    existing_filename = _filename_from_context_key(key)
    if existing_filename:
        existing_content_type = _content_type_from_name_or_url(existing_filename)
        suffix = _CONTENT_TYPE_EXTENSIONS.get(content_type, ".txt")
        if existing_content_type and existing_content_type != content_type:
            for existing_suffix in _FILENAME_CONTENT_TYPE_SUFFIXES:
                if existing_filename.lower().endswith(existing_suffix):
                    return f"{existing_filename[: -len(existing_suffix)]}{suffix}"
            # Keep the original filename if a known content type was detected
            # but its concrete suffix cannot be rewritten safely.
        return existing_filename

    suffix = _CONTENT_TYPE_EXTENSIONS.get(content_type, ".txt")
    fallback_name = "context"
    if key:
        for token in reversed(_CONTEXT_KEY_SEGMENT_RE.split(key)):
            candidate = token.strip().split("?", 1)[0].split("#", 1)[0]
            if candidate:
                fallback_name = candidate
                break
    return f"{fallback_name}{suffix}"


def _inline_file_upload_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    normalized = content_type.strip().lower()
    return normalized.startswith("text/") or normalized in {
        "application/json",
        "application/xml",
        "image/svg+xml",
    }


def _string_is_upload_locator(value: str) -> bool:
    # URL/data locators should stay as fetchable uploads even when their key
    # ends in an inline-capable extension such as .svg, .json, or .md. Lowering
    # here deliberately accepts uppercase schemes such as DATA:.
    cleaned = value.strip().lower()
    return cleaned.startswith(("http://", "https://", "data:"))


def _declared_inline_text_upload(normalized_declared: str) -> bool:
    # Keep plain "text" values as context notes, but let agents declare
    # markup/document payloads without needing a temporary URL first.
    return normalized_declared in {"json", "markdown", "md", "svg", "xml"}


def _content_from_structured_value(value: dict[str, Any], content_type: str) -> Any:
    if content_type == "text/html" and isinstance(value.get("html"), str):
        return value["html"]
    for key in _GENERIC_STRUCTURED_CONTENT_VALUE_KEYS:
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate
    return None


def _prepare_context_set_value(
    key: str,
    value: Any,
    *,
    content_type: str | None = None,
    render_as: str | None = None,
) -> tuple[Any, str | None, str | None]:
    content_type_hint = content_type.strip() if content_type else None
    render_as_hint = render_as.strip() if render_as else None
    requested_content_type = _resolve_content_type_hint(content_type_hint)
    if content_type_hint and requested_content_type is None:
        return value, None, None
    if requested_content_type is None:
        requested_content_type = _resolve_content_type_hint(render_as_hint)
    inferred_content_type = None
    if requested_content_type is None and not _context_key_has_known_extension(key):
        inferred_content_type = _sniff_inline_render_content_type(value)

    effective_content_type = requested_content_type or inferred_content_type
    if not effective_content_type or not _inline_file_upload_content_type(effective_content_type):
        return value, None, None

    filename = _renderable_filename_for_context_key(key, effective_content_type)
    if isinstance(value, str):
        if _string_is_upload_locator(value):
            return (
                {
                    "type": "file_upload",
                    "filename": filename,
                    "content_type": effective_content_type,
                    "url": value.strip(),
                },
                effective_content_type,
                filename,
            )
        return (
            {
                "type": "file_upload",
                "filename": filename,
                "content_type": effective_content_type,
                "content": value,
            },
            effective_content_type,
            filename,
        )

    if isinstance(value, dict):
        normalized_upload = _normalize_file_upload_value(value)
        if normalized_upload:
            normalized_upload.setdefault("filename", filename)
            if requested_content_type is not None:
                normalized_upload["content_type"] = effective_content_type
            else:
                normalized_upload.setdefault("content_type", effective_content_type)
            return (
                normalized_upload,
                normalized_upload.get("content_type"),
                normalized_upload.get("filename"),
            )

        inline_content = _content_from_structured_value(value, effective_content_type)
        if inline_content is not None:
            preserved = {
                item_key: item_value
                for item_key, item_value in value.items()
                if item_key not in _CONTEXT_SET_FILE_UPLOAD_DRAIN_KEYS
            }
            preserved.update(
                {
                    "type": "file_upload",
                    "filename": filename,
                    "content_type": effective_content_type,
                    "content": inline_content,
                }
            )
            return preserved, effective_content_type, filename

    return value, None, None


def _normalize_file_upload_from_context_key(key: str | None, value: Any) -> dict[str, Any] | None:
    filename = _filename_from_context_key(key)
    content_type = _content_type_from_name_or_url(filename)
    if not filename or not content_type:
        return None

    if isinstance(value, str):
        normalized = {
            "type": "file_upload",
            "filename": filename,
            "content_type": content_type,
        }
        if _string_is_upload_locator(value):
            normalized["url"] = value.strip()
            return normalized
        if _inline_file_upload_content_type(content_type):
            normalized["content"] = value
            return normalized
        return None

    if not isinstance(value, dict):
        return None

    if content_type == "application/json" and not any(
        alias_key in value for alias_key in _EXPLICIT_FILE_UPLOAD_METADATA_KEYS
    ):
        return {
            "type": "file_upload",
            "filename": filename,
            "content_type": content_type,
            "content": json.dumps(value, ensure_ascii=False, indent=2),
        }

    upload_candidate = dict(value)
    upload_candidate.setdefault("type", "file_upload")
    upload_candidate.setdefault("filename", filename)
    upload_candidate.setdefault("content_type", content_type)
    return _normalize_file_upload_value(upload_candidate)


def _normalize_file_upload_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(parsed, str):
            return None
        return _normalize_file_upload_value(parsed)

    if not isinstance(value, dict):
        return None

    declared = _string_field(value, "type", "kind", "artifact_type", "artifact kind")
    normalized_declared = _NORMALIZE_ALIAS_RE.sub("_", (declared or "").strip().lower())
    filename = _string_field(value, "filename", "file_name", "file name", "fileName", "name", "title")
    url = _string_field(
        value,
        "url",
        "href",
        "download_url",
        "download url",
        "downloadUrl",
        "file_url",
        "file url",
        "fileUrl",
        "resource_url",
        "resource url",
        "resourceUrl",
        "src",
        "data_url",
        "data url",
        "dataUrl",
    )
    explicit_content_type = _string_field(
        value,
        "content_type",
        "content type",
        "contentType",
        "mime_type",
        "mime type",
        "mimeType",
        "resource_mime_type",
        "resource mime type",
        "resourceMimeType",
        "media_type",
        "media type",
        "mediaType",
        "file_type",
        "file type",
        "fileType",
    )
    declared_content_type = _declared_upload_content_type(declared)
    content_type = (
        explicit_content_type
        or _content_type_from_name_or_url(filename, url)
        or declared_content_type
    )
    attachment_id = _string_field(value, "attachment_id", "attachment id", "attachmentId")
    size = _number_field(
        value,
        "size",
        "size_bytes",
        "size bytes",
        "sizeBytes",
        "byte_size",
        "byte size",
        "byteSize",
        "bytes",
    )
    inline_binary_payload = any(
        key in value
        for key in (
            "base64",
            "content base64",
            "content_base64",
            "contentBase64",
            "data base64",
            "data_base64",
            "dataBase64",
            "data url",
            "data_url",
            "dataUrl",
        )
    )
    inline_text_payload = any(
        key in value
        for key in (
            "body",
            "content",
            "data",
            "text",
        )
    )
    explicitly_file = normalized_declared in {"file_upload", "file", "attachment"}
    declared_text_type = normalized_declared in {"text", "txt"}
    has_url_locator = url is not None and not declared_text_type
    has_content_backed_upload = content_type is not None and any(
        field is not None for field in (filename, attachment_id, size)
    )
    has_content_backed_upload = has_content_backed_upload or (content_type is not None and has_url_locator)
    has_attachment_backed_upload = attachment_id is not None and any(
        field is not None for field in (filename, url, content_type)
    )
    has_inline_upload = content_type is not None and (
        inline_binary_payload
        or (
            inline_text_payload
            and (
                explicitly_file
                or explicit_content_type is not None
                or _declared_inline_text_upload(normalized_declared)
            )
        )
    )
    if not explicitly_file and not (
        has_content_backed_upload or has_attachment_backed_upload or has_inline_upload
    ):
        return None

    normalized = {
        key: item for key, item in value.items() if key not in _FILE_UPLOAD_ALIAS_KEYS
    }
    normalized["type"] = "file_upload"
    if filename:
        normalized["filename"] = filename
    if content_type:
        normalized["content_type"] = content_type
    if url:
        normalized["url"] = url
    if attachment_id:
        normalized["attachment_id"] = attachment_id
    if size is not None:
        normalized["size"] = size
    return normalized


_LIST_METADATA_STRING_MAX_CHARS = 512


def _serialized_len(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def _list_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _metadata_string(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) <= _LIST_METADATA_STRING_MAX_CHARS:
        return text
    return text[: _LIST_METADATA_STRING_MAX_CHARS - 1].rstrip() + "..."


def _project_file_upload_metadata(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    projected: dict[str, Any] = {}
    for field in ("filename", "content_type", "size", "attachment_id"):
        field_value = value.get(field)
        if field_value not in (None, "", [], {}):
            projected[field] = field_value
    for field in ("url_kind", "url_size", "url_omitted", "url_truncated"):
        field_value = value.get(field)
        if field_value not in (None, "", [], {}):
            projected[field] = field_value

    url = value.get("url")
    if isinstance(url, str) and url.strip():
        clean_url = url.strip()
        projected["url_size"] = len(clean_url)
        if clean_url.startswith("data:"):
            projected["url_kind"] = "data"
            projected["url_omitted"] = True
        else:
            projected_url = _metadata_string(clean_url)
            if projected_url:
                projected["url"] = projected_url
                if projected_url != clean_url:
                    projected["url_truncated"] = True
    return projected or None


def _structured_value_content_type(value: Any) -> str | None:
    """Best-effort content_type for a bare structured artifact value (e.g.
    {"type": "html", "html": ...}) that is NOT a file_upload. Lets list rows be
    labeled by kind even though the value body is omitted; without it such items
    fall through to the generic "ctx" label. Returns None for plain/unknown values."""
    if not isinstance(value, dict):
        return None
    declared = value.get("content_type") or value.get("contentType")
    if isinstance(declared, str) and declared.strip():
        return declared.strip().lower().split(";")[0].strip()
    type_field = str(value.get("type") or "").strip().lower()
    type_map = {
        "html": "text/html",
        "svg": "image/svg+xml",
        "markdown": "text/markdown",
        "md": "text/markdown",
        "json": "application/json",
    }
    if type_field in type_map:
        return type_map[type_field]
    if isinstance(value.get("html"), str):
        return "text/html"
    if isinstance(value.get("svg"), str):
        return "image/svg+xml"
    return None


def _project_list_item(item: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {"key": item.get("key", "")}
    file_upload = _project_file_upload_metadata(item.get("file_upload"))
    if file_upload:
        projected["file_upload"] = file_upload
    for field in (
        "summary",
        "created_at",
        "updated_at",
        "expires_at",
        "topic",
        "ttl",
        "agent_name",
        "source",
        "storage",
        "subject",
        "note_preview",
        "value_preview",
        "value_omitted",
        "value_size",
        "value_type",
        "content_type",
    ):
        value = item.get(field)
        if value not in (None, "", [], {}):
            projected[field] = value
    if "value" in item:
        raw_value = item.get("value")
        projected["value_type"] = _list_value_type(raw_value)
        projected["value_size"] = _serialized_len(raw_value)
        projected["value_omitted"] = True
        if not projected.get("value_preview"):
            preview = _derive_summary_from_value(raw_value, key=item.get("key"))
            if preview:
                projected["value_preview"] = preview
    # Bare structured artifacts (e.g. {"type":"html"}) carry no file_upload
    # metadata; surface their content_type so the explorer labels them by kind
    # (HTML/SVG/JSON/...) instead of the generic "ctx" catch-all.
    if "file_upload" not in projected and not projected.get("content_type"):
        structured_ct = _structured_value_content_type(item.get("value"))
        if structured_ct:
            projected["content_type"] = structured_ct
    if not projected.get("updated_at") and projected.get("created_at"):
        projected["updated_at"] = projected["created_at"]
    return projected


async def _enrich_context_list_result(
    result: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    items = _extract_context_items(result)
    keys = _extract_context_keys(result, items)

    if items and not all(_is_key_only_item(item) for item in items):
        projected_items = [_project_list_item(item) for item in items]
        return {
            **result,
            "items": projected_items,
            "keys": keys or [item.get("key", "") for item in projected_items if item.get("key")],
            "count": result.get("count", len(projected_items)),
        }

    if not keys:
        return result

    async def fetch_one(key: str) -> dict[str, Any]:
        try:
            detail = await api_request(
                "GET",
                f"/api/v1/context/{quote(key, safe='')}",
                ctx["jwt"],
                params=user_request_space_params(ctx),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            detail_items = _extract_context_items(detail)
            if detail_items:
                return _project_list_item(detail_items[0])
        except Exception:
            logger.warning("Failed to enrich context list item %s", key, exc_info=True)
        return {"key": key, "summary": _truncate_summary(_humanize_key(key))}

    fetched = await asyncio.gather(*(fetch_one(key) for key in keys))
    return {
        **result,
        "items": fetched,
        "keys": keys,
        "count": result.get("count", len(keys)),
    }


def _extract_context_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = result.get("items")
    if isinstance(items, list):
        return [_normalize_context_item(item) for item in items if isinstance(item, dict)]

    results = result.get("results")
    if isinstance(results, list):
        return [_normalize_context_item(item) for item in results if isinstance(item, dict)]

    entries = result.get("entries")
    if isinstance(entries, list):
        return [_normalize_context_item(entry) for entry in entries if isinstance(entry, dict)]

    context_map = result.get("context")
    if isinstance(context_map, dict):
        mapped_items = [
            _normalize_context_item({"value": value}, default_key=key)
            for key, value in context_map.items()
        ]
        if mapped_items:
            return mapped_items

    # Single entry result
    if isinstance(result.get("key"), str):
        return [_normalize_context_item(result)]

    mapped_items: list[dict[str, Any]] = []
    for key, value in result.items():
        if key in _CONTEXT_RESULT_META_KEYS:
            continue
        mapped_items.append(_normalize_context_item({"value": value}, default_key=key))
    if mapped_items:
        return mapped_items

    return []


def _extract_context_keys(result: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    keys = result.get("keys")
    if isinstance(keys, list):
        extracted: list[str] = []
        for entry in keys:
            if isinstance(entry, str) and entry.strip():
                extracted.append(entry.strip())
            elif isinstance(entry, dict):
                key = entry.get("key")
                if isinstance(key, str) and key.strip():
                    extracted.append(key.strip())
        if extracted:
            return extracted

    if items:
        extracted = [item["key"] for item in items if isinstance(item.get("key"), str) and item["key"].strip()]
        if extracted:
            return extracted

    context_map = result.get("context")
    if isinstance(context_map, dict):
        return [key for key in context_map.keys() if isinstance(key, str) and key.strip()]

    key = result.get("key")
    if isinstance(key, str) and key.strip():
        return [key.strip()]

    return []


def _normalize_key_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw_values = value if isinstance(value, list) else [value]
    keys: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, str):
            continue
        key = raw.strip()
        if not key or key in seen:
            continue
        keys.append(key)
        seen.add(key)
    return keys


def _context_limit_param(limit: int | str | float | None) -> int:
    if limit is None:
        return 50
    try:
        limit_int = int(limit)
    except (TypeError, ValueError, OverflowError):
        return 50
    return max(1, min(limit_int, _CONTEXT_TERMINATE_MAX_LIMIT))


def _context_query_params(
    *,
    prefix: str | None = None,
    topic: str | None = None,
    limit: int | str | float | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if isinstance(prefix, str) and prefix.strip():
        params["prefix"] = prefix.strip()
    if isinstance(topic, str) and topic.strip():
        params["topic"] = topic.strip()
    params["limit"] = _context_limit_param(limit)
    return params


async def _delete_context_keys(ctx: dict[str, Any], target_keys: list[str]) -> tuple[list[str], list[str]]:
    semaphore = asyncio.Semaphore(_CONTEXT_TERMINATE_DELETE_CONCURRENCY)

    async def delete_one(target_key: str) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            result = await api_request(
                "DELETE",
                f"/api/v1/context/{quote(target_key, safe='')}",
                ctx["jwt"],
                params=user_request_space_params(ctx),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            return target_key, result

    terminated_keys: list[str] = []
    failed_keys: list[str] = []
    # asyncio.gather preserves input order, so returned keys line up with the
    # caller's selector order even though deletion itself runs concurrently.
    delete_results = await asyncio.gather(
        *(delete_one(target_key) for target_key in target_keys),
        return_exceptions=True,
    )
    for fallback_key, result in zip(target_keys, delete_results):
        if isinstance(result, Exception):
            failed_keys.append(fallback_key)
            continue
        target_key, delete_result = result
        if delete_result.get("error"):
            failed_keys.append(target_key)
        else:
            terminated_keys.append(target_key)
    return terminated_keys, failed_keys


def _is_key_only_item(item: dict[str, Any]) -> bool:
    if not isinstance(item.get("key"), str) or not item["key"].strip():
        return False
    meaningful_fields = {
        field
        for field, value in item.items()
        if field != "key" and value not in (None, "", [], {})
    }
    return not meaningful_fields


def _context_widget_result(
    result: dict[str, Any],
    action: str,
    *,
    selected_key: str | None = None,
    notice: dict[str, Any] | None = None,
    permission_bundle: dict[str, Any] | None = None,
) -> ToolResult:
    items = _extract_context_items(result)
    keys = _extract_context_keys(result, items)
    if action == "list" and keys and all(_is_key_only_item(item) for item in items):
        items = []
    elif action == "list" and items:
        items = [_project_list_item(item) for item in items]

    total_count = result.get("count", len(items))
    # Cap is request-aware: a caller (the explorer UI) may ask for a larger page
    # up to _CONTEXT_LIST_HARD_MAX; with no explicit limit (agent calls) it stays
    # at the lean default. Bodies are already omitted, so larger pages stay small.
    requested_limit = result.get("limit")
    try:
        display_cap = int(requested_limit) if requested_limit is not None else _CONTEXT_LIST_MAX_ITEMS
    except (TypeError, ValueError):
        display_cap = _CONTEXT_LIST_MAX_ITEMS
    display_cap = max(1, min(display_cap, _CONTEXT_LIST_HARD_MAX))
    list_capped = False
    if len(items) > display_cap:
        items = items[:display_cap]
        list_capped = True
    if len(keys) > display_cap:
        keys = keys[:display_cap]
        list_capped = True

    structured = {
        "kind": "context",
        "version": 2,
        "action": action,
        "count": total_count,
        "items": items,
        "keys": keys,
    }
    if action == "list":
        structured["returned"] = len(items)
        for field in ("limit", "offset", "has_more"):
            if field in result:
                structured[field] = result[field]
        if "has_more" not in structured:
            try:
                offset_value = int(structured.get("offset") or 0)
                structured["has_more"] = offset_value + len(items) < int(total_count)
            except (TypeError, ValueError):
                pass
    if list_capped:
        structured["list_capped"] = True
        if notice is None:
            notice = build_notice(
                f"Showing {len(items)} of {total_count}. Narrow with prefix/topic, "
                "or read one key with get.",
                severity="warning",
                code="context_list_capped",
            )
    if selected_key:
        structured["selected_key"] = selected_key
    for field in ("error", "detail", "requested_space_id", "current_space_id"):
        if field in result:
            structured[field] = result[field]
    if notice:
        structured["notice"] = notice
    for field in ("terminated_keys", "terminated_count", "failed_keys"):
        if field in result:
            structured[field] = result[field]
    if permission_bundle:
        structured["space_context"] = permission_bundle.get("space_context")
        structured["permissions"] = permission_bundle.get("permissions")
    runtime_meta = {
        "ax_context_contract": "context.v1",
    }
    if isinstance(result, dict):
        runtime_meta["ax_context_raw_keys"] = sorted(
            key for key in result.keys() if isinstance(key, str)
        )[:20]
    return widget_tool_result(
        "context",
        content=structured,
        structured_content=structured,
        meta=runtime_meta,
    )


def _ctx_with_permission_space(
    ctx: dict[str, Any],
    permission_bundle: dict[str, Any] | None,
) -> dict[str, Any]:
    if ctx.get("space_id") or not isinstance(permission_bundle, dict):
        return ctx
    space_context = permission_bundle.get("space_context")
    if not isinstance(space_context, dict):
        return ctx
    resolved_space_id = space_context.get("id")
    if not isinstance(resolved_space_id, str) or not resolved_space_id.strip():
        return ctx
    return {**ctx, "space_id": resolved_space_id.strip()}


def _explicit_space_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _context_space_switch_required_result(
    action: str,
    *,
    requested_space_id: str,
    current_space_id: str,
    permission_bundle: dict[str, Any] | None,
) -> ToolResult:
    detail = (
        "Agent-authored context writes use the agent's current operating space. "
        "The context tool will not retarget a write through its space_id argument."
    )
    message = (
        f"{detail} Switch first with spaces(action=\"switch\", "
        "space_id=<target_space_id>) before writing context there."
    )
    return _context_widget_result(
        {
            "count": 0,
            "items": [],
            "keys": [],
            "error": "context_space_switch_required",
            "detail": detail,
            "requested_space_id": requested_space_id,
            "current_space_id": current_space_id,
        },
        action,
        notice=build_notice(
            message,
            severity="error",
            code="context_space_switch_required",
        ),
        permission_bundle=permission_bundle,
    )


def register_context_tool(mcp: FastMCP):

    @mcp.tool(
        annotations=bounded_write_annotations(destructive=False),
        app=tool_app_config("context"),
        meta={**tool_meta("context"), **_context_action_forms()},
        output_schema=tool_output_schema("context"),
    )
    async def context(
        action: Annotated[
            Literal[
                "get",
                "set",
                "list",
                "delete",
                "approve",
                "decline",
                "terminate",
                "promote",
                "create",
                "act",
                "patch",
            ],
            Field(
                description=(
                    "Context action. Use get/set/list/delete for ephemeral "
                    "key-value state, approve/decline for pending entries, "
                    "terminate for bulk cleanup, promote to copy a key into "
                    "the vault or governed catalog, and create/act/patch for "
                    "governed catalog entries."
                )
            ),
        ],
        key: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Context key. Required for get, set, delete, approve, "
                    "decline, and promote. A file extension drives artifact "
                    "rendering (e.g. chart.svg)."
                ),
            ),
        ] = None,
        keys: Annotated[
            Optional[list[str]],
            Field(
                default=None,
                description="For action=terminate, multiple keys to remove at once.",
            ),
        ] = None,
        value: Annotated[
            JsonValue,
            Field(
                default=None,
                description=(
                    "For action=set, the value to store: inline text/JSON, "
                    "HTML/SVG content, a URL or data URL, or a dict with "
                    "html/content/body/text/data. Also accepted as an alias "
                    "for artifact_value on create."
                ),
            ),
        ] = None,
        content_type: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Explicit MIME type for set (e.g. text/html, image/svg+xml); "
                    "takes precedence over render_as."
                ),
            ),
        ] = None,
        render_as: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Rendering hint for set when the key has no extension: "
                    "text, json, xml, svg, or html."
                ),
            ),
        ] = None,
        ttl: Annotated[
            Optional[int],
            Field(
                default=None,
                description=(
                    "For action=set, seconds until the ephemeral entry expires; "
                    "omit for the default working-copy lifetime."
                ),
            ),
        ] = None,
        topic: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Topic label to group related keys; filters list/terminate.",
            ),
        ] = None,
        artifact_type: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    'Catalog artifact type for create/promote(to="catalog"), '
                    'e.g. "review.html" or "report.md".'
                ),
            ),
        ] = None,
        agent_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Filter list results to entries written by one agent.",
            ),
        ] = None,
        prefix: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Key prefix filter for list, or bulk selector for terminate.",
            ),
        ] = None,
        limit: Annotated[
            Optional[int],
            Field(default=None, ge=1, description="Maximum entries to return for list."),
        ] = None,
        offset: Annotated[
            Optional[int],
            Field(default=None, ge=0, description="Result offset for paging list results."),
        ] = None,
        tier: Annotated[
            Optional[Literal["catalog"]],
            Field(
                default=None,
                description=(
                    'Storage tier for reads: omit for ephemeral/vault; "catalog" '
                    "makes get/list read the governed catalog."
                ),
            ),
        ] = None,
        to: Annotated[
            Optional[Literal["vault", "catalog"]],
            Field(
                default=None,
                description=(
                    "For action=promote, destination tier: vault (default) for "
                    "a durable versioned copy, or catalog for a governed entry."
                ),
            ),
        ] = None,
        catalog_entry_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Governed catalog entry ID. Required for act/patch and "
                    'tier="catalog" get.'
                ),
            ),
        ] = None,
        include_content: Annotated[
            bool,
            Field(
                default=False,
                description="For catalog reads, include full artifact content in the result.",
            ),
        ] = False,
        context_key: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Source context key when creating a catalog entry from "
                    "existing context; prefer passing key on promote."
                ),
            ),
        ] = None,
        title: Annotated[
            Optional[str],
            Field(
                default=None,
                description='Human-readable title. Required for create and promote(to="catalog").',
            ),
        ] = None,
        description: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Longer description stored on a governed catalog entry.",
            ),
        ] = None,
        summary: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Short summary line stored on a governed catalog entry.",
            ),
        ] = None,
        shelf: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Catalog shelf/collection name used to organize entries.",
            ),
        ] = None,
        artifact_kind: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Override for the artifact kind; defaults to the last "
                    'segment of artifact_type (e.g. "review.html" -> "html").'
                ),
            ),
        ] = None,
        action_set_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Action-set ID attached to a catalog entry on create.",
            ),
        ] = None,
        content_ref: Annotated[
            Optional[str],
            Field(
                default=None,
                description="External content reference for create when the payload is not inline.",
            ),
        ] = None,
        sha256: Annotated[
            Optional[str],
            Field(
                default=None,
                description="SHA-256 of referenced content for create (pair with size_bytes).",
            ),
        ] = None,
        size_bytes: Annotated[
            Optional[int],
            Field(
                default=None,
                ge=0,
                description="Size in bytes of referenced content for create.",
            ),
        ] = None,
        artifact_value: Annotated[
            JsonValue,
            Field(
                default=None,
                description=(
                    "Inline artifact payload for create; value is accepted as "
                    "an alias when this is omitted."
                ),
            ),
        ] = None,
        state_value: Annotated[
            JsonValue,
            Field(
                default=None,
                description="Initial mutable state payload stored alongside a created artifact.",
            ),
        ] = None,
        pinned: Annotated[
            Optional[bool],
            Field(
                default=None,
                description="Pin or unpin a catalog entry in listings.",
            ),
        ] = None,
        status: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Catalog entry status filter for list, or status value on writes.",
            ),
        ] = None,
        owner_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Filter catalog list results by owning principal ID.",
            ),
        ] = None,
        updated_after: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Filter catalog list results to entries updated after this ISO 8601 time.",
            ),
        ] = None,
        action_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=act, the catalog action to invoke on the entry.",
            ),
        ] = None,
        payload: Annotated[
            Optional[dict[str, Any]],
            Field(
                default=None,
                description="For action=act, arguments passed to the invoked catalog action.",
            ),
        ] = None,
        base_artifact_version_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For act/patch, the artifact version the change is based on.",
            ),
        ] = None,
        base_state_version_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For act/patch, the state version the change is based on.",
            ),
        ] = None,
        idempotency_key: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Client-supplied key to make catalog writes safe to retry.",
            ),
        ] = None,
        source_lane: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Originating lane/workflow label recorded with a patch proposal.",
            ),
        ] = None,
        patch_type: Annotated[
            Optional[str],
            Field(
                default=None,
                description="For action=patch, the kind of patch being proposed.",
            ),
        ] = None,
        content_hash: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Hash of the patched content for integrity verification.",
            ),
        ] = None,
        patch: Annotated[
            Optional[dict[str, Any]],
            Field(
                default=None,
                description="For action=patch, the patch document to propose against the entry.",
            ),
        ] = None,
        space_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Debug/test override only; the active space is resolved "
                    "from the authenticated session, and a write mismatch is "
                    "rejected."
                ),
            ),
        ] = None,
        # DI params (hidden from MCP schema):
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> ToolResult | dict:
        """Bounded, permission-gated shared context store for authenticated Commonflame workspace state.

        The tool reads and writes only first-party Commonflame context/catalog state. It
        does not call external systems, deploy, rotate credentials, or perform
        open-world destructive operations. delete/decline/terminate remove only
        ephemeral TTL-bound working copies and are gated behind a can_delete
        permission; durable vault copies and governed catalog entries cannot be
        deleted through this tool.

        Shared context has three tiers: ephemeral, vault, and governed catalog.

        Space is always implied from the agent session. For writes, space_id is a
        debug/test override only and a mismatch is rejected (switch spaces first).
        For reads (including tier="catalog"), space_id is ignored — the space
        always resolves from the session.

        Actions and their key parameters:
          get        key (tier="catalog" + catalog_entry_id reads a governed entry)
          set        key, value, ttl, topic, content_type/render_as. The key's file
                     extension drives artifact rendering (chart.svg -> SVG); inline
                     HTML is inferred, and explicit content_type/render_as can make
                     no-extension keys render as text/*, JSON, XML, or SVG artifacts
                     when the value is inline content, a URL/data URL locator, or a
                     dict with html/content/body/text/data. content_type takes
                     precedence over render_as; artifact_type applies only to
                     governed catalog writes.
          list       prefix, topic, limit, offset (tier="catalog" lists governed entries)
          delete     key
          approve    key; re-saves the current entry without ttl so a pending
                     document/entry is kept permanently. Backend GET errors are
                     preserved instead of being reported as missing entries.
          decline    key; removes a pending document/entry and refreshes the list
          terminate  keys | key | prefix | topic
          promote    key, to ("vault" default | "catalog"). Pass the source key as
                     `key` (same as vault promote); to="catalog" also takes
                     title/artifact_type.
          create     title, artifact_type, artifact_value (or sha256+size_bytes).
                     Catalog-only: unlike `set` it always requires title+artifact_type;
                     `value` is accepted as an alias for artifact_value.
          act        catalog_entry_id, action_id, base_artifact_version_id,
                     base_state_version_id
          patch      catalog_entry_id, patch, base_artifact_version_id,
                     base_state_version_id

        Plain ephemeral usage needs only key/value/ttl/topic; the governed-catalog
        params apply only to create/act/patch, promote(to=catalog), tier=catalog reads.
        """
        ctx = extract_agent_context(token, request)
        permission_bundle = await resolve_space_scoped_permissions(ctx, "context")
        ctx = _ctx_with_permission_space(ctx, permission_bundle)
        requested_space_id = _explicit_space_id(space_id)
        current_space_id = _explicit_space_id(ctx.get("space_id"))
        if (
            action in _CONTEXT_WRITE_ACTIONS
            and ctx.get("principal_type") == "agent"
            and requested_space_id
            and requested_space_id != current_space_id
        ):
            return _context_space_switch_required_result(
                action,
                requested_space_id=requested_space_id,
                current_space_id=current_space_id,
                permission_bundle=permission_bundle,
            )

        # CTX-ARTIFACTS-002: governed-catalog tier. Space is already resolved
        # above (space-is-implied), so the catalog handler never needs space_id.
        catalog_dispatch_action: Optional[str] = None
        if action in ("create", "act", "patch"):
            catalog_dispatch_action = {
                "create": "create",
                "act": "invoke_action",
                "patch": "propose_patch",
            }[action]
        elif action == "promote" and to == "catalog":
            catalog_dispatch_action = "create_from_context"
        elif action in ("list", "get") and tier == "catalog":
            catalog_dispatch_action = action
        if catalog_dispatch_action is not None:
            return await handle_catalog_action(
                catalog_dispatch_action,
                ctx=ctx,
                permission_bundle=permission_bundle,
                catalog_entry_id=catalog_entry_id,
                include_content=include_content,
                context_key=context_key if context_key is not None else key,
                title=title,
                description=description,
                summary=summary,
                shelf=shelf,
                artifact_type=artifact_type,
                artifact_kind=artifact_kind,
                action_set_id=action_set_id,
                content_ref=content_ref,
                sha256=sha256,
                size_bytes=size_bytes,
                artifact_value=artifact_value if artifact_value is not None else value,
                state_value=state_value,
                pinned=pinned,
                status=status,
                owner_id=owner_id,
                updated_after=updated_after,
                limit=limit,
                action_id=action_id,
                payload=payload,
                base_artifact_version_id=base_artifact_version_id,
                base_state_version_id=base_state_version_id,
                idempotency_key=idempotency_key,
                source_lane=source_lane,
                patch_type=patch_type,
                content_hash=content_hash,
                patch=patch,
            )

        if action == "get":
            if not key:
                return {"error": "'key' required for get action"}
            result = await api_request(
                "GET",
                f"/api/v1/context/{quote(key, safe='')}",
                ctx["jwt"],
                params=user_request_space_params(ctx),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            return _context_widget_result(
                result,
                action,
                selected_key=key,
                permission_bundle=permission_bundle,
            )

        elif action == "set":
            if not (
                permissions_allow(permission_bundle, "can_create")
                or permissions_allow(permission_bundle, "can_update")
            ):
                refreshed = await _enrich_context_list_result(
                    await api_request(
                        "GET",
                        "/api/v1/context",
                        ctx["jwt"],
                        params=user_request_space_params(
                            ctx,
                            {"limit": limit if limit is not None else 50},
                        ),
                        agent_name=ctx["agent_name"],
                        agent_id=ctx.get("agent_id"),
                        space_id=ctx["space_id"],
                    ),
                    ctx,
                )
                return _context_widget_result(
                    refreshed,
                    "list",
                    notice=build_notice(
                        blocked_reason(permission_bundle),
                        severity="error",
                        code="context_set_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )
            if not key or value is None:
                return {"error": "'key' and 'value' required for set action"}
            prepared_value, prepared_content_type, prepared_filename = _prepare_context_set_value(
                key,
                value,
                content_type=content_type,
                render_as=render_as,
            )
            payload = {"key": key, "value": prepared_value}
            if ttl is not None:
                payload["ttl"] = ttl
            if topic is not None:
                payload["topic"] = topic
            result = await api_request(
                "POST",
                "/api/v1/context",
                ctx["jwt"],
                json_data=user_request_space_payload(ctx, payload),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if result.get("error"):
                return _context_widget_result(result, action)
            detail = await api_request(
                "GET",
                f"/api/v1/context/{quote(key, safe='')}",
                ctx["jwt"],
                params=user_request_space_params(ctx),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            return _context_widget_result(
                detail,
                "get",
                selected_key=key,
                notice=(
                    build_notice(
                        f'Saved context "{key}" as a renderable {prepared_content_type} '
                        f"artifact ({prepared_filename}).",
                        code="context_saved_renderable",
                    )
                    if prepared_content_type and prepared_filename
                    else build_notice(f'Saved context "{key}".', code="context_saved")
                ),
                permission_bundle=permission_bundle,
            )

        elif action == "list":
            params = {}
            if prefix:
                params["prefix"] = prefix
            if topic:
                params["topic"] = topic
            params["limit"] = limit if limit is not None else 50
            if offset is not None:
                params["offset"] = offset
            result = await api_request(
                "GET",
                "/api/v1/context",
                ctx["jwt"],
                params=user_request_space_params(ctx, params),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            result.setdefault("limit", params["limit"])
            result.setdefault("offset", params.get("offset", 0))
            result = await _enrich_context_list_result(result, ctx)
            return _context_widget_result(result, action, permission_bundle=permission_bundle)

        elif action == "delete":
            if not permissions_allow(permission_bundle, "can_delete"):
                detail = (
                    await api_request(
                        "GET",
                        f"/api/v1/context/{quote(key, safe='')}",
                        ctx["jwt"],
                        params=user_request_space_params(ctx),
                        agent_name=ctx["agent_name"],
                        agent_id=ctx.get("agent_id"),
                        space_id=ctx["space_id"],
                    )
                    if key
                    else {}
                )
                return _context_widget_result(
                    detail,
                    "get" if key else "list",
                    selected_key=key,
                    notice=build_notice(
                        blocked_reason(permission_bundle),
                        severity="error",
                        code="context_delete_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )
            if not key:
                return {"error": "'key' required for delete action"}
            result = await api_request(
                "DELETE",
                f"/api/v1/context/{quote(key, safe='')}",
                ctx["jwt"],
                params=user_request_space_params(ctx),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if result.get("error"):
                return _context_widget_result(result, action)
            params = {}
            if prefix:
                params["prefix"] = prefix
            if topic:
                params["topic"] = topic
            params["limit"] = limit if limit is not None else 50
            refreshed = await api_request(
                "GET",
                "/api/v1/context",
                ctx["jwt"],
                params=user_request_space_params(ctx, params),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            refreshed = await _enrich_context_list_result(refreshed, ctx)
            return _context_widget_result(
                refreshed,
                "list",
                notice=build_notice(f'Deleted context "{key}".', code="context_deleted"),
                permission_bundle=permission_bundle,
            )

        elif action == "approve":
            if not (
                permissions_allow(permission_bundle, "can_create")
                or permissions_allow(permission_bundle, "can_update")
            ):
                detail = (
                    await api_request(
                        "GET",
                        f"/api/v1/context/{quote(key, safe='')}",
                        ctx["jwt"],
                        params=user_request_space_params(ctx),
                        agent_name=ctx["agent_name"],
                        agent_id=ctx.get("agent_id"),
                        space_id=ctx["space_id"],
                    )
                    if key
                    else {}
                )
                return _context_widget_result(
                    detail,
                    "get" if key else "list",
                    selected_key=key,
                    notice=build_notice(
                        blocked_reason(permission_bundle),
                        severity="error",
                        code="context_approve_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )
            if not key:
                return {"error": "'key' required for approve action"}
            current = await api_request(
                "GET",
                f"/api/v1/context/{quote(key, safe='')}",
                ctx["jwt"],
                params=user_request_space_params(ctx),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if current.get("error"):
                return _context_widget_result(
                    {"key": key, **current},
                    "get",
                    selected_key=key,
                    notice=build_notice(
                        f'Cannot approve "{key}": failed to fetch current entry.',
                        severity="error",
                        code="context_approve_fetch_failed",
                    ),
                    permission_bundle=permission_bundle,
                )
            existing_items = _extract_context_items(current)
            if not existing_items:
                return _context_widget_result(
                    current,
                    "get",
                    selected_key=key,
                    notice=build_notice(
                        f'Cannot approve "{key}": entry not found.',
                        severity="error",
                        code="context_approve_missing",
                    ),
                    permission_bundle=permission_bundle,
                )
            existing_item = existing_items[0]
            existing_value = (
                current.get("value")
                if isinstance(current.get("key"), str) and "value" in current
                else existing_item.get("value")
            )
            existing_topic = existing_item.get("topic")
            payload = {"key": key, "value": existing_value}
            if existing_topic:
                payload["topic"] = existing_topic
            # Intentionally omit `ttl` — backend treats POST without ttl as
            # "no expiry", which is exactly what approval should produce.
            result = await api_request(
                "POST",
                "/api/v1/context",
                ctx["jwt"],
                json_data=user_request_space_payload(ctx, payload),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if result.get("error"):
                return _context_widget_result(result, action)
            detail = await api_request(
                "GET",
                f"/api/v1/context/{quote(key, safe='')}",
                ctx["jwt"],
                params=user_request_space_params(ctx),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            return _context_widget_result(
                detail,
                "get",
                selected_key=key,
                notice=build_notice(
                    f'Approved "{key}" — kept permanently.',
                    code="context_approved",
                ),
                permission_bundle=permission_bundle,
            )

        elif action == "decline":
            if not permissions_allow(permission_bundle, "can_delete"):
                detail = (
                    await api_request(
                        "GET",
                        f"/api/v1/context/{quote(key, safe='')}",
                        ctx["jwt"],
                        params=user_request_space_params(ctx),
                        agent_name=ctx["agent_name"],
                        agent_id=ctx.get("agent_id"),
                        space_id=ctx["space_id"],
                    )
                    if key
                    else {}
                )
                return _context_widget_result(
                    detail,
                    "get" if key else "list",
                    selected_key=key,
                    notice=build_notice(
                        blocked_reason(permission_bundle),
                        severity="error",
                        code="context_decline_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )
            if not key:
                return {"error": "'key' required for decline action"}
            result = await api_request(
                "DELETE",
                f"/api/v1/context/{quote(key, safe='')}",
                ctx["jwt"],
                params=user_request_space_params(ctx),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if result.get("error"):
                return _context_widget_result(result, action)
            params = {}
            if prefix:
                params["prefix"] = prefix
            if topic:
                params["topic"] = topic
            params["limit"] = limit if limit is not None else 50
            refreshed = await api_request(
                "GET",
                "/api/v1/context",
                ctx["jwt"],
                params=user_request_space_params(ctx, params),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            refreshed = await _enrich_context_list_result(refreshed, ctx)
            return _context_widget_result(
                refreshed,
                "list",
                notice=build_notice(f'Declined context "{key}".', code="context_declined"),
                permission_bundle=permission_bundle,
            )

        elif action == "terminate":
            explicit_keys = _normalize_key_list(keys)
            if not explicit_keys and key:
                explicit_keys = _normalize_key_list([key])
            # The selector/list refresh path intentionally defaults to the
            # normal 50-item context page. Callers can request up to
            # _CONTEXT_TERMINATE_MAX_LIMIT when they need a larger cleanup batch.
            selector_params = _context_query_params(prefix=prefix, topic=topic, limit=limit)
            has_selector = bool(explicit_keys or selector_params.get("prefix") or selector_params.get("topic"))
            if not has_selector:
                return {"error": "'keys', 'key', 'prefix', or 'topic' required for terminate action"}

            if not permissions_allow(permission_bundle, "can_delete"):
                refreshed = await api_request(
                    "GET",
                    "/api/v1/context",
                    ctx["jwt"],
                    params=user_request_space_params(ctx, selector_params),
                    agent_name=ctx["agent_name"],
                    agent_id=ctx.get("agent_id"),
                    space_id=ctx["space_id"],
                )
                refreshed = await _enrich_context_list_result(refreshed, ctx)
                return _context_widget_result(
                    refreshed,
                    "terminate",
                    notice=build_notice(
                        blocked_reason(permission_bundle),
                        severity="error",
                        code="context_terminate_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )

            target_keys = explicit_keys
            if not target_keys:
                # Prefix/topic termination intentionally snapshots candidates
                # before delete; entries added after this list call are not in
                # the batch and survive until a later terminate run.
                candidates = await api_request(
                    "GET",
                    "/api/v1/context",
                    ctx["jwt"],
                    params=user_request_space_params(ctx, selector_params),
                    agent_name=ctx["agent_name"],
                    agent_id=ctx.get("agent_id"),
                    space_id=ctx["space_id"],
                )
                candidate_items = _extract_context_items(candidates)
                target_keys = _extract_context_keys(candidates, candidate_items)

            if not target_keys:
                refreshed = await api_request(
                    "GET",
                    "/api/v1/context",
                    ctx["jwt"],
                    params=user_request_space_params(ctx, selector_params),
                    agent_name=ctx["agent_name"],
                    agent_id=ctx.get("agent_id"),
                    space_id=ctx["space_id"],
                )
                refreshed = await _enrich_context_list_result(refreshed, ctx)
                refreshed["terminated_keys"] = []
                refreshed["terminated_count"] = 0
                return _context_widget_result(
                    refreshed,
                    "terminate",
                    notice=build_notice(
                        "No context entries matched the terminate selector.",
                        severity="warning",
                        code="context_terminate_no_candidates",
                    ),
                    permission_bundle=permission_bundle,
                )

            terminated_keys, failed_keys = await _delete_context_keys(ctx, target_keys)

            # Explicit-key termination has no natural list selector, so refresh
            # against the caller's current prefix/topic scope when present and
            # otherwise return the default current context page.
            refreshed = await api_request(
                "GET",
                "/api/v1/context",
                ctx["jwt"],
                params=user_request_space_params(ctx, selector_params),
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            refreshed = await _enrich_context_list_result(refreshed, ctx)
            refreshed["terminated_keys"] = terminated_keys
            refreshed["terminated_count"] = len(terminated_keys)
            if failed_keys:
                refreshed["failed_keys"] = failed_keys
            notice = (
                build_notice(
                    f"Terminated {len(terminated_keys)} {_context_entry_label(len(terminated_keys))}; {len(failed_keys)} failed.",
                    severity="error",
                    code="context_terminate_partial",
                )
                if failed_keys
                else build_notice(
                    f"Terminated {len(terminated_keys)} {_context_entry_label(len(terminated_keys))}.",
                    code="context_terminated",
                )
            )
            return _context_widget_result(
                refreshed,
                "terminate",
                notice=notice,
                permission_bundle=permission_bundle,
            )

        elif action == "promote":
            if not (
                permissions_allow(permission_bundle, "can_create")
                or permissions_allow(permission_bundle, "can_update")
            ):
                detail = (
                    await api_request(
                        "GET",
                        f"/api/v1/context/{quote(key, safe='')}",
                        ctx["jwt"],
                        params=user_request_space_params(ctx),
                        agent_name=ctx["agent_name"],
                        agent_id=ctx.get("agent_id"),
                        space_id=ctx["space_id"],
                    )
                    if key
                    else {}
                )
                return _context_widget_result(
                    detail,
                    "get" if key else "list",
                    selected_key=key,
                    notice=build_notice(
                        blocked_reason(permission_bundle),
                        severity="error",
                        code="context_promote_blocked",
                    ),
                    permission_bundle=permission_bundle,
                )
            if not key:
                return {"error": "'key' required for promote action"}
            if not ctx.get("space_id"):
                return _context_widget_result(
                    {"key": key, "error": "Missing active space context"},
                    "promote",
                    selected_key=key,
                    notice=build_notice(
                        "Promote requires an active workspace context.",
                        severity="error",
                        code="context_promote_missing_space",
                    ),
                    permission_bundle=permission_bundle,
                )

            payload: dict[str, Any] = {"key": key}
            if artifact_type:
                payload["artifact_type"] = artifact_type
            if agent_id:
                payload["agent_id"] = agent_id
            result = await api_request(
                "POST",
                f"/api/v1/spaces/{quote(str(ctx['space_id']), safe='')}/intelligence/promote",
                ctx["jwt"],
                json_data=payload,
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if result.get("error"):
                return _context_widget_result(
                    {"key": key, **result},
                    "promote",
                    selected_key=key,
                    permission_bundle=permission_bundle,
                )
            promoted = {
                "key": result.get("key", key),
                "storage": "vault",
                "status": result.get("status"),
                "value": {"vault": result},
            }
            return _context_widget_result(
                {"items": [promoted], "keys": [key], "count": 1, **result},
                "promote",
                selected_key=key,
                notice=build_notice(f'Promoted context "{key}" to vault.', code="context_promoted"),
                permission_bundle=permission_bundle,
            )

        else:
            return {"error": f"Unknown action: {action}. Available: get, set, list, delete, approve, decline, terminate, promote"}
