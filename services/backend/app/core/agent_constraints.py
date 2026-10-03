"""
Agent name validation constants and utilities - single source of truth
"""
import re
from typing import Tuple, Set

# Agent name constraints
AGENT_NAME_MAX_LENGTH = 50
AGENT_NAME_MIN_LENGTH = 3
AGENT_NAME_PATTERN = r'^[a-zA-Z][a-zA-Z0-9_-]*$'
AGENT_NAME_ALLOWED_CHARS = "letters, numbers, underscore, hyphen"

# Token size constraints
MAX_AGENTS_IN_TOKEN = 50  # Keeps JWT under 3KB

# Reserved agent names that cannot be used
RESERVED_AGENT_NAMES: Set[str] = {
    'admin', 'system', 'root', 'api', 'mcp',
    'platform', 'service', 'agent', 'bot',
    'anonymous', 'unknown', 'default',
    'user'  # Reserved keyword for /mcp/agents/user → {github_username}
}

# Cloud agent names that can be shared across users
# These agents are created automatically for each user during onboarding
# and are exempt from global uniqueness constraints
CLOUD_AGENT_NAMES: Set[str] = {
    # Add cloud agents here as needed
}

def validate_agent_name(name: str) -> Tuple[bool, str]:
    """
    Validate agent name against constraints

    Args:
        name: The agent name to validate

    Returns:
        Tuple of (is_valid, error_message)
        If valid, error_message is empty string
    """
    if not name:
        return False, "Agent name is required"

    if len(name) < AGENT_NAME_MIN_LENGTH:
        return False, f"Agent name must be at least {AGENT_NAME_MIN_LENGTH} characters"

    if len(name) > AGENT_NAME_MAX_LENGTH:
        return False, f"Agent name cannot exceed {AGENT_NAME_MAX_LENGTH} characters"

    if name.lower() in RESERVED_AGENT_NAMES:
        return False, f"'{name}' is a reserved name and cannot be used"

    if not re.match(AGENT_NAME_PATTERN, name):
        return False, f"Agent name must start with a letter and contain only {AGENT_NAME_ALLOWED_CHARS}"

    return True, ""

def normalize_agent_name(name: str) -> str:
    """
    Normalize agent name for consistent storage and comparison

    Args:
        name: The agent name to normalize

    Returns:
        Normalized agent name (lowercase, trimmed)
    """
    if not name:
        return ""
    return name.strip().lower()

def is_agent_name_reserved(name: str) -> bool:
    """
    Check if an agent name is reserved

    Args:
        name: The agent name to check

    Returns:
        True if the name is reserved
    """
    return normalize_agent_name(name) in RESERVED_AGENT_NAMES

def is_cloud_agent(name: str) -> bool:
    """
    Check if an agent name is a cloud agent (can be shared across users)

    Args:
        name: The agent name to check

    Returns:
        True if the name is a cloud agent
    """
    return normalize_agent_name(name) in CLOUD_AGENT_NAMES

def truncate_agent_list_for_token(agent_names: list[str]) -> Tuple[list[str], bool]:
    """
    Truncate agent list to fit in JWT token

    Args:
        agent_names: List of agent names

    Returns:
        Tuple of (truncated_list, was_truncated)
    """
    if len(agent_names) <= MAX_AGENTS_IN_TOKEN:
        return agent_names, False

    return agent_names[:MAX_AGENTS_IN_TOKEN], True


def resolve_keyword_agent_name(raw: str, username: str) -> Tuple[str | None, str | None]:
    """
    Resolve keyword-based agent names to a concrete agent name for the user.

    Examples:
        "user" -> "<username>", keyword "user"
        "user-bot" -> "<username>-bot", keyword "user"
        "user_bot" -> "<username>_bot", keyword "user"
        "user-assistant" -> "<username>-assistant", keyword "user"
        "user_assistant" -> "<username>_assistant", keyword "user"
        "bot" -> "<username>_bot", keyword "bot"
        "assistant" -> "<username>_assistant", keyword "assistant"
        "me" -> "<username>_mcp", keyword "me"
    """
    if not username:
        return None, None

    lower = (raw or "").strip().lower()
    if not lower:
        return None, None

    if lower.startswith("user"):
        suffix = raw[len("user") :] if len(raw) > len("user") else ""
        resolved = f"{username}{suffix}"
        return resolved, "user"

    keyword_map = {
        "bot": "_bot",
        "assistant": "_assistant",
        "me": "_mcp",
    }
    if lower in keyword_map:
        return f"{username}{keyword_map[lower]}", lower

    return raw, None


def is_agent_limit_enforced(user) -> bool:
    """
    Determine if agent creation limits should be enforced based on role/plan.
    """
    role = getattr(user, "role", "") or ""
    plan = getattr(user, "subscription_plan", None) or getattr(user, "plan", "") or ""

    role_l = role.lower()
    plan_l = plan.lower()

    if role_l in {"admin", "agent_manager"}:
        return False
    if plan_l in {"premium", "enterprise"}:
        return False
    return True
