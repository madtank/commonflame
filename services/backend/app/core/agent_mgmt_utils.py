"""
AX-AGENT-MGMT-001: Shared utilities for agent management.

- canonical_payload_hash: deterministic hash for proposal payload binding
- dispatch_allowed: dispatch precedence evaluation
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.agent_space_access import AgentSpaceAccess


# ---------------------------------------------------------------------------
# Canonical payload hash (spec §17.9)
# ---------------------------------------------------------------------------

def canonical_payload_hash(payload: dict) -> str:
    """
    Canonical SHA-256 hash for proposal payload binding.

    Canonical serialization format (spec §17.9):
    1. Remove all keys whose value is None
    2. Sort remaining keys lexicographically (recursive via json sort_keys=True)
    3. Serialize with json.dumps(separators=(",", ":")) — no whitespace
    4. Encode as UTF-8 bytes
    5. SHA-256 hash → lowercase hex digest (64 chars)

    Normalization rules:
    - None/null values are OMITTED (not serialized as "null")
    - Empty string "" is kept (not treated as null)
    - Boolean values use JSON true/false (Python json default)
    - UUID values must be pre-converted to strings by the caller
    - Nested dicts are recursively sorted by json sort_keys=True
    - Lists preserve insertion order (not sorted)

    This format is the binding contract between proposal creation and
    approval verification. Both sides MUST use this function.
    """
    cleaned = {k: v for k, v in sorted(payload.items()) if v is not None}
    canonical = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dispatch precedence (spec §17.10)
# ---------------------------------------------------------------------------

def dispatch_allowed(
    *,
    agent_global_state: str,
    attachment_state: str | None,
    management_class: str = "regular",
    concierge_control_plane_state: str | None = None,
) -> bool:
    """
    Evaluate whether an agent may be dispatched in a space.

    Precedence (highest to lowest):
    1. archived — terminal, wins over everything
    2. disabled — blocks dispatch in all spaces
    3. detached — blocks participation in that space
    4. suspended — blocks participation in that space
    5. paused concierge control-plane — blocks orchestration
    6. active + active — dispatchable

    Race resolution: global disable wins over local resume.
    """
    if agent_global_state == "archived":
        return False
    if agent_global_state == "disabled":
        return False
    if attachment_state is None or attachment_state == "detached":
        return False
    if attachment_state == "suspended":
        return False
    if management_class == "concierge":
        if concierge_control_plane_state and concierge_control_plane_state != "active":
            return False
    return agent_global_state == "active" and attachment_state == "active"


# ---------------------------------------------------------------------------
# Field classification (spec §5.5)
# ---------------------------------------------------------------------------

# LOCKED: Only name and description are cosmetic (delegated without approval).
# All other fields are behavior-changing or identity. Do NOT add fields here
# without updating the spec and getting security review.
COSMETIC_FIELDS = frozenset({"name", "description"})

BEHAVIOR_CHANGING_FIELDS = frozenset({
    "system_prompt", "model", "enabled_tools", "template_type",
    "web_fetch_enabled", "brave_search_enabled", "ax_mcp_enabled",
    "image_gen_enabled", "memory_data", "capabilities",
    "webhook_url", "webhook_secret", "cloud_function_url",
    "bedrock_agent_id", "bedrock_agent_alias_id",
})

IDENTITY_FIELDS = frozenset({
    "origin", "management_class", "owner_type", "owner_user_id",
    "owner_space_id", "home_space_id", "platform_managed",
    "identity_locked", "deletion_protected", "space_locked",
})


def classify_fields(fields: dict) -> tuple[set[str], set[str], set[str]]:
    """
    Classify a set of field changes into cosmetic, behavior-changing, and identity.

    Returns (cosmetic, behavior_changing, identity) sets of field names.
    """
    cosmetic = set()
    behavior = set()
    identity = set()
    for key in fields:
        if key in COSMETIC_FIELDS:
            cosmetic.add(key)
        elif key in BEHAVIOR_CHANGING_FIELDS:
            behavior.add(key)
        elif key in IDENTITY_FIELDS:
            identity.add(key)
        else:
            # Unknown fields treated as behavior-changing (fail-safe)
            behavior.add(key)
    return cosmetic, behavior, identity
