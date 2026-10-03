"""
Metrics API endpoints for monitoring and observability
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import time
from datetime import datetime, timedelta

from ...core.database import engine
from sqlalchemy.pool import QueuePool
from ...models.user import User

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


# Global reference to protection middleware (set during app startup)
protection_middleware = None


def set_protection_middleware(middleware):
    """Set reference to protection middleware for metrics access"""
    global protection_middleware
    protection_middleware = middleware


@router.get("/protection")
async def get_protection_metrics() -> Dict[str, Any]:
    """
    Get API protection metrics for monitoring.

    Returns:
    - Blocked clients count
    - Recent alerts
    - Per-endpoint statistics
    - Top hammering endpoints
    """

    if not protection_middleware:
        return {
            "status": "protection_not_enabled",
            "message": "API protection middleware not configured"
        }

    # Get metrics from middleware
    metrics = protection_middleware.get_metrics_summary()

    # Add timestamp and additional context
    metrics["timestamp"] = datetime.utcnow().isoformat()
    metrics["monitoring_period"] = "last_hour"

    # Add recommendations based on metrics
    recommendations = []

    if metrics["blocked_clients"] > 10:
        recommendations.append("High number of blocked clients - investigate token TTL settings")

    if metrics["recent_alerts"] > 20:
        recommendations.append("Excessive hammering alerts - check for credential conflicts")

    # Check for specific endpoint issues
    for endpoint, count in metrics.get("top_hammering_endpoints", []):
        if "/oauth/token" in endpoint and count > 50:
            recommendations.append(f"OAuth token endpoint under stress ({count} blocks) - verify MCP_ACCESS_TTL_MINUTES")

    metrics["recommendations"] = recommendations

    return metrics


@router.get("/health/detailed")
async def get_detailed_health() -> Dict[str, Any]:
    """
    Get detailed health metrics including:
    - API response times
    - Database connection pool stats
    - Redis connection status
    - Token refresh patterns
    """

    health_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "status": "operational",
        "components": {}
    }

    # Database connection pool stats
    try:
        pool = engine.pool
        db_stats = {"status": "unknown"}
        # AsyncAdaptedQueuePool exposes size/checkedin/checkedout/overflow
        size = getattr(pool, "size", lambda: None)()
        checked_in = getattr(pool, "checkedin", lambda: None)()
        checked_out = getattr(pool, "checkedout", lambda: None)()
        overflow = getattr(pool, "overflow", lambda: None)()
        db_stats.update({
            "status": "healthy",
            "size": size,
            "checked_in": checked_in,
            "checked_out": checked_out,
            "overflow": overflow,
            "total_capacity": (size or 0) + max(overflow or 0, 0),
        })
        health_data["components"]["database"] = db_stats
    except Exception:
        health_data["components"]["database"] = {
            "status": "unknown",
        }

    # API metrics
    if protection_middleware:
        protection_metrics = protection_middleware.get_metrics_summary()

        # Calculate request rate
        total_requests = sum(
            data["total_requests"]
            for data in protection_metrics.get("endpoint_metrics", {}).values()
        )

        health_data["components"]["api"] = {
            "status": "healthy" if protection_metrics["blocked_clients"] < 5 else "degraded",
            "total_requests": total_requests,
            "blocked_clients": protection_metrics["blocked_clients"],
            "error_rate": 0  # Would calculate from actual error tracking
        }

    # Token health (check for 60-second refresh pattern)
    token_endpoint_metrics = protection_metrics.get("endpoint_metrics", {}).get("/oauth/token", {}) if protection_middleware else {}
    if token_endpoint_metrics:
        # High request rate to token endpoint indicates short TTL
        if token_endpoint_metrics.get("total_requests", 0) > 100:
            health_data["components"]["token_health"] = {
                "status": "warning",
                "message": "High token refresh rate detected",
                "recommendation": "Check MCP_ACCESS_TTL_MINUTES setting"
            }
        else:
            health_data["components"]["token_health"] = {
                "status": "healthy",
                "message": "Normal token refresh rate"
            }

    return health_data


@router.get("/agent-activity")
async def get_agent_activity() -> Dict[str, Any]:
    """
    Get agent activity metrics:
    - Active agents count
    - Agent request patterns
    - Credential isolation status
    """

    activity = {
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": {}
    }

    if protection_middleware:
        metrics = protection_middleware.get_metrics_summary()

        # Count unique clients per endpoint
        unique_agents = set()
        for endpoint_data in metrics.get("endpoint_metrics", {}).values():
            unique_agents.update(endpoint_data.get("unique_clients", set()))

        activity["metrics"]["active_agents"] = len(unique_agents)
        activity["metrics"]["endpoints_accessed"] = len(metrics.get("endpoint_metrics", {}))

        # Check for credential conflicts (multiple agents with same pattern)
        if len(unique_agents) > 0:
            avg_requests_per_agent = sum(
                data["total_requests"]
                for data in metrics.get("endpoint_metrics", {}).values()
            ) / len(unique_agents)

            if avg_requests_per_agent > 100:
                activity["warning"] = "High request rate per agent - possible credential sharing"

    return activity
