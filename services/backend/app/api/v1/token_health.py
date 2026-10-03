"""
Token Health Monitoring Endpoints
Provides visibility into token refresh patterns and potential issues
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, Dict, List
import redis.asyncio as redis
import json
import time
from datetime import datetime, timedelta
import logging
from app.middleware.token_refresh_rate_limiter import get_rate_limiter
from .admin import require_admin_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/token-health", tags=["monitoring"])

# Redis connection
REDIS_URL = "redis://redis_new:6379/0"


@router.get("/refresh-stats")
async def get_refresh_statistics(
    agent_id: Optional[str] = Query(None, description="Specific agent to get stats for"),
    include_details: bool = Query(False, description="Include detailed breakdown"),
    _admin=Depends(require_admin_role),
) -> Dict:
    """
    Get token refresh statistics

    Returns current state of token refresh rate limiting and any ongoing issues
    """
    try:
        # Get rate limiter instance
        rate_limiter = await get_rate_limiter()

        # Get basic stats
        stats = await rate_limiter.get_stats(agent_id)

        # Add health status
        stats["health_status"] = "healthy"
        stats["issues"] = []

        # Check for any agents in backoff
        if include_details and rate_limiter.redis_client:
            try:
                redis_client = rate_limiter.redis_client
                pattern = "rate:refresh_limit:*"
                cursor = 0
                agents_in_backoff = []
                total_violations = 0

                while True:
                    cursor, keys = await redis_client.scan(
                        cursor,
                        match=pattern,
                        count=100
                    )

                    for key in keys:
                        data = await redis_client.get(key)
                        if data:
                            try:
                                limit_data = json.loads(data)
                                backoff_until = limit_data.get("backoff_until", 0)
                                current_time = time.time()

                                if backoff_until > current_time:
                                    agent = key.replace("rate:refresh_limit:", "")
                                    agents_in_backoff.append({
                                        "agent": agent,
                                        "backoff_remaining": int(backoff_until - current_time),
                                        "violations": limit_data.get("violations", 0)
                                    })
                                    stats["health_status"] = "degraded"

                                violations = limit_data.get("violations", 0)
                                if violations > 0:
                                    total_violations += violations
                            except json.JSONDecodeError:
                                continue

                    if cursor == 0:
                        break

                if agents_in_backoff:
                    stats["agents_in_backoff"] = agents_in_backoff
                    stats["issues"].append({
                        "type": "agents_rate_limited",
                        "severity": "warning",
                        "count": len(agents_in_backoff),
                        "message": f"{len(agents_in_backoff)} agents currently in backoff"
                    })

                if total_violations > 10:
                    stats["health_status"] = "unhealthy"
                    stats["issues"].append({
                        "type": "high_violation_rate",
                        "severity": "critical",
                        "total_violations": total_violations,
                        "message": "High number of rate limit violations detected"
                    })

                stats["total_violations"] = total_violations

            except Exception as e:
                logger.error(f"Error getting detailed stats: {e}")
                stats["details_error"] = str(e)

        return stats

    except Exception as e:
        logger.error(f"Error getting refresh statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alerts")
async def get_recent_alerts(
    hours: int = Query(1, description="Number of hours to look back", ge=1, le=24),
    _admin=Depends(require_admin_role),
) -> Dict:
    """
    Get recent token storm alerts

    Returns alerts generated in the specified time window
    """
    try:
        redis_client = await redis.from_url(REDIS_URL, decode_responses=True)

        # Calculate time window
        current_time = time.time()
        window_start = current_time - (hours * 3600)

        # Find alert keys
        pattern = "alert:token_storm:*"
        cursor = 0
        alerts = []

        while True:
            cursor, keys = await redis_client.scan(
                cursor,
                match=pattern,
                count=100
            )

            for key in keys:
                # Extract timestamp from key
                parts = key.split(":")
                if len(parts) >= 4:
                    try:
                        alert_time = int(parts[-1])
                        if alert_time >= window_start:
                            # Get alert data
                            alert_data = await redis_client.get(key)
                            if alert_data:
                                alert = json.loads(alert_data)
                                alerts.append(alert)
                    except (ValueError, json.JSONDecodeError):
                        continue

            if cursor == 0:
                break

        # Sort alerts by timestamp
        alerts.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        await redis_client.close()

        return {
            "time_window_hours": hours,
            "alert_count": len(alerts),
            "alerts": alerts,
            "status": "critical" if len(alerts) > 5 else "warning" if alerts else "ok"
        }

    except Exception as e:
        logger.error(f"Error getting alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def get_token_system_health(_admin=Depends(require_admin_role)) -> Dict:
    """
    Get overall health of the token system

    Combines multiple health indicators into a single status
    """
    try:
        health = {
            "timestamp": datetime.utcnow().isoformat(),
            "status": "healthy",
            "components": {}
        }

        # Check rate limiter health
        try:
            rate_limiter = await get_rate_limiter()
            stats = await rate_limiter.get_stats()

            health["components"]["rate_limiter"] = {
                "status": "healthy",
                "agents_tracked": stats.get("total_agents_tracked", 0)
            }
        except Exception as e:
            health["components"]["rate_limiter"] = {
                "status": "unhealthy",
                "error": str(e)
            }
            health["status"] = "degraded"

        # Check Redis connection
        try:
            redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
            await redis_client.ping()
            await redis_client.close()

            health["components"]["redis"] = {"status": "healthy"}
        except Exception as e:
            health["components"]["redis"] = {
                "status": "unhealthy",
                "error": str(e)
            }
            health["status"] = "unhealthy"

        # Check for recent storms
        try:
            redis_client = await redis.from_url(REDIS_URL, decode_responses=True)

            # Count recent alerts
            pattern = "alert:token_storm:*"
            cursor = 0
            alert_count = 0
            current_time = time.time()
            hour_ago = current_time - 3600

            while True:
                cursor, keys = await redis_client.scan(
                    cursor,
                    match=pattern,
                    count=100
                )

                for key in keys:
                    parts = key.split(":")
                    if len(parts) >= 4:
                        try:
                            alert_time = int(parts[-1])
                            if alert_time >= hour_ago:
                                alert_count += 1
                        except ValueError:
                            continue

                if cursor == 0:
                    break

            await redis_client.close()

            if alert_count > 0:
                health["status"] = "degraded" if alert_count < 5 else "unhealthy"
                health["components"]["storm_detection"] = {
                    "status": "warning" if alert_count < 5 else "critical",
                    "recent_storms": alert_count
                }
            else:
                health["components"]["storm_detection"] = {
                    "status": "healthy",
                    "recent_storms": 0
                }

        except Exception as e:
            health["components"]["storm_detection"] = {
                "status": "unknown",
                "error": str(e)
            }

        # Overall status determination
        component_statuses = [c.get("status", "unknown") for c in health["components"].values()]
        if "unhealthy" in component_statuses or "critical" in component_statuses:
            health["status"] = "unhealthy"
        elif "degraded" in component_statuses or "warning" in component_statuses:
            health["status"] = "degraded"

        return health

    except Exception as e:
        logger.error(f"Error getting system health: {e}")
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "status": "error",
            "error": str(e)
        }


@router.post("/reset-rate-limit")
async def reset_rate_limit(
    agent_id: str,
    reason: str = Query(..., description="Reason for manual reset"),
    _admin=Depends(require_admin_role),
) -> Dict:
    """
    Manually reset rate limit for a specific agent

    Use this endpoint to clear rate limiting for an agent that's stuck
    """
    try:
        redis_client = await redis.from_url(REDIS_URL, decode_responses=False)

        # Delete rate limit key
        key = f"rate:refresh_limit:{agent_id}"
        deleted = await redis_client.delete(key)

        # Log the reset
        reset_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent_id": agent_id,
            "reason": reason,
            "deleted": bool(deleted)
        }

        log_key = f"rate_limit_reset:{agent_id}:{int(time.time())}"
        await redis_client.setex(
            log_key,
            86400,  # Keep log for 24 hours
            json.dumps(reset_log)
        )

        await redis_client.close()

        logger.info(f"Rate limit reset for {agent_id}: {reason}")

        return {
            "status": "success",
            "agent_id": agent_id,
            "reset": bool(deleted),
            "message": f"Rate limit {'reset' if deleted else 'was not set'} for {agent_id}"
        }

    except Exception as e:
        logger.error(f"Error resetting rate limit: {e}")
        raise HTTPException(status_code=500, detail=str(e))
