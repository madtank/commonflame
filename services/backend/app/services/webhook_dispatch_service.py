"""
Webhook Dispatch Service - Universal Plug for External Agents

Handles verification and dispatch for external agents (Moltbot, Ollama, custom).
External agents receive HTTP POST notifications and use MCP tools for reasoning.

Architecture:
1. Verification: WebSub-style GET challenge-response
2. Dispatch: Signed HTTP POST with V3 payload + auth token
3. Callback: External agent uses MCP tools, posts response via messages.send
"""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import time
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import redis_client
from app.models.agent import Agent

logger = logging.getLogger(__name__)

# Configuration
VERIFICATION_TIMEOUT_SECONDS = 60
DISPATCH_TIMEOUT_SECONDS = int(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "30"))  # 30s default, aligned with frontend timeout
DISPATCH_RETRIES = [(0, 1), (1, 5), (2, 15)]  # (attempt, delay_seconds)
VERIFICATION_RATE_LIMIT_ATTEMPTS = 3
VERIFICATION_RATE_LIMIT_COOLDOWN = 300  # 5 minutes
TIMESTAMP_MAX_AGE_SECONDS = 300  # 5 minutes for replay protection
MCP_ENDPOINT = os.getenv("MCP_ENDPOINT", "https://paxai.app")
# Dispatch rate limiting (Structural Shield)
DISPATCH_RATE_LIMIT_PER_MINUTE = 100  # Max dispatches per agent per minute
DISPATCH_RATE_LIMIT_WINDOW = 60  # seconds

# SSRF Protection - blocked IP ranges
# Set WEBHOOK_ALLOW_PRIVATE_IPS=true for local Docker development
ALLOW_PRIVATE_IPS = os.getenv("WEBHOOK_ALLOW_PRIVATE_IPS", "false").lower() == "true"
# Whitelist specific hosts (comma-separated) - these skip IP checks entirely
# Example: WEBHOOK_ALLOWED_HOSTS=host.docker.internal,moltbot,clawdbot
ALLOWED_HOSTS = [h.strip().lower() for h in os.getenv("WEBHOOK_ALLOWED_HOSTS", "").split(",") if h.strip()]

BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Localhost
    ipaddress.ip_network("10.0.0.0/8"),       # Private (Class A)
    ipaddress.ip_network("172.16.0.0/12"),    # Private (Class B)
    ipaddress.ip_network("192.168.0.0/16"),   # Private (Class C)
    ipaddress.ip_network("100.64.0.0/10"),    # Carrier-grade NAT
    ipaddress.ip_network("::1/128"),          # IPv6 localhost
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

# Always block metadata endpoints (even in dev) - these are dangerous
ALWAYS_BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local (AWS/GCP metadata)
]


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """
    Validate webhook URL to prevent SSRF attacks.

    Returns (is_valid, error_message).
    """
    try:
        parsed = urlparse(url)

        # Must be http or https
        if parsed.scheme not in ("http", "https"):
            return False, "URL must use http or https scheme"

        # Must have a hostname
        if not parsed.hostname:
            return False, "URL must have a valid hostname"

        hostname_lower = parsed.hostname.lower()

        # ALWAYS block metadata endpoints (dangerous even in dev, even if whitelisted)
        always_blocked = [
            "metadata.google.internal",  # GCP metadata
            "169.254.169.254",           # AWS/GCP metadata IP
        ]
        if hostname_lower in always_blocked:
            return False, f"Hostname '{parsed.hostname}' is not allowed (metadata endpoint)"

        # Check whitelist - if host is explicitly allowed, skip IP checks
        if ALLOWED_HOSTS and hostname_lower in ALLOWED_HOSTS:
            logger.info(f"SSRF_WHITELIST_ALLOWED hostname={hostname_lower}")
            return True, ""

        # Block localhost unless ALLOW_PRIVATE_IPS is set
        if not ALLOW_PRIVATE_IPS:
            localhost_patterns = ["localhost", "127.0.0.1", "0.0.0.0"]
            if hostname_lower in localhost_patterns:
                return False, f"Hostname '{parsed.hostname}' is not allowed"

        # Try to resolve and check IP
        try:
            import socket
            ip_str = socket.gethostbyname(parsed.hostname)
            ip = ipaddress.ip_address(ip_str)

            # Always block metadata IP range (169.254.x.x)
            for network in ALWAYS_BLOCKED_NETWORKS:
                if ip in network:
                    return False, f"Resolved IP {ip_str} is in blocked range (metadata)"

            # Block private IPs unless ALLOW_PRIVATE_IPS is set
            if not ALLOW_PRIVATE_IPS:
                for network in BLOCKED_IP_NETWORKS:
                    if ip in network:
                        return False, f"Resolved IP {ip_str} is in blocked range"
        except socket.gaierror:
            # Can't resolve - might be valid external domain, allow it
            # The request will fail later if truly unreachable
            pass

        return True, ""

    except Exception as e:
        return False, f"Invalid URL: {e}"


class WebhookVerificationError(Exception):
    """Raised when webhook verification fails."""
    pass


class WebhookDispatchError(Exception):
    """Raised when webhook dispatch fails."""
    pass


async def _post_agent_response(
    session: AsyncSession,
    agent: Agent,
    response_content: str,
    dispatch_id: str,
    original_message_id: str,
    space_id: str,
) -> None:
    """
    Post the external agent's response as a message.

    This replicates the V3 pattern where cloud agents call /internal/agent-reply.
    For external agents, we capture the response from the webhook HTTP body and
    create the message directly.
    """
    from datetime import datetime, timezone
    from uuid import UUID as UUIDType

    from app.models.message import Message
    from app.services.messages_notifications import MessagesNotificationHelper, sanitize_self_mentions
    from app.services.redis_sse_broker import redis_sse_broker

    # Sanitize self-mentions to prevent loops
    sanitized_content, was_stripped = sanitize_self_mentions(response_content, agent.name)

    if was_stripped:
        logger.info(f"WEBHOOK_RESPONSE_SANITIZED agent_name={agent.name} stripped_self_mention=True")

    if not sanitized_content.strip():
        logger.warning(f"WEBHOOK_RESPONSE_EMPTY agent_name={agent.name} dispatch_id={dispatch_id}")
        return

    try:
        space_uuid = UUIDType(space_id)
        original_msg_uuid = UUIDType(original_message_id) if original_message_id else None

        # Create the reply message
        reply_message = Message(
            space_id=space_uuid,
            agent_id=agent.id,
            user_id=None,  # Agent message, not user
            content=sanitized_content.strip(),
            parent_id=original_msg_uuid,  # Thread as reply to triggering message
            created_at=datetime.now(timezone.utc),
        )

        session.add(reply_message)
        await session.commit()
        await session.refresh(reply_message)

        logger.info(
            f"WEBHOOK_RESPONSE_SAVED agent_name={agent.name} "
            f"reply_id={reply_message.id} dispatch_id={dispatch_id}"
        )

        # Broadcast via SSE
        notifier = MessagesNotificationHelper(
            db=session,
            redis_client=redis_client,
            sse_broker=redis_sse_broker,
        )
        await notifier.broadcast_sse(
            space_id=space_uuid,
            msg=reply_message,
            author_name=agent.name,
        )

        # Intelligence handled by aX dispatch — see dispatch_executor.py

        # Dispatch to any mentioned agents (V3 pattern)
        from app.services.mentions_service import MentionsService
        mentions = list(MentionsService.parse_mentions(sanitized_content))
        if mentions:
            logger.info(f"WEBHOOK_RESPONSE_DISPATCH agent_name={agent.name} mentions={mentions}")
            await notifier._dispatch_cloud_agents(
                msg=reply_message,
                author_name=agent.name,
                mentions=mentions,
            )

    except Exception as e:
        logger.error(f"WEBHOOK_RESPONSE_ERROR agent_name={agent.name} dispatch_id={dispatch_id} error={e}")


async def generate_webhook_secret() -> str:
    """Generate a cryptographically secure webhook secret."""
    return secrets.token_urlsafe(32)


async def _check_verification_rate_limit(agent_id: UUID) -> bool:
    """
    Check if verification attempts are rate-limited.
    Returns True if rate-limited (should block), False if allowed.
    """
    try:
        key = f"webhook_verify_attempts:{agent_id}"
        attempts = await redis_client.get(key)

        if attempts and int(attempts) >= VERIFICATION_RATE_LIMIT_ATTEMPTS:
            return True

        return False
    except Exception:
        return False  # Allow if Redis unavailable


async def _increment_verification_attempts(agent_id: UUID) -> None:
    """Increment verification attempt counter with TTL."""
    try:
        key = f"webhook_verify_attempts:{agent_id}"
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, VERIFICATION_RATE_LIMIT_COOLDOWN)
        await pipe.execute()
    except Exception:
        pass  # Best effort


async def _clear_verification_attempts(agent_id: UUID) -> None:
    """Clear verification attempt counter on success."""
    try:
        key = f"webhook_verify_attempts:{agent_id}"
        await redis_client.delete(key)
    except Exception:
        pass  # Best effort


async def _check_dispatch_rate_limit(agent_id: UUID) -> bool:
    """
    Check if dispatch is rate-limited for this agent.
    Returns True if rate-limited (should block), False if allowed.

    Structural Shield: 100 dispatches/minute per agent.
    """
    try:
        key = f"webhook_dispatch_rate:{agent_id}"
        count = await redis_client.get(key)

        if count and int(count) >= DISPATCH_RATE_LIMIT_PER_MINUTE:
            logger.warning(f"Agent {agent_id} dispatch rate-limited ({count} dispatches/min)")
            return True

        return False
    except Exception:
        return False  # Allow if Redis unavailable


async def _increment_dispatch_count(agent_id: UUID) -> None:
    """Increment dispatch counter with sliding window TTL."""
    try:
        key = f"webhook_dispatch_rate:{agent_id}"
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, DISPATCH_RATE_LIMIT_WINDOW)
        await pipe.execute()
    except Exception:
        pass  # Best effort


async def verify_webhook(
    session: AsyncSession,
    agent: Agent,
) -> bool:
    """
    Verify webhook URL ownership using WebSub-style challenge-response.

    Flow:
    1. Generate challenge token
    2. GET {webhook_url}?hub.mode=subscribe&hub.challenge={challenge}&hub.agent_id={agent_id}
    3. Agent must respond with challenge token in body within 60s
    4. On success, set webhook_verified=True

    Returns True if verification succeeded, False otherwise.
    """
    if not agent.webhook_url:
        logger.warning(f"Agent {agent.id} has no webhook_url to verify")
        return False

    # SSRF Protection: Validate URL before making any requests
    is_valid, error = validate_webhook_url(agent.webhook_url)
    if not is_valid:
        logger.warning(f"SSRF_BLOCKED agent={agent.id} url={agent.webhook_url} reason={error}")
        raise WebhookVerificationError(f"Invalid webhook URL: {error}")

    # Check rate limit
    if await _check_verification_rate_limit(agent.id):
        logger.warning(f"Agent {agent.id} webhook verification rate-limited")
        raise WebhookVerificationError("Verification rate-limited. Please wait 5 minutes.")

    # Generate challenge
    challenge = secrets.token_urlsafe(16)

    # Build verification URL with WebSub-style params
    params = {
        "hub.mode": "subscribe",
        "hub.challenge": challenge,
        "hub.agent_id": str(agent.id),
    }

    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"Sending verification challenge to {agent.webhook_url}")
            response = await client.get(
                agent.webhook_url,
                params=params,
                timeout=VERIFICATION_TIMEOUT_SECONDS,
            )

            if response.status_code != 200:
                await _increment_verification_attempts(agent.id)
                logger.warning(
                    f"Webhook verification failed for agent {agent.id}: "
                    f"status={response.status_code}"
                )
                return False

            # Check challenge echo (must be plain text, exact match)
            response_text = response.text.strip()
            if response_text != challenge:
                await _increment_verification_attempts(agent.id)
                logger.warning(
                    f"Webhook verification failed for agent {agent.id}: "
                    f"challenge mismatch (expected={challenge}, got={response_text})"
                )
                return False

            # Success - update agent
            agent.webhook_verified = True
            agent.webhook_secret = await generate_webhook_secret()
            agent.last_dispatch_error = None

            await session.commit()
            await _clear_verification_attempts(agent.id)

            logger.info(f"Webhook verified successfully for agent {agent.id}")
            return True

    except httpx.TimeoutException:
        await _increment_verification_attempts(agent.id)
        logger.warning(f"Webhook verification timed out for agent {agent.id}")
        return False
    except Exception as e:
        await _increment_verification_attempts(agent.id)
        logger.exception(f"Webhook verification error for agent {agent.id}: {e}")
        return False


def compute_signature(secret: str, timestamp: int, payload: dict) -> str:
    """
    Compute HMAC-SHA256 signature for webhook payload.

    Format: {timestamp}.{json_payload}
    This prevents replay attacks by including timestamp in signature.
    """
    sign_data = f"{timestamp}.{json.dumps(payload, separators=(',', ':'), sort_keys=True)}"
    signature = hmac.new(
        secret.encode(),
        sign_data.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature


def verify_signature(secret: str, timestamp: int, payload: dict, signature: str) -> bool:
    """Verify HMAC signature matches expected value."""
    expected = compute_signature(secret, timestamp, payload)
    return hmac.compare_digest(expected, signature)


async def dispatch_to_external_agent(
    session: AsyncSession,
    agent: Agent,
    payload: dict,
    auth_token: str,
) -> bool:
    """
    Dispatch message to external agent via webhook.

    Flow:
    1. Verify agent is verified and active
    2. Build webhook payload (V3 + auth_token + mcp_endpoint)
    3. Sign with HMAC-SHA256
    4. POST with retry logic (1s, 5s, 15s)
    5. Track fidelity_ms on success
    6. Post system message on failure

    External agent should:
    1. Return 200/202 immediately
    2. Validate HMAC signature
    3. Reject if timestamp > 5 min old
    4. Use auth_token to call MCP tools
    5. Post response via messages.send

    Returns True if dispatch succeeded (got 200/202), False otherwise.
    """
    # Safety checks
    # Note: webhook_verified removed as dispatch gate - HMAC signing is the real security
    # If gateway has the secret, HMAC will validate. If not, dispatch fails with 401.

    if agent.status != "active":
        logger.warning(f"Cannot dispatch to non-active agent {agent.id} (status={agent.status})")
        return False

    # Rate limit check (Structural Shield)
    if await _check_dispatch_rate_limit(agent.id):
        logger.warning(f"Dispatch rate-limited for agent {agent.id}")
        return False

    if not agent.webhook_url:
        logger.warning(f"Agent {agent.id} has no webhook_url")
        return False

    # SSRF Protection: Validate URL before making any requests
    is_valid, error = validate_webhook_url(agent.webhook_url)
    if not is_valid:
        logger.warning(f"SSRF_BLOCKED_DISPATCH agent={agent.id} url={agent.webhook_url} reason={error}")
        return False

    if not agent.webhook_secret:
        logger.warning(f"Agent {agent.id} has no webhook_secret")
        return False

    # Build webhook payload
    timestamp = int(time.time())
    webhook_payload = {
        **payload,
        "auth_token": auth_token,
        "mcp_endpoint": MCP_ENDPOINT,
        "dispatch_type": "message",
        # Ensure these are in body (some agents expect them here, not just headers)
        "agent_handle": f"@{agent.name}",
    }

    # CRITICAL: Serialize ONCE so signature matches exactly what we send
    # Using sort_keys=True ensures deterministic ordering for signature verification
    body_str = json.dumps(webhook_payload, separators=(",", ":"), sort_keys=True)

    # Compute signature over the EXACT string we'll send
    sign_data = f"{timestamp}.{body_str}"
    signature = hmac.new(
        agent.webhook_secret.encode(),
        sign_data.encode(),
        hashlib.sha256
    ).hexdigest()

    # Build headers
    headers = {
        "Content-Type": "application/json",
        "X-AX-Signature": f"sha256={signature}",
        "X-AX-Timestamp": str(timestamp),
        "X-AX-Dispatch-ID": payload.get("dispatch_id", ""),
        "User-Agent": "aX-Platform-Dispatch/2026.1",
    }

    # Dispatch with retry
    last_error = None
    async with httpx.AsyncClient() as client:
        for attempt, delay in DISPATCH_RETRIES:
            try:
                start = time.monotonic()
                response = await client.post(
                    agent.webhook_url,
                    content=body_str,  # Send exact string we signed (not json=)
                    headers=headers,
                    timeout=DISPATCH_TIMEOUT_SECONDS,
                )
                elapsed_ms = int((time.monotonic() - start) * 1000)

                if response.status_code in (200, 202):
                    # Success - update fidelity tracking
                    agent.fidelity_ms = elapsed_ms
                    agent.last_dispatch_error = None
                    await session.commit()

                    # Track dispatch for rate limiting
                    await _increment_dispatch_count(agent.id)

                    logger.info(
                        f"Webhook dispatch succeeded for agent {agent.id}: "
                        f"status={response.status_code}, fidelity_ms={elapsed_ms}"
                    )

                    # Parse response and post agent reply if present
                    try:
                        response_data = response.json()
                        agent_response = response_data.get("response")
                        if agent_response and agent_response.strip():
                            # Post the response as a message from the agent
                            await _post_agent_response(
                                session=session,
                                agent=agent,
                                response_content=agent_response,
                                dispatch_id=payload.get("dispatch_id", ""),
                                original_message_id=payload.get("message_id", ""),
                                space_id=payload.get("space_id", ""),
                            )
                            logger.info(
                                f"Webhook response posted for agent {agent.id}: "
                                f"content_len={len(agent_response)}"
                            )
                    except Exception as parse_err:
                        # Response parsing failed - not fatal, dispatch succeeded
                        logger.warning(
                            f"Failed to parse/post webhook response for agent {agent.id}: {parse_err}"
                        )

                    return True

                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.warning(
                    f"Webhook dispatch attempt {attempt + 1} failed for agent {agent.id}: "
                    f"{last_error}"
                )

            except httpx.TimeoutException:
                last_error = "Request timed out"
                logger.warning(
                    f"Webhook dispatch attempt {attempt + 1} timed out for agent {agent.id}"
                )
            except Exception as e:
                last_error = str(e)
                logger.exception(
                    f"Webhook dispatch attempt {attempt + 1} error for agent {agent.id}: {e}"
                )

            # Wait before retry (except on last attempt)
            if attempt < len(DISPATCH_RETRIES) - 1:
                await asyncio.sleep(delay)

    # All retries failed
    agent.last_dispatch_error = last_error
    await session.commit()

    logger.error(f"Webhook dispatch failed for agent {agent.id} after all retries: {last_error}")
    return False


async def revoke_verification_on_failures(
    session: AsyncSession,
    agent_id: UUID,
) -> None:
    """
    SIMPLIFIED: Just log failures, don't quarantine.

    HMAC is the security mechanism - if endpoint has the secret, it's trusted.
    Quarantine was causing more pain than it solved (tunnel URL changes, etc.)
    """
    # No-op: We no longer quarantine on failures
    # HMAC signing is sufficient security - user manages their own endpoint
    logger.debug(f"Webhook dispatch failure for agent {agent_id} (no quarantine)")


async def clear_dispatch_failures(agent_id: UUID) -> None:
    """Clear failure counter on successful dispatch."""
    try:
        key = f"webhook_dispatch_failures:{agent_id}"
        await redis_client.delete(key)
    except Exception:
        pass  # Best effort
