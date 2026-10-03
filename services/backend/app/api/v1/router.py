"""
Router Endpoint — Multi-agent fan-out with multiplexed SSE streaming.

DEPRECATED: aX (Space Agent) now handles routing internally via
auto-dispatch in messages_notifications.py. This endpoint remains for
backwards compatibility but will be removed in a future release.

POST /api/v1/spaces/{space_id}/router/message
  → SSE stream: routing → chunk* → agent_done* → done

See spec: /home/ax-agent/shared/docs/spec-router-streaming-v1.md
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from app.models.agent_space_access import AgentSpaceAccess
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.rls import SecureSession, get_secure_session
from ...models.agent import Agent
from ...models.space_membership import SpaceMembership
from ...services.redis_sse_broker import redis_sse_broker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/spaces", tags=["router"])

# --- Configuration ---
ROUTER_FAN_OUT_CAP = int(os.getenv("ROUTER_FAN_OUT_CAP", "4"))
ROUTER_AGENT_TIMEOUT_S = int(os.getenv("ROUTER_AGENT_TIMEOUT_S", "120"))
ROUTER_CLASSIFY_TIMEOUT_S = int(os.getenv("ROUTER_CLASSIFY_TIMEOUT_S", "5"))
SSE_HEARTBEAT_S = 15.0

# Classification provider: "bedrock" or "gemini"
ROUTER_CLASSIFY_PROVIDER = os.getenv("ROUTER_CLASSIFY_PROVIDER", "bedrock")
ROUTER_CLASSIFY_MODEL = os.getenv("ROUTER_CLASSIFY_MODEL", "amazon.nova-micro-v1:0")

from ...core.router_filters import router_filter_pipeline

# Circuit breaker: status file shared with the coordinator's check-rate-limit.sh.
# When Bedrock throttles, the API writes here so the next coordinator cycle backs off.
_RATE_LIMIT_STATUS_FILE = "/home/ax-agent/shared/state/rate-limit-status.json"
_CIRCUIT_BREAKER_COOLDOWN_MINUTES = 30


async def _log_filter_event(
    query_hash: str,
    filter_stage: str,
    filter_reason: Optional[str],
    override_used: bool,
) -> None:
    """
    Write a filter decision to concierge_routing_logs (best-effort).
    Called as a background task — never raises, never blocks routing.
    """
    try:
        import uuid as _uuid
        from app.core.database import AsyncSessionLocal
        from app.models.concierge_routing_log import ConciergeRoutingLog

        async with AsyncSessionLocal() as db:
            log = ConciergeRoutingLog(
                request_id=_uuid.uuid4(),
                query_hash=query_hash,
                filter_stage=filter_stage,
                filter_reason=filter_reason,
                override_used=override_used,
                # No routing decision — message was filtered or overridden
                combined_winner=None,
                resolution_step=None,
                confidence=None,
            )
            db.add(log)
            await db.commit()
    except Exception as e:
        logger.debug(f"_log_filter_event: non-fatal write failure: {e}")


def _trip_circuit_breaker(error: Exception) -> None:
    """
    Write a rate-limit trip to the circuit breaker status file.
    Non-fatal — if the write fails (e.g. path not mounted), log and continue.
    check-rate-limit.sh reads this file before the coordinator wakes agents.
    """
    import json as _json
    import os as _os
    from datetime import datetime, timezone

    try:
        now = datetime.now(timezone.utc)
        cooldown_until = datetime(
            now.year, now.month, now.day, now.hour,
            now.minute + _CIRCUIT_BREAKER_COOLDOWN_MINUTES,
            now.second, tzinfo=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Read existing state (may not exist yet)
        existing = {}
        if _os.path.exists(_RATE_LIMIT_STATUS_FILE):
            try:
                with open(_RATE_LIMIT_STATUS_FILE) as f:
                    existing = _json.load(f)
            except Exception:
                existing = {}

        errors = existing.get("errors", [])
        errors.append({"at": now_str, "source": "bedrock_classify", "error": str(error)[:200]})

        state = {
            "tripped": True,
            "lastTrippedAt": now_str,
            "cooldownUntil": cooldown_until,
            "errors": errors[-20:],  # keep last 20 events
        }

        # Atomic write via tmp file
        tmp = _RATE_LIMIT_STATUS_FILE + ".tmp"
        with open(tmp, "w") as f:
            _json.dump(state, f, indent=2)
        _os.replace(tmp, _RATE_LIMIT_STATUS_FILE)
        logger.warning(f"🔴 Circuit breaker TRIPPED — cooldown until {cooldown_until}")
    except Exception as write_err:
        logger.warning(f"⚠️ Circuit breaker write failed (non-fatal): {write_err}")


# Semaphore: cap concurrent Bedrock calls to protect shared Nova Micro daily quota.
# Nova Micro quota is account-wide — message_intelligence.py draws from the same bucket.
# 2 concurrent max keeps us well under the default TPS limit during normal traffic.
# Pre-load-test: set ROUTER_CLASSIFY_PROVIDER=gemini to bypass this path entirely.
_BEDROCK_CLASSIFY_SEMAPHORE = asyncio.Semaphore(2)


# --- Request/Response schemas ---

class RouterMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    conversation_id: Optional[str] = None
    primary_agent_id: Optional[UUID] = Field(
        default=None,
        description="Sticky primary route target (selected recipient).",
    )
    mentioned_agent_ids: list[UUID] = Field(
        default_factory=list,
        description="Additive secondary route targets (from @mentions or explicit multi-select).",
    )
    override_token: Optional[str] = Field(
        default=None,
        description="One-time override token issued when a filter fires. "
                    "Resend the original message with this token to bypass the filter once.",
    )


class AgentTarget(BaseModel):
    agent_id: str
    agent_name: str
    role: str
    reason: str


# --- SSE helpers ---

def sse_event(event: str, data: dict) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def sse_heartbeat() -> str:
    return ": heartbeat\n\n"


# --- Intent Classification ---

async def classify_intent(
    content: str,
    agents: list[dict],
    *,
    context_window: str | None = None,
    sender_identity: dict[str, Any] | None = None,
) -> list[dict]:
    """
    Use a fast LLM to classify which agents should handle a message.
    Returns list of {agent_id, agent_name, role, reason}.
    Falls back to all agents on error.
    """
    if not agents:
        return []

    # First, check for explicit @mentions in the message
    import re
    explicit_mentions = re.findall(r'@([\w-]+)', content)
    logger.info(f"🔍 Router: Found explicit mentions {explicit_mentions} in message: {content[:100]}")

    # Also check for implicit agent mentions (agent names without @ symbol)
    agent_lookup = {a['name']: a for a in agents}
    implicit_mentions = []

    # Check for agent names at word boundaries (case-insensitive)
    content_lower = content.lower()
    for agent_name in agent_lookup.keys():
        agent_name_lower = agent_name.lower()
        # Look for agent name as whole word, possibly followed by punctuation or addressing words
        patterns = [
            rf'\b{re.escape(agent_name_lower)}\b[,:]?\s',
            rf'\b{re.escape(agent_name_lower)}\b$',
            rf'^{re.escape(agent_name_lower)}\b',
            rf'\bhey\s+{re.escape(agent_name_lower)}\b',
            rf'\bcan\s+{re.escape(agent_name_lower)}\b',
        ]

        for pattern in patterns:
            if re.search(pattern, content_lower):
                if agent_name not in explicit_mentions and agent_name not in implicit_mentions:
                    implicit_mentions.append(agent_name)
                    logger.info(f"🔍 Router: Found implicit mention of {agent_name}")
                break

    # Combine explicit and implicit mentions
    all_mentions = explicit_mentions + implicit_mentions

    if all_mentions:
        # Route directly to mentioned agents if they exist in available agents
        mentioned_agents = []

        for mention in all_mentions:
            if mention in agent_lookup:
                agent = agent_lookup[mention]
                mention_type = "directly mentioned as @" + mention if mention in explicit_mentions else "implicitly mentioned"
                mentioned_agents.append({
                    "agent_id": str(agent["id"]),
                    "agent_name": agent["name"],
                    "role": "Mentioned Agent",
                    "reason": mention_type,
                })
                logger.info(f"✅ Router: Route to mentioned agent {mention} ({mention_type})")

        if mentioned_agents:
            return mentioned_agents[:ROUTER_FAN_OUT_CAP]

    agent_descriptions = "\n".join(
        f"- {a['name']} (id: {a['id']}): {a.get('description', a.get('routing_hint', 'general assistant'))}"
        for a in agents
    )

    logger.info(f"🤖 Router: Available agents for classification: {[a['name'] for a in agents]}")

    context_snippet = (context_window or "").strip()
    sender_profile = ""
    if sender_identity:
        sender_items = []
        for key, value in sender_identity.items():
            if value is None or isinstance(value, (dict, list)):
                continue
            sender_items.append(f"- {key}: {value}")
        if sender_items:
            sender_profile = "\n\nSender identity:\n" + "\n".join(sender_items)

    if context_snippet:
        context_snippet = "\n\nRecent context:\n" + context_snippet

    prompt = f"""You are an intent router. Given a user message and available agents, decide which agent(s) should handle it.
Use sender identity and recent thread context when present to infer intent for follow-ups and preserve ownership.
Return JSON array of objects with: agent_id, agent_name, role (short label), reason (one sentence).
Select 1-{ROUTER_FAN_OUT_CAP} agents. Only select multiple if the task genuinely needs parallel work.

Available agents:
{agent_descriptions}{context_snippet}{sender_profile}

User message: {content}

Respond ONLY with the JSON array, no markdown fences."""

    try:
        if ROUTER_CLASSIFY_PROVIDER == "bedrock":
            try:
                result = await _classify_bedrock(prompt)
                logger.info(f"✅ Router: Bedrock classification successful: {result}")
                return result
            except Exception as bedrock_err:
                _resp = getattr(bedrock_err, "response", None)
                err_code = (_resp.get("Error", {}).get("Code", "") if isinstance(_resp, dict) else "")
                is_throttle = (
                    "ThrottlingException" in str(bedrock_err)
                    or "TooManyRequestsException" in str(bedrock_err)
                    or err_code in ("ThrottlingException", "TooManyRequestsException")
                )
                if is_throttle:
                    logger.warning(f"⚠️ Router: Bedrock throttled — falling back to Gemini: {bedrock_err}")
                    _trip_circuit_breaker(bedrock_err)
                    result = await _classify_gemini(prompt)
                    logger.info(f"✅ Router: Gemini fallback classification successful: {result}")
                    return result
                raise
        else:
            result = await _classify_gemini(prompt)
            logger.info(f"✅ Router: Gemini classification successful: {result}")
            return result
    except Exception as e:
        logger.error(f"❌ Router: Classification failed (BUG-06 fix: no longer silent): {e}", exc_info=True)
        # Prefer Space Agent as fallback when classification fails
        a = next((ag for ag in agents if ag.get("origin") == "space_agent"), agents[0])
        return [
            {
                "agent_id": str(a["id"]),
                "agent_name": a["name"],
                "role": "Assistant",
                "reason": "classification_failed",
                "error": str(e),
            }
        ]


async def _classify_bedrock(prompt: str) -> list[dict]:
    """Classify using AWS Bedrock (Nova Micro for speed).

    ⚠️ SHARED QUOTA WARNING — read before load testing:
    AWS Bedrock quotas (TPS and daily token limits) are enforced account-wide
    per model per region. There is NO per-service or per-function isolation.
    This function and message_intelligence.py BOTH draw from the same
    Nova Micro bucket in us-east-1 simultaneously.

    Consequences:
    - High-frequency classification calls (e.g. load tests) will exhaust the
      TPS limit and starve message_intelligence.py for production traffic.
    - Daily token quota is shared too — if either service burns through it,
      the other gets ThrottlingException for the rest of the day.
      (We hit this on 2026-02-21: daily quota exhausted by classification load.)

    Mitigations already in place:
    1. ROUTER_CLASSIFY_PROVIDER=gemini env var routes all classification to
       Gemini Flash Lite, leaving Nova Micro headroom for summarization.
       → Set this on staging before any load test run.
    2. Throttle-aware fallback in classify_intent(): ThrottlingException /
       TooManyRequestsException automatically fall through to _classify_gemini()
       rather than silently degrading to agents[0].

    Before running load tests against this path:
    - Flip ROUTER_CLASSIFY_PROVIDER=gemini in staging compose
    - Confirm _classify_gemini() is healthy with a single test call
    - Do NOT load test with ROUTER_CLASSIFY_PROVIDER=bedrock while
      message_intelligence.py is processing live traffic
    """
    import boto3
    client = boto3.client("bedrock-runtime", region_name=os.getenv("BEDROCK_REGION", "us-east-1"))

    body = json.dumps({
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": 500, "temperature": 0.0},
    })

    loop = asyncio.get_event_loop()
    async with _BEDROCK_CLASSIFY_SEMAPHORE:
        response = await loop.run_in_executor(
            None,
            lambda: client.invoke_model(
                modelId=ROUTER_CLASSIFY_MODEL,
                body=body,
                contentType="application/json",
                accept="application/json",
            ),
        )

    result = json.loads(response["body"].read())
    text = result["output"]["message"]["content"][0]["text"].strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    parsed = json.loads(text)
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], list):
        parsed = parsed[0]
    if isinstance(parsed, dict):
        for key in ('agents', 'result', 'data', 'classifications'):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    return parsed


async def _classify_gemini(prompt: str) -> list[dict]:
    """Classify using Gemini Flash Lite."""
    import google.generativeai as genai

    model = genai.GenerativeModel(os.getenv("GEMINI_SUMMARY_MODEL", "gemini-2.5-flash-lite"))
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: model.generate_content(prompt),
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(text)
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], list):
        parsed = parsed[0]
    if isinstance(parsed, dict):
        for key in ('agents', 'result', 'data', 'classifications'):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    return parsed


# --- Stream Multiplexer ---

async def multiplex_agents(
    *,
    targets: list[AgentTarget],
    user_message: str,
    space_id: str,
    user_id: str,
    conversation_id: str,
    request: Request,
    db: AsyncSession,
) -> AsyncIterator[str]:
    """
    Fan out to multiple agents and multiplex their SSE responses.

    Flow:
    1. Emit `routing` event
    2. Post message to each agent (triggers existing dispatch pipeline)
    3. Subscribe to Redis SSE stream, filter for target agent responses
    4. Emit `chunk` events with agent attribution
    5. Emit `agent_done` as each completes
    6. Emit `done` when all complete or timeout
    """
    message_id = str(uuid.uuid4())
    target_agent_ids = {t.agent_id for t in targets}
    target_map = {t.agent_id: t for t in targets}

    # Phase 1: Routing event
    yield sse_event("routing", {
        "message_id": message_id,
        "targets": [t.model_dump() for t in targets],
        "router_message": _build_router_message(targets),
    })

    # Phase 2: Dispatch messages to agents via existing pipeline
    # We create a message in the DB with @mentions, which triggers _dispatch_cloud_agents
    from ...services.messages_service import MessagesService
    from ...core.actor import Actor, CAP_MESSAGES_SEND

    # Get redis client for MessagesService
    redis_client = await redis_sse_broker._get_redis()
    svc = MessagesService(db, redis_client, redis_sse_broker)
    actor = Actor(id=UUID(user_id), space_id=UUID(space_id), type="user", capabilities={CAP_MESSAGES_SEND})

    # Build content with @mentions for all target agents
    mention_str = " ".join(f"@{t.agent_name}" for t in targets)
    dispatch_content = f"{mention_str} {user_message}"

    try:
        msg = await svc.create_message(
            actor=actor,
            content=dispatch_content,
            channel="main",
            metadata={
                "router_message_id": message_id,
                "router_targets": [t.agent_id for t in targets],
                "conversation_id": conversation_id,
            },
        )
        logger.info(f"🔀 ROUTER: Dispatched to {len(targets)} agents via msg {msg.id}")
    except Exception as e:
        logger.error(f"Router dispatch failed: {e}")
        yield sse_event("error", {"message": f"Dispatch failed: {str(e)}"})
        return

    # Phase 3: Subscribe to Redis SSE stream and multiplex responses
    completed_agents: set[str] = set()
    agent_chunks: dict[str, list[str]] = {aid: [] for aid in target_agent_ids}
    agent_seq: dict[str, int] = {aid: 0 for aid in target_agent_ids}
    start_time = time.monotonic()

    try:
        redis_conn = await redis_sse_broker._get_redis()
        stream_key = redis_sse_broker._stream_key(space_id)
        last_id = "$"  # Only new events

        while len(completed_agents) < len(target_agent_ids):
            # Check timeout
            elapsed = time.monotonic() - start_time
            if elapsed > ROUTER_AGENT_TIMEOUT_S:
                logger.warning(f"Router timeout after {elapsed:.0f}s, {len(completed_agents)}/{len(target_agent_ids)} done")
                # Mark remaining as timed out
                for aid in target_agent_ids - completed_agents:
                    t = target_map[aid]
                    yield sse_event("agent_done", {
                        "agent_id": aid,
                        "agent_name": t.agent_name,
                        "role": t.role,
                        "status": "timeout",
                        "summary": "Agent timed out",
                        "ts": int(time.time() * 1000),
                    })
                    completed_agents.add(aid)
                break

            # Check if client disconnected
            if await request.is_disconnected():
                logger.info("Router: client disconnected")
                return

            # Read from Redis stream (block up to 2s to stay responsive)
            try:
                results = await redis_conn.xread(
                    {stream_key: last_id},
                    count=50,
                    block=2000,
                )
            except Exception as e:
                logger.warning(f"Redis xread error: {e}")
                await asyncio.sleep(1)
                continue

            if not results:
                # No new events — send heartbeat
                yield sse_heartbeat()
                continue

            for stream_name, events in results:
                for event_id, event_data in events:
                    last_id = event_id

                    try:
                        event_type = event_data.get("event", "")
                        data_str = event_data.get("data", "{}")
                        data = json.loads(data_str) if isinstance(data_str, str) else data_str
                    except (json.JSONDecodeError, AttributeError):
                        continue

                    # Filter: only care about messages from our target agents
                    if event_type == "new_message":
                        msg_agent_id = str(data.get("agent_id", ""))
                        if msg_agent_id not in target_agent_ids:
                            continue
                        if msg_agent_id in completed_agents:
                            continue

                        content = data.get("content", "")
                        agent_name = data.get("agent_name", data.get("author", ""))
                        t = target_map.get(msg_agent_id)
                        if not t:
                            continue

                        # Emit as chunk
                        agent_seq[msg_agent_id] += 1
                        agent_chunks[msg_agent_id].append(content)

                        yield sse_event("chunk", {
                            "agent_id": msg_agent_id,
                            "agent_name": t.agent_name,
                            "role": t.role,
                            "content": content,
                            "seq": agent_seq[msg_agent_id],
                            "ts": int(time.time() * 1000),
                        })

                        # For now, treat each message as a complete response
                        # (agents send one message, not streaming chunks)
                        completed_agents.add(msg_agent_id)
                        yield sse_event("agent_done", {
                            "agent_id": msg_agent_id,
                            "agent_name": t.agent_name,
                            "role": t.role,
                            "status": "complete",
                            "summary": content[:200] if content else "Done",
                            "ts": int(time.time() * 1000),
                            "usage": {},  # TODO: extract from agent response metadata
                        })

    except Exception as e:
        logger.error(f"Router multiplex error: {e}", exc_info=True)
        yield sse_event("error", {"message": f"Stream error: {str(e)}"})

    # Phase 4: Done event
    results_list = []
    for aid in target_agent_ids:
        t = target_map[aid]
        results_list.append({
            "agent_id": aid,
            "agent_name": t.agent_name,
            "role": t.role,
            "status": "complete" if aid in completed_agents else "timeout",
        })

    yield sse_event("done", {
        "message_id": message_id,
        "results": results_list,
        "router_summary": _build_done_summary(targets, completed_agents),
    })


def _build_router_message(targets: list[AgentTarget]) -> str:
    """Build the router's initial message to the user."""
    if len(targets) == 1:
        return f"Routing to {targets[0].agent_name} — {targets[0].reason}."
    names = ", ".join(t.agent_name for t in targets[:-1]) + f" and {targets[-1].agent_name}"
    return f"On it — sending to {names}. You'll see their progress as they work."


def _build_done_summary(targets: list[AgentTarget], completed: set[str]) -> str:
    """Build the done summary message."""
    total = len(targets)
    done = len(completed)
    if done == total:
        if total == 1:
            return f"{targets[0].agent_name} is done."
        return "All agents done."
    return f"{done}/{total} agents completed."


# --- Endpoint ---

@router.post("/{space_id}/router/message")
async def router_message(
    space_id: UUID,
    body: RouterMessageRequest,
    request: Request,
    secure: SecureSession = Depends(get_secure_session),
):
    """
    Route a message to one or more agents in a space, returning a multiplexed SSE stream.

    The router:
    1. Classifies intent (which agents should handle the message)
    2. Fans out to those agents
    3. Multiplexes their responses back as attributed SSE chunks
    """
    db = secure.db
    user = secure.user
    space_id = str(secure.space_id)

    # Verify space_id matches user's org context
    if str(space_id) != space_id:
        raise HTTPException(status_code=403, detail="Space mismatch — you can only route within your active space.")

    # ── Pre-routing filter pipeline ────────────────────────────────────────────
    # Checks for prompt injection, spam, and gibberish before any classification.
    # If flagged: return a FilteredRoutingResponse with an override_token.
    # The frontend surfaces this as a user alert (not a board message).
    # User can resend with override_token to bypass the filter once.
    #
    # Override flow: resend message with body.override_token set to the issued token.
    _override_used = False
    if body.override_token:
        # Override is admin-only on staging (production policy: revisit with @operator)
        user_is_admin = getattr(user, "role", None) in ("admin", "agent_manager")
        if not user_is_admin:
            raise HTTPException(
                status_code=403,
                detail="Filter overrides are restricted to admin users on staging.",
            )
        # Validate and consume the override token
        if not router_filter_pipeline.validate_override(body.override_token):
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired override token. Request a new one by sending the message again.",
            )
        _override_used = True
        logger.info(f"🛡️ RouterFilter: admin override accepted for user {user.id}")
    else:
        filter_result = router_filter_pipeline.scan(body.content, sender_id=str(user.id))
        if filter_result.flagged:
            logger.warning(
                f"🛡️ RouterFilter: blocked message from {user.id} "
                f"type={filter_result.filter_type} stage={filter_result.stage} "
                f"confidence={filter_result.confidence:.2f}"
            )
            # Log to routing-logs table (best-effort background task)
            import hashlib as _hashlib
            _qhash = _hashlib.sha256(body.content.encode()).hexdigest()[:16]
            asyncio.create_task(_log_filter_event(
                query_hash=_qhash,
                filter_stage=f"flagged_{filter_result.filter_type}",
                filter_reason=filter_result.reason,
                override_used=False,
            ))
            return JSONResponse(
                status_code=422,  # 422 Unprocessable Content — filter blocked routing (spec locked 2026-02-21)
                content={
                    "filtered": True,
                    "filter_type": filter_result.filter_type,
                    "confidence": filter_result.confidence,
                    "reason": filter_result.reason,
                    "override_token": filter_result.override_token,
                    "message": "Message flagged — see reason above. Send again with override_token to allow.",
                },
            )
    # ── End filter pipeline ────────────────────────────────────────────────────

    # Check for default_route_to (skip classification)
    default_result = await db.execute(
        select(SpaceMembership.default_route_to)
        .where(
            SpaceMembership.user_id == user.id,
            SpaceMembership.space_id == space_id,
        )
    )
    default_agent_id = default_result.scalar()

    targets: list[AgentTarget] = []

    # Explicit routing contract (primary + additive mentions)
    # Priority: explicit selection > default route > classifier.
    requested_ids: list[UUID] = []
    seen_requested: set[UUID] = set()

    if body.primary_agent_id:
        requested_ids.append(body.primary_agent_id)
        seen_requested.add(body.primary_agent_id)

    for mention_id in (body.mentioned_agent_ids or []):
        if mention_id not in seen_requested:
            requested_ids.append(mention_id)
            seen_requested.add(mention_id)

    if requested_ids:
        eligible_result = await db.execute(
            select(Agent)
            .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
            .where(
                and_(
                    Agent.id.in_(requested_ids),
                    Agent.status == "active",
                    or_(
                        AgentSpaceAccess.space_id == space_id,
                        Agent.visibility_level == "global",
                    ),
                )
            )
        )
        eligible_agents = {agent.id: agent for agent in eligible_result.scalars().all()}

        for idx, requested_id in enumerate(requested_ids):
            agent = eligible_agents.get(requested_id)
            if not agent:
                logger.warning(
                    "Router explicit target not available in space %s: %s",
                    space_id,
                    requested_id,
                )
                continue

            caps = agent.capabilities or {}
            reason = "selected primary" if idx == 0 and body.primary_agent_id else "explicit mention"
            targets.append(AgentTarget(
                agent_id=str(agent.id),
                agent_name=agent.name,
                role=caps.get("routing_hint", "Assistant"),
                reason=reason,
            ))

    if not targets and default_agent_id:
        # Direct route — skip classification
        agent_result = await db.execute(
            select(Agent).where(Agent.id == default_agent_id, Agent.status == "active")
        )
        agent = agent_result.scalar_one_or_none()
        if agent:
            caps = agent.capabilities or {}
            targets = [AgentTarget(
                agent_id=str(agent.id),
                agent_name=agent.name,
                role=caps.get("routing_hint", "Assistant"),
                reason="default route",
            )]
        else:
            logger.warning(f"Default route agent {default_agent_id} not found/active, falling back to classification")

    if not targets:
        # Get available agents in this space (relaxed filtering for better agent discovery)
        agents_result = await db.execute(
            select(Agent)
            .join(AgentSpaceAccess, AgentSpaceAccess.agent_id == Agent.id)
            .where(
                and_(
                    Agent.status == "active",
                    or_(
                        AgentSpaceAccess.space_id == space_id,
                        Agent.visibility_level == "global",
                    ),
                )
            )
        )
        all_agents = agents_result.scalars().all()

        # Prefer dispatchable agents but include all active agents for routing
        dispatchable_agents = [
            a for a in all_agents
            if (a.cloud_function_url is not None or
                (a.origin == "external_gateway" and a.webhook_url is not None) or
                a.origin == "space_agent" or
                a.origin == "agentcore")
        ]

        # If no dispatchable agents, use all active agents
        available_agents = dispatchable_agents if dispatchable_agents else all_agents

        logger.info(f"🔍 Router: Found {len(all_agents)} total agents, {len(dispatchable_agents)} dispatchable, using {len(available_agents)}")
        logger.info(f"🔍 Router: Available agent names: {[a.name for a in available_agents]}")

        if not available_agents:
            raise HTTPException(status_code=404, detail="No active agents available in this space.")

        agent_dicts = []
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

            agent_dicts.append({
                "id": str(a.id),
                "name": a.name,
                "description": description,
                "origin": a.origin,
            })

        # Classify intent
        classifications = await classify_intent(
            body.content,
            agent_dicts,
            context_window=None,
            sender_identity={
                "actor_type": "human",
                "actor_id": str(user.id),
            },
        )

        for c in classifications[:ROUTER_FAN_OUT_CAP]:
            targets.append(AgentTarget(
                agent_id=c["agent_id"],
                agent_name=c["agent_name"],
                role=c.get("role", "Assistant"),
                reason=c.get("reason", "matched intent"),
            ))

    if not targets:
        raise HTTPException(status_code=404, detail="No agents matched the message intent.")

    conversation_id = body.conversation_id or str(uuid.uuid4())

    return StreamingResponse(
        multiplex_agents(
            targets=targets,
            user_message=body.content,
            space_id=space_id,
            user_id=str(user.id),
            conversation_id=conversation_id,
            request=request,
            db=db,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
