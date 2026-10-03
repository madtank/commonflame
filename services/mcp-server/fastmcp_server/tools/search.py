"""Search tool for FastMCP server.

Provides cross-platform message search. Read-only.
All operations route through backend API (ROUTER-001).
Uses CurrentAccessToken() DI to forward backend-issued JWTs.
"""

import logging
import re
from typing import Annotated, Any, Optional

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.server.dependencies import CurrentAccessToken, CurrentRequest
from fastmcp.server.auth import AccessToken
from fastmcp.tools import ToolResult
from starlette.requests import Request

from fastmcp_server.api_client import api_request, extract_agent_context
from fastmcp_server.mcp_ui import (
    build_action,
    build_tool_output,
    read_only_annotations,
    tool_app_config,
    tool_meta,
    tool_output_schema,
    widget_tool_result,
)

logger = logging.getLogger(__name__)
_SEARCH_SNIPPET_LIMIT = 220


def _truncate_text(value: Any, limit: int = _SEARCH_SNIPPET_LIMIT) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_search_result(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    content = normalized.pop("content", None)
    snippet = normalized.get("snippet") or normalized.get("preview") or content
    if snippet is not None:
        normalized["snippet"] = _truncate_text(snippet)
    highlights = normalized.get("highlights")
    if isinstance(highlights, list):
        normalized["highlights"] = [_truncate_text(entry, 160) for entry in highlights if entry]
    elif isinstance(highlights, str) and highlights.strip():
        normalized["highlights"] = [_truncate_text(highlights, 160)]
    elif normalized.get("snippet"):
        normalized["highlights"] = [normalized["snippet"]]
    return normalized


def _search_actions(query: str, limit: int, offset: int, has_more: bool) -> list[dict[str, Any]]:
    actions = [
        build_action(
            "refresh-search",
            "Refresh",
            kind="tool",
            target="search",
            args={"query": query, "limit": limit, "offset": offset},
            style="secondary",
            enabled=True,
            idempotent=True,
        )
    ]
    if has_more:
        actions.append(
            build_action(
                "next-page",
                "Next page",
                kind="tool",
                target="search",
                args={"query": query, "limit": limit, "offset": offset + limit},
                style="primary",
                enabled=True,
                idempotent=True,
            )
        )
    return actions


def _search_widget_result(result: dict[str, Any], query: str, limit: int, offset: int) -> ToolResult:
    results = result.get("results")
    if not isinstance(results, list):
        results = result.get("messages", [])
    if not isinstance(results, list):
        results = []
    items = [_normalize_search_result(item) for item in results if isinstance(item, dict)]
    total = result.get("total", result.get("count", len(items)))
    count = result.get("count", len(items))
    has_more = bool(result.get("has_more")) or (offset + len(items) < total)
    structured = build_tool_output(
        "search_results",
        2,
        "empty" if not items else "ready",
        {
            "query": query,
            "items": items,
            "count": count,
            "total": total,
            "has_more": has_more,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "next_offset": offset + limit if has_more else None,
            },
        },
        actions=_search_actions(query, limit, offset, has_more),
    )
    return widget_tool_result("search", action="search", content=result, structured_content=structured)


def register_search_tool(mcp: FastMCP):

    @mcp.tool(
        annotations=read_only_annotations(),
        app=tool_app_config("search"),
        meta=tool_meta("search"),
        output_schema=tool_output_schema("search"),
    )
    async def search(
        query: Annotated[
            str,
            Field(
                description=(
                    "Full-text search query matched against message content "
                    "in the active space."
                )
            ),
        ],
        limit: Annotated[
            int,
            Field(
                default=20,
                ge=1,
                le=100,
                description="Maximum results to return per page.",
            ),
        ] = 20,
        offset: Annotated[
            int,
            Field(
                default=0,
                ge=0,
                description="Result offset for paging through more matches.",
            ),
        ] = 0,
        channel: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Restrict matches to a single channel name.",
            ),
        ] = None,
        sender_type: Annotated[
            Optional[str],
            Field(
                default=None,
                description='Restrict matches by sender kind: "user" or "agent".',
            ),
        ] = None,
        date_from: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Earliest message timestamp to include (ISO 8601, e.g. "
                    "2026-06-01 or 2026-06-01T00:00:00Z)."
                ),
            ),
        ] = None,
        date_to: Annotated[
            Optional[str],
            Field(
                default=None,
                description="Latest message timestamp to include (ISO 8601).",
            ),
        ] = None,
        space_id: Annotated[
            Optional[str],
            Field(
                default=None,
                description=(
                    "Accepted for MCP client compatibility; the searched space "
                    "is resolved from the authenticated session."
                ),
            ),
        ] = None,
        token: AccessToken = CurrentAccessToken(),
        request: Request = CurrentRequest(),
    ) -> ToolResult | dict:
        """Read-only full-text search over message history in the active space.

        Use this to find prior discussion, decisions, or links instead of
        paging through messages(check). Narrow large result sets with
        channel, sender_type, or date_from/date_to, and page with
        limit/offset. Returns matched messages with sender and timestamp;
        it never modifies any state.
        """
        ctx = extract_agent_context(token, request)

        payload = {"query": query, "limit": limit, "offset": offset}
        if channel:
            payload["channel"] = channel
        if sender_type:
            payload["sender_type"] = sender_type
        if date_from:
            payload["date_from"] = date_from
        if date_to:
            payload["date_to"] = date_to

        result = await api_request(
            "POST", "/api/v1/search/messages", ctx["jwt"],
            json_data=payload,
            agent_name=ctx["agent_name"],
            agent_id=ctx.get("agent_id"),
            space_id=ctx["space_id"],
        )
        return _search_widget_result(result, query, limit, offset)
