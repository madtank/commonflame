"""
Parity logging helpers for message operations.
Tracks operations across API and MCP adapters to ensure consistency.
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from uuid import UUID

from ..core.actor import Actor
from ..core.parity import ParityLogger, compute_message_digest
from ..models.message import Message


class MessagesParityHelper:
    """Helper for parity logging operations."""

    @staticmethod
    def log_send(
        *,
        adapter: str,
        actor: Actor,
        msg: Message,
        unread_count: int = 0
    ) -> None:
        """Log a message send operation for parity tracking."""
        try:
            message_dict = {
                "id": str(msg.id),
                "content": msg.content,
                "channel": msg.channel,
                "message_type": msg.message_type,
                "parent_id": str(msg.parent_id) if msg.parent_id else None,
                "read_state": msg.read_state,
                "cursor_position": msg.cursor_position or 0,
                "metadata": msg.message_metadata,
                "created_at": msg.created_at,
                "updated_at": msg.updated_at,
                "author": {
                    "id": str(actor.id),
                    "type": actor.type
                },
                "replies_count": 0
            }

            digest = compute_message_digest(message_dict)

            ParityLogger.log_operation(
                adapter=adapter,
                actor_id=str(actor.id),
                space_id=str(actor.space_id),
                action="send",
                message_id=str(msg.id),
                digest=digest,
                unread_fanout_count=unread_count
            )
        except Exception as e:
            # Don't fail on logging errors
            print(f"Failed to log parity (send): {e}")

    @staticmethod
    def log_list(
        *,
        adapter: str,
        actor: Actor
    ) -> None:
        """Log a message list operation for parity tracking."""
        try:
            ParityLogger.log_operation(
                adapter=adapter,
                actor_id=str(actor.id),
                space_id=str(actor.space_id),
                action="list",
                message_id=None,
                digest=None,
                unread_fanout_count=None
            )
        except Exception as e:
            print(f"Failed to log parity (list): {e}")

    @staticmethod
    def log_edit(
        *,
        adapter: str,
        actor: Actor,
        msg: Message
    ) -> None:
        """Log a message edit operation for parity tracking."""
        try:
            message_dict = {
                "id": str(msg.id),
                "content": msg.content,
                "channel": msg.channel,
                "message_type": msg.message_type,
                "parent_id": str(msg.parent_id) if msg.parent_id else None,
                "read_state": msg.read_state,
                "cursor_position": msg.cursor_position or 0,
                "metadata": msg.message_metadata,
                "created_at": msg.created_at,
                "updated_at": msg.updated_at,
                "author": {"id": str(actor.id), "type": actor.type},
                "replies_count": 0,
            }
            digest = compute_message_digest(message_dict)
            ParityLogger.log_operation(
                adapter=adapter,
                actor_id=str(actor.id),
                space_id=str(actor.space_id),
                action="edit",
                message_id=str(msg.id),
                digest=digest,
                unread_fanout_count=None,
            )
        except Exception as e:
            print(f"Failed to log parity (edit): {e}")

    @staticmethod
    def log_delete(
        *,
        adapter: str,
        actor: Actor,
        message_id: UUID
    ) -> None:
        """Log a message delete operation for parity tracking."""
        try:
            ParityLogger.log_operation(
                adapter=adapter,
                actor_id=str(actor.id),
                space_id=str(actor.space_id),
                action="delete",
                message_id=str(message_id),
                digest=None,
                unread_fanout_count=None
            )
        except Exception as e:
            print(f"Failed to log parity (delete): {e}")

    @staticmethod
    def log_get_history(
        *,
        adapter: str,
        actor: Actor,
        message_id: UUID
    ) -> None:
        """Log a message history retrieval for parity tracking."""
        try:
            ParityLogger.log_operation(
                adapter=adapter,
                actor_id=str(actor.id),
                space_id=str(actor.space_id),
                action="get_history",
                message_id=str(message_id),
                digest=None,
                unread_fanout_count=None
            )
        except Exception as e:
            print(f"Failed to log parity (get_history): {e}")
