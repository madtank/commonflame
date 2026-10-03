import { describe, expect, it } from "vitest";

import type { ChatEntry } from "./transcript-model";
import { getPendingMonitorState } from "./pending-monitor";
import type { PendingResponseState } from "./pending-response";

const baseEntry: ChatEntry = {
  id: "agent-msg-1",
  role: "agent",
  meta: "Dispatcher",
  content: "Forwarding this to @builder",
  fromLabel: "Dispatcher",
  toLabel: "@builder",
};

describe("getPendingMonitorState", () => {
  it("shows live pending activity on routed agent entries", () => {
    const pendingResponse: PendingResponseState = {
      sourceEntryId: "agent-msg-1",
      targetHandle: "builder",
      targetLabel: "@builder",
      activeAgentLabel: "Builder",
      statusLabel: "processing",
      toolName: null,
      activity: null,
      toolCount: 2,
      stepLabel: "Reading files",
      commandLabels: ["npm test", "vite build", "pnpm lint"],
      progress: null,
      reason: null,
      errorMessage: null,
      retryAfterSeconds: null,
      isKnownTarget: true,
      hasPresenceSignal: true,
      presenceStatus: "active",
    };

    const state = getPendingMonitorState({
      entry: baseEntry,
      pendingStreamingReplyByParentId: new Map(),
      inlinePendingResponseEntryId: "agent-msg-1",
      pendingResponse,
      pendingResponseDisplay: {
        title: "Builder is working",
        detail: null,
        signals: ["Reading files", "2 tools", "npm test"],
      },
      getSafeRouteLabel: (value) => value || null,
      getPersistedPendingReplyDisplay: () => ({
        title: "unused",
        detail: null,
        signals: [],
      }),
    });

    expect(state.showPendingInline).toBe(true);
    expect(state.pendingRouteLabel).toBe("@builder");
    expect(state.pendingChipStatus).toBe("Builder working");
    expect(state.visibleInlinePendingSignals).toEqual([
      "Reading files",
      "2 tools",
    ]);
    expect(state.hiddenInlinePendingSignalCount).toBe(1);
  });

  it("uses persisted progress replies as agent-specific activity", () => {
    const persistedReply: ChatEntry = {
      id: "progress-reply-1",
      role: "agent",
      meta: "frontend_sentinel",
      fromLabel: "frontend_sentinel",
      content: "Working... (3 tools)",
      parentId: "agent-msg-1",
    };

    const state = getPendingMonitorState({
      entry: {
        ...baseEntry,
        toLabel: null,
      },
      pendingStreamingReplyByParentId: new Map([
        ["agent-msg-1", persistedReply],
      ]),
      inlinePendingResponseEntryId: null,
      pendingResponse: null,
      pendingResponseDisplay: null,
      getSafeRouteLabel: (value) => value || null,
      getPersistedPendingReplyDisplay: () => ({
        title: "frontend_sentinel is working",
        detail: null,
        signals: ["3 tools active", "git status"],
      }),
    });

    expect(state.persistedPendingReply).toBe(persistedReply);
    expect(state.showPendingInline).toBe(true);
    expect(state.pendingRouteLabel).toBe("frontend_sentinel");
    expect(state.pendingChipStatus).toBe("frontend_sentinel working");
    expect(state.visibleInlinePendingSignals).toEqual([
      "3 tools active",
      "git status",
    ]);
  });

  it("shows durable processing metadata on routed agent-to-agent entries", () => {
    const state = getPendingMonitorState({
      entry: {
        ...baseEntry,
        statusLabel: "working",
        toolName: "messages",
        hasProcessingState: true,
      },
      pendingStreamingReplyByParentId: new Map(),
      inlinePendingResponseEntryId: null,
      pendingResponse: null,
      pendingResponseDisplay: null,
      getSafeRouteLabel: (value) => value || null,
      getPersistedPendingReplyDisplay: () => ({
        title: "unused",
        detail: null,
        signals: [],
      }),
    });

    expect(state.showPendingInline).toBe(true);
    expect(state.pendingRouteLabel).toBe("@builder");
    expect(state.pendingChipStatus).toBe("@builder working");
  });

  it("prefers live pending activity over durable processing metadata", () => {
    const pendingResponse: PendingResponseState = {
      sourceEntryId: "agent-msg-1",
      targetHandle: "builder",
      targetLabel: "@builder",
      activeAgentLabel: "Builder",
      statusLabel: "tool_use",
      toolName: "tasks.list",
      activity: null,
      toolCount: 1,
      stepLabel: "Checking tasks",
      commandLabels: [],
      progress: null,
      reason: null,
      errorMessage: null,
      retryAfterSeconds: null,
      isKnownTarget: true,
      hasPresenceSignal: true,
      presenceStatus: "active",
    };

    const state = getPendingMonitorState({
      entry: {
        ...baseEntry,
        statusLabel: "queued",
        toolName: null,
        hasProcessingState: true,
      },
      pendingStreamingReplyByParentId: new Map(),
      inlinePendingResponseEntryId: "agent-msg-1",
      pendingResponse,
      pendingResponseDisplay: {
        title: "Builder is using tasks list...",
        detail: null,
        signals: ["1 tool active", "Checking tasks"],
      },
      getSafeRouteLabel: (value) => value || null,
      getPersistedPendingReplyDisplay: () => ({
        title: "unused",
        detail: null,
        signals: [],
      }),
    });

    expect(state.pendingChipStatus).toBe("using tasks list");
    expect(state.visibleInlinePendingSignals).toEqual([
      "1 tool active",
      "Checking tasks",
    ]);
  });
});
