"""MCP resource subscription support for agent inbox updates."""

from __future__ import annotations

import logging
import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from mcp.shared.exceptions import McpError
import mcp.types

logger = logging.getLogger(__name__)

INBOX_ME_URI = "ax://inbox/me"
_MENTION_RE = re.compile(r"(?<![\w.-])@([A-Za-z0-9][A-Za-z0-9_.-]{0,63})")


@dataclass
class InboxSubscription:
    agent_name: str
    agent_id: str | None
    uri: str
    session: Any


@dataclass(frozen=True)
class ResolvedInboxSubscription:
    agent_name: str
    agent_id: str | None
    uri: str


class InboxSubscriptionRegistry:
    """In-process registry for live MCP inbox resource subscriptions.

    This intentionally covers the current single-process MCP deployment path.
    Multi-worker or multi-task deployments need sticky routing or a shared
    fanout layer such as Redis pub/sub so notifications can reach sessions
    registered in another process.

    Stale sessions are cleaned up lazily when delivery fails. TODO(FASTMCP-SESSION-CLOSE-HOOK):
    remove subscriptions on session close when FastMCP exposes a public hook.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, list[InboxSubscription]] = {}

    def _key(self, agent_name: str, agent_id: str | None = None) -> str:
        if agent_id is not None:
            cleaned_agent_id = agent_id.strip()
            if cleaned_agent_id:
                return f"id:{cleaned_agent_id}"
            return "id:"
        return f"name:{normalize_agent_name(agent_name)}"

    def subscribe(
        self,
        agent_name: str,
        uri: str,
        session: Any,
        *,
        agent_id: str | None = None,
    ) -> None:
        agent_key = self._key(agent_name, agent_id)
        existing = self._subscriptions.setdefault(agent_key, [])
        for sub in existing:
            if sub.session is session and sub.uri == uri:
                return
        existing.append(
            InboxSubscription(normalize_agent_name(agent_name), agent_id, uri, session)
        )

    def unsubscribe(
        self,
        agent_name: str,
        uri: str,
        session: Any,
        *,
        agent_id: str | None = None,
    ) -> None:
        agent_key = self._key(agent_name, agent_id)
        subscriptions = self._subscriptions.get(agent_key, [])
        remaining = [
            sub
            for sub in subscriptions
            if not (sub.session is session and sub.uri == uri)
        ]
        if remaining:
            self._subscriptions[agent_key] = remaining
        else:
            self._subscriptions.pop(agent_key, None)

    async def notify(self, agent_name: str, *, agent_id: str | None = None) -> int:
        agent_key = self._key(agent_name, agent_id)
        # Snapshot current subscribers; sessions added during this notify call
        # receive the next best-effort notification.
        subscriptions = list(self._subscriptions.get(agent_key, []))
        delivered = 0
        stale: list[InboxSubscription] = []

        for sub in subscriptions:
            try:
                await sub.session.send_resource_updated(sub.uri)
                delivered += 1
            except Exception:
                logger.debug(
                    "Dropping stale inbox subscription for @%s uri=%s",
                    agent_key,
                    sub.uri,
                    exc_info=True,
                )
                stale.append(sub)

        if stale:
            current = self._subscriptions.get(agent_key, [])
            stale_ids = {id(sub) for sub in stale}
            # Compare by object identity so concurrent additions with matching
            # field values are preserved. The stale list keeps these objects
            # alive, so id() reuse cannot happen during this cleanup pass.
            remaining = [sub for sub in current if id(sub) not in stale_ids]
            if remaining:
                self._subscriptions[agent_key] = remaining
            else:
                self._subscriptions.pop(agent_key, None)

        return delivered


inbox_subscriptions = InboxSubscriptionRegistry()


def normalize_agent_name(agent_name: str) -> str:
    return agent_name.strip().lstrip("@").lower()


def mentioned_agents(content: str | None) -> set[str]:
    if not content:
        return set()
    return {
        normalize_agent_name(match.group(1).rstrip("._-"))
        for match in _MENTION_RE.finditer(content)
    }


def _csv_env_values(name: str) -> frozenset[str]:
    return frozenset(
        value.strip() for value in os.getenv(name, "").split(",") if value.strip()
    )


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
    return claim_client_ids


def _current_token_claims(*, allow_no_auth: bool = False) -> dict[str, Any] | None:
    try:
        token = get_access_token()
    except Exception as exc:
        logger.warning(
            "Inbox subscription token context is unavailable; rejecting subscription",
            exc_info=True,
        )
        raise McpError(
            mcp.types.ErrorData(
                code=-32602,
                message="Agent token context is required for inbox subscriptions.",
            )
        ) from exc
    if token is None and allow_no_auth:
        return None
    if token is None:
        raise McpError(
            mcp.types.ErrorData(
                code=-32602,
                message="Agent token is required for inbox subscriptions.",
            )
        )
    claims = getattr(token, "claims", None)
    if not isinstance(claims, dict):
        raise McpError(
            mcp.types.ErrorData(
                code=-32602,
                message="Agent token claims are required for inbox subscriptions.",
            )
        )
    return claims


def _agent_from_request(request: Any) -> str | None:
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    return headers.get("x-agent-name")


def _agent_from_inbox_uri(uri: str, *, current_agent: str | None) -> str | None:
    prefix = "ax://inbox/"
    if not uri.startswith(prefix):
        return None
    target = uri[len(prefix) :].strip()
    if target == "me":
        return current_agent
    return target or None


def _is_frontend_user_token(claims: dict[str, Any]) -> bool:
    frontend_client_ids = _csv_env_values("COGNITO_FRONTEND_CLIENT_ID")
    return bool(frontend_client_ids and _claim_client_ids(claims) & frontend_client_ids)


def _claim_agent_id(claims: dict[str, Any]) -> str | None:
    agent_id = claims.get("agent_id")
    if agent_id:
        return str(agent_id)
    return None


def _authorize_subscription_agent(
    *,
    current_agent: str,
    target_agent: str,
    token_claims: dict[str, Any] | None,
) -> tuple[str, str | None]:
    current_key = normalize_agent_name(current_agent)
    target_key = normalize_agent_name(target_agent)
    if target_key != current_key:
        raise McpError(
            mcp.types.ErrorData(
                code=-32602,
                message="Inbox subscriptions are limited to the connected agent.",
            )
        )

    # token_claims=None is reserved for servers with no auth middleware, such
    # as the local MCP smoke harness. In authenticated deployments,
    # _current_token_claims returns dict claims or raises before this point;
    # multi-tenant expansion remains blocked on TODO(AUTH-INBOX-SUBSCRIPTIONS).
    if token_claims is not None:
        if _is_frontend_user_token(token_claims):
            raise McpError(
                mcp.types.ErrorData(
                    code=-32602,
                    message="Agent token is required for inbox subscriptions.",
                )
            )
        claimed_agent = token_claims.get("agent_name")
        if not claimed_agent:
            raise McpError(
                mcp.types.ErrorData(
                    code=-32602,
                    message="Agent token claim is required for inbox subscriptions.",
                )
            )
        if normalize_agent_name(str(claimed_agent)) != current_key:
            raise McpError(
                mcp.types.ErrorData(
                    code=-32602,
                    message="Connected agent does not match token claims.",
                )
            )
        claimed_agent_id = _claim_agent_id(token_claims)
        if not claimed_agent_id:
            raise McpError(
                mcp.types.ErrorData(
                    code=-32602,
                    message="Agent token id claim is required for inbox subscriptions.",
                )
            )
        return target_key, claimed_agent_id

    return target_key, None


def _resolve_subscription_target(uri: str, request: Any) -> tuple[str, str, str]:
    current_agent = _agent_from_request(request)
    if not current_agent:
        raise McpError(
            mcp.types.ErrorData(
                code=-32602,
                message="Agent identity is required to use ax://inbox/me.",
            )
        )
    # Non-me subscriptions still resolve against the route-injected connected
    # agent. Keep TODO(AUTH-INBOX-SUBSCRIPTIONS) closed before expanding this
    # primitive beyond the current single-process authenticated route model.
    agent_name = _agent_from_inbox_uri(uri, current_agent=current_agent)
    if not agent_name:
        raise McpError(
            mcp.types.ErrorData(
                code=-32602,
                message="Only ax://inbox/me or ax://inbox/{agent} subscriptions are supported.",
            )
        )
    return current_agent, agent_name, uri


def _resolve_subscription_with_claims_for_test(
    uri: str,
    request: Any,
    *,
    token_claims: dict[str, Any] | None,
) -> tuple[str, str]:
    """Resolve a subscription with explicit claims for tests/local no-auth harnesses."""
    current_agent, agent_name, subscription_uri = _resolve_subscription_target(
        uri,
        request,
    )
    agent_name, _agent_id = _authorize_subscription_agent(
        current_agent=current_agent,
        target_agent=agent_name,
        token_claims=token_claims,
    )
    # Test-only compatibility helper: production subscriptions must use
    # _resolve_subscription so agent_id is preserved as the fanout key.
    return agent_name, subscription_uri


def _resolve_subscription(
    uri: str,
    request: Any,
    *,
    allow_no_auth: bool = False,
) -> ResolvedInboxSubscription:
    current_agent, agent_name, subscription_uri = _resolve_subscription_target(
        uri,
        request,
    )
    authorized_agent_name, agent_id = _authorize_subscription_agent(
        current_agent=current_agent,
        target_agent=agent_name,
        token_claims=_current_token_claims(allow_no_auth=allow_no_auth),
    )
    return ResolvedInboxSubscription(
        agent_name=authorized_agent_name,
        agent_id=agent_id,
        uri=subscription_uri,
    )


async def notify_inbox_updated(agent_name: str, *, agent_id: str | None = None) -> int:
    return await inbox_subscriptions.notify(agent_name, agent_id=agent_id)


async def notify_mentioned_inboxes(content: str | None) -> int:
    agents = mentioned_agents(content)
    if not agents:
        return 0
    deliveries = await asyncio.gather(
        *(notify_inbox_updated(agent_name) for agent_name in agents)
    )
    return sum(deliveries)


def _target_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("agent_name", "name", "handle", "username"):
        candidate = value.get(key)
        if candidate:
            return str(candidate)
    return None


def _target_agent_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("agent_id", "id"):
        candidate = value.get(key)
        if candidate:
            return str(candidate)
    return None


def _iter_candidate_targets(source: Any):
    if isinstance(source, dict):
        for key in (
            "recipient",
            "recipient_agent",
            "target_agent",
        ):
            candidate = source.get(key)
            if isinstance(candidate, dict):
                yield candidate
        for key in (
            "recipients",
            "recipient_agents",
            "target_agents",
            "effective_mentions",
            "mentions",
            "mentioned_agents",
        ):
            candidates = source.get(key)
            if isinstance(candidates, list):
                for candidate in candidates:
                    if isinstance(candidate, dict):
                        yield candidate
        for key in ("routing", "intelligence"):
            nested = source.get(key)
            if isinstance(nested, dict):
                yield from _iter_candidate_targets(nested)


def notification_targets_from_receipt(receipt: dict[str, Any]) -> list[tuple[str, str]]:
    """Return backend-resolved recipient targets as (agent_name, agent_id).

    The user/agent security boundary is the signed agent id, not the display
    handle. We intentionally ignore string-only routed_to/mention lists here
    because agent_name is not unique across user/space ownership boundaries.
    """
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in _iter_candidate_targets(receipt):
        agent_id = _target_agent_id(candidate)
        if not agent_id or agent_id in seen:
            continue
        agent_name = _target_name(candidate) or agent_id
        targets.append((normalize_agent_name(agent_name), agent_id))
        seen.add(agent_id)
    return targets


async def notify_message_inboxes(
    receipt: dict[str, Any],
    *,
    fallback_content: str | None,
) -> int:
    targets = notification_targets_from_receipt(receipt)
    if targets:
        deliveries = await asyncio.gather(
            *(
                notify_inbox_updated(agent_name, agent_id=agent_id)
                for agent_name, agent_id in targets
            )
        )
        return sum(deliveries)
    logger.debug(
        "No agent_id inbox notification targets in backend receipt; "
        "falling back to mention scan. receipt_keys=%s",
        sorted(receipt.keys()),
    )
    # Safe fallback for local no-auth harnesses and legacy test receipts.
    # Production subscriptions are keyed by signed agent_id, so name-only
    # fallback cannot wake another user-sponsored agent with the same handle.
    return await notify_mentioned_inboxes(fallback_content)


def register_inbox_notifications(
    mcp: FastMCP,
    *,
    enabled: bool = True,
    allow_no_auth: bool = False,
) -> None:
    if not enabled:
        logger.info(
            "Inbox resource notifications disabled; stateful Streamable HTTP is required"
        )
        return

    # FastMCP 3.3 does not expose public decorators for resource subscription
    # capability advertising, so these handlers use the underlying MCP server
    # API. TODO(FASTMCP-SUBSCRIBE-PUBLIC-API): replace this when FastMCP offers
    # public subscription capability registration. Validated against the
    # fastmcp[tasks]>=3.3.1,<3.4.0 requirement in this repo.
    if getattr(mcp._mcp_server, "_ax_inbox_subscribe_capability", False):
        return

    original_get_capabilities = mcp._mcp_server.get_capabilities
    if not callable(original_get_capabilities):
        raise RuntimeError(
            "FastMCP get_capabilities API changed; cannot advertise inbox subscriptions"
        )
    if not hasattr(mcp._mcp_server, "subscribe_resource") or not hasattr(
        mcp._mcp_server,
        "unsubscribe_resource",
    ):
        raise RuntimeError(
            "FastMCP resource subscription API changed; cannot register inbox subscriptions"
        )

    def get_capabilities(notification_options, experimental_capabilities):
        capabilities = original_get_capabilities(
            notification_options,
            experimental_capabilities,
        )
        if capabilities.resources is not None:
            capabilities.resources.subscribe = True
        return capabilities

    mcp._mcp_server.get_capabilities = get_capabilities

    # MCP registers one resources/subscribe handler for the whole server, not
    # per resource URI. Until FastMCP exposes resource-scoped subscription
    # routing, this global handler intentionally accepts only ax://inbox/* and
    # returns InvalidParams for other resource namespaces.
    @mcp._mcp_server.subscribe_resource()
    async def _subscribe_inbox(uri) -> None:
        ctx = mcp._mcp_server.request_context
        resolved = _resolve_subscription(
            str(uri),
            ctx.request,
            allow_no_auth=allow_no_auth,
        )
        inbox_subscriptions.subscribe(
            resolved.agent_name,
            resolved.uri,
            ctx.session,
            agent_id=resolved.agent_id,
        )

    @mcp._mcp_server.unsubscribe_resource()
    async def _unsubscribe_inbox(uri) -> None:
        ctx = mcp._mcp_server.request_context
        resolved = _resolve_subscription(
            str(uri),
            ctx.request,
            allow_no_auth=allow_no_auth,
        )
        inbox_subscriptions.unsubscribe(
            resolved.agent_name,
            resolved.uri,
            ctx.session,
            agent_id=resolved.agent_id,
        )

    mcp._mcp_server._ax_inbox_subscribe_capability = True
