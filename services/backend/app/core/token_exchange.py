"""Token exchange service (AUTH-SPEC-001 §9).

PAT in -> validated exchange parameters out. Does NOT touch the database.
Database operations (PAT lookup, agent verification) happen in the endpoint.
"""
import logging
from dataclasses import dataclass

from .credential_service import (
    PAT_CLASS_EXCHANGE_MATRIX,
    TOKEN_CLASS_MAX_TTL,
    TOKEN_CLASS_SCOPE_ALLOWLIST,
)

logger = logging.getLogger(__name__)

# Phase 3: ax-api and ax-mcp audiences
_ALLOWED_AUDIENCES = frozenset({"ax-api", "ax-mcp"})

# PAT audience selector → aud claim mapping
AUDIENCE_MAP = {
    "cli": ["ax-api"],
    "mcp": ["ax-mcp"],
    "both": ["ax-api", "ax-mcp"],
}

# Resource URI → audience mapping (RFC 8707)
RESOURCE_TO_AUDIENCE = {
    "https://paxai.app/api": "ax-api",
    "https://paxai.app/mcp": "ax-mcp",
    "https://next.paxai.app/api": "ax-api",
    "https://next.paxai.app/mcp": "ax-mcp",
    "https://dev.paxai.app/api": "ax-api",
    "https://dev.paxai.app/mcp": "ax-mcp",
}


class ExchangeError(Exception):
    """Structured exchange error (AUTH-SPEC-001 §9.4)."""

    def __init__(self, error_code: str, http_status: int, detail: str):
        self.error_code = error_code
        self.http_status = http_status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class ValidatedExchange:
    """Validated exchange parameters, ready for JWT minting."""
    token_class: str
    audience: str
    scope: str
    ttl: int
    agent_id: str | None


def validate_exchange_request(
    *,
    pat_type_char: str,
    requested_token_class: str,
    requested_scope: str,
    requested_audience: str,
    requested_ttl: int | None,
    agent_id: str | None,
    agent_name: str | None = None,
    resource: str | None = None,
    pat_audience: str | None = None,
) -> ValidatedExchange:
    """Validate an exchange request against the spec rules.

    Returns ValidatedExchange on success. Raises ExchangeError on failure.
    Does not access the database -- pure validation logic.
    """
    # 1. Check PAT class -> token class allowed (§4.1)
    allowed_classes = PAT_CLASS_EXCHANGE_MATRIX.get(pat_type_char, set())
    if requested_token_class not in allowed_classes:
        raise ExchangeError(
            "class_not_allowed", 403,
            f"PAT class '{pat_type_char}' cannot exchange for '{requested_token_class}'",
        )

    # 2. Check scope is present (§9.3)
    if not requested_scope or not requested_scope.strip():
        raise ExchangeError("scope_required", 400, "Scope parameter is required")

    # 3. Check all scopes are in the allowlist for this token class (§6, §9.3)
    #    No silent downscoping -- reject if any scope is outside the allowlist.
    requested_scopes = set(requested_scope.strip().split())
    allowed_scopes = TOKEN_CLASS_SCOPE_ALLOWLIST.get(requested_token_class, frozenset())
    disallowed = requested_scopes - allowed_scopes
    if disallowed:
        raise ExchangeError(
            "scope_not_allowed", 400,
            f"Scopes not allowed for {requested_token_class}: {sorted(disallowed)}",
        )

    # 4. Resolve audience from resource URI (RFC 8707) or explicit audience
    effective_audience = requested_audience
    if resource:
        mapped = RESOURCE_TO_AUDIENCE.get(resource)
        if mapped:
            effective_audience = mapped
        else:
            raise ExchangeError(
                "audience_not_allowed", 400,
                f"Unknown resource URI: '{resource}'",
            )

    if effective_audience not in _ALLOWED_AUDIENCES:
        raise ExchangeError(
            "audience_not_allowed", 400,
            f"Audience '{effective_audience}' not allowed. Allowed: {sorted(_ALLOWED_AUDIENCES)}",
        )

    # 4b. Enforce PAT's stored audience restriction
    if pat_audience and pat_audience != "both":
        allowed_aud = AUDIENCE_MAP.get(pat_audience, [])
        if effective_audience not in allowed_aud:
            raise ExchangeError(
                "audience_not_allowed", 400,
                f"PAT audience is '{pat_audience}' — cannot exchange for '{effective_audience}'",
            )

    # 5. Check agent_id or agent_name for agent_access
    if requested_token_class == "agent_access" and not agent_id and not agent_name:
        raise ExchangeError(
            "agent_not_found", 422,
            "agent_id or agent_name is required for agent_access token class",
        )

    # 6. Check TTL (§9.2)
    max_ttl = TOKEN_CLASS_MAX_TTL.get(requested_token_class, 900)
    if requested_ttl is not None:
        if requested_ttl > max_ttl:
            raise ExchangeError(
                "ttl_exceeds_max", 400,
                f"Requested TTL {requested_ttl}s exceeds max {max_ttl}s for {requested_token_class}",
            )
        effective_ttl = requested_ttl
    else:
        effective_ttl = max_ttl

    return ValidatedExchange(
        token_class=requested_token_class,
        audience=effective_audience,
        scope=requested_scope.strip(),
        ttl=effective_ttl,
        agent_id=agent_id,
    )
