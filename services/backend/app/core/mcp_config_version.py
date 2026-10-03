"""Definitions and helpers for MCP configuration variants."""

from __future__ import annotations

import os
from typing import Optional

# Config version identifiers
CONFIG_VERSION_V0_LEGACY = "v0-legacy"
CONFIG_VERSION_V1_HTTP_NATIVE = "v1-http-native"

# Primary default remains the legacy remote transport until migration completes
MCP_CONFIG_VERSION = CONFIG_VERSION_V0_LEGACY

# Shared header so HTTP-native clients can self-identify without heuristics
CONFIG_VERSION_HEADER = "X-AX-MCP-Config-Version"

VALID_CONFIG_VERSIONS = {
    CONFIG_VERSION_V0_LEGACY,
    CONFIG_VERSION_V1_HTTP_NATIVE,
}

# OAuth client ID used by default for HTTP-native configs
HTTP_NATIVE_CLIENT_ID = os.getenv("MCP_HTTP_NATIVE_CLIENT_ID", "ax-platform-http-native")


def normalize_config_version(value: Optional[str]) -> Optional[str]:
    """Return a canonical config version string when provided, else None."""

    if not value:
        return None
    candidate = value.strip().lower()
    if candidate in VALID_CONFIG_VERSIONS:
        return candidate
    return None


def infer_config_version(
    header_value: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """Infer which config version a request is using."""

    normalized = normalize_config_version(header_value)
    if normalized:
        return normalized

    ua = (user_agent or "").lower()
    if "mcp-remote" in ua:
        return CONFIG_VERSION_V0_LEGACY

    # Default to native HTTP – new registry configs advertise without extra hints
    return CONFIG_VERSION_V1_HTTP_NATIVE


def transport_label_for_config_version(version: Optional[str]) -> str:
    """Map a config version to the metrics/observability transport label."""

    if version == CONFIG_VERSION_V1_HTTP_NATIVE:
        return "native-http"
    return "remote-http"
