"""
Parity utilities for ensuring UI and MCP consistency.
Provides digests, normalization, and comparison tools.
"""
import hashlib
import json
from typing import Dict, Any, Optional
from datetime import datetime


def compute_task_digest(task: Any) -> str:
    """
    Compute a stable digest for a task.
    Used to verify parity between API and MCP paths.
    """
    # Extract stable fields (not timestamps)
    task_id = str(task.id) if hasattr(task, 'id') else ""
    title = task.title if hasattr(task, 'title') else ""
    status = task.status if hasattr(task, 'status') else ""
    priority = task.priority if hasattr(task, 'priority') else ""
    assigned_to = str(task.assigned_to) if hasattr(task, 'assigned_to') and task.assigned_to else ""

    # Create stable string representation
    digest_string = f"{task_id}|{title}|{status}|{priority}|{assigned_to}"

    # Return first 16 chars of SHA256 hash
    return hashlib.sha256(digest_string.encode()).hexdigest()[:16]


def compute_message_digest(message: Dict[str, Any]) -> str:
    """
    Compute stable digest of canonical message fields.
    This digest is identical regardless of transport (API vs MCP).
    """
    # Extract canonical fields
    msg_id = message.get("id", "")
    content = message.get("content", "")
    channel = message.get("channel", "main")
    message_type = message.get("message_type", "message")
    parent_id = message.get("parent_id") or ""
    read_state = message.get("read_state", "delivered")
    cursor_position = message.get("cursor_position", 0)

    # Handle timestamps
    created_at = message.get("created_at", "")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    updated_at = message.get("updated_at", "")
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat()

    # Handle author
    author = message.get("author", {})
    author_id = author.get("id", "")
    author_type = author.get("type", "")

    # Handle replies count
    replies_count = message.get("replies_count", 0)

    # Create stable string representation
    digest_string = (
        f"{msg_id}|{content}|{channel}|{message_type}|{parent_id}|"
        f"{read_state}|{cursor_position}|{created_at}|{updated_at}|"
        f"{author_id}|{author_type}|{replies_count}"
    )

    # Compute SHA256
    return hashlib.sha256(digest_string.encode()).hexdigest()[:16]


def normalize_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize message to canonical form for comparison.
    Strips transport-specific fields and sorts keys.
    """
    # Define canonical fields
    canonical_fields = {
        "id", "content", "channel", "message_type", "parent_id",
        "read_state", "cursor_position", "metadata", "created_at",
        "updated_at", "author", "replies_count"
    }

    # Extract only canonical fields
    normalized = {
        k: v for k, v in message.items()
        if k in canonical_fields
    }

    # Sort keys for stable comparison
    return dict(sorted(normalized.items()))


def compare_messages(api_msg: Dict[str, Any], mcp_msg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compare API and MCP messages for parity.
    Returns detailed comparison results.
    """
    # Normalize both
    api_norm = normalize_message(api_msg)
    mcp_norm = normalize_message(mcp_msg)

    # Compute digests
    api_digest = compute_message_digest(api_norm)
    mcp_digest = compute_message_digest(mcp_norm)

    # Find differences
    differences = []
    for key in set(api_norm.keys()) | set(mcp_norm.keys()):
        api_val = api_norm.get(key)
        mcp_val = mcp_norm.get(key)
        if api_val != mcp_val:
            differences.append({
                "field": key,
                "api_value": api_val,
                "mcp_value": mcp_val
            })

    return {
        "identical": len(differences) == 0,
        "api_digest": api_digest,
        "mcp_digest": mcp_digest,
        "digests_match": api_digest == mcp_digest,
        "differences": differences,
        "api_normalized": api_norm,
        "mcp_normalized": mcp_norm
    }


class ParityLogger:
    """Logger for tracking parity between API and MCP operations."""

    @staticmethod
    def log_operation(
        adapter: str,  # "api" or "mcp"
        actor_id: str,
        space_id: str,
        action: str,  # "send", "list", "mark_read"
        message_id: Optional[str] = None,
        digest: Optional[str] = None,
        unread_fanout_count: Optional[int] = None
    ):
        """Log operation for parity monitoring."""
        import logging
        import json

        logger = logging.getLogger("parity")

        log_entry = {
            "adapter": adapter,
            "actor_id": actor_id,
            "space_id": space_id,
            "action": action,
            "message_id": message_id,
            "digest": digest,
            "unread_fanout_count": unread_fanout_count,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Structured logging for easy querying
        logger.info(f"PARITY: {json.dumps(log_entry)}")
