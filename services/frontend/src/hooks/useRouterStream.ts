/**
 * useRouterStream — React hook for router SSE streaming
 *
 * Consumes the multiplexed SSE stream from
 * POST /api/v1/spaces/{space_id}/router/message
 *
 * Groups chunks by agent_id, tracks per-agent state, emits
 * 50ms-debounced updates to avoid thrashing React.
 *
 * Spec: spec-router-streaming-v1.md (cipher, 2026-02-17)
 */

import { useState, useRef, useCallback } from "react";
import { config } from "@/config/environment";
import { storage } from "@/lib/storage";

// --- SSE event payloads (matching backend spec) ---

export interface RoutingTarget {
  agent_id: string;
  role: string;
  reason: string;
}

export interface RoutingEvent {
  message_id?: string;
  conversation_id?: string;
  targets?: RoutingTarget[];
  selected_agents?: Array<
    | string
    | {
        agent_id?: string;
        id?: string;
        role?: string;
        reason?: string;
        name?: string;
      }
  >;
  router_message?: string;
  reasoning?: string;
}

export interface ChunkEvent {
  agent_id?: string;
  agent?: string;
  role?: string;
  content?: string;
  delta?: string;
  text?: string;
  seq?: number;
  ts?: number;
}

export interface AgentDoneEvent {
  agent_id: string;
  role: string;
  summary: string;
  ts: number;
  usage?: { prompt_tokens: number; completion_tokens: number };
}

export interface DoneEvent {
  message_id?: string;
  conversation_id?: string;
  results?: Array<{
    agent_id: string;
    role: string;
    status: "complete" | "error";
  }>;
  router_summary?: string;
  summary?: string;
}

// --- Per-agent card state ---

export type AgentCardStatus = "pending" | "streaming" | "done" | "error";

export interface AgentCardState {
  agent_id: string;
  role: string;
  reason: string;
  content: string;
  status: AgentCardStatus;
  summary?: string;
  errorText?: string;
  usage?: { prompt_tokens: number; completion_tokens: number };
  startedAt: number;
  lastSeq: number;
}

// --- Overall router stream state ---

export type RouterPhase = "idle" | "routing" | "streaming" | "done" | "error";

export interface RouterStreamState {
  phase: RouterPhase;
  messageId: string | null;
  routerMessage: string | null;
  routerSummary: string | null;
  errorText?: string;
  errorInfo?: RouterStreamErrorInfo;
  agents: Map<string, AgentCardState>;
  timings: RouterStreamTimings;
}

export interface RouterStreamErrorInfo {
  statusCode?: number;
  filtered?: boolean;
  filterType?: string | null;
  confidence?: number | null;
  reason?: string | null;
  message?: string | null;
  overrideToken?: string | null;
  raw?: string | null;
}

export interface RouterStreamTimings {
  requestStartedAt: number | null;
  responseStartedAt: number | null;
  firstRoutingEventAt: number | null;
  firstChunkEventAt: number | null;
  firstAgentDoneAt: number | null;
  doneEventAt: number | null;
  lastEventAt: number | null;
  eventCount: number;
  chunkCount: number;
  parseErrorCount: number;
}

const INITIAL_TIMINGS: RouterStreamTimings = {
  requestStartedAt: null,
  responseStartedAt: null,
  firstRoutingEventAt: null,
  firstChunkEventAt: null,
  firstAgentDoneAt: null,
  doneEventAt: null,
  lastEventAt: null,
  eventCount: 0,
  chunkCount: 0,
  parseErrorCount: 0,
};

const INITIAL_STATE: RouterStreamState = {
  phase: "idle",
  messageId: null,
  routerMessage: null,
  routerSummary: null,
  errorText: undefined,
  errorInfo: undefined,
  agents: new Map(),
  timings: INITIAL_TIMINGS,
};

function normalizeRoutingTargets(input: RoutingEvent): RoutingTarget[] {
  if (Array.isArray(input.targets) && input.targets.length > 0) {
    return input.targets
      .map((target) => ({
        agent_id: target.agent_id,
        role: target.role || "Agent",
        reason: target.reason || "",
      }))
      .filter((target) => Boolean(target.agent_id));
  }

  if (
    Array.isArray(input.selected_agents) &&
    input.selected_agents.length > 0
  ) {
    return input.selected_agents
      .map((target) => {
        if (typeof target === "string") {
          return {
            agent_id: target,
            role: "Agent",
            reason: input.reasoning || "",
          };
        }

        const agentId = target.agent_id || target.id || target.name || "";
        return {
          agent_id: agentId,
          role: target.role || "Agent",
          reason: target.reason || input.reasoning || "",
        };
      })
      .filter((target) => Boolean(target.agent_id));
  }

  return [];
}

function normalizeChunkEvent(input: ChunkEvent) {
  return {
    agent_id: input.agent_id || input.agent || "stream",
    role: input.role || "Agent",
    content: input.content || input.delta || input.text || "",
    seq: typeof input.seq === "number" ? input.seq : Date.now(),
    ts: typeof input.ts === "number" ? input.ts : Date.now(),
  };
}

/**
 * Hook for consuming router SSE streams.
 *
 * Usage:
 *   const { state, sendToRouter, abort } = useRouterStream(spaceId);
 *   sendToRouter("Build me a landing page");
 */
export function useRouterStream(spaceId: string | null) {
  const [state, setState] = useState<RouterStreamState>(INITIAL_STATE);
  const accRef = useRef<RouterStreamState>({
    ...INITIAL_STATE,
    agents: new Map(),
    timings: { ...INITIAL_TIMINGS },
  });
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const flush = useCallback(() => {
    flushTimer.current = null;
    setState({
      ...accRef.current,
      agents: new Map(accRef.current.agents),
      timings: { ...accRef.current.timings },
    });
  }, []);

  const scheduleFlush = useCallback(() => {
    if (flushTimer.current === null) {
      flushTimer.current = setTimeout(flush, 50);
    }
  }, [flush]);

  const flushNow = useCallback(() => {
    if (flushTimer.current) {
      clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
    flush();
  }, [flush]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (flushTimer.current) {
      clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
  }, []);

  const processEvent = useCallback(
    (event: string, data: unknown) => {
      const acc = accRef.current;
      const now = Date.now();
      acc.timings.eventCount += 1;
      acc.timings.lastEventAt = now;

      switch (event) {
        case "routing": {
          const d = data as RoutingEvent;
          acc.phase = "routing";
          acc.timings.firstRoutingEventAt ??= now;
          acc.messageId = d.message_id || d.conversation_id || acc.messageId;
          acc.routerMessage =
            d.router_message || d.reasoning || acc.routerMessage;
          for (const t of normalizeRoutingTargets(d)) {
            acc.agents.set(t.agent_id, {
              agent_id: t.agent_id,
              role: t.role,
              reason: t.reason,
              content: "",
              status: "pending",
              startedAt: Date.now(),
              lastSeq: -1,
            });
          }
          flushNow();
          break;
        }

        case "chunk": {
          const d = normalizeChunkEvent(data as ChunkEvent);
          acc.phase = "streaming";
          acc.timings.firstChunkEventAt ??= now;
          acc.timings.chunkCount += 1;
          const card = acc.agents.get(d.agent_id);
          if (card) {
            card.content += d.content;
            card.status = "streaming";
            card.lastSeq = d.seq;
          } else {
            // Chunk arrived before routing event — create card
            acc.agents.set(d.agent_id, {
              agent_id: d.agent_id,
              role: d.role,
              reason: "",
              content: d.content,
              status: "streaming",
              startedAt: Date.now(),
              lastSeq: d.seq,
            });
          }
          scheduleFlush();
          break;
        }

        case "agent_done": {
          const d = data as AgentDoneEvent;
          acc.timings.firstAgentDoneAt ??= now;
          const card = acc.agents.get(d.agent_id);
          if (card) {
            card.status = "done";
            card.summary = d.summary;
            card.usage = d.usage;
          }
          flushNow();
          break;
        }

        case "done": {
          const d = data as DoneEvent;
          acc.phase = "done";
          acc.timings.doneEventAt ??= now;
          acc.messageId = d.message_id || d.conversation_id || acc.messageId;
          acc.routerSummary =
            d.router_summary || d.summary || acc.routerSummary;
          // Mark any agents that errored
          for (const r of d.results || []) {
            const card = acc.agents.get(r.agent_id);
            if (card && r.status === "error") {
              card.status = "error";
            }
          }
          flushNow();
          break;
        }

        default:
          // Unknown event type — ignore
          break;
      }
    },
    [flushNow, scheduleFlush],
  );

  const sendToRouter = useCallback(
    async (
      content: string,
      conversationId?: string,
      options?: { overrideToken?: string | null },
    ) => {
      if (!spaceId) return;

      abort();

      const fresh: RouterStreamState = {
        phase: "routing",
        messageId: null,
        routerMessage: null,
        routerSummary: null,
        errorText: undefined,
        errorInfo: undefined,
        agents: new Map(),
        timings: {
          ...INITIAL_TIMINGS,
          requestStartedAt: Date.now(),
        },
      };
      accRef.current = fresh;
      flushNow();

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const token = await storage.getUserTokenAsync();
        const apiUrl = config.apiUrl || "";

        const body: Record<string, string> = { content };
        if (conversationId) body.conversation_id = conversationId;
        if (options?.overrideToken) {
          body.override_token = options.overrideToken;
        }

        const response = await fetch(
          `${apiUrl}/api/v1/spaces/${spaceId}/router/message`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Accept: "text/event-stream",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify(body),
            signal: controller.signal,
          },
        );

        if (!response.ok) {
          const errorBody = await response.text().catch(() => "");
          const errorSnippet = errorBody.trim().slice(0, 300);
          let errorInfo: RouterStreamErrorInfo | undefined;
          try {
            const parsed = JSON.parse(errorBody);
            errorInfo = {
              statusCode: response.status,
              filtered: Boolean(parsed?.filtered),
              filterType:
                typeof parsed?.filter_type === "string"
                  ? parsed.filter_type
                  : null,
              confidence:
                typeof parsed?.confidence === "number"
                  ? parsed.confidence
                  : null,
              reason: typeof parsed?.reason === "string" ? parsed.reason : null,
              message:
                typeof parsed?.message === "string" ? parsed.message : null,
              overrideToken:
                typeof parsed?.override_token === "string"
                  ? parsed.override_token
                  : null,
              raw: errorBody || null,
            };
          } catch {
            errorInfo = {
              statusCode: response.status,
              raw: errorBody || null,
            };
          }
          accRef.current = {
            ...accRef.current,
            phase: "error",
            errorInfo,
            errorText: errorSnippet
              ? `HTTP ${response.status} ${response.statusText}: ${errorSnippet}`
              : `HTTP ${response.status} ${response.statusText}`,
          };
          flushNow();
          return;
        }

        accRef.current.timings.responseStartedAt = Date.now();
        flushNow();

        const reader = response.body?.getReader();
        if (!reader) {
          accRef.current = {
            ...accRef.current,
            phase: "error",
            errorInfo: {
              message: "Response body did not expose a readable stream.",
            },
            errorText: "Response body did not expose a readable stream.",
          };
          flushNow();
          return;
        }

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          let currentEvent = "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              const raw = line.slice(6);
              try {
                const data = JSON.parse(raw);
                processEvent(currentEvent, data);
              } catch {
                accRef.current.timings.parseErrorCount += 1;
                // skip malformed JSON
              }
            }
          }
        }

        if (
          accRef.current.phase !== "done" &&
          accRef.current.phase !== "error"
        ) {
          accRef.current = { ...accRef.current, phase: "done" };
          flushNow();
        }
      } catch (err: unknown) {
        if ((err as Error).name === "AbortError") return;
        accRef.current = {
          ...accRef.current,
          phase: "error",
          errorInfo: {
            message:
              err instanceof Error ? err.message : "Router request failed.",
          },
          errorText:
            err instanceof Error ? err.message : "Router request failed.",
        };
        flushNow();
      }
    },
    [spaceId, abort, flushNow, processEvent],
  );

  return { state, sendToRouter, abort };
}
