"""
aX JWT Token Issuer — RS256 signed JWTs for Space Agent MCP auth.

Security model:
- Private key stays on backend only (signs tokens)
- Public key served via JWKS endpoint (verifies tokens)
- MCP server fetches JWKS to validate, never sees private key

Spec: specs/AX-MCP-AUTH-001/spec.md
"""

import json
import logging
import time
import uuid
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

logger = logging.getLogger(__name__)

_KEY_ID = "ax-backend-1"
_ISSUER = "ax-backend"
_ALGORITHM = "RS256"
_SAFE_EXTRA_CLAIMS = {
    "correlation_id",
    "dispatch_id",
    "message_id",
    "delegation_mode",
    "delegated_for",
    "delegated_space_owner",
    "scope",
}


@lru_cache(maxsize=1)
def _get_signing_key() -> tuple:
    """
    Generate or load RSA key pair. Cached for process lifetime.

    Production: set AX_JWT_PRIVATE_KEY_PEM (base64-encoded PEM).
    Local dev: ephemeral key pair generated on startup.
    """
    import os

    pem_file = os.environ.get("AX_JWT_PRIVATE_KEY_FILE")
    pem_b64 = os.environ.get("AX_JWT_PRIVATE_KEY_PEM")
    if pem_file:
        from pathlib import Path
        private_key = serialization.load_pem_private_key(Path(pem_file).read_bytes(), password=None)
    elif pem_b64:
        import base64

        pem_bytes = base64.b64decode(pem_b64)
        private_key = serialization.load_pem_private_key(pem_bytes, password=None)
    else:
        env = os.environ.get("ENVIRONMENT", "development").lower()
        if env not in ("development", "local", "test"):
            raise RuntimeError(
                "AX_JWT_PRIVATE_KEY_PEM must be set in non-development environments "
                f"(current: {env})"
            )
        logger.warning(
            "AX_JWT_PRIVATE_KEY_PEM not set — generating ephemeral RSA key pair "
            "(tokens will not survive restart)"
        )
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

    public_key = private_key.public_key()
    return private_key, public_key


def get_jwks() -> dict:
    """Return JWKS document containing the public key for token verification."""
    _, public_key = _get_signing_key()
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = _KEY_ID
    jwk["use"] = "sig"
    jwk["alg"] = _ALGORITHM
    return {"keys": [jwk]}


def mint_ax_jwt(
    agent_id: str,
    agent_name: str,
    space_id: str,
    tools_allowed: list[str],
    ttl_seconds: int = 900,
    extra_claims: dict | None = None,
) -> str:
    """
    Mint an RS256-signed JWT for aX to authenticate with the MCP server.

    Claims:
        iss:            "ax-backend" (issuer)
        sub:            canonical agent subject `agent:<uuid>`
        space_id:       space UUID — MCP server forwards to backend for RLS
        agent_name:     display name
        agent_id:       same as sub (explicit for clarity)
        tools_allowed:  ["messages", "tasks", ...] — MCP server checks before executing
        exp:            expiration (Unix epoch)
        iat:            issued-at (Unix epoch)
        jti:            unique token ID (audit trail / future revocation)
        extra_claims:   optional dict merged into payload (e.g. correlation_id)
    """
    private_key, _ = _get_signing_key()
    now = int(time.time())

    payload = {
        "iss": _ISSUER,
        "sub": f"agent:{agent_id}",
        "token_class": "agent_access",
        "agent_id": agent_id,
        "agent_name": agent_name,
        "space_id": space_id,
        "tools_allowed": tools_allowed,
        "aud": ["ax-mcp", "ax-api"],
        "exp": now + ttl_seconds,
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    if extra_claims:
        # Only allow known safe claim names — prevent overwriting security claims
        for k, v in extra_claims.items():
            if k in _SAFE_EXTRA_CLAIMS and v is not None:
                payload[k] = v

    return jwt.encode(
        payload,
        private_key,
        algorithm=_ALGORITHM,
        headers={"kid": _KEY_ID},
    )


def mint_exchange_jwt(
    *,
    sub: str,
    token_class: str,
    audience: str,
    scope: str,
    ttl_seconds: int,
    src_credential_id: str,
    owner_user_id: str,
    agent_id: str | None = None,
    delegated_by: str | None = None,
    authorized_space_id: str | None = None,
) -> str:
    """Mint an RS256 JWT via PAT exchange (AUTH-SPEC-001 §9).

    This is the ONLY function that mints tokens for the exchange endpoint.
    Claims follow the canonical format from §10.
    """
    private_key, _ = _get_signing_key()
    now = int(time.time())

    payload = {
        "iss": _ISSUER,
        "sub": sub,
        "token_class": token_class,
        "aud": audience,
        "scope": scope,
        "exp": now + ttl_seconds,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "src_credential_id": src_credential_id,
        "owner_user_id": owner_user_id,
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    if delegated_by is not None:
        payload["delegated_by"] = delegated_by
    if authorized_space_id is not None:
        payload["authorized_space_id"] = authorized_space_id

    return jwt.encode(
        payload,
        private_key,
        algorithm=_ALGORITHM,
        headers={"kid": _KEY_ID},
    )
