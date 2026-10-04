"""
Agent Toggle Registry - Single source of truth for all agent tool toggles.

==============================================================================
HOW TO ADD A NEW TOGGLE (2 STEPS)
==============================================================================

STEP 1: Add to AGENT_TOGGLES below
-------------------------------
Add a new ToggleDefinition with a unique tool_key. Example:

    ToggleDefinition(
        tool_key="my_new_tool",        # Key in enabled_tools JSONB: {"my_new_tool": true}
        default=False,                  # Default value for new agents
        description="Enable my new tool feature",
        # Optional: legacy field name if migrating from an existing boolean column
        legacy_field="my_new_tool_enabled",    # Maps to agent.my_new_tool_enabled
        legacy_alias="myNewToolEnabled",        # camelCase for API compatibility
        # Optional: require template capability (matches mcp_servers.tool_id)
        template_capability="my_new_tool",
        # Optional: require minimum role to enable
        min_role="plus",  # "user", "plus", or "admin"
        upsell_message="My New Tool is a Plus feature! Visit ax-platform.com to upgrade.",
    )

STEP 2: (Only if adding a new database column) Create Alembic migration
------------------------------------------------------------------------
New toggles that use ONLY the enabled_tools JSONB column need NO migration.
The enabled_tools JSONB column stores all toggles dynamically.

If you need a separate boolean column for backwards compatibility or indexing:
    docker exec ax-backend-api alembic revision -m "add_my_new_tool_enabled"

That's it! The following are automatically handled:
- API schemas (AgentCreate, AgentUpdate, AgentResponse)
- Agent creation with defaults
- Agent updates with validation
- Dispatch payload (tool_config.enabled_tools)
- Roster service (enabled_tools in roster entries)

==============================================================================
ARCHITECTURE OVERVIEW
==============================================================================

Storage:
    agents.enabled_tools JSONB column: {"ax_mcp": true, "web_fetch": false, ...}
    Individual boolean columns (ax_mcp_enabled, etc.) are DEPRECATED but synced.

API:
    - New format: {"enabled_tools": {"ax_mcp": true, "web_fetch": false}}
    - Legacy format (deprecated): {"axMcpEnabled": true, "webFetchEnabled": false}
    Both formats work; the system auto-converts legacy to new format.

Dispatch:
    Cloud agent payloads include tool_config.enabled_tools dict.

==============================================================================
"""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToggleDefinition:
    """Definition of an agent tool toggle."""

    # Required: Tool key used in enabled_tools JSONB (e.g., "ax_mcp", "web_fetch")
    tool_key: str

    # Required: Default value for new agents
    default: bool

    # Required: Description for API docs
    description: str

    # Optional: Legacy field name (snake_case) for backwards compat with boolean columns
    # e.g., "ax_mcp_enabled" - can be None for new tools that don't have legacy columns
    legacy_field: str | None = None

    # Optional: Legacy API alias (camelCase) for backwards compat with frontend
    legacy_alias: str | None = None

    # Optional: Validation function (value, agent, user) -> raises HTTPException if invalid
    validator: Callable[[bool, Any, Any], None] | None = None

    # Optional: Requires template capability check (matches mcp_servers.tool_id)
    template_capability: str | None = None

    # Optional: Minimum role required to enable ("user", "plus", "admin")
    min_role: str | None = None

    # Optional: Friendly upsell message for role-gated features
    upsell_message: str | None = None


# =============================================================================
# TOGGLE REGISTRY - Add new toggles here!
# =============================================================================

AGENT_TOGGLES: list[ToggleDefinition] = [
    ToggleDefinition(
        tool_key="ax_mcp",
        default=True,
        description="Enable Commonflame MCP tools (messages, tasks, context)",
        legacy_field="ax_mcp_enabled",
        legacy_alias="axMcpEnabled",
    ),
    ToggleDefinition(
        tool_key="web_fetch",
        default=False,
        description="Enable web page fetching and parsing",
        legacy_field="web_fetch_enabled",
        legacy_alias="webFetchEnabled",
        template_capability="web_fetch",
    ),
    ToggleDefinition(
        tool_key="brave_search",
        default=False,
        description="Enable Brave web search",
        legacy_field="brave_search_enabled",
        legacy_alias="braveSearchEnabled",
        template_capability="brave_search",
        min_role="plus",
        upsell_message="Brave Search is a Plus feature! Visit ax-platform.com or join our Discord to upgrade.",
    ),
    ToggleDefinition(
        tool_key="image_gen",
        default=False,
        description="Enable image generation",
        legacy_field="image_gen_enabled",
        legacy_alias="imageGenEnabled",
        template_capability="image_gen",
        min_role="plus",
        upsell_message="Image Generation is a Plus feature! Visit ax-platform.com or join our Discord to upgrade.",
    ),
]


# =============================================================================
# LOOKUP DICTIONARIES (auto-built from registry)
# =============================================================================

TOGGLE_BY_KEY: dict[str, ToggleDefinition] = {t.tool_key: t for t in AGENT_TOGGLES}
TOGGLE_BY_LEGACY_FIELD: dict[str, ToggleDefinition] = {
    t.legacy_field: t for t in AGENT_TOGGLES if t.legacy_field
}
TOGGLE_BY_LEGACY_ALIAS: dict[str, ToggleDefinition] = {
    t.legacy_alias: t for t in AGENT_TOGGLES if t.legacy_alias
}
ALL_TOOL_KEYS: list[str] = [t.tool_key for t in AGENT_TOGGLES]


# =============================================================================
# CORE HELPER FUNCTIONS
# =============================================================================


def get_enabled_tools_defaults() -> dict[str, bool]:
    """Get default enabled_tools dict for new agents."""
    return {t.tool_key: t.default for t in AGENT_TOGGLES}


def build_enabled_tools_from_agent(agent: Any) -> dict[str, bool]:
    """
    Build enabled_tools dict from an Agent model instance.

    Reads from enabled_tools JSONB if available, falls back to legacy columns.
    Ensures all registered toggles are present with defaults.

    Args:
        agent: Agent model instance

    Returns:
        Complete dict of all toggles: {"ax_mcp": true, "web_fetch": false, ...}
    """
    # If agent has enabled_tools JSONB and it's populated, use it
    if hasattr(agent, "enabled_tools") and agent.enabled_tools:
        result = dict(agent.enabled_tools)
        # Ensure all known tools are present with defaults
        for toggle in AGENT_TOGGLES:
            if toggle.tool_key not in result:
                result[toggle.tool_key] = toggle.default
        return result

    # Fall back to legacy boolean columns
    result = {}
    for toggle in AGENT_TOGGLES:
        if toggle.legacy_field and hasattr(agent, toggle.legacy_field):
            result[toggle.tool_key] = getattr(agent, toggle.legacy_field, toggle.default)
        else:
            result[toggle.tool_key] = toggle.default
    return result


def apply_enabled_tools_defaults(
    enabled_tools: dict[str, bool] | None,
    template_capabilities: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """
    Apply defaults and template constraints to enabled_tools.

    Args:
        enabled_tools: Requested tool settings (can be partial or None)
        template_capabilities: Template's default_tools dict (limits what can be enabled)

    Returns:
        Complete enabled_tools dict with all tools
    """
    result = get_enabled_tools_defaults()
    caps = template_capabilities or {}

    # Apply requested values
    if enabled_tools:
        for key, value in enabled_tools.items():
            if key in TOGGLE_BY_KEY:
                result[key] = value

    # Apply template capability limits
    for toggle in AGENT_TOGGLES:
        if toggle.template_capability:
            # If template explicitly disables this capability, force to False
            if not caps.get(toggle.template_capability, True):
                result[toggle.tool_key] = False

    return result


def validate_enabled_tools_update(
    enabled_tools: dict[str, bool],
    user: Any,
    template_capabilities: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """
    Validate enabled_tools update request against role and template constraints.

    Args:
        enabled_tools: Requested tool settings
        user: Current user (for role checks)
        template_capabilities: Template's default_tools dict

    Returns:
        Validated enabled_tools dict (only valid keys)

    Raises:
        HTTPException: If validation fails (role check, template constraint)
    """
    from fastapi import HTTPException, status

    from .models_config import ROLE_HIERARCHY

    result = {}
    caps = template_capabilities or {}

    for key, value in enabled_tools.items():
        toggle = TOGGLE_BY_KEY.get(key)
        if not toggle:
            continue  # Ignore unknown tools

        # Check role requirements (only when enabling)
        if toggle.min_role and value:
            user_role = getattr(user, "role", "user").lower()
            user_level = ROLE_HIERARCHY.get(user_role, 0)
            required_level = ROLE_HIERARCHY.get(toggle.min_role, 0)
            if user_level < required_level:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=toggle.upsell_message or f"{toggle.tool_key} requires {toggle.min_role} access",
                )

        # Check template capability limits (only when enabling)
        if toggle.template_capability and value:
            if not caps.get(toggle.template_capability, True):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Template does not support {toggle.tool_key.replace('_', ' ')}",
                )

        # Run custom validator if defined
        if toggle.validator:
            toggle.validator(value, None, user)

        result[key] = value

    return result


def merge_legacy_toggles_into_enabled_tools(
    enabled_tools: dict[str, bool] | None,
    legacy_data: dict[str, Any],
) -> dict[str, bool]:
    """
    Merge legacy toggle fields into enabled_tools dict.

    Allows the API to accept both formats:
    - New: {"enabled_tools": {"ax_mcp": true}}
    - Legacy: {"axMcpEnabled": true} or {"ax_mcp_enabled": true}

    Args:
        enabled_tools: The enabled_tools dict (may be None or partial)
        legacy_data: Dict that may contain legacy toggle fields

    Returns:
        Merged enabled_tools dict
    """
    result = dict(enabled_tools) if enabled_tools else {}

    for toggle in AGENT_TOGGLES:
        # Skip if already set in enabled_tools
        if toggle.tool_key in result:
            continue

        # Check for legacy field name (snake_case)
        if toggle.legacy_field and toggle.legacy_field in legacy_data:
            value = legacy_data[toggle.legacy_field]
            if value is not None:
                result[toggle.tool_key] = value
                continue

        # Check for legacy alias (camelCase)
        if toggle.legacy_alias and toggle.legacy_alias in legacy_data:
            value = legacy_data[toggle.legacy_alias]
            if value is not None:
                result[toggle.tool_key] = value

    return result


def get_legacy_columns_from_enabled_tools(enabled_tools: dict[str, bool]) -> dict[str, bool]:
    """
    Build dict of legacy column values from enabled_tools.

    Used to populate legacy fields in AgentResponse for backwards compatibility.

    Args:
        enabled_tools: Current enabled_tools dict

    Returns:
        Dict like {"ax_mcp_enabled": True, "web_fetch_enabled": False, ...}
    """
    result = {}
    for toggle in AGENT_TOGGLES:
        if toggle.legacy_field:
            result[toggle.legacy_field] = enabled_tools.get(toggle.tool_key, toggle.default)
    return result


def get_legacy_column_updates_from_enabled_tools(enabled_tools: dict[str, bool]) -> dict[str, bool]:
    """
    Build dict for SQLAlchemy update() to sync legacy columns.

    Alias for get_legacy_columns_from_enabled_tools (same logic).
    """
    return get_legacy_columns_from_enabled_tools(enabled_tools)


def extract_legacy_toggle_values(model: Any) -> dict[str, Any]:
    """
    Extract legacy toggle field values from a Pydantic model or dict.

    Used to collect legacy field values for merge_legacy_toggles_into_enabled_tools.

    Args:
        model: Pydantic model instance or dict

    Returns:
        Dict like {"web_fetch_enabled": True, "ax_mcp_enabled": None, ...}
    """
    result = {}
    for toggle in AGENT_TOGGLES:
        if toggle.legacy_field:
            if hasattr(model, toggle.legacy_field):
                result[toggle.legacy_field] = getattr(model, toggle.legacy_field)
            elif isinstance(model, dict) and toggle.legacy_field in model:
                result[toggle.legacy_field] = model[toggle.legacy_field]
    return result


# =============================================================================
# LEGACY COMPATIBILITY ALIASES (deprecated - use above functions instead)
# =============================================================================

# These are kept for backwards compatibility with existing code imports
TOGGLE_BY_NAME = TOGGLE_BY_LEGACY_FIELD
TOGGLE_BY_ALIAS = TOGGLE_BY_LEGACY_ALIAS


def get_toggle_defaults() -> dict[str, bool]:
    """[DEPRECATED] Use get_enabled_tools_defaults() instead."""
    return {t.legacy_field: t.default for t in AGENT_TOGGLES if t.legacy_field}


def apply_toggle_defaults(data: dict, template_capabilities: dict | None = None) -> dict:
    """[DEPRECATED] Use apply_enabled_tools_defaults() instead."""
    result = {}
    caps = template_capabilities or {}

    for toggle in AGENT_TOGGLES:
        if not toggle.legacy_field:
            continue
        value = data.get(toggle.legacy_field, toggle.default)
        if toggle.template_capability and not caps.get(toggle.template_capability, True):
            value = False
        result[toggle.legacy_field] = value

    return result


def process_toggle_updates(
    agent_update: Any,
    agent: Any,
    user: Any,
    template_capabilities: dict | None = None,
) -> dict[str, bool]:
    """
    [DEPRECATED] Process legacy toggle field updates from an agent update request.

    Use validate_enabled_tools_update() for the new enabled_tools format instead.

    Args:
        agent_update: Pydantic model with legacy toggle fields
        agent: Current agent model instance
        user: Current user (for role checks)
        template_capabilities: Template's default_tools dict

    Returns:
        Dict of {legacy_field: value} for fields that should be updated
    """
    from fastapi import HTTPException, status
    from .models_config import ROLE_HIERARCHY

    updates = {}
    caps = template_capabilities or {}

    for toggle in AGENT_TOGGLES:
        if not toggle.legacy_field:
            continue

        value = getattr(agent_update, toggle.legacy_field, None)
        if value is None:
            continue

        # Check role requirements (only when enabling)
        if toggle.min_role and value:
            user_role = getattr(user, "role", "user").lower()
            user_level = ROLE_HIERARCHY.get(user_role, 0)
            required_level = ROLE_HIERARCHY.get(toggle.min_role, 0)
            if user_level < required_level:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=toggle.upsell_message or f"{toggle.legacy_field} requires {toggle.min_role} access",
                )

        # Check template capability limits (only when enabling)
        if toggle.template_capability and value:
            if not caps.get(toggle.template_capability, True):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Template does not support {toggle.legacy_field.replace('_', ' ')}",
                )

        # Run custom validator if defined
        if toggle.validator:
            toggle.validator(value, agent, user)

        updates[toggle.legacy_field] = value

    return updates


# =============================================================================
# PYDANTIC SCHEMA HELPERS
# =============================================================================
# These functions generate Field definitions for Pydantic models.
# They allow the API schemas to automatically include all registered toggles.


def get_create_update_schema_fields() -> dict[str, tuple[type, Any]]:
    """
    Generate Pydantic Field definitions for AgentCreate/AgentUpdate schemas.

    Returns dict suitable for creating a Pydantic model dynamically, or for reference
    when adding fields manually.

    Returns:
        Dict of {field_name: (type_annotation, Field(...))}

    Example output:
        {
            "web_fetch_enabled": (bool | None, Field(False, description="...", alias="webFetchEnabled")),
            "ax_mcp_enabled": (bool | None, Field(True, description="...", alias="axMcpEnabled")),
            ...
        }
    """
    from pydantic import Field

    fields = {}
    for toggle in AGENT_TOGGLES:
        if toggle.legacy_field:
            fields[toggle.legacy_field] = (
                bool | None,
                Field(
                    toggle.default,
                    description=f"[DEPRECATED] Use enabled_tools.{toggle.tool_key} instead. {toggle.description}",
                    alias=toggle.legacy_alias,
                ),
            )
    return fields


def get_response_schema_fields() -> dict[str, tuple[type, Any]]:
    """
    Generate Pydantic Field definitions for AgentResponse schema.

    Unlike create/update, response fields are not optional (they have actual values).

    Returns:
        Dict of {field_name: (type_annotation, default_value)}
    """
    fields = {}
    for toggle in AGENT_TOGGLES:
        if toggle.legacy_field:
            fields[toggle.legacy_field] = (bool, toggle.default)
    return fields


def get_response_defaults_dict() -> dict[str, bool]:
    """
    Get default enabled_tools dict for AgentResponse schema default_factory.

    This is used in the AgentResponse.enabled_tools Field default_factory.

    Returns:
        Dict like {"ax_mcp": True, "web_fetch": False, ...}
    """
    return get_enabled_tools_defaults()


def print_schema_fields_for_reference() -> None:
    """
    Print Pydantic field definitions for copy-paste reference.

    Run this to see what fields to add to your schemas:
        python -c "from app.core.agent_toggles import print_schema_fields_for_reference; print_schema_fields_for_reference()"
    """
    print("=" * 60)
    print("LEGACY FIELDS FOR AgentCreate / AgentUpdate:")
    print("(Copy these if manually defining fields)")
    print("=" * 60)
    for toggle in AGENT_TOGGLES:
        if toggle.legacy_field:
            print(f'''    {toggle.legacy_field}: bool | None = Field(
        {toggle.default},
        description="[DEPRECATED] Use enabled_tools.{toggle.tool_key}. {toggle.description}",
        alias="{toggle.legacy_alias}",
    )''')
    print()
    print("=" * 60)
    print("LEGACY FIELDS FOR AgentResponse:")
    print("=" * 60)
    for toggle in AGENT_TOGGLES:
        if toggle.legacy_field:
            print(f"    {toggle.legacy_field}: bool = {toggle.default}  # [DEPRECATED] Use enabled_tools")
    print()
    print("=" * 60)
    print("ENABLED_TOOLS DEFAULT (for AgentResponse.enabled_tools):")
    print("=" * 60)
    defaults = get_enabled_tools_defaults()
    print(f"    default_factory=lambda: {defaults}")
