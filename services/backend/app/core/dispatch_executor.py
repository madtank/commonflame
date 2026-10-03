"""
Unified Dispatch Executor

Single implementation for dispatching to cloud agent runners AND webhook agents.
Used by both:
- dispatch_worker.py (local development via Redis Streams)
- internal.py (GCP production via Cloud Tasks)

This ensures LOCAL and GCP use the EXACT SAME dispatch logic.
Fix bugs once, fixed everywhere.

Supports dispatch types:
- "cloud": HTTP POST to agent_runner (Gemini-based cloud agents)
- "webhook": HMAC-signed HTTP POST to webhook URL (external gateway agents)
- "agentcore": Bedrock AgentCore with Return of Control
- "space_agent": Local Space Agent container with streaming
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx
import uuid as uuid_module

from app.core.config import get_settings
from app.core.attachment_payload import attachments_from_dispatch_payload
from app.core.database import AsyncSessionLocal
from app.core.tool_call_cache import load_tool_call_initial_data

logger = logging.getLogger(__name__)

# Webhook dispatch configuration
# Allow long-running webhook agents (was hardcoded 30s, now 600s default)
# Future: support hours-long runs with heartbeat/checkpoint pattern
WEBHOOK_TIMEOUT_SECONDS = int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "600"))
MCP_ENDPOINT = os.getenv("MCP_ENDPOINT", "https://paxai.app")

# Circuit breaker: after N consecutive connect failures to the same URL,
# stop dispatching to it for a cooldown period. Prevents queue backlog storms
# when an agent_runner container is down.
CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_THRESHOLD", "3"))
CIRCUIT_BREAKER_COOLDOWN_SECS = int(os.getenv("CIRCUIT_BREAKER_COOLDOWN_SECS", "120"))

# Inline retry configuration for transient failures (e.g., Cloudflare DO resets)
# Retries happen immediately instead of waiting for Cloud Tasks exponential backoff
INLINE_RETRY_MAX_ATTEMPTS = int(os.getenv("INLINE_RETRY_MAX_ATTEMPTS", "3"))
INLINE_RETRY_DELAY_SECONDS = float(os.getenv("INLINE_RETRY_DELAY_SECONDS", "3.0"))
# Patterns that indicate a transient error worth retrying immediately
# Use specific phrases to avoid false positives
INLINE_RETRY_ERROR_PATTERNS = [
    "Durable Object reset because its code was updated",
    "Moltbot gateway failed to start",
]


async def _check_circuit_breaker(url: str) -> bool:
    """Returns True if circuit is open (should skip dispatch)."""
    if CIRCUIT_BREAKER_THRESHOLD <= 0:
        return False
    try:
        from app.core.redis_client import get_redis_client
        redis = await get_redis_client()
        failures = await redis.get(f"circuit:{url}")
        return failures is not None and int(failures) >= CIRCUIT_BREAKER_THRESHOLD
    except Exception:
        return False


async def _record_circuit_failure(url: str):
    """Record a connect failure for circuit breaker tracking."""
    try:
        from app.core.redis_client import get_redis_client
        redis = await get_redis_client()
        key = f"circuit:{url}"
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, CIRCUIT_BREAKER_COOLDOWN_SECS)
        await pipe.execute()
    except Exception:
        pass


async def _reset_circuit(url: str):
    """Clear circuit breaker on successful dispatch."""
    try:
        from app.core.redis_client import get_redis_client
        redis = await get_redis_client()
        await redis.delete(f"circuit:{url}")
    except Exception:
        pass


class DispatchStatus(Enum):
    """Result status of a dispatch attempt."""
    SUCCESS = "success"           # Agent completed, response saved
    SUCCESS_NO_RESPONSE = "success_no_response"  # Agent completed, no response content
    FAILED_PERMANENT = "failed_permanent"  # 4xx error, don't retry
    FAILED_RETRYABLE = "failed_retryable"  # 5xx/timeout, should retry
    FAILED_MISSING_URL = "failed_missing_url"  # No cloud_function_url or webhook_url
    FAILED_WEBHOOK_UNVERIFIED = "failed_webhook_unverified"  # Webhook not verified
    FAILED_CIRCUIT_OPEN = "failed_circuit_open"  # Circuit breaker tripped
    FAILED_FLEET_STOPPED = "failed_fleet_stopped"  # Fleet emergency stop is active


def _truncate_for_log(text: str, max_len: int = 200) -> str:
    """Truncate text for log output, converting newlines to spaces."""
    if not text:
        return ""
    preview = text[:max_len] + "..." if len(text) > max_len else text
    return preview.replace("\n", " ")


def _is_inline_retryable_error(status_code: int, error_text: str) -> bool:
    """Check if an error should be retried immediately (inline) instead of via Cloud Tasks."""
    if status_code != 503:
        return False
    return any(pattern in error_text for pattern in INLINE_RETRY_ERROR_PATTERNS)


async def _store_dispatch_state(
    dispatch_id: str,
    agent_name: str,
    agent_id: str,
    space_id: str,
    message_id: str,
    dispatch_type: str,
    status: str,
    **extra_fields
) -> None:
    """
    Store dispatch state in Redis for dashboard observability.

    This allows the dashboard to track dispatch lifecycle even when
    agents respond via async callbacks (not via inline HTTP response).

    Key: dispatch:{dispatch_id}
    TTL: 24 hours (86400 seconds)
    """
    import redis.asyncio as aioredis

    settings = get_settings()

    try:
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)

        data = {
            "agent_name": agent_name,
            "agent_id": str(agent_id) if agent_id else "",
            "space_id": str(space_id) if space_id else "",
            "message_id": str(message_id) if message_id else "",
            "dispatch_type": dispatch_type,
            "status": status,
            "updated_at": str(int(time.time())),
            **{k: str(v) if v is not None else "" for k, v in extra_fields.items()}
        }

        await redis_conn.hset(f"dispatch:{dispatch_id}", mapping=data)
        await redis_conn.expire(f"dispatch:{dispatch_id}", 86400)  # 24 hour TTL
        await redis_conn.close()

    except Exception as e:
        logger.warning(f"DISPATCH_STATE_STORE_ERROR dispatch_id={dispatch_id} error={e}")


@dataclass
class DispatchResult:
    """Result of a dispatch attempt."""
    status: DispatchStatus
    dispatch_id: str
    agent_name: str
    duration_ms: int = 0
    http_status: Optional[int] = None
    error: Optional[str] = None
    reply_message_id: Optional[str] = None

    @property
    def should_retry(self) -> bool:
        """Whether the caller should retry this dispatch."""
        return self.status == DispatchStatus.FAILED_RETRYABLE

    @property
    def is_success(self) -> bool:
        """Whether the dispatch completed successfully."""
        return self.status in (DispatchStatus.SUCCESS, DispatchStatus.SUCCESS_NO_RESPONSE)


@dataclass(frozen=True)
class SaveAgentResponseResult:
    """Outcome of inline response handling for cloud/webhook dispatches."""

    reply_message_id: Optional[str] = None
    completed_inline: bool = False
    suppressed: bool = False


async def execute_dispatch(
    payload: dict,
    dispatch_id: str,
    retry_count: int = 0,
    task_id: Optional[str] = None,
) -> DispatchResult:
    """
    Execute a dispatch to cloud agent runner OR webhook endpoint.

    This is the SINGLE implementation used by both local and GCP dispatch paths.
    Routes to the appropriate execution path based on dispatch_type.

    Args:
        payload: Full dispatch payload
            - For cloud: must include cloud_function_url, agent_name, etc.
            - For webhook: must include webhook_url, webhook_secret, auth_token
        dispatch_id: Unique dispatch ID for tracing
        retry_count: Current retry attempt (for logging)
        task_id: Optional task ID (for logging)

    Returns:
        DispatchResult with status, timing, and any errors
    """
    # Fleet emergency stop blocks outbound agent communication before any
    # network call/retry path. This is read-only enforcement; toggling the
    # switch still happens through the audited fleet-control API.
    try:
        from app.core.redis_client import redis_client
        from app.services.fleet_control_service import FleetControlService

        fleet_readback = await FleetControlService(redis_client).enforcement_readback(
            surface="agent_communication"
        )
        if fleet_readback["blocked"]:
            logger.warning(
                "DISPATCH_FLEET_STOPPED dispatch_id=%s task_id=%s reason=%s transition_id=%s",
                dispatch_id,
                task_id,
                fleet_readback.get("reason"),
                fleet_readback.get("transition_id"),
            )
            return DispatchResult(
                status=DispatchStatus.FAILED_FLEET_STOPPED,
                dispatch_id=dispatch_id,
                agent_name=payload.get("agent_name", "unknown"),
                error=fleet_readback.get("reason") or "Fleet emergency stop is active",
            )
    except Exception:
        logger.exception("DISPATCH_FLEET_CONTROL_CHECK_FAILED dispatch_id=%s", dispatch_id)
        return DispatchResult(
            status=DispatchStatus.FAILED_FLEET_STOPPED,
            dispatch_id=dispatch_id,
            agent_name=payload.get("agent_name", "unknown"),
            error="Fleet-control readback failed; failing closed",
        )

    # Route based on dispatch_type
    dispatch_type = payload.get("dispatch_type", "cloud")

    if dispatch_type == "webhook":
        return await _execute_webhook_dispatch(
            payload=payload,
            dispatch_id=dispatch_id,
            retry_count=retry_count,
            task_id=task_id,
        )

    if os.getenv("ENABLE_CLOUD_AI", "false").lower() != "true":
        return DispatchResult(
            status=DispatchStatus.FAILED_PERMANENT, dispatch_id=dispatch_id,
            agent_name=payload.get("agent_name", "unknown"),
            error="Managed AI execution is disabled; use an external OAuth-connected agent or configure an optional runtime",
        )

    if dispatch_type == "agentcore":
        return await _execute_agentcore_dispatch(
            payload=payload,
            dispatch_id=dispatch_id,
            retry_count=retry_count,
            task_id=task_id,
        )

    if dispatch_type == "space_agent":
        return await _execute_space_agent_dispatch(
            payload=payload,
            dispatch_id=dispatch_id,
            retry_count=retry_count,
            task_id=task_id,
        )

    # Default: cloud agent dispatch
    return await _execute_cloud_dispatch(
        payload=payload,
        dispatch_id=dispatch_id,
        retry_count=retry_count,
        task_id=task_id,
    )


async def _execute_cloud_dispatch(
    payload: dict,
    dispatch_id: str,
    retry_count: int = 0,
    task_id: Optional[str] = None,
) -> DispatchResult:
    """
    Execute a dispatch to the cloud agent runner (Gemini-based agents).

    Args:
        payload: Full dispatch payload (must include cloud_function_url, agent_name, etc.)
        dispatch_id: Unique dispatch ID for tracing
        retry_count: Current retry attempt (for logging)
        task_id: Optional task ID (for logging)

    Returns:
        DispatchResult with status, timing, and any errors
    """
    settings = get_settings()

    # Extract required fields from payload
    cloud_function_url = payload.get("cloud_function_url")
    agent_name = payload.get("agent_name", "unknown")
    agent_id = payload.get("agent_id")
    message_id = payload.get("message_id", "unknown")
    space_id = payload.get("space_id") or payload.get("org_id")

    # ============================================================
    # URL REWRITING: Cloud Functions → Cloud Run Migration
    # ============================================================
    # Agents have cloud_function_url stored in DB. During migration from
    # Cloud Functions to Cloud Run, we intercept old URLs and rewrite them.
    # This allows us to delete Cloud Functions without breaking existing agents.
    #
    # Pattern matching:
    # - *cloudfunctions.net*agent-runner-fn* → STABLE (production)
    # - *cloudfunctions.net*agent-runner-v2* → EXPERIMENTAL (new features)
    # - *agent_runner:8080* (local Docker) → No rewrite needed
    # - *a.run.app* (already Cloud Run) → No rewrite needed
    #
    # TODO: Remove this rewrite logic once all agents are migrated to
    # store Cloud Run URLs directly (or use engine_variant column).
    # ============================================================
    original_url = cloud_function_url
    url_rewritten = False

    if cloud_function_url and "cloudfunctions.net" in cloud_function_url:
        if "agent-runner-v2" in cloud_function_url:
            # V2/Experimental Cloud Function → Experimental Cloud Run
            cloud_function_url = settings.agent_runner_experimental_url
            url_rewritten = True
            logger.info(
                f"DISPATCH_URL_REWRITE dispatch_id={dispatch_id} "
                f"agent_name={agent_name} variant=experimental "
                f"from={original_url} to={cloud_function_url}"
            )
        elif "agent-runner" in cloud_function_url:
            # V1/Stable Cloud Function → Stable Cloud Run
            cloud_function_url = settings.agent_runner_stable_url
            url_rewritten = True
            logger.info(
                f"DISPATCH_URL_REWRITE dispatch_id={dispatch_id} "
                f"agent_name={agent_name} variant=stable "
                f"from={original_url} to={cloud_function_url}"
            )

    # Validate required URL
    if not cloud_function_url:
        logger.error(
            f"DISPATCH_MISSING_URL dispatch_id={dispatch_id} "
            f"agent_name={agent_name}"
        )
        return DispatchResult(
            status=DispatchStatus.FAILED_MISSING_URL,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            error="Missing cloud_function_url",
        )

    # Circuit breaker — skip dispatch if URL has too many consecutive failures
    if await _check_circuit_breaker(cloud_function_url):
        logger.warning(
            f"DISPATCH_CIRCUIT_OPEN dispatch_id={dispatch_id} "
            f"agent_name={agent_name} url={cloud_function_url}"
        )
        return DispatchResult(
            status=DispatchStatus.FAILED_CIRCUIT_OPEN,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            error=f"Circuit breaker open for {cloud_function_url}",
        )

    payload_version = payload.get("payload_version", "2")

    # Log dispatch attempt with full context for debugging
    timeout_seconds = settings.cloud_agent_http_timeout_seconds
    rewrite_info = f" url_rewritten=true original_url={original_url}" if url_rewritten else ""
    logger.info(
        f"DISPATCH_ATTEMPT dispatch_id={dispatch_id} "
        f"task_id={task_id} agent_name={agent_name} agent_id={agent_id} "
        f"message_id={message_id} space_id={space_id} "
        f"url={cloud_function_url} retry={retry_count} "
        f"timeout={timeout_seconds}s dispatch_type=cloud{rewrite_info}"
    )

    # Log payload details for observability
    tool_config = payload.get("tool_config", {})
    logger.info(
        f"DISPATCH_PAYLOAD dispatch_id={dispatch_id} "
        f"agent_name={agent_name} model={payload.get('model', 'unknown')} "
        f"sender={payload.get('sender_handle', 'unknown')} sender_type={payload.get('sender_type', 'unknown')} "
        f"web_browsing={tool_config.get('web_browsing_enabled', False)} "
        f"ax_mcp={tool_config.get('ax_mcp_enabled', False)} "
        f"image_gen={tool_config.get('image_gen_enabled', False)} "
        f"history_count={len(payload.get('history', []))} payload_version={payload_version} "
        f"dispatch_type=cloud "
        f"message_preview=\"{_truncate_for_log(payload.get('user_message', ''))}\""
    )

    dispatch_start = time.time()

    # DIAGNOSTIC: Log when we're about to call agent_runner
    logger.info(
        f"DISPATCH_AGENT_CALL_START dispatch_id={dispatch_id} "
        f"agent_name={agent_name} url={cloud_function_url} "
        f"started_at={dispatch_start}"
    )

    try:
        # Prepare headers for agent runner
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": settings.agent_runner_api_key,
            "X-Payload-Version": payload.get("payload_version", "2"),
            "X-Dispatch-ID": dispatch_id,
        }

        # Make the HTTP call to agent runner
        async with httpx.AsyncClient() as client:
            response = await client.post(
                cloud_function_url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=float(settings.cloud_agent_http_timeout_seconds),
                    write=10.0,
                    pool=10.0,
                ),
            )

        dispatch_duration_ms = int((time.time() - dispatch_start) * 1000)

        # DIAGNOSTIC: Log when agent_runner responds
        logger.info(
            f"DISPATCH_AGENT_CALL_DONE dispatch_id={dispatch_id} "
            f"agent_name={agent_name} duration_ms={dispatch_duration_ms} "
            f"http_status={response.status_code} responded_at={time.time()}"
        )

        if response.is_success:
            logger.info(
                f"DISPATCH_SUCCESS dispatch_id={dispatch_id} "
                f"task_id={task_id} agent_name={agent_name} space_id={space_id} "
                f"duration_ms={dispatch_duration_ms} "
                f"http_status={response.status_code} dispatch_type=cloud "
                f"sender={payload.get('sender_handle', 'unknown')} sender_type={payload.get('sender_type', 'unknown')}"
            )

            # Extract and save the agent's response FIRST
            # to know if we got inline response or async
            save_result = await _save_agent_response(
                response=response,
                dispatch_id=dispatch_id,
                agent_id=agent_id,
                agent_name=agent_name,
                message_id=message_id,
                space_id=space_id,
                sender_handle=payload.get("sender_handle"),
                sender_type=payload.get("sender_type"),
                channel=payload.get("channel", "main"),
            )
            reply_message_id = save_result.reply_message_id

            # Audit-based widget attachment — same as space agent path.
            # Cloud agents that use MCP tools will have ToolCall audit records.
            if reply_message_id and space_id:
                try:
                    audit_widget = await _get_widget_from_audit(dispatch_id)
                    if audit_widget:
                        logger.info(
                            "CLOUD_WIDGET_FROM_AUDIT dispatch_id=%s tool=%s",
                            dispatch_id, audit_widget["tool_name"],
                        )
                        widget_extra = {
                            k: v for k, v in {
                                "title": "Request processed",
                                "tools_used": [audit_widget["tool_name"]],
                                "arguments": audit_widget.get("arguments"),
                                "initial_data": audit_widget.get("initial_data"),
                                "result_kind": audit_widget.get("result_kind"),
                                "tool_action": audit_widget.get("tool_action"),
                                "tool_call_id": audit_widget.get("tool_call_id"),
                                "agent_id": audit_widget.get("agent_id"),
                            }.items() if v is not None
                        }
                        await _set_widget(
                            reply_message_id,
                            space_id,
                            "complete",
                            tool_name=audit_widget["tool_name"],
                            extra_fields=widget_extra,
                            resource_uri=audit_widget.get("resource_uri"),
                        )
                except Exception as widget_err:
                    logger.warning(
                        "CLOUD_WIDGET_ATTACH_ERROR dispatch_id=%s err=%s",
                        dispatch_id, widget_err,
                    )

            # Notify frontend that agent processing is done (spinner teardown)
            # Only fire for completed dispatches with a saved reply.
            if save_result.completed_inline and space_id:
                try:
                    event_payload = {
                        "status": "completed",
                        "dispatch_id": dispatch_id,
                        "message_id": message_id,
                        "agent_name": agent_name,
                    }
                    if reply_message_id:
                        event_payload["reply_message_id"] = reply_message_id
                    from app.services.redis_sse_broker import redis_sse_broker
                    await redis_sse_broker.publish(
                        space_id=space_id,
                        event="agent_processing",
                        data=event_payload,
                    )
                except Exception:
                    pass  # Best effort — don't fail dispatch on SSE publish error

            # Update dispatch state based on whether we got inline response
            # - "completed" = got inline response, task done
            # - "awaiting_callback" = empty response, expecting heartbeats/complete
            dispatch_status = (
                "completed" if save_result.completed_inline else "awaiting_callback"
            )
            await _store_dispatch_state(
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                agent_id=agent_id,
                space_id=space_id,
                message_id=message_id,
                dispatch_type="cloud",
                status=dispatch_status,
                duration_ms=dispatch_duration_ms,
                http_status=response.status_code,
            )

            # Log async pattern for dashboard observability
            if not save_result.completed_inline:
                logger.info(
                    f"DISPATCH_AWAITING_CALLBACK dispatch_id={dispatch_id} "
                    f"agent_name={agent_name} dispatch_type=cloud "
                    f"(agent will send heartbeats and/or complete callback)"
                )

            # Clear circuit breaker on success
            await _reset_circuit(cloud_function_url)

            return DispatchResult(
                status=(
                    DispatchStatus.SUCCESS
                    if save_result.completed_inline
                    else DispatchStatus.SUCCESS_NO_RESPONSE
                ),
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                duration_ms=dispatch_duration_ms,
                http_status=response.status_code,
                reply_message_id=reply_message_id,
            )
        else:
            # Agent runner returned an error
            raw_error = response.text[:200] if response.text else "No response body"
            # Sanitize sensitive info
            error_detail = re.sub(
                r'(api[_-]?key|token|secret|password)[=:][^\s&"\']+',
                r'\1=[REDACTED]',
                raw_error,
                flags=re.IGNORECASE
            )

            logger.warning(
                f"DISPATCH_FAILED dispatch_id={dispatch_id} "
                f"task_id={task_id} agent_name={agent_name} "
                f"duration_ms={dispatch_duration_ms} "
                f"http_status={response.status_code} error={error_detail}"
            )

            # 4xx = permanent error, don't retry
            # 5xx = retryable error
            is_retryable = response.status_code >= 500

            return DispatchResult(
                status=DispatchStatus.FAILED_RETRYABLE if is_retryable else DispatchStatus.FAILED_PERMANENT,
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                duration_ms=dispatch_duration_ms,
                http_status=response.status_code,
                error=error_detail,
            )

    except httpx.TimeoutException as e:
        dispatch_duration_ms = int((time.time() - dispatch_start) * 1000)
        logger.error(
            f"DISPATCH_TIMEOUT dispatch_id={dispatch_id} "
            f"task_id={task_id} agent_name={agent_name} "
            f"duration_ms={dispatch_duration_ms} error={e}"
        )
        return DispatchResult(
            status=DispatchStatus.FAILED_RETRYABLE,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            duration_ms=dispatch_duration_ms,
            error=f"Timeout: {e}",
        )

    except httpx.ConnectError as e:
        dispatch_duration_ms = int((time.time() - dispatch_start) * 1000)
        logger.error(
            f"DISPATCH_CONNECT_ERROR dispatch_id={dispatch_id} "
            f"task_id={task_id} agent_name={agent_name} "
            f"duration_ms={dispatch_duration_ms} error={e}"
        )
        await _record_circuit_failure(cloud_function_url)
        return DispatchResult(
            status=DispatchStatus.FAILED_RETRYABLE,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            duration_ms=dispatch_duration_ms,
            error=f"Connection error: {e}",
        )

    except Exception as e:
        dispatch_duration_ms = int((time.time() - dispatch_start) * 1000)
        logger.error(
            f"DISPATCH_ERROR dispatch_id={dispatch_id} "
            f"task_id={task_id} agent_name={agent_name} "
            f"duration_ms={dispatch_duration_ms} error={e}"
        )
        return DispatchResult(
            status=DispatchStatus.FAILED_RETRYABLE,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            duration_ms=dispatch_duration_ms,
            error=str(e),
        )


async def _save_agent_response(
    response: httpx.Response,
    dispatch_id: str,
    agent_id: Optional[str],
    agent_name: str,
    message_id: str,
    space_id: Optional[str],
    sender_handle: Optional[str] = None,
    sender_type: Optional[str] = None,
    channel: str = "main",
) -> SaveAgentResponseResult:
    """
    Extract and save agent response via /internal/agent-reply.

    Returns whether inline handling completed the dispatch, plus any saved reply id.
    """
    settings = get_settings()

    # DIAGNOSTIC: Log when save operation starts
    save_start = time.time()
    logger.info(
        f"DISPATCH_SAVE_START dispatch_id={dispatch_id} "
        f"agent_name={agent_name} started_at={save_start}"
    )

    try:
        response_data = response.json()
        reply_content = response_data.get("response", "")
        intelligence, visible_content = _parse_ax_intelligence(reply_content)
        suppress_reply = intelligence and intelligence.get("visible") is False

        if intelligence and message_id and space_id:
            await _store_ax_intelligence(message_id, space_id, intelligence)

        if suppress_reply:
            logger.info(
                "DISPATCH_REPLY_SUPPRESSED dispatch_id=%s agent_name=%s dispatch_type=cloud",
                dispatch_id,
                agent_name,
            )
            if _is_signal_only_no_reply(intelligence):
                await _publish_agent_skipped_signal(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    message_id=message_id,
                    space_id=space_id,
                    intelligence=intelligence or {},
                )
                return SaveAgentResponseResult(completed_inline=True)
            if _is_internal_only_suppression(intelligence):
                logger.info(
                    "DISPATCH_REPLY_INTERNAL_ONLY dispatch_id=%s agent_name=%s dispatch_type=cloud",
                    dispatch_id,
                    agent_name,
                    )
                return SaveAgentResponseResult()
            pause_notice_id = await _save_agent_pause_notice(
                dispatch_id=dispatch_id,
                agent_id=agent_id,
                agent_name=agent_name,
                message_id=message_id,
                space_id=space_id,
                channel=channel,
                intelligence=intelligence or {},
            )
            return SaveAgentResponseResult(
                reply_message_id=pause_notice_id,
                completed_inline=True,
            )

        if intelligence:
            reply_content = visible_content

        # Classify simple ack replies as signals instead of messages
        from app.services.message_visibility import is_ack_message
        if is_ack_message(reply_content, is_agent=True):
            logger.info(
                "DISPATCH_ACK_SIGNAL dispatch_id=%s agent_name=%s content=%r",
                dispatch_id, agent_name, reply_content[:80] if reply_content else "",
            )
            await _publish_agent_skipped_signal(
                agent_id=agent_id,
                agent_name=agent_name,
                message_id=message_id,
                space_id=space_id,
                intelligence={"reason": "ack", "signal_kind": "ack"},
            )
            return SaveAgentResponseResult(completed_inline=True)

        # Preserve visible reply content exactly as generated by the agent.
        # Auto-prepending @sender caused reply loops, especially for space-agent
        # and agent-to-agent threads, and breaks reply integrity.

        if not reply_content:
            logger.info(
                f"DISPATCH_NO_RESPONSE dispatch_id={dispatch_id} "
                f"agent_name={agent_name} (agent returned empty response)"
            )
            return SaveAgentResponseResult()

        # Validate required fields
        if not all([agent_id, message_id, space_id]):
            logger.warning(
                f"DISPATCH_SAVE_SKIP dispatch_id={dispatch_id} "
                f"agent_name={agent_name} reason=missing_required_fields "
                f"agent_id={agent_id} message_id={message_id} space_id={space_id}"
            )
            return SaveAgentResponseResult()

        # Save response via internal agent-reply endpoint
        reply_url = f"{settings.backend_api_url}/internal/agent-reply"

        async with httpx.AsyncClient() as client:
            reply_response = await client.post(
                reply_url,
                json={
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "message_id": message_id,
                    "space_id": space_id,
                    "content": reply_content,
                    "dispatch_id": dispatch_id,
                },
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": settings.internal_dispatch_api_key,
                },
                timeout=30.0,
            )

        # DIAGNOSTIC: Calculate save duration
        save_duration_ms = int((time.time() - save_start) * 1000)

        if reply_response.is_success:
            reply_message_id = reply_response.json().get("reply_message_id")
            logger.info(
                f"DISPATCH_RESPONSE_SAVED dispatch_id={dispatch_id} "
                f"agent_name={agent_name} reply_id={reply_message_id} "
                f"content_len={len(reply_content)} "
                f"save_duration_ms={save_duration_ms} saved_at={time.time()} "
                f"response_preview=\"{_truncate_for_log(reply_content, 300)}\""
            )
            return SaveAgentResponseResult(
                reply_message_id=reply_message_id,
                completed_inline=True,
            )

        logger.error(
            f"DISPATCH_RESPONSE_SAVE_FAILED dispatch_id={dispatch_id} "
            f"agent_name={agent_name} status={reply_response.status_code} "
            f"save_duration_ms={save_duration_ms} "
            f"error={_truncate_for_log(reply_response.text)}"
        )
        return SaveAgentResponseResult()

    except Exception as e:
        # Log but don't fail the dispatch - agent completed successfully
        save_duration_ms = int((time.time() - save_start) * 1000)
        logger.error(
            f"DISPATCH_SAVE_ERROR dispatch_id={dispatch_id} "
            f"agent_name={agent_name} save_duration_ms={save_duration_ms} error={e}"
        )
        return SaveAgentResponseResult()


# =============================================================================
# AgentCore Response Saving
# =============================================================================


async def _save_agentcore_response(
    response_text: str,
    dispatch_id: str,
    agent_id: Optional[str],
    agent_name: str,
    message_id: str,
    space_id: Optional[str],
    sender_handle: Optional[str] = None,
    sender_type: Optional[str] = None,
    channel: str = "main",
) -> Optional[str]:
    """
    Save AgentCore response via /internal/agent-reply.
    Same as _save_agent_response but accepts raw text instead of httpx.Response.
    """
    settings = get_settings()
    save_start = time.time()

    try:
        reply_content = response_text or ""
        intelligence, visible_content = _parse_ax_intelligence(reply_content)
        suppress_reply = intelligence and intelligence.get("visible") is False

        if intelligence and message_id and space_id:
            await _store_ax_intelligence(message_id, space_id, intelligence)

        if suppress_reply:
            logger.info(
                "DISPATCH_REPLY_SUPPRESSED dispatch_id=%s agent_name=%s dispatch_type=agentcore_like",
                dispatch_id,
                agent_name,
            )
            if _is_signal_only_no_reply(intelligence):
                await _publish_agent_skipped_signal(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    message_id=message_id,
                    space_id=space_id,
                    intelligence=intelligence or {},
                )
                return None
            return await _save_agent_pause_notice(
                dispatch_id=dispatch_id,
                agent_id=agent_id,
                agent_name=agent_name,
                message_id=message_id,
                space_id=space_id,
                channel=channel,
                intelligence=intelligence or {},
            )

        if intelligence:
            reply_content = visible_content

        # Preserve visible reply content exactly as generated by the agent.
        # Auto-prepending @sender caused reply loops, especially for space-agent
        # and agent-to-agent threads, and breaks reply integrity.

        if not reply_content:
            return None

        if not all([agent_id, message_id, space_id]):
            logger.warning(
                f"DISPATCH_SAVE_SKIP dispatch_id={dispatch_id} "
                f"agent_name={agent_name} reason=missing_required_fields"
            )
            return None

        reply_url = f"{settings.backend_api_url}/internal/agent-reply"

        async with httpx.AsyncClient() as client:
            reply_response = await client.post(
                reply_url,
                json={
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "message_id": message_id,
                    "space_id": space_id,
                    "content": reply_content,
                    "dispatch_id": dispatch_id,
                },
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": settings.internal_dispatch_api_key,
                },
                timeout=30.0,
            )

        save_duration_ms = int((time.time() - save_start) * 1000)

        if reply_response.is_success:
            reply_message_id = reply_response.json().get("reply_message_id")
            logger.info(
                f"DISPATCH_RESPONSE_SAVED dispatch_id={dispatch_id} "
                f"agent_name={agent_name} reply_id={reply_message_id} "
                f"content_len={len(reply_content)} save_duration_ms={save_duration_ms}"
            )
            return reply_message_id

        logger.error(
            f"DISPATCH_RESPONSE_SAVE_FAILED dispatch_id={dispatch_id} "
            f"agent_name={agent_name} status={reply_response.status_code} "
            f"save_duration_ms={save_duration_ms}"
        )
        return None

    except Exception as e:
        save_duration_ms = int((time.time() - save_start) * 1000)
        logger.error(
            f"DISPATCH_SAVE_ERROR dispatch_id={dispatch_id} "
            f"agent_name={agent_name} save_duration_ms={save_duration_ms} error={e}"
        )
        return None


# =============================================================================
# Webhook Dispatch (External Gateway Agents)
# =============================================================================


async def _track_webhook_failure(agent_id: str, dispatch_id: str) -> None:
    """
    SIMPLIFIED: No longer quarantines agents on failure.
    HMAC is the security - user manages their own endpoint availability.
    """
    # No-op: Just log, don't quarantine
    pass


async def _execute_agentcore_dispatch(
    payload: dict,
    dispatch_id: str,
    retry_count: int = 0,
    task_id: Optional[str] = None,
) -> DispatchResult:
    """
    Execute a dispatch via Bedrock AgentCore (Mode A).

    Uses the shared Bedrock Agent with backend-owned runtime partitioning:
    stable actor scope per space plus deterministic per-conversation sessions.
    """
    agent_name = payload.get("agent_name", "unknown")
    agent_id = payload.get("agent_id")
    message_id = payload.get("message_id", "unknown")
    space_id = payload.get("space_id") or payload.get("org_id")
    sender_id = payload.get("sender_id", "")
    user_message = payload.get("user_message", "")
    context_data = payload.get("context_data", {}) or {}
    conversation_id = (
        payload.get("conversation_id")
        or payload.get("thread_id")
        or context_data.get("conversation_id")
        or context_data.get("trigger_message_id")
        or message_id
    )

    start_time = time.time()

    logger.info(
        f"DISPATCH_AGENTCORE_START dispatch_id={dispatch_id} "
        f"agent_name={agent_name} space_id={space_id} retry={retry_count}"
    )

    await _store_dispatch_state(
        dispatch_id=dispatch_id,
        agent_name=agent_name,
        agent_id=agent_id or "",
        space_id=space_id or "",
        message_id=message_id,
        dispatch_type="agentcore",
        status="dispatching",
    )

    if not space_id or not sender_id:
        return DispatchResult(
            status=DispatchStatus.FAILED_PERMANENT,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            error="Missing space_id or sender_id for AgentCore dispatch",
        )

    try:
        from app.services.agentcore_service import invoke_space_agent

        # Extract MCP auth token for space-scoped tool execution
        mcp_auth = payload.get("mcp_auth", {})
        mcp_auth_token = mcp_auth.get("access_token") if mcp_auth else None

        result = await invoke_space_agent(
            space_id=space_id,
            user_id=sender_id,
            conversation_id=conversation_id,
            input_text=user_message,
            dispatch_id=dispatch_id,
            mcp_auth_token=mcp_auth_token,
            agent_id=payload.get("bedrock_agent_id"),
            agent_alias_id=payload.get("bedrock_agent_alias_id"),
            system_context={
                "space_name": payload.get("space_name", ""),
                "agent_name": agent_name,
                "platform_agent_id": agent_id,
                "sender_handle": payload.get("sender_handle", ""),
                "agents": context_data.get("agents", []),
                "message_id": message_id,
                "authority_class": payload.get("authority_class", "space_concierge"),
                "writes_allowed": payload.get("writes_allowed"),
                "confirmation_required": payload.get("confirmation_required"),
                "tool_allowlist": payload.get("tool_allowlist"),
            },
        )

        duration_ms = int((time.time() - start_time) * 1000)

        if result.success:
            audit_widgets = await _get_all_widgets_from_audit(dispatch_id)
            audit_widget = audit_widgets[0] if audit_widgets else None
            primary_tool = next(
                (tool for tool in result.tools_used if tool in _TOOL_RESOURCE_URI),
                None,
            )

            # Save response message to DB (same pattern as cloud dispatch)
            reply_message_id = None
            reply_text = result.response_text.strip() if result.response_text else ""
            if not reply_text and (audit_widget or primary_tool):
                reply_text = "Request processed"

            if reply_text:
                try:
                    reply_message_id = await _save_agentcore_response(
                        response_text=reply_text,
                        dispatch_id=dispatch_id,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        message_id=message_id,
                        space_id=space_id,
                        sender_handle=payload.get("sender_handle"),
                        sender_type=payload.get("sender_type"),
                        channel=payload.get("channel", "main"),
                    )
                except Exception as save_err:
                    logger.error(
                        f"DISPATCH_AGENTCORE_SAVE_ERROR dispatch_id={dispatch_id} "
                        f"error={save_err}"
                    )

            if reply_message_id and (audit_widget or primary_tool):
                widget_source = audit_widget or {
                    "tool_name": primary_tool,
                    "resource_uri": _TOOL_RESOURCE_URI.get(primary_tool, ""),
                }
                try:
                    widget_extra: dict = {
                        "title": "Request processed",
                        "tools_used": [tool for tool in result.tools_used if tool],
                    }
                    if audit_widget:
                        for key in (
                            "arguments",
                            "initial_data",
                            "result_kind",
                            "tool_action",
                            "tool_call_id",
                            "agent_id",
                        ):
                            val = audit_widget.get(key)
                            if val is not None:
                                widget_extra[key] = val
                        if audit_widgets:
                            widget_extra["widgets"] = audit_widgets

                    await _set_widget(
                        reply_message_id,
                        space_id,
                        "complete",
                        tool_name=widget_source["tool_name"],
                        extra_fields=widget_extra,
                        resource_uri=widget_source.get("resource_uri"),
                    )
                except Exception as widget_err:
                    logger.warning(
                        "DISPATCH_AGENTCORE_WIDGET_ATTACH_ERROR dispatch_id=%s reply_id=%s tool=%s err=%s",
                        dispatch_id,
                        reply_message_id,
                        widget_source.get("tool_name"),
                        widget_err,
                    )

            try:
                from app.services.redis_sse_broker import redis_sse_broker

                await redis_sse_broker.publish(
                    space_id=space_id,
                    event="agent_processing",
                    data={
                        "agent_id": agent_id or "",
                        "agent_name": agent_name,
                        "message_id": result.stream_message_id or message_id,
                        "parent_id": message_id,
                        "dispatch_id": dispatch_id,
                        "status": "completed",
                        "dispatch_type": "agentcore",
                    },
                )
            except Exception:
                pass

            await _store_dispatch_state(
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                agent_id=agent_id or "",
                space_id=space_id or "",
                message_id=message_id,
                dispatch_type="agentcore",
                status="completed",
                duration_ms=str(duration_ms),
            )

            logger.info(
                f"DISPATCH_AGENTCORE_DONE dispatch_id={dispatch_id} "
                f"agent_name={agent_name} duration_ms={duration_ms} "
                f"response_len={len(result.response_text)}"
            )

            return DispatchResult(
                status=DispatchStatus.SUCCESS if result.response_text else DispatchStatus.SUCCESS_NO_RESPONSE,
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                duration_ms=duration_ms,
                reply_message_id=reply_message_id,
            )
        else:
            await _store_dispatch_state(
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                agent_id=agent_id or "",
                space_id=space_id or "",
                message_id=message_id,
                dispatch_type="agentcore",
                status="failed",
                error=result.error or "Unknown error",
            )

            # Check if the error is retryable (throttling, service unavailable)
            is_retryable = any(
                pattern in (result.error or "")
                for pattern in ("ThrottlingException", "ServiceUnavailable", "TooManyRequests")
            )
            return DispatchResult(
                status=DispatchStatus.FAILED_RETRYABLE if is_retryable else DispatchStatus.FAILED_PERMANENT,
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                duration_ms=duration_ms,
                error=result.error,
            )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(
            f"DISPATCH_AGENTCORE_ERROR dispatch_id={dispatch_id} "
            f"agent_name={agent_name} duration_ms={duration_ms} error={error_msg}"
        )
        return DispatchResult(
            status=DispatchStatus.FAILED_RETRYABLE,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            duration_ms=duration_ms,
            error=error_msg,
        )


# =============================================================================
# Space Agent Dispatch (Local Container — Streaming)
# =============================================================================


def _clamp_score(value, default: float = 0.0) -> float:
    """Validate and clamp score to 0.0-1.0 range."""
    try:
        score = float(value)
        return max(0.0, min(1.0, score))
    except (TypeError, ValueError):
        return default


_INLINE_WIDGET_TAG_RE = re.compile(r"<\s*ui://[A-Za-z0-9._/\-]+\s*/?\s*>", re.IGNORECASE)


def _strip_inline_widget_markers(text: str) -> str:
    """Remove inline MCP widget tags from visible reply content."""
    if not text:
        return text

    cleaned = _INLINE_WIDGET_TAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _parse_ax_intelligence(full_response: str) -> tuple[dict | None, str]:
    """Parse structured intelligence from aX response.

    Three-position parsing:
    1. Front: {"ax_intel": {...}}\n---\nvisible reply
    2. Back:  visible reply\n{"ax_intel": {...}}
    3. No marker → (None, full_response) — plain reply

    Note: SUMMARY: prefix parsing removed — standalone summarizer handles
    summaries independently (see summarize_and_store in messages_notifications.py).
    """
    stripped = full_response.strip()
    if not stripped:
        return None, full_response

    # Position 1: structured {"ax_intel": ...} at FRONT with --- separator
    if stripped.startswith("{"):
        try:
            parts = re.split(r'\n---\n', stripped, maxsplit=1)
            parsed = json.loads(parts[0])
            if isinstance(parsed, dict) and "ax_intel" in parsed:
                intel = parsed["ax_intel"]
                visible = _strip_inline_widget_markers(parts[1]) if len(parts) > 1 else ""
                return intel, visible
        except (json.JSONDecodeError, IndexError):
            pass

    # Position 2: {"ax_intel": ...} at END of response (aX sometimes appends it)
    # Look for last occurrence of {"ax_intel" in the response
    ax_intel_idx = stripped.rfind('{"ax_intel"')
    if ax_intel_idx > 0:
        try:
            json_part = stripped[ax_intel_idx:]
            parsed = json.loads(json_part)
            if isinstance(parsed, dict) and "ax_intel" in parsed:
                intel = parsed["ax_intel"]
                visible = _strip_inline_widget_markers(stripped[:ax_intel_idx])
                return intel, visible
        except (json.JSONDecodeError, IndexError):
            pass

    # Position 3: plain response — no intelligence extracted
    return None, _strip_inline_widget_markers(full_response)


def _normalize_pause_notice(
    intelligence: dict | None,
    agent_name: str,
) -> tuple[str, dict]:
    """Build a visible non-error pause/no-reply notice from structured intelligence."""
    intel = intelligence or {}

    raw_reason_code = intel.get("reason_code") or intel.get("reason") or "no_reply"
    reason_code = str(raw_reason_code or "no_reply").strip().lower().replace(" ", "_")

    pause_duration = intel.get("duration_s") or intel.get("pause_duration")
    until = intel.get("until") or intel.get("pause_expires_at")
    emoji = str(intel.get("emoji") or "").strip() or None

    try:
        duration_seconds = int(pause_duration) if pause_duration is not None else None
        if duration_seconds is not None and duration_seconds <= 0:
            duration_seconds = None
    except (TypeError, ValueError):
        duration_seconds = None

    if not until and duration_seconds:
        from datetime import datetime, timedelta, timezone

        until = (datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)).isoformat()

    is_break = bool(duration_seconds or until) or reason_code in {
        "taking_break",
        "break",
        "pause",
        "cooldown",
        "loop_detected",
    }

    default_text = (
        f"{agent_name} is taking a break."
        if is_break
        else f"{agent_name} chose not to reply."
    )
    pause_reason_text = str(intel.get("reason_text") or "").strip() or default_text

    if len(pause_reason_text) > 240:
        pause_reason_text = f"{pause_reason_text[:237].rstrip()}..."

    content = f"{emoji or ('⏸️' if is_break else '🤐')} {default_text}"
    metadata = {
        "reason": reason_code,
        "reason_text": pause_reason_text,
        "pause_reason": reason_code,
        "pause_reason_text": pause_reason_text,
        "emoji": emoji or ("⏸️" if is_break else "🤐"),
        "pause_emoji": emoji or ("⏸️" if is_break else "🤐"),
        "agent_name": agent_name,
    }

    if duration_seconds:
        metadata["pause_duration"] = duration_seconds
    if isinstance(until, str) and until.strip():
        metadata["pause_expires_at"] = until.strip()

    return content, metadata


def _normalized_suppression_reason(
    intelligence: dict | None,
) -> tuple[str, bool]:
    """Return normalized suppression reason and whether it carries pause semantics."""
    intel = intelligence or {}
    reason_code = str(
        intel.get("reason_code") or intel.get("reason") or "no_reply",
    ).strip().lower().replace(" ", "_")
    has_pause_window = bool(
        intel.get("duration_s")
        or intel.get("pause_duration")
        or intel.get("until")
        or intel.get("pause_expires_at"),
    )
    return reason_code, has_pause_window


def _is_signal_only_no_reply(intelligence: dict | None) -> bool:
    """Return True when a suppressed reply should stay UI-only, not persisted.

    Quiet exit reasons are transcript signals, not real messages. Timed pauses
    and stronger break states still use persisted pause notices because they
    carry durable moderation semantics.
    """
    reason_code, has_pause_window = _normalized_suppression_reason(intelligence)
    return reason_code in {"no_reply", "no_reply_requested", "user_requested_silence", "not_best_fit"} and not has_pause_window


def _is_internal_only_suppression(intelligence: dict | None) -> bool:
    """Return True when suppression should remain internal-only with no UI signal."""
    reason_code, has_pause_window = _normalized_suppression_reason(intelligence)
    return reason_code in {"agent_working"} and not has_pause_window


async def _publish_agent_skipped_signal(
    *,
    agent_id: Optional[str],
    agent_name: str,
    message_id: str,
    space_id: Optional[str],
    intelligence: dict | None,
) -> None:
    """Publish an ephemeral no-reply signal without saving a message record."""
    if not all([agent_id, message_id, space_id]):
        logger.warning(
            "DISPATCH_AGENT_SKIPPED_SIGNAL_SKIP agent_name=%s reason=missing_required_fields",
            agent_name,
        )
        return

    _, metadata = _normalize_pause_notice(intelligence, agent_name)
    original_reason_code = str(metadata.get("reason") or "no_reply").strip().lower()
    quiet_exit_reason_codes = {
        "no_reply",
        "no_reply_requested",
        "user_requested_silence",
        "not_best_fit",
    }
    public_reason_code = "no_reply" if original_reason_code in quiet_exit_reason_codes else original_reason_code
    is_quiet_exit = original_reason_code in quiet_exit_reason_codes
    public_reason_text = (
        "no reply"
        if is_quiet_exit
        else (
            metadata.get("pause_reason_text")
            or metadata.get("reason_text")
            or f"{agent_name} chose not to reply."
        )
    )
    signal_key = str(agent_id or agent_name).strip().lower().replace(" ", "-")
    signal_id = f"signal-no-reply:{message_id}:{signal_key}"
    created_at = datetime.now(timezone.utc).isoformat()
    signal_payload = {
        "id": signal_id,
        "agent_id": str(agent_id) if agent_id else None,
        "agent_name": agent_name,
        "message_id": str(message_id),
        "reason": public_reason_text,
        "label": public_reason_text,
        "reason_code": public_reason_code,
        "signal_kind": original_reason_code,
        "detail_reason_text": public_reason_text if is_quiet_exit else metadata.get("pause_reason_text") or metadata.get("reason_text"),
        "emoji": "" if is_quiet_exit else metadata.get("pause_emoji") or metadata.get("emoji") or "🤐",
        "created_at": created_at,
        "signal_only": True,
    }

    try:
        from app.models.message import Message
        from sqlalchemy import select
        from sqlalchemy.orm.attributes import flag_modified

        try:
            target_message_id = uuid_module.UUID(str(message_id))
            target_space_id = uuid_module.UUID(str(space_id))
        except (TypeError, ValueError):
            logger.warning(
                "DISPATCH_AGENT_SKIPPED_PARENT_METADATA_SKIP message_id=%s space_id=%s reason=invalid_uuid",
                message_id,
                space_id,
            )
            target_message_id = None
            target_space_id = None

        async with AsyncSessionLocal() as session:
            parent = None
            if target_message_id is not None and target_space_id is not None:
                result = await session.execute(
                    select(Message).where(
                        Message.id == target_message_id,
                        Message.space_id == target_space_id,
                    )
                )
                parent = result.scalar_one_or_none()
            if parent:
                parent_meta = parent.message_metadata or {}
                parent_ui = parent_meta.get("ui") or {}
                parent_signals = parent_ui.get("signals") or {}
                existing_signals = parent_signals.get("agent_skipped")
                if not isinstance(existing_signals, list):
                    existing_signals = []
                existing_signals = [
                    item
                    for item in existing_signals
                    if not isinstance(item, dict) or item.get("id") != signal_id
                ]
                existing_signals.append(signal_payload)
                parent_signals["agent_skipped"] = existing_signals
                parent_ui["signals"] = parent_signals
                parent_meta["ui"] = parent_ui
                parent.message_metadata = parent_meta
                flag_modified(parent, "message_metadata")
                await session.commit()
            else:
                logger.warning(
                    "DISPATCH_AGENT_SKIPPED_PARENT_METADATA_SKIP message_id=%s reason=parent_not_found",
                    message_id,
                )
    except Exception as exc:
        logger.error(
            "DISPATCH_AGENT_SKIPPED_PARENT_METADATA_ERROR agent_name=%s message_id=%s error=%s",
            agent_name,
            message_id,
            exc,
        )

    try:
        from app.services.redis_sse_broker import redis_sse_broker

        await redis_sse_broker.publish(
            space_id=str(space_id),
            event="agent_skipped",
            data=signal_payload,
        )
        await redis_sse_broker.publish(
            space_id=str(space_id),
            event="message_updated",
            data={
                "message_id": str(message_id),
                "field": "message_metadata",
                "metadata": {
                    "ui": {
                        "signals": {
                            "agent_skipped": [signal_payload],
                        }
                    }
                },
            },
        )
    except Exception as exc:
        logger.error(
            "DISPATCH_AGENT_SKIPPED_SIGNAL_ERROR agent_name=%s error=%s",
            agent_name,
            exc,
        )


async def _save_agent_pause_notice(
    *,
    dispatch_id: str,
    agent_id: Optional[str],
    agent_name: str,
    message_id: str,
    space_id: Optional[str],
    channel: str,
    intelligence: dict | None,
) -> Optional[str]:
    """Persist a visible transcript notice when an agent intentionally exits without replying."""
    if not all([agent_id, message_id, space_id]):
        logger.warning(
            "DISPATCH_PAUSE_NOTICE_SKIP dispatch_id=%s agent_name=%s reason=missing_required_fields",
            dispatch_id,
            agent_name,
        )
        return None

    try:
        from app.core.actor import Actor, CAP_MESSAGES_SEND
        from app.core.redis_client import redis_client
        from app.services.messages_service import MessagesService
        from app.services.redis_sse_broker import redis_sse_broker

        actor_id = uuid_module.UUID(str(agent_id))
        space_uuid = uuid_module.UUID(str(space_id))
        content, metadata = _normalize_pause_notice(intelligence, agent_name)
        metadata["dispatch_id"] = dispatch_id
        metadata["agent_id"] = str(agent_id)

        async with AsyncSessionLocal() as session:
            svc = MessagesService(session, redis_client, redis_sse_broker)
            msg = await svc.send(
                actor=Actor(
                    id=actor_id,
                    type="agent",
                    space_id=space_uuid,
                    capabilities={CAP_MESSAGES_SEND},
                ),
                content=content,
                channel=channel or "main",
                message_type="agent_pause",
                parent_id=message_id,
                metadata=metadata,
                author_display_name=agent_name,
                adapter="dispatch_no_reply",
            )
            return str(msg.id)
    except Exception as exc:
        logger.error(
            "DISPATCH_PAUSE_NOTICE_ERROR dispatch_id=%s agent_name=%s error=%s",
            dispatch_id,
            agent_name,
            exc,
        )
        return None


async def _store_ax_intelligence(message_id: str, space_id: str, intelligence: dict):
    """Store aX-generated intelligence on the original message.

    Writes to:
    - Message.ai_summary + Message.summarized_at (if summary present)
    - MessageIntelligence row (if scores present)
    """
    import uuid as uuid_module
    from datetime import datetime, timezone
    from app.models.message import Message
    from app.models.message_intelligence import MessageIntelligence

    summary = intelligence.get("summary")
    has_scores = any(k in intelligence for k in ("spam", "toxicity", "quality", "security_score"))
    silent_forward_targets = (
        _extract_forward_targets(intelligence)
        if intelligence.get("visible") is False
        else []
    )

    try:
        async with AsyncSessionLocal() as session:
            # Update summary on message
            msg = await session.get(Message, uuid_module.UUID(message_id))
            if msg and silent_forward_targets:
                from sqlalchemy.orm.attributes import flag_modified

                meta = msg.message_metadata or {}
                routing = meta.get("routing") or {}
                existing_via = routing.get("via") if isinstance(routing.get("via"), list) else []
                via = [str(item) for item in existing_via if item]
                if "aX" not in via:
                    via.append("aX")

                current_hops = routing.get("hops")
                try:
                    current_hops_value = int(current_hops) if current_hops is not None else 0
                except (TypeError, ValueError):
                    current_hops_value = 0

                routing.update(
                    {
                        "mode": "ax_relay",
                        "hops": max(1, current_hops_value + 1),
                        "via": via,
                        "source_message_id": str(msg.id),
                        "original_sender_id": str(msg.user_id or msg.agent_id or ""),
                        "targets": silent_forward_targets,
                    }
                )
                meta["routing"] = routing
                meta["routed_by_ax"] = True
                meta["top_level_ingress"] = False
                meta["forwarded_by"] = "aX"
                meta["forward_targets"] = silent_forward_targets
                msg.message_metadata = meta
                flag_modified(msg, "message_metadata")

            if summary:
                if msg and not msg.ai_summary:
                    msg.ai_summary = summary
                    msg.summarized_at = datetime.now(timezone.utc)

            # Upsert MessageIntelligence row if we have scores
            if has_scores:
                from sqlalchemy import select
                existing = await session.execute(
                    select(MessageIntelligence).where(
                        MessageIntelligence.message_id == uuid_module.UUID(message_id)
                    )
                )
                intel_row = existing.scalar_one_or_none()
                if intel_row:
                    if "spam" in intelligence:
                        intel_row.spam_score = _clamp_score(intelligence["spam"])
                    if "toxicity" in intelligence:
                        intel_row.toxicity_score = _clamp_score(intelligence["toxicity"])
                    if "quality" in intelligence:
                        intel_row.quality_score = _clamp_score(intelligence["quality"])
                    if "security_score" in intelligence:
                        intel_row.security_risk = _clamp_score(intelligence["security_score"])
                    if "security_category" in intelligence:
                        cat = intelligence["security_category"]
                        intel_row.security_type = cat if cat != "none" else None
                    intel_row.provider_metadata = {"provider": "ax", "method": "unified_intelligence"}
                else:
                    security_cat = intelligence.get("security_category", "none")
                    intel_row = MessageIntelligence(
                        message_id=uuid_module.UUID(message_id),
                        spam_score=_clamp_score(intelligence.get("spam"), 0.0),
                        toxicity_score=_clamp_score(intelligence.get("toxicity"), 0.0),
                        quality_score=_clamp_score(intelligence.get("quality"), 0.5),
                        security_risk=_clamp_score(intelligence.get("security_score"), 0.0),
                        security_type=security_cat if security_cat != "none" else None,
                        security_reason=str(intelligence.get("security_reason", ""))[:500] or None,
                        provider_metadata={"provider": "ax", "method": "unified_intelligence"},
                    )
                    session.add(intel_row)

            await session.commit()
            logger.info(
                f"AX_INTELLIGENCE_STORED message_id={message_id} "
                f"summary={'yes' if summary else 'no'} scores={'yes' if has_scores else 'no'}"
            )

        if silent_forward_targets and space_id:
            await _trigger_silent_forward(
                message_id=message_id,
                space_id=space_id,
                targets=silent_forward_targets,
            )
    except Exception as e:
        logger.error(f"AX_INTELLIGENCE_STORE_ERROR message_id={message_id} error={e}")


def _extract_forward_targets(intelligence: dict | None) -> list[str]:
    """Return normalized target agents for executable aX forwards."""
    if not isinstance(intelligence, dict):
        return []

    routing_decision = str(intelligence.get("routing_decision") or "").strip().lower()
    if routing_decision != "forward":
        return []

    raw_target = intelligence.get("target")
    if isinstance(raw_target, (list, tuple, set)):
        raw_targets = list(raw_target)
    elif isinstance(raw_target, str):
        raw_targets = raw_target.split(",")
    else:
        return []

    normalized: list[str] = []
    for item in raw_targets:
        if not isinstance(item, str):
            continue
        target = item.strip()
        if not target:
            continue
        if target.startswith("@"):
            target = target[1:]
        if not target:
            continue
        normalized.append(target)

    return list(dict.fromkeys(normalized))


async def _trigger_silent_forward(*, message_id: str, space_id: str, targets: list[str]) -> bool:
    """Dispatch a silent concierge handoff through the canonical backend trigger."""
    normalized_targets = [str(target).strip().lstrip("@") for target in targets if str(target).strip()]
    normalized_targets = list(dict.fromkeys([target for target in normalized_targets if target]))
    if not message_id or not space_id or not normalized_targets:
        return False

    settings = get_settings()
    trigger_url = f"{settings.backend_api_url}/internal/dispatch-trigger"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                trigger_url,
                json={
                    "message_id": str(message_id),
                    "space_id": str(space_id),
                    "mentions": normalized_targets,
                },
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": settings.internal_dispatch_api_key,
                },
                timeout=30.0,
            )

        if response.is_success:
            logger.info(
                "AX_SILENT_FORWARD_TRIGGERED message_id=%s space_id=%s targets=%s",
                message_id,
                space_id,
                normalized_targets,
            )
            return True

        logger.warning(
            "AX_SILENT_FORWARD_FAILED message_id=%s status=%s body=%s",
            message_id,
            response.status_code,
            _truncate_for_log(response.text),
        )
        return False
    except Exception as exc:
        logger.error(
            "AX_SILENT_FORWARD_ERROR message_id=%s targets=%s error=%s",
            message_id,
            normalized_targets,
            exc,
        )
        return False


async def _store_ax_summary(message_id: str, summary: str):
    """Legacy wrapper — delegates to _store_ax_intelligence."""
    await _store_ax_intelligence(message_id, "", {"summary": summary})


# MCP tool → ui:// resource URI mapping (mirrors MCP server's WIDGET_SPECS)
_TOOL_RESOURCE_URI: dict[str, str] = {
    "tasks": "ui://task-board",
    "messages": "ui://message-timeline",
    "agents": "ui://agent-dashboard",
    "spaces": "ui://space-navigator",
    "search": "ui://search-results",
    "context": "ui://context-explorer",
    "whoami": "ui://whoami/identity",
}
async def _set_widget(
    message_id: str,
    space_id: str,
    lifecycle: str,
    tool_name: str = "",
    extra_fields: dict | None = None,
    resource_uri: str | None = None,
):
    """Create or update ui.widget on a message and broadcast SSE.

    If no widget exists yet, creates one. If one exists, updates it.
    Widget is only emitted when a real MCP tool is used — not on every ingress.
    resource_uri maps to the MCP server's ui:// resources for iframe rendering.
    """
    import uuid as uuid_module
    from app.models.message import Message

    # Use explicit resource_uri if provided, fall back to allowlist
    if not resource_uri:
        resource_uri = _TOOL_RESOURCE_URI.get(tool_name)
    if not resource_uri and lifecycle != "error":
        logger.debug(
            "WIDGET_SET_SKIP message_id=%s reason=no_widget_for_tool tool=%s",
            message_id, tool_name,
        )
        return

    try:
        async with AsyncSessionLocal() as session:
            msg = await session.get(Message, uuid_module.UUID(message_id))
            if not msg:
                logger.warning(
                    "WIDGET_SET_SKIP message_id=%s reason=not_found", message_id
                )
                return

            meta = msg.message_metadata or {}
            ui_block = meta.get("ui", {})
            widget = ui_block.get("widget")

            if not widget:
                # Create new widget — first tool use on this message
                widget = {
                    "kind": "mcp_app",
                    "tool_name": tool_name,
                    "tool_call_id": str(uuid_module.uuid4()),
                    "resource_uri": resource_uri or f"ui://{tool_name}",
                    "display_mode": "inline",
                }

            widget["lifecycle"] = lifecycle
            widget["revision"] = widget.get("revision", 0) + 1
            if extra_fields:
                widget.update(extra_fields)

            ui_block["widget"] = widget
            meta["ui"] = ui_block
            msg.message_metadata = meta
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(msg, "message_metadata")

            await session.commit()

        # Broadcast SSE so frontend gets the thin widget pointer
        try:
            from app.services.redis_sse_broker import redis_sse_broker
            await redis_sse_broker.publish(
                space_id=space_id,
                event="message_updated",
                data={
                    "message_id": message_id,
                    "field": "ui.widget",
                    "widget": widget,
                },
            )
        except Exception:
            pass

        logger.info(
            "WIDGET_SET message_id=%s lifecycle=%s tool=%s", message_id, lifecycle, tool_name
        )
    except Exception as e:
        logger.error(
            "WIDGET_SET_ERROR message_id=%s lifecycle=%s error=%s",
            message_id, lifecycle, e,
        )


async def _get_widget_from_audit(correlation_id: str) -> dict | None:
    """Query ToolCall audit records to find widget-capable tool calls.

    Returns the PRIMARY widget (most recent) for backward compatibility.
    Use _get_all_widgets_from_audit() for the full list.
    """
    widgets = await _get_all_widgets_from_audit(correlation_id)
    return widgets[0] if widgets else None


async def _get_all_widgets_from_audit(correlation_id: str) -> list[dict]:
    """Query ALL widget-capable tool calls for a dispatch.

    Returns a list of widget dicts from all successful tool calls that
    have a resource_uri, ordered by creation time (newest first).
    Every successful tool call is preserved so the UI can render one
    surface per call instead of collapsing repeated calls by widget type.

    This is the multi-widget version of _get_widget_from_audit().
    """
    from sqlalchemy import select
    from app.models.tool_call import ToolCall

    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(ToolCall)
                .where(
                    ToolCall.correlation_id == correlation_id,
                    ToolCall.status == "success",
                    ToolCall.resource_uri.isnot(None),
                )
                .order_by(ToolCall.created_at.desc())
                .limit(10)  # Cap at 10 widgets per turn
            )
            result = await session.execute(stmt)
            candidates = result.scalars().all()

            if not candidates:
                return []

            widgets = []
            for candidate in candidates:
                # space_id comes from the audit row itself — never from caller
                # input — so the cache lookup is always scoped to the same
                # space that originally stored the payload.
                candidate_space_id = (
                    str(candidate.space_id) if candidate.space_id else None
                )
                initial_data = (
                    await load_tool_call_initial_data(
                        candidate.tool_call_id,
                        space_id=candidate_space_id,
                    )
                    if candidate_space_id
                    else None
                )
                widgets.append({
                    "kind": "mcp_app",
                    "tool_name": candidate.tool_name,
                    "tool_action": candidate.tool_action,
                    "tool_call_id": candidate.tool_call_id,
                    "resource_uri": candidate.resource_uri,
                    "arguments": candidate.arguments,
                    "initial_data": initial_data,
                    "result_kind": candidate.kind,
                    "agent_id": str(candidate.agent_id) if candidate.agent_id else None,
                    "agent_name": candidate.agent_name,
                    "display_mode": "inline",
                })
            return widgets

    except Exception as e:
        logger.error("AUDIT_WIDGET_LOOKUP_ERROR correlation_id=%s error=%s", correlation_id, e)
        return None


async def _execute_space_agent_dispatch(
    payload: dict,
    dispatch_id: str,
    retry_count: int = 0,
    task_id: Optional[str] = None,
) -> DispatchResult:
    """
    Execute a dispatch to the local Space Agent container.

    POSTs to the Space Agent's /invoke endpoint, consumes streaming response,
    relays token deltas via SSE, and saves the final response as a message.
    """
    settings = get_settings()

    agent_name = payload.get("agent_name", "Space Agent")
    agent_id = payload.get("agent_id")
    message_id = payload.get("message_id", "unknown")
    space_id = payload.get("space_id") or payload.get("org_id")
    sender_id = payload.get("sender_id", "")
    sender_handle = payload.get("sender_handle", "")
    sender_type = payload.get("sender_type", "user")
    user_message = payload.get("user_message", "")

    start_time = time.time()

    logger.info(
        f"DISPATCH_SPACE_AGENT_START dispatch_id={dispatch_id} "
        f"agent_name={agent_name} space_id={space_id} retry={retry_count}"
    )

    await _store_dispatch_state(
        dispatch_id=dispatch_id,
        agent_name=agent_name,
        agent_id=agent_id or "",
        space_id=space_id or "",
        message_id=message_id,
        dispatch_type="space_agent",
        status="dispatching",
    )

    if not space_id:
        return DispatchResult(
            status=DispatchStatus.FAILED_PERMANENT,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            error="Missing space_id for Space Agent dispatch",
        )

    # Build session ID for Space Agent
    from app.core.agentcore import build_session_id
    conversation_id = payload.get("context_data", {}).get("trigger_message_id", message_id)
    session_id = build_session_id(space_id, conversation_id) if space_id else f"ax-{space_id[:24]}"

    # Build the invoke payload per Space Agent contract
    context_data = payload.get("context_data", {})
    attachments = attachments_from_dispatch_payload(payload)
    invoke_payload = {
        "inputText": user_message,
        "sessionId": session_id,
        "sessionAttributes": {
            "space_id": space_id,
            "user_id": sender_id,
            "space_name": payload.get("space_name", ""),
            "agent_name": agent_name,
            "mentions": context_data.get("original_mentions", []),
            # Default True: aX dispatch only happens for messages that passed _is_top_level_ingress.
            # Only set False explicitly when aX is forwarding to itself (relay).
            "top_level_ingress": context_data.get("top_level_ingress", True),
        },
        "correlation_id": dispatch_id,
        "stream": True,
        "system_prompt": payload.get("system_prompt", ""),
        "history": payload.get("history", []),
        "context_data": context_data,
        "attachments": attachments,
        # Backwards-compatible alias for callers/logs that still inspect the
        # older name. Space Agent consumes `attachments`.
        "accepted_attachments": attachments,
        "sender_handle": sender_handle,
        "sender_type": sender_type,
    }

    # Pass MCP auth if available so agent can use MCP tools
    mcp_auth = payload.get("mcp_auth", {})
    if mcp_auth:
        # Include correlation context so MCP middleware can link tool calls
        # to this dispatch. Space agent should forward as X-Correlation-Id header.
        mcp_auth = {**mcp_auth, "correlation_id": dispatch_id}
        invoke_payload["mcp_auth"] = mcp_auth

    invoke_url = f"{settings.space_agent_url}/invocations"
    full_response = ""
    tools_used = []  # Track MCP tools used — widget only emitted if non-empty
    widget_error_text: str | None = None

    # Pre-generate a reply message ID so SSE events use a DIFFERENT ID
    # than the original sender's message. The frontend needs distinct IDs
    # to avoid overwriting the landed message with streaming deltas.
    import uuid as _uuid
    reply_stream_id = str(_uuid.uuid4())

    try:
        # Publish "processing" SSE event so frontend shows spinner
        try:
            from app.services.redis_sse_broker import redis_sse_broker
            await redis_sse_broker.publish(
                space_id=space_id,
                event="agent_processing",
                data={
                    "agent_id": agent_id or "",
                    "agent_name": agent_name,
                    "message_id": reply_stream_id,
                    "parent_id": message_id,
                    "status": "processing",
                    "dispatch_type": "space_agent",
                    "dispatch_id": dispatch_id,
                },
            )
        except Exception:
            pass

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.space_agent_timeout, connect=10.0)
        ) as client:
            async with client.stream(
                "POST",
                invoke_url,
                json=invoke_payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Dispatch-ID": dispatch_id,
                },
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_msg = f"Space Agent returned {response.status_code}: {error_body.decode()[:500]}"
                    logger.error(
                        f"DISPATCH_SPACE_AGENT_ERROR dispatch_id={dispatch_id} {error_msg}"
                    )
                    return DispatchResult(
                        status=DispatchStatus.FAILED_PERMANENT if response.status_code < 500 else DispatchStatus.FAILED_RETRYABLE,
                        dispatch_id=dispatch_id,
                        agent_name=agent_name,
                        duration_ms=int((time.time() - start_time) * 1000),
                        error=error_msg,
                    )

                # Stream response — consume Space Agent SSE events
                # Events: message_start, content_delta, tool_start, tool_end, message_end, error
                current_event_type = None
                import time as _time
                _stream_start = _time.monotonic()
                _delta_count = 0
                async for line in response.aiter_lines():
                    if not line:
                        current_event_type = None
                        continue

                    # Parse SSE event type
                    if line.startswith("event: "):
                        current_event_type = line[7:].strip()
                        continue

                    # Parse SSE data
                    if line.startswith("data: "):
                        chunk_data = line[6:]
                        if chunk_data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(chunk_data)
                        except json.JSONDecodeError:
                            continue

                        if current_event_type == "content_delta":
                            delta = chunk.get("text", "")
                            if delta:
                                _delta_count += 1
                                _elapsed = int((_time.monotonic() - _stream_start) * 1000)
                                logger.info(
                                    "DISPATCH_STREAM_DELTA dispatch_id=%s delta_num=%d elapsed_ms=%d len=%d",
                                    dispatch_id, _delta_count, _elapsed, len(delta),
                                )
                                full_response += delta
                                try:
                                    await redis_sse_broker.publish(
                                        space_id=space_id,
                                        event="message_delta",
                                        data={
                                            "agent_id": agent_id or "",
                                            "agent_name": agent_name,
                                            "message_id": reply_stream_id,
                                            "parent_id": message_id,
                                            "dispatch_id": dispatch_id,
                                            "delta": delta,
                                            "accumulated": len(full_response),
                                        },
                                    )
                                except Exception as sse_err:
                                    logger.warning(
                                        "DISPATCH_STREAM_DELTA_SSE_ERROR dispatch_id=%s msg=%s err=%s",
                                        dispatch_id, message_id, sse_err,
                                    )

                        elif current_event_type in ("tool_start", "tool_end"):
                            tool_name = chunk.get("tool", "")
                            try:
                                await redis_sse_broker.publish(
                                    space_id=space_id,
                                    event="agent_processing",
                                    data={
                                        "agent_id": agent_id or "",
                                        "agent_name": agent_name,
                                        "message_id": reply_stream_id,
                                        "parent_id": message_id,
                                        "dispatch_id": dispatch_id,
                                        "status": "tool_use" if current_event_type == "tool_start" else "processing",
                                        "tool": tool_name,
                                    },
                                )
                            except Exception as sse_err:
                                logger.warning(
                                    "DISPATCH_STREAM_TOOL_SSE_ERROR dispatch_id=%s tool=%s err=%s",
                                    dispatch_id, tool_name, sse_err,
                                )

                            if current_event_type == "tool_start":
                                tools_used.append(tool_name)

                        elif current_event_type == "error":
                            error_text = chunk.get("error", "Unknown agent error")
                            logger.error(
                                f"DISPATCH_SPACE_AGENT_STREAM_ERROR dispatch_id={dispatch_id} "
                                f"error={error_text}"
                            )
                            if tools_used:
                                widget_error_text = error_text[:500]

                logger.info(
                    "DISPATCH_STREAM_COMPLETE dispatch_id=%s total_deltas=%d total_elapsed_ms=%d response_len=%d",
                    dispatch_id, _delta_count, int((_time.monotonic() - _stream_start) * 1000), len(full_response),
                )

        duration_ms = int((time.time() - start_time) * 1000)

        # Parse structured intelligence from aX response (three-tier fallback)
        intelligence, visible_content = _parse_ax_intelligence(full_response)
        ai_summary = intelligence.get("summary") if intelligence else None

        # Check if aX explicitly suppressed visibility (observe/silent mode)
        suppress_reply = intelligence and intelligence.get("visible") is False

        # Store intelligence on the ORIGINAL message (not the reply)
        if intelligence:
            await _store_ax_intelligence(message_id, space_id, intelligence)
            if ai_summary:
                try:
                    from app.services.redis_sse_broker import redis_sse_broker
                    await redis_sse_broker.publish(
                        space_id=space_id,
                        event="message_updated",
                        data={"message_id": message_id, "ai_summary": ai_summary, "field": "ai_summary"},
                    )
                except Exception:
                    pass

        # Audit-based widget detection — query ToolCall records written by
        # MCP server middleware during this dispatch. Deterministic: only
        # attaches a widget when a real tool call was audited.
        audit_widgets = await _get_all_widgets_from_audit(dispatch_id)
        audit_widget = audit_widgets[0] if audit_widgets else None
        if audit_widget:
            logger.info(
                "WIDGET_FROM_AUDIT dispatch_id=%s tool=%s resource_uri=%s",
                dispatch_id, audit_widget["tool_name"], audit_widget["resource_uri"],
            )

        # Also check streaming tools_used (tool_start events during streaming)
        primary_tool = next((t for t in tools_used if t in _TOOL_RESOURCE_URI), None)

        # Save the child reply before attaching widget metadata so the widget
        # lands on the visible reply message instead of the original ingress.
        reply_message_id = None
        reply_content = visible_content.strip()
        if not reply_content and (primary_tool or audit_widget):
            reply_content = ai_summary or widget_error_text or "Request processed"

        if reply_content and not suppress_reply:
            try:
                reply_message_id = await _save_agentcore_response(
                    response_text=reply_content,
                    dispatch_id=dispatch_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    message_id=message_id,
                    space_id=space_id,
                    sender_handle=sender_handle,
                    sender_type=sender_type,
                    channel=payload.get("channel", "main"),
                )
                # If ax_intel was stripped, the saved content differs from what
                # was streamed. Emit message_updated so the frontend replaces
                # the streamed version (which may contain raw ax_intel JSON).
                if intelligence and reply_message_id:
                    try:
                        from app.services.redis_sse_broker import redis_sse_broker
                        await redis_sse_broker.publish(
                            space_id=space_id,
                            event="message_updated",
                            data={
                                "message_id": reply_message_id,
                                "field": "content",
                                "content": reply_content,
                            },
                        )
                    except Exception:
                        pass
                # Notify frontend that agent processing is done (spinner teardown)
                if reply_message_id:
                    try:
                        await redis_sse_broker.publish(
                            space_id=space_id,
                            event="agent_processing",
                            data={
                                "status": "completed",
                                "dispatch_id": dispatch_id,
                                "message_id": message_id,
                                "agent_name": agent_name,
                                "reply_message_id": reply_message_id,
                            },
                        )
                    except Exception:
                        pass
            except Exception as save_err:
                logger.error(
                    f"DISPATCH_SPACE_AGENT_SAVE_ERROR dispatch_id={dispatch_id} error={save_err}"
                )
        elif suppress_reply:
            logger.info(
                "DISPATCH_REPLY_SUPPRESSED dispatch_id=%s reason=visible_false",
                dispatch_id,
            )
            if _is_signal_only_no_reply(intelligence):
                await _publish_agent_skipped_signal(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    message_id=message_id,
                    space_id=space_id,
                    intelligence=intelligence or {},
                )
                reply_message_id = None
            elif _is_internal_only_suppression(intelligence):
                logger.info(
                    "DISPATCH_REPLY_INTERNAL_ONLY dispatch_id=%s reason=visible_false",
                    dispatch_id,
                )
                reply_message_id = None
            else:
                reply_message_id = await _save_agent_pause_notice(
                    dispatch_id=dispatch_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    message_id=message_id,
                    space_id=space_id,
                    channel=payload.get("channel", "main"),
                    intelligence=intelligence or {},
                )
            # Tell frontend processing is done so it can tear down the spinner
            try:
                from app.services.redis_sse_broker import redis_sse_broker
                await redis_sse_broker.publish(
                    space_id=space_id,
                    event="agent_processing",
                    data={
                        "status": "completed",
                        "dispatch_id": dispatch_id,
                        "message_id": message_id,
                        "suppress": True,
                    },
                )
            except Exception:
                pass

        # Attach widget metadata to the reply message.
        # Messages keep the thin pointer fields plus small first-paint payloads
        # like initial_data. Heavy rendering blobs stay out of message metadata.
        # Attach widget — prefer audit record, fall back to streaming tools_used
        widget_source = audit_widget or (
            {"tool_name": primary_tool, "resource_uri": _TOOL_RESOURCE_URI.get(primary_tool, "")}
            if primary_tool else None
        )

        if reply_message_id and widget_source and not suppress_reply:
            try:
                widget_extra: dict = {
                    "title": ai_summary or "Request processed",
                    "tools_used": [t for t in tools_used if t],
                }
                if audit_widget:
                    # Rich widget from audit — includes arguments + result_kind
                    for key in (
                        "arguments",
                        "initial_data",
                        "result_kind",
                        "tool_action",
                        "tool_call_id",
                        "agent_id",
                    ):
                        val = audit_widget.get(key)
                        if val is not None:
                            widget_extra[key] = val
                    if audit_widgets:
                        widget_extra["widgets"] = audit_widgets
                if widget_error_text:
                    widget_extra["error_summary"] = widget_error_text[:200]

                await _set_widget(
                    reply_message_id,
                    space_id,
                    "error" if widget_error_text else "complete",
                    tool_name=widget_source["tool_name"],
                    extra_fields=widget_extra,
                    resource_uri=widget_source.get("resource_uri"),
                )
            except Exception as widget_err:
                logger.warning(
                    "DISPATCH_WIDGET_ATTACH_ERROR dispatch_id=%s reply_id=%s tool=%s err=%s",
                    dispatch_id, reply_message_id, widget_source.get("tool_name"), widget_err,
                )

        await _store_dispatch_state(
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            agent_id=agent_id or "",
            space_id=space_id,
            message_id=message_id,
            dispatch_type="space_agent",
            status="completed",
            duration_ms=str(duration_ms),
        )

        logger.info(
            f"DISPATCH_SPACE_AGENT_DONE dispatch_id={dispatch_id} "
            f"agent_name={agent_name} duration_ms={duration_ms} "
            f"response_len={len(full_response)} ai_summary={'yes' if ai_summary else 'no'}"
        )

        return DispatchResult(
            status=DispatchStatus.SUCCESS if full_response.strip() else DispatchStatus.SUCCESS_NO_RESPONSE,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            duration_ms=duration_ms,
            reply_message_id=reply_message_id,
        )

    except httpx.ConnectError as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = f"Cannot connect to Space Agent at {invoke_url}: {e}"
        logger.error(
            f"DISPATCH_SPACE_AGENT_CONNECT_ERROR dispatch_id={dispatch_id} "
            f"duration_ms={duration_ms} error={error_msg}"
        )
        return DispatchResult(
            status=DispatchStatus.FAILED_RETRYABLE,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            duration_ms=duration_ms,
            error=error_msg,
        )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(
            f"DISPATCH_SPACE_AGENT_ERROR dispatch_id={dispatch_id} "
            f"agent_name={agent_name} duration_ms={duration_ms} error={error_msg}"
        )
        return DispatchResult(
            status=DispatchStatus.FAILED_RETRYABLE,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            duration_ms=duration_ms,
            error=error_msg,
        )


# =============================================================================
# Webhook Dispatch (External Gateway Agents)
# =============================================================================


async def _execute_webhook_dispatch(
    payload: dict,
    dispatch_id: str,
    retry_count: int = 0,
    task_id: Optional[str] = None,
) -> DispatchResult:
    """
    Execute a dispatch to an external gateway agent via webhook.

    Uses HMAC-SHA256 signed payload. Follows the same retry semantics as cloud dispatch
    (retries handled by dispatch_worker or Cloud Tasks, not inline).

    Args:
        payload: Full dispatch payload (must include webhook_url, auth_token; secret fetched from DB)
        dispatch_id: Unique dispatch ID for tracing
        retry_count: Current retry attempt (for logging)
        task_id: Optional task ID (for logging)

    Returns:
        DispatchResult with status, timing, and any errors
    """
    settings = get_settings()

    # Extract required fields
    agent_name = payload.get("agent_name", "unknown")
    agent_id = payload.get("agent_id")
    message_id = payload.get("message_id", "unknown")
    space_id = payload.get("space_id") or payload.get("org_id")
    webhook_url = payload.get("webhook_url")  # Fallback from payload
    auth_token = payload.get("auth_token")

    # Fetch webhook_url and webhook_secret FRESH from database
    # This ensures URL changes take effect immediately, even for queued dispatches
    webhook_secret = None
    if agent_id:
        try:
            from sqlalchemy import select
            from app.models.agent import Agent

            async with AsyncSessionLocal() as db:
                try:
                    agent_uuid = uuid_module.UUID(str(agent_id))
                except (ValueError, TypeError):
                    agent_uuid = None

                if agent_uuid:
                    result = await db.execute(
                        select(Agent.webhook_url, Agent.webhook_secret, Agent.webhook_verified, Agent.status)
                        .where(Agent.id == agent_uuid)
                    )
                    row = result.first()
                    if row:
                        # Use FRESH webhook_url from DB (not stale payload data)
                        db_webhook_url = row.webhook_url
                        if db_webhook_url and db_webhook_url != webhook_url:
                            logger.info(
                                f"DISPATCH_URL_REFRESH dispatch_id={dispatch_id} "
                                f"agent_name={agent_name} old_url={webhook_url[:50]}... new_url={db_webhook_url[:50]}..."
                            )
                            webhook_url = db_webhook_url

                        webhook_secret = row.webhook_secret
                        webhook_verified = row.webhook_verified
                        # Note: We no longer check quarantine status here
                        # HMAC is the security - if they have the secret, dispatch proceeds
        except Exception as e:
            logger.error(f"Failed to fetch agent data from DB: {e}")

    # Validate required fields
    if not webhook_url:
        logger.error(
            f"DISPATCH_MISSING_URL dispatch_id={dispatch_id} "
            f"agent_name={agent_name} dispatch_type=webhook"
        )
        return DispatchResult(
            status=DispatchStatus.FAILED_MISSING_URL,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            error="Missing webhook_url",
        )

    if not webhook_secret:
        logger.error(
            f"DISPATCH_FAILED dispatch_id={dispatch_id} "
            f"agent_name={agent_name} dispatch_type=webhook "
            f"error=missing_webhook_secret"
        )
        # Return FAILED_PERMANENT so it doesn't retry indefinitely
        return DispatchResult(
            status=DispatchStatus.FAILED_PERMANENT,
            dispatch_id=dispatch_id,
            agent_name=agent_name,
            error="Missing webhook_secret - agent may not be verified",
        )

    # Build webhook payload (V3 format + webhook-specific fields)
    timestamp = int(time.time())

    # Build callback URLs for async heartbeat/completion pattern
    # These allow Cloudflare containers to push progress and responses back
    # Use webhook_callback_url if set (for external agents), otherwise fall back to backend_api_url
    callback_base_url = (settings.webhook_callback_url or settings.backend_api_url).rstrip("/")
    heartbeat_url = f"{callback_base_url}/internal/dispatch/{dispatch_id}/heartbeat"
    callback_url = f"{callback_base_url}/internal/dispatch/{dispatch_id}/complete"

    webhook_payload = {
        **{k: v for k, v in payload.items() if k not in ("webhook_secret",)},  # Exclude secret from payload
        "auth_token": auth_token,
        "mcp_endpoint": MCP_ENDPOINT,
        "dispatch_type": "message",
        "agent_handle": f"@{agent_name}",
        # Async callback URLs with API key for authentication
        "heartbeat_url": heartbeat_url,
        "callback_url": callback_url,
        "callback_api_key": settings.internal_dispatch_api_key,
    }

    # Serialize ONCE for consistent signature
    body_str = json.dumps(webhook_payload, separators=(",", ":"), sort_keys=True)

    # Compute HMAC-SHA256 signature
    sign_data = f"{timestamp}.{body_str}"
    signature = hmac.new(
        webhook_secret.encode(),
        sign_data.encode(),
        hashlib.sha256
    ).hexdigest()

    # Build headers
    headers = {
        "Content-Type": "application/json",
        "X-AX-Signature": f"sha256={signature}",
        "X-AX-Timestamp": str(timestamp),
        "X-AX-Dispatch-ID": dispatch_id,
        "User-Agent": "aX-Platform-Dispatch/2026.1",
    }

    # Log dispatch attempt
    logger.info(
        f"DISPATCH_ATTEMPT dispatch_id={dispatch_id} "
        f"task_id={task_id} agent_name={agent_name} agent_id={agent_id} "
        f"message_id={message_id} space_id={space_id} "
        f"url={webhook_url} retry={retry_count} "
        f"timeout={WEBHOOK_TIMEOUT_SECONDS}s dispatch_type=webhook"
    )

    # Log payload details for observability
    tool_config = payload.get("tool_config", {})
    logger.info(
        f"DISPATCH_PAYLOAD dispatch_id={dispatch_id} "
        f"agent_name={agent_name} model={payload.get('model', 'unknown')} "
        f"sender={payload.get('sender_handle', 'unknown')} sender_type={payload.get('sender_type', 'unknown')} "
        f"web_browsing={tool_config.get('web_browsing_enabled', False)} "
        f"ax_mcp={tool_config.get('ax_mcp_enabled', False)} "
        f"image_gen={tool_config.get('image_gen_enabled', False)} "
        f"history_count={len(payload.get('history', []))} payload_version={payload.get('payload_version', '3')} "
        f"dispatch_type=webhook "
        f"message_preview=\"{_truncate_for_log(payload.get('user_message', ''))}\""
    )

    dispatch_start = time.time()
    inline_attempt = 0
    # Leave 30s buffer before timeout to allow Cloud Tasks to handle final retry
    max_total_time = WEBHOOK_TIMEOUT_SECONDS - 30

    while True:
        # Guard against retrying past the timeout
        elapsed = time.time() - dispatch_start
        if elapsed > max_total_time:
            logger.warning(
                f"DISPATCH_INLINE_RETRY_TIME_LIMIT dispatch_id={dispatch_id} "
                f"agent_name={agent_name} elapsed_s={elapsed:.1f} limit_s={max_total_time} "
                f"falling_back_to_cloud_tasks_retry"
            )
            return DispatchResult(
                status=DispatchStatus.FAILED_RETRYABLE,
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                duration_ms=int(elapsed * 1000),
                error="Inline retry time limit exceeded",
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    content=body_str,  # Send exact string we signed
                    headers=headers,
                    timeout=httpx.Timeout(
                        connect=10.0,
                        read=float(WEBHOOK_TIMEOUT_SECONDS),
                        write=10.0,
                        pool=10.0,
                    ),
                )

            dispatch_duration_ms = int((time.time() - dispatch_start) * 1000)

            # Check for inline-retryable errors (e.g., Cloudflare DO reset)
            if _is_inline_retryable_error(response.status_code, response.text):
                inline_attempt += 1
                if inline_attempt < INLINE_RETRY_MAX_ATTEMPTS:
                    # First retry is immediate (0s), subsequent retries have delay
                    # This optimizes for DO resets that recover instantly
                    delay = 0 if inline_attempt == 1 else INLINE_RETRY_DELAY_SECONDS
                    logger.warning(
                        f"DISPATCH_INLINE_RETRY dispatch_id={dispatch_id} "
                        f"agent_name={agent_name} inline_attempt={inline_attempt}/{INLINE_RETRY_MAX_ATTEMPTS} "
                        f"http_status={response.status_code} "
                        f"error_preview={_truncate_for_log(response.text, 100)} "
                        f"waiting={delay}s"
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue  # Retry
                else:
                    logger.warning(
                        f"DISPATCH_INLINE_RETRY_EXHAUSTED dispatch_id={dispatch_id} "
                        f"agent_name={agent_name} inline_attempts={inline_attempt} "
                        f"falling_back_to_cloud_tasks_retry"
                    )
                    # Fall through to normal error handling (Cloud Tasks will retry)

            if response.status_code in (200, 202):
                logger.info(
                    f"DISPATCH_SUCCESS dispatch_id={dispatch_id} "
                    f"task_id={task_id} agent_name={agent_name} space_id={space_id} "
                    f"duration_ms={dispatch_duration_ms} "
                    f"http_status={response.status_code} dispatch_type=webhook "
                    f"sender={payload.get('sender_handle', 'unknown')} sender_type={payload.get('sender_type', 'unknown')}"
                )

                # Extract and save the agent's response (if inline)
                # Do this FIRST to know if we have an inline response or async
                save_result = await _save_webhook_response(
                    response=response,
                    dispatch_id=dispatch_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    message_id=message_id,
                    space_id=space_id,
                )
                reply_message_id = save_result.reply_message_id

                # Update dispatch state based on whether we got an inline terminal result
                # - "completed" = got inline response or explicit suppression, task done
                # - "awaiting_callback" = empty response, expecting heartbeats/complete
                dispatch_status = "completed" if save_result.completed_inline else "awaiting_callback"
                await _store_dispatch_state(
                    dispatch_id=dispatch_id,
                    agent_name=agent_name,
                    agent_id=agent_id,
                    space_id=space_id,
                    message_id=message_id,
                    dispatch_type="webhook",
                    status=dispatch_status,
                    duration_ms=dispatch_duration_ms,
                    http_status=response.status_code,
                )

                # Log async pattern for dashboard observability
                if not save_result.completed_inline:
                    logger.info(
                        f"DISPATCH_AWAITING_CALLBACK dispatch_id={dispatch_id} "
                        f"agent_name={agent_name} dispatch_type=webhook "
                        f"(agent will send heartbeats and/or complete callback)"
                    )

                # Clear failure counter on success (Broken Shield protocol)
                if agent_id:
                    try:
                        from app.services.webhook_dispatch_service import clear_dispatch_failures
                        await clear_dispatch_failures(uuid_module.UUID(str(agent_id)))
                    except Exception as e:
                        logger.warning(f"Failed to clear dispatch failures: {e}")

                return DispatchResult(
                    status=(
                        DispatchStatus.SUCCESS
                        if save_result.completed_inline
                        else DispatchStatus.SUCCESS_NO_RESPONSE
                    ),
                    dispatch_id=dispatch_id,
                    agent_name=agent_name,
                    duration_ms=dispatch_duration_ms,
                    http_status=response.status_code,
                    reply_message_id=reply_message_id,
                )
            else:
                # Error response
                raw_error = response.text[:200] if response.text else "No response body"
                error_detail = re.sub(
                    r'(api[_-]?key|token|secret|password)[=:][^\s&"\']+',
                    r'\1=[REDACTED]',
                    raw_error,
                    flags=re.IGNORECASE
                )

                logger.warning(
                    f"DISPATCH_FAILED dispatch_id={dispatch_id} "
                    f"task_id={task_id} agent_name={agent_name} "
                    f"duration_ms={dispatch_duration_ms} "
                    f"http_status={response.status_code} dispatch_type=webhook error={error_detail}"
                )

                # Track failure for Broken Shield protocol (may quarantine after 3 failures)
                if agent_id:
                    await _track_webhook_failure(agent_id, dispatch_id)

                # 4xx = permanent error, 5xx = retryable
                is_retryable = response.status_code >= 500

                return DispatchResult(
                    status=DispatchStatus.FAILED_RETRYABLE if is_retryable else DispatchStatus.FAILED_PERMANENT,
                    dispatch_id=dispatch_id,
                    agent_name=agent_name,
                    duration_ms=dispatch_duration_ms,
                    http_status=response.status_code,
                    error=error_detail,
                )

        except httpx.TimeoutException as e:
            dispatch_duration_ms = int((time.time() - dispatch_start) * 1000)
            logger.error(
                f"DISPATCH_TIMEOUT dispatch_id={dispatch_id} "
                f"task_id={task_id} agent_name={agent_name} "
                f"duration_ms={dispatch_duration_ms} dispatch_type=webhook error={e}"
            )
            # Track failure for Broken Shield protocol
            if agent_id:
                await _track_webhook_failure(agent_id, dispatch_id)
            return DispatchResult(
                status=DispatchStatus.FAILED_RETRYABLE,
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                duration_ms=dispatch_duration_ms,
                error=f"Timeout: {e}",
            )

        except httpx.ConnectError as e:
            dispatch_duration_ms = int((time.time() - dispatch_start) * 1000)
            logger.error(
                f"DISPATCH_CONNECT_ERROR dispatch_id={dispatch_id} "
                f"task_id={task_id} agent_name={agent_name} "
                f"duration_ms={dispatch_duration_ms} dispatch_type=webhook error={e}"
            )
            # Track failure for Broken Shield protocol
            if agent_id:
                await _track_webhook_failure(agent_id, dispatch_id)
            return DispatchResult(
                status=DispatchStatus.FAILED_RETRYABLE,
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                duration_ms=dispatch_duration_ms,
                error=f"Connection error: {e}",
            )

        except Exception as e:
            dispatch_duration_ms = int((time.time() - dispatch_start) * 1000)
            logger.error(
                f"DISPATCH_ERROR dispatch_id={dispatch_id} "
                f"task_id={task_id} agent_name={agent_name} "
                f"duration_ms={dispatch_duration_ms} dispatch_type=webhook error={e}"
            )
            # Track failure for Broken Shield protocol
            if agent_id:
                await _track_webhook_failure(agent_id, dispatch_id)
            return DispatchResult(
                status=DispatchStatus.FAILED_RETRYABLE,
                dispatch_id=dispatch_id,
                agent_name=agent_name,
                duration_ms=dispatch_duration_ms,
                error=str(e),
            )


async def _save_webhook_response(
    response: httpx.Response,
    dispatch_id: str,
    agent_id: Optional[str],
    agent_name: str,
    message_id: str,
    space_id: Optional[str],
) -> SaveAgentResponseResult:
    """
    Extract and save webhook agent response via /internal/agent-reply.

    External agents can return a response directly in the HTTP body,
    or they can use MCP tools to post the response later.

    Returns terminal inline result metadata, including explicit suppression.
    """
    settings = get_settings()

    try:
        response_data = response.json()
        reply_content = response_data.get("response", "")

        if not reply_content:
            logger.info(
                f"DISPATCH_NO_RESPONSE dispatch_id={dispatch_id} "
                f"agent_name={agent_name} dispatch_type=webhook "
                "(agent returned empty response or will use MCP)"
            )
            return SaveAgentResponseResult()

        if not all([agent_id, message_id, space_id]):
            logger.warning(
                f"DISPATCH_SAVE_SKIP dispatch_id={dispatch_id} "
                f"agent_name={agent_name} dispatch_type=webhook "
                f"reason=missing_required_fields "
                f"agent_id={agent_id} message_id={message_id} space_id={space_id}"
            )
            return SaveAgentResponseResult()

        reply_url = f"{settings.backend_api_url}/internal/agent-reply"

        async with httpx.AsyncClient() as client:
            reply_response = await client.post(
                reply_url,
                json={
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "message_id": message_id,
                    "space_id": space_id,
                    "content": reply_content,
                    "dispatch_id": dispatch_id,
                },
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": settings.internal_dispatch_api_key,
                },
                timeout=30.0,
            )

        if reply_response.is_success:
            reply_result = reply_response.json()
            reply_message_id = reply_result.get("reply_message_id")
            suppressed = bool(reply_result.get("suppressed"))
            logger.info(
                f"DISPATCH_RESPONSE_SAVED dispatch_id={dispatch_id} "
                f"agent_name={agent_name} reply_id={reply_message_id} "
                f"suppressed={suppressed} "
                f"content_len={len(reply_content)} dispatch_type=webhook "
                f"response_preview=\"{_truncate_for_log(reply_content, 300)}\""
            )
            return SaveAgentResponseResult(
                reply_message_id=reply_message_id,
                completed_inline=bool(reply_message_id or suppressed),
                suppressed=suppressed,
            )

        logger.error(
            f"DISPATCH_RESPONSE_SAVE_FAILED dispatch_id={dispatch_id} "
            f"agent_name={agent_name} dispatch_type=webhook "
            f"status={reply_response.status_code} "
            f"error={_truncate_for_log(reply_response.text)}"
        )
        return SaveAgentResponseResult()

    except Exception as e:
        logger.error(
            f"DISPATCH_SAVE_ERROR dispatch_id={dispatch_id} "
            f"agent_name={agent_name} dispatch_type=webhook error={e}"
        )
        return SaveAgentResponseResult()
