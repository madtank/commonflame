"""
Bedrock AgentCore client helpers.

This module owns the backend-side runtime partitioning metadata for AgentCore
invocations. Phase 1 uses:

- shared AgentCore runtime across spaces
- stable actor scope for long-term reasoning memory
- deterministic per-conversation session ids
- backend-enforced space isolation for every tool call

Usage:
    client = get_agentcore_client()
    result = client.invoke_with_tool_loop(
        agent_id, alias_id, session_id, text,
        tool_executor=my_executor,
    )
"""

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)

# Thread-safe lazy singleton
_client: Optional["AgentCoreClient"] = None
_client_lock = threading.Lock()

# Safety limit: max tool calls per invocation to prevent infinite loops
MAX_TOOL_CALLS_PER_INVOCATION = int(os.getenv("AGENTCORE_MAX_TOOL_CALLS", "10"))


@dataclass
class ToolCall:
    """A tool call returned by the agent via Return of Control."""
    invocation_id: str
    action_group: str
    function_name: str
    parameters: dict


@dataclass
class ToolResult:
    """Result of executing a tool call."""
    action_group: str
    function_name: str
    response_text: str
    success: bool = True


@dataclass
class AgentCoreResponse:
    """Complete response from an invocation (may involve multiple tool call rounds)."""
    text: str
    session_id: str
    tool_calls_made: list = field(default_factory=list)
    tool_call_count: int = 0
    traces: list = field(default_factory=list)


@dataclass
class AgentCoreSessionContext:
    """Backend-owned logical session context for AgentCore invocation."""
    space_id: str
    user_id: str
    conversation_id: str
    dispatch_id: str
    authority_class: Optional[str] = None
    actor_id: Optional[str] = None
    memory_actor_id: Optional[str] = None
    space_name: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    membership_id: Optional[str] = None
    membership_role: Optional[str] = None
    mode: Optional[str] = None
    space_prompt_version: Optional[str] = None
    correlation_id: Optional[str] = None
    message_id: Optional[str] = None
    tool_allowlist: Optional[list[str]] = None
    writes_allowed: Optional[bool] = None
    confirmation_required: Optional[bool] = None


def build_actor_id(
    *,
    space_id: Optional[str] = None,
    user_id: Optional[str] = None,
    actor_scope: str = "space",
) -> str:
    """
    Build the stable AgentCore actor scope.

    Phase 1 defaults to shared space memory (`space:{space_id}`), while keeping
    room for user-scoped and per-user-in-space overlays later.
    """
    if actor_scope == "space":
        if not space_id:
            raise ValueError("space_id is required for actor_scope='space'")
        return f"space:{space_id}"

    if actor_scope == "user":
        if not user_id:
            raise ValueError("user_id is required for actor_scope='user'")
        return f"user:{user_id}"

    if actor_scope == "space_user":
        if not space_id or not user_id:
            raise ValueError("space_id and user_id are required for actor_scope='space_user'")
        return f"space:{space_id}:user:{user_id}"

    raise ValueError(f"Unsupported actor_scope: {actor_scope}")


def build_session_id(
    space_id: str,
    conversation_id: str,
    authority_class: Optional[str] = None,
) -> str:
    """
    Deterministic session ID from (space_id, conversation_id, authority_class).

    This intentionally avoids collapsing all user activity in one space into a
    single runtime lane, and it prevents concierge or management runs from
    silently sharing short-term context with standard space-agent runs.
    """
    session_authority = authority_class or "standard"
    raw = f"{session_authority}:{space_id}:{conversation_id}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:48]
    return f"ax-{digest}"


def build_session_attributes(ctx: AgentCoreSessionContext) -> dict:
    """Build the v1 sessionAttributes payload for backend → AgentCore calls."""
    attrs = {
        "space_id": ctx.space_id,
        "user_id": ctx.user_id,
        "conversation_id": ctx.conversation_id,
        "dispatch_id": ctx.dispatch_id,
    }
    if ctx.authority_class:
        attrs["authority_class"] = ctx.authority_class
    if ctx.actor_id:
        attrs["actor_id"] = ctx.actor_id
    if ctx.memory_actor_id:
        attrs["memory_actor_id"] = ctx.memory_actor_id

    if ctx.space_name:
        attrs["space_name"] = ctx.space_name
    if ctx.agent_id:
        attrs["agent_id"] = ctx.agent_id
    if ctx.agent_name:
        attrs["agent_name"] = ctx.agent_name
    if ctx.membership_id:
        attrs["membership_id"] = ctx.membership_id
    if ctx.membership_role:
        attrs["membership_role"] = ctx.membership_role
    if ctx.mode:
        attrs["mode"] = ctx.mode
    if ctx.space_prompt_version:
        attrs["space_prompt_version"] = ctx.space_prompt_version
    if ctx.correlation_id:
        attrs["correlation_id"] = ctx.correlation_id
    if ctx.message_id:
        attrs["message_id"] = ctx.message_id
    if ctx.tool_allowlist is not None:
        attrs["tool_allowlist"] = ",".join(ctx.tool_allowlist)
    if ctx.writes_allowed is not None:
        attrs["writes_allowed"] = str(ctx.writes_allowed).lower()
    if ctx.confirmation_required is not None:
        attrs["confirmation_required"] = str(ctx.confirmation_required).lower()

    return attrs


class AgentCoreClient:
    """
    Thin wrapper around bedrock-agent-runtime invoke_agent with Return of Control.
    Thread-safe: boto3 clients are thread-safe for API calls.
    """

    def __init__(
        self,
        region: Optional[str] = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 120.0,
        max_retries: int = 3,
    ):
        self._region = region or os.getenv("BEDROCK_AGENT_REGION", os.getenv("AWS_REGION", "us-west-2"))
        self._boto_config = BotoConfig(
            region_name=self._region,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            retries={"max_attempts": max_retries, "mode": "adaptive"},
        )
        self._client = boto3.client(
            "bedrock-agent-runtime",
            config=self._boto_config,
        )
        logger.info(f"AgentCoreClient initialized region={self._region}")

    def _invoke_agent(
        self,
        agent_id: str,
        agent_alias_id: str,
        session_id: str,
        input_text: str = "",
        enable_trace: bool = False,
        session_state: Optional[dict] = None,
        end_session: bool = False,
        on_text_chunk: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Raw invoke_agent call. Returns parsed response with completion chunks or returnControl."""
        kwargs = {
            "agentId": agent_id,
            "agentAliasId": agent_alias_id,
            "sessionId": session_id,
            "enableTrace": enable_trace,
        }
        if input_text:
            kwargs["inputText"] = input_text
        if session_state:
            kwargs["sessionState"] = session_state
        if end_session:
            kwargs["endSession"] = end_session

        response = self._client.invoke_agent(**kwargs)

        # Parse the EventStream
        text_parts = []
        traces = []
        return_control = None

        for event in response.get("completion", []):
            if "chunk" in event:
                chunk_text = event["chunk"].get("bytes", b"").decode("utf-8")
                if chunk_text:
                    text_parts.append(chunk_text)
                    if on_text_chunk:
                        on_text_chunk(chunk_text)
            elif "trace" in event:
                traces.append(event["trace"])
            elif "returnControl" in event:
                return_control = event["returnControl"]

        return {
            "text": "".join(text_parts),
            "traces": traces,
            "return_control": return_control,
        }

    def invoke_with_tool_loop(
        self,
        agent_id: str,
        agent_alias_id: str,
        session_id: str,
        input_text: str,
        tool_executor: Callable[[ToolCall], ToolResult],
        enable_trace: bool = False,
        session_state: Optional[dict] = None,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_INVOCATION,
        on_text_chunk: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[ToolCall], None]] = None,
        on_tool_end: Optional[Callable[[ToolCall, ToolResult], None]] = None,
    ) -> AgentCoreResponse:
        """
        Invoke agent with Return of Control tool loop.

        1. Call invoke_agent with user input
        2. If agent returns RETURN_CONTROL, execute the tool via tool_executor
        3. Call invoke_agent again with tool results
        4. Repeat until agent returns final text or max_tool_calls reached

        Args:
            tool_executor: Function that takes a ToolCall and returns a ToolResult.
                           This is where MCP server calls happen.
        """
        tool_calls_made = []
        all_traces = []
        tool_call_count = 0
        response_parts = []

        logger.info(
            f"AGENTCORE_INVOKE agent_id={agent_id} alias={agent_alias_id} "
            f"session={session_id} input_len={len(input_text)}"
        )

        # First call with user input
        result = self._invoke_agent(
            agent_id=agent_id,
            agent_alias_id=agent_alias_id,
            session_id=session_id,
            input_text=input_text,
            enable_trace=enable_trace,
            session_state=session_state,
            on_text_chunk=on_text_chunk,
        )
        if result["text"]:
            response_parts.append(result["text"])
        all_traces.extend(result["traces"])

        # Return of Control loop
        while result["return_control"] and tool_call_count < max_tool_calls:
            rc = result["return_control"]
            invocation_id = rc.get("invocationId", "")
            invocation_inputs = rc.get("invocationInputs", [])

            tool_results = []
            for inv_input in invocation_inputs:
                func_input = inv_input.get("functionInvocationInput", {})
                tool_call = ToolCall(
                    invocation_id=invocation_id,
                    action_group=func_input.get("actionGroup", ""),
                    function_name=func_input.get("function", ""),
                    parameters={
                        p["name"]: p.get("value", "")
                        for p in func_input.get("parameters", [])
                    },
                )

                logger.info(
                    f"AGENTCORE_TOOL_CALL session={session_id} "
                    f"function={tool_call.function_name} "
                    f"params={json.dumps(tool_call.parameters)}"
                )

                tool_calls_made.append(tool_call)
                tool_call_count += 1
                if on_tool_start:
                    on_tool_start(tool_call)

                # Execute the tool via callback
                try:
                    tool_result = tool_executor(tool_call)
                except Exception as e:
                    logger.error(
                        f"AGENTCORE_TOOL_ERROR session={session_id} "
                        f"function={tool_call.function_name} error={e}"
                    )
                    tool_result = ToolResult(
                        action_group=tool_call.action_group,
                        function_name=tool_call.function_name,
                        response_text=f"Tool execution failed: {e}",
                        success=False,
                    )
                if on_tool_end:
                    on_tool_end(tool_call, tool_result)

                tool_results.append({
                    "functionResult": {
                        "actionGroup": tool_result.action_group,
                        "function": tool_result.function_name,
                        "responseBody": {
                            "TEXT": {"body": tool_result.response_text}
                        },
                    }
                })

            # Send tool results back to agent
            result = self._invoke_agent(
                agent_id=agent_id,
                agent_alias_id=agent_alias_id,
                session_id=session_id,
                enable_trace=enable_trace,
                session_state={
                    "invocationId": invocation_id,
                    "returnControlInvocationResults": tool_results,
                },
                on_text_chunk=on_text_chunk,
            )
            if result["text"]:
                response_parts.append(result["text"])
            all_traces.extend(result["traces"])

        if tool_call_count >= max_tool_calls and result["return_control"]:
            logger.warning(
                f"AGENTCORE_TOOL_LIMIT session={session_id} "
                f"max_calls={max_tool_calls} - forcing final response"
            )

        return AgentCoreResponse(
            text="".join(response_parts),
            session_id=session_id,
            tool_calls_made=tool_calls_made,
            tool_call_count=tool_call_count,
            traces=all_traces,
        )

    def end_session(self, agent_id: str, agent_alias_id: str, session_id: str) -> None:
        """Explicitly end a Bedrock Agent session."""
        try:
            self._invoke_agent(
                agent_id=agent_id,
                agent_alias_id=agent_alias_id,
                session_id=session_id,
                end_session=True,
            )
            logger.info(f"AGENTCORE_SESSION_END session={session_id}")
        except Exception as e:
            logger.warning(f"AGENTCORE_SESSION_END_ERROR session={session_id} error={e}")


def get_agentcore_client() -> AgentCoreClient:
    """Get or create singleton AgentCore client (thread-safe)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = AgentCoreClient()
    return _client
