"""
System Agents Registry and Utilities

This module defines the internal system agents that power platform-wide operations.
System agents are:
- Invisible to all users (is_internal=True)
- Not returned by any API endpoint
- Cross-org capable (can operate in any organization)
- Only created via database migrations

Security:
- System agents cannot be created/modified via API
- All operations are logged with is_system=True audit flag
- NO MCP access (system operations use internal service calls only)
"""

from enum import Enum
from typing import TypedDict
from uuid import UUID

# Reserved UUIDs for system infrastructure
# Using all-zeros pattern makes them easy to identify and filter
SYSTEM_ORG_ID = UUID("00000000-0000-0000-0000-000000000000")
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000001")

# Reserved UUID for aX-guide global agent (visible in all spaces)
AX_GUIDE_AGENT_ID = UUID("00000000-0000-0000-0000-000000000099")


class SystemAgentType(str, Enum):
    """Types of internal system agents."""

    NOTIFICATION_BOT = "notification_bot"
    CLEANUP_SERVICE = "cleanup_service"
    CONTEXT_MANAGER = "context_manager"
    TASK_ORCHESTRATOR = "task_orchestrator"


class GlobalAgentType(str, Enum):
    """Types of globally visible agents (visible in all spaces)."""

    AX_GUIDE = "ax_guide"  # Onboarding helper


class SystemAgentConfig(TypedDict):
    """Configuration for a system agent."""

    name: str
    description: str
    capabilities: list[str]
    cross_org: bool
    mcp_enabled: bool


class GlobalAgentConfig(TypedDict):
    """Configuration for a globally visible agent."""

    id: UUID
    name: str
    description: str
    bio: str
    capabilities: list[str]
    is_cloud_agent: bool


# Registry of globally visible agents (available in all spaces)
GLOBAL_AGENTS: dict[GlobalAgentType, GlobalAgentConfig] = {
    GlobalAgentType.AX_GUIDE: {
        "id": AX_GUIDE_AGENT_ID,
        "name": "ax_guide",
        "description": "aX Platform Guide - Your onboarding assistant for MCP setup",
        "bio": "I'm ax_guide, your friendly platform assistant! I help you connect AI agents to aX using MCP (Model Context Protocol). Just mention @ax_guide to get started!",
        "capabilities": [
            "messages.send",
            "messages.read",
            "context.read",
        ],
        "is_cloud_agent": True,
    },
}


# Registry of all system agents and their configurations
SYSTEM_AGENTS: dict[SystemAgentType, SystemAgentConfig] = {
    SystemAgentType.NOTIFICATION_BOT: {
        "name": "__notification_bot__",
        "description": "Platform-wide notification delivery",
        "capabilities": [
            "messages.send",
            "notifications.send",
        ],
        "cross_org": True,
        "mcp_enabled": False,
    },
    SystemAgentType.CLEANUP_SERVICE: {
        "name": "__cleanup_service__",
        "description": "Data cleanup and maintenance operations",
        "capabilities": [
            "data.cleanup",
            "audit.log",
        ],
        "cross_org": True,
        "mcp_enabled": False,
    },
    SystemAgentType.CONTEXT_MANAGER: {
        "name": "__context_manager__",
        "description": "Team context and knowledge management",
        "capabilities": [
            "context.read",
            "context.write",
            "messages.read",
        ],
        "cross_org": True,
        "mcp_enabled": False,
    },
    SystemAgentType.TASK_ORCHESTRATOR: {
        "name": "__task_orchestrator__",
        "description": "Task assignment and coordination",
        "capabilities": [
            "tasks.read",
            "tasks.assign",
            "messages.send",
        ],
        "cross_org": True,
        "mcp_enabled": False,
    },
}


def get_system_agent_config(agent_type: SystemAgentType) -> SystemAgentConfig | None:
    """Get configuration for a system agent type."""
    return SYSTEM_AGENTS.get(agent_type)


def is_system_space(space_id: UUID) -> bool:
    """Check if a space_id is the system space."""
    return space_id == SYSTEM_ORG_ID


def is_system_user(user_id: UUID) -> bool:
    """Check if a user_id is the system user."""
    return user_id == SYSTEM_USER_ID


def is_internal_agent_name(name: str | None) -> bool:
    """Check if an agent name follows internal naming convention (__name__).

    Args:
        name: Agent name to check. Returns False for None/empty values.

    Returns:
        True if name matches __name__ pattern (internal system agent).
    """
    if not name:
        return False
    return name.startswith("__") and name.endswith("__")


def get_global_agent_config(agent_type: GlobalAgentType) -> GlobalAgentConfig | None:
    """Get configuration for a global agent type."""
    return GLOBAL_AGENTS.get(agent_type)


def get_all_global_agents() -> list[GlobalAgentConfig]:
    """Get all globally visible agents."""
    return list(GLOBAL_AGENTS.values())


def is_global_agent(agent_id: UUID) -> bool:
    """Check if an agent_id is a global agent."""
    return any(config["id"] == agent_id for config in GLOBAL_AGENTS.values())
