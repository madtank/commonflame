"""
Message notifications and broadcasting: SSE events, unread tracking, mentions.
Extracted from messages_service.py for better modularity.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from opentelemetry import trace

from ..models.message import Message
from ..models.user import User
from ..models.agent import Agent
from ..models.space import Space
from ..models.space_membership import SpaceMembership
import httpx
import asyncio
import json
import logging
from sqlalchemy import func, and_

logger = logging.getLogger(__name__)

from app.services.cloud_agent_limiter import CloudAgentLimiter
from app.core.attachment_payload import (
    attachment_event_refs_from_message_metadata,
    attachments_from_message_metadata,
)
from app.core.models_config import DEFAULT_MODEL, resolve_bedrock_model_id
from app.services.mentions_service import MentionsService
from app.services.message_visibility import exclude_ui_only_no_reply_clause
from app.core.config import get_settings
from app.services.agent_control_service import AgentControlService

# Import for MCP token generation (cached tokens with sliding window)
from app.core.mcp_token_cache import get_or_mint_mcp_token, mint_delegated_token
# Import for tool toggles (DRY)
from app.core.agent_toggles import build_enabled_tools_from_agent
from app.services.message_intelligence import summarize_message


# Configurable placeholder for self-mentions (prevents loops while being clear)
SELF_MENTION_PLACEHOLDER = os.environ.get("SELF_MENTION_PLACEHOLDER", "[self-reference]")

# Background tasks set to prevent GC of fire-and-forget tasks
_background_tasks: set = set()

# Minimum pause duration for throttling (prevents very short pauses)
MIN_THROTTLE_PAUSE_SECONDS = int(os.environ.get("MIN_THROTTLE_PAUSE_SECONDS", "10"))

# Maximum routing hops for concierge relay — prevents aX→A→aX→A loops
MAX_ROUTING_HOPS = int(os.environ.get("MAX_ROUTING_HOPS", "2"))

# Routing timeout — seconds to wait before marking a relay dispatch as timed out
ROUTING_TIMEOUT_SECONDS = int(os.environ.get("ROUTING_TIMEOUT_SECONDS", "60"))

# Direct bypass is intentionally stricter than general mention parsing:
# allow normal prose punctuation, but reject copied handles wrapped in markup
# noise like angle brackets or backticks.
DIRECT_ROUTE_MENTION_PATTERN = re.compile(
    r"""(?<![A-Za-z0-9_/-])@([A-Za-z][\w-]*)(?=$|[\s\.,!?:;\)\]\}\"'])"""
)


async def _routing_timeout_check(dispatch_id: str, space_id: str) -> None:
    """Background task: after ROUTING_TIMEOUT_SECONDS, mark dispatch as timed out if still pending."""
    try:
        await asyncio.sleep(ROUTING_TIMEOUT_SECONDS)
        import redis.asyncio as aioredis
        settings = get_settings()
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)
        routing_key = f"ax:routing-track:{dispatch_id}"
        current_status = await redis_conn.hget(routing_key, "status")
        if current_status == "dispatched":
            await redis_conn.hset(routing_key, mapping={
                "status": "timeout",
                "timed_out_at": datetime.now(timezone.utc).isoformat(),
            })
            # Publish timeout event
            from app.services.redis_sse_broker import redis_sse_broker
            routing_data = await redis_conn.hgetall(routing_key)
            await redis_sse_broker.publish(
                space_id=space_id,
                event="routing_status",
                data={
                    "original_message_id": routing_data.get("original_msg_id", ""),
                    "target_agent": routing_data.get("target_agent_name", ""),
                    "target_agent_id": routing_data.get("target_agent_id", ""),
                    "status": "timeout",
                    "dispatch_id": dispatch_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.warning(
                "ROUTING_TIMEOUT dispatch_id=%s target=%s after=%ds",
                dispatch_id, routing_data.get("target_agent_name"), ROUTING_TIMEOUT_SECONDS,
            )
        await redis_conn.close()
    except Exception as e:
        logger.warning("ROUTING_TIMEOUT_CHECK_FAILED dispatch_id=%s error=%s", dispatch_id, e)


async def _resolve_message_sender_display_name(session: AsyncSession, msg: Message) -> str:
    """Resolve a stable display name for conversation-card participants."""
    if msg.agent_id:
        agent = await session.get(Agent, msg.agent_id)
        return (agent.name if agent else "") or ""
    if msg.user_id:
        user = await session.get(User, msg.user_id)
        if not user:
            return ""
        return user.username or user.full_name or ""
    return ""


def build_agent_dispatch_prompt(*, agent_name: str, system_prompt: str | None = None) -> str:
    """
    Compose the prompt every dispatched specialist receives.

    This keeps group-chat / anti-swarm rules load-bearing even when an agent has
    a custom prompt that overclaims capabilities or encourages chatter.
    """
    identity_prefix = (
        f"YOUR IDENTITY:\n"
        f"- Your name is @{agent_name}\n"
        f"- You are an AI agent (not a user)\n"
        f"- When you see '(user)' or '(agent)' in messages, those labels describe the MESSAGE SENDER, not you\n\n"
    )

    dispatch_contract = (
        "UNIVERSAL DISPATCH CONTRACT:\n"
        "- You are in a shared workspace, not an invitation to a town hall.\n"
        "- Only respond when the current dispatch is actually for you.\n"
        "- If someone is only naming agents, describing capabilities, or listing the roster, stay silent unless they clearly asked you to act.\n"
        "- Do not self-introduce just because your name or specialty was mentioned.\n"
        "- A fresh direct human request or explicit routed assignment outranks ambient agent chatter.\n"
        "- If you lack the required access, say that plainly. Do not imply shell, code, or tool access you do not actually have.\n"
        "- Do not pull extra agents into the thread unless collaboration is necessary for the work.\n"
        "- Keep responses focused on the specific request you were dispatched to handle.\n\n"
    )

    default_prompt = (
        "DEFAULT WORKSPACE BEHAVIOR:\n"
        "- This is a GROUP CHAT with multiple users and AI agents.\n"
        "- You see recent shared context for continuity.\n"
        "- Messages route through aX, the platform's concierge intelligence.\n"
        "- ONLY respond when someone @mentions you directly or you are explicitly dispatched for the task.\n"
        "- Answer the specific question or do the specific work requested.\n"
        "- Start responses with @sender_name so it is clear who you are replying to.\n"
        "- Users can already see rendered MCP widgets and tool results, so summarize or interpret them rather than repeating raw payloads.\n"
        "- Be direct, useful, and brief.\n"
    )

    custom_prompt = (system_prompt or "").strip()
    if custom_prompt:
        return (
            identity_prefix
            + dispatch_contract
            + "AGENT-SPECIFIC INSTRUCTIONS:\n"
            + custom_prompt
        )

    return identity_prefix + dispatch_contract + default_prompt


async def summarize_and_store(message_id: str, space_id: str, content: str):
    """Background task: summarize message and store result.

    Fast path — calls cheap model, writes ai_summary, publishes SSE.
    Independent of aX dispatch. Never raises.
    """
    import uuid as uuid_module
    from datetime import datetime, timezone
    from app.core.database import AsyncSessionLocal
    from app.models.message import Message

    summary = await summarize_message(content)
    if not summary:
        return

    try:
        async with AsyncSessionLocal() as session:
            msg = await session.get(Message, uuid_module.UUID(message_id))
            if msg and not msg.ai_summary:  # Don't overwrite if aX already set one
                msg.ai_summary = summary
                msg.summarized_at = datetime.now(timezone.utc)
                await session.commit()

        # Publish SSE
        from app.services.redis_sse_broker import redis_sse_broker
        await redis_sse_broker.publish(
            space_id=space_id,
            event="message_updated",
            data={"message_id": message_id, "ai_summary": summary, "field": "ai_summary"},
        )
        logger.info("FAST_SUMMARY_STORED message_id=%s len=%d", message_id, len(summary))

        sender_display_name = await _resolve_message_sender_display_name(session, msg) if msg else ""

        # Update conversation card (thread-level rolling summary)
        try:
            if msg and msg.parent_id:
                # Reply — walk to thread root
                root_id = msg.parent_id
                parent = await session.get(Message, msg.parent_id)
                while parent and parent.parent_id:
                    root_id = parent.parent_id
                    parent = await session.get(Message, parent.parent_id)

                from app.services.conversation_card_service import update_card_on_new_message
                await update_card_on_new_message(
                    root_message_id=str(root_id),
                    space_id=space_id,
                    new_content=content,
                    sender_display_name=sender_display_name,
                    sender_type="agent" if msg.agent_id else "human",
                    sender_id=str(msg.agent_id or msg.user_id or ""),
                )
            elif msg and not msg.parent_id:
                # Root message — create initial card with summary
                from app.services.conversation_card_service import get_or_create_card, publish_card_updated
                async with AsyncSessionLocal() as card_session:
                    card = await get_or_create_card(
                        db=card_session,
                        root_message_id=message_id,
                        space_id=space_id,
                        channel=msg.channel or "main",
                        sender_display_name=sender_display_name,
                        sender_type="agent" if msg.agent_id else "human",
                        sender_id=str(msg.agent_id or msg.user_id or ""),
                    )
                    if card and not card.summary and summary:
                        card.summary = summary
                    await card_session.commit()
                    await publish_card_updated(space_id, card)
        except Exception as card_err:
            logger.warning("CONV_CARD_HOOK_ERROR message_id=%s error=%s", message_id, card_err)

    except Exception as e:
        logger.warning("FAST_SUMMARY_ERROR message_id=%s error=%s", message_id, e)


def classify_dispatch_failure(exc: Exception) -> str:
    """Classify cloud-agent dispatch failures for actionable alerting."""
    msg = str(exc).lower()

    if isinstance(exc, httpx.TimeoutException):
        return "timeout"

    if isinstance(exc, httpx.ConnectError):
        if "name resolution" in msg or "nodename nor servname" in msg:
            return "dns_error"
        return "connect_error"

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code in (401, 403):
            return "auth_error"
        if status_code == 429:
            return "rate_limited"
        if status_code and status_code >= 500:
            return "upstream_5xx"
        return "upstream_4xx"

    if isinstance(exc, httpx.NetworkError):
        return "network_error"

    return "error"


def sanitize_self_mentions(content: str, agent_name: str) -> tuple[str, bool]:
    """
    Strip the @ from self-mentions to prevent infinite dispatch loops without
    introducing a confusing placeholder that other agents misinterpret.
    Returns (sanitized_content, was_stripped).
    """
    if not content or not agent_name:
        return content, False

    # Replace @agent_name with just agent_name (drop the @ to prevent re-dispatch)
    pattern = rf'@{re.escape(agent_name)}\b'
    original = content
    sanitized = re.sub(pattern, agent_name, content, flags=re.IGNORECASE)
    was_stripped = sanitized != original
    return sanitized, was_stripped


class MessagesNotificationHelper:
    """Helper for message notifications and broadcasting."""

    def __init__(self, db: AsyncSession, redis_client, sse_broker):
        self.db = db
        self.redis = redis_client
        self.sse = sse_broker
        self._tracer = trace.get_tracer("ax.services.messages.notifications")
        self.cloud_agent_limiter = CloudAgentLimiter(redis_client)
        self.agent_control_service = AgentControlService(redis_client) if redis_client else None

    def _strip_self_mention(self, content: str, agent_name: str) -> str:
        """
        Replace self-mentions (e.g. @agent_name) with placeholder to prevent loops.
        Also handles the agent's name if it appears as a prefix (e.g. "QuickTitan: Hello").

        Uses SELF_MENTION_PLACEHOLDER so it's clear what was removed.
        """
        if not content or not agent_name:
            return content

        # 1. Replace @mentions of self with placeholder
        pattern = re.compile(rf"@{re.escape(agent_name)}\b", re.IGNORECASE)
        content = pattern.sub(SELF_MENTION_PLACEHOLDER, content).strip()

        # 2. Handle agent name appearing as a prefix (e.g. "QuickTitan: Hello")
        # Only replace at the start of content, not everywhere (to avoid over-sanitizing)
        prefix_pattern = re.compile(rf"^{re.escape(agent_name)}\s*:\s*", re.IGNORECASE)
        content = prefix_pattern.sub("", content)

        # 3. Cleanup: collapse multiple spaces
        content = re.sub(r"\s+", " ", content).strip()

        return content

    async def _get_org_tier(self, session, space_id: UUID) -> str:
        try:
            result = await session.execute(select(Space.tier).where(Space.id == space_id))
            tier = result.scalar() or "free"
            return (tier or "free").lower()
        except Exception:
            return "free"

    def _normalize_dispatch_mentions(self, mentions: list[str] | None) -> list[str]:
        """Normalize explicit mention handles and apply optional aliases."""
        normalized_mentions = [m.lower().lstrip("@").strip() for m in (mentions or []) if m]
        if not normalized_mentions:
            return []

        alias_map: dict[str, str] = {}
        alias_map_raw = os.environ.get("MENTION_ALIAS_MAP_JSON", "")
        if alias_map_raw:
            try:
                loaded = json.loads(alias_map_raw)
                if isinstance(loaded, dict):
                    alias_map = {
                        str(k).lower().lstrip("@").strip(): str(v).lower().lstrip("@").strip()
                        for k, v in loaded.items()
                        if k and v
                    }
            except Exception as alias_err:
                logger.warning("Invalid MENTION_ALIAS_MAP_JSON: %s", alias_err)

        resolved_mentions = [alias_map.get(m, m) for m in normalized_mentions]
        return list(dict.fromkeys(resolved_mentions))

    def _extract_direct_route_mentions(self, content: str | None) -> list[str]:
        """Extract only clean explicit @handles eligible for direct bypass.

        Strips code fences and inline code before extraction, matching
        MentionsService.parse_mentions behavior. Without this, @handles
        quoted inside fenced context blocks (e.g. check-in prompt previews)
        would be treated as real dispatch targets, causing fan-out to every
        agent named in recent activity.
        """
        if not content:
            return []

        stripped = MentionsService._CODE_FENCE_PATTERN.sub('', content)
        stripped = MentionsService._INLINE_CODE_PATTERN.sub('', stripped)
        handles = [match.group(1).lower() for match in DIRECT_ROUTE_MENTION_PATTERN.finditer(stripped)]
        return list(dict.fromkeys(handles))

    async def _lookup_explicit_space_agent_mentions(
        self,
        *,
        session: AsyncSession,
        msg: Message,
        normalized_mentions: list[str],
    ) -> list[Agent]:
        """Resolve same-space space-agent mentions even without AgentSpaceAccess rows."""
        if not normalized_mentions:
            return []

        from app.core.agent_space import agents_in_space_subquery
        result = await session.execute(
            select(Agent).where(
                Agent.id.in_(agents_in_space_subquery(msg.space_id)),
                Agent.origin == "space_agent",
                Agent.status == "active",
                func.lower(Agent.name).in_(normalized_mentions),
            )
        )
        agents = result.scalars().all()
        if not agents:
            return []

        by_name = {
            (agent.name or "").strip().lower(): agent
            for agent in agents
            if (agent.name or "").strip()
        }
        if any(name not in by_name for name in normalized_mentions):
            return []
        return [by_name[name] for name in normalized_mentions]

    async def _resolve_top_level_direct_targets(self, *, msg: Message, mentions: list[str]) -> list[Agent]:
        """
        Resolve whether explicit mentions should bypass aX.

        Direct bypass is allowed only when:
        - every explicit mention resolves to an active accessible agent
        - none of those mentions is the space concierge itself
        """
        normalized_mentions = self._normalize_dispatch_mentions(mentions)
        if not normalized_mentions:
            return []

        from ..core.database import AsyncSessionLocal
        from app.models.agent_space_access import AgentSpaceAccess

        base_filters = and_(
            Agent.status == 'active',
            or_(
                AgentSpaceAccess.space_id == msg.space_id,
                Agent.visibility_level == 'global',
            ),
            AgentSpaceAccess.state == 'active',
        )

        query = (
            select(Agent)
            .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
            .where(
                and_(
                    func.lower(Agent.name).in_(normalized_mentions),
                    base_filters,
                )
            )
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(query)
            agents = result.scalars().all()
        if not agents:
            return []

        by_name = {
            (agent.name or "").strip().lower(): agent
            for agent in agents
            if (agent.name or "").strip()
        }

        if any(name not in by_name for name in normalized_mentions):
            return []

        ordered_agents = [by_name[name] for name in normalized_mentions]
        if any((agent.origin or "").lower() == "space_agent" for agent in ordered_agents):
            return []

        return ordered_agents

    def _requires_backend_dispatch(self, agent: Agent) -> bool:
        """Return True when backend must actively dispatch beyond SSE mention delivery."""
        origin = (agent.origin or "").lower()
        if agent.cloud_function_url:
            return True
        if origin == "external_gateway" and agent.webhook_url:
            return True
        if origin in {"space_agent", "agentcore"}:
            return True
        return False

    async def _persist_message_metadata(self, msg: Message) -> None:
        """Persist metadata updates made during notification-time routing decisions."""
        from ..core.database import AsyncSessionLocal
        from ..models.message import Message as MsgModel
        from sqlalchemy.orm.attributes import flag_modified

        async with AsyncSessionLocal() as session:
            db_msg = await session.get(MsgModel, msg.id)
            if not db_msg:
                return
            db_msg.message_metadata = msg.message_metadata
            flag_modified(db_msg, "message_metadata")
            await session.commit()

    async def _dispatch_top_level_ingress(
        self,
        *,
        msg: Message,
        author_name: str,
        mentions: list[str],
    ) -> None:
        """Route top-level ingress either directly to valid mentioned agents or to aX."""
        normalized_mentions = self._normalize_dispatch_mentions(mentions)

        # Agent-authored messages should not auto-dispatch to aX as a fallback.
        # The aX concierge fallback is intended for human ingress; agent traffic
        # (replies, status updates, agent-to-agent, agent-to-human) should not
        # generate concierge noise like "🤐 aX chose not to reply", work cards,
        # or processing indicators in the shared space. Agents reach aX only
        # via an explicit @ax mention.
        #
        # The same rule already exists in two other dispatch paths in this
        # file: the aX self-loop guard at lines ~889-914 (passes
        # skip_ax_fallback=True) and _dispatch_cloud_agents:1530-1537
        # (early-return when msg.agent_id and no agent matched). This
        # extends the rule to the top-level ingress path so that
        # parentless agent messages don't bounce back into aX as fallback.
        sender_is_agent = msg.agent_id is not None
        explicit_ax_mention = "ax" in normalized_mentions
        ax_fallback_allowed = (not sender_is_agent) or explicit_ax_mention

        eligible_mentions = self._normalize_dispatch_mentions(
            self._extract_direct_route_mentions(msg.content or "")
        )
        metadata_direct_route = bool(
            normalized_mentions
            and (msg.message_metadata or {}).get("direct_route_eligible") is True
        )
        if metadata_direct_route:
            # Explicit server-normalized metadata targets (primary_agent_id,
            # mentioned_agent_ids, mentioned_group_ids) are direct routes even
            # when their handles are not present in content. This is how UI
            # group sends fan out to members without forcing synthetic @handles
            # into the human-visible message body.
            eligible_mentions = list(normalized_mentions)
        if normalized_mentions and set(eligible_mentions) != set(normalized_mentions):
            logger.info(
                "DIRECT_MENTION_BYPASS_AX_SKIPPED message_id=%s requested=%s eligible=%s sender=%s",
                msg.id,
                normalized_mentions,
                eligible_mentions,
                author_name,
            )
            if ax_fallback_allowed:
                await self._dispatch_to_ax(msg=msg, author_name=author_name, mentions=mentions or [])
            else:
                logger.info(
                    "TOP_LEVEL_INGRESS_AGENT_SKIP_AX_FALLBACK message_id=%s sender=%s reason=mixed_mentions",
                    msg.id, author_name,
                )
            return

        direct_targets = await self._resolve_top_level_direct_targets(
            msg=msg,
            mentions=eligible_mentions,
        )
        if not direct_targets:
            if ax_fallback_allowed:
                await self._dispatch_to_ax(msg=msg, author_name=author_name, mentions=mentions or [])
            elif sender_is_agent and eligible_mentions:
                # Rule 3: agent→user mediation. The agent mentioned handles
                # that don't resolve to agents are likely user @mentions.
                # Route through aX so the concierge can mediate agent-to-user
                # communication (format as alert, filter noise, etc.) instead
                # of silently dropping the message.
                logger.info(
                    "TOP_LEVEL_INGRESS_AGENT_USER_MEDIATION message_id=%s sender=%s "
                    "unresolved_mentions=%s reason=agent_to_user_via_ax",
                    msg.id, author_name, eligible_mentions,
                )
                msg.message_metadata = msg.message_metadata or {}
                msg.message_metadata["agent_to_user_mediation"] = True
                await self._dispatch_to_ax(
                    msg=msg,
                    author_name=author_name,
                    mentions=mentions or [],
                )
            else:
                logger.info(
                    "TOP_LEVEL_INGRESS_AGENT_SKIP_AX_FALLBACK message_id=%s sender=%s reason=no_direct_targets",
                    msg.id, author_name,
                )
            return

        msg.message_metadata = msg.message_metadata or {}
        msg.message_metadata["top_level_ingress"] = False
        msg.message_metadata["original_mentions"] = mentions or []
        routing_meta = dict(msg.message_metadata.get("routing") or {})
        routing_meta["mode"] = "direct_mention"
        routing_meta.setdefault("hops", 0)
        routing_meta.setdefault("via", [])
        msg.message_metadata["routing"] = routing_meta
        msg.message_metadata["routing_story"] = {
            "targets": [
                {
                    "agent_id": str(agent.id),
                    "agent_name": agent.name,
                    "display_name": f"@{agent.name}",
                    "source": "direct_mention",
                    "type": "space_agent" if (agent.origin or "").lower() == "space_agent" else "agent",
                }
                for agent in direct_targets
            ],
            "route_inferred": False,
            "default_routed": False,
            "route_continuation": False,
            "continuation_source": None,
            "summary": [f"@{agent.name}" for agent in direct_targets],
        }
        await self._persist_message_metadata(msg)

        logger.info(
            "DIRECT_MENTION_BYPASS_AX message_id=%s targets=%s sender=%s",
            msg.id,
            [agent.name for agent in direct_targets],
            author_name,
        )

        dispatch_mentions = [agent.name for agent in direct_targets if self._requires_backend_dispatch(agent)]
        if dispatch_mentions:
            await self._dispatch_cloud_agents(
                msg=msg,
                author_name=author_name,
                mentions=dispatch_mentions,
                skip_ax_fallback=True,
            )

    async def _is_agent_disabled_cache(self, agent_id: str, space_id: str = None) -> bool:
        """Check Redis kill-switch for paused/disabled agents (checks both status and control service)."""
        if not self.redis:
            return False
        try:
            # 1. Check legacy/status cache (from update_agent)
            disabled_key = f"agent:{agent_id}:disabled"
            paused_key = f"agent:{agent_id}:paused"
            if await self.redis.exists(disabled_key, paused_key):
                return True

            # 2. Check AgentControlService keys (Global -> Org -> Agent)
            # Global
            global_key = "ax:agent-control:global"
            if await self._check_control_disabled(global_key):
                return True

            # Org (Workspace)
            if space_id:
                org_key = f"ax:agent-control:space:{space_id}"
                if await self._check_control_disabled(org_key):
                    return True

            # Agent
            agent_control_key = f"ax:agent-control:agent:{agent_id}"
            if await self._check_control_disabled(agent_control_key):
                return True

            return False
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                f"Redis kill-switch check failed for agent {agent_id}: {exc}"
            )
            return False

    async def _check_control_disabled(self, key: str) -> bool:
        """Helper to check if 'disabled' field is '1' in a control hash.

        Honors the ``disabled_until`` field: if the timestamp has passed,
        the break is expired, the key is auto-cleaned from Redis, and the
        agent is treated as not-disabled. Without this, every timed break
        leaves a stale ``disabled: '1'`` entry that blocks dispatch
        indefinitely after the break window closes.
        """
        try:
            val = await self.redis.hget(key, "disabled")
            if val != "1":
                return False
            # Check if this is a timed break that has expired
            until_raw = await self.redis.hget(key, "disabled_until")
            if until_raw:
                from datetime import datetime, timezone
                until_str = until_raw.decode() if isinstance(until_raw, bytes) else str(until_raw)
                try:
                    expiry = datetime.fromisoformat(until_str)
                    if expiry <= datetime.now(timezone.utc):
                        # Break expired — clear only the disable-related fields,
                        # NOT the entire hash. The same key may hold other active
                        # controls (no_reply, rate-limit overrides) that must not
                        # be silently dropped when a timed disable expires.
                        await self.redis.hdel(key, "disabled", "disabled_until", "reason")
                        import logging
                        logging.getLogger(__name__).info(
                            "CONTROL_BREAK_EXPIRED key=%s expired_at=%s — auto-cleaned",
                            key, until_str,
                        )
                        return False
                except (ValueError, TypeError):
                    pass  # unparseable timestamp — treat as indefinite disable
            return True
        except Exception:
            return False

    async def _should_throttle_agent_reply(
        self,
        session,
        *,
        agent_id,
        space_id,
        channel,
        window_seconds: int = 60,  # Deprecated, retained for backward compatibility
        user_id=None,
        sender_is_human: bool = False,
        tier: Optional[str] = None,
    ):
        """
        Enforce the per-user daily cloud agent cap to prevent runaway usage.
        """
        limiter = getattr(self, "cloud_agent_limiter", None)
        if not limiter:
            return False, None

        outcome = await limiter.check_and_increment(
            agent_id=str(agent_id),
            space_id=str(space_id),
            user_id=str(user_id) if user_id else None,
            sender_is_human=sender_is_human,
            tier=tier,
        )
        return outcome.blocked, outcome

    async def broadcast_sse(
        self, *, space_id: UUID, msg: Message, author_name: str, author_type: str | None = None
    ) -> None:
        """Broadcast message via both UI SSE and MCP Streams.

        Parity contract:
        - UI (users) and MCP (agents) must observe the same events.
        - We publish to the in-memory UI broker and to the MCP Redis Streams bus.
        - This keeps blocking waits and UI live views in sync.

        SECURITY: author_type is auto-detected from message fields when not
        explicitly provided. Messages with agent_id are always typed as "agent"
        to prevent impersonation in SSE events.
        """
        # Auto-detect author_type from message attribution (defense-in-depth)
        if author_type is None:
            author_type = "agent" if getattr(msg, "agent_id", None) else "user"
        import logging
        from app.core.system_agents import is_internal_agent_name
        logger = logging.getLogger(__name__)

        # Check if this is from an internal system agent (e.g., __ai_validator__)
        is_internal = is_internal_agent_name(author_name)

        # Identify sender type for debugging MCP vs user dispatch issues
        sender_debug = "agent" if getattr(msg, "agent_id", None) else "user" if getattr(msg, "user_id", None) else "unknown"
        logger.info(f"BROADCAST_SSE org={space_id} msg_id={msg.id} author={author_name} internal={is_internal} sender_type={sender_debug}")

        # Extract explicit @mentions from content.
        # Keep explicit mentions separate for SSE events — only explicit
        # @mentions should trigger SSE mention events (per SPEC-ROUTER-001).
        # Router-inferred mentions go to the dispatch path (aX) but NOT SSE.
        mentions = list(MentionsService.parse_mentions(msg.content or ''))
        explicit_mentions = list(mentions)  # snapshot before metadata merge
        seen_mentions = {m.lower() for m in mentions}

        metadata_mentions = msg.message_metadata.get("mentions", []) if msg.message_metadata else []
        metadata_agent_ids: list[UUID] = []
        metadata_handles: list[str] = []

        for m in metadata_mentions or []:
            if isinstance(m, dict):
                agent_id = m.get("agent_id")
                if agent_id:
                    try:
                        metadata_agent_ids.append(UUID(str(agent_id)))
                    except (ValueError, TypeError, AttributeError):
                        pass
                    continue
                handle = str(m.get("handle") or m.get("agent_name") or "").strip().lstrip("@").lower()
                if handle:
                    metadata_handles.append(handle)
                continue

            if isinstance(m, str):
                raw = m.strip()
                if not raw:
                    continue
                # Support both legacy string handles and string UUID ids
                try:
                    metadata_agent_ids.append(UUID(raw))
                    continue
                except (ValueError, TypeError):
                    pass

                handle = raw.lstrip("@").strip().lower()
                if handle:
                    metadata_handles.append(handle)

        if metadata_agent_ids:
            try:
                res = await self.db.execute(
                    select(Agent.name).where(Agent.id.in_(metadata_agent_ids))
                )
                for name in res.scalars().all():
                    handle = (name or "").strip().lower()
                    if handle:
                        metadata_handles.append(handle)
            except Exception as e:
                logger.warning(f"🔀 INFERRED ROUTE DISPATCH: Failed to resolve metadata agent ids: {e}")

        merged_from_metadata: list[str] = []
        for handle in metadata_handles:
            if handle and handle not in seen_mentions:
                seen_mentions.add(handle)
                mentions.append(handle)
                merged_from_metadata.append(handle)

        if merged_from_metadata:
            logger.info(
                "🔀 INFERRED ROUTE DISPATCH: merged_metadata_mentions=%s total_mentions=%s",
                merged_from_metadata,
                mentions,
            )

        # ── Kill switch filter: remove disabled agents from mention targets ──
        #
        # Universal enforcement point. Any agent whose AgentControlService state
        # is disabled (UI Break button, MCP `agents.set_control`, global kill
        # switch) must not receive mention events via ANY transport — SSE,
        # MCP bus, dispatch loop, webhook, or anything else that publishes
        # below. Filtering here makes the backend the single source of truth
        # for "is this agent routable right now?" instead of every client
        # re-implementing its own check (see feedback:
        # kill_switch_enforcement_belongs_on_api).
        #
        # The filter runs against both `mentions` (all targets, content +
        # metadata) and `explicit_mentions` (content-only snapshot used
        # for SSE `mention` events per SPEC-ROUTER-001).
        if mentions and self.agent_control_service is not None:
            resolved_agent_names: set[str] = set()
            try:
                mention_lower = [m.lower() for m in mentions]
                from app.core.agent_space import agents_in_space_subquery
                result = await self.db.execute(
                    select(Agent.id, Agent.name).where(
                        Agent.id.in_(agents_in_space_subquery(msg.space_id)),
                        func.lower(Agent.name).in_(mention_lower),
                    )
                )
                resolved_mention_agents = list(result.all())
                resolved_agent_names = {
                    (agent_row_name or "").strip().lower()
                    for _, agent_row_name in resolved_mention_agents
                    if (agent_row_name or "").strip()
                }
                if resolved_mention_agents:
                    batch_input = [
                        (agent_row_id, msg.space_id, (agent_row_name or "").strip().lower() or None)
                        for agent_row_id, agent_row_name in resolved_mention_agents
                    ]
                    control_states = await self.agent_control_service.get_control_states_batch(batch_input)
                    disabled_names: set[str] = set()
                    for agent_row_id, agent_row_name in resolved_mention_agents:
                        state = control_states.get(agent_row_id)
                        if state is not None and state.is_disabled:
                            disabled_names.add((agent_row_name or "").lower())
                    if disabled_names:
                        before_mentions = list(mentions)
                        before_explicit = list(explicit_mentions)
                        mentions = [m for m in mentions if m.lower() not in disabled_names]
                        explicit_mentions = [m for m in explicit_mentions if m.lower() not in disabled_names]
                        logger.info(
                            "🛑 KILL SWITCH FILTER: removed %d disabled agent(s) from mention targets "
                            "(msg_id=%s, disabled=%s, before=%s, after=%s)",
                            len(before_mentions) - len(mentions),
                            msg.id,
                            sorted(disabled_names),
                            before_mentions,
                            mentions,
                        )
            except Exception as exc:
                # SAFETY CRITICAL: never publish agent wake targets when pause /
                # kill-switch state cannot be checked. Chat still publishes as a
                # normal message. When agent names were resolved before the
                # control-plane failure, strip only those agent wake targets so
                # ordinary user mentions can still receive non-agent handling
                # such as email notifications. If agent-name resolution itself
                # failed, fall back to clearing all mention targets.
                before_mentions = list(mentions)
                before_explicit = list(explicit_mentions)
                if resolved_agent_names:
                    mentions = [m for m in mentions if m.lower() not in resolved_agent_names]
                    explicit_mentions = [m for m in explicit_mentions if m.lower() not in resolved_agent_names]
                else:
                    mentions = []
                    explicit_mentions = []
                logger.error(
                    "🛑 KILL SWITCH FILTER: lookup failed, FAILING CLOSED — "
                    "cleared agent mention targets (msg_id=%s, agent_targets=%s, before=%s, after=%s, explicit_before=%s): %s",
                    msg.id,
                    sorted(resolved_agent_names),
                    before_mentions,
                    mentions,
                    before_explicit,
                    exc,
                )

        logger.info(f"BROADCAST_SSE msg_id={msg.id} mentions={mentions} will_dispatch={bool(mentions)}")

        actor_roster_id = None
        if getattr(msg, "agent_id", None):
            actor_roster_id = str(msg.agent_id)
        elif getattr(msg, "user_id", None):
            actor_roster_id = str(msg.user_id)

        attachment_refs = attachment_event_refs_from_message_metadata(msg.message_metadata)
        event_metadata = {"attachments": attachment_refs} if attachment_refs else {}

        # 1) UI SSE (Redis Streams broker) — cross-process event delivery
        # DEFENSE IN DEPTH: Also skip UI SSE for internal agents
        # Internal system agents like __ai_validator__ should be invisible to all consumers
        if not is_internal:
            await self.sse.publish(
                space_id=str(space_id),
                event="message",
                data={
                    "id": str(msg.id),
                    "content": msg.content,
                    "author": {
                        "id": str(msg.agent_id) if getattr(msg, "agent_id", None) else str(msg.user_id) if getattr(msg, "user_id", None) else "",
                        "name": author_name,
                        "type": author_type,
                    },
                    "author_id": str(msg.agent_id) if getattr(msg, "agent_id", None) else str(msg.user_id) if getattr(msg, "user_id", None) else None,
                    "author_type": author_type,
                    "sender_type": author_type,  # match REST API field name so frontend pending-bubble clearing works
                    "agent_id": str(msg.agent_id) if getattr(msg, "agent_id", None) else None,
                    "parent_id": str(msg.parent_id) if getattr(msg, "parent_id", None) else None,
                    "space_id": str(space_id),
                    "timestamp": msg.created_at.isoformat() if hasattr(msg, "created_at") and msg.created_at else None,
                    "actor_roster_id": actor_roster_id,
                    "mentions": mentions,
                    "channel": msg.channel,
                    "attachments": attachment_refs,
                    "metadata": event_metadata,
                },
            )
            # SSE mention events: ONLY for explicit @mentions in content.
            # Router-inferred mentions (from metadata) go through dispatch
            # to aX but do NOT generate SSE mention events. This prevents
            # SSE sentinels from responding to messages they weren't
            # explicitly @mentioned in (cascade loop prevention).
            sse_mention_targets = explicit_mentions if explicit_mentions else []
            for mentioned_agent in sse_mention_targets:
                await self.sse.publish(
                    space_id=str(space_id),
                    event="mention",
                    data={
                        "id": str(msg.id),
                        "content": msg.content,
                        "author": author_name,
                        "author_type": "agent" if getattr(msg, "agent_id", None) else "user",
                        "agent_id": str(msg.agent_id) if getattr(msg, "agent_id", None) else None,
                        "sender_name": author_name,  # Alias for compatibility
                        "mentioned_agent": mentioned_agent,
                        "mentions": [mentioned_agent],  # Array format for SSE client wait_mode=mentions
                        "actor_roster_id": actor_roster_id,
                        "space_id": str(space_id),
                        "timestamp": msg.created_at.isoformat() if hasattr(msg, "created_at") and msg.created_at else None,
                        "channel": msg.channel,
                        "metadata": msg.message_metadata or {},
                    },
                )
        else:
            logger.debug(f"🔕 UI SSE skipped for internal agent: {author_name}")

        # 2) MCP Redis Streams bus — publish the same events so blocking wait sees them
        # SKIP for internal agents (e.g., __ai_validator__) - their actions should not trigger agent waits
        if not is_internal:
            try:
                from app.services.mcp_event_publishers import (
                    publish_message_event,
                    publish_mention_event,
                )
                # Publish main message event
                await publish_message_event(
                    space_id=str(space_id),
                    message_id=str(msg.id),
                    content=msg.content or "",
                    sender_name=author_name,
                    created_at=msg.created_at.isoformat() if getattr(msg, "created_at", None) else None,
                    actor_roster_id=actor_roster_id,
                    parent_id=str(msg.parent_id) if getattr(msg, "parent_id", None) else None,
                    attachments=attachment_refs,
                )
                # Publish mention events for each mentioned agent
                for mentioned_agent in mentions:
                    await publish_mention_event(
                        space_id=str(space_id),
                        message_id=str(msg.id),
                        content=msg.content or "",
                        mentions=[mentioned_agent],
                        sender_name=author_name,
                        created_at=msg.created_at.isoformat() if getattr(msg, "created_at", None) else None,
                        actor_roster_id=actor_roster_id,
                    )
            except Exception as e:
                # Do not fail the request if MCP bus is unavailable
                import logging
                logging.getLogger(__name__).warning(f"Failed to publish to MCP bus: {e}")
        else:
            logger.debug(f"🔕 MCP bus skipped for internal agent: {author_name}")

        # --- aX Orchestrator: Ingress classification ---
        # Layer 0: Internal system agents (e.g. __ai_validator__) NEVER enter dispatch
        if is_internal:
            logger.debug(f"🔕 Dispatch skipped for internal agent: {author_name}")
            return

        # Layer 1: aX self-loop guard — messages FROM aX never re-enter aX
        ax_is_sender = False
        if msg.agent_id:
            org_ax_id = await self._get_org_space_agent_id(msg.space_id)
            if org_ax_id and str(msg.agent_id) == str(org_ax_id):
                ax_is_sender = True

        if ax_is_sender:
            # aX sent this — internal relay, dispatch to @mentioned agents only
            # skip_ax_fallback=True prevents unresolved mentions from looping back to aX
            if mentions:
                # Loop protection: check routing hops before dispatching
                routing = (msg.message_metadata or {}).get("routing", {})
                hops = routing.get("hops", 0)
                if hops >= MAX_ROUTING_HOPS:
                    logger.warning(
                        "ROUTING_LOOP_PREVENTED message_id=%s hops=%d via=%s mentions=%s",
                        msg.id, hops, routing.get("via", []), mentions,
                    )
                else:
                    task = asyncio.create_task(self._dispatch_cloud_agents(
                        msg=msg, author_name=author_name, mentions=mentions,
                        skip_ax_fallback=True,
                    ))
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
        else:
            is_top_level = await self._is_top_level_ingress(msg)

            # Fire fast summarizer in parallel with aX dispatch (independent)
            if msg.content and len(msg.content.strip()) >= 40:
                summary_task = asyncio.create_task(
                    summarize_and_store(str(msg.id), str(msg.space_id), msg.content)
                )
                _background_tasks.add(summary_task)
                summary_task.add_done_callback(_background_tasks.discard)

            if is_top_level:
                task = asyncio.create_task(self._dispatch_top_level_ingress(
                    msg=msg, author_name=author_name, mentions=mentions or [],
                ))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            elif msg.parent_id:
                task = asyncio.create_task(self._dispatch_reply_target(
                    msg=msg,
                    author_name=author_name,
                ))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
            elif mentions:
                # Internal relay with @mentions → dispatch to mentioned agents directly
                # This is traffic aX already routed — no re-ingress
                routing = (msg.message_metadata or {}).get("routing", {})
                hops = routing.get("hops", 0)
                if hops >= MAX_ROUTING_HOPS:
                    logger.warning(
                        "ROUTING_LOOP_PREVENTED message_id=%s hops=%d via=%s mentions=%s",
                        msg.id, hops, routing.get("via", []), mentions,
                    )
                else:
                    task = asyncio.create_task(self._dispatch_cloud_agents(
                        msg=msg, author_name=author_name, mentions=mentions,
                    ))
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)

            # 2b) Email notifications for user @mentions
            # If a user (not agent) is @mentioned, send them an email
            # Uses its own session to avoid sharing AsyncSession across coroutines
            try:
                from app.services.email_mention_hook import check_user_mentions_and_notify
                from app.core.database import AsyncSessionLocal

                _email_mentions = list(mentions)  # snapshot
                _email_content = msg.content or ''
                _email_org_id = space_id
                _email_msg_id = msg.id
                _email_author = author_name

                async def _run_email_notifications():
                    async with AsyncSessionLocal() as email_db:
                        await check_user_mentions_and_notify(
                            db=email_db,
                            redis=self.redis,
                            mentions=_email_mentions,
                            author_name=_email_author,
                            message_content=_email_content,
                            space_id=_email_org_id,
                            space_name='',
                            message_id=_email_msg_id,
                        )

                email_task = asyncio.create_task(_run_email_notifications())
                _background_tasks.add(email_task)
                email_task.add_done_callback(_background_tasks.discard)
            except Exception as e:
                logger.warning(f'Email notification hook failed: {e}')

        # 3) Reaction Events (Real-time Reputation)
        # If this is a reaction, broadcast a specific event so UI can update reputation chips
        if msg.message_type == "reaction" and msg.parent_id:
            try:
                # We need to find the author of the PARENT message to know who got the reaction
                # We can't trust msg.agent_id/user_id because that's the reactor, not the recipient
                parent_query = select(Message).where(Message.id == msg.parent_id)
                parent_result = await self.db.execute(parent_query)
                parent = parent_result.scalar_one_or_none()

                if parent and parent.agent_id:
                    target_agent_id = str(parent.agent_id)
                    emoji = msg.content.strip()

                    # Count reactions with this emoji for messages by this agent
                    from sqlalchemy.orm import aliased
                    ParentMessage = aliased(Message)

                    stmt = select(func.count(Message.id)).join(
                        ParentMessage, Message.parent_id == ParentMessage.id
                    ).where(
                        and_(
                            ParentMessage.agent_id == parent.agent_id,
                            Message.content == emoji,
                            or_(
                                Message.message_type == "reaction",
                                func.coalesce(Message.message_metadata["is_reaction"].astext, "false") == "true"
                            )
                        )
                    )

                    count_res = await self.db.execute(stmt)
                    new_count = count_res.scalar() or 0

                    # Broadcast reaction_added event
                    await self.sse.publish(
                        space_id=str(space_id),
                        event="reaction_added",
                        data={
                            "agent_id": target_agent_id,
                            "emoji": emoji,
                            "parent_message_id": str(msg.parent_id),
                            "new_count": new_count,
                            "reactor_id": str(msg.user_id or msg.agent_id),
                            "reactor_name": author_name
                        }
                    )

                    # Also publish to MCP bus if needed (optional for now)

            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to broadcast reaction event: {e}")

    async def mark_unread_for_org(
        self, *, space_id: UUID, sender_user_id: UUID, message_id: UUID
    ) -> int:
        """Mark message as unread for all org members except sender."""
        # Get users attached directly to org
        org_users = await self.db.execute(
            select(User.id).where(
                or_(User.space_id == space_id, User.current_space_id == space_id)
            )
        )
        org_member_ids = {str(uid) for uid in org_users.scalars().all()}

        # Get users via membership table
        memberships = await self.db.execute(
            select(SpaceMembership.user_id).where(
                SpaceMembership.space_id == space_id
            )
        )
        membership_ids = {str(uid) for uid in memberships.scalars().all()}

        # Combine all recipients, excluding sender
        recipients = (org_member_ids | membership_ids) - {str(sender_user_id)}
        if not recipients:
            return 0

        # Batch update Redis unread sets
        pipe = self.redis.pipeline()
        for recipient_id in recipients:
            key = f"unread:{recipient_id}"
            pipe.sadd(key, str(message_id))
            pipe.expire(key, 7 * 24 * 3600)  # 7 day TTL

        with self._tracer.start_as_current_span(
            "messages.unread_fanout",
            attributes={
                "ax.space_id": str(space_id),
                "ax.sender_user_id": str(sender_user_id),
                "ax.message_id": str(message_id),
                "ax.recipient_count": len(recipients),
            },
        ) as span:
            span.set_attribute("ax.recipient_count", len(recipients))
            await pipe.execute()

        return len(recipients)

    async def _is_top_level_ingress(self, msg: Message) -> bool:
        """
        Classify whether a message is top-level ingress (should go through aX)
        or internal relay traffic (should NOT re-enter aX).

        Top-level ingress:
          - New parentless human UI messages
          - Parentless agent-authored messages with no relay context
          - Messages explicitly marked top_level_ingress=true

        Internal relay:
          - Any reply/follow-up (parent_id set) by default
          - Agent-authored replies/follow-ups by default
          - Messages with routed_by_ax=true in metadata
          - Messages with ax_work_id in metadata (part of aX-managed work)
          - Messages explicitly marked top_level_ingress=false
          - Messages whose parent has relay context (inherited)
        """
        metadata = msg.message_metadata or {}

        # Explicit markers take priority
        routing = metadata.get("routing", {})
        routing_mode = routing.get("mode")
        if routing_mode in {"ax_relay", "direct_mention", "reply_target"}:
            return False  # relay dispatch handles this separately
        # Backward compat: legacy routed_by_ax flag
        if metadata.get("routed_by_ax"):
            return False
        if metadata.get("ax_work_id"):
            return False
        if metadata.get("top_level_ingress") is True:
            return True
        if metadata.get("top_level_ingress") is False:
            return False

        # Replies target the addressed thread participant directly and never
        # re-enter concierge routing.
        if msg.parent_id:
            parent_meta = await self._get_parent_relay_context(msg.parent_id)
            msg.message_metadata = msg.message_metadata or {}
            if parent_meta and parent_meta.get("ax_work_id"):
                msg.message_metadata["ax_work_id"] = parent_meta.get("ax_work_id")
            msg.message_metadata["top_level_ingress"] = False
            return False

        # New parentless human messages are top-level ingress.
        if msg.user_id and not msg.agent_id:
            return True

        # Parentless agent-authored messages are treated like fresh ingress:
        # direct mentions bypass to the target, and missing/unresolved mentions
        # fall through to aX. Replies/follow-ups stay internal by default.
        if msg.agent_id:
            return True

        return False

    async def _resolve_reply_target(self, msg: Message) -> tuple[Message | None, str | None]:
        """Resolve the directly addressed parent author for a reply."""
        if not msg.parent_id:
            return None, None

        from ..core.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as session:
                parent = await session.get(Message, msg.parent_id)
                if not parent or parent.space_id != msg.space_id:
                    return None, None

                if parent.agent_id:
                    agent = await session.get(Agent, parent.agent_id)
                    if not agent or (agent.status or "").lower() != "active":
                        return parent, None
                    return parent, agent.name

                return parent, None
        except Exception as e:
            logger.warning("Failed to resolve reply target for %s: %s", msg.id, e)
            return None, None

    async def _dispatch_reply_target(self, *, msg: Message, author_name: str) -> None:
        """Dispatch a reply directly to the parent author, bypassing concierge."""
        parent, target_agent_name = await self._resolve_reply_target(msg)
        if not parent:
            return

        msg.message_metadata = msg.message_metadata or {}
        msg.message_metadata["top_level_ingress"] = False
        routing_meta = dict(msg.message_metadata.get("routing") or {})
        routing_meta["mode"] = "reply_target"
        routing_meta.setdefault("hops", 0)
        routing_meta.setdefault("via", [])
        msg.message_metadata["routing"] = routing_meta
        msg.message_metadata["routing_story"] = {
            "targets": [
                {
                    "agent_id": str(parent.agent_id),
                    "agent_name": target_agent_name,
                    "display_name": f"@{target_agent_name}",
                    "source": "reply_target",
                    "type": "space_agent" if target_agent_name == "aX" else "agent",
                }
            ] if target_agent_name and parent.agent_id else [],
            "route_inferred": False,
            "default_routed": False,
            "route_continuation": True,
            "continuation_source": "parent_id",
            "summary": [f"@{target_agent_name}"] if target_agent_name else [],
        }
        await self._persist_message_metadata(msg)

        if not target_agent_name:
            logger.info(
                "REPLY_TARGET_USER_ONLY message_id=%s parent_id=%s sender=%s",
                msg.id,
                msg.parent_id,
                author_name,
            )
            return

        if str(parent.agent_id) == str(msg.agent_id):
            logger.info(
                "REPLY_TARGET_SELF_SKIP message_id=%s parent_id=%s agent=%s",
                msg.id,
                msg.parent_id,
                target_agent_name,
            )
            return

        logger.info(
            "REPLY_TARGET_DISPATCH message_id=%s parent_id=%s target=%s sender=%s",
            msg.id,
            msg.parent_id,
            target_agent_name,
            author_name,
        )
        await self._dispatch_cloud_agents(
            msg=msg,
            author_name=author_name,
            mentions=[target_agent_name],
            skip_ax_fallback=True,
        )

    async def _get_parent_relay_context(self, parent_id) -> dict | None:
        """Look up parent message metadata for relay context markers."""
        from ..core.database import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Message.message_metadata).where(Message.id == parent_id)
                )
                parent_meta = result.scalar_one_or_none()
                if parent_meta and (
                    parent_meta.get("routed_by_ax")
                    or parent_meta.get("ax_work_id")
                    or parent_meta.get("routing", {}).get("mode") in ("ax_relay", "direct_mention")
                ):
                    return parent_meta
        except Exception as e:
            logger.warning("Failed to look up parent relay context for %s: %s", parent_id, e)
        return None

    async def _get_org_space_agent_id(self, space_id) -> str | None:
        """Get the agent ID of the org's Space Agent (aX) for loop detection."""
        from ..core.database import AsyncSessionLocal
        from ..models.space import Space
        from ..services.space_agent_service import ensure_space_agent_for_org
        try:
            async with AsyncSessionLocal() as session:
                org = await session.get(Space, space_id)
                if not org:
                    return None
                ax = await ensure_space_agent_for_org(session, org)
                if not ax or ax.status != "active":
                    return None
                await session.commit()
                return str(ax.id)
        except Exception as e:
            logger.warning("Failed to look up space agent ID for org %s: %s", space_id, e)
            return None

    async def _dispatch_to_ax(
        self,
        *,
        msg: Message,
        author_name: str,
        mentions: list[str] | None = None,
    ) -> None:
        """
        Dispatch to the org's Space Agent (aX) for orchestration.
        All top-level ingress routes through aX. @mentions are passed as
        intent hints so aX can decide whether to handle or delegate.
        """
        import logging
        logger = logging.getLogger(__name__)

        from ..core.database import AsyncSessionLocal
        from ..models.space import Space
        from ..services.space_agent_service import ensure_space_agent_for_org

        try:
            async with AsyncSessionLocal() as session:
                # Find the org's Space Agent
                org = await session.get(Space, msg.space_id)
                ax = await ensure_space_agent_for_org(session, org) if org else None
                ax_name = ax.name if ax and ax.status == "active" else None
                await session.commit()
                if not ax_name:
                    # No aX agent — fall back to direct dispatch if mentions exist
                    if mentions:
                        logger.info(
                            "AX_FALLBACK_DIRECT message_id=%s reason=no_active_space_agent mentions=%s",
                            msg.id, mentions,
                        )
                        await self._dispatch_cloud_agents(
                            msg=msg, author_name=author_name, mentions=mentions,
                        )
                    else:
                        logger.info(
                            "AX_AUTO_DISPATCH_SKIP message_id=%s reason=no_active_space_agent",
                            msg.id,
                        )
                    return

            # Mark intent metadata on the message
            msg.message_metadata = msg.message_metadata or {}
            msg.message_metadata["top_level_ingress"] = True
            if mentions:
                msg.message_metadata["original_mentions"] = mentions

            # NOTE: Widget metadata is NOT stamped at ingress time.
            # Widgets only appear when aX's downstream tools produce a real
            # display surface (task board, agent list, etc.).
            # The _update_widget_lifecycle() helper in dispatch_executor.py
            # handles widget emission when tool results warrant it.

            # Persist metadata to DB (top_level_ingress + mentions)
            async with AsyncSessionLocal() as meta_session:
                from ..models.message import Message as MsgModel
                from sqlalchemy.orm.attributes import flag_modified
                db_msg = await meta_session.get(MsgModel, msg.id)
                if db_msg:
                    db_msg.message_metadata = msg.message_metadata
                    flag_modified(db_msg, "message_metadata")
                    await meta_session.commit()

            logger.info(
                "AX_ORCHESTRATOR_DISPATCH message_id=%s agent=%s sender=%s mentions=%s",
                msg.id, ax_name, author_name, mentions or [],
            )
            # Dispatch to aX via the standard pipeline
            await self._dispatch_cloud_agents(
                msg=msg,
                author_name=author_name,
                mentions=[ax_name],
            )

        except Exception:
            logger.exception("AX_ORCHESTRATOR_DISPATCH_ERROR message_id=%s", msg.id)

    async def _dispatch_cloud_agents(
        self,
        *,
        msg: Message,
        author_name: str,
        mentions: List[str],
        skip_ax_fallback: bool = False,
    ) -> None:
        """
        Check if any mentioned agents are Cloud Agents and dispatch the message to them.
        """
        import logging
        logger = logging.getLogger(__name__)
        # Debug: identify sender type to trace MCP vs user dispatch issues
        sender_type_debug = "agent" if msg.agent_id else "user" if msg.user_id else "unknown"
        logger.info(f"DISPATCH_CLOUD_AGENTS msg_id={msg.id} mentions={mentions} sender_type={sender_type_debug} msg_agent_id={msg.agent_id}")

        if not mentions:
            return

        # SAFETY: Global kill switch
        if os.environ.get("ENABLE_CLOUD_AGENTS", "true").lower() not in ("true", "1", "yes"):
            logger.warning("Cloud agents are DISABLED via ENABLE_CLOUD_AGENTS env var")
            return

        # SAFETY: Prevent agent-to-agent loops
        # We now rely on:
        # 1. _strip_self_mention (prevents direct loops)
        # 2. Rate limiting (prevents runaway conversations)
        # 3. Circuit breaker (future)
        # if msg.agent_id:
        #    logger.warning(f"SAFETY: Blocked cloud agent dispatch - sender {author_name} is an agent (prevents loops)")
        #    return

        # Create a new session for the background task
        from sqlalchemy import select, and_, func, or_
        from ..core.database import AsyncSessionLocal
        from ..core.actor import Actor, CAP_MESSAGES_SEND
        from .messages_service import MessagesService
        from app.services.sse_broker import org_event_broker

        # Fallback for sanitization
        try:
            # from mcp_modular.tools.message_helpers.sanitization import sanitize_self_mentions
            # content = sanitize_self_mentions(content, agent_name)
            pass
        except ImportError:
            pass

        try:
            async with AsyncSessionLocal() as session:
                # Background dispatch is system-authored. Set the RLS bypass
                # context before resolving dispatchable agents, otherwise prod
                # RLS can hide the space agent lookup entirely.
                await session.execute(text("SELECT set_config('app.is_privileged', 'true', true)"))

                # Resolve agents that have a cloud_function_url
                # We match by name (case-insensitive) and org context

                # Normalize mentions
                normalized_mentions = [m.lower().lstrip("@").strip() for m in mentions if m]

                # Optional alias map for human shorthand -> canonical agent name.
                # Example: {"clawd":"clawdbot_cipher"}
                alias_map: dict[str, str] = {}
                alias_map_raw = os.environ.get("MENTION_ALIAS_MAP_JSON", "")
                if alias_map_raw:
                    try:
                        loaded = json.loads(alias_map_raw)
                        if isinstance(loaded, dict):
                            alias_map = {
                                str(k).lower().lstrip("@").strip(): str(v).lower().lstrip("@").strip()
                                for k, v in loaded.items()
                                if k and v
                            }
                    except Exception as alias_err:
                        logger.warning("Invalid MENTION_ALIAS_MAP_JSON: %s", alias_err)

                resolved_mentions = [alias_map.get(m, m) for m in normalized_mentions]
                if resolved_mentions != normalized_mentions:
                    logger.info(
                        "MENTION_ALIAS_RESOLVED original=%s resolved=%s",
                        normalized_mentions,
                        resolved_mentions,
                    )
                normalized_mentions = list(dict.fromkeys(resolved_mentions))

                # SECURITY: Cloud agents must respect space boundaries
                # Only dispatch if agent is in THIS org, pinned to THIS org, or is GLOBAL
                # Global agents (like ax-guide) can be mentioned from any space
                # DO NOT allow owner access across orgs - that breaks multi-tenant isolation
                # Dispatch targets:
                # 1. Cloud agents: have cloud_function_url (dispatch to agent_runner)
                # 2. External gateway agents: have webhook_url AND verified (dispatch via webhook)
                # 3. Space agents: origin='space_agent' (dispatch to local container)
                # 4. AgentCore agents: origin='agentcore' (dispatch via Bedrock)
                from app.models.agent_space_access import AgentSpaceAccess
                base_filters = and_(
                    # Must be dispatchable
                    or_(
                        Agent.cloud_function_url.isnot(None),  # Cloud agents
                        and_(  # External gateway agents (Moltbot, etc.)
                            Agent.origin == 'external_gateway',
                            Agent.webhook_url.isnot(None),
                        ),
                        Agent.origin == 'space_agent',  # Local Space Agent container
                        Agent.origin == 'agentcore',  # Bedrock AgentCore
                    ),
                    Agent.status == 'active',  # CRITICAL: Only dispatch to active agents (prevents runaway costs)
                    or_(
                        AgentSpaceAccess.space_id == msg.space_id,  # Agent has access to this space
                        Agent.visibility_level == 'global',  # Global agents available everywhere
                    ),
                    AgentSpaceAccess.state == 'active',  # Skip suspended/detached space access records
                )

                query = select(Agent).join(
                    AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id
                ).where(
                    and_(
                        func.lower(Agent.name).in_(normalized_mentions),
                        base_filters,
                    )
                ).execution_options(populate_existing=True, autoflush=True, autocommit=False, no_cache=True)

                logger.info(f"🔍 PRE-QUERY cloud agents: mentions={normalized_mentions}")
                result = await session.execute(query)
                cloud_agents = result.scalars().all()

                # Fallback: if exact mention lookup misses, try deterministic prefix match.
                # This prevents silent drops for common shorthand/typos (e.g., @clawd -> clawdbot_cipher).
                if not cloud_agents and normalized_mentions:
                    prefix_conditions = [func.lower(Agent.name).like(f"{m}%") for m in normalized_mentions if len(m) >= 3]
                    if prefix_conditions:
                        prefix_query = select(Agent).join(
                            AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id
                        ).where(
                            and_(
                                or_(*prefix_conditions),
                                base_filters,
                            )
                        ).limit(5)
                        prefix_result = await session.execute(prefix_query)
                        prefix_candidates = prefix_result.scalars().all()
                        if len(prefix_candidates) == 1:
                            cloud_agents = prefix_candidates
                            logger.warning(
                                "MENTION_FALLBACK_PREFIX message_id=%s mentions=%s matched=%s",
                                msg.id,
                                normalized_mentions,
                                [a.name for a in cloud_agents],
                            )

                if not cloud_agents and normalized_mentions:
                    cloud_agents = await self._lookup_explicit_space_agent_mentions(
                        session=session,
                        msg=msg,
                        normalized_mentions=normalized_mentions,
                    )
                    if cloud_agents:
                        logger.info(
                            "MENTION_FALLBACK_SPACE_AGENT message_id=%s mentions=%s matched=%s",
                            msg.id,
                            normalized_mentions,
                            [a.name for a in cloud_agents],
                        )

                logger.info(
                    f"🔍 POST-QUERY: Found {len(cloud_agents)} agents: "
                    f"{[(ca.name, ca.status, ca.origin, bool(ca.cloud_function_url), bool(ca.webhook_url)) for ca in cloud_agents]}"
                )

                tier = await self._get_org_tier(session, msg.space_id)

                if not cloud_agents:
                    if skip_ax_fallback:
                        # Caller explicitly opted out of aX fallback (e.g. aX is the sender)
                        logger.info(
                            "DISPATCH_NO_MATCH_SKIP_AX_FALLBACK message_id=%s mentions=%s",
                            msg.id, normalized_mentions,
                        )
                        return

                    # Agent-authored messages should not bounce unresolved
                    # mentions back into aX by default. That creates feedback
                    # loops when specialist agents talk to each other.
                    if msg.agent_id:
                        logger.info(
                            "DISPATCH_NO_MATCH_AGENT_SKIP message_id=%s mentions=%s sender=%s",
                            msg.id,
                            normalized_mentions,
                            author_name,
                        )
                        return

                    # No agents matched the @mentions from a human sender —
                    # fall back to aX so it can inform the user ("did you mean
                    # @code_weaver?") or intelligently handle the request itself.
                    from app.core.agent_space import agents_in_space_subquery
                    ax_result = await session.execute(
                        select(Agent).where(
                            Agent.id.in_(agents_in_space_subquery(msg.space_id)),
                            Agent.origin == "space_agent",
                            Agent.status == "active",
                        ).limit(1)
                    )
                    ax_agent = ax_result.scalar_one_or_none()
                    if ax_agent:
                        logger.info(
                            "DISPATCH_NO_MATCH_TO_AX message_id=%s mentions=%s "
                            "routing_to=%s",
                            msg.id, normalized_mentions, ax_agent.name,
                        )
                        cloud_agents = [ax_agent]
                        # Carry the unresolved mentions so aX can suggest corrections
                        msg.message_metadata = msg.message_metadata or {}
                        msg.message_metadata["unresolved_mentions"] = normalized_mentions
                    else:
                        logger.warning(
                            "DISPATCH_NO_MATCH message_id=%s mentions=%s sender=%s",
                            msg.id,
                            normalized_mentions,
                            author_name,
                        )
                        return

                logger.info(f"Dispatching to {len(cloud_agents)} Cloud Agents: {[a.name for a in cloud_agents]}")

                # Fetch recent history (configurable via AGENT_HISTORY_LIMIT env var)
                # Exclude messages from internal system agents (e.g., __ai_validator__)
                # AND messages containing internal agent names (contaminated responses)
                # to prevent Gemini from mimicking system agent names/patterns
                from app.core.config import get_settings
                history_settings = get_settings()
                history_limit = history_settings.agent_history_limit  # default 100

                internal_agent_ids_subq = select(Agent.id).where(Agent.is_internal.is_(True))
                history_query = select(Message).where(
                    and_(
                        Message.space_id == msg.space_id,
                        Message.channel == msg.channel,
                        Message.created_at < msg.created_at,
                        exclude_ui_only_no_reply_clause(),
                        # Exclude internal system agent messages
                        or_(
                            Message.agent_id.is_(None),
                            Message.agent_id.notin_(internal_agent_ids_subq)
                        ),
                        # Exclude messages that mention internal agent names (contaminated history)
                        ~Message.content.ilike('%__ai_validator__%'),
                        ~Message.content.ilike('%ai_validator%'),
                    )
                ).order_by(Message.created_at.desc()).limit(history_limit)

                history_res = await session.execute(history_query)
                history_msgs = history_res.scalars().all()
                # Reverse to chronological
                history_msgs = list(reversed(history_msgs))

                formatted_history = []
                for h_msg in history_msgs:
                    role = "user"
                    sender_name = "Unknown"

                    # Get the sender's name (agent or user)
                    if h_msg.agent_id:
                        role = "assistant"
                        # Query agent name
                        agent_query = select(Agent.name).where(Agent.id == h_msg.agent_id)
                        agent_result = await session.execute(agent_query)
                        agent_row = agent_result.scalar_one_or_none()
                        sender_name = agent_row if agent_row else f"agent_{h_msg.agent_id}"
                    elif h_msg.user_id:
                        # Query user name
                        user_query = select(User.username).where(User.id == h_msg.user_id)
                        user_result = await session.execute(user_query)
                        user_row = user_result.scalar_one_or_none()
                        sender_name = user_row if user_row else f"user_{h_msg.user_id}"

                    # Strip ALL @mentions from history to prevent Gemini from learning the pattern
                    # Gemini was seeing "@quick_titan_363 hey" and mimicking the @mention style
                    clean_content = re.sub(r'@[\w_]+', '', h_msg.content).strip()

                    # CRITICAL FIX: Prepend sender name so Gemini knows WHO sent each message
                    # This prevents the agent from thinking all messages are from themselves
                    formatted_content = f"{sender_name}: {clean_content}"

                    formatted_history.append({
                        "role": role,
                        "parts": [formatted_content]
                    })

                # Build messages list with IDs for context_data (for reactions, replies, etc.)
                # This allows cloud agents to reference specific messages by ID
                recent_msgs = history_msgs[-20:]  # Last 20 messages with IDs

                # Batch fetch agent and user names to avoid N+1 queries
                agent_ids = {h.agent_id for h in recent_msgs if h.agent_id}
                user_ids = {h.user_id for h in recent_msgs if h.user_id}

                agent_name_map = {}
                if agent_ids:
                    agent_query = select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
                    agent_result = await session.execute(agent_query)
                    agent_name_map = {row.id: row.name for row in agent_result.all()}

                user_name_map = {}
                if user_ids:
                    user_query = select(User.id, User.username).where(User.id.in_(user_ids))
                    user_result = await session.execute(user_query)
                    user_name_map = {row.id: row.username for row in user_result.all()}

                # Batch fetch intelligence scores for context enrichment
                from ..models.message_intelligence import MessageIntelligence
                intel_map = {}
                msg_ids_for_intel = [h.id for h in recent_msgs]
                if msg_ids_for_intel:
                    try:
                        intel_result = await session.execute(
                            select(
                                MessageIntelligence.message_id,
                                MessageIntelligence.quality_score,
                                MessageIntelligence.spam_score,
                            ).where(MessageIntelligence.message_id.in_(msg_ids_for_intel))
                        )
                        intel_map = {
                            r.message_id: {"quality": r.quality_score, "spam": r.spam_score}
                            for r in intel_result.all()
                        }
                    except Exception as e:
                        logger.debug(f"Intel scores fetch failed (non-critical): {e}")

                context_messages = []
                for h_msg in recent_msgs:
                    if h_msg.agent_id:
                        msg_author = agent_name_map.get(h_msg.agent_id) or f"agent_{h_msg.agent_id}"
                        msg_author_type = "agent"
                    elif h_msg.user_id:
                        msg_author = user_name_map.get(h_msg.user_id) or f"user_{h_msg.user_id}"
                        msg_author_type = "user"
                    else:
                        msg_author = "unknown"
                        msg_author_type = "user"

                    context_messages.append({
                        "id": str(h_msg.id),
                        "author": msg_author,
                        "author_type": msg_author_type,
                        "content": h_msg.content[:500] if h_msg.content else "",  # Truncate for context
                        "timestamp": h_msg.created_at.isoformat() if h_msg.created_at else None,
                        "parent_id": str(h_msg.parent_id) if h_msg.parent_id else None,
                        "ai_summary": h_msg.ai_summary,
                        "quality_score": intel_map.get(h_msg.id, {}).get("quality"),
                        "spam_score": intel_map.get(h_msg.id, {}).get("spam"),
                    })

                # Helper to notify frontend when agent is skipped (prevents stuck spinner)
                async def notify_agent_skipped(agent, reason: str):
                    try:
                        from app.services.redis_sse_broker import redis_sse_broker
                        await redis_sse_broker.publish(
                            space_id=str(msg.space_id),
                            event="agent_skipped",
                            data={
                                "agent_id": str(agent.id),
                                "agent_name": agent.name,
                                "message_id": str(msg.id),
                                "reason": reason,
                            }
                        )
                    except Exception:
                        pass  # Best effort

                async with httpx.AsyncClient() as client:
                    for agent in cloud_agents:
                        # Prevent self-loop
                        if str(agent.id) == str(msg.agent_id):
                            continue

                        # IDEMPOTENCY CHECK: Prevent duplicate dispatches
                        # This can happen when both broadcast_sse() and /dispatch-trigger
                        # call _dispatch_cloud_agents for the same message (e.g., MCP path)
                        # TTL must cover the full dispatch timeout + margin to prevent
                        # duplicate dispatches if the original takes longer than expected
                        dedup_settings = get_settings()
                        dedup_ttl = max(120, dedup_settings.cloud_agent_http_timeout_seconds) + 30
                        dedup_key = f"dispatch:dedup:{msg.id}:{agent.id}"
                        if self.redis:
                            try:
                                # SETNX returns True if key was set (first dispatch)
                                # Returns False if key already exists (duplicate)
                                is_first = await self.redis.set(
                                    dedup_key, "1", nx=True, ex=dedup_ttl
                                )
                                if not is_first:
                                    logger.info(
                                        f"🔄 DEDUP: Skipping duplicate dispatch for msg={msg.id} "
                                        f"agent={agent.name} (already dispatched)"
                                    )
                                    continue
                            except Exception as dedup_err:
                                logger.warning(f"Dedup check failed (proceeding): {dedup_err}")

                        logger.info(f"DEBUG STATUS CHECK (pre-loop): agent={agent.name}, status={repr(agent.status)}")

                        if (agent.status or "").lower() != "active":
                            logger.warning(f"🛑 STATUS CHECK: Skipping agent '{agent.name}' because status={agent.status}")
                            await notify_agent_skipped(agent, f"Agent is {agent.status or 'inactive'}")
                            # Cache disabled state best-effort
                            if self.redis:
                                try:
                                    await self.redis.setex(f"agent:{agent.id}:disabled", 24 * 3600, "1")
                                except Exception:
                                    pass
                            continue

                        dispatch_space_id = str(msg.space_id or agent.space_id)

                        if self.agent_control_service:
                            try:
                                control_state = await self.agent_control_service.get_control_state(
                                    agent_id=agent.id,
                                    space_id=msg.space_id or agent.space_id,
                                    agent_slug=(agent.name or "").strip().lower() or None,
                                )
                            except Exception as exc:
                                logger.error(
                                    "🛑 SAFETY BLOCK: Agent-control lookup failed for %s; "
                                    "failing closed and skipping dispatch: %s",
                                    agent.name,
                                    exc,
                                )
                                await notify_agent_skipped(
                                    agent,
                                    "Agent pause/kill-switch state could not be verified",
                                )
                                continue
                            if control_state.is_disabled:
                                logger.info(
                                    "🛑 SAFETY BLOCK: Agent %s disabled by control state. Skipping.",
                                    agent.name,
                                )
                                reason = control_state.disabled_reason or (
                                    "Agent is taking a break"
                                    if control_state.disabled_until
                                    else "Agent is disabled"
                                )
                                await notify_agent_skipped(agent, reason)
                                continue

                            if control_state.no_reply:
                                logger.info(
                                    "🤐 NO_REPLY: Agent %s chose not to reply. Skipping.",
                                    agent.name,
                                )
                                await notify_agent_skipped(
                                    agent,
                                    control_state.no_reply_reason or "Chose not to reply",
                                )
                                continue

                            if getattr(control_state, "routing_only", False):
                                logger.info(
                                    "🔀 ROUTING_ONLY: Agent %s suppresses direct replies. Skipping direct dispatch.",
                                    agent.name,
                                )
                                await notify_agent_skipped(
                                    agent,
                                    getattr(control_state, "routing_only_reason", None)
                                    or "Routing only",
                                )
                                continue

                        if await self._is_agent_disabled_cache(str(agent.id), dispatch_space_id):
                            logger.info(
                                "🛑 SAFETY BLOCK: Agent %s is disabled/paused in Redis cache. Skipping.",
                                agent.name,
                            )
                            await notify_agent_skipped(agent, "Agent is paused or disabled")
                            continue

                        owner_user_id = getattr(msg, "user_id", None) or getattr(agent, "user_id", None)

                        # Check if sender (msg.agent_id) is also a cloud agent
                        # This determines whether we apply rate limiting
                        sender_is_cloud_agent = False
                        if msg.agent_id:
                            sender_agent_query = select(Agent.cloud_function_url).where(Agent.id == msg.agent_id)
                            sender_result = await session.execute(sender_agent_query)
                            sender_cloud_url = sender_result.scalar_one_or_none()
                            sender_is_cloud_agent = sender_cloud_url is not None

                        # THROTTLE: Cloud-to-cloud communication is rate limited to prevent feedback loops
                        # Cloud agent → Cloud agent: THROTTLED (limited to ~5 messages, configurable)
                        # Human/User → Cloud agent: ALLOWED (no rate limit)
                        # MCP/Sentinel agent → Cloud agent: ALLOWED (no rate limit)
                        if sender_is_cloud_agent:
                            # Apply rate limiting for cloud-to-cloud only
                            # Use settings for configurable throttle window (visible in CI)
                            throttle_settings = get_settings()
                            throttle_hit, throttle_info = await self._should_throttle_agent_reply(
                                session,
                                agent_id=agent.id,
                                space_id=msg.space_id,
                                channel=msg.channel,
                                window_seconds=throttle_settings.cloud_agent_burst_window_seconds,
                                user_id=owner_user_id,
                                sender_is_human=False,  # Cloud agent sender
                                tier=tier,
                            )

                            if throttle_hit:
                                # Use the configurable pause duration for consistent behavior
                                # Default 120s (2 minutes) - configurable via CLOUD_AGENT_THROTTLE_PAUSE_SECONDS
                                throttle_metadata = throttle_info.metadata if throttle_info and throttle_info.metadata else {}
                                pause_duration = int(
                                    throttle_metadata.get("window_seconds")
                                    or throttle_settings.cloud_agent_throttle_pause_seconds
                                )
                                # Ensure minimum pause (configurable via MIN_THROTTLE_PAUSE_SECONDS)
                                pause_duration = max(MIN_THROTTLE_PAUSE_SECONDS, pause_duration)

                                logger.info(
                                    f"🛑 CLOUD-TO-CLOUD THROTTLE: Skipping auto-reply from cloud agent '{agent.name}' - "
                                    f"sender '{author_name}' is also a cloud agent. "
                                    f"Limit hit: {throttle_info.count if throttle_info else '?'}/{throttle_info.limit if throttle_info else '?'}. "
                                    f"Pause duration: {pause_duration}s (full window, not remaining TTL)"
                                )

                                # Send throttle notification (once per window)
                                reason_code = throttle_info.reason if throttle_info else "cloud_to_cloud"
                                throttle_msg_key = f"throttle:msg:{agent.id}:{msg.space_id}:{reason_code}"

                                if not await self.redis.exists(throttle_msg_key):
                                    # Use full window duration for notification suppression
                                    await self.redis.setex(throttle_msg_key, pause_duration, "sent")

                                    pause_metadata = {
                                        "reason": "cloud_to_cloud_throttle",
                                        "reason_text": (
                                            f"Cloud-to-cloud rate limit: {throttle_info.count if throttle_info else '?'}/"
                                            f"{throttle_info.limit if throttle_info else '?'} messages"
                                        ),
                                        "pause_reason_text": (
                                            f"Pausing cloud-to-cloud replies for {pause_duration}s to prevent loops"
                                        ),
                                        "emoji": "⏸️",
                                        "pause_duration": pause_duration,  # Full window, not remaining TTL
                                        "pause_expires_at": (
                                            (datetime.now(timezone.utc) + timedelta(seconds=pause_duration)).isoformat()
                                        ),
                                        "window_seconds": pause_duration,  # For UI consistency
                                        "limit": throttle_info.limit if throttle_info else 5,
                                        "count": throttle_info.count if throttle_info else 0,
                                        "sender_type": "cloud_agent",
                                    }

                                    svc = MessagesService(session, self.redis, self.sse)
                                    await svc.send(
                                        actor=Actor(
                                            id=agent.id,
                                            type="agent",
                                            space_id=msg.space_id,
                                            capabilities={CAP_MESSAGES_SEND}
                                        ),
                                        content=f"@{author_name} I'm pausing cloud-to-cloud replies for {pause_duration}s to prevent loops.",
                                        channel=msg.channel,
                                        author_display_name=agent.name,
                                        adapter="cloud_agent_throttle",
                                        message_type="agent_pause",
                                        metadata=pause_metadata
                                    )

                                await notify_agent_skipped(agent, f"Rate limited ({throttle_info.count if throttle_info else '?'}/{throttle_info.limit if throttle_info else '?'})")
                                continue  # Skip this auto-reply due to throttle
                            else:
                                logger.info(
                                    f"✅ Cloud-to-cloud allowed for '{agent.name}' - "
                                    f"within rate limit ({throttle_info.count if throttle_info else 0}/{throttle_info.limit if throttle_info else '?'})"
                                )
                        else:
                            # Human/User or MCP agent - no rate limiting
                            logger.info(
                                f"✅ Proceeding with cloud agent '{agent.name}' auto-reply - "
                                f"sender is {'MCP agent' if msg.agent_id else 'user'} (no rate limit)"
                            )

                        effective_prompt = build_agent_dispatch_prompt(
                            agent_name=agent.name,
                            system_prompt=agent.system_prompt,
                        )

                        # Build sender info for current message
                        sender_name = "Unknown"
                        sender_id = None
                        sender_type = "unknown"
                        if msg.agent_id:
                            # Check sender agent type: cloud, webhook, or MCP
                            sender_agent_row = await session.execute(
                                select(Agent.name, Agent.cloud_function_url, Agent.origin, Agent.webhook_url).where(Agent.id == msg.agent_id)
                            )
                            sender_agent = sender_agent_row.one_or_none()
                            if sender_agent:
                                sender_name = sender_agent.name or sender_name
                                # Determine sender type by origin/url:
                                # - Cloud agents: origin='cloud' or have cloud_function_url
                                # - Webhook agents: origin='external_gateway' or have webhook_url
                                # - MCP agents: everything else (client-controlled)
                                if sender_agent.origin == "cloud" or sender_agent.cloud_function_url:
                                    sender_type = "cloud_agent"
                                elif sender_agent.origin == "external_gateway" or sender_agent.webhook_url:
                                    sender_type = "webhook_agent"
                                else:
                                    sender_type = "agent"  # MCP agent
                            sender_id = str(msg.agent_id)
                        elif msg.user_id:
                            sender_name_row = await session.execute(
                                select(User.username).where(User.id == msg.user_id)
                            )
                            sender_name = sender_name_row.scalar_one_or_none() or sender_name
                            sender_id = str(msg.user_id)
                            sender_type = "user"
                        sender_role = "agent" if msg.agent_id else "user"
                        labeled_user_message = f"{sender_name} ({sender_role}): {msg.content}"

                        # Get agent owner info for security context (owner vs sender distinction)
                        owner_handle = None
                        owner_id = None
                        if agent.user_id:
                            owner_row = await session.execute(
                                select(User.username).where(User.id == agent.user_id)
                            )
                            owner_handle = owner_row.scalar_one_or_none()
                            owner_id = str(agent.user_id)

                        # Fetch space info for context + default_model
                        from app.models.space import Space
                        space_result = await session.execute(
                            select(Space.name, Space.description, Space.default_model)
                            .where(Space.id == msg.space_id)
                        )
                        space_row = space_result.first()
                        space_name = space_row[0] if space_row else "Unknown Space"
                        space_description = space_row[1] if space_row else ""
                        space_default_model = space_row[2] if space_row else None

                        # Fetch top 5 nearby agents for collaboration context
                        # (active agents in same org, excluding current agent)
                        from app.core.agent_space import agents_in_space_subquery
                        nearby_agents_result = await session.execute(
                            select(Agent.name, Agent.agent_type, Agent.description, Agent.specialization, Agent.capabilities)
                            .where(
                                and_(
                                    Agent.id.in_(agents_in_space_subquery(msg.space_id)),
                                    Agent.status == "active",
                                    Agent.id != agent.id,
                                    Agent.is_internal.is_(False),
                                )
                            )
                            .limit(20)
                        )
                        nearby_agents = [
                            {
                                "name": row[0],
                                "type": row[1] or "assistant",
                                "description": row[2] or "",
                                "specialization": row[3] or "",
                                "capabilities": row[4] or {},
                            }
                            for row in nearby_agents_result.all()
                        ]

                        # Dispatcher context: tell the agent if it's the default router
                        is_dispatcher = False
                        try:
                            from app.models import SpaceMembership
                            dispatcher_check = await session.execute(
                                select(func.count()).select_from(SpaceMembership).where(
                                    and_(
                                        SpaceMembership.space_id == msg.space_id,
                                        SpaceMembership.default_route_to == agent.id,
                                    )
                                )
                            )
                            is_dispatcher = (dispatcher_check.scalar() or 0) > 0
                        except Exception as e:
                            logger.warning(f"Failed to check dispatcher status: {e}")

                        metadata = msg.message_metadata or {}
                        trigger_attachments = attachments_from_message_metadata(metadata)

                        # V3 payload format for agent_runner (CANONICAL FORMAT)
                        # V3 = minimal payload, agent hydrates context via MCP tools
                        # This reduces payload size and ensures fresh context at execution time.
                        # V1/V2 formats are deprecated - do not add new V1/V2 dispatch paths.
                        payload = {
                            # Version indicator for agent_runner to detect v3 format
                            "payload_version": "3",
                            # Core identification fields
                            "agent_name": agent.name,
                            "agent_id": str(agent.id),
                            "space_id": str(msg.space_id),
                            "space_name": space_name,
                            # Message reference for context/auth fetch
                            "message_id": str(msg.id),
                            # Sender info (who triggered this message)
                            "sender_handle": sender_name,
                            "sender_id": sender_id,
                            "sender_type": sender_type,  # "user" or "agent"
                            # Owner info (who created/owns this agent)
                            "owner_handle": owner_handle,
                            "owner_id": owner_id,
                            # Agent classification (for MCP token decisions)
                            "agent_type": agent.origin or "cloud",  # Use origin as source of truth (AGENTS-001)
                            "is_internal": agent.is_internal or False,
                            # Message content
                            "user_message": labeled_user_message,
                            "system_prompt": effective_prompt,
                            "history": formatted_history,
                            # Resolve model: space default_model overrides for space agents,
                            # then agent.model, then catalog default. Always resolve to Bedrock ID.
                            "model": resolve_bedrock_model_id(
                                (space_default_model if agent.origin == "space_agent" else None)
                                or agent.model
                                or DEFAULT_MODEL
                            ),
                            "org_tier": tier,  # For agent_runner tier-aware validation
                            # Context for agent awareness
                            "context_data": {
                                "agents": nearby_agents,
                                "is_primary_dispatcher": is_dispatcher,
                                "space_info": {
                                    "name": space_name,
                                    "description": space_description or "",
                                },
                                # Recent messages with IDs for reactions/replies
                                "messages": context_messages,
                                # The triggering message ID (for direct replies)
                                "trigger_message_id": str(msg.id),
                                # Attachments on the triggering message. Keep this in
                                # context_data for text-only agents and dashboards, and
                                # top-level `attachments` for multimodal runtimes.
                                "attachments": trigger_attachments,
                            },
                            "attachments": trigger_attachments,
                            "message_metadata": {
                                "accepted_attachments": trigger_attachments,
                                "attachments": trigger_attachments,
                                "context_uploads": metadata.get("context_uploads", []),
                            },
                            # Tool configuration - use helper from toggle registry (DRY)
                            "tool_config": {
                                "enabled_tools": build_enabled_tools_from_agent(agent),
                                # Legacy fields (deprecated, kept for backwards compat with runner)
                                "web_browsing_enabled": agent.web_browsing_enabled,
                                "ax_mcp_enabled": agent.ax_mcp_enabled,
                                "image_gen_enabled": agent.image_gen_enabled,
                                "capabilities": agent.capabilities or {},
                            },
                        }

                        # Concierge context: enrich payload when dispatching to aX
                        # This gives aX the routing metadata it needs for concierge behavior
                        if agent.origin == "space_agent":
                            routing_meta = metadata.get("routing", {})
                            payload["concierge_context"] = {
                                "role": "concierge",
                                "unresolved_mentions": metadata.get("unresolved_mentions", []),
                                "original_mentions": metadata.get("original_mentions", []),
                                "default_routed": not metadata.get("original_mentions") and not metadata.get("unresolved_mentions"),
                                "router_inferred": metadata.get("router_inferred", False),
                                "routing_hops": routing_meta.get("hops", 0),
                            }

                        try:
                            # CRITICAL SAFETY CHECK: Double-check agent is still active before making expensive API call
                            # This prevents runaway costs if agent was disabled mid-processing
                            fresh_status_result = await session.execute(
                                select(Agent.status)
                                .where(Agent.id == agent.id)
                                .execution_options(no_cache=True, populate_existing=True)
                            )
                            fresh_status = fresh_status_result.scalar()

                            if (fresh_status or "").lower() != 'active':
                                logger.warning(
                                    f"🛑 SAFETY BLOCK: Agent '{agent.name}' status changed to '{fresh_status}' - "
                                    f"skipping cloud function call to prevent runaway costs"
                                )
                                await notify_agent_skipped(agent, f"Agent status changed to {fresh_status}")
                                continue

                            logger.info(f"✅ SAFETY CHECK PASSED: Agent '{agent.name}' is active, proceeding with cloud function call")

                            # Generate dispatch_id for end-to-end tracing
                            import uuid as uuid_module
                            import time as time_module
                            dispatch_id = str(uuid_module.uuid4())

                            # Security: Add API key header for authentication
                            # Also add version header so agent_runner knows to expect v3 payload
                            from app.core.config import get_settings
                            settings = get_settings()
                            headers = {
                                "X-API-Key": settings.agent_runner_api_key,
                                "X-Payload-Version": "3",
                                "X-Dispatch-ID": dispatch_id,
                            }

                            # ============================================================
                            # Get MCP auth token (needed for both sync and async paths)
                            # ============================================================
                            mcp_auth = None
                            # DIAGNOSTIC: Log user_id status for debugging mcp_auth issues (using WARNING for visibility)
                            logger.warning(
                                f"MCP_AUTH_CHECK agent_name={agent.name} "
                                f"agent_id={agent.id} user_id={agent.user_id} "
                                f"has_user_id={agent.user_id is not None}"
                            )
                            if agent.user_id:
                                # DELEGATE-001: Try delegated token for home space first
                                # Use the MESSAGE sender's user_id (who triggered this),
                                # not agent.user_id (which is the system user for space agents)
                                sender_user_id = str(msg.user_id) if msg.user_id else str(agent.user_id)
                                mcp_auth = await mint_delegated_token(
                                    agent_id=str(agent.id),
                                    agent_name=agent.name,
                                    user_id=sender_user_id,
                                    space_id=str(msg.space_id),
                                    db=session,
                                    extra_claims={"correlation_id": dispatch_id},
                                )
                                if mcp_auth:
                                    logger.info(
                                        f"DELEGATE_DISPATCH agent={agent.name} "
                                        f"sender_user={sender_user_id} space={msg.space_id}"
                                    )
                                else:
                                    # Not a home space or checks failed — use normal token
                                    mcp_auth = await get_or_mint_mcp_token(
                                        agent_id=str(agent.id),
                                        agent_name=agent.name,
                                        user_id=str(agent.user_id),
                                        space_id=str(msg.space_id),
                                    )
                                # DIAGNOSTIC: Log token minting result (using WARNING for visibility)
                                logger.warning(
                                    f"MCP_AUTH_RESULT agent_name={agent.name} "
                                    f"token_minted={mcp_auth is not None} "
                                    f"has_access_token={bool(mcp_auth.get('access_token')) if mcp_auth else False}"
                                )
                                if mcp_auth:
                                    # Include correlation_id so agent runners can
                                    # forward as X-Correlation-Id header to MCP server
                                    mcp_auth["correlation_id"] = dispatch_id
                                    payload["mcp_auth"] = mcp_auth
                            else:
                                logger.warning(
                                    f"MCP_AUTH_SKIPPED agent_name={agent.name} "
                                    f"reason=no_user_id agent_id={agent.id}"
                                )

                            # Add cloud_function_url to payload (needed by dispatch queue)
                            payload["cloud_function_url"] = agent.cloud_function_url
                            payload["dispatch_id"] = dispatch_id

                            # Routing tracking: store relay metadata in Redis
                            # for delivery confirmation loop (Phase 4)
                            routing_meta = (msg.message_metadata or {}).get("routing", {})
                            if routing_meta.get("mode") == "ax_relay":
                                try:
                                    import redis.asyncio as aioredis
                                    routing_settings = get_settings()
                                    routing_redis = aioredis.from_url(
                                        routing_settings.redis_url, decode_responses=True
                                    )
                                    await routing_redis.hset(
                                        f"ax:routing-track:{dispatch_id}",
                                        mapping={
                                            "original_msg_id": str(routing_meta.get("source_message_id", msg.id)),
                                            "target_agent_id": str(agent.id),
                                            "target_agent_name": agent.name,
                                            "sender_id": str(routing_meta.get("original_sender_id", "")),
                                            "status": "dispatched",
                                            "dispatched_at": datetime.now(timezone.utc).isoformat(),
                                            "space_id": str(msg.space_id),
                                        },
                                    )
                                    await routing_redis.expire(
                                        f"ax:routing-track:{dispatch_id}", 3600
                                    )
                                    await routing_redis.close()
                                    logger.info(
                                        "ROUTING_TRACK_STORED dispatch_id=%s target=%s",
                                        dispatch_id, agent.name,
                                    )
                                    # Schedule timeout check
                                    timeout_task = asyncio.create_task(
                                        _routing_timeout_check(dispatch_id, str(msg.space_id))
                                    )
                                    _background_tasks.add(timeout_task)
                                    timeout_task.add_done_callback(_background_tasks.discard)
                                except Exception as rt_err:
                                    logger.warning(
                                        "ROUTING_TRACK_STORE_FAILED dispatch_id=%s error=%s",
                                        dispatch_id, rt_err,
                                    )

                            logger.info(
                                f"V3_PAYLOAD_BUILT dispatch_id={dispatch_id} "
                                f"agent_name={agent.name} message_id={msg.id} "
                                f"origin={agent.origin} "
                                f"use_cloud_tasks={settings.use_cloud_tasks}"
                            )

                            # ============================================================
                            # EXTERNAL GATEWAY DISPATCH (webhook-based)
                            # For agents with origin='external_gateway', dispatch via the
                            # SAME queue system as cloud agents for unified observability.
                            # The dispatch_executor routes to webhook HTTP based on dispatch_type.
                            # ============================================================
                            if agent.origin == 'external_gateway':
                                try:
                                    from app.core.dispatch_queue import get_dispatch_queue
                                    from app.core.mcp_token_cache import mint_enclave_token

                                    # Validate webhook is configured
                                    if not agent.webhook_url:
                                        logger.warning(
                                            f"DISPATCH_SKIP dispatch_id={dispatch_id} "
                                            f"agent_name={agent.name} reason=no_webhook_url"
                                        )
                                        continue

                                    # Mint enclave-scoped token for external agent
                                    enclave_auth = await mint_enclave_token(
                                        agent_id=str(agent.id),
                                        agent_name=agent.name,
                                        owner_id=str(agent.user_id),
                                        dispatch_id=dispatch_id,
                                        pinned_to_space=str(agent.pinned_to_space) if agent.pinned_to_space else None,
                                    )

                                    if not enclave_auth:
                                        logger.error(
                                            f"DISPATCH_SKIP dispatch_id={dispatch_id} "
                                            f"agent_name={agent.name} reason=enclave_token_failed"
                                        )
                                        continue

                                    # Add webhook-specific fields to payload for unified dispatch
                                    # NOTE: webhook_secret is NOT included - executor fetches fresh from DB
                                    # This prevents secret exposure in logs/queue storage
                                    payload["dispatch_type"] = "webhook"
                                    payload["webhook_url"] = agent.webhook_url
                                    payload["auth_token"] = enclave_auth["access_token"]

                                    # Use SAME queue path as cloud agents
                                    queue = get_dispatch_queue()
                                    task_id = await queue.enqueue(
                                        agent_id=str(agent.id),
                                        agent_name=agent.name,
                                        message_id=str(msg.id),
                                        payload=payload,
                                        dispatch_id=dispatch_id,
                                    )

                                    logger.info(
                                        f"DISPATCH_QUEUED dispatch_id={dispatch_id} "
                                        f"task_id={task_id} agent_name={agent.name} "
                                        f"message_id={msg.id} dispatch_type=webhook"
                                    )

                                    # Notify frontend that agent is processing (shows spinner)
                                    try:
                                        from app.services.redis_sse_broker import redis_sse_broker
                                        await redis_sse_broker.publish(
                                            space_id=str(msg.space_id),
                                            event="agent_processing",
                                            data={
                                                "agent_id": str(agent.id),
                                                "agent_name": agent.name,
                                                "message_id": str(msg.id),
                                                "status": "queued",
                                                "dispatch_type": "webhook",
                                            }
                                        )
                                    except Exception:
                                        pass  # Best effort - don't block on SSE failure

                                    # Continue to next agent (don't fall through to cloud dispatch)
                                    continue

                                except Exception as webhook_err:
                                    logger.exception(
                                        f"DISPATCH_ERROR dispatch_id={dispatch_id} "
                                        f"agent_name={agent.name} dispatch_type=webhook error={webhook_err}"
                                    )
                                    continue

                            # ============================================================
                            # SPACE AGENT DISPATCH (Local Container — Streaming)
                            # For agents with origin='space_agent', dispatch to local
                            # Space Agent container with streaming responses.
                            # ============================================================
                            if agent.origin == 'space_agent':
                                try:
                                    from app.core.dispatch_queue import get_dispatch_queue
                                    from app.core.mcp_token_cache import mint_space_agent_token
                                    runtime_settings = get_settings()
                                    use_agentcore_runtime = (
                                        runtime_settings.space_agent_runtime_backend == "agentcore"
                                    )

                                    # Preserve delegated home-space auth minted above. Replacing it
                                    # with a normal space-agent token strips delegated_for and breaks
                                    # HITL draft creation/control flows.
                                    existing_mcp_auth = payload.get("mcp_auth") or {}
                                    if existing_mcp_auth.get("access_token"):
                                        logger.info(
                                            "SPACE_AGENT_MCP_AUTH_PRESERVED dispatch_id=%s agent=%s",
                                            dispatch_id,
                                            agent.name,
                                        )
                                    else:
                                        # Mint MCP auth token for Space Agent
                                        # Space Agent uses MCP workspace tools (messages, tasks, etc.),
                                        # NOT cloud agent toggles (ax_mcp, web_fetch, etc.).
                                        # Pass None to use mint_space_agent_token's correct defaults.
                                        space_auth = await mint_space_agent_token(
                                            agent_id=str(agent.id),
                                            agent_name=agent.name,
                                            space_id=str(msg.space_id),
                                            user_id=str(agent.user_id) if agent.user_id else "",
                                            extra_claims={"correlation_id": dispatch_id},
                                        )
                                        if space_auth:
                                            payload["mcp_auth"] = space_auth

                                    if use_agentcore_runtime:
                                        bedrock_agent_id = agent.bedrock_agent_id or runtime_settings.bedrock_agent_id
                                        bedrock_agent_alias_id = (
                                            agent.bedrock_agent_alias_id or runtime_settings.bedrock_agent_alias_id
                                        )
                                        if not bedrock_agent_id or not bedrock_agent_alias_id:
                                            logger.error(
                                                "DISPATCH_ERROR dispatch_id=%s agent_name=%s "
                                                "dispatch_type=agentcore_space_agent reason=missing_bedrock_config",
                                                dispatch_id,
                                                agent.name,
                                            )
                                            continue

                                        payload["dispatch_type"] = "agentcore"
                                        payload["bedrock_agent_id"] = bedrock_agent_id
                                        payload["bedrock_agent_alias_id"] = bedrock_agent_alias_id
                                        payload["authority_class"] = "space_concierge"
                                    else:
                                        payload["dispatch_type"] = "space_agent"

                                    queue = get_dispatch_queue()
                                    task_id = await queue.enqueue(
                                        agent_id=str(agent.id),
                                        agent_name=agent.name,
                                        message_id=str(msg.id),
                                        payload=payload,
                                        dispatch_id=dispatch_id,
                                    )

                                    logger.info(
                                        f"DISPATCH_QUEUED dispatch_id={dispatch_id} "
                                        f"task_id={task_id} agent_name={agent.name} "
                                        f"message_id={msg.id} dispatch_type={payload['dispatch_type']}"
                                    )

                                    try:
                                        from app.services.redis_sse_broker import redis_sse_broker
                                        await redis_sse_broker.publish(
                                            space_id=str(msg.space_id),
                                            event="agent_processing",
                                            data={
                                                "agent_id": str(agent.id),
                                                "agent_name": agent.name,
                                                "message_id": str(msg.id),
                                                "status": "queued",
                                                "dispatch_type": payload["dispatch_type"],
                                            }
                                        )
                                    except Exception:
                                        pass

                                    continue

                                except Exception as sa_err:
                                    logger.exception(
                                        f"DISPATCH_ERROR dispatch_id={dispatch_id} "
                                        f"agent_name={agent.name} dispatch_type=space_agent error={sa_err}"
                                    )
                                    continue

                            # ============================================================
                            # AGENTCORE DISPATCH (Bedrock Agent — Mode A)
                            # For agents with origin='agentcore', dispatch via Bedrock
                            # AgentCore with per-space/per-user session isolation.
                            # ============================================================
                            if agent.origin == 'agentcore':
                                try:
                                    from app.core.dispatch_queue import get_dispatch_queue

                                    payload["dispatch_type"] = "agentcore"
                                    payload["bedrock_agent_id"] = agent.bedrock_agent_id or ""
                                    payload["bedrock_agent_alias_id"] = agent.bedrock_agent_alias_id or ""

                                    queue = get_dispatch_queue()
                                    task_id = await queue.enqueue(
                                        agent_id=str(agent.id),
                                        agent_name=agent.name,
                                        message_id=str(msg.id),
                                        payload=payload,
                                        dispatch_id=dispatch_id,
                                    )

                                    logger.info(
                                        f"DISPATCH_QUEUED dispatch_id={dispatch_id} "
                                        f"task_id={task_id} agent_name={agent.name} "
                                        f"message_id={msg.id} dispatch_type=agentcore"
                                    )

                                    try:
                                        from app.services.redis_sse_broker import redis_sse_broker
                                        await redis_sse_broker.publish(
                                            space_id=str(msg.space_id),
                                            event="agent_processing",
                                            data={
                                                "agent_id": str(agent.id),
                                                "agent_name": agent.name,
                                                "message_id": str(msg.id),
                                                "status": "queued",
                                                "dispatch_type": "agentcore",
                                            }
                                        )
                                    except Exception:
                                        pass

                                    continue

                                except Exception as ac_err:
                                    logger.exception(
                                        f"DISPATCH_ERROR dispatch_id={dispatch_id} "
                                        f"agent_name={agent.name} dispatch_type=agentcore error={ac_err}"
                                    )
                                    continue

                            # ============================================================
                            # CLOUD TASKS DISPATCH (async queue-based)
                            # When USE_CLOUD_TASKS=true, enqueue to dispatch queue.
                            # dispatch_worker (local) or Cloud Tasks (prod) calls agent_runner
                            # via HTTP and captures the response in execute_dispatch().
                            # ============================================================
                            if settings.use_cloud_tasks:
                                try:
                                    from app.core.dispatch_queue import get_dispatch_queue

                                    # DIAGNOSTIC: Log when we're about to enqueue
                                    enqueue_timestamp = time_module.time()
                                    logger.info(
                                        f"DISPATCH_ENQUEUE_START dispatch_id={dispatch_id} "
                                        f"agent_name={agent.name} message_id={msg.id} "
                                        f"enqueue_start_at={enqueue_timestamp}"
                                    )

                                    queue = get_dispatch_queue()
                                    task_id = await queue.enqueue(
                                        agent_id=str(agent.id),
                                        agent_name=agent.name,
                                        message_id=str(msg.id),
                                        payload=payload,
                                        dispatch_id=dispatch_id,
                                    )

                                    enqueue_duration_ms = int((time_module.time() - enqueue_timestamp) * 1000)
                                    logger.info(
                                        f"DISPATCH_QUEUED dispatch_id={dispatch_id} "
                                        f"task_id={task_id} agent_name={agent.name} "
                                        f"message_id={msg.id} "
                                        f"enqueue_duration_ms={enqueue_duration_ms} "
                                        f"enqueued_at={time_module.time()}"
                                    )

                                    # Notify frontend that agent is processing (shows spinner)
                                    try:
                                        from app.services.redis_sse_broker import redis_sse_broker
                                        await redis_sse_broker.publish(
                                            space_id=str(msg.space_id),
                                            event="agent_processing",
                                            data={
                                                "agent_id": str(agent.id),
                                                "agent_name": agent.name,
                                                "message_id": str(msg.id),
                                                "status": "queued",
                                            }
                                        )
                                    except Exception:
                                        pass  # Best effort - don't block on SSE failure

                                    # Fire-and-forget: continue to next agent
                                    continue

                                except Exception as queue_err:
                                    logger.error(
                                        f"DISPATCH_QUEUE_ERROR dispatch_id={dispatch_id} "
                                        f"agent_name={agent.name} error={queue_err}"
                                    )
                                    # Fall through to sync HTTP as fallback
                                    logger.info(
                                        f"DISPATCH_FALLBACK dispatch_id={dispatch_id} "
                                        f"agent_name={agent.name} reason=queue_error"
                                    )

                            # ============================================================
                            # LEGACY PARALLEL DISPATCH - Redis Streams + HTTP
                            # Queue ensures reliable delivery; HTTP provides sync response
                            # ============================================================

                            # 1. XADD to Redis Streams inbox (reliable, async)
                            # Agent runner consumes via XREADGROUP with consumer group
                            try:
                                inbox_stream = f"inbox:{msg.space_id}:{agent.name.lower()}"

                                # Build queue-specific payload (includes mcp_auth from above)
                                queue_payload = payload.copy()

                                stream_payload = {"payload": json.dumps(queue_payload)}
                                # Phase 1: No stream trimming - let messages accumulate
                                # Cleanup will be handled by agent_runner after XACK
                                stream_id = await self.redis.xadd(
                                    inbox_stream,
                                    stream_payload,
                                )
                                logger.info(
                                    f"📨 QUEUE DISPATCH: Added to {inbox_stream}, "
                                    f"stream_id={stream_id}, agent={agent.name}"
                                )
                            except Exception as queue_err:
                                # Queue failure is non-fatal - HTTP dispatch continues
                                logger.warning(
                                    f"⚠️ QUEUE DISPATCH FAILED: {inbox_stream}, "
                                    f"agent={agent.name}, error={queue_err}"
                                )

                            # 2. HTTP POST to cloud function (existing path, sync response)
                            # Notify frontend that agent is processing (shows spinner)
                            try:
                                from app.services.redis_sse_broker import redis_sse_broker
                                await redis_sse_broker.publish(
                                    space_id=str(msg.space_id),
                                    event="agent_processing",
                                    data={
                                        "agent_id": str(agent.id),
                                        "agent_name": agent.name,
                                        "message_id": str(msg.id),
                                        "status": "processing",
                                    }
                                )
                            except Exception:
                                pass  # Best effort - don't block on SSE failure

                            # DISPATCH_ATTEMPT: Log before HTTP call for tracing
                            logger.info(
                                f"DISPATCH_ATTEMPT dispatch_id={dispatch_id} "
                                f"message_id={msg.id} agent_name={agent.name} "
                                f"space_id={msg.space_id} url={agent.cloud_function_url}"
                            )
                            dispatch_start = time_module.time()

                            response = await client.post(
                                agent.cloud_function_url,
                                json=payload,
                                headers=headers,
                                timeout=settings.cloud_agent_http_timeout_seconds
                            )
                            response.raise_for_status()

                            # DISPATCH_RESULT: Log success with timing
                            dispatch_duration_ms = int((time_module.time() - dispatch_start) * 1000)
                            logger.info(
                                f"DISPATCH_RESULT dispatch_id={dispatch_id} "
                                f"status=success duration_ms={dispatch_duration_ms} "
                                f"http_status={response.status_code}"
                            )

                            data = response.json()
                            reply_content = data.get("response", "")

                            if reply_content:
                                # Import required modules for sanitization and service layer
                                logger = logging.getLogger(__name__)

                                # DEBUG: Log what Gemini actually returned
                                logger.info(f"🔍 GEMINI-RAW: agent={agent.name}, content={repr(reply_content[:200])}")

                                # STEP 1: Sanitize self-mentions (strip @, fallback hard strip)
                                sanitized_content, self_mention_stripped = sanitize_self_mentions(
                                    reply_content,
                                    agent.name
                                )
                                logger.info(f"🔍 AFTER-sanitize_self_mentions: stripped={self_mention_stripped}, content={repr(sanitized_content[:200])}")

                                sanitized_content = self._strip_self_mention(sanitized_content, agent.name)
                                logger.info(f"🔍 AFTER-_strip_self_mention: content={repr(sanitized_content[:200])}")

                                if self_mention_stripped:
                                    logger.info(
                                        f"CLOUD-AGENT-SANITIZE: Stripped @{agent.name} from "
                                        f"cloud agent '{agent.name}' reply"
                                    )

                                # SAFETY CHECK: If self-mention persists (sanitization failed), ABORT
                                # This prevents the infinite loop if regex misses something
                                if f"@{agent.name}".lower() in sanitized_content.lower():
                                    logger.error(
                                        f"🚨 CRITICAL SAFETY: Sanitization FAILED for agent '{agent.name}'. "
                                        f"Content still contains self-mention. ABORTING DISPATCH."
                                    )
                                    continue

                                # STEP 2: Ensure parent sender is mentioned so wait=mentions triggers
                                # BUT: Skip auto-mention if parent is a cloud agent (prevents loops)
                                parent_sender_query = text("""
                                    SELECT
                                        CASE
                                            WHEN m.agent_id IS NOT NULL THEN a.name
                                            ELSE u.username
                                        END as sender_name,
                                        a.cloud_function_url as parent_cloud_url
                                    FROM messages m
                                    LEFT JOIN agents a ON m.agent_id = a.id
                                    LEFT JOIN users u ON m.user_id = u.id
                                    WHERE m.id = :msg_id
                                """)

                                parent_result = await session.execute(
                                    parent_sender_query,
                                    {"msg_id": msg.id}
                                )
                                parent_sender = parent_result.fetchone()

                                if parent_sender and parent_sender.sender_name:
                                    parent_name = parent_sender.sender_name
                                    is_self_reply = (parent_name.lower() == agent.name.lower())
                                    parent_is_cloud_agent = parent_sender.parent_cloud_url is not None

                                    # Skip auto-mention if:
                                    # 1. Replying to self (already handled)
                                    # 2. Parent is a cloud agent (prevents cloud-to-cloud loops)
                                    # Auto-prepend @mention removed — replies should be clean content.
                                    # Agents handle their own @mentions if needed.
                                    if not is_self_reply and not parent_is_cloud_agent:
                                        pass  # No auto-prepend
                                    elif parent_is_cloud_agent:
                                        logger.info(
                                            f"CLOUD-AGENT-SKIP-AUTO-MENTION: Skipping @{parent_name} "
                                            f"auto-mention - parent is cloud agent (prevents loops)"
                                        )

                                # DEBUG: Log final content before send
                                logger.info(f"🔍 FINAL-BEFORE-SEND: content={repr(sanitized_content[:200])}")

                                # STEP 2.5: Warn-only cost metering (estimates until tokens are available)
                                try:
                                    if self.cloud_agent_limiter:
                                        cost_outcome = await self.cloud_agent_limiter.record_cost(
                                            agent_id=agent.id,
                                            space_id=msg.space_id,
                                            user_id=getattr(msg, "user_id", None),
                                            input_tokens=data.get("input_tokens"),
                                            output_tokens=data.get("output_tokens"),
                                            input_chars=len(msg.content or "") if msg and msg.content else 0,
                                            output_chars=len(sanitized_content or ""),
                                            tier=tier,
                                        )
                                        if cost_outcome:
                                            logger.info(
                                                "CLOUD_AGENT_COST_RECORDED",
                                                extra={
                                                    "agent_id": str(agent.id),
                                                    "space_id": str(msg.space_id),
                                                    "user_id": str(getattr(msg, 'user_id', None)) if getattr(msg, "user_id", None) else None,
                                                    "tier": tier,
                                                    "cents_added": cost_outcome.cents_added,
                                                    "estimate": cost_outcome.estimate,
                                                    "buckets_crossed": cost_outcome.buckets_crossed,
                                                    "totals": cost_outcome.totals,
                                                },
                                            )
                                except Exception as exc:
                                    logger.warning(f"CLOUD_AGENT_COST_WARN_ONLY_FAILED: {exc}")

                                # STEP 3: Create actor and use service layer
                                # FIX: Use redis_sse_broker directly for cross-process SSE delivery
                                # The legacy org_event_broker (in-memory) was silently failing to bridge to Redis
                                from app.services.redis_sse_broker import redis_sse_broker

                                actor = Actor(
                                    id=agent.id,
                                    type="agent",
                                    space_id=msg.space_id,
                                    capabilities={CAP_MESSAGES_SEND}
                                )

                                svc = MessagesService(
                                    db=session,
                                    redis_client=self.redis,
                                    sse_broker=redis_sse_broker  # Use Redis Streams for reliable cross-process delivery
                                )

                                # Service handles everything: persist, broadcast, mention extraction
                                reply_msg = await svc.send(
                                    actor=actor,
                                    content=sanitized_content,
                                    channel=msg.channel,
                                    parent_id=msg.id,
                                    metadata={},
                                    author_display_name=agent.name,
                                    adapter="cloud_agent"
                                )

                                # DISPATCH_RESPONSE_SAVED: Log for observability dashboard
                                # "The reply is API" - this is the HTTP response saved to DB
                                response_preview = sanitized_content[:300].replace("\n", " ") if sanitized_content else ""
                                saved_log = (
                                    f"DISPATCH_RESPONSE_SAVED dispatch_id={dispatch_id} "
                                    f"agent_name={agent.name} reply_id={reply_msg.id} "
                                    f"parent_id={msg.id} content_len={len(sanitized_content)} "
                                    f"sender_type={sender_type} "
                                    f'response_preview="{response_preview}"'
                                )
                                logger.info(saved_log)

                                # No need for manual commit/broadcast - service handles it!

                                # CRITICAL: Clear processing status on success
                                # This fixes stuck processing badges in the frontend
                                try:
                                    await redis_sse_broker.publish(
                                        space_id=str(msg.space_id),
                                        event="agent_processing",
                                        data={
                                            "agent_id": str(agent.id),
                                            "agent_name": agent.name,
                                            "message_id": str(msg.id),
                                            "status": "completed",
                                        }
                                    )
                                except Exception:
                                    pass  # Best effort

                        except Exception as e:
                            import logging
                            logger = logging.getLogger(__name__)

                            # DISPATCH_RESULT: Log failure with timing (if dispatch_start was set)
                            if 'dispatch_start' in dir():
                                dispatch_duration_ms = int((time_module.time() - dispatch_start) * 1000)
                            else:
                                dispatch_duration_ms = 0

                            failure_class = classify_dispatch_failure(e)
                            error_type = "timeout" if failure_class == "timeout" else "error"
                            logger.error(
                                f"DISPATCH_RESULT dispatch_id={dispatch_id if 'dispatch_id' in dir() else 'unknown'} "
                                f"status={error_type} duration_ms={dispatch_duration_ms} "
                                f"failure_class={failure_class} error_type={type(e).__name__} error={str(e)[:200]}"
                            )

                            # Include exception type + classification for debugging
                            logger.error(
                                f"Failed to invoke Cloud Agent {agent.name}: {type(e).__name__}: {e} "
                                f"(failure_class={failure_class})"
                            )

                            # CRITICAL: Notify frontend of failure via SSE
                            # This prevents the stuck progress bar at 0% issue
                            try:
                                from app.services.redis_sse_broker import redis_sse_broker
                                await redis_sse_broker.publish(
                                    space_id=str(msg.space_id),
                                    event="agent_error",
                                    data={
                                        "agent_id": str(agent.id),
                                        "agent_name": agent.name,
                                        "message_id": str(msg.id),
                                        "error": str(e)[:200],  # Truncate for safety
                                        "error_type": type(e).__name__,
                                        "failure_class": failure_class,
                                    }
                                )
                                logger.info(f"📤 Sent agent_error SSE event for {agent.name}")
                            except Exception as sse_err:
                                logger.warning(f"Failed to send agent_error SSE: {sse_err}")

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Error in _dispatch_cloud_agents: {e}")
