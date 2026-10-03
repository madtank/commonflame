"""Visibility rules for message rows that should not behave like conversation."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import and_, func, not_, or_

from ..models.message import Message

QUIET_EXIT_REASONS = {
    "no_reply",
    "no_reply_requested",
    "user_requested_silence",
    "not_best_fit",
    "agent_working",
    "ack",
}

# ── Ack message classification ──────────────────────────────────────────────
# Short agent responses with no substantive content that should be stored as
# signals rather than messages.  Prevents routing loops and inbox noise.

_ACK_PATTERNS = re.compile(
    r"^(?:"
    r"standing\s+by\.?"
    r"|acknowledged\.?"
    r"|confirmed\.?"
    r"|copy\.?"
    r"|roger\.?"
    r"|received\.?"
    r"|request\s+processed\.?"
    r"|understood\.?"
    r"|noted\.?"
    r"|all\s+(?:clear|green)\.?"
    r"|no\s+action\s+needed\.?"
    r"|nothing\s+(?:to\s+)?(?:action|report)\.?"
    r"|waiting\s+(?:for|on)\b.*"
    r"|standing\s+by\s+for\b.*"
    r"|\U0001f4ad\s*\*?thinking\.{0,3}\*?"  # 💭 thinking...
    r")$",
    re.IGNORECASE,
)

_ACK_MAX_WORDS = 12


def is_ack_message(content: str | None, is_agent: bool = False) -> bool:
    """Return True if content is a simple acknowledgment with no substance.

    Only applies to agent-authored messages.  Human messages are never
    reclassified — they reflect user intent.
    """
    if not is_agent or not content:
        return False
    text = content.strip()
    if not text:
        return False
    # Must be short
    if len(text.split()) > _ACK_MAX_WORDS:
        return False
    return bool(_ACK_PATTERNS.match(text))


def is_ui_only_no_reply_metadata(message_type: str | None, metadata: dict[str, Any] | None) -> bool:
    """Return True when a row is just a no-reply UI signal.

    The product contract allows storage/audit as an implementation detail, but
    these rows must not participate in message history, search, summaries, or
    agent-visible conversational context.
    """
    if (message_type or "").strip() != "agent_pause":
        return False
    if not isinstance(metadata, dict):
        return False

    reasons = {
        str(metadata.get("reason") or "").strip().lower(),
        str(metadata.get("reason_code") or "").strip().lower(),
        str(metadata.get("pause_reason") or "").strip().lower(),
        str(metadata.get("signal_kind") or "").strip().lower(),
    }
    if reasons & QUIET_EXIT_REASONS:
        return True

    return bool(metadata.get("signal_only"))


def ui_only_no_reply_clause():
    """SQLAlchemy clause for rows that should stay UI-only."""
    quiet_reason_checks = []
    for key in ("reason", "reason_code", "pause_reason", "signal_kind"):
        for reason in QUIET_EXIT_REASONS:
            quiet_reason_checks.append(
                func.coalesce(Message.message_metadata[key].astext, "") == reason,
            )
    return and_(
        Message.message_type == "agent_pause",
        or_(
            *quiet_reason_checks,
            func.coalesce(Message.message_metadata["signal_only"].astext, "false") == "true",
        ),
    )


def exclude_ui_only_no_reply_clause():
    """Clause that excludes UI-only no-reply rows from conversational queries."""
    return not_(ui_only_no_reply_clause())
