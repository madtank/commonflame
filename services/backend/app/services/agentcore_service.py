"""
AgentCore service — high-level Bedrock Agent invocation with space isolation.

Phase 1 AgentCore cutover keeps the backend authority model unchanged:

- one shared AgentCore runtime reused across spaces
- stable actor scope for reasoning memory
- deterministic per-conversation session ids
- backend-enforced tool access and effective-space resolution

This service is called by the dispatch executor for agents with origin='agentcore'.
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.core.agentcore import (
    AgentCoreResponse,
    AgentCoreSessionContext,
    ToolCall,
    ToolResult,
    build_actor_id,
    build_session_attributes,
    build_session_id,
    get_agentcore_client,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class AgentCoreDispatchResult:
    """Result of an AgentCore dispatch."""
    success: bool
    response_text: str = ""
    session_id: str = ""
    dispatch_id: str = ""
    duration_ms: int = 0
    tool_call_count: int = 0
    traces: list = field(default_factory=list)
    stream_message_id: Optional[str] = None
    tools_used: list[str] = field(default_factory=list)
    error: Optional[str] = None


# Environment config — Terraform outputs feed these
BEDROCK_AGENT_ID = os.getenv("BEDROCK_AGENT_ID", "")
BEDROCK_AGENT_ALIAS_ID = os.getenv("BEDROCK_AGENT_ALIAS_ID", "")
BEDROCK_AGENT_ENABLE_TRACE = os.getenv("BEDROCK_AGENT_ENABLE_TRACE", "false").lower() == "true"

# MCP tool execution timeout
MCP_TOOL_TIMEOUT_SECONDS = int(os.getenv("MCP_TOOL_TIMEOUT_SECONDS", "30"))


def _parse_mcp_jsonrpc_response(response: httpx.Response) -> dict:
    """Parse MCP JSON-RPC response bodies from JSON or SSE payloads."""
    content_type = (response.headers.get("content-type") or "").lower()
    if "text/event-stream" in content_type:
        for line in reversed(response.text.strip().splitlines()):
            if line.startswith("data: "):
                try:
                    return json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
        raise ValueError("No JSON-RPC payload found in SSE response")

    return response.json()


def _normalize_mcp_structured_content(tool_name: str, structured: object) -> object:
    """Normalize tool payloads into an agent-facing schema."""
    if not isinstance(structured, dict):
        return structured

    if tool_name == "tasks" and structured.get("kind") == "task_collection":
        data = structured.get("data") if isinstance(structured.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
        return {
            "kind": "task_collection",
            "state": structured.get("state"),
            "filter": scope.get("filter") or data.get("filter") or "all",
            "count": len(items),
            "total": data.get("total", len(items)),
            "tasks": items,
        }

    return structured


def _extract_mcp_tool_response_text(tool_name: str, result_data: dict) -> str:
    """
    Convert MCP JSON-RPC results into the text body Bedrock expects.

    Prefer structuredContent when present so the agent receives the canonical
    machine-readable payload, not just a human summary sentence.
    """
    result = result_data.get("result", {}) if isinstance(result_data, dict) else {}
    structured = result.get("structuredContent")
    if structured is not None:
        normalized = _normalize_mcp_structured_content(tool_name, structured)
        return json.dumps(normalized, separators=(",", ":"))

    content = result.get("content", [])
    if not content:
        return json.dumps(result_data, separators=(",", ":"))

    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and item.get("text") is not None:
            text_parts.append(str(item["text"]))

    if text_parts:
        return "\n".join(text_parts)

    return json.dumps(result_data, separators=(",", ":"))


# =============================================================================
# MCP Tool Executor — bridges Bedrock Return of Control to MCP server
# =============================================================================

# Map Bedrock function names → MCP tool names
FUNCTION_TO_MCP_TOOL = {
    "searchMessages": "search",
    "sendMessage": "messages",
    "listAgents": "agents",
    "createTask": "tasks",
    "listTasks": "tasks",
    "getSpaceContext": "context",
}

# Map Bedrock function names → MCP action
FUNCTION_TO_MCP_ACTION = {
    "searchMessages": "search",
    "sendMessage": "send",
    "listAgents": "list",
    "createTask": "create",
    "listTasks": "list",
    "getSpaceContext": "get",
}

_BOUND_SPACE_PARAM_KEYS = ("space_id", "org_id")
_TARGET_SPACE_PARAM_KEYS = ("target_space_id", "target_org_id")


def _derive_authority_class(system_context: Optional[dict]) -> str:
    """Resolve the runtime authority class for the invocation."""
    return str((system_context or {}).get("authority_class") or "space_concierge")


def _resolve_actor_scope(
    *,
    space_id: str,
    user_id: str,
    authority_class: str,
    system_context: Optional[dict],
) -> tuple[str, str]:
    """
    Resolve the stable ActorCore actor scope.

    Returns:
        actor_id, memory_actor_id
    """
    context = system_context or {}
    actor_scope = str(context.get("actor_scope") or "space")

    expected_actor_id = build_actor_id(
        space_id=space_id,
        user_id=user_id,
        actor_scope=actor_scope,
    )
    provided_actor_id = context.get("actor_id")
    if provided_actor_id and provided_actor_id != expected_actor_id:
        logger.warning(
            "AGENTCORE_ACTOR_SCOPE_MISMATCH dispatch_id=%s space_id=%s "
            "authority_class=%s provided_actor_id=%s resolved_actor_id=%s",
            context.get("dispatch_id"),
            space_id,
            authority_class,
            provided_actor_id,
            expected_actor_id,
        )

    memory_actor_id = str(context.get("memory_actor_id") or expected_actor_id)
    if memory_actor_id != expected_actor_id:
        logger.warning(
            "AGENTCORE_MEMORY_SCOPE_UNEXPECTED dispatch_id=%s space_id=%s "
            "authority_class=%s actor_id=%s memory_actor_id=%s",
            context.get("dispatch_id"),
            space_id,
            authority_class,
            expected_actor_id,
            memory_actor_id,
        )
        memory_actor_id = expected_actor_id

    return expected_actor_id, memory_actor_id


def _normalize_mcp_arguments(
    *,
    parameters: dict,
    space_id: str,
    dispatch_id: str,
    actor_id: str,
    session_id: str,
    authority_class: str,
) -> dict:
    """Fail closed on caller-supplied space scope and strip redundant keys."""
    normalized = dict(parameters or {})

    for key in _BOUND_SPACE_PARAM_KEYS:
        supplied = normalized.pop(key, None)
        if supplied is None:
            continue
        if str(supplied) != str(space_id):
            logger.warning(
                "AGENTCORE_SPACE_CONTEXT_MISMATCH dispatch_id=%s space_id=%s "
                "actor_id=%s session_id=%s authority_class=%s param_key=%s supplied_space_id=%s",
                dispatch_id,
                space_id,
                actor_id,
                session_id,
                authority_class,
                key,
                supplied,
            )
            raise ValueError(f"Tool argument {key} targets a different space")

    for key in _TARGET_SPACE_PARAM_KEYS:
        supplied = normalized.pop(key, None)
        if supplied is None:
            continue
        if str(supplied) != str(space_id):
            logger.warning(
                "AGENTCORE_SESSION_SCOPE_REJECTED dispatch_id=%s space_id=%s "
                "actor_id=%s session_id=%s authority_class=%s param_key=%s supplied_target_space_id=%s",
                dispatch_id,
                space_id,
                actor_id,
                session_id,
                authority_class,
                key,
                supplied,
            )
            raise ValueError(f"Tool argument {key} exceeds the active space scope")

    return normalized


def _build_mcp_tool_executor(
    mcp_auth_token: str,
    space_id: str,
    actor_id: str,
    session_id: str,
    dispatch_id: str,
    authority_class: str,
    mcp_base_url: str,
) -> callable:
    """
    Build a synchronous tool executor that calls MCP server.

    Returns a function: ToolCall → ToolResult

    Defense in depth:
    - MCP token is scoped to (space_id, agent_id) — can only access this space
    - MCP server validates token and enforces space_id on every call
    - Database RLS filters by space_id even if MCP somehow leaks
    """
    def execute_tool(tool_call: ToolCall) -> ToolResult:
        if tool_call.function_name == "readMessages":
            return ToolResult(
                action_group=tool_call.action_group,
                function_name=tool_call.function_name,
                response_text=json.dumps(
                    {
                        "kind": "conversation_context_notice",
                        "message": (
                            "Current conversation history is already available in runtime context. "
                            "Use searchMessages only when you need older or broader space history."
                        ),
                    },
                    separators=(",", ":"),
                ),
                success=True,
            )

        mcp_tool = FUNCTION_TO_MCP_TOOL.get(tool_call.function_name)
        mcp_action = FUNCTION_TO_MCP_ACTION.get(tool_call.function_name)

        if not mcp_tool or not mcp_action:
            return ToolResult(
                action_group=tool_call.action_group,
                function_name=tool_call.function_name,
                response_text=f"Unknown function: {tool_call.function_name}",
                success=False,
            )

        try:
            normalized_parameters = _normalize_mcp_arguments(
                parameters=tool_call.parameters,
                space_id=space_id,
                dispatch_id=dispatch_id,
                actor_id=actor_id,
                session_id=session_id,
                authority_class=authority_class,
            )
        except ValueError as exc:
            return ToolResult(
                action_group=tool_call.action_group,
                function_name=tool_call.function_name,
                response_text=str(exc),
                success=False,
            )

        # Build MCP request body
        mcp_body = {
            "action": mcp_action,
            **normalized_parameters,
        }

        # Special handling for specific functions
        if tool_call.function_name == "sendMessage":
            mcp_body = {
                "action": "send",
                "content": normalized_parameters.get("content", ""),
            }
            if normalized_parameters.get("reply_to_message_id"):
                mcp_body["reply_to"] = normalized_parameters["reply_to_message_id"]

        elif tool_call.function_name == "searchMessages":
            mcp_body = {
                "action": "search",
                "query": normalized_parameters.get("query", ""),
                "limit": int(normalized_parameters.get("limit", "10")),
            }

        try:
            # Synchronous HTTP call to MCP server (we're in a thread pool)
            with httpx.Client(timeout=MCP_TOOL_TIMEOUT_SECONDS) as client:
                response = client.post(
                    f"{mcp_base_url}/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": str(uuid_module.uuid4()),
                        "method": "tools/call",
                        "params": {
                            "name": mcp_tool,
                            "arguments": mcp_body,
                        },
                    },
                    headers={
                        "Authorization": f"Bearer {mcp_auth_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                        "X-Space-Id": space_id,
                    },
                )

            if response.is_success:
                result_data = _parse_mcp_jsonrpc_response(response)
                return ToolResult(
                    action_group=tool_call.action_group,
                    function_name=tool_call.function_name,
                    response_text=_extract_mcp_tool_response_text(mcp_tool, result_data),
                    success=True,
                )
            else:
                if response.status_code in (401, 403):
                    logger.warning(
                        "AGENTCORE_TOOL_RLS_DENIED dispatch_id=%s space_id=%s "
                        "actor_id=%s session_id=%s authority_class=%s tool=%s action=%s status=%s",
                        dispatch_id,
                        space_id,
                        actor_id,
                        session_id,
                        authority_class,
                        mcp_tool,
                        mcp_action,
                        response.status_code,
                    )
                return ToolResult(
                    action_group=tool_call.action_group,
                    function_name=tool_call.function_name,
                    response_text=f"MCP server returned {response.status_code}: {response.text[:200]}",
                    success=False,
                )

        except Exception as e:
            return ToolResult(
                action_group=tool_call.action_group,
                function_name=tool_call.function_name,
                response_text=f"MCP call failed: {type(e).__name__}: {e}",
                success=False,
            )

    return execute_tool


# =============================================================================
# Main dispatch entry point
# =============================================================================

async def invoke_space_agent(
    space_id: str,
    user_id: str,
    conversation_id: str,
    input_text: str,
    dispatch_id: str,
    mcp_auth_token: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_alias_id: Optional[str] = None,
    system_context: Optional[dict] = None,
    correlation_id: Optional[str] = None,
) -> AgentCoreDispatchResult:
    """
    Invoke the space agent via Bedrock AgentCore with Return of Control tool loop.

    Args:
        space_id: Space UUID (tenant isolation boundary)
        user_id: User UUID for actor identity
        conversation_id: Backend-owned logical conversation/session identifier
        input_text: User message text
        dispatch_id: Dispatch correlation ID
        mcp_auth_token: Space-scoped MCP auth token for tool execution
        agent_id: Override Bedrock agent ID (default: env BEDROCK_AGENT_ID)
        agent_alias_id: Override alias ID (default: env BEDROCK_AGENT_ALIAS_ID)
        system_context: Optional context (space_name, sender, agents)
        correlation_id: End-to-end trace ID
    """
    effective_agent_id = agent_id or BEDROCK_AGENT_ID
    effective_alias_id = agent_alias_id or BEDROCK_AGENT_ALIAS_ID

    if not effective_agent_id or not effective_alias_id:
        return AgentCoreDispatchResult(
            success=False,
            dispatch_id=dispatch_id,
            error="BEDROCK_AGENT_ID and BEDROCK_AGENT_ALIAS_ID must be configured",
        )

    context = dict(system_context or {})
    context["dispatch_id"] = dispatch_id
    authority_class = _derive_authority_class(context)
    actor_id, memory_actor_id = _resolve_actor_scope(
        space_id=space_id,
        user_id=user_id,
        authority_class=authority_class,
        system_context=context,
    )

    session_ctx = AgentCoreSessionContext(
        space_id=space_id,
        user_id=user_id,
        conversation_id=conversation_id,
        dispatch_id=dispatch_id,
        authority_class=authority_class,
        actor_id=actor_id,
        memory_actor_id=memory_actor_id,
        space_name=context.get("space_name"),
        agent_id=effective_agent_id,
        agent_name=context.get("agent_name"),
        membership_id=context.get("membership_id"),
        membership_role=context.get("membership_role"),
        mode=context.get("mode"),
        space_prompt_version=context.get("space_prompt_version"),
        correlation_id=correlation_id,
        message_id=context.get("message_id"),
        tool_allowlist=context.get("tool_allowlist"),
        writes_allowed=context.get("writes_allowed"),
        confirmation_required=context.get("confirmation_required"),
    )
    session_id = build_session_id(
        space_id,
        conversation_id,
        authority_class=authority_class,
    )
    settings = get_settings()
    parent_message_id = context.get("message_id")
    display_agent_name = str(context.get("agent_name") or context.get("display_name") or "aX")
    platform_agent_id = str(context.get("platform_agent_id") or "")
    reply_stream_id = str(uuid_module.uuid4())
    tools_used: list[str] = []
    loop = asyncio.get_running_loop()
    pending_sse: list[concurrent.futures.Future] = []

    from app.services.redis_sse_broker import redis_sse_broker

    logger.info(
        f"AGENTCORE_DISPATCH_START dispatch_id={dispatch_id} "
        f"space_id={space_id} user_id={user_id} actor_id={actor_id} "
        f"authority_class={authority_class} session_id={session_id} "
        f"correlation_id={correlation_id} input_len={len(input_text)}"
    )

    start_time = time.monotonic()

    async def _publish_sse(event: str, data: dict) -> None:
        await redis_sse_broker.publish(space_id=space_id, event=event, data=data)

    def _schedule_sse(event: str, data: dict) -> None:
        future = asyncio.run_coroutine_threadsafe(_publish_sse(event, data), loop)
        pending_sse.append(future)

    try:
        await _publish_sse(
            "agent_processing",
            {
                "agent_id": platform_agent_id,
                "agent_name": display_agent_name,
                "message_id": reply_stream_id,
                "parent_id": parent_message_id,
                "dispatch_id": dispatch_id,
                "status": "processing",
                "dispatch_type": "agentcore",
            },
        )
    except Exception as sse_err:
        logger.warning(
            "AGENTCORE_STREAM_SSE_ERROR dispatch_id=%s session_id=%s err=%s",
            dispatch_id,
            session_id,
            sse_err,
        )

    # Build session state from backend-owned logical session context
    session_state = {
        "sessionAttributes": build_session_attributes(session_ctx),
        "promptSessionAttributes": {},
    }
    if context.get("sender_handle"):
        session_state["promptSessionAttributes"]["sender_handle"] = context["sender_handle"]

    # Build MCP tool executor (space-scoped)
    tool_executor = None
    if mcp_auth_token:
        tool_executor = _build_mcp_tool_executor(
            mcp_auth_token=mcp_auth_token,
            space_id=space_id,
            actor_id=actor_id,
            session_id=session_id,
            dispatch_id=dispatch_id,
            authority_class=authority_class,
            mcp_base_url=settings.mcp_server_url,
        )
    else:
        # Fallback: no-op executor that tells agent tools are unavailable
        def no_tools(tc: ToolCall) -> ToolResult:
            return ToolResult(
                action_group=tc.action_group,
                function_name=tc.function_name,
                response_text="Tools are not available — MCP auth token was not provided.",
                success=False,
            )
        tool_executor = no_tools
        logger.warning(
            f"AGENTCORE_NO_MCP_AUTH dispatch_id={dispatch_id} "
            f"space_id={space_id} actor_id={actor_id} "
            f"authority_class={authority_class} — tools will be unavailable"
        )

    try:
        client = get_agentcore_client()

        def _on_text_chunk(delta: str) -> None:
            if not delta:
                return
            _schedule_sse(
                "message_delta",
                {
                    "agent_id": platform_agent_id,
                    "agent_name": display_agent_name,
                    "message_id": reply_stream_id,
                    "parent_id": parent_message_id,
                    "dispatch_id": dispatch_id,
                    "delta": delta,
                },
            )

        def _on_tool_start(tool_call: ToolCall) -> None:
            tool_name = FUNCTION_TO_MCP_TOOL.get(tool_call.function_name) or tool_call.function_name
            tools_used.append(tool_name)
            _schedule_sse(
                "agent_processing",
                {
                    "agent_id": platform_agent_id,
                    "agent_name": display_agent_name,
                    "message_id": reply_stream_id,
                    "parent_id": parent_message_id,
                    "dispatch_id": dispatch_id,
                    "status": "tool_use",
                    "tool": tool_name,
                    "dispatch_type": "agentcore",
                },
            )

        def _on_tool_end(tool_call: ToolCall, tool_result: ToolResult) -> None:
            tool_name = FUNCTION_TO_MCP_TOOL.get(tool_call.function_name) or tool_call.function_name
            _schedule_sse(
                "agent_processing",
                {
                    "agent_id": platform_agent_id,
                    "agent_name": display_agent_name,
                    "message_id": reply_stream_id,
                    "parent_id": parent_message_id,
                    "dispatch_id": dispatch_id,
                    "status": "processing",
                    "tool": tool_name,
                    "tool_success": tool_result.success,
                    "dispatch_type": "agentcore",
                },
            )

        # Run synchronous boto3 call + tool loop in thread pool
        response: AgentCoreResponse = await loop.run_in_executor(
            None,
            lambda: client.invoke_with_tool_loop(
                agent_id=effective_agent_id,
                agent_alias_id=effective_alias_id,
                session_id=session_id,
                input_text=input_text,
                tool_executor=tool_executor,
                enable_trace=BEDROCK_AGENT_ENABLE_TRACE,
                session_state=session_state,
                on_text_chunk=_on_text_chunk,
                on_tool_start=_on_tool_start,
                on_tool_end=_on_tool_end,
            ),
        )

        for future in pending_sse:
            try:
                await asyncio.wrap_future(future)
            except Exception as sse_err:
                logger.warning(
                    "AGENTCORE_STREAM_SSE_ERROR dispatch_id=%s session_id=%s err=%s",
                    dispatch_id,
                    session_id,
                    sse_err,
                )

        duration_ms = int((time.monotonic() - start_time) * 1000)

        logger.info(
            f"AGENTCORE_DISPATCH_DONE dispatch_id={dispatch_id} "
            f"session_id={session_id} duration_ms={duration_ms} "
            f"response_len={len(response.text)} "
            f"tool_calls={response.tool_call_count}"
        )

        return AgentCoreDispatchResult(
            success=True,
            response_text=response.text,
            session_id=session_id,
            dispatch_id=dispatch_id,
            duration_ms=duration_ms,
            tool_call_count=response.tool_call_count,
            traces=response.traces,
            stream_message_id=reply_stream_id,
            tools_used=tools_used,
        )

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        error_msg = f"{type(e).__name__}: {e}"

        try:
            await _publish_sse(
                "agent_error",
                {
                    "agent_id": platform_agent_id,
                    "agent_name": display_agent_name,
                    "message_id": reply_stream_id,
                    "parent_id": parent_message_id,
                    "dispatch_id": dispatch_id,
                    "error": error_msg[:200],
                    "error_type": type(e).__name__,
                },
            )
        except Exception:
            pass

        logger.error(
            f"AGENTCORE_DISPATCH_ERROR dispatch_id={dispatch_id} "
            f"session_id={session_id} duration_ms={duration_ms} "
            f"error={error_msg}"
        )

        return AgentCoreDispatchResult(
            success=False,
            session_id=session_id,
            dispatch_id=dispatch_id,
            duration_ms=duration_ms,
            stream_message_id=reply_stream_id,
            tools_used=tools_used,
            error=error_msg,
        )


async def end_space_agent_session(
    space_id: str,
    user_id: str,
    conversation_id: Optional[str] = None,
    authority_class: str = "space_concierge",
    agent_id: Optional[str] = None,
    agent_alias_id: Optional[str] = None,
) -> bool:
    """Explicitly end a Bedrock Agent session."""
    effective_agent_id = agent_id or BEDROCK_AGENT_ID
    effective_alias_id = agent_alias_id or BEDROCK_AGENT_ALIAS_ID
    effective_conversation_id = conversation_id or user_id
    session_id = build_session_id(
        space_id,
        effective_conversation_id,
        authority_class=authority_class,
    )

    try:
        client = get_agentcore_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: client.end_session(effective_agent_id, effective_alias_id, session_id),
        )
        return True
    except Exception as e:
        logger.warning(f"AGENTCORE_SESSION_END_ERROR session={session_id} error={e}")
        return False
