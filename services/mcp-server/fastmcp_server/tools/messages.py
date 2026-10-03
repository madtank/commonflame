"""Messages tool for FastMCP server.

Provides message sending, checking, and management.
ALL write operations route through backend API (ROUTER-001).
Uses CurrentAccessToken() DI to forward backend-issued JWTs.

Send returns immediately after delivery by default. Set wait=True to wait
for a reply on the delivered message, no routing escape hatch required.
Messages normally route through Waystation first so Waystation can answer directly or route
the work. Use messages(check) to see later replies.

MCP Tasks (MSG-ASYNC-001): messages(send, wait=true) supports optional
MCP Task execution — clients that pass task:{} get a task ID immediately
and can poll for the result. Clients without task support get synchronous
behavior (backward compatible).

See specs/MSG-ASYNC-001/spec.md for the full async messaging design.
"""

import logging
import re
import time
from collections import Counter
from datetime import timedelta
from typing import Annotated, Any, Literal, Optional

from fastmcp import FastMCP
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.server.auth import AccessToken
from fastmcp.utilities.tasks import TaskConfig
from fastmcp.dependencies import Progress
from fastmcp.tools import ToolResult
from pydantic import Field
from starlette.requests import Request

from fastmcp_server.api_client import (
    api_request,
    extract_agent_context,
    is_message_receipt,
    wait_for_reply,
)
from fastmcp_server.resources.inbox_notifications import notify_message_inboxes
from fastmcp_server.mcp_ui import (
    build_notice,
    build_tool_output,
    bounded_write_annotations,
    tool_app_config,
    tool_meta,
    tool_output_schema,
    widget_tool_result,
)

logger = logging.getLogger(__name__)
AX_CHECKIN_MAX_RECENT_MESSAGES = 8
INBOX_SUMMARY_ADDRESSED_TO_ME_LIMIT = 5
INBOX_SUMMARY_TEXT_LIMIT = 96

WORKING_ON_PATTERNS = [
    re.compile(r"\bworking on\s+(.+)", re.IGNORECASE),
    re.compile(
        r"\b(?:fixing|building|implementing|updating|reviewing|shipping|investigating|writing)\s+(.+)",
        re.IGNORECASE,
    ),
]

LOOKING_FOR_PATTERNS = [
    re.compile(r"\blooking for\s+(.+)", re.IGNORECASE),
    re.compile(r"\bneed help with\s+(.+)", re.IGNORECASE),
    re.compile(r"\bhelp with\s+(.+)", re.IGNORECASE),
    re.compile(r"\bcan you\s+(.+)", re.IGNORECASE),
    re.compile(r"\bwho can\s+(.+)", re.IGNORECASE),
    re.compile(r"\bneed\s+(.+)", re.IGNORECASE),
]

TOPIC_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "just",
    "have",
    "about",
    "your",
    "their",
    "there",
    "what",
    "when",
    "where",
    "will",
    "would",
    "should",
    "could",
    "please",
    "thanks",
    "thank",
    "need",
    "help",
    "check",
    "message",
    "messages",
    "reply",
    "working",
    "looking",
    "want",
    "like",
    "have",
    "been",
    "they",
    "them",
    "we",
    "you",
    "our",
    "out",
    "not",
    "but",
    "are",
    "was",
    "has",
    "had",
    "all",
    "too",
    "now",
    "can",
    "who",
    "how",
    "why",
    "use",
    "using",
}


def _normalize_send_action(action: str, content: str | None) -> tuple[str, str | None]:
    """Collapse legacy Waystation ingress into the canonical messages(send) path."""
    if action != "ask_ax":
        return action, content

    if not content:
        return "ask_ax", content

    stripped = content.lstrip()
    if stripped.lower().startswith("@ax ") or stripped.lower().startswith("@ax\n"):
        return "send", content
    if stripped.lower().startswith("@ax"):
        return "send", content
    return "send", f"@aX {content}"


def _build_delivery(result: dict[str, Any]) -> dict[str, Any]:
    status = str(result.get("status") or "")
    sent = result.get("sent") if isinstance(result.get("sent"), dict) else None
    outbound = (
        result.get("outbound") if isinstance(result.get("outbound"), dict) else None
    )
    # timed_out means the message receipt was confirmed; only the optional
    # reply wait expired, so delivery should remain confirmed.
    confirmed = status in {"sent", "reply_received", "timed_out"} and bool(sent)
    state = "confirmed" if confirmed else "unconfirmed"
    if status == "reply_received":
        state = "working"
    evidence = "none"
    if confirmed and sent:
        evidence = str(sent.get("delivery_evidence") or "message_receipt")
    elif status == "accepted_unconfirmed":
        evidence = "transport_ack_only"
    elif outbound:
        evidence = str(outbound.get("delivery_evidence") or "transport_ack_only")
    return {
        "state": state,
        "status": status or ("sent" if confirmed else "accepted_unconfirmed"),
        "confirmed": confirmed,
        "requires_runtime_receipt": True,
        "evidence": evidence,
        "can_escalate": not confirmed,
        "target_state": "delivery_unconfirmed" if not confirmed else "delivered",
    }


def _coerce_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    coerced = _coerce_int(value, default=-1)
    return coerced if coerced >= 0 else None


def _sanitize_notification_text(
    value: Any, *, fallback: str | None = None
) -> str | None:
    if not isinstance(value, str):
        return fallback
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return fallback
    # Keep phone-notification summaries compact and avoid obvious secret leakage.
    cleaned = re.sub(
        r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization|bearer)\b\s*[:=]\s*\S+",
        r"\1=[redacted]",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+", "bearer [redacted]", cleaned)
    if len(cleaned) > INBOX_SUMMARY_TEXT_LIMIT:
        cleaned = cleaned[: INBOX_SUMMARY_TEXT_LIMIT - 3].rstrip() + "..."
    return cleaned


def _notification_sender(entry: dict[str, Any]) -> dict[str, Any]:
    sender = entry.get("sender") if isinstance(entry.get("sender"), dict) else {}
    sender_value = (
        sender.get("handle")
        or sender.get("name")
        or sender.get("username")
        or sender.get("login")
        or entry.get("sender")
        or entry.get("sender_name")
        or entry.get("handle")
        or entry.get("username")
        or entry.get("agent_name")
    )
    display_value = (
        sender.get("display_name")
        or sender.get("name")
        or entry.get("display_name")
        or entry.get("sender_display_name")
        or sender_value
    )
    return {
        "sender": _sanitize_notification_text(sender_value, fallback="unknown")
        or "unknown",
        "display_name": _sanitize_notification_text(display_value, fallback="unknown")
        or "unknown",
    }


def _notification_summary(entry: dict[str, Any], kind: str, reason: str) -> str:
    for key in ("summary", "notification_summary", "short_summary", "subject", "title"):
        summary = _sanitize_notification_text(entry.get(key))
        if summary:
            return summary
    if reason and reason != "direct":
        return reason.replace("_", " ").capitalize()
    if kind and kind != "message":
        return kind.replace("_", " ").capitalize()
    return "Direct message"


def _build_addressed_to_me(source: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries: Any = None
    for key in (
        "addressed_to_me",
        "direct_notifications",
        "direct_mentions",
        "notifications",
    ):
        candidate = source.get(key)
        if isinstance(candidate, list):
            raw_entries = candidate
            break
    if not isinstance(raw_entries, list):
        return []

    entries: list[dict[str, Any]] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        message_id = raw_entry.get("message_id") or raw_entry.get("id")
        if not message_id:
            continue
        kind = (
            _sanitize_notification_text(raw_entry.get("kind"), fallback="message")
            or "message"
        )
        reason = (
            _sanitize_notification_text(
                raw_entry.get("reason") or raw_entry.get("addressing_reason"),
                fallback="direct",
            )
            or "direct"
        )
        sender = _notification_sender(raw_entry)
        entries.append(
            {
                "message_id": str(message_id),
                "sender": sender["sender"],
                "display_name": sender["display_name"],
                "summary": _notification_summary(raw_entry, kind, reason),
                "kind": kind,
                "reason": reason,
            }
        )
        if len(entries) >= INBOX_SUMMARY_ADDRESSED_TO_ME_LIMIT:
            break
    return entries


def _build_inbox_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Return send-safe inbox awareness metadata with no message content.

    The backend may attach this metadata to a send acknowledgement as either
    ``inbox_summary`` or ``inbox``. Keep this shape additive and conservative:
    only counts/flags/timestamps are copied, and any raw message arrays or
    preview/content fields are intentionally ignored.
    """
    source: dict[str, Any] = {}
    for key in ("inbox_summary", "inbox", "inbox_awareness"):
        candidate = result.get(key)
        if isinstance(candidate, dict):
            source = candidate
            break

    unread_count = _coerce_int(
        source.get("unread_count", result.get("unread_count", 0))
    )
    pending_mentions_count = _coerce_optional_int(
        source.get("pending_mentions_count", source.get("mention_count"))
    )
    has_pending_mentions_value = source.get("has_pending_mentions")
    if has_pending_mentions_value is None:
        has_pending_mentions = bool(pending_mentions_count)
    else:
        has_pending_mentions = _coerce_bool(has_pending_mentions_value)

    addressed_to_me = _build_addressed_to_me(source)
    addressed_to_others = _coerce_optional_int(
        source.get(
            "addressed_to_others",
            source.get("addressed_to_others_count", source.get("ambient_count")),
        )
    )
    if addressed_to_others is None:
        addressed_to_others = max(unread_count - len(addressed_to_me), 0)

    summary: dict[str, Any] = {
        "unread_count": unread_count,
        "has_pending_mentions": has_pending_mentions,
        "oldest_unread_at": source.get("oldest_unread_at"),
        "addressed_to_me": addressed_to_me,
        "addressed_to_others": addressed_to_others,
    }
    if pending_mentions_count is not None:
        summary["pending_mentions_count"] = pending_mentions_count
    for key in ("latest_unread_at", "unread_thread_count", "source"):
        value = source.get(key)
        if value is not None:
            summary[key] = value
    return summary


def _messages_structured_content(result: dict[str, Any], action: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {"body": result}

    if result.get("error"):
        structured = build_tool_output(
            "message_error",
            2,
            "error",
            {
                "action": action,
                "error": result.get("error"),
                "detail": result.get("detail"),
            },
        )
        structured["notice"] = build_notice(
            str(
                result.get("detail") or result.get("error") or "Message action failed."
            ),
            severity="error",
            code="message_error",
        )
        return structured

    if action == "check":
        messages = result.get("messages")
        if not isinstance(messages, list):
            messages = []
        awareness = _build_messages_awareness(messages)
        return build_tool_output(
            "message_timeline",
            2,
            "empty" if not messages else "ready",
            {
                "action": action,
                "messages": messages,
                "threads": _build_message_threads(messages),
                "count": result.get("count", len(messages)),
                "unread_count": result.get("unread_count", 0),
                "session": result.get("session"),
                "space_name": result.get("space_name"),
                "agent_name": result.get("agent_name"),
                "reason": result.get("reason"),
                "awareness": awareness,
                "briefing": result.get("briefing"),
            },
        )

    if action == "draft":
        return build_tool_output(
            "message_draft",
            2,
            "ready",
            {
                "action": action,
                "messages": [],
                "count": 0,
                "unread_count": 0,
                "draft": {
                    "content": result.get("content"),
                    "reply_to": result.get("reply_to"),
                    "status": result.get("status"),
                    "hint": result.get("hint"),
                },
            },
        )

    if action in {"react", "edit", "delete"}:
        mutation: dict[str, Any] = {
            "type": {
                "react": "reaction",
                "edit": "edit",
                "delete": "delete",
            }[action],
            "message_id": result.get("message_id"),
            "reason": result.get("reason"),
            "policy": _message_mutation_policy(action),
        }
        if action == "react":
            mutation["emoji"] = result.get("emoji")
            mutation["target_preview"] = result.get("target")
        elif action == "edit":
            mutation["before"] = result.get("before")
            mutation["after"] = result.get("after")
        elif action == "delete":
            mutation["deleted"] = result.get("deleted", True)
            mutation["delete_mode"] = result.get("delete_mode", "soft")
            mutation["target_preview"] = result.get("target")
        structured = build_tool_output(
            "message_mutation",
            2,
            "ready",
            {
                "action": action,
                "mutation": mutation,
            },
        )
        notice = _message_mutation_notice(result, action)
        if notice:
            structured["notice"] = notice
        return structured

    timeline_messages: list[dict[str, Any]] = []
    sent = result.get("sent")
    if isinstance(sent, dict):
        timeline_messages.append(sent)
    reply = result.get("reply")
    if isinstance(reply, dict):
        timeline_messages.append(reply)
    thread_root_id = _message_thread_root(sent if isinstance(sent, dict) else reply)
    inbox_summary = _build_inbox_summary(result)

    structured = build_tool_output(
        "message_sent" if action == "send" else "message_timeline",
        2,
        "ready",
        {
            "messages": timeline_messages,
            "threads": _build_message_threads(timeline_messages),
            "count": len(timeline_messages),
            "unread_count": inbox_summary["unread_count"],
            "inbox_summary": inbox_summary,
            "action": action,
            "sent": sent if isinstance(sent, dict) else None,
            "reply": reply if isinstance(reply, dict) else None,
            "outbound": result.get("outbound")
            if isinstance(result.get("outbound"), dict)
            else None,
            "delivery": _build_delivery(result),
            "status": result.get("status"),
            "ack_ms": result.get("ack_ms"),
            "reply_ms": result.get("reply_ms"),
            "waited_seconds": result.get("waited_seconds"),
            "reply_wait": result.get("reply_wait")
            if isinstance(result.get("reply_wait"), dict)
            else None,
            "reply_match": result.get("reply_match"),
            "hint": result.get("hint"),
            "warning": result.get("warning"),
            "concierge": result.get("concierge"),
            "conversation_id": thread_root_id,
            "thread": {
                "thread_root_id": thread_root_id,
                "conversation_id": thread_root_id,
                "sent_message_id": _message_id(sent),
                "reply_message_id": _message_id(reply),
                "has_reply": isinstance(reply, dict),
                "message_count": len(timeline_messages),
            },
        },
    )
    notice = _message_mutation_notice(result, action)
    if notice:
        structured["notice"] = notice
    return structured


def _messages_summary(result: dict[str, Any], action: str) -> str:
    if action == "check":
        count = result.get("count")
        unread = result.get("unread_count", 0)
        messages = (
            result.get("messages") if isinstance(result.get("messages"), list) else []
        )
        awareness = _build_messages_awareness(messages)
        working = len(awareness.get("working_on") or [])
        looking = len(awareness.get("looking_for") or [])
        briefing = result.get("briefing") or {}
        if briefing.get("status") == "private_briefing" and briefing.get("content"):
            return f"Private inbox briefing ready. Loaded {count or 0} messages ({unread} unread)."
        return f"Loaded {count or 0} messages ({unread} unread). {working} active updates, {looking} active asks."

    if action == "draft":
        return "Draft prepared for review."

    if result.get("reply"):
        reply = result["reply"]
        author = (
            reply.get("handle")
            or reply.get("username")
            or reply.get("login")
            or reply.get("agent_name")
            or (
                reply.get("display_name")
                if isinstance(reply.get("display_name"), str)
                and " " not in reply.get("display_name")
                else None
            )
            or "reply"
        )
        return f"Reply received from @{author}."

    if result.get("ax_response"):
        return "Message sent. Waystation is still processing — check back shortly."

    if result.get("status") == "timed_out":
        waited = result.get("waited_seconds")
        if waited:
            return f"Message sent. No reply yet after {waited} seconds."
        return "Message sent. No reply yet."

    if result.get("status") == "accepted_unconfirmed":
        return "Delivery unconfirmed. The runtime did not emit a listener receipt, so scheduler follow-up may be needed."

    if result.get("sent"):
        return "Message sent."

    return "Message action completed."


def _public_actor_label(message: dict[str, Any]) -> str:
    candidates = [
        message.get("handle"),
        message.get("username"),
        message.get("login"),
        message.get("agent_name"),
        message.get("sender_name"),
        message.get("display_name")
        if isinstance(message.get("display_name"), str)
        and " " not in message.get("display_name")
        else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    sender_id = (
        message.get("sender_id") or message.get("agent_id") or message.get("user_id")
    )
    if sender_id:
        return str(sender_id)[:8]
    if message.get("sender_type") == "user":
        return "user"
    return "unknown"


def _message_id(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    value = message.get("id") or message.get("message_id")
    return str(value) if value else None


def _message_thread_root(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    value = (
        message.get("conversation_id")
        or message.get("parent_id")
        or message.get("id")
        or message.get("message_id")
    )
    return str(value) if value else None


def _message_timestamp(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    value = message.get("created_at") or message.get("timestamp") or ""
    return str(value)


def _message_excerpt(message: Any, limit: int = 96) -> str:
    if not isinstance(message, dict):
        return ""
    content = str(message.get("content") or message.get("text") or "").strip()
    if len(content) <= limit:
        return content
    return content[: limit - 3].rstrip() + "..."


def _message_preview(message: Any, *, limit: int = 220) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    preview: dict[str, Any] = {}
    message_id = _message_id(message)
    if message_id:
        preview["id"] = message_id
    conversation_id = _message_thread_root(message)
    if conversation_id:
        preview["conversation_id"] = conversation_id
    author = _public_actor_label(message)
    if author:
        preview["author"] = author
    content = _message_excerpt(message, limit=limit)
    if content:
        preview["content"] = content
    created_at = message.get("created_at") or message.get("timestamp")
    if created_at:
        preview["created_at"] = str(created_at)
    return preview or None


def _message_mutation_notice(
    result: dict[str, Any], action: str
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    if action == "send":
        if result.get("reply"):
            return build_notice("Reply received.", code="message_reply_received")
        if result.get("status") == "timed_out":
            waited = result.get("waited_seconds")
            suffix = f" after {waited} seconds" if waited else ""
            return build_notice(
                f"No reply yet{suffix}. The message was delivered.",
                code="message_reply_wait_timed_out",
            )
        if result.get("status") == "accepted_unconfirmed":
            return build_notice(
                "Delivery unconfirmed — no runtime listener receipt arrived.",
                code="message_delivery_unconfirmed",
            )
        return build_notice("Message sent.", code="message_sent")
    if action == "react":
        emoji = str(result.get("emoji") or "").strip()
        return build_notice(
            f"Reaction {emoji} added." if emoji else "Reaction added.",
            code="message_reacted",
        )
    if action == "edit":
        return build_notice("Message edited.", code="message_edited")
    if action == "delete":
        return build_notice("Message deleted.", code="message_deleted")
    return None


def _message_mutation_policy(action: str) -> dict[str, Any]:
    if action == "react":
        return {
            "toggle_supported": False,
            "removal_supported": False,
        }
    if action == "edit":
        return {
            "edit_window_minutes": 15,
            "max_edit_count": 5,
            "blocked_after_replies": True,
            "soft_badge_expected": True,
        }
    if action == "delete":
        return {
            "reason_required": True,
            "soft_delete_default": True,
            "hard_delete_exposed": False,
        }
    return {}


def _build_message_threads(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        thread_root_id = _message_thread_root(message)
        if not thread_root_id:
            continue

        bucket = buckets.setdefault(
            thread_root_id,
            {
                "thread_root_id": thread_root_id,
                "conversation_id": thread_root_id,
                "message_ids": [],
                "participants": [],
                "participant_set": set(),
                "latest_message_id": None,
                "latest_created_at": "",
                "latest_preview": "",
            },
        )

        message_id = _message_id(message)
        if message_id:
            bucket["message_ids"].append(message_id)

        actor = _public_actor_label(message)
        participant_set = bucket["participant_set"]
        if actor and actor not in participant_set:
            participant_set.add(actor)
            bucket["participants"].append(actor)

        created_at = _message_timestamp(message)
        if created_at >= bucket["latest_created_at"]:
            bucket["latest_created_at"] = created_at
            bucket["latest_message_id"] = message_id
            bucket["latest_preview"] = _message_excerpt(message)

    threads = []
    for thread in buckets.values():
        threads.append(
            {
                "thread_root_id": thread["thread_root_id"],
                "conversation_id": thread["conversation_id"],
                "message_ids": thread["message_ids"],
                "message_count": len(thread["message_ids"]),
                "participants": thread["participants"],
                "latest_message_id": thread["latest_message_id"],
                "latest_created_at": thread["latest_created_at"] or None,
                "latest_preview": thread["latest_preview"] or None,
            }
        )

    threads.sort(key=lambda item: item.get("latest_created_at") or "", reverse=True)
    return threads


def _clean_focus_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" .,:;!?-\n\t")
    cleaned = re.sub(r"^[@#][\w-]+\s*", "", cleaned)
    cleaned = re.sub(r"^(?:for|on|about|with)\s+", "", cleaned, flags=re.IGNORECASE)
    if len(cleaned) > 96:
        cleaned = cleaned[:93].rstrip() + "..."
    return cleaned


def _extract_focus_value(content: str, patterns: list[re.Pattern[str]]) -> str | None:
    for pattern in patterns:
        match = pattern.search(content)
        if match:
            value = _clean_focus_text(match.group(1))
            if value:
                return value
    return None


def _extract_topic_terms(messages: list[dict[str, Any]]) -> list[str]:
    counter: Counter[str] = Counter()
    for message in messages:
        content = str(message.get("content") or "")
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", content.lower()):
            if token in TOPIC_STOPWORDS:
                continue
            counter[token] += 1
    return [term for term, _ in counter.most_common(5)]


def _role_label(message: dict[str, Any]) -> str:
    candidates: list[Any] = [
        message.get("role"),
        message.get("specialization"),
        message.get("title"),
        message.get("sender_type"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "participant"


def _capability_summary(message: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        message.get("capability"),
        message.get("skill"),
        message.get("interest"),
        message.get("intent"),
        message.get("topic"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return _clean_focus_text(candidate)
    capabilities = message.get("capabilities")
    if isinstance(capabilities, list):
        values = [str(item).strip() for item in capabilities if str(item).strip()]
        if values:
            return ", ".join(values[:3])
    return None


def _build_messages_awareness(messages: list[dict[str, Any]]) -> dict[str, Any]:
    working_on: list[dict[str, Any]] = []
    looking_for: list[dict[str, Any]] = []
    collaborator_counts: Counter[str] = Counter()
    last_seen: dict[str, str] = {}
    collaborator_profiles: dict[str, dict[str, Any]] = {}
    seen_working: set[tuple[str, str]] = set()
    seen_looking: set[tuple[str, str]] = set()

    for message in messages:
        if not isinstance(message, dict):
            continue
        actor = _public_actor_label(message)
        collaborator_counts[actor] += 1
        created_at = str(message.get("created_at") or message.get("timestamp") or "")
        if created_at and (actor not in last_seen or created_at > last_seen[actor]):
            last_seen[actor] = created_at
            collaborator_profiles[actor] = {
                "role": _role_label(message),
                "capability": _capability_summary(message),
            }

        content = str(message.get("content") or message.get("text") or "").strip()
        if not content:
            continue

        working_summary = _extract_focus_value(content, WORKING_ON_PATTERNS)
        if working_summary:
            working_key = (actor, working_summary)
            if working_key not in seen_working and len(working_on) < 3:
                seen_working.add(working_key)
                working_on.append(
                    {
                        "actor": actor,
                        "summary": working_summary,
                        "message_id": message.get("id") or message.get("message_id"),
                        "created_at": created_at,
                    }
                )

        looking_summary = _extract_focus_value(content, LOOKING_FOR_PATTERNS)
        if not looking_summary and "?" in content:
            looking_summary = _clean_focus_text(content)
        if looking_summary:
            looking_key = (actor, looking_summary)
            if looking_key not in seen_looking and len(looking_for) < 3:
                seen_looking.add(looking_key)
                looking_for.append(
                    {
                        "actor": actor,
                        "summary": looking_summary,
                        "message_id": message.get("id") or message.get("message_id"),
                        "created_at": created_at,
                    }
                )

    collaborators = [
        {
            "actor": actor,
            "count": count,
            "last_seen": last_seen.get(actor),
            "role": collaborator_profiles.get(actor, {}).get("role", "participant"),
            "capability": collaborator_profiles.get(actor, {}).get("capability"),
            "working_on": next(
                (item["summary"] for item in working_on if item["actor"] == actor),
                None,
            ),
            "looking_for": next(
                (item["summary"] for item in looking_for if item["actor"] == actor),
                None,
            ),
        }
        for actor, count in collaborator_counts.most_common(4)
    ]
    topics = _extract_topic_terms(messages)

    query = ""
    if looking_for:
        lead = looking_for[0]
        query = f"Looking for {lead['summary']}"
    elif working_on:
        lead = working_on[0]
        query = f"Working on {lead['summary']}"
    elif topics:
        query = f"Looking for updates on {', '.join(topics[:2])}"
    elif collaborators:
        query = f"Checking on @{collaborators[0]['actor']}"

    return {
        "headline": query,
        "query": query,
        "working_on": working_on,
        "looking_for": looking_for,
        "active_collaborators": collaborators,
        "top_topics": topics,
    }


def _briefing_message_preview(message: dict[str, Any]) -> str:
    actor = _public_actor_label(message)
    role = _role_label(message)
    content = _clean_focus_text(
        str(message.get("content") or message.get("text") or "")
    )
    if len(content) > 110:
        content = content[:107].rstrip() + "..."
    return f"- @{actor} ({role}): {content}"


def _build_concierge_send_prompt(
    agent_name: str | None,
    content: str,
    status: str | None = None,
) -> str:
    """Wrap an outbound agent message so Waystation handles first-hop routing."""
    agent_label = agent_name or "unknown"
    parts = [
        f"@aX Concierge routing request from @{agent_label}.",
        "You are the required first hop for this MCP message unless the sender explicitly used bypass=true.",
        "Review the sender context, current tasks, notes, and recipient hints inside the message.",
        "Reply to the sender, then route or handle the request as appropriate.",
    ]
    if status:
        parts.append(f"Sender status: {status}")
    parts.extend(["", "Original message:", content])
    return "\n".join(parts)


def _build_ax_checkin_prompt(
    agent_name: str | None,
    result: dict[str, Any],
    reason: str,
    status: str | None = None,
) -> str:
    """Build the future private Waystation briefing prompt.

    SECURITY: Do not POST this prompt to /api/v1/messages. Check-in is a read
    operation; sending this through the public message pipeline turns inbox
    context into routable mentions and can wake every referenced agent.
    """
    agent_label = agent_name or "unknown"
    messages = result.get("messages")
    if not isinstance(messages, list):
        messages = []
    awareness = _build_messages_awareness(messages)
    recent_lines = [
        _briefing_message_preview(message)
        for message in messages[:AX_CHECKIN_MAX_RECENT_MESSAGES]
        if isinstance(message, dict) and (message.get("content") or message.get("text"))
    ]
    working = awareness.get("working_on") or []
    looking = awareness.get("looking_for") or []
    collaborators = awareness.get("active_collaborators") or []
    topics = awareness.get("top_topics") or []

    summary_lines = [
        f"Unread count: {result.get('unread_count', 0)}",
        f"Recent message count: {result.get('count', len(messages))}",
    ]
    if working:
        summary_lines.append(
            "Working on: "
            + "; ".join(
                f"@{item['actor']} -> {item['summary']}" for item in working[:3]
            )
        )
    if looking:
        summary_lines.append(
            "Looking for: "
            + "; ".join(
                f"@{item['actor']} -> {item['summary']}" for item in looking[:3]
            )
        )
    if collaborators:
        summary_lines.append(
            "Active collaborators: "
            + ", ".join(
                f"@{item['actor']} ({item.get('role') or 'participant'})"
                for item in collaborators[:4]
            )
        )
    if topics:
        summary_lines.append("Topics: " + ", ".join(topics[:5]))

    prompt_parts = [
        f"@aX Agent @{agent_label} is checking in on the system.",
        f"Check-in reason: {reason}",
        (
            f"Agent update: {status}"
            if status
            else "Agent update: No extra status provided beyond the reason above."
        ),
        "Give a custom inbox briefing tailored to this reason for checking messages.",
        "Use the reason to infer what the agent is working on, where they need help, whether they are blocked, any open questions, what support they can offer, whether they need assignments surfaced, and whether they are looking for more work.",
        "Please check tasks, notes, relevant agent information, and the inbox context below.",
        "Return a concise response with these sections only:",
        "1. What needs attention now",
        "2. Messages and tasks relevant to this check-in",
        "3. Help, blockers, or open questions",
        "4. Where this agent can contribute or pick up work",
        "5. Recommended next step",
        "If the reason is vague, say what missing context the agent should provide next time.",
        "Use handles only. Do not use full names. Keep it compact.",
        "",
    ]
    # Wrap the Context / Recent inbox blocks in a code fence so any @handles
    # inside (awareness summary, collaborator list, preview actors) do NOT
    # trigger backend mention fan-out. The backend MentionsService strips
    # fenced blocks before extracting @mentions, so only the explicit routing
    # line above (@aX + @{agent_label}) reaches dispatch. Without the fence,
    # every actor named in summary_lines / recent_lines would be re-resolved
    # by the backend and dispatched as a real @-mention, causing every recent
    # participant to wake up and produce a noisy five-section briefing they
    # were never supposed to generate.
    context_block: list[str] = ["```", "Context:", *summary_lines]
    if recent_lines:
        context_block.extend(["", "Recent inbox:", *recent_lines])
    context_block.append("```")
    prompt_parts.extend(context_block)
    return "\n".join(prompt_parts)


def _private_briefing_section(title: str, lines: list[str]) -> list[str]:
    body = lines if lines else ["- Nothing specific surfaced."]
    return [title, *body, ""]


def _build_private_checkin_briefing(
    ctx: dict[str, Any],
    inbox_result: dict[str, Any],
    *,
    reason: str,
    status: str | None = None,
) -> dict[str, Any]:
    """Return a private briefing without entering the message pipeline.

    This is intentionally deterministic until the backend exposes a true
    non-persisted concierge briefing endpoint. It preserves the MCP contract
    that check-ins return useful guidance while preventing public fan-out.
    User and agent principals both use this local read-only path; identity
    boundaries still matter for the inbox GET that produced inbox_result.
    """
    messages = inbox_result.get("messages")
    if not isinstance(messages, list):
        messages = []
    awareness = _build_messages_awareness(messages)
    agent_label = ctx.get("agent_name") or ctx.get("agent_id") or "unknown"
    unread = inbox_result.get("unread_count", 0)
    count = inbox_result.get("count", len(messages))

    headline = awareness.get("headline")
    attention = []
    if unread:
        attention.append(f"- {unread} unread message{'s' if unread != 1 else ''}.")
    if headline and headline != "No active asks detected":
        attention.append(f"- {headline}.")
    if reason:
        attention.append(f"- Check-in reason: {reason}")
    if status:
        attention.append(f"- Current status: {status}")

    relevant = []
    for item in (awareness.get("looking_for") or [])[:3]:
        relevant.append(f"- @{item['actor']}: {item['summary']}")
    for item in (awareness.get("working_on") or [])[:3]:
        relevant.append(f"- @{item['actor']}: {item['summary']}")

    blockers = []
    lowered_context = " ".join(part for part in (reason, status or "") if part).lower()
    if any(word in lowered_context for word in ("block", "stuck", "help", "waiting")):
        blockers.append(
            "- Your check-in mentions a possible blocker; surface the exact "
            "owner or missing artifact if it is not already in the inbox."
        )
    if awareness.get("looking_for"):
        blockers.append(
            "- Active asks are present in the recent inbox; review them before taking new work."
        )

    contributions = []
    for item in (awareness.get("active_collaborators") or [])[:4]:
        actor = item.get("actor")
        summary = item.get("looking_for") or item.get("working_on")
        if actor and summary:
            contributions.append(f"- Coordinate with @{actor}: {summary}")
        elif actor:
            contributions.append(f"- Check whether @{actor} needs follow-up.")

    next_step = []
    if awareness.get("looking_for"):
        first = awareness["looking_for"][0]
        next_step.append(f"- Start with @{first['actor']}: {first['summary']}")
    elif messages:
        next_step.append(
            "- Review the latest inbox thread and reply where you can unblock work."
        )
    else:
        next_step.append(
            "- No inbox work surfaced; continue current task or ask for a specific assignment."
        )

    content_lines = [
        f"Private check-in briefing for @{agent_label}",
        f"Loaded {count or 0} messages ({unread} unread).",
        "",
        *_private_briefing_section("1. What needs attention now", attention),
        *_private_briefing_section("2. Recent inbox activity", relevant),
        *_private_briefing_section("3. Help, blockers, or open questions", blockers),
        *_private_briefing_section(
            "4. Where this agent can contribute or pick up work", contributions
        ),
        *_private_briefing_section("5. Recommended next step", next_step),
    ]

    return {
        "status": "private_briefing",
        "source": "mcp_local_awareness",
        "private": True,
        "reason": reason,
        "content": "\n".join(content_lines).rstrip(),
    }


async def _get_private_checkin_briefing(
    ctx: dict[str, Any],
    inbox_result: dict[str, Any],
    *,
    reason: str,
    status: str | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    if progress:
        await progress.set_message("Building private inbox briefing")
    logger.info(
        "AX_CHECKIN_BRIEFING_PRIVATE principal_type=%s agent_id=%s agent_name=%s user_id=%s space_id=%s",
        ctx.get("principal_type"),
        ctx.get("agent_id"),
        ctx.get("agent_name"),
        ctx.get("user_id"),
        ctx.get("space_id"),
    )
    return _build_private_checkin_briefing(
        ctx,
        inbox_result,
        reason=reason,
        status=status,
    )


def _messages_widget_result(result: dict[str, Any], action: str) -> ToolResult:
    return widget_tool_result(
        "messages",
        content=result,
        structured_content=_messages_structured_content(result, action),
        meta={"ax_supervision": True},
    )


async def _fetch_message_detail(
    ctx: dict[str, Any],
    message_id: str,
) -> dict[str, Any] | None:
    result = await api_request(
        "GET",
        f"/api/v1/messages/{message_id}",
        ctx["jwt"],
        agent_name=ctx["agent_name"],
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
    )
    if not isinstance(result, dict) or result.get("error"):
        return None
    if isinstance(result.get("message"), dict):
        return result["message"]
    return result


async def _recover_sent_message(
    ctx: dict[str, Any],
    expected_content: str,
) -> dict[str, Any] | None:
    """Best-effort lookup when backend acknowledges a send without a receipt body."""
    recent = await api_request(
        "GET",
        "/api/v1/messages",
        ctx["jwt"],
        params={
            "limit": 10,
            "show_own_messages": True,
            "mark_read": False,
        },
        agent_name=ctx["agent_name"],
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
    )
    messages = recent.get("messages")
    if not isinstance(messages, list):
        return None
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("content") or "") == expected_content:
            return message
    return None


async def _recover_most_recent_own_message(
    ctx: dict[str, Any],
) -> dict[str, Any] | None:
    """Grab the most recent message sent by this agent as a last-resort recovery."""
    agent_name = ctx.get("agent_name", "")
    recent = await api_request(
        "GET",
        "/api/v1/messages",
        ctx["jwt"],
        params={
            "limit": 5,
            "show_own_messages": True,
            "mark_read": False,
        },
        agent_name=agent_name,
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
    )
    messages = recent.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    # Return the most recent message actually sent BY this agent
    for msg in messages:
        if not isinstance(msg, dict) or not is_message_receipt(msg):
            continue
        sender = (
            msg.get("agent_name")
            or msg.get("sender_name")
            or msg.get("handle")
            or msg.get("display_name", "")
        )
        if sender and sender.lower() == agent_name.lower():
            return msg
    return None


def register_messages_tool(mcp: FastMCP):
    @mcp.tool(
        task=TaskConfig(mode="optional", poll_interval=timedelta(seconds=2)),
        annotations=bounded_write_annotations(),
        app=tool_app_config("messages"),
        meta=tool_meta("messages"),
        output_schema=tool_output_schema("messages"),
    )
    async def messages(
        action: Annotated[
            Literal["check", "send", "ask_ax", "draft", "react", "edit", "delete"],
            Field(
                description=(
                    "Message action. Hosts should render this as a dropdown. "
                    "Use 'check' for Waystation check-ins, 'send' for concierge-routed "
                    "messages, 'ask_ax' as shorthand for sending to the concierge, "
                    "'draft' to prepare text, 'react' to add emoji, 'edit' to "
                    "modify a message, and 'delete' to remove one."
                )
            ),
        ],
        content: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Message body. Required for send, ask_ax, draft, react "
                    "(emoji), and edit."
                ),
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                default=10,
                ge=1,
                le=100,
                description="Maximum messages to fetch for check.",
            ),
        ] = 10,
        message_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Target message ID for edit or delete.",
            ),
        ] = None,
        reply_to: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Parent message ID for replies or reactions.",
            ),
        ] = None,
        mark_read: Annotated[
            bool,
            Field(default=True, description="Mark returned inbox messages as read."),
        ] = True,
        filter: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Optional backend inbox filter for check.",
            ),
        ] = None,
        show_own_messages: Annotated[
            bool,
            Field(
                default=False,
                description="Include the caller's own messages in check results.",
            ),
        ] = False,
        reason: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Required for check and delete. For check, explain why you "
                    "are checking now: what you are working on, blockers, help "
                    "needed, questions, support you can offer, assignments to "
                    "surface, or whether you want more work. For delete, supply "
                    "a short audit reason."
                ),
            ),
        ] = None,
        curate: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "When true, include a private tailored briefing in the MCP "
                    "tool response. This must not post a briefing request into "
                    "the shared message stream."
                ),
            ),
        ] = False,
        curate_max_wait: Annotated[
            int,
            Field(
                default=20,
                ge=1,
                le=300,
                description=(
                    "Deprecated no-op for check-in curation. Kept temporarily "
                    "for client compatibility until a non-persisted concierge "
                    "briefing endpoint replaces the local private briefing."
                ),
            ),
        ] = 20,
        wait: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "When true, wait for a reply on the delivered message "
                    "(up to max_wait seconds). Default false returns "
                    "immediately after delivery. The MCP client's request "
                    "timeout must be greater than max_wait."
                ),
            ),
        ] = False,
        max_wait: Annotated[
            int,
            Field(
                default=60,
                ge=1,
                le=3600,
                description=(
                    "Maximum seconds to wait for a send reply. Ensure the "
                    "client request timeout is greater than this value."
                ),
            ),
        ] = 60,
        bypass: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Advanced escape hatch. When true, skip Waystation first-hop "
                    "routing and deliver the original content directly. Not "
                    "required for waiting."
                ),
            ),
        ] = False,
        status: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Optional extra sender status for Waystation when checking in or "
                    "routing through concierge."
                ),
            ),
        ] = None,
        # DI params (hidden from MCP schema):
        progress: Progress = Progress(),
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> ToolResult | dict:
        """Send and check messages.

        Actions:
        - check: Check in with Waystation and provide a required `reason` describing why
          you're checking now: what you're working on, help needed, blockers,
          questions, support you can offer, assignments to surface, or whether
          you're looking for more work. Returns your raw inbox by default. Set
          curate=True to add a private briefing to the MCP tool response. The
          briefing must never create a message in the shared conversation.
        - send: Post a message through Waystation by default so Waystation can respond or route
          it. Returns instantly after delivery by default. Set wait=True to
          wait for a reply on the delivered message, up to max_wait seconds
          (default 60). Set bypass=True only when you explicitly want direct
          delivery with no concierge involvement.
        - draft: Propose message content for review before sending.
        - react: Add emoji reaction (requires reply_to + content as emoji).
        - edit: Modify a sent message (requires message_id + content). Uses the
          guarded backend service path.
        - delete: Soft-delete a message (requires message_id + reason). Uses the
          guarded backend service path.

        When called with MCP Task support (task:{}), send+wait=true returns
        a task ID immediately. Poll tasks/get for progress and result.
        """
        ctx = extract_agent_context(token, request)
        action, content = _normalize_send_action(action, content)
        scoped_agent_missing_space = token.claims.get(
            "tools_allowed"
        ) is not None and not ctx.get("space_id")

        if action == "check":
            if scoped_agent_missing_space:
                return _messages_widget_result(
                    {
                        "error": "Scoped agent token is missing space_id. Refusing messages(check) to avoid cross-space leakage."
                    },
                    action,
                )
            check_reason = (reason or status or "").strip()
            if not check_reason:
                # Widget refreshes and raw inbox reads should not be blocked on a
                # narrative reason. Default to a generic refresh reason and let
                # curate=False keeps widget refreshes quiet by default.
                check_reason = "widget refresh"
            params = {"limit": limit, "mark_read": mark_read}
            if filter:
                params["filter"] = filter
            if show_own_messages:
                params["show_own_messages"] = show_own_messages
            result = await api_request(
                "GET",
                "/api/v1/messages",
                ctx["jwt"],
                params=params,
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if not result.get("error"):
                result["reason"] = check_reason
            if curate and not result.get("error"):
                result["briefing"] = await _get_private_checkin_briefing(
                    ctx,
                    result,
                    reason=check_reason,
                    status=status,
                    progress=progress,
                )
            return _messages_widget_result(result, action)

        elif action == "send":
            if not ctx.get("agent_name"):
                return _messages_widget_result(
                    {
                        "error": "Agent identity required to send messages. "
                        "Connect via /mcp/agents/{name} or include X-Agent-Name header. "
                        "Sending without agent identity would impersonate the token owner."
                    },
                    action,
                )
            if scoped_agent_missing_space:
                return _messages_widget_result(
                    {
                        "error": "Scoped agent token is missing space_id. Refusing messages(send) to avoid cross-space delivery."
                    },
                    action,
                )
            if not content:
                return _messages_widget_result(
                    {"error": "'content' required for send/ask_ax action"},
                    action,
                )

            use_concierge = not bypass
            loop_guard_fired = False

            # Loop guard: when the concierge (Waystation) itself sends a message,
            # force bypass to prevent routing back through itself.
            sender_name = (ctx.get("agent_name") or "").lower().strip()
            if use_concierge and sender_name in {"ax", "ax-concierge"}:
                logger.warning(
                    "Loop guard: Waystation agent sent with bypass=false — auto-correcting to bypass=true"
                )
                use_concierge = False
                bypass = True
                loop_guard_fired = True

            # Always send the original content — the backend router handles
            # Waystation routing for ALL messages. Wrapping content with @aX prompt
            # triggers a backend code path that doesn't persist the message.
            payload = {"content": content}
            if reply_to:
                payload["parent_id"] = reply_to

            # Measure backend round-trip for ack latency (health signal)
            start = time.monotonic()
            result = await api_request(
                "POST",
                "/api/v1/messages",
                ctx["jwt"],
                json_data=payload,
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            ack_ms = round((time.monotonic() - start) * 1000)

            if result.get("error"):
                result["ack_ms"] = ack_ms
                return _messages_widget_result(result, action)

            sent_msg = result.get("message", result)
            recovery_warning = None

            # Transport-only acknowledgements ({status: ok, code: 200}) are
            # not enough to confirm listener delivery. A concrete runtime/backend
            # message receipt is required before the UI can show a working state.
            backend_ack = (
                isinstance(result, dict)
                and result.get("status") == "ok"
                and result.get("code") == 200
            )
            if not is_message_receipt(sent_msg) and not backend_ack:
                # Unexpected non-receipt — try recovery
                recovered_msg = await _recover_sent_message(ctx, content)
                if recovered_msg is None:
                    recovered_msg = await _recover_most_recent_own_message(ctx)
                if recovered_msg is not None:
                    sent_msg = recovered_msg
                    recovery_warning = (
                        "Backend returned an empty acknowledgement; recovered the "
                        "sent message from recent history."
                    )
                else:
                    sent_msg = {}
                    recovery_warning = (
                        "Backend returned an empty acknowledgement with no message "
                        "ID. Delivery could not be confirmed; verify space_id "
                        "routing and check recent messages."
                    )
            elif backend_ack and not is_message_receipt(sent_msg):
                sent_msg = {}
                recovery_warning = (
                    "Transport acknowledged the send, but no runtime listener receipt "
                    "arrived. Keep this delivery unconfirmed and allow scheduler escalation."
                )
            sent_id = sent_msg.get("id") or sent_msg.get("message_id")

            await progress.set_message(
                "Message delivered"
                if is_message_receipt(sent_msg)
                else "Delivery unconfirmed"
            )
            if is_message_receipt(sent_msg):
                try:
                    # Treat backend receipt content as canonical. An empty
                    # string is intentional and should not fall back to raw
                    # tool input or alternate display fields.
                    notification_content = sent_msg.get("content")
                    if notification_content is None:
                        notification_content = sent_msg.get("text")
                    if notification_content is None:
                        notification_content = content
                    await notify_message_inboxes(
                        sent_msg,
                        fallback_content=notification_content,
                    )
                except Exception:
                    logger.warning(
                        "Failed to notify inbox resource subscribers",
                        exc_info=True,
                    )

            concierge_meta = {
                "routed": use_concierge,
                "bypass_requested": bypass,
                "auto_corrected": loop_guard_fired,
            }

            # --- Delivery confirmed: build the base result ---
            delivery_status = (
                "sent" if is_message_receipt(sent_msg) else "accepted_unconfirmed"
            )
            base_result = {
                "sent": sent_msg if is_message_receipt(sent_msg) else None,
                "outbound": {
                    "content": content,
                    "reply_to": reply_to,
                    "delivery_evidence": "message_receipt"
                    if is_message_receipt(sent_msg)
                    else "transport_ack_only",
                },
                "status": delivery_status,
                "ack_ms": ack_ms,
                "concierge": concierge_meta,
                "inbox_summary": _build_inbox_summary(result),
                "_meta": {"ax_supervision": True},
            }
            if recovery_warning:
                base_result["warning"] = recovery_warning

            if not wait:
                return _messages_widget_result(base_result, action)

            # --- Wait phase: attach to the delivered message receipt ---
            await progress.set_message(
                f"Message delivered — waiting up to {max_wait}s for reply"
            )

            if sent_id:
                reply_result = await wait_for_reply(
                    ctx, sent_msg, sent_id, max_wait=max_wait, progress=progress
                )
            else:
                reply_result = {
                    "reply": None,
                    "status": "no_reply",
                    "waited_seconds": 0,
                }

            # Merge reply into base result
            if reply_result.get("reply"):
                base_result["reply"] = reply_result["reply"]
                base_result["status"] = "reply_received"
                base_result["reply_ms"] = reply_result.get("reply_ms")
                base_result["reply_match"] = reply_result.get("reply_match")
                base_result["reply_wait"] = {
                    "state": "reply_received",
                    "max_wait_seconds": max_wait,
                    "reply_ms": reply_result.get("reply_ms"),
                    "reply_match": reply_result.get("reply_match"),
                }
            else:
                # No reply within timeout — still confirm delivery
                waited_seconds = reply_result.get("waited_seconds", max_wait)
                base_result["status"] = "timed_out"
                base_result["waited_seconds"] = waited_seconds
                base_result["reply_wait"] = {
                    "state": base_result["status"],
                    "max_wait_seconds": max_wait,
                    "waited_seconds": waited_seconds,
                    "can_check_later": True,
                }
                base_result["hint"] = (
                    f"No reply yet after {waited_seconds} seconds. The message "
                    "was delivered; check back with "
                    "messages(action='check') in a moment."
                )

            return _messages_widget_result(base_result, action)

        elif action == "draft":
            if not content:
                return _messages_widget_result(
                    {"error": "'content' required for draft action"},
                    action,
                )
            result = {
                "action": "draft",
                "content": content,
                "reply_to": reply_to,
                "status": "ready_for_review",
                "hint": "Review the draft above, then call messages(action='send', ...) to post it.",
            }
            return _messages_widget_result(result, action)

        elif action == "react":
            if not reply_to or not content:
                return _messages_widget_result(
                    {
                        "error": "'reply_to' (message ID) and 'content' (emoji) required for react action"
                    },
                    action,
                )
            target = await _fetch_message_detail(ctx, reply_to)
            reaction_result = await api_request(
                "POST",
                f"/api/v1/messages/{reply_to}/reactions",
                ctx["jwt"],
                json_data={"emoji": content},
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if reaction_result.get("error"):
                return _messages_widget_result(reaction_result, action)
            return _messages_widget_result(
                {
                    "status": "reaction_added",
                    "message_id": reply_to,
                    "emoji": content,
                    "target": _message_preview(target),
                },
                action,
            )

        elif action == "edit":
            if not message_id or not content:
                return _messages_widget_result(
                    {"error": "'message_id' and 'content' required for edit action"},
                    action,
                )
            before = await _fetch_message_detail(ctx, message_id)
            edit_result = await api_request(
                "PUT",
                f"/api/messages/{message_id}",
                ctx["jwt"],
                json_data={"content": content},
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if edit_result.get("error"):
                return _messages_widget_result(edit_result, action)
            updated = (
                edit_result.get("message")
                if isinstance(edit_result.get("message"), dict)
                else edit_result
            )
            return _messages_widget_result(
                {
                    "status": "edited",
                    "message_id": message_id,
                    "reason": reason,
                    "before": _message_preview(before),
                    "after": _message_preview(updated),
                },
                action,
            )

        elif action == "delete":
            if not message_id:
                return _messages_widget_result(
                    {"error": "'message_id' required for delete action"},
                    action,
                )
            if not reason or not reason.strip():
                return _messages_widget_result(
                    {"error": "'reason' required for delete action"},
                    action,
                )
            target = await _fetch_message_detail(ctx, message_id)
            delete_result = await api_request(
                "DELETE",
                f"/api/messages/{message_id}",
                ctx["jwt"],
                params={"reason": reason.strip()},
                agent_name=ctx["agent_name"],
                agent_id=ctx.get("agent_id"),
                space_id=ctx["space_id"],
            )
            if delete_result.get("error"):
                return _messages_widget_result(delete_result, action)
            return _messages_widget_result(
                {
                    "status": "deleted",
                    "deleted": bool(delete_result.get("deleted", True)),
                    "message_id": message_id,
                    "reason": reason.strip(),
                    "delete_mode": "soft",
                    "target": _message_preview(target),
                },
                action,
            )

        else:
            return _messages_widget_result(
                {
                    "error": f"Unknown action: {action}. Available: check, send, draft, react, edit, delete"
                },
                action,
            )
