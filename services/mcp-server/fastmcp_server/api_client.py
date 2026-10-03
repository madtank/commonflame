"""Shared HTTP client for backend API calls.

CRITICAL: All write operations MUST go through the backend API.
This ensures the message router (loop detection, rate limiting) applies to ALL traffic.
Direct DB reads are acceptable for performance-critical queries only.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from functools import cache
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_URL", "http://backend:8080")


def _csv_env_values(name: str) -> frozenset[str]:
    return frozenset(
        value.strip()
        for value in os.getenv(name, "").split(",")
        if value.strip()
    )


# Empty by default is intentional: mutable x-on-behalf-of headers are ignored
# unless deployment config names immutable, vetted concierge/space-agent IDs.
_TRUSTED_CONCIERGE_AGENT_IDS = _csv_env_values("AX_TRUSTED_CONCIERGE_AGENT_IDS")
_FRONTEND_CLIENT_IDS = _csv_env_values("COGNITO_FRONTEND_CLIENT_ID")
_MCP_CLIENT_IDS = (
    _csv_env_values("COGNITO_MCP_CLIENT_ID")
    | _csv_env_values("COGNITO_MCP_M2M_CLIENT_ID")
)


def _claim_scopes(claims: dict[str, Any]) -> set[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        return {item for item in scope.split() if item}
    if isinstance(scope, list):
        return {str(item) for item in scope if item}
    return set()


def _claim_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, list):
        return {str(item) for item in value if item}
    return set()


def _claim_client_ids(claims: dict[str, Any]) -> set[str]:
    claim_client_ids = set()
    claim_client_ids.update(_claim_values(claims.get("client_id")))
    claim_client_ids.update(_claim_values(claims.get("azp")))
    # Cognito and backend PAT-exchange tokens are not perfectly consistent:
    # `aud` can carry a resource audience or the OAuth client identifier.
    # Treat it as a possible client ID only for route-binding classification;
    # audience/resource validation remains separate in _claim_audiences().
    claim_client_ids.update(_claim_values(claims.get("aud")))
    return claim_client_ids


def _claim_audiences(claims: dict[str, Any]) -> set[str]:
    audiences = set()
    audiences.update(_claim_values(claims.get("aud")))
    audiences.update(_claim_values(claims.get("audience")))
    audiences.update(_claim_values(claims.get("resource")))
    return audiences


def _normalize_mcp_resource(value: str) -> str:
    stripped = value.strip().rstrip("/")
    if not stripped:
        return ""
    if "/mcp/" in stripped:
        # Operators sometimes paste a named-agent or subresource URL; the OAuth
        # resource is still the MCP server root, not the individual path.
        stripped = stripped.split("/mcp/", 1)[0] + "/mcp"
    return stripped if stripped.endswith("/mcp") else f"{stripped}/mcp"


@cache
def _configured_mcp_audiences() -> frozenset[str]:
    # Env is read once per process. Tests that patch MCP audience env vars must
    # call _configured_mcp_audiences.cache_clear() around the patched block.
    audiences = {"ax-mcp"}
    mcp_server_url = os.getenv("MCP_SERVER_URL", "http://localhost:3000")
    for value in (
        os.getenv("AX_MCP_RESOURCE_URL", ""),
        os.getenv("MCP_RESOURCE_URL", ""),
        mcp_server_url,
    ):
        normalized = _normalize_mcp_resource(value)
        if normalized:
            audiences.add(normalized)
    return frozenset(audiences)


def _is_mcp_audience(value: str) -> bool:
    audiences = _configured_mcp_audiences()
    # Direct matching keeps the legacy "ax-mcp" string audience valid without
    # pretending it is URL-shaped.
    if value in audiences:
        return True
    return _normalize_mcp_resource(value) in audiences


def _is_pat_exchange_mcp_token(claims: dict[str, Any]) -> bool:
    """Return whether claims are from the backend PAT→JWT MCP exchange path.

    Headless MCP clients and MCP Jam exchange a user/agent PAT for a backend
    JWT with `token_class` and an MCP resource audience. Older exchange tokens
    used `audience=ax-mcp`; Waystation-AS remote OAuth tokens use the protected resource
    URL such as `https://waystation.example/mcp`. That token is not a browser frontend
    JWT, so a named `/mcp/agents/{name}` route is the reviewed agent binding
    while the original user remains requester context.
    """
    token_class = str(claims.get("token_class") or "").strip()
    return bool(
        token_class in {"user_access", "agent_access"}
        and any(_is_mcp_audience(value) for value in _claim_audiences(claims))
    )


def _is_frontend_user_session_token(claims: dict[str, Any]) -> bool:
    """Return whether claims belong to the browser/frontend user-session path.

    Frontend quick-launch widgets call MCP tools with the viewer's raw Cognito
    JWT. Those tokens must remain user principals even when the request passes
    through a named agent route/header. MCP OAuth proxy sessions are different:
    the route is the reviewed agent binding.
    """
    return bool(
        claims.get("typ") == "local-user"
        or (_FRONTEND_CLIENT_IDS and _claim_client_ids(claims) & _FRONTEND_CLIENT_IDS)
    )


def _is_route_bound_agent_session(claims: dict[str, Any], header_agent_name: Any) -> bool:
    """Return whether a named MCP route may bind this token to an agent.

    Browser/frontend Cognito tokens fail closed: if the deployment does not
    configure known frontend client IDs, route labels are not allowed to turn a
    user token into an agent principal. MCP OAuth and M2M clients are explicitly
    route-bound because the reviewed route/header carries the target agent.
    """
    if not header_agent_name:
        return False

    claim_client_ids = _claim_client_ids(claims)
    if _is_frontend_user_session_token(claims):
        return False
    if claim_client_ids & _MCP_CLIENT_IDS:
        return True
    if _is_pat_exchange_mcp_token(claims):
        return True

    # Transitional compatibility for older MCP OAuth tokens that carry the
    # resource scope but predate stable MCP client-id configuration. This still
    # fails closed for browser tokens: they either match _FRONTEND_CLIENT_IDS
    # above or, with no known client config, lack the MCP resource scope.
    # TODO(MCPJAM-TOKENS-001): remove after legacy MCP OAuth tokens are cycled
    # out and all environments set COGNITO_MCP_CLIENT_ID / M2M client IDs.
    return bool(claim_client_ids and "ax-api/mcp:read" in _claim_scopes(claims))


def _is_trusted_concierge_principal(*, agent_id: Any) -> bool:
    """Return whether signed agent claims may provide user delegation hints.

    `x-on-behalf-of` is a mutable request header used by the first-party space
    agent to attach the human requester to HITL draft calls. It is not accepted
    from arbitrary agents, and display names are intentionally ignored because
    they are not stable trust anchors across spaces.
    """
    signed_id = str(agent_id or "").strip()
    return bool(signed_id and signed_id in _TRUSTED_CONCIERGE_AGENT_IDS)


def _delegated_user_id(delegated_for: Any) -> str | None:
    if isinstance(delegated_for, dict):
        value = delegated_for.get("user_id")
        return str(value) if value else None
    if isinstance(delegated_for, str) and delegated_for.strip():
        return delegated_for.strip()
    return None


_ON_BEHALF_EXACT_WRITES = frozenset(
    {
        ("POST", "/api/v1/agents"),
        ("POST", "/api/v1/drafts/agents"),
        ("POST", "/api/v1/drafts/spaces"),
        ("POST", "/api/v1/tasks"),
        ("PUT", "/api/v1/tasks/reminders/pause"),
    }
)
_ON_BEHALF_DYNAMIC_WRITES = (
    ("PATCH", re.compile(r"^/api/v1/agents/[^/]+$")),
    ("POST", re.compile(r"^/api/v1/agents/[^/]+/placement$")),
    ("PATCH", re.compile(r"^/auth/agents/[^/]+/control$")),
    ("PATCH", re.compile(r"^/api/v1/drafts/[^/]+$")),
    ("POST", re.compile(r"^/api/v1/drafts/[^/]+/(approve|reject|cancel)$")),
    ("PUT", re.compile(r"^/api/v1/tasks/[^/]+(?:/status)?$")),
    ("PATCH", re.compile(r"^/api/v1/tasks/[^/]+$")),
    ("POST", re.compile(r"^/api/v1/tasks/[^/]+/nudge$")),
)


def _should_forward_on_behalf(method: str, path: str) -> bool:
    """Forward delegated user identity only to reviewed management write routes."""
    normalized_method = method.upper()
    normalized_path = path.split("?", 1)[0].rstrip("/") or "/"
    if (normalized_method, normalized_path) in _ON_BEHALF_EXACT_WRITES:
        return True
    return any(
        normalized_method == allowed_method and pattern.match(normalized_path)
        for allowed_method, pattern in _ON_BEHALF_DYNAMIC_WRITES
    )


def user_request_space_override(
    ctx: Dict[str, Any], mapping: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Add explicit space_id fields for user-authored API-first calls.

    Browser/CLI user sessions may navigate a space that differs from stale token
    claims. Some backend endpoints intentionally require an explicit override
    after membership verification, so user widget replays must carry the
    resolved request space as data, not only as X-Space-Id.

    Backend membership/RLS checks remain the enforcement boundary; this helper
    only resolves which space scope the user intended to use.
    """
    scoped = dict(mapping or {})
    if ctx.get("principal_type") == "user" and ctx.get("space_id"):
        scoped.setdefault("space_id", ctx["space_id"])
    return scoped


def user_request_space_params(
    ctx: Dict[str, Any], params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Add explicit space_id query params for user-authored API-first reads.

    Keep the query/body wrappers named by carrier at call sites, but keep the
    user-space override guard centralized in user_request_space_override().
    """
    return user_request_space_override(ctx, params)


def user_request_space_payload(
    ctx: Dict[str, Any], payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Add explicit space_id body fields for user-authored API-first writes.

    Keep the query/body wrappers named by carrier at call sites, but keep the
    user-space override guard centralized in user_request_space_override().
    """
    return user_request_space_override(ctx, payload)


def extract_agent_context(token, request) -> Dict[str, Any]:
    """Extract agent identity and delegation claims from token claims + request headers.

    Resolution order per AGENT-TOKEN-001:
    1. agent_id from token claims (canonical principal identity)
    2. agent_name from token claims (display/routing convenience)
    3. agent_name from X-Agent-Name header (compatibility fallback)
    4. space_id from token claims for agent principals, or from X-Space-Id for
       user principals when the UI/CLI explicitly selects a space

    Delegation claims are trusted as-issued by the backend JWT and forwarded unchanged.
    Mutable x-on-behalf-of request headers are accepted only from signed
    first-party concierge/space-agent principals.
    """
    jwt = token.token
    claims = token.claims or {}
    headers = request.headers if request else {}

    agent_id = claims.get("agent_id")
    claimed_agent_name = claims.get("agent_name")
    header_agent_name = headers.get("x-agent-name")
    user_id = claims.get("user_id") or claims.get("sub")

    route_bound_agent_session = _is_route_bound_agent_session(claims, header_agent_name)
    # Frontend widget user JWTs can arrive through named MCP routes when
    # widgets run viewer-private quick actions. Those route labels must not
    # convert the viewer into an agent. MCP OAuth/proxy and headless sessions
    # on /mcp/agents/{name} are route-bound agent sessions; the backend still
    # verifies that the token holder may resolve/use that agent.
    principal_type = (
        "agent"
        if (agent_id or claimed_agent_name or route_bound_agent_session)
        else "user"
    )
    agent_name = claimed_agent_name if principal_type == "agent" else None
    if principal_type == "agent" and not agent_name:
        agent_name = header_agent_name
    username = (
        claims.get("username")
        or claims.get("cognito:username")
        or claims.get("preferred_username")
    )
    email = claims.get("email")

    # Agent/headless tokens are bound to the space in signed claims. User
    # sessions are different: the browser/CLI may explicitly navigate a space,
    # and backend membership checks remain the enforcement boundary.
    claims_space = claims.get("space_id")
    header_space = headers.get("x-space-id")
    if claims_space and header_space and claims_space != header_space:
        if principal_type == "user":
            logger.info(
                "space_id mismatch for user principal: claims=%s header=%s - "
                "using explicit request space",
                claims_space,
                header_space,
            )
        else:
            logger.warning(
                "space_id mismatch for agent principal: claims=%s header=%s - "
                "using claims (AGENT-TOKEN-001)",
                claims_space,
                header_space,
            )
    if principal_type == "user":
        space_id = header_space or claims_space
    else:
        space_id = claims_space or header_space

    on_behalf_of = headers.get("x-on-behalf-of")
    delegation_mode = claims.get("delegation_mode")
    delegated_for = claims.get("delegated_for")
    if principal_type == "agent" and on_behalf_of and not delegated_for:
        if _is_trusted_concierge_principal(agent_id=agent_id):
            delegation_mode = delegation_mode or "concierge_delegated"
            delegated_for = on_behalf_of
        else:
            logger.warning(
                "Ignoring x-on-behalf-of from untrusted agent principal: "
                "claimed_agent_name=%s agent_id=%s route_agent_name=%s",
                claimed_agent_name,
                agent_id,
                header_agent_name,
            )
    delegated_space_owner = claims.get("delegated_space_owner")

    if principal_type == "agent" and not agent_name:
        logger.warning(
            "No agent identity resolved — client should connect via "
            "/mcp/agents/{name} or include X-Agent-Name header. "
            "Requests will use the JWT owner's identity (user impersonation risk)."
        )
    return {
        "jwt": jwt,
        "principal_type": principal_type,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "route_agent_name": header_agent_name,
        "user_id": user_id,
        "username": username,
        "email": email,
        "space_id": space_id,
        "delegation_mode": delegation_mode,
        "delegated_for": delegated_for,
        "delegated_space_owner": delegated_space_owner,
    }


async def api_request(
    method: str,
    path: str,
    auth_token: str,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
    agent_name: Optional[str] = None,
    agent_id: Optional[str] = None,
    space_id: Optional[str] = None,
    delegation_mode: Optional[str] = None,
    delegated_for: Optional[Any] = None,
) -> Dict[str, Any]:
    """Make an authenticated request to the backend API."""
    url = f"{API_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }
    if agent_id:
        headers["X-Agent-Id"] = agent_id
    if agent_name:
        headers["X-Agent-Name"] = agent_name
    if space_id:
        headers["X-Space-Id"] = space_id
    if delegated_for is not None:
        # All reviewed delegation modes currently forward X-Delegated-For so
        # backend audit/policy code can make the final authorization decision.
        # X-On-Behalf-Of is narrower and only emitted for reviewed write routes.
        if delegation_mode:
            headers["X-Delegation-Mode"] = delegation_mode
        if isinstance(delegated_for, (dict, list)):
            headers["X-Delegated-For"] = json.dumps(delegated_for)
        else:
            headers["X-Delegated-For"] = str(delegated_for)
        delegated_user_id = _delegated_user_id(delegated_for)
        if delegated_user_id and _should_forward_on_behalf(method, path):
            headers["X-On-Behalf-Of"] = delegated_user_id

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            json=json_data,
            params=params,
            timeout=timeout,
        )

        if response.status_code >= 400:
            body = response.text or "(empty response)"
            logger.error(
                "API error %s: %s - %s", response.status_code, path, body[:200]
            )
            return {"error": f"API error {response.status_code}", "detail": body[:200]}

        # Handle empty responses (204 No Content, empty 200/201)
        if not response.content:
            logger.warning(
                "[api_request] Empty response body: %s %s → %s (content-length: %s)",
                method, path, response.status_code,
                response.headers.get("content-length", "missing"),
            )
            return {"status": "ok", "code": response.status_code}

        try:
            data = response.json()
        except Exception:
            # Non-JSON response — return raw text
            return {
                "status": "ok",
                "code": response.status_code,
                "body": response.text[:500],
            }

        # FastMCP tools must return dicts. Some backend endpoints return
        # bare arrays (e.g. GET /api/v1/spaces). Wrap them.
        if isinstance(data, list):
            return {"results": data, "count": len(data)}
        return data


async def api_request_with_context(
    ctx: Dict[str, Any],
    method: str,
    path: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Forward the standard MCP request context to backend API calls.

    Tools should use this helper when forwarding the active JWT plus agent,
    space, and delegation headers. Keeping this in one place prevents agents
    and spaces tools from drifting on delegation behavior.
    """
    return await api_request(
        method,
        path,
        ctx["jwt"],
        agent_name=ctx["agent_name"],
        agent_id=ctx.get("agent_id"),
        space_id=ctx["space_id"],
        delegation_mode=ctx.get("delegation_mode"),
        delegated_for=ctx.get("delegated_for"),
        **kwargs,
    )


async def wait_for_reply(
    ctx: Dict[str, Any],
    sent_msg: Dict[str, Any],
    sent_id: str,
    max_wait: int = 90,
    poll_interval: int = 2,
    progress_interval: int = 10,
    progress=None,
) -> Dict[str, Any]:
    """Poll for a reply to a sent message. Returns the reply or timeout.

    Used by messages send flows, including messages(action="ask_ax"), to wait
    for Waystation's response. Default 90s accommodates larger models (~24s turn latency).

    When progress is provided (MCP Tasks), emits periodic status updates
    so clients can show meaningful progress without polling messages themselves.
    """
    poll_start = time.monotonic()
    attempt = 0
    next_progress_at = min(progress_interval, max_wait) if progress_interval > 0 else None
    conversation_id = sent_msg.get("conversation_id", sent_id)
    sent_created_at = _parse_backend_timestamp(sent_msg.get("created_at"))

    while time.monotonic() - poll_start < max_wait:
        await asyncio.sleep(poll_interval)
        attempt += 1
        elapsed = round(time.monotonic() - poll_start)

        # Emit a concise heartbeat through the MCP task status surface without
        # making every backend poll visible to users.
        if progress and next_progress_at is not None and elapsed >= next_progress_at:
            await progress.set_message(
                f"Still waiting for reply... {elapsed}s elapsed (max {max_wait}s)"
            )
            next_progress_at += progress_interval

        replies = await api_request(
            "GET",
            "/api/v1/messages",
            ctx["jwt"],
            params={
                "conversation_id": conversation_id,
                "limit": 5,
            },
            agent_name=ctx["agent_name"],
            agent_id=ctx.get("agent_id"),
            space_id=ctx["space_id"],
            delegation_mode=ctx.get("delegation_mode"),
            delegated_for=ctx.get("delegated_for"),
        )
        conversation_fallback = None
        for msg in replies.get("messages", []):
            msg_id = msg.get("id") or msg.get("message_id")
            if msg_id == sent_id:
                continue
            if msg.get("parent_id") == sent_id:
                if progress:
                    await progress.set_message("Reply received")
                return {
                    "reply": msg,
                    "status": "reply_received",
                    "reply_ms": round((time.monotonic() - poll_start) * 1000),
                    "reply_match": "direct_reply",
                }
            if conversation_fallback is None and _is_conversation_reply_candidate(
                msg,
                conversation_id=conversation_id,
                sent_created_at=sent_created_at,
            ):
                conversation_fallback = msg

        if conversation_fallback is not None:
            if progress:
                await progress.set_message("Reply received")
            return {
                "reply": conversation_fallback,
                "status": "reply_received",
                "reply_ms": round((time.monotonic() - poll_start) * 1000),
                "reply_match": "conversation_activity",
            }

    if progress:
        await progress.set_message(f"No reply after {max_wait}s — check messages later")
    return {
        "reply": None,
        "status": "timeout",
        "waited_seconds": max_wait,
    }


def _parse_backend_timestamp(value: Any) -> datetime | None:
    """Parse backend ISO timestamps safely."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _is_conversation_reply_candidate(
    message: Dict[str, Any],
    *,
    conversation_id: Any,
    sent_created_at: datetime | None,
) -> bool:
    """Fallback reply detection when backend conversation activity is present."""
    if not isinstance(message, dict):
        return False
    if conversation_id and message.get("conversation_id") != conversation_id:
        return False

    created_at = _parse_backend_timestamp(message.get("created_at"))
    if sent_created_at and created_at and created_at < sent_created_at:
        return False

    return True


def is_message_receipt(payload: Any) -> bool:
    """True when a backend response looks like a concrete message object."""
    if not isinstance(payload, dict):
        return False
    return bool(
        payload.get("id")
        or payload.get("message_id")
        or payload.get("created_at")
        or payload.get("content")
    )
