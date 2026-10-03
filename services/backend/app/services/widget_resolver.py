"""
Widget Resolver Service

Resolves resource_uri values from ui.widget metadata into renderable HTML.
Uses Python string.Template — no extra dependencies.
"""

import html
import logging
from string import Template

logger = logging.getLogger(__name__)

# Content Security Policy for rendered widgets
WIDGET_CSP = "default-src 'none'; style-src 'unsafe-inline'"

# Legacy templates keyed by resource_uri prefix
_TEMPLATES: dict[str, Template] = {
    "ui://ask_ax/work-card.html": Template(
        '<div class="ax-work-card" data-lifecycle="$lifecycle">'
        '$summary_section'
        '$reply_section'
        "</div>"
    ),
}

# MCP App widget URIs emitted by dispatch_executor.py.
_MCP_WIDGET_TITLES: dict[str, str] = {
    # Canonical URIs (current MCP server format)
    "ui://tasks/board": "Task Board",
    "ui://tasks/detail": "Task Detail",
    "ui://messages/timeline": "Message Timeline",
    "ui://agents/dashboard": "Agent Dashboard",
    "ui://spaces/navigator": "Space Navigator",
    "ui://search/results": "Search Results",
    "ui://context/explorer": "Context Explorer",
    "ui://whoami/identity": "Agent Identity",
    # Legacy aliases
    "ui://task-board": "Task Board",
    "ui://message-timeline": "Message Timeline",
    "ui://agent-dashboard": "Agent Dashboard",
    "ui://space-navigator": "Space Navigator",
    "ui://search-results": "Search Results",
    "ui://context-explorer": "Context Explorer",
}


def select_widget_meta(
    widget_meta: dict | None,
    *,
    resource_uri: str,
    tool_call_id: str | None = None,
) -> dict | None:
    """Select the best widget payload for a resource request.

    Multi-widget messages store child widget payloads under ui.widget.widgets.
    Resolve requests need the matching child entry, not always the top-level
    aggregate widget.
    """
    if not isinstance(widget_meta, dict):
        return widget_meta

    nested_widgets = widget_meta.get("widgets")
    if not isinstance(nested_widgets, list):
        return widget_meta

    for candidate in nested_widgets:
        if not isinstance(candidate, dict):
            continue
        if tool_call_id and candidate.get("tool_call_id") == tool_call_id:
            return candidate
        if candidate.get("resource_uri") == resource_uri:
            return candidate

    return widget_meta


def _preview_items(values: list[dict], *fields: str) -> str:
    lines = []
    for item in values[:5]:
        if not isinstance(item, dict):
            continue
        parts = []
        for field in fields:
            value = item.get(field)
            if value:
                parts.append(str(value))
        if parts:
            lines.append(html.escape(" - ".join(parts)))
    if not lines:
        return ""
    return "".join(f"<li>{line}</li>" for line in lines)


def _render_structured_preview(structured_content: dict | None) -> str:
    if not isinstance(structured_content, dict) or not structured_content:
        return ""

    preview = ""
    if isinstance(structured_content.get("tasks"), list):
        preview = _preview_items(structured_content["tasks"], "title", "status")
    elif isinstance(structured_content.get("messages"), list):
        preview = _preview_items(structured_content["messages"], "display_name", "content")
    elif isinstance(structured_content.get("items"), list):
        preview = _preview_items(structured_content["items"], "name", "title", "key", "id")
    elif isinstance(structured_content.get("results"), list):
        preview = _preview_items(structured_content["results"], "display_name", "content", "id")
    elif isinstance(structured_content.get("spaces"), list):
        preview = _preview_items(structured_content["spaces"], "name", "slug", "id")

    if preview:
        return f'<ul class="ax-widget-list">{preview}</ul>'

    # Unknown shape — return empty string so _render_mcp_widget falls back to
    # the placeholder. The frontend's isBackendWidgetShellHtml matches the
    # placeholder and fetches the real widget HTML directly from the MCP
    # server via resources/read. Rendering raw JSON here would show as
    # "<pre>{...}</pre>" in the iframe and bypass the MCP app — see aX task
    # 253fb5d1 (widget JSON regression).
    return ""


def _render_agents_dashboard(structured_content: dict | None) -> str:
    if not isinstance(structured_content, dict):
        return ""

    items = structured_content.get("items")
    if not isinstance(items, list) or not items:
        return ""

    cards = []
    for item in items:
        if not isinstance(item, dict):
            continue

        name = html.escape(str(item.get("name") or item.get("id") or "Unknown agent"))
        agent_type = html.escape(str(item.get("agent_type") or item.get("origin") or "agent"))
        availability = html.escape(
            str(item.get("availability_confidence") or item.get("status") or "unknown")
        )
        connection_type = item.get("connection_type")
        connection = (
            html.escape(str(connection_type))
            if isinstance(connection_type, str) and connection_type.strip()
            else None
        )
        description = item.get("description") or item.get("specialization") or item.get("bio")
        description_html = ""
        if isinstance(description, str) and description.strip():
            description_html = (
                f'<p class="ax-agent-description">{html.escape(description.strip())}</p>'
            )

        meta_bits = [
            f'<span class="ax-agent-chip">{agent_type}</span>',
            f'<span class="ax-agent-chip">{availability}</span>',
        ]
        if connection:
            meta_bits.append(f'<span class="ax-agent-chip">{connection}</span>')

        identifier = item.get("id")
        identifier_html = ""
        if isinstance(identifier, str) and identifier.strip():
            identifier_html = (
                f'<p class="ax-agent-id"><code>{html.escape(identifier)}</code></p>'
            )

        cards.append(
            '<article class="ax-agent-card">'
            f'<div class="ax-agent-card-header"><h3 class="ax-agent-name">{name}</h3></div>'
            f'<div class="ax-agent-meta">{"".join(meta_bits)}</div>'
            f"{description_html}"
            f"{identifier_html}"
            "</article>"
        )

    if not cards:
        return ""

    return (
        "<style>"
        ".ax-agent-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:12px;}"
        ".ax-agent-card{border:1px solid #d7dbe3;border-radius:12px;padding:12px;background:#ffffff;color:#111827;"
        "box-shadow:0 1px 2px rgba(15,23,42,0.06);}"
        ".ax-agent-card-header{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:8px;}"
        ".ax-agent-name{margin:0;font-size:16px;line-height:1.3;font-weight:600;}"
        ".ax-agent-meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;}"
        ".ax-agent-chip{display:inline-flex;align-items:center;border-radius:999px;padding:2px 8px;background:#eef2ff;"
        "color:#334155;font-size:12px;line-height:1.4;}"
        ".ax-agent-description{margin:0 0 8px 0;font-size:13px;line-height:1.5;color:#334155;}"
        ".ax-agent-id{margin:0;font-size:12px;line-height:1.4;color:#64748b;word-break:break-all;}"
        ".ax-agent-id code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#f8fafc;padding:1px 4px;border-radius:4px;}"
        "</style>"
        f'<section class="ax-agent-grid">{"".join(cards)}</section>'
    )


def _render_mcp_widget(resource_uri: str, widget: dict, meta: dict) -> dict:
    lifecycle = html.escape(widget.get("lifecycle", "complete"))
    title = html.escape(widget.get("title") or _MCP_WIDGET_TITLES.get(resource_uri, resource_uri))
    summary = html.escape(meta.get("ai_summary") or widget.get("title") or "")
    structured_content = widget.get("structured_content") or widget.get("initial_data")

    # Error lifecycle: render error state with error_summary
    if lifecycle == "error":
        error_summary = html.escape(widget.get("error_summary") or "Widget failed to load")
        rendered = (
            f'<div class="ax-mcp-widget ax-widget-error" data-resource-uri="{html.escape(resource_uri)}" '
            f'data-lifecycle="error">'
            f'<h2 class="ax-widget-title">{title}</h2>'
            f'<p class="ax-widget-error-summary">{error_summary}</p>'
            "</div>"
        )
        return {
            "html": rendered,
            "title": widget.get("title") or _MCP_WIDGET_TITLES.get(resource_uri, resource_uri),
            "csp": WIDGET_CSP,
        }

    count_value = None
    if isinstance(structured_content, dict):
        count_value = (
            structured_content.get("count")
            or structured_content.get("total")
            or structured_content.get("team_agents_total")
        )
    count_html = ""
    if count_value is not None:
        count_html = f'<p class="ax-widget-count">Count: {html.escape(str(count_value))}</p>'

    summary_html = f'<p class="ax-widget-summary">{summary}</p>' if summary else ""
    if resource_uri in {"ui://agents/dashboard", "ui://agent-dashboard"}:
        body_html = _render_agents_dashboard(structured_content)
    else:
        body_html = _render_structured_preview(structured_content)
    if not body_html:
        # Thin pointer: no structured_content available.  Render a placeholder
        # card that tells the frontend to fetch content from the MCP server.
        tool_call_id = widget.get("tool_call_id", "")
        placeholder_attrs = f' data-tool-call-id="{html.escape(tool_call_id)}"' if tool_call_id else ""
        error_summary = widget.get("error_summary")
        if error_summary:
            body_html = f'<p class="ax-widget-empty">{html.escape(error_summary)}</p>'
        else:
            body_html = (
                f'<p class="ax-widget-placeholder"{placeholder_attrs}>'
                f"Loading widget from <code>{html.escape(resource_uri)}</code>&hellip;"
                "</p>"
            )

    rendered = (
        f'<div class="ax-mcp-widget" data-resource-uri="{html.escape(resource_uri)}" '
        f'data-lifecycle="{lifecycle}">'
        f'<h2 class="ax-widget-title">{title}</h2>'
        f"{summary_html}"
        f"{count_html}"
        f"{body_html}"
        "</div>"
    )
    return {
        "html": rendered,
        "title": widget.get("title") or _MCP_WIDGET_TITLES.get(resource_uri, resource_uri),
        "csp": WIDGET_CSP,
    }


class WidgetResolver:
    """Resolves widget resource URIs to HTML."""

    def resolve(
        self,
        resource_uri: str,
        widget_meta: dict | None = None,
        message_meta: dict | None = None,
    ) -> dict | None:
        """Resolve a resource_uri to rendered HTML.

        Returns dict with {html, title, csp} or None if URI is unknown.
        """
        if resource_uri in _MCP_WIDGET_TITLES:
            return _render_mcp_widget(resource_uri, widget_meta or {}, message_meta or {})

        template = _TEMPLATES.get(resource_uri)
        if not template:
            logger.warning("WIDGET_RESOLVE_UNKNOWN uri=%s", resource_uri)
            return None

        widget = widget_meta or {}
        meta = message_meta or {}

        # Build template variables with safe defaults
        lifecycle = widget.get("lifecycle", "complete")
        title = html.escape(widget.get("title", ""))
        summary = html.escape(
            meta.get("ai_summary")
            or widget.get("title")
            or ""
        )

        # Build sections conditionally
        summary_section = f'<p class="ax-summary">{summary}</p>' if summary else ""
        reply_section = ""
        if widget.get("html"):
            # If widget already has inline HTML, use the resolver just for title/csp
            pass

        rendered = template.safe_substitute(
            lifecycle=lifecycle,
            title=title,
            summary=summary,
            summary_section=summary_section,
            reply_section=reply_section,
        )

        return {
            "html": rendered,
            "title": widget.get("title"),
            "csp": WIDGET_CSP,
        }


# Module-level singleton
widget_resolver = WidgetResolver()
