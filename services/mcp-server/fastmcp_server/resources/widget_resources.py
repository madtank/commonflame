"""Registration for self-contained MCP widget HTML resources."""

from __future__ import annotations

from fastmcp import FastMCP

from fastmcp_server.mcp_ui import (
    get_widget_specs,
    get_widget_html_path,
    get_legacy_widget_resource_uri,
    resource_app_config,
    resource_meta,
)


def _register_widget_resource(mcp: FastMCP, widget_name: str) -> None:
    spec = get_widget_specs()[widget_name]
    html_path = get_widget_html_path(widget_name)

    @mcp.resource(
        spec.resource_uri,
        title=spec.title,
        description=spec.description,
        app=resource_app_config(),
        meta=resource_meta(widget_name),
    )
    async def _resource() -> str:
        return html_path.read_text(encoding="utf-8")


def _register_legacy_widget_resource(mcp: FastMCP, widget_name: str, legacy_uri: str) -> None:
    spec = get_widget_specs()[widget_name]
    html_path = get_widget_html_path(widget_name)

    @mcp.resource(
        legacy_uri,
        title=spec.title,
        description=f"{spec.description} (legacy URI alias)",
        app=resource_app_config(),
        meta=resource_meta(widget_name),
    )
    async def _legacy_resource() -> str:
        return html_path.read_text(encoding="utf-8")


def register_widget_resources(mcp: FastMCP):
    """Register static widget resources used by MCP UI tools."""
    for widget_name in get_widget_specs():
        _register_widget_resource(mcp, widget_name)
        legacy_uri = get_legacy_widget_resource_uri(widget_name)
        if legacy_uri:
            _register_legacy_widget_resource(mcp, widget_name, legacy_uri)
