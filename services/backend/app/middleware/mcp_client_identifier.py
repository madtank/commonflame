"""
MCP Client Identifier Middleware

Extracts and enriches client identification for MCP connections.
Ensures each MCP client gets unique identification even through proxies.
"""

import hashlib
import json
import logging
import uuid
from typing import Dict, Optional
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class MCPClientIdentifierMiddleware(BaseHTTPMiddleware):
    """
    Middleware to identify and track individual MCP clients.

    Adds unique client fingerprint to requests for better rate limiting granularity.
    """

    async def dispatch(self, request: Request, call_next):
        """Add MCP client identification to request"""

        # Skip non-MCP requests
        if not (request.url.path.startswith("/mcp") or request.url.path.startswith("/oauth")):
            return await call_next(request)

        # Extract all available identifiers
        identifiers = self._extract_identifiers(request)

        # Generate composite fingerprint
        fingerprint = self._generate_fingerprint(identifiers)

        # Add to request state for downstream use
        request.state.mcp_client_id = fingerprint
        request.state.mcp_identifiers = identifiers

        # Log for debugging (only in debug mode)
        if identifiers.get("agent_name") or identifiers.get("client_instance"):
            logger.debug(f"MCP Client: {fingerprint} | {json.dumps(identifiers)}")

        # Add response header for client tracking
        response = await call_next(request)
        response.headers["X-MCP-Client-ID"] = fingerprint

        return response

    def _extract_identifiers(self, request: Request) -> Dict[str, Optional[str]]:
        """Extract all available client identifiers"""

        identifiers = {}

        # 1. Agent name (most specific)
        identifiers["agent_name"] = request.headers.get("X-Agent-Name")

        # 2. Client instance ID (unique per Claude window)
        identifiers["client_instance"] = request.headers.get("X-Client-Instance")

        # 3. MCP Session ID
        identifiers["session_id"] = request.headers.get("Mcp-Session-Id")

        # 4. Client ID from OAuth
        if hasattr(request, 'query_params'):
            identifiers["client_id"] = request.query_params.get("client_id")

        # 5. User agent fingerprint
        user_agent = request.headers.get("User-Agent", "")
        if "mcp-remote" in user_agent.lower():
            # Extract mcp-remote version
            import re
            match = re.search(r'mcp-remote/([0-9.]+)', user_agent)
            if match:
                identifiers["mcp_version"] = match.group(1)

        # 6. JWT subject (user identification)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                import base64
                token_parts = auth_header[7:].split(".")
                if len(token_parts) >= 2:
                    # Add padding if needed
                    payload_b64 = token_parts[1]
                    padding = 4 - (len(payload_b64) % 4)
                    if padding != 4:
                        payload_b64 += "=" * padding

                    payload = base64.urlsafe_b64decode(payload_b64)
                    payload_data = json.loads(payload)
                    identifiers["user_id"] = payload_data.get("sub")
                    identifiers["agent_id"] = payload_data.get("agent_id")
            except Exception as e:
                logger.debug(f"JWT decode error: {e}")

        # 7. Token hash (for consistency)
        if auth_header:
            identifiers["token_hash"] = hashlib.md5(auth_header.encode()).hexdigest()[:8]

        # 8. Source IP (least specific, but still useful)
        if request.client:
            identifiers["source_ip"] = request.client.host

        # 9. GitHub OAuth state (for OAuth flows)
        if request.url.path == "/oauth/authorize":
            identifiers["oauth_state"] = request.query_params.get("state", "")[:8]

        return identifiers

    def _generate_fingerprint(self, identifiers: Dict[str, Optional[str]]) -> str:
        """Generate unique fingerprint from identifiers"""

        # Priority order for fingerprinting
        # Use the most specific available identifier

        # Best case: agent_name + client_instance
        if identifiers.get("agent_name") and identifiers.get("client_instance"):
            return f"mcp:{identifiers['agent_name']}:{identifiers['client_instance'][:8]}"

        # Good: agent_name + session_id
        if identifiers.get("agent_name") and identifiers.get("session_id"):
            return f"mcp:{identifiers['agent_name']}:{identifiers['session_id'][:8]}"

        # Good: agent_name + user_id
        if identifiers.get("agent_name") and identifiers.get("user_id"):
            return f"mcp:{identifiers['agent_name']}:{identifiers['user_id'][:8]}"

        # Fallback: agent_name alone
        if identifiers.get("agent_name"):
            return f"mcp:{identifiers['agent_name']}"

        # OAuth flow: client_id + oauth_state
        if identifiers.get("client_id") and identifiers.get("oauth_state"):
            return f"oauth:{identifiers['client_id']}:{identifiers['oauth_state']}"

        # Token-based
        if identifiers.get("token_hash"):
            return f"token:{identifiers['token_hash']}"

        # Last resort: IP-based
        return f"ip:{identifiers.get('source_ip', 'unknown')}"


def generate_client_instance_id() -> str:
    """
    Generate a unique client instance ID.
    This should be called once per Claude window/session and persisted.
    """
    return str(uuid.uuid4())


def add_mcp_client_headers(headers: Dict[str, str], agent_name: str, instance_id: Optional[str] = None) -> Dict[str, str]:
    """
    Helper to add MCP client identification headers.

    Usage in .mcp.json:
    {
        "mcpServers": {
            "my-server": {
                "command": "npx",
                "args": ["mcp-remote", "..."],
                "env": {
                    "MCP_CLIENT_INSTANCE": "<generated-uuid>"
                }
            }
        }
    }
    """
    headers = headers.copy()
    headers["X-Agent-Name"] = agent_name

    if instance_id:
        headers["X-Client-Instance"] = instance_id
    else:
        # Generate one if not provided
        headers["X-Client-Instance"] = generate_client_instance_id()

    return headers
