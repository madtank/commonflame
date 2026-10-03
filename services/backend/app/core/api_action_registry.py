"""Action metadata registry for API-first mutation enforcement.

This is the build-time contract between route handlers and the action policy
matrix. Every mutating unified `/api/v1` route must declare either:

- a static `action_id`, or
- a resolver that maps normalized request state to a concrete `action_id`

Tests use this module to fail fast when a route has no metadata or resolves to
an unknown action.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi.routing import APIRoute


@dataclass(frozen=True)
class ResolvedRouteAction:
    action_family: str
    action_id: str
    target_resource_type: str | None = None
    target_resource_id: str | None = None
    target_resource_space_id: str | None = None


ActionResolver = Callable[..., ResolvedRouteAction | str]


@dataclass(frozen=True)
class RouteActionMetadata:
    action_family: str
    static_action_id: str | None = None
    resolver: ActionResolver | None = None
    target_resource_type: str | None = None


def declare_route_action(
    endpoint: Callable[..., Any],
    *,
    action_family: str,
    action_id: str | None = None,
    resolver: ActionResolver | None = None,
    target_resource_type: str | None = None,
) -> Callable[..., Any]:
    """Attach action metadata to a route handler."""
    if bool(action_id) == bool(resolver):
        raise ValueError("Declare exactly one of action_id or resolver")

    setattr(
        endpoint,
        "_ax_route_action_meta",
        RouteActionMetadata(
            action_family=action_family,
            static_action_id=action_id,
            resolver=resolver,
            target_resource_type=target_resource_type,
        ),
    )
    return endpoint


def get_route_action_metadata(endpoint: Callable[..., Any]) -> RouteActionMetadata | None:
    return getattr(endpoint, "_ax_route_action_meta", None)


def resolve_declared_route_action(
    endpoint: Callable[..., Any],
    **resolver_kwargs: Any,
) -> ResolvedRouteAction:
    """Resolve static or dynamic action metadata into a concrete action."""
    metadata = get_route_action_metadata(endpoint)
    if metadata is None:
        raise ValueError(f"No action metadata declared for endpoint {endpoint!r}")

    if metadata.static_action_id:
        return ResolvedRouteAction(
            action_family=metadata.action_family,
            action_id=metadata.static_action_id,
            target_resource_type=metadata.target_resource_type,
        )

    resolved = metadata.resolver(**resolver_kwargs)
    if isinstance(resolved, ResolvedRouteAction):
        return resolved
    return ResolvedRouteAction(
        action_family=metadata.action_family,
        action_id=str(resolved),
        target_resource_type=metadata.target_resource_type,
    )


def iter_declared_mutation_routes(router: Any) -> list[APIRoute]:
    """Return all mutating HTTP routes from a FastAPI router."""
    mutating_methods = {"POST", "PUT", "PATCH", "DELETE"}
    return [
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.methods and route.methods.intersection(mutating_methods)
    ]


@lru_cache(maxsize=1)
def load_action_policy_matrix() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    matrix_path = repo_root / "specs" / "DRAFT-001" / "action_policy_matrix.yaml"
    with matrix_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@lru_cache(maxsize=1)
def known_action_ids() -> set[str]:
    matrix = load_action_policy_matrix()
    return {row["action_id"] for row in matrix.get("actions", [])}


@lru_cache(maxsize=1)
def no_bypass_action_ids() -> set[str]:
    matrix = load_action_policy_matrix()
    return set(matrix.get("no_bypass_actions", []))
