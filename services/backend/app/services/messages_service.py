"""
Unified message service for both API and MCP.
Transport-agnostic business logic for message operations.

REFACTORED: Split into modular components for maintainability.
"""

# @ax:tag area=backend component=messages_service tech=fastapi,postgres,redis guide=backend/AGENT.md
from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import exists
from sqlalchemy.types import String

from app.services.cloud_agent_limiter import CloudAgentLimiter

from ..core.actor import CAP_MESSAGES_SEND, Actor
from ..models.agent import Agent
from ..models.message import Message
from ..models.space import Space
from ..models.space_membership import SpaceMembership
from ..models.user import User
from ..services.notifications_service import NotificationsService
from ..services.space_validation_service import SpaceValidationService

# Import modular helpers
from .message_visibility import exclude_ui_only_no_reply_clause, is_ui_only_no_reply_metadata
from .messages_core import MessagesCoreHelper
from .messages_notifications import MessagesNotificationHelper
from .messages_parity import MessagesParityHelper

# Strong reference holder for background tasks to prevent GC
_background_tasks = set()

UNREAD_ID_FILTER_BATCH_SIZE = 10_000


def _chunk_uuid_list(ids: list[UUID], size: int | None = None) -> list[list[UUID]]:
    """Split UUID filters so unread-list queries stay under DB bind limits."""
    size = size or UNREAD_ID_FILTER_BATCH_SIZE
    return [ids[index : index + size] for index in range(0, len(ids), size)]


class MessagesService:
    """Transport-agnostic message operations used by both API and MCP."""

    def __init__(self, db: AsyncSession, redis_client, sse_broker):
        self.db = db
        self.redis = redis_client
        self.sse = sse_broker
        self.notifications = NotificationsService(db, redis_client)

        # Initialize modular helpers
        self.core = MessagesCoreHelper(db)
        self.notify = MessagesNotificationHelper(db, redis_client, sse_broker)
        self.parity = MessagesParityHelper()
        self.limiter = CloudAgentLimiter(redis_client)

    @staticmethod
    def _normalize_filter_agent(raw: str | None) -> tuple[UUID | None, str | None]:
        """Accept UUIDs or agent handle forms for message filtering."""

        if not raw:
            return None, None

        term = raw.strip()
        if not term:
            return None, None

        if term.lower().startswith("agent:"):
            term = term.split(":", 1)[1].strip()

        term = term.lstrip("@").strip()
        if not term:
            return None, None

        try:
            return UUID(term), None
        except (ValueError, TypeError, AttributeError):
            return None, term

    @staticmethod
    def _normalize_agent_id(raw: Any) -> str | None:
        """Normalize a candidate agent id to canonical UUID string."""
        if raw is None:
            return None

        try:
            value = str(raw).strip()
            if not value:
                return None
            return str(UUID(value))
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _allows_implicit_routing(metadata: dict[str, Any]) -> bool:
        """Return whether default/aX routing may be inferred for this message."""
        return metadata.get("top_level_ingress") is not False

    @classmethod
    def _merge_routing_mentions(
        cls,
        *,
        existing_mentions: list[Any] | None,
        primary_agent_id: Any = None,
        mentioned_agent_ids: Any = None,
    ) -> tuple[list[Any], bool]:
        """
        Merge routing targets from metadata while preserving primary-first ordering.

        Output mention entries are either:
        - agent handles (str)
        - {"agent_id": <uuid>, "source": <source>} dict entries
        """

        merged: list[Any] = []
        seen_agent_ids: set[str] = set()
        seen_handles: set[str] = set()

        explicit_targets_present = False

        def add_entry(entry: Any, *, source: str = "mention") -> None:
            if isinstance(entry, dict):
                agent_id = cls._normalize_agent_id(entry.get("agent_id"))
                if agent_id:
                    if agent_id in seen_agent_ids:
                        return
                    seen_agent_ids.add(agent_id)
                    merged.append({"agent_id": agent_id, "source": entry.get("source") or source})
                    return

                handle = str(entry.get("handle") or entry.get("agent_name") or "").strip().lstrip("@").lower()
                if handle and handle not in seen_handles:
                    seen_handles.add(handle)
                    merged.append(handle)
                return

            if isinstance(entry, str):
                raw = entry.strip()
                if not raw:
                    return

                agent_id = cls._normalize_agent_id(raw)
                if agent_id:
                    if agent_id in seen_agent_ids:
                        return
                    seen_agent_ids.add(agent_id)
                    merged.append({"agent_id": agent_id, "source": source})
                    return

                handle = raw.lstrip("@").strip().lower()
                if handle and handle not in seen_handles:
                    seen_handles.add(handle)
                    merged.append(handle)

        primary_id = cls._normalize_agent_id(primary_agent_id)
        if primary_id:
            explicit_targets_present = True
            add_entry({"agent_id": primary_id, "source": "primary_selected"}, source="primary_selected")

        for existing in (existing_mentions or []):
            add_entry(existing, source="mention")

        if mentioned_agent_ids is not None:
            explicit_targets_present = True
            if isinstance(mentioned_agent_ids, (list, tuple, set)):
                candidates = mentioned_agent_ids
            else:
                candidates = [mentioned_agent_ids]
            for candidate in candidates:
                add_entry({"agent_id": candidate, "source": "mentioned_agent_ids"}, source="mentioned_agent_ids")

        return merged, explicit_targets_present

    async def _expand_group_mentions(self, group_ids: Any, space_id: Any) -> list[str]:
        """Resolve agent-group ids into member agent_id strings.

        Used to fan a message out to a whole group. Resolves:
          - explicit members (agent_group_members rows), and
          - "smart" groups (is_dynamic) via simple dynamic_rules:
              {"all": true}            -> every active agent in the space
              {"agent_type": "x"|[..]} -> active agents of those type(s)
        Unknown rule keys are ignored (explicit members still apply). All
        resolution is scoped to ``space_id`` so a group can only ever expand to
        agents that have active access to the sender's space.
        """
        if not group_ids:
            return []
        from app.core.agent_space import agents_in_space_subquery
        from app.models.agent import Agent
        from app.models.agent_groups import AgentGroup, AgentGroupMember

        try:
            space_uuid = space_id if isinstance(space_id, uuid.UUID) else uuid.UUID(str(space_id))
        except (ValueError, TypeError):
            return []

        raw = group_ids if isinstance(group_ids, (list, tuple, set)) else [group_ids]
        norm_ids: list[uuid.UUID] = []
        for g in raw:
            try:
                norm_ids.append(uuid.UUID(str(g)))
            except (ValueError, TypeError):
                continue
        if not norm_ids:
            return []

        agent_ids: list[str] = []
        seen: set[str] = set()

        def _add(value: Any) -> None:
            s = str(value)
            if s not in seen:
                seen.add(s)
                agent_ids.append(s)

        # Explicit members (defensively space-scoped).
        rows = await self.db.execute(
            select(AgentGroupMember.agent_id).where(
                AgentGroupMember.group_id.in_(norm_ids),
                AgentGroupMember.space_id == space_uuid,
            )
        )
        for (member_agent_id,) in rows.all():
            _add(member_agent_id)

        # Smart groups: resolve simple dynamic rules over agents in the space.
        dyn_res = await self.db.execute(
            select(AgentGroup).where(
                AgentGroup.id.in_(norm_ids),
                AgentGroup.is_dynamic.is_(True),
                AgentGroup.space_id == space_uuid,
            )
        )
        dyn_groups = dyn_res.scalars().all()
        if dyn_groups:
            type_filters: set[str] = set()
            want_all = False
            for grp in dyn_groups:
                rules = grp.dynamic_rules or {}
                if rules.get("all") is True:
                    want_all = True
                at = rules.get("agent_type")
                if isinstance(at, str):
                    type_filters.add(at)
                elif isinstance(at, (list, tuple, set)):
                    type_filters.update(str(x) for x in at)
            if want_all or type_filters:
                stmt = select(Agent.id).where(
                    Agent.id.in_(agents_in_space_subquery(space_uuid)),
                    Agent.status == "active",
                )
                if not want_all and type_filters:
                    stmt = stmt.where(Agent.agent_type.in_(type_filters))
                arows = await self.db.execute(stmt)
                for (aid,) in arows.all():
                    _add(aid)

        return agent_ids

    async def _coerce_mentions_to_handles(self, mentions: list[Any] | None) -> list[str]:
        """Convert mention metadata entries into handle strings for mention notifications."""
        if not mentions:
            return []

        handles: list[str] = []
        seen: set[str] = set()
        agent_ids: list[UUID] = []

        for entry in mentions:
            if isinstance(entry, str):
                handle = entry.strip().lstrip("@").lower()
                if not handle:
                    continue
                normalized_id = self._normalize_agent_id(handle)
                if normalized_id:
                    try:
                        agent_ids.append(UUID(normalized_id))
                    except (ValueError, TypeError):
                        continue
                elif handle not in seen:
                    seen.add(handle)
                    handles.append(handle)
                continue

            if isinstance(entry, dict):
                agent_id = self._normalize_agent_id(entry.get("agent_id"))
                if agent_id:
                    try:
                        agent_ids.append(UUID(agent_id))
                    except (ValueError, TypeError):
                        pass
                    continue

                handle = str(entry.get("handle") or entry.get("agent_name") or "").strip().lstrip("@").lower()
                if handle and handle not in seen:
                    seen.add(handle)
                    handles.append(handle)

        if agent_ids:
            result = await self.db.execute(select(Agent.name).where(Agent.id.in_(agent_ids)))
            for name in result.scalars().all():
                handle = (name or "").strip().lower()
                if handle and handle not in seen:
                    seen.add(handle)
                    handles.append(handle)

        return handles

    @staticmethod
    def _normalize_router_text(value: str | None) -> str:
        if not value:
            return ""
        lowered = value.lower()
        lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    @staticmethod
    def _token_overlap_ratio(a: str, b: str) -> float:
        a_tokens = {t for t in a.split() if len(t) > 2}
        b_tokens = {t for t in b.split() if len(t) > 2}
        if not a_tokens or not b_tokens:
            return 0.0
        inter = len(a_tokens & b_tokens)
        union = len(a_tokens | b_tokens)
        if union == 0:
            return 0.0
        return inter / union

    async def _resolve_org_default_router_agent_id(self, space_id: UUID) -> str | None:
        fallback_router_handle = (
            os.getenv("DEFAULT_ROUTER_AGENT_HANDLE", "project_lead_ai")
            .strip()
            .lstrip("@")
            .lower()
        )
        if not fallback_router_handle:
            return None

        from app.core.agent_space import agents_in_space_subquery
        fallback_router_res = await self.db.execute(
            select(Agent.id)
            .where(Agent.id.in_(agents_in_space_subquery(space_id)))
            .where(func.lower(Agent.name) == fallback_router_handle)
            .where(Agent.status == "active")
        )
        value = fallback_router_res.scalar()
        return str(value) if value else None

    async def _collect_router_context_messages(
        self,
        *,
        space_id: UUID,
        channel: str,
        parent_id: UUID | None,
        limit: int = 6,
    ) -> list[dict[str, str]]:
        """Collect recent message context for context-aware routing.

        Preference:
        1) Thread ancestry (for replies), newest->oldest up to limit.
        2) Otherwise, recent channel messages (latest first, then reversed to oldest->newest).

        We intentionally keep this minimal and deterministic: only content + speaker role, no extra payload.
        """
        history_limit = max(1, min(limit, 12))
        context: list[dict[str, str]] = []

        def _append_entry(*, speaker: str, text: str) -> None:
            clean = (text or "").strip()
            if not clean:
                return
            context.append({"speaker": speaker, "content": clean[:220]})

        if parent_id:
            current_id: UUID | None = parent_id
            hops = 0
            seen_ids: set[UUID] = set()

            while current_id and hops < history_limit:
                parent_row = await self.db.execute(
                    select(Message.content, Message.agent_id, Message.user_id, Message.parent_id)
                    .where(Message.id == current_id)
                    .where(Message.space_id == space_id)
                    .where(Message.channel == channel)
                    .where(Message.message_type != "reaction")
                    .where(exclude_ui_only_no_reply_clause())
                )
                parent_msg = parent_row.first()
                if not parent_msg:
                    break

                pcontent, pagent_id, puser_id, pparent_id = parent_msg
                if current_id in seen_ids:
                    break
                seen_ids.add(current_id)

                if pagent_id:
                    _append_entry(speaker=f"agent({str(pagent_id)[:8]})", text=pcontent or "")
                else:
                    _append_entry(speaker=f"human({str(puser_id)[:8]})", text=pcontent or "")

                current_id = pparent_id if isinstance(pparent_id, UUID) else None
                hops += 1

            context.reverse()
        else:
            history_rows = await self.db.execute(
                select(Message.content, Message.agent_id, Message.user_id)
                .where(Message.space_id == space_id)
                .where(Message.channel == channel)
                .where(Message.message_type != "reaction")
                .where(exclude_ui_only_no_reply_clause())
                .order_by(desc(Message.created_at))
                .limit(history_limit)
            )
            for row in reversed(history_rows.all()):
                h_content, h_agent_id, h_user_id = row
                if h_agent_id:
                    _append_entry(speaker=f"agent({str(h_agent_id)[:8]})", text=h_content or "")
                else:
                    _append_entry(speaker=f"human({str(h_user_id)[:8]})", text=h_content or "")

        return context

    async def _infer_router_mentions(
        self,
        *,
        space_id: UUID,
        content: str,
        routing_context: list[dict[str, str]] | None = None,
        sender_context: dict[str, str] | None = None,
    ) -> list[dict]:
        """
        Run router-style agent classification and return normalized mention objects.

        Behavior:
        - Use active agents in the org plus pinned/global agents (matching router behavior).
        - Prefer dispatchable agents for classification, with fallback to all active agents.
        - Never hard-fail send on classify failures; caller handles fallback.
        - Optional context gives the router prior messages for better routing confidence.
        """
        from app.api.v1.router import ROUTER_FAN_OUT_CAP, classify_intent

        candidate_agents = await self._get_router_candidate_agents(space_id=space_id)
        if not candidate_agents:
            return []

        context_lines: list[str] = []
        if routing_context:
            for item in routing_context[-16:]:
                speaker = (item.get("speaker") or "unknown").strip() if isinstance(item, dict) else "unknown"
                snippet = (item.get("content") or "").replace("\n", " ") if isinstance(item, dict) else ""
                snippet = snippet.strip()
                if snippet:
                    context_lines.append(f"- {speaker}: {snippet[:300]}")

        context_window = "\n".join(context_lines)

        classified = await classify_intent(
            content,
            candidate_agents,
            context_window=context_window or None,
            sender_identity=sender_context,
        )

        candidate_lookup = {
            str(agent.get("name", "")).strip().lstrip("@").lower(): str(agent.get("id"))
            for agent in candidate_agents
            if isinstance(agent, dict) and agent.get("name") and agent.get("id")
        }

        normalized: list[dict] = []
        for item in self._iter_router_classified_items(classified)[: ROUTER_FAN_OUT_CAP]:
            if not isinstance(item, dict):
                continue

            agent_id = self._normalize_agent_id(item.get("agent_id"))
            if not agent_id:
                raw_agent_id = item.get("agent_id")
                if isinstance(raw_agent_id, str):
                    agent_id = candidate_lookup.get(raw_agent_id.strip().lstrip("@").lower())

            if not agent_id:
                agent_name = item.get("agent_name")
                if isinstance(agent_name, str):
                    agent_id = candidate_lookup.get(agent_name.strip().lstrip("@").lower())

            if not agent_id:
                continue

            normalized.append(
                {
                    "agent_id": str(agent_id),
                    "agent_name": item.get("agent_name") or item.get("name"),
                    "role": item.get("role"),
                    "reason": item.get("reason"),
                    "source": "router_classified",
                }
            )

        return normalized

    def _iter_router_classified_items(self, classified: object) -> list[dict | list[dict]]:
        """Yield router classification entries, flattening one level of nested lists.

        Bedrock/Gemini occasionally wrap responses as [[...]] or nested arrays.
        """
        if not isinstance(classified, list):
            return []

        items: list[dict | list[dict]] = []
        for item in classified:
            if isinstance(item, list):
                items.extend(item)
            else:
                items.append(item)

        return items

    async def _get_router_candidate_agents(self, *, space_id: UUID) -> list[dict]:
        from app.models.agent_space_access import AgentSpaceAccess
        rows = await self.db.execute(
            select(Agent)
            .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
            .where(
                and_(
                    Agent.status == "active",
                    or_(
                        AgentSpaceAccess.space_id == space_id,
                        Agent.visibility_level == "global",
                    ),
                    AgentSpaceAccess.state == "active",  # Skip suspended/detached space access records
                )
            )
        )
        all_agents = rows.scalars().all()

        if not all_agents:
            return []

        dispatchable_agents = [
            a
            for a in all_agents
            if (a.cloud_function_url is not None
                or (a.origin == "external_gateway" and a.webhook_url is not None)
                or a.origin == "space_agent"
                or a.origin == "agentcore")
        ]
        available_agents = dispatchable_agents if dispatchable_agents else all_agents

        agents: list[dict] = []
        for a in available_agents:
            caps_raw = a.capabilities if a.capabilities is not None else {}
            if isinstance(caps_raw, str):
                try:
                    import json
                    caps_raw = json.loads(caps_raw)
                except Exception:
                    caps_raw = {}
            caps = caps_raw if isinstance(caps_raw, dict) else {}
            description = caps.get("routing_hint")
            if not description:
                description = a.description or (a.system_prompt[:100] if a.system_prompt else "")
            if not description:
                description = a.specialization or "general assistant"
            agents.append(
                {
                    "id": str(a.id),
                    "name": a.name,
                    "description": description,
                    "origin": a.origin,
                }
            )

        return agents

    async def _select_keepalive_teammate_agent_id(
        self,
        *,
        space_id: UUID,
        content: str,
        exclude_agent_ids: list[str] | None = None,
    ) -> str | None:
        """
        Pick next eligible teammate for GO-mode continuation.

        Eligibility (org-agnostic policy):
        - active, non-internal agent in same org
        - not MCP-origin
        - has autonomous dispatch capability (cloud_function_url or webhook_url)
        """
        excluded = {
            self._normalize_agent_id(v)
            for v in (exclude_agent_ids or [])
            if self._normalize_agent_id(v)
        }

        from app.core.agent_space import agents_in_space_subquery
        rows = await self.db.execute(
            select(Agent.id, Agent.name, Agent.specialization)
            .where(Agent.id.in_(agents_in_space_subquery(space_id)))
            .where(Agent.status == "active")
            .where(Agent.is_internal.is_(False))
            .where(or_(Agent.origin.is_(None), func.lower(Agent.origin) != "mcp"))
            .where(
                or_(
                    and_(Agent.cloud_function_url.isnot(None), Agent.cloud_function_url != ""),
                    and_(Agent.webhook_url.isnot(None), Agent.webhook_url != ""),
                )
            )
            .limit(25)
        )

        candidates = rows.all()
        if not candidates:
            return None

        content_norm = self._normalize_router_text(content)

        best_id: str | None = None
        best_score = -1.0

        for cid, cname, cspec in candidates:
            norm_id = self._normalize_agent_id(cid)
            if not norm_id or norm_id in excluded:
                continue

            profile = self._normalize_router_text(f"{cname or ''} {cspec or ''}")
            score = self._token_overlap_ratio(content_norm, profile)

            # Deterministic slight preference for specialized agents when overlap ties.
            if score > best_score:
                best_score = score
                best_id = norm_id

        return best_id

    async def _evaluate_thread_continuation(
        self,
        *,
        parent_id: UUID,
        actor_id: UUID,
        content: str,
        space_id: UUID,
        channel: str,
    ) -> dict[str, Any]:
        """
        Evaluate continuation policy for inferred thread routing.

        Policy intent:
        - forward by default
        - mention-add only explicit (handled upstream by explicit target parsing)
        - max N auto-turns then escalate
        - detect repetitive/loop/drift patterns and stop/escalate
        """
        max_auto_turns = max(1, int(os.getenv("ROUTER_AUTO_TURN_CAP", "3")))
        max_history = max(8, max_auto_turns * 3)

        chain: list[dict[str, Any]] = []
        current_id: UUID | None = parent_id

        for _ in range(max_history):
            if not current_id:
                break
            row_res = await self.db.execute(
                select(
                    Message.id,
                    Message.parent_id,
                    Message.agent_id,
                    Message.content,
                    Message.message_metadata,
                )
                .where(Message.id == current_id)
                .where(Message.space_id == space_id)
                .where(Message.channel == channel)
            )
            row = row_res.first()
            if not row:
                break

            chain.append(
                {
                    "id": row[0],
                    "parent_id": row[1],
                    "agent_id": row[2],
                    "content": row[3] or "",
                    "metadata": row[4] or {},
                }
            )
            current_id = row[1]

        if not chain:
            return {
                "action": "forward_parent",
                "reason": "no_history",
                "parent_agent_id": None,
                "prospective_auto_turns": 1,
            }

        parent_agent_id = chain[0].get("agent_id")

        # Count consecutive inferred auto-routing turns from parent backwards.
        inferred_streak = 0
        for item in chain:
            md = item.get("metadata") or {}
            if md.get("route_inferred"):
                inferred_streak += 1
            else:
                break

        prospective_auto_turns = inferred_streak + 1

        new_text = self._normalize_router_text(content)
        parent_text = self._normalize_router_text(chain[0].get("content"))

        # Repetition check: exact normalized duplicate with direct parent
        repetitive = bool(new_text and parent_text and new_text == parent_text)

        # Loop check: ABAB pattern in last 4 agent turns (including current actor)
        agent_seq: list[str] = [str(actor_id)]
        for item in chain:
            aid = item.get("agent_id")
            if aid:
                agent_seq.append(str(aid))
            if len(agent_seq) >= 5:
                break

        loop_detected = (
            len(agent_seq) >= 4
            and agent_seq[0] == agent_seq[2]
            and agent_seq[1] == agent_seq[3]
            and agent_seq[0] != agent_seq[1]
        )

        # Drift heuristic: low overlap with both parent and root while thread keeps going.
        root_text = self._normalize_router_text(chain[-1].get("content"))
        overlap_parent = self._token_overlap_ratio(new_text, parent_text)
        overlap_root = self._token_overlap_ratio(new_text, root_text)
        drift_detected = (
            prospective_auto_turns >= 2
            and len(new_text.split()) >= 6
            and overlap_parent < 0.08
            and overlap_root < 0.08
        )

        reason = "continue"
        action = "forward_parent"

        if prospective_auto_turns > max_auto_turns:
            reason = "turn_cap"
            action = "escalate_router"
        elif loop_detected:
            reason = "loop_detected"
            action = "escalate_router"
        elif repetitive:
            reason = "repetition_detected"
            action = "escalate_router"
        elif drift_detected:
            reason = "drift_detected"
            action = "escalate_router"

        # GO mode fallback: if continuing but parent is unavailable/self, choose a keepalive teammate.
        keepalive_agent_id = None
        normalized_parent_agent_id = self._normalize_agent_id(parent_agent_id)
        if action == "forward_parent" and (
            not normalized_parent_agent_id or normalized_parent_agent_id == str(actor_id)
        ):
            keepalive_agent_id = await self._select_keepalive_teammate_agent_id(
                space_id=space_id,
                content=content,
                exclude_agent_ids=[str(actor_id), str(normalized_parent_agent_id or "")],
            )
            if keepalive_agent_id:
                action = "keepalive_teammate"
                reason = "productive_keepalive"

        router_agent_id = None
        if action == "escalate_router":
            router_agent_id = await self._resolve_org_default_router_agent_id(space_id)
            if router_agent_id and router_agent_id == str(actor_id):
                # Router cannot escalate back to itself in this path.
                action = "stop"
                reason = f"{reason}_router_self"
                router_agent_id = None
            elif not router_agent_id:
                action = "stop"
                reason = f"{reason}_no_router"

        conversation_root_id = str(chain[-1].get("id")) if chain and chain[-1].get("id") else None
        turns_used = prospective_auto_turns

        recommended_owner = None
        if action == "keepalive_teammate":
            recommended_owner = keepalive_agent_id
        elif action == "escalate_router":
            recommended_owner = router_agent_id
        elif action == "forward_parent":
            recommended_owner = str(normalized_parent_agent_id) if normalized_parent_agent_id else None
        elif action == "stop":
            recommended_owner = router_agent_id or await self._resolve_org_default_router_agent_id(space_id)

        return {
            "action": action,
            "reason": reason,
            "parent_agent_id": str(normalized_parent_agent_id) if normalized_parent_agent_id else None,
            "router_agent_id": router_agent_id,
            "keepalive_agent_id": keepalive_agent_id,
            "prospective_auto_turns": prospective_auto_turns,
            "turns_used": turns_used,
            "conversation_root_id": conversation_root_id,
            "recommended_owner": recommended_owner,
            "loop_detected": loop_detected,
            "repetitive": repetitive,
            "drift_detected": drift_detected,
        }

    def _validate_agent_content_guard_rails(self, *, content: str, actor: Actor, author_display_name: str) -> None:
        """
        Apply content guard rails for agent messages.

        Extensible validation system to prevent unwanted agent behaviors:
        - Spam patterns (future)
        - Prohibited content (future)
        - Rate limit triggers (future)

        Args:
            content: Message content to validate
            actor: Actor sending the message
            author_display_name: Display name of the author

        Raises:
            ValueError: If content violates any guard rail
        """
        if actor.type != "agent":
            return  # Guard rails only apply to agents

        logger = logging.getLogger(__name__)
        # Self-reference in content is allowed. Loop prevention belongs in
        # mention extraction and dispatch, where self-targeting delivery can be
        # skipped without blocking legitimate content.
        logger.info(
            "✅ GUARD RAIL: agent content accepted agent=%s preview=%s",
            author_display_name,
            (content or "")[:100],
        )

        # Guard Rail 2: Future - Spam detection
        # if self._detect_spam_pattern(content):
        #     raise ValueError("Spam pattern detected")

        # Guard Rail 3: Future - Prohibited content
        # if self._contains_prohibited_content(content):
        #     raise ValueError("Prohibited content detected")

    async def send(
        self,
        *,
        actor: Actor,
        content: str,
        channel: str = "main",
        message_type: str = "message",
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        author_display_name: str,  # caller computes (username/full_name/agent_name)
        adapter: str = "unknown",  # "api" or "mcp" for parity tracking
        before_broadcast: Callable[[Message], Awaitable[None]] | None = None,
    ) -> Message:
        """
        Send a message with unified business logic.
        Used by both API and MCP endpoints.
        """
        print(f"📨 MESSAGES_SERVICE.SEND called: actor={actor.id}, adapter={adapter}, content_preview={content[:50] if content else 'None'}...")

        # Permission check
        actor.require(CAP_MESSAGES_SEND)

        # Validate content
        if not content or not content.strip():
            raise ValueError("Message content cannot be empty")

        if len(content) > 10000:
            raise ValueError("Message content exceeds maximum length of 10000 characters")

        # Apply agent content guard rails (extensible validation system)
        self._validate_agent_content_guard_rails(content=content, actor=actor, author_display_name=author_display_name)

        # Parent validation (org/channel guard)
        pid = await self.core.validate_parent(space_id=actor.space_id, channel=channel, parent_id=parent_id)

        # Self-reply prevention: agents and users cannot reply to their own messages.
        # This prevents loops where an agent responds to itself and also prevents
        # accidental user self-replies in the UI.
        if pid:
            parent_res = await self.db.execute(
                select(Message.agent_id, Message.user_id).where(Message.id == pid)
            )
            parent_row = parent_res.one_or_none()
            if parent_row:
                parent_agent_id, parent_user_id = parent_row
                sender_agent_id = getattr(actor, "agent_id", None) or (
                    actor.id if actor.type == "agent" else None
                )
                sender_user_id = actor.id if actor.type != "agent" else None
                if sender_agent_id and parent_agent_id and str(sender_agent_id) == str(parent_agent_id):
                    logging.getLogger(__name__).warning(
                        "SELF_REPLY_BLOCKED agent_id=%s message_id=%s parent_id=%s",
                        sender_agent_id, "pending", pid,
                    )
                    raise ValueError("Cannot reply to your own message")
                if sender_user_id and parent_user_id and str(sender_user_id) == str(parent_user_id):
                    logging.getLogger(__name__).info(
                        "SELF_REPLY_BLOCKED user_id=%s parent_id=%s", sender_user_id, pid,
                    )
                    raise ValueError("Cannot reply to your own message")

        # Check if it's an emoji reaction
        is_reaction = await self.core.is_emoji_reaction(content) if pid else False
        if is_reaction:
            message_type = "reaction"
            # Add metadata flag for extra clarity
            if not metadata:
                metadata = {}
            metadata["is_reaction"] = True

        # Normalize metadata container and capture mentions once so every path stays consistent.
        # Important contract:
        # - primary_agent_id = sticky selected recipient (primary route)
        # - mentioned_agent_ids[] = additive recipients (secondary routes)
        normalized_metadata: dict[str, Any] = dict(metadata or {})
        # Server-owned routing flag. Clients may supply primary_agent_id,
        # mentioned_agent_ids, or mentioned_group_ids; those explicit target
        # fields below are the only inputs allowed to make a metadata-only
        # mention eligible for direct dispatch bypass.
        normalized_metadata.pop("direct_route_eligible", None)
        extracted_mentions = await self.core.extract_mentions(content or "")
        if extracted_mentions:
            normalized_metadata["mentions"] = extracted_mentions

        primary_agent_id = normalized_metadata.pop("primary_agent_id", None)
        mentioned_agent_ids = normalized_metadata.pop("mentioned_agent_ids", None)

        # Agent groups: expand mentioned_group_ids into individual member agent
        # ids so "send to a group" rides the normal mention fan-out. Resolution
        # is scoped to the sender's space (see _expand_group_mentions).
        mentioned_group_ids = normalized_metadata.pop("mentioned_group_ids", None)
        if mentioned_group_ids:
            group_member_ids = await self._expand_group_mentions(
                mentioned_group_ids, actor.space_id
            )
            if group_member_ids:
                combined = list(mentioned_agent_ids) if isinstance(mentioned_agent_ids, (list, tuple, set)) else (
                    [mentioned_agent_ids] if mentioned_agent_ids else []
                )
                combined.extend(group_member_ids)
                mentioned_agent_ids = combined

        merged_mentions, explicit_targets_present = self._merge_routing_mentions(
            existing_mentions=normalized_metadata.get("mentions", []),
            primary_agent_id=primary_agent_id,
            mentioned_agent_ids=mentioned_agent_ids,
        )
        if merged_mentions:
            normalized_metadata["mentions"] = merged_mentions
        else:
            normalized_metadata.pop("mentions", None)

        if explicit_targets_present:
            normalized_metadata["direct_route_eligible"] = True

        routing_mentions = normalized_metadata.get("mentions", [])

        # THREAD REPLY ROUTING: if any sender replies in-thread without explicit
        # routing targets, run continuation policy first (continue/stop/escalate).
        if (
            not routing_mentions
            and pid
            and message_type != "reaction"
        ):
            continuation = await self._evaluate_thread_continuation(
                parent_id=pid,
                actor_id=actor.id,
                content=content,
                space_id=actor.space_id,
                channel=channel,
            )

            normalized_metadata["continuation_decision"] = continuation.get("action")
            normalized_metadata["continuation_reason"] = continuation.get("reason")
            normalized_metadata["continuation_auto_turns"] = continuation.get("prospective_auto_turns")
            normalized_metadata["continuation_summary"] = {
                "conversation_id": continuation.get("conversation_root_id"),
                "reason": continuation.get("reason"),
                "turns_used": continuation.get("turns_used") or continuation.get("prospective_auto_turns"),
                "accomplished": "",
                "open_items": "",
                "recommended_owner": continuation.get("recommended_owner"),
            }

            decision = continuation.get("action")
            if decision == "forward_parent":
                parent_agent_id = self._normalize_agent_id(continuation.get("parent_agent_id"))
                if parent_agent_id and str(parent_agent_id) != str(actor.id):
                    parent_active_res = await self.db.execute(
                        select(Agent.id).where(Agent.id == UUID(parent_agent_id), Agent.status == "active")
                    )
                    if parent_active_res.scalar():
                        normalized_metadata["mentions"] = [{"agent_id": str(parent_agent_id), "source": "thread_parent"}]
                        normalized_metadata["route_inferred"] = True
                        routing_mentions = normalized_metadata["mentions"]
                        logging.getLogger(__name__).info(
                            "🔀 THREAD ROUTE: Agent %s → parent agent %s (reply without explicit route)",
                            actor.id,
                            parent_agent_id,
                        )
            elif decision == "keepalive_teammate":
                teammate_agent_id = self._normalize_agent_id(continuation.get("keepalive_agent_id"))
                if teammate_agent_id:
                    normalized_metadata["mentions"] = [{"agent_id": str(teammate_agent_id), "source": "keepalive_teammate"}]
                    normalized_metadata["route_inferred"] = True
                    normalized_metadata["continuation_keepalive"] = True
                    routing_mentions = normalized_metadata["mentions"]
                    logging.getLogger(__name__).info(
                        "✅ CONTINUATION KEEPALIVE: Agent %s → teammate %s (reason=%s)",
                        actor.id,
                        teammate_agent_id,
                        continuation.get("reason"),
                    )
            elif decision == "escalate_router":
                router_agent_id = self._normalize_agent_id(continuation.get("router_agent_id"))
                if router_agent_id:
                    normalized_metadata["mentions"] = [{"agent_id": str(router_agent_id), "source": "continuation_escalation"}]
                    normalized_metadata["route_inferred"] = True
                    normalized_metadata["continuation_escalated"] = True
                    routing_mentions = normalized_metadata["mentions"]
                    logging.getLogger(__name__).warning(
                        "🛑 CONTINUATION ESCALATE: Agent %s → router %s (reason=%s)",
                        actor.id,
                        router_agent_id,
                        continuation.get("reason"),
                    )
            else:
                normalized_metadata["continuation_stopped"] = True
                logging.getLogger(__name__).warning(
                    "🛑 CONTINUATION STOP: Agent %s (reason=%s)",
                    actor.id,
                    continuation.get("reason"),
                )

        if explicit_targets_present and routing_mentions:
            logging.getLogger(__name__).info(
                "🔀 EXPLICIT ROUTE TARGETS: sender=%s targets=%s",
                actor.id,
                routing_mentions,
            )

        # AUTO ROUTING via classifier: if no explicit/continuation targets,
        # infer recipients from router classifier.
        # Feature flag: ROUTER_AUTO_CLASSIFY (default: false).
        # When disabled, messages without explicit @mentions go to aX instead.
        router_auto_classify = os.getenv("ROUTER_AUTO_CLASSIFY", "false").lower() in ("true", "1", "yes")
        if (
            router_auto_classify
            and not routing_mentions
            and message_type != "reaction"
            and not normalized_metadata.get("continuation_stopped")
            and self._allows_implicit_routing(normalized_metadata)
        ):
            try:
                routing_context = await self._collect_router_context_messages(
                    space_id=actor.space_id,
                    channel=channel,
                    parent_id=pid,
                    limit=int(os.getenv("ROUTER_HISTORY_WINDOW", "25")),
                )

                # Enrich context with active agent roster (specialization + status)
                from app.core.agent_space import agents_in_space_subquery
                agent_roster_res = await self.db.execute(
                    select(Agent.name, Agent.specialization, Agent.status)
                    .where(Agent.id.in_(agents_in_space_subquery(actor.space_id)))
                    .where(Agent.status == "active")
                    .order_by(Agent.name)
                )
                agent_roster_rows = agent_roster_res.all()
                if agent_roster_rows:
                    roster_lines = [
                        f"- {row[0]} ({row[1] or 'general'}): {row[2]}"
                        for row in agent_roster_rows
                    ]
                    routing_context.append({
                        "speaker": "[workspace_agents]",
                        "content": "\n".join(roster_lines),
                    })

                router_mentions = await self._infer_router_mentions(
                    space_id=actor.space_id,
                    content=content,
                    routing_context=routing_context,
                    sender_context={
                        "actor_id": str(actor.id),
                        "actor_type": actor.type,
                        "actor_name": author_display_name,
                    },
                )
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.warning(
                    "⚠️ ROUTER_CLASSIFY_SKIPPED: sender=%s error=%s",
                    actor.id,
                    e,
                )
                router_mentions = []

            if router_mentions:
                normalized_metadata["mentions"] = router_mentions
                normalized_metadata["route_inferred"] = True
                normalized_metadata["router_inferred"] = True
                routing_mentions = normalized_metadata["mentions"]
                logging.getLogger(__name__).info(
                    "🧭 ROUTER AUTO ROUTE: sender=%s inferred_targets=%s",
                    actor.id,
                    router_mentions,
                )

        # DEFAULT ROUTING (unified): if no routing targets remain, route via default_route_to.
        # - Human senders: membership default for the human user.
        # - Agent senders (MCP/webhook/cloud): membership default for the agent owner.
        if (
            not routing_mentions
            and message_type != "reaction"
            and not normalized_metadata.get("continuation_stopped")
            and self._allows_implicit_routing(normalized_metadata)
        ):
            route_user_id = None
            route_source = None

            if actor.type in ("user", "human"):
                route_user_id = actor.id
                route_source = "default_route"
            elif actor.type == "agent":
                owner_res = await self.db.execute(
                    select(Agent.user_id).where(Agent.id == actor.id)
                )
                route_user_id = owner_res.scalar()
                route_source = "owner_default_route"

            default_agent_id = None

            if route_user_id:
                default_route_result = await self.db.execute(
                    select(SpaceMembership.default_route_to)
                    .where(SpaceMembership.user_id == route_user_id)
                    .where(SpaceMembership.space_id == actor.space_id)
                )
                default_agent_id = default_route_result.scalar()

            # Space Agent fallback: check org.space_agent_id first
            # This is the canonical default handler for every space.
            if not default_agent_id:
                from app.models.space import Space
                from app.services.space_agent_service import ensure_space_agent_for_org

                org = await self.db.get(Space, actor.space_id)
                space_agent = await ensure_space_agent_for_org(self.db, org) if org else None
                if space_agent:
                    # Verify Space Agent is active before routing
                    sa_active = await self.db.execute(
                        select(Agent.id).where(
                            Agent.id == space_agent.id,
                            Agent.status == "active",
                        )
                    )
                    if sa_active.scalar():
                        default_agent_id = str(space_agent.id)
                        route_source = "space_agent_default"

            # Org-level fallback router (designated or system default)
            # Used when sender/owner has no personal default_route_to AND no Space Agent.
            if not default_agent_id:
                default_agent_id = await self._resolve_org_default_router_agent_id(actor.space_id)
                if default_agent_id:
                    route_source = "org_default_router"

            resolved_default_agent_id = self._normalize_agent_id(default_agent_id)
            if resolved_default_agent_id:
                # Verify agent is still active before routing
                agent_active = await self.db.execute(
                    select(Agent.id).where(Agent.id == UUID(resolved_default_agent_id), Agent.status == "active")
                )
                if agent_active.scalar():
                    normalized_metadata["mentions"] = [{"agent_id": str(resolved_default_agent_id), "source": route_source}]
                    normalized_metadata["default_routed"] = True
                    routing_mentions = normalized_metadata["mentions"]
                    logging.getLogger(__name__).info(
                        "🔀 DEFAULT ROUTE: sender=%s route_user=%s → agent %s (source=%s)",
                        actor.id,
                        route_user_id,
                        resolved_default_agent_id,
                        route_source,
                    )
                else:
                    logging.getLogger(__name__).warning(
                        f"⚠️ DEFAULT ROUTE SKIPPED: Agent {resolved_default_agent_id} is not active"
                    )
        # Append routing story directly into message metadata for client visibility.
        # This guarantees sender can see where their message was routed immediately.
        routing_targets: list[dict] = []
        routing_target_ids: list[str] = []

        if routing_mentions and isinstance(routing_mentions, list):
            for item in routing_mentions:
                if not isinstance(item, dict):
                    continue
                agent_id = self._normalize_agent_id(item.get("agent_id"))
                if not agent_id:
                    continue

                routing_target_ids.append(str(agent_id))
                routing_targets.append(
                    {
                        "agent_id": str(agent_id),
                        "source": item.get("source") or "routed",
                    }
                )

        target_name_map: dict[str, str] = {}
        if routing_target_ids:
            name_rows = await self.db.execute(
                select(Agent.id, Agent.name).where(Agent.id.in_([UUID(x) for x in routing_target_ids]))
            )
            target_name_map = {str(row.id): row.name for row in name_rows}

        for target in routing_targets:
            aid = target.get("agent_id")
            if not isinstance(aid, str):
                continue
            # Keep classifier-supplied name if present; otherwise hydrate from DB.
            explicit_name = None
            if isinstance(routing_targets, list):
                for item in routing_mentions:
                    if isinstance(item, dict) and self._normalize_agent_id(item.get("agent_id")) == aid:
                        explicit_name = item.get("agent_name")
                        if explicit_name:
                            break
            target["agent_name"] = explicit_name or target_name_map.get(aid, "unknown")
            target["display_name"] = f"@{target.get('agent_name')}" if isinstance(target.get('agent_name'), str) else f"@{aid}"

        routing_story = {
            "targets": routing_targets,
            "route_inferred": bool(normalized_metadata.get("route_inferred")),
            "default_routed": bool(normalized_metadata.get("default_routed")),
            "route_continuation": bool(normalized_metadata.get("continuation_source") or normalized_metadata.get("continuation_stopped") is False),
            "continuation_source": normalized_metadata.get("continuation_decision"),
        }

        routing_story["summary"] = []
        for item in routing_targets:
            if not isinstance(item, dict):
                continue
            routing_story["summary"].append(item.get("display_name"))

        if routing_targets:
            normalized_metadata["routing_story"] = routing_story
        else:
            normalized_metadata.pop("routing_story", None)


        # Rate limiting for agents (cloud → cloud only)
        # DEBUG: Log actor type to confirm we are entering this block
        logging.getLogger(__name__).info(f"🔍 RATE LIMIT CHECK: Actor type={actor.type}, ID={actor.id}")

        if actor.type == "agent":
            try:
                # Sender details
                agent_owner_id = None
                cloud_function_url = None
                agent_res = await self.db.execute(
                    select(Agent.user_id, Agent.cloud_function_url).where(Agent.id == actor.id)
                )
                result = agent_res.first()
                if result:
                    agent_owner_id = result[0]
                    cloud_function_url = result[1]

                is_cloud_sender = cloud_function_url is not None and cloud_function_url.strip() != ""

                # Resolve cloud recipients from routing mentions
                cloud_recipient_ids = set()
                if routing_mentions:
                    mentioned_agent_ids = [
                        m["agent_id"] for m in routing_mentions if isinstance(m, dict) and m.get("agent_id")
                    ]
                    if mentioned_agent_ids:
                        mentioned_res = await self.db.execute(
                            select(Agent.id, Agent.cloud_function_url).where(Agent.id.in_(mentioned_agent_ids))
                        )
                        for agent_id, agent_cloud_url in mentioned_res:
                            if agent_cloud_url and str(agent_cloud_url).strip() != "":
                                cloud_recipient_ids.add(str(agent_id))

                # Include parent author if it's a cloud agent (thread replies)
                if pid:
                    parent_agent_res = await self.db.execute(select(Message.agent_id).where(Message.id == pid))
                    parent_agent_id = parent_agent_res.scalar()
                    if parent_agent_id:
                        parent_cloud_res = await self.db.execute(
                            select(Agent.cloud_function_url).where(Agent.id == parent_agent_id)
                        )
                        parent_cloud_url = parent_cloud_res.scalar()
                        if parent_cloud_url and str(parent_cloud_url).strip() != "":
                            cloud_recipient_ids.add(str(parent_agent_id))

                logging.getLogger(__name__).info(
                    f"🔍 RATE LIMIT DETAILS: Agent={actor.id}, Owner={agent_owner_id}, "
                    f"IsCloudSender={is_cloud_sender}, CloudRecipients={list(cloud_recipient_ids)}"
                )

                # Only rate limit when sender is cloud AND at least one cloud recipient (cloud→cloud)
                if not is_cloud_sender or not cloud_recipient_ids:
                    logging.getLogger(__name__).info(
                        f"⏭️  SKIPPING RATE LIMIT: sender_cloud={is_cloud_sender}, cloud_targets={len(cloud_recipient_ids)}"
                    )
                else:
                    tier = "free"
                    org_res = await self.db.execute(select(Space.tier).where(Space.id == actor.space_id))
                    tier = (org_res.scalar() or "free").lower()

                    outcome = await self.limiter.check_and_increment(
                        agent_id=str(actor.id),
                        space_id=str(actor.space_id),
                        user_id=str(agent_owner_id) if agent_owner_id else None,
                        sender_is_human=False,
                        tier=tier,
                    )

                    logging.getLogger(__name__).info(
                        f"🔍 RATE LIMIT OUTCOME: Blocked={outcome.blocked}, Reason={outcome.reason}, Count={outcome.limit}"
                    )

                    if outcome.blocked:
                        retry_after = outcome.retry_after_seconds or 60
                        reason = outcome.reason or "limit"
                        limit = outcome.limit or 0

                        msg = f"Rate limit exceeded. Try again in {retry_after}s."
                        if reason == "daily_user":
                            msg = f"Daily message limit reached for this user's agents ({limit})."
                        elif reason == "burst":
                            msg = f"Burst limit reached ({limit} messages). Slow down."
                        elif reason == "sustained":
                            msg = f"Sustained rate limit reached ({limit} messages). Take a break."

                        logging.getLogger(__name__).warning(
                            f"⛔ RATE LIMIT BLOCK: Agent {actor.id} blocked ({reason}). {msg}"
                        )
                        raise ValueError(msg)

            except ValueError:
                raise  # Re-raise the blocking error
            except Exception as e:
                # Fail open on infra errors, but log
                logging.getLogger(__name__).error(f"❌ RATE LIMIT ERROR: {e}", exc_info=True)

        # Create message with explicit adapter branching to avoid mis-attribution
        # IMPORTANT: For API (human) sends, ALWAYS persist as user and NEVER set agent_id
        actual_agent_id: str | None = None
        if "agent_id" in normalized_metadata and adapter != "api":
            # Only honor metadata agent_id for non-API (e.g., MCP) paths
            actual_agent_id = str(normalized_metadata.pop("agent_id"))

        if adapter == "api" and actor.type != "agent":
            # UI/API posts authored by a human user
            msg = Message(
                id=uuid.uuid4(),
                space_id=actor.space_id,
                user_id=actor.id,
                agent_id=None,
                content=content,
                channel=channel,
                message_type=message_type,
                parent_id=pid,
                message_metadata=normalized_metadata,
                read_state="delivered",
            )
        else:
            # MCP/other transports: allow agent authorship
            # Resolve final ids
            resolved_agent_id = None
            resolved_user_id = None
            if actual_agent_id:
                try:
                    resolved_agent_id = uuid.UUID(actual_agent_id)
                except Exception:
                    resolved_agent_id = None
            if actor.type == "agent" and not resolved_agent_id:
                resolved_agent_id = actor.id
                # SECURITY: Agent-authored messages must have user_id=None to prevent
                # impersonation. The agent_id field is the sole identity marker.
                # This matches the /internal/agent-reply path (internal.py:593).
                resolved_user_id = None
            if actor.type == "human" and not resolved_agent_id:
                resolved_user_id = actor.id

            # Classify agent ack messages as signals to prevent routing loops
            if resolved_agent_id and message_type != "agent_pause":
                from app.services.message_visibility import is_ack_message
                if is_ack_message(content, is_agent=True):
                    message_type = "agent_pause"
                    normalized_metadata["signal_kind"] = "ack"
                    normalized_metadata["signal_only"] = True

            msg = Message(
                id=uuid.uuid4(),
                space_id=actor.space_id,
                user_id=resolved_user_id,
                agent_id=resolved_agent_id,
                content=content,
                channel=channel,
                message_type=message_type,
                parent_id=pid,
                message_metadata=normalized_metadata,
                read_state="delivered",
            )

        self.db.add(msg)
        await self.db.commit()

        # Refresh to get relationships
        await self.db.refresh(msg, ["user", "agent"])

        if before_broadcast is not None:
            await before_broadcast(msg)

        # V3 RESPONSE LOGGING: Log when agents send messages (for dispatch dashboard)
        if msg.agent_id and msg.agent:
            agent_name = msg.agent.name if msg.agent else "unknown"
            reply_to = str(msg.parent_id) if msg.parent_id else None
            content_preview = msg.content[:300].replace("\n", " ") if msg.content else ""
            print(
                f"AGENT_REPLY agent_name={agent_name} agent_id={msg.agent_id} "
                f"message_id={msg.id} reply_to={reply_to} "
                f"content_len={len(msg.content) if msg.content else 0} "
                f'response_preview="{content_preview}"'
            )

        # UI-only signal rows should not create unread badges.
        unread_count = 0
        if not is_ui_only_no_reply_metadata(msg.message_type, msg.message_metadata):
            try:
                unread_count = await self.notify.mark_unread_for_org(
                    space_id=actor.space_id, sender_user_id=actor.id, message_id=msg.id
                )
            except Exception as e:
                # Log but don't fail the operation
                logging.getLogger(__name__).warning(f"Failed to mark unread: {e}")

        # SSE broadcast (non-fatal)
        try:
            await self.notify.broadcast_sse(space_id=actor.space_id, msg=msg, author_name=author_display_name, author_type=actor.type)
        except Exception as e:
            # Log but don't fail
            logging.getLogger(__name__).warning(f"Failed to broadcast SSE: {e}")

        # Mention notifications are best-effort; avoid blocking message send
        # Note: Mentions already extracted and stored in metadata before message creation (line 131)
        try:
            stored_mentions = msg.message_metadata.get("mentions", []) if msg.message_metadata else []
            mention_handles = await self._coerce_mentions_to_handles(stored_mentions)
            if mention_handles:
                actor_agent_id = msg.agent_id if msg.agent_id else None
                await self.notifications.record_mentions(
                    space_id=actor.space_id,
                    message=msg,
                    mentioned_handles=mention_handles,
                    actor_agent_id=actor_agent_id,
                )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to record mention notifications: %s",
                exc,
            )

        # Parity logging for monitoring
        self.parity.log_send(adapter=adapter, actor=actor, msg=msg, unread_count=unread_count)

        # Intelligence handled by aX dispatch — see dispatch_executor.py
        # Legacy IntelligenceService removed (aX consolidation).
        # Rollback: set ENABLE_LEGACY_INTELLIGENCE=true to re-enable.

        return msg

    async def list_messages(
        self,
        *,
        actor: Actor,
        since: datetime | None = None,
        limit: int = 50,
        channel: str = "main",
        parent_id: UUID | None = None,
        unread_only: bool = False,
        include_unread_counts: bool = False,
        include_mentions: bool = False,
        include_activity_metrics: bool = False,
        include_hot_topics: bool = False,
        include_reactions: bool = False,
        include_total_count: bool = False,
        include_intelligence: bool = False,
        search_text: str | None = None,
        filter_agent: str | None = None,
        filter_topic: str | None = None,
        before_timestamp: datetime | None = None,
        before_message_id: UUID | None = None,
        message_type_filter: str | None = None,
        media_types_filter: list[str] | None = None,
        unread_message_ids: set[UUID] | None = None,
        adapter: str = "unknown",
    ) -> dict[str, Any]:
        """
        List messages with unified query logic for API and MCP.
        Returns dict with messages and optional intelligence extras.
        """
        # Build shared filters
        base_filters = [
            Message.space_id == actor.space_id,
            Message.channel == channel,
            Message.message_type != "reaction",
            exclude_ui_only_no_reply_clause(),
            # Filter out soft-deleted messages (critical bug fix)
            # SQL three-valued logic fix: COALESCE treats NULL as 'false' instead of UNKNOWN
            or_(
                Message.message_metadata.is_(None),
                func.coalesce(Message.message_metadata["is_deleted"].astext, "false") != "true",
            ),
            # SECURITY: Exclude messages from internal system agents (e.g., __ai_validator__)
            # These agents are invisible to users and should never appear in message lists
            # Using OR with NULL check to handle messages from users (agent_id=NULL)
            or_(
                Message.agent_id.is_(None),  # User messages (no agent) - always include
                ~exists(
                    select(1).where(
                        and_(
                            Agent.id == Message.agent_id,
                            Agent.is_internal.is_(True),
                        )
                    ).correlate(Message)
                ),
            ),
        ]
        if since:
            base_filters.append(Message.created_at >= since)
        if parent_id:
            base_filters.append(Message.parent_id == parent_id)
        unread_filter_chunks: list[list[UUID]] | None = None
        if unread_only and unread_message_ids is not None:
            unread_id_list = list(unread_message_ids)
            if not unread_id_list:
                base_filters.append(Message.id == uuid.UUID(int=0))
            elif len(unread_id_list) <= UNREAD_ID_FILTER_BATCH_SIZE:
                base_filters.append(Message.id.in_(unread_id_list))
            else:
                unread_filter_chunks = _chunk_uuid_list(unread_id_list)
        elif unread_only:
            base_filters.append(Message.read_state != "read")

        # Cursor-based pagination with tie-breaker for stable ordering
        # Uses compound cursor (timestamp|id) to handle messages with same timestamp
        if before_timestamp is not None:
            if before_message_id is not None:
                # Compound cursor: get messages before this exact position
                # (older timestamp) OR (same timestamp but smaller ID)
                base_filters.append(
                    or_(
                        Message.created_at < before_timestamp,
                        and_(
                            Message.created_at == before_timestamp,
                            Message.id < before_message_id,
                        ),
                    )
                )
            else:
                # Legacy: timestamp-only cursor (backward compatibility)
                base_filters.append(Message.created_at < before_timestamp)

        # Message type filtering
        if message_type_filter:
            base_filters.append(Message.message_type == message_type_filter)

        # Media types filtering (OR condition for multiple types)
        if media_types_filter:
            base_filters.append(Message.message_type.in_(media_types_filter))

        if search_text:
            raw_search = search_text.strip()
            if raw_search:
                # Search both message content AND message ID (supports short IDs like "c84064ec")
                # Uses PostgreSQL UUID-to-text casting pattern (same as edit/delete resolution)
                base_filters.append(
                    or_(Message.content.ilike(f"%{raw_search}%"), cast(Message.id, String).ilike(f"%{raw_search}%"))
                )

        if filter_topic:
            topic = filter_topic.lstrip("#").strip()
            if topic:
                base_filters.append(Message.content.ilike(f"%#{topic}%"))

        if filter_agent:
            agent_uuid, agent_term = self._normalize_filter_agent(filter_agent)

            if agent_uuid:
                base_filters.append(
                    or_(
                        Message.user_id == agent_uuid,
                        Message.agent_id == agent_uuid,
                    )
                )
            elif agent_term:
                like_pattern = f"%{agent_term}%"
                user_match = select(User.id).where(
                    User.id == Message.user_id,
                    or_(
                        User.username.ilike(like_pattern),
                        User.full_name.ilike(like_pattern),
                    ),
                )
                agent_match = select(Agent.id).where(
                    Agent.id == Message.agent_id,
                    Agent.name.ilike(like_pattern),
                )
                base_filters.append(or_(exists(user_match), exists(agent_match)))

        # Count total if requested
        total_count = None
        if include_total_count:
            if unread_filter_chunks:
                total_count = 0
                for chunk in unread_filter_chunks:
                    count_query = select(func.count()).select_from(Message).where(
                        and_(*base_filters, Message.id.in_(chunk))
                    )
                    count_res = await self.db.execute(count_query)
                    total_count += count_res.scalar() or 0
            else:
                count_query = select(func.count()).select_from(Message).where(and_(*base_filters))
                count_res = await self.db.execute(count_query)
                total_count = count_res.scalar() or 0

        # Main query for messages
        query_options = [
            selectinload(Message.user),
            selectinload(Message.agent).selectinload(Agent.user),
        ]
        # Add intelligence data loading if requested
        if include_intelligence:
            query_options.append(selectinload(Message.intelligence_data))

        if unread_filter_chunks:
            chunked_messages: dict[UUID, Message] = {}
            for chunk in unread_filter_chunks:
                query = (
                    select(Message)
                    .options(*query_options)
                    .where(and_(*base_filters, Message.id.in_(chunk)))
                    # Secondary ORDER BY id ensures deterministic ordering for messages
                    # with identical timestamps (critical for cursor-based pagination)
                    .order_by(desc(Message.created_at), desc(Message.id))
                    .limit(limit)
                )
                result = await self.db.execute(query)
                for message in result.scalars().all():
                    chunked_messages[message.id] = message
            messages = sorted(
                chunked_messages.values(),
                key=lambda message: (message.created_at, message.id.int),
                reverse=True,
            )[:limit]
        else:
            query = (
                select(Message)
                .options(*query_options)
                .where(and_(*base_filters))
                # Secondary ORDER BY id ensures deterministic ordering for messages
                # with identical timestamps (critical for cursor-based pagination)
                .order_by(desc(Message.created_at), desc(Message.id))
                .limit(limit)
            )
            result = await self.db.execute(query)
            messages: list[Message] = result.scalars().all()

        # BATCH REPLY COUNTING - eliminates per-message loops
        if messages:
            message_ids = [m.id for m in messages]
            reply_counts = await self.core.get_reply_counts_batch(
                space_id=actor.space_id, channel=channel, message_ids=message_ids
            )
            # Attach reply counts to messages
            for m in messages:
                m.replies_count = reply_counts.get(str(m.id), 0)

        # Include reactions aggregated by parent message
        if include_reactions and messages:
            parent_ids = [m.id for m in messages]
            react_query = select(Message.parent_id, Message.content).where(
                and_(
                    Message.parent_id.in_(parent_ids),
                    Message.space_id == actor.space_id,
                    Message.channel == channel,
                    or_(
                        Message.message_type == "reaction",
                        func.coalesce(Message.message_metadata["is_reaction"].astext, "false") == "true",
                    ),
                )
            )
            react_res = await self.db.execute(react_query)
            reactions_map: dict[str, dict[str, int]] = {}
            for pid, content in react_res.all():
                for emoji in self.core.extract_emojis(content or ""):
                    pid_str = str(pid)
                    reactions_map.setdefault(pid_str, {})
                    reactions_map[pid_str][emoji] = reactions_map[pid_str].get(emoji, 0) + 1
            for m in messages:
                rid = reactions_map.get(str(m.id))
                if rid:
                    m.reactions = rid

        # Compute extras as requested
        extras: dict[str, Any] = {}

        # Unread counts per agent for the requesting actor (if actor is a human)
        if include_unread_counts:
            try:
                unread_set = await self.redis.smembers(f"unread:{actor.id}")
                unread_ids = set(unread_set or [])
                counts: dict[str, int] = {}
                for m in messages:
                    if str(m.id) in unread_ids and m.agent_id:
                        key = str(m.agent_id)
                        counts[key] = counts.get(key, 0) + 1
                extras["unread_counts"] = counts
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to compute unread counts: {e}")

        if include_mentions:
            try:
                all_mentions: list[str] = []
                for m in messages:
                    all_mentions.extend(await self.core.extract_mentions(m.content))
                # unique mentions
                extras["mentions"] = sorted(list(set(all_mentions)))
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to extract mentions: {e}")

        if include_activity_metrics:
            try:
                from collections import Counter

                # Bucket by hour UTC
                buckets = Counter()
                for m in messages:
                    ts = m.created_at
                    if ts:
                        hour_key = ts.replace(minute=0, second=0, microsecond=0).isoformat()
                        buckets[hour_key] += 1
                extras["activity_metrics"] = {"per_hour": dict(buckets)}
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to compute activity metrics: {e}")

        if include_hot_topics:
            try:
                import re as _re
                from collections import Counter

                words = []
                for m in messages:
                    tokens = _re.findall(r"[a-zA-Z0-9_]{4,}", (m.content or "").lower())
                    tokens = [t for t in tokens if not t.startswith("@")]
                    words.extend(tokens)
                top = Counter(words).most_common(10)
                extras["hot_topics"] = [w for w, _ in top]
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to compute hot topics: {e}")

        # Cursor for pagination (use last item's cursor_position if available)
        cursor = None
        if messages:
            try:
                cursor = messages[-1].cursor_position
            except Exception:
                cursor = None

        # Parity logging (high-level)
        self.parity.log_list(adapter=adapter, actor=actor)

        return {"messages": messages, "extras": extras, "cursor": cursor, "total_count": total_count}

    async def edit(
        self, *, actor: Actor, message_id: UUID, new_content: str, reason: str | None = None, adapter: str = "unknown"
    ) -> Message:
        """
        Edit a message with policy constraints enforced in the service layer.
        Constraints (from spec):
        - 15-minute window from creation time
        - Max 5 edits total
        - No edits if the message has replies
        - Only the original author (human) may edit; admins TBD
        """
        if not new_content or not new_content.strip():
            raise ValueError("New content cannot be empty")

        # Load target message (org guard)
        res = await self.db.execute(
            select(Message).where(and_(Message.id == message_id, Message.space_id == actor.space_id))
        )
        msg = res.scalar_one_or_none()
        if not msg:
            raise ValueError("Message not found or not in actor's organization")

        # Authorization: author or admin can edit
        is_admin = "admin" in actor.capabilities or "moderator" in actor.capabilities
        if not is_admin:
            if actor.type == "human":
                # Human author must match user_id
                if not msg.user_id or msg.user_id != actor.id:
                    raise PermissionError("Only the original author can edit this message")
            # Agent author must match agent_id
            elif not getattr(msg, "agent_id", None) or msg.agent_id != actor.id:
                raise PermissionError("Only the original agent author can edit this message")

        # Time window: 15 minutes
        now = datetime.utcnow()
        created_at = msg.created_at
        if created_at is None:
            raise ValueError("Message is missing created_at timestamp")
        elapsed_seconds = (now - created_at.replace(tzinfo=None)).total_seconds()
        if not is_admin and elapsed_seconds > 15 * 60:
            raise PermissionError("Edit window expired (15 minutes)")

        # No edits if there are replies
        replies = await self.db.execute(select(Message.id).where(Message.parent_id == msg.id))
        if not is_admin and replies.scalars().first() is not None:
            raise PermissionError("Cannot edit a message that already has replies")

        # Edit count via message_metadata to avoid schema change
        metadata = msg.message_metadata or {}
        edit_count = int(metadata.get("edit_count", 0))
        if edit_count >= 5:
            raise PermissionError("Maximum edit count reached (5)")

        # Apply edit and mark indicator
        previous_content = msg.content
        msg.content = new_content
        metadata["is_edited"] = True
        metadata["edit_count"] = edit_count + 1
        metadata["last_edited_at"] = now.isoformat()
        if reason:
            metadata["last_edit_reason"] = reason
        # Small inline indicator for UIs that don't render an edited badge
        try:
            if "(edited)" not in msg.content:
                msg.content = f"{msg.content} (edited)"
        except Exception:
            pass
        # Append to edit history (bounded growth)
        try:
            history = metadata.get("edit_history", [])
            history.append(
                {"previous": previous_content, "new": new_content, "at": now.isoformat(), "by": str(actor.id)}
            )
            # Keep last 5 entries
            metadata["edit_history"] = history[-5:]
        except Exception:
            pass
        msg.message_metadata = metadata

        await self.db.commit()
        try:
            await self.db.refresh(msg)
        except Exception:
            pass

        # SSE broadcast for edit (non-fatal) — lets UI update in real-time
        try:
            await self.sse.publish(
                space_id=str(actor.space_id),
                event="message_edited",
                data={
                    "id": str(msg.id),
                    "content": msg.content,
                    "edited_by": str(actor.id),
                    "edited_at": metadata.get("last_edited_at"),
                },
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to broadcast edit SSE: {e}")

        # Parity log
        self.parity.log_edit(adapter=adapter, actor=actor, msg=msg)

        return msg

    async def delete(
        self, *, actor: Actor, message_id: UUID, reason: str, permanent: bool = False, adapter: str = "unknown"
    ) -> bool:
        """
        Delete a message with policy constraints.
        Constraints (from spec):
        - 24-hour window for regular users
        - Admins can delete anytime
        - Soft delete by default
        - Hard delete only for admins with permanent=True
        """
        if not reason or not reason.strip():
            raise ValueError("Delete reason is required")

        # Load target message (org guard)
        res = await self.db.execute(
            select(Message).where(and_(Message.id == message_id, Message.space_id == actor.space_id))
        )
        msg = res.scalar_one_or_none()
        if not msg:
            raise ValueError("Message not found or not in actor's organization")

        # Determine workspace classification for policy exceptions
        is_admin = "admin" in actor.capabilities or "moderator" in actor.capabilities
        try:
            org_res = await self.db.execute(select(Space).where(Space.id == msg.space_id))
            org = org_res.scalar_one_or_none()
            is_personal = False
            is_personal_owner = False
            if org:
                is_personal = SpaceValidationService.is_personal_workspace(org)
                owner_user_ids: list[str] = []
                try:
                    mems = await self.db.execute(
                        select(SpaceMembership.user_id).where(SpaceMembership.space_id == org.id)
                    )
                    owner_user_ids = [str(uid) for uid in mems.scalars().all()]
                except Exception:
                    owner_user_ids = []

                if not is_personal and org.visibility == "private":
                    try:
                        member_count = len(owner_user_ids) or await SpaceValidationService.get_member_count(self.db, str(org.id))
                        is_personal = member_count == 1
                        if member_count == 1 and actor.type == "human":
                            is_personal_owner = len(owner_user_ids) == 1 and owner_user_ids[0] == str(actor.id)
                    except Exception:
                        pass
                if SpaceValidationService.is_personal_workspace(org) and actor.type == "human":
                    is_personal_owner = len(owner_user_ids) == 1 and owner_user_ids[0] == str(actor.id)
            if is_personal and is_personal_owner:
                is_admin = True
        except Exception:
            pass

        # Authorization and time window
        if not is_admin:
            if msg.user_id != actor.id and msg.agent_id != actor.id:
                raise PermissionError("You can only delete your own messages")
            now = datetime.utcnow()
            created_at = msg.created_at
            if created_at:
                elapsed_seconds = (now - created_at.replace(tzinfo=None)).total_seconds()
                if elapsed_seconds > 24 * 3600:
                    raise PermissionError("Delete window expired (24 hours)")

        # Apply delete (soft by default)
        has_replies = False
        try:
            rep = await self.db.execute(select(Message.id).where(Message.parent_id == msg.id))
            has_replies = rep.scalars().first() is not None
        except Exception:
            has_replies = False

        if permanent and is_admin and not has_replies:
            # Hard delete - actually remove from database (only if no replies)
            await self.db.delete(msg)
        else:
            # Soft delete - mark as deleted with metadata
            metadata = msg.message_metadata or {}
            metadata["is_deleted"] = True
            metadata["deleted_at"] = datetime.utcnow().isoformat()
            metadata["deleted_by"] = str(actor.id)
            metadata["delete_reason"] = reason
            msg.message_metadata = metadata
            msg.content = "[Message deleted]"

        await self.db.commit()

        # SSE broadcast for delete (non-fatal) — lets UI update in real-time
        try:
            await self.sse.publish(
                space_id=str(actor.space_id),
                event="message_deleted",
                data={
                    "id": str(message_id),
                    "deleted_by": str(actor.id),
                    "permanent": permanent and is_admin and not has_replies,
                },
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to broadcast delete SSE: {e}")

        # Parity log
        self.parity.log_delete(adapter=adapter, actor=actor, message_id=message_id)

        return True

    async def get_history(self, *, actor: Actor, message_id: UUID, adapter: str = "unknown") -> list[dict[str, Any]]:
        """
        Get edit/delete history for a message.
        Only the author and admins can view history.
        """
        # Load message with org guard
        res = await self.db.execute(
            select(Message).where(and_(Message.id == message_id, Message.space_id == actor.space_id))
        )
        msg = res.scalar_one_or_none()
        if not msg:
            raise ValueError("Message not found or not in actor's organization")

        # Authorization: author or admin only
        is_admin = "admin" in actor.capabilities or "moderator" in actor.capabilities
        is_author = (msg.user_id == actor.id) or (msg.agent_id == actor.id)

        if not (is_author or is_admin):
            raise PermissionError("Only the author or admins can view message history")

        # Build history from metadata (simplified version)
        metadata = msg.message_metadata or {}
        history = []

        # Add creation event
        history.append(
            {
                "action": "created",
                "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                "actor_id": str(msg.user_id or msg.agent_id),
                "content": msg.content if not metadata.get("is_deleted") else None,
            }
        )

        # Add edit events if present
        if metadata.get("is_edited"):
            edit_count = metadata.get("edit_count", 0)
            last_edited = metadata.get("last_edited_at")
            if last_edited:
                history.append(
                    {
                        "action": "edited",
                        "timestamp": last_edited,
                        "actor_id": str(actor.id),
                        "edit_count": edit_count,
                        "reason": metadata.get("last_edit_reason"),
                    }
                )

        # Add delete event if present
        if metadata.get("is_deleted"):
            history.append(
                {
                    "action": "deleted",
                    "timestamp": metadata.get("deleted_at"),
                    "actor_id": metadata.get("deleted_by"),
                    "reason": metadata.get("delete_reason"),
                }
            )

        # Parity log
        self.parity.log_get_history(adapter=adapter, actor=actor, message_id=message_id)

        return history
