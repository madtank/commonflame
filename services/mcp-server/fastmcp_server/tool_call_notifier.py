"""ToolCallNotificationMiddleware — fire-and-forget POST to backend after every tool call.

Captures tool execution metadata (tool_name, resource_uri, status, duration, agent, space)
and POSTs it to the backend for audit trail and SSE broadcast. Non-blocking — failures
are logged but never affect tool results.

The backend uses this to:
1. Store audit trail (who called what tool, when, in which space)
2. Broadcast SSE events (tool_call_completed) so the frontend can render widgets
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any

from fastmcp.server.dependencies import get_access_token, get_http_request
from fastmcp.server.middleware.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
import mcp.types as mt

from fastmcp_server.api_client import _is_frontend_user_session_token, api_request
from fastmcp_server.mcp_ui import get_widget_specs

logger = logging.getLogger(__name__)

# Endpoint on the backend that receives tool call notifications
TOOL_CALLS_ENDPOINT = "/api/v1/tool-calls"

# Note: duplicate-broadcast suppression for activity-stream noise (e.g. the
# same reminder card rendering twice within seconds) belongs at the broadcast
# layer (backend SSE filter), not here. The original salvage from
# widget-hermes-local placed dedup in this middleware, but POSTs to
# /api/v1/tool-calls drive both the audit trail AND the SSE broadcast on the
# backend, so deduping here silently loses legitimate audit entries when a
# user repeats a tool call within the dedup window. That trade-off was
# unsafe; this middleware now always POSTs.


def _extract_task_activity_context(
    initial_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract task context for activity/reminder alert cards.

    Reminder events are system/task activity addressed to an assignee. Include
    explicit target context so downstream renderers do not infer a sender/target
    from chat concepts such as a viewing-user pseudo-mention label.
    """
    if not isinstance(initial_data, dict):
        return {}
    data = (
        initial_data.get("data")
        if isinstance(initial_data.get("data"), dict)
        else initial_data
    )
    task = data.get("task") if isinstance(data, dict) else None
    if not isinstance(task, dict):
        return {}

    context: dict[str, Any] = {}
    task_id = task.get("id") or task.get("task_id")
    if task_id:
        context["task_id"] = str(task_id)
    task_title = task.get("title")
    if task_title:
        context["task_title"] = str(task_title)

    assignee = task.get("assignee")
    if isinstance(assignee, dict):
        assignee_id = assignee.get("id") or assignee.get("agent_id")
        assignee_handle = assignee.get("handle") or assignee.get("name")
        assignee_label = (
            assignee.get("display_name") or assignee.get("label") or assignee_handle
        )
        if assignee_id:
            context["target_agent_id"] = str(assignee_id)
            context["assigned_to"] = str(assignee_id)
        if assignee_handle:
            context["target_agent_handle"] = str(assignee_handle)
        if assignee_label:
            context["target_label"] = str(assignee_label)
    return context


def _safe_arguments(arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return arguments safe for the SSE broadcast.

    Keeps action, IDs, filters, queries — everything the frontend needs
    to replay the call or understand what happened. Strips large content
    fields (message bodies, descriptions) to keep the payload small.
    """
    if not arguments:
        return None
    # Fields that are safe and useful for the frontend
    safe_keys = {
        "action",
        "task_id",
        "query",
        "filter",
        "status",
        "priority",
        "limit",
        "offset",
        "key",
        "space_id",
        "channel",
        "sender_type",
        "date_from",
        "date_to",
        "reply_to",
        "message_id",
        "id",
        "assigned_agent_id",
        "assignee_id",
        "assigned_to",
        "mark_read",
        "curate",
        "reason",
        "wait",
        "bypass",
    }
    result = {}
    for k, v in arguments.items():
        if k in safe_keys:
            result[k] = v
        elif k in ("title",):
            # Keep title but truncate
            result[k] = str(v)[:100] if v else v
    return result if result else None


def _arguments_hash(arguments: dict[str, Any] | None) -> str | None:
    """SHA-256 hash of serialized arguments (no full args stored for privacy)."""
    if not arguments:
        return None
    try:
        raw = json.dumps(arguments, sort_keys=True, default=str)
        return f"sha256:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
    except Exception:
        return None


def _extract_result_kind(result: ToolResult) -> str | None:
    """Extract the structuredContent.kind from a tool result."""
    try:
        sc = result.structured_content
        if isinstance(sc, dict):
            return sc.get("kind")
    except Exception as exc:
        logger.debug("Unable to extract tool result kind", exc_info=exc)
    return None


def _extract_initial_data(result: ToolResult) -> dict[str, Any] | None:
    """Extract the structured initial widget payload from a tool result."""
    try:
        sc = result.structured_content
        if isinstance(sc, dict) and sc:
            return sc
    except Exception as exc:
        logger.debug("Unable to extract initial tool result data", exc_info=exc)
    return None


def _resolve_resource_uri(tool_name: str, action: str | None = None) -> str | None:
    """Look up the widget resource URI for a tool, action-specific when available."""
    from fastmcp_server.mcp_ui import ACTION_WIDGET_MAP

    key = tool_name
    if action and tool_name in ACTION_WIDGET_MAP:
        key = ACTION_WIDGET_MAP[tool_name].get(action, tool_name)
    spec = get_widget_specs().get(key)
    return spec.resource_uri if spec else None


class ToolCallNotificationMiddleware(Middleware):
    """Fire-and-forget POST to backend after every tools/call completion.

    Captures: tool_name, tool_call_id, resource_uri, status, duration_ms,
    agent_name, space_id, arguments_hash, timestamp.

    Non-blocking: uses asyncio.create_task() so the tool result returns
    immediately. If the POST fails, it's logged and ignored.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = context.message.name
        arguments = context.message.arguments
        tool_call_id = str(uuid.uuid4())
        start = time.monotonic()

        # Execute the actual tool call
        try:
            result = await call_next(context)
            status = "success"
        except Exception as exc:
            status = "error"
            duration_ms = round((time.monotonic() - start) * 1000)
            # Still notify on error, then re-raise
            self._fire_notification(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                arguments=arguments,
                status=status,
                duration_ms=duration_ms,
            )
            raise exc

        duration_ms = round((time.monotonic() - start) * 1000)

        # Extract context-specific render payload from result
        result_kind = _extract_result_kind(result)
        initial_data = _extract_initial_data(result)

        # Fire notification in background — don't block the tool result
        self._fire_notification(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=arguments,
            status=status,
            duration_ms=duration_ms,
            result_kind=result_kind,
            initial_data=initial_data,
        )

        return result

    def _fire_notification(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any] | None,
        status: str,
        duration_ms: int,
        result_kind: str | None = None,
        initial_data: dict[str, Any] | None = None,
    ) -> None:
        """Spawn a background task to POST the notification."""
        # Extract agent context from DI (may be None if auth is disabled)
        token = get_access_token()
        request = get_http_request()

        jwt = token.token if token else None
        agent_id = None
        agent_name = None
        space_id = None

        correlation_id = None

        if token:
            agent_id = token.claims.get("agent_id")
            agent_name = token.claims.get("agent_name")
            space_id = token.claims.get("space_id")
            correlation_id = token.claims.get("correlation_id") or token.claims.get(
                "dispatch_id"
            )

        header_agent_name = request.headers.get("x-agent-name") if request else None

        # Audit only independently issued, signed agent identities. Browser
        # credentials and mutable route labels never become agent authorship.
        principal_is_agent = bool(
            token and agent_id and not _is_frontend_user_session_token(token.claims)
        )

        if not principal_is_agent:
            # Return before building the audit payload — viewers never POST to
            # the shared audit endpoint.
            logger.debug(
                "Viewer-private tool call %s — token asserts no agent principal; "
                "skipping shared audit/broadcast (no /api/v1/tool-calls POST)",
                tool_name,
            )
            return

        if request:
            agent_name = agent_name or header_agent_name
            space_id = space_id or request.headers.get("x-space-id")
            correlation_id = (
                correlation_id
                or request.headers.get("x-correlation-id")
                or request.headers.get("x-dispatch-id")
            )

        # Extract action from arguments (e.g., "list", "create", "get")
        action = arguments.get("action") if arguments else None

        payload = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "resource_uri": _resolve_resource_uri(tool_name, action),
            "tool_action": action,
            "kind": result_kind,
            "initial_data": initial_data,
            "status": status,
            "duration_ms": duration_ms,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "space_id": space_id,
            "correlation_id": correlation_id,
            "arguments": _safe_arguments(arguments),
            "arguments_hash": _arguments_hash(arguments),
        }
        payload.update(_extract_task_activity_context(initial_data))

        asyncio.create_task(self._post_notification(payload, jwt))

    @staticmethod
    async def _post_notification(payload: dict[str, Any], jwt: str | None) -> None:
        """POST to backend. Swallow all errors — this is fire-and-forget."""
        try:
            if not jwt:
                logger.debug("Skipping tool-call notification (no JWT)")
                return

            if not payload.get("agent_name") and not payload.get("agent_id"):
                logger.debug(
                    "Skipping tool-call notification for %s (no agent identity "
                    "— backend requires authenticated agent context)",
                    payload.get("tool_name"),
                )
                return

            await api_request(
                "POST",
                TOOL_CALLS_ENDPOINT,
                jwt,
                json_data=payload,
                agent_name=payload.get("agent_name"),
                agent_id=payload.get("agent_id"),
                space_id=payload.get("space_id"),
                timeout=5.0,
            )
            logger.debug(
                "Tool call notification sent: %s/%s (%dms)",
                payload["tool_name"],
                payload["tool_call_id"],
                payload["duration_ms"],
            )
        except Exception:
            logger.warning(
                "Failed to send tool call notification for %s (non-blocking)",
                payload.get("tool_name"),
                exc_info=False,
            )
