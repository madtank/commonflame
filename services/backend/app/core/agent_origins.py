"""
Agent Origin Registry — single source of truth for valid agent origins.

Per AGENTS-001 spec: origin determines ALL agent behavior (dispatch, auth, capabilities).
agent_type MUST equal origin. No freeform strings.
"""

VALID_ORIGINS: set[str] = frozenset({
    "space_agent",       # 1 per space, default handler, HTTP stream
    "cloud",             # Our infra via agent_runner, HTTP POST
    "external_gateway",  # User's infra via HMAC webhook
    "agentcore",         # AWS Bedrock, Return of Control
    "mcp",               # Client-controlled, inbound only, never dispatched
})

DISPATCHABLE_ORIGINS: set[str] = frozenset({
    "space_agent",
    "cloud",
    "external_gateway",
    "agentcore",
})


def is_valid_origin(origin: str | None) -> bool:
    """Check if an origin value is valid."""
    return origin in VALID_ORIGINS


def origin_is_dispatchable(origin: str | None) -> bool:
    """Check if an origin supports autonomous dispatch (platform calls agent)."""
    return origin in DISPATCHABLE_ORIGINS
