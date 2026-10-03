"""
Container Registry - Agent Template Configuration

Defines available agent templates, their capabilities, models, and access rules.
Frontend fetches this via GET /auth/agent-templates to populate template selector.

Architecture:
- Global templates defined here (platform-wide)
- Future: Space-local templates in DB for partner containers
- Resolution: space-local → global (when partners added)
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Agent Templates Registry
# =============================================================================
# Each template defines a container type with its capabilities and access rules.
# The template_type field on agents references these keys.

AGENT_TEMPLATES = {
    # ==========================================================================
    # Legacy templates (kept for backwards compatibility)
    # ==========================================================================
    "ax_agent": {
        "display_name": "aX Agent",
        "description": "General-purpose AI agent with web browsing, image generation, and platform tools",
        "icon": "sparkles",
        "container_url_env": "AGENT_RUNNER_URL",
        "capabilities": {
            "web_browsing": True,
            "image_gen": True,
            "ax_mcp": True,
            "web_fetch": True,
            "brave_search": True,
        },
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "tier": "free"},
            {"id": "gemini-2.5-flash-lite", "name": "Gemini 2.5 Flash Lite", "tier": "free"},
        ],
        "default_model": "gemini-2.5-flash",
        "access": {"min_role": "user"},
        "is_default": True,
    },
    "gemma_research": {
        "display_name": "Gemma Research",
        "description": "Specialized for medical and research analysis using Google's Gemma models",
        "icon": "flask",
        "container_url_env": "AGENT_RUNNER_VERTEX_URL",
        "capabilities": {
            "web_browsing": True,
            "image_gen": False,
            "ax_mcp": True,
            "web_fetch": True,
            "brave_search": True,
        },
        "models": [
            {"id": "gemma-3", "name": "Gemma 3", "tier": "plus"},
            {"id": "medgemma", "name": "MedGemma", "tier": "plus"},
        ],
        "default_model": "gemma-3",
        "access": {"min_role": "plus"},
        "badge": "Research",
    },
    # ==========================================================================
    # New templates (from agent_templates database table)
    # These keys match the 'key' column in agent_templates table
    # ==========================================================================
    "general": {
        "display_name": "General Assistant",
        "description": "Helpful collaborative agent with full aX toolkit",
        "icon": "sparkles",
        "container_url_env": "AGENT_RUNNER_URL",
        "capabilities": {
            "ax_mcp": True,
            "web_fetch": True,
            "brave_search": False,
            "image_gen": True,
        },
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "tier": "free"},
            {"id": "gemini-2.5-flash-lite", "name": "Gemini 2.5 Flash Lite", "tier": "free"},
        ],
        "default_model": "gemini-2.5-flash",
        "access": {"min_role": "user"},
    },
    "research": {
        "display_name": "Research",
        "description": "Deep-dive analyst with web research capabilities",
        "icon": "search",
        "container_url_env": "AGENT_RUNNER_URL",
        "capabilities": {
            "ax_mcp": True,
            "web_fetch": True,
            "brave_search": True,
            "image_gen": False,
        },
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "tier": "free"},
            {"id": "gemini-2.5-flash-lite", "name": "Gemini 2.5 Flash Lite", "tier": "free"},
        ],
        "default_model": "gemini-2.5-flash",
        "access": {"min_role": "user"},
        "badge": "Research",
    },
    "standard_developer": {
        "display_name": "Developer",
        "description": "Technical problem solver and code architect",
        "icon": "code",
        "container_url_env": "AGENT_RUNNER_URL",
        "capabilities": {
            "ax_mcp": True,
            "web_fetch": True,
            "brave_search": True,
            "image_gen": False,
        },
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "tier": "free"},
        ],
        "default_model": "gemini-2.5-flash",
        "access": {"min_role": "user"},
        "badge": "Developer",
    },
    "standard_pm": {
        "display_name": "Project Manager",
        "description": "Task coordinator and project organizer",
        "icon": "clipboard",
        "container_url_env": "AGENT_RUNNER_URL",
        "capabilities": {
            "ax_mcp": True,
            "web_fetch": False,
            "brave_search": False,
            "image_gen": False,
        },
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "tier": "free"},
        ],
        "default_model": "gemini-2.5-flash",
        "access": {"min_role": "user"},
        "badge": "PM",
    },
    "standard_librarian": {
        "display_name": "Librarian",
        "description": "Knowledge guardian and context curator",
        "icon": "book",
        "container_url_env": "AGENT_RUNNER_URL",
        "capabilities": {
            "ax_mcp": True,
            "web_fetch": True,
            "brave_search": True,
            "image_gen": False,
        },
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "tier": "free"},
        ],
        "default_model": "gemini-2.5-flash",
        "access": {"min_role": "user"},
        "badge": "Librarian",
    },
}


# =============================================================================
# Helper Functions
# =============================================================================

def get_template(template_type: str) -> Optional[dict]:
    """Get template config by type."""
    return AGENT_TEMPLATES.get(template_type)


def get_default_template() -> tuple[str, dict]:
    """Get the default template (type, config)."""
    for template_type, config in AGENT_TEMPLATES.items():
        if config.get("is_default"):
            return template_type, config
    # Fallback to first template
    first = next(iter(AGENT_TEMPLATES.items()))
    return first


def get_container_url(template_type: str) -> Optional[str]:
    """Get container URL for a template type."""
    template = get_template(template_type)
    if not template:
        return None

    env_var = template.get("container_url_env")
    if not env_var:
        return None

    return os.getenv(env_var)


def get_available_templates(user_role: str) -> list[dict]:
    """
    Get templates available to a user based on their role.
    Returns list of template configs with template_type included.
    """
    available = []

    role_hierarchy = ["user", "plus", "admin", "super_admin"]
    try:
        user_level = role_hierarchy.index(user_role)
    except ValueError:
        user_level = 0  # Default to user level

    for template_type, config in AGENT_TEMPLATES.items():
        min_role = config.get("access", {}).get("min_role", "user")
        try:
            required_level = role_hierarchy.index(min_role)
        except ValueError:
            required_level = 0

        if user_level >= required_level:
            # Include template_type in the response
            template_data = {
                "template_type": template_type,
                **config,
            }
            # Remove internal fields
            template_data.pop("container_url_env", None)
            available.append(template_data)

    return available


def validate_model_for_template(model: str, template_type: str) -> bool:
    """Check if a model is valid for a template."""
    template = get_template(template_type)
    if not template:
        return False

    valid_models = [m["id"] for m in template.get("models", [])]
    return model in valid_models


def get_template_capabilities(template_type: str) -> dict:
    """Get capabilities dict for a template."""
    template = get_template(template_type)
    if not template:
        return {}
    return template.get("capabilities", {})


def can_use_capability(template_type: str, capability: str) -> bool:
    """Check if a template supports a capability."""
    capabilities = get_template_capabilities(template_type)
    return capabilities.get(capability, False)
