"""Local and container configuration for the Commonflame MCP resource server."""
import os


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


MCP_SERVER_NAME = os.getenv("MCP_SERVER_NAME", "Commonflame MCP")
FASTMCP_PORT = int(os.getenv("FASTMCP_PORT", os.getenv("PORT", "8080")))
API_BASE_URL = os.getenv("API_URL", "http://backend:8080")
# Public origin (no /mcp suffix). All OAuth resource indicators use this origin.
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:3000")
AX_MCP_RESOURCE_URL = os.getenv("AX_MCP_RESOURCE_URL", "")
MCP_RESOURCE_URL = os.getenv("MCP_RESOURCE_URL", "")
AX_AUTH_MODE = os.getenv("AX_AUTH_MODE", "remote").strip().lower()
AX_AUTH_SERVER_URL = os.getenv("AX_AUTH_SERVER_URL", "http://localhost:3000")
AX_AUTH_SERVER_INTERNAL_URL = os.getenv("AX_AUTH_SERVER_INTERNAL_URL", "http://backend:8080")
MCP_STATELESS_HTTP = env_bool("MCP_STATELESS_HTTP", True)
BACKEND_JWKS_URI = os.getenv("BACKEND_JWKS_URI", "http://backend:8080/.well-known/jwks.json")
# JWT and OAuth metadata issuer share the configured public AS origin.
BACKEND_ISSUER = os.getenv("BACKEND_ISSUER", AX_AUTH_SERVER_URL)
