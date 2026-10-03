"""
Actor context for unified permissions across API and MCP.
Provides a transport-agnostic way to represent acting entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class Actor:
    """Universal context for both humans and agents performing actions."""

    id: UUID
    type: Literal["human", "agent"]
    space_id: UUID
    capabilities: set[str]
    is_system: bool = False  # True for internal system agents (audit trail)

    def require(self, capability: str) -> None:
        """
        Check if actor has required capability.
        Raises PermissionError if capability is missing.
        System actors may bypass certain capability checks.

        SECURITY NOTE: is_system flag should ONLY be set by internal services
        (e.g., message_intelligence.py). API endpoints must NEVER construct
        Actors with is_system=True. The actor's space_id may differ from system
        space because system agents operate cross-space (using target message's space).
        """
        # System actors can bypass certain checks for internal operations
        # SECURITY: is_system is trusted because it's only set by internal code paths
        # API endpoints construct Actors from JWT tokens which never have is_system=True
        if self.is_system and capability in SYSTEM_BYPASS_CAPABILITIES:
            # Verify actor has ONLY the minimum required capabilities (defense-in-depth)
            allowed_caps = {"messages.send", "messages.react"}
            if not self.capabilities.issubset(allowed_caps):
                raise PermissionError(
                    f"System actor has unexpected capabilities: {self.capabilities - allowed_caps}"
                )
            return
        if capability not in self.capabilities:
            raise PermissionError(f"Actor lacks capability: {capability}")


# Capabilities that system actors can bypass
# Keep this list minimal for security
SYSTEM_BYPASS_CAPABILITIES = {
    "messages.react",  # AI Validator reactions
}


# Capability constants for messages
CAP_MESSAGES_SEND = "messages.send"
CAP_MESSAGES_LIST = "messages.list"
CAP_MESSAGES_DELETE = "messages.delete"
