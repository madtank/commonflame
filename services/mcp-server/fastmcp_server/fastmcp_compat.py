"""FastMCP import compatibility helpers.

FastMCP moved MCP Apps metadata classes between import paths across v3
releases. Keep the server importable with either layout while using the
new public path when it is available.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


def load_app_config_types() -> tuple[type[Any], type[Any]]:
    """Return FastMCP AppConfig and ResourceCSP across supported v3 layouts."""
    last_error: BaseException | None = None
    for module_name in ("fastmcp.apps", "fastmcp.server.apps"):
        try:
            module = import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                last_error = exc
                continue
            raise
        try:
            return module.AppConfig, module.ResourceCSP
        except AttributeError as exc:
            last_error = exc
            continue

    raise ImportError(
        "Unable to import FastMCP AppConfig and ResourceCSP from "
        "fastmcp.apps or fastmcp.server.apps"
    ) from last_error


AppConfig, ResourceCSP = load_app_config_types()
