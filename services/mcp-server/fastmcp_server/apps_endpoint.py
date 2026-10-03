"""REST endpoint for serving MCP App widget HTML to frontends.

Serves widget HTML files directly over HTTP so frontends can render
MCP Apps without needing a full MCP client. Widget data is fetched
separately by the widget itself via callServerTool().

Routes:
    GET /apps/          → JSON registry of available widgets
    GET /apps/{name}    → Widget HTML (by tool name or slug)
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from fastmcp_server.mcp_ui import (
    D3_VERSION, MCP_APPS_BRIDGE_VERSION, WIDGET_STATIC_DIR,
    get_widget_manifest, get_widget_specs, load_widget_html,
)


async def apps_bridge(_request: Request) -> FileResponse:
    """Serve the immutable, integrity-verified MCP Apps browser bundle."""
    bundle = WIDGET_STATIC_DIR.parent / "vendor" / "ext-apps" / MCP_APPS_BRIDGE_VERSION / "app-with-deps.js"
    return FileResponse(bundle, media_type="text/javascript", headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })


async def apps_d3(_request: Request) -> FileResponse:
    """Serve the pinned standalone D3 bundle used by the context graph."""
    bundle = WIDGET_STATIC_DIR.parent / "vendor" / "d3" / D3_VERSION / "d3.min.js"
    return FileResponse(bundle, media_type="text/javascript", headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })


def _widget_registry_item_contract(item: dict) -> dict | None:
    """Return host-facing form metadata for an /apps registry item."""
    if item.get("action_forms"):
        return item["action_forms"]
    if item.get("tool_name") == "context":
        # Context keeps the richest contract beside its tool implementation so
        # the governed catalog forms stay in lockstep with validation logic.
        from fastmcp_server.tools.context import _context_action_forms

        return _context_action_forms()["ax/actionForms"]
    return None


async def apps_list(request: Request) -> JSONResponse:
    """Return registry of available widgets, grouped by tool."""
    registry: dict[str, dict] = {}
    for item in get_widget_manifest():
        registry_item = {
            "tool_name": item["tool_name"],
            "resource_uri": item["resource_uri"],
            "title": item["title"],
            "description": item["description"],
            "primitive": item["primitive"],
            "surface": item["surface"],
            "actions": item["actions"],
        }
        contract = _widget_registry_item_contract(item)
        if contract:
            registry_item["action_forms"] = contract
        registry[item["name"]] = registry_item
    return JSONResponse(registry)


async def apps_get(request: Request) -> HTMLResponse | JSONResponse:
    """Serve widget HTML by tool name or slug.

    Accepts either the tool name (e.g., "tasks") or the filename slug
    (e.g., "task-board"). No auth required — widget HTML is static and
    contains no sensitive data. All interactivity goes through
    callServerTool() which requires authentication.
    """
    name = request.path_params.get("name", "")

    if not name:
        return await apps_list(request)

    # Look up by tool name first (e.g., "tasks" → task-board.html)
    widget_specs = get_widget_specs()
    spec = widget_specs.get(name)

    if not spec:
        # Try matching by filename slug (e.g., "task-board" → tasks)
        for s in widget_specs.values():
            slug = s.filename.removesuffix(".html")
            if slug == name:
                spec = s
                break

    if not spec:
        return JSONResponse(
            {"error": f"Widget '{name}' not found"},
            status_code=404,
        )

    html_path = WIDGET_STATIC_DIR / spec.filename
    if not html_path.is_file():
        return JSONResponse(
            {"error": f"Widget HTML not found: {spec.filename}"},
            status_code=404,
        )

    html = load_widget_html(html_path)
    response = HTMLResponse(
        content=html,
        media_type="text/html;profile=mcp-app",
    )
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response
