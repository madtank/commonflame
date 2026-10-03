import { describe, expect, it } from "vitest";
import type { SpaceAgentConversationCard } from "@/lib/space-agent-api";
import {
  type ChatEntry,
  buildConversationCardAnchors,
  buildAttachedNoReplyIndicators,
  buildConversationGroups,
  buildDisplayEntries,
  buildEntryLookup,
  buildNoReplySignalEntry,
  buildPendingStreamingReplyByParentId,
  buildHiddenEntryIds,
  buildThreadIdByEntryId,
  getCompactPauseTone,
  isNoReplyPauseEntry,
  isPendingStreamingReplyEntry,
  isSignalOnlyTranscriptEntry,
  mapLiveMessageToEntry,
  mapSendReceiptToEntry,
  shouldHidePendingStreamingReplyEntry,
  shouldRenderActivityEntryAsSurfaceOnly,
  shouldUseSummaryCard,
  shouldRenderEntryAsCard,
} from "./transcript-model";

function createAgentEntry(
  id: string,
  overrides: Partial<ChatEntry> = {},
): ChatEntry {
  return {
    id,
    role: "agent",
    meta: "aX",
    fromLabel: "aX",
    content: `content-${id}`,
    createdAt: "2026-03-13T10:00:00Z",
    ...overrides,
  };
}

function createThreadState(
  entries: ChatEntry[],
  card?: SpaceAgentConversationCard | null,
) {
  const entryLookup = buildEntryLookup(entries);
  const threadIdByEntryId = buildThreadIdByEntryId(entries, entryLookup);
  const conversationGroups = buildConversationGroups(
    entries,
    threadIdByEntryId,
  );
  const conversationCardsByThread = new Map<
    string,
    SpaceAgentConversationCard
  >();

  if (card) {
    const threadId = card.root_message_id || card.conversation_id || card.id;
    if (threadId) conversationCardsByThread.set(threadId, card);
  }

  const conversationCardAnchors = buildConversationCardAnchors(
    conversationGroups,
    conversationCardsByThread,
  );

  return {
    entryLookup,
    threadIdByEntryId,
    conversationGroups,
    conversationCardsByThread,
    conversationCardAnchors,
  };
}

describe("transcript model", () => {
  it("keeps the root agent message visible when collapsing a summarized thread", () => {
    const entries = [
      createAgentEntry("root", {
        aiSummary: "Root summary",
      }),
      createAgentEntry("middle", {
        parentId: "root",
        conversationId: "root",
        aiSummary: "Middle summary",
      }),
      createAgentEntry("latest", {
        parentId: "middle",
        conversationId: "root",
        aiSummary: "Latest summary",
      }),
    ];

    const state = createThreadState(entries, {
      id: "card-1",
      root_message_id: "root",
      summary: "Thread summary",
    });

    const hiddenEntryIds = buildHiddenEntryIds({
      cardsEnabled: true,
      conversationGroups: state.conversationGroups,
      conversationCardsByThread: state.conversationCardsByThread,
      conversationCardAnchors: state.conversationCardAnchors,
    });

    expect(hiddenEntryIds.has("root")).toBe(false);
    expect(hiddenEntryIds.has("middle")).toBe(true);
    expect(hiddenEntryIds.has("latest")).toBe(false);
  });

  it("suppresses card rendering for the actively streaming thread", () => {
    const entries = [
      createAgentEntry("root", {
        aiSummary: "Short summary",
      }),
    ];
    const state = createThreadState(entries);

    expect(
      shouldRenderEntryAsCard({
        entry: entries[0],
        cardsEnabled: true,
        streamingThreadId: "root",
        threadIdByEntryId: state.threadIdByEntryId,
        conversationCardsByThread: state.conversationCardsByThread,
        conversationCardAnchors: state.conversationCardAnchors,
        conversationGroups: state.conversationGroups,
      }),
    ).toBe(false);
  });

  it("renders only the anchor card for a summarized grouped thread", () => {
    const entries = [
      createAgentEntry("root", {
        aiSummary: "Root summary",
      }),
      createAgentEntry("middle", {
        parentId: "root",
        conversationId: "root",
        aiSummary: "Middle summary",
      }),
      createAgentEntry("latest", {
        parentId: "middle",
        conversationId: "root",
        aiSummary: "Latest summary",
      }),
    ];

    const state = createThreadState(entries, {
      id: "card-1",
      root_message_id: "root",
      summary: "Thread summary",
    });

    expect(
      shouldRenderEntryAsCard({
        entry: entries[0],
        cardsEnabled: true,
        streamingThreadId: null,
        threadIdByEntryId: state.threadIdByEntryId,
        conversationCardsByThread: state.conversationCardsByThread,
        conversationCardAnchors: state.conversationCardAnchors,
        conversationGroups: state.conversationGroups,
      }),
    ).toBe(false);

    expect(
      shouldRenderEntryAsCard({
        entry: entries[2],
        cardsEnabled: true,
        streamingThreadId: null,
        threadIdByEntryId: state.threadIdByEntryId,
        conversationCardsByThread: state.conversationCardsByThread,
        conversationCardAnchors: state.conversationCardAnchors,
        conversationGroups: state.conversationGroups,
      }),
    ).toBe(true);
  });

  it("does not append a synthetic streaming entry after the landed reply is present", () => {
    const liveEntries = [
      createAgentEntry("landed", {
        content: "Finished response",
        createdAt: "2026-03-13T10:00:05Z",
      }),
    ];

    const displayEntries = buildDisplayEntries({
      liveEntries,
      pendingEntries: [],
      hasLiveConversation: true,
      agentName: "aX",
      streamingEntry: {
        id: "streaming-space-agent",
        agentName: "aX",
        content: "Finished response",
        statusLabel: "streaming",
      },
      streamTiming: {
        sendStartedAt: Date.parse("2026-03-13T10:00:00Z"),
        firstAgentProcessingAt: Date.parse("2026-03-13T10:00:01Z"),
        firstDeltaAt: Date.parse("2026-03-13T10:00:02Z"),
        finalMessageAt: Date.parse("2026-03-13T10:00:04Z"),
        deltaCount: 3,
        eventCount: 4,
        activeMessageId: "landed",
        lastErrorText: null,
      },
    });

    expect(displayEntries).toHaveLength(1);
    expect(displayEntries[0]?.id).toBe("landed");
  });

  it("does not append a synthetic streaming entry before the final reply lands", () => {
    const liveEntries = [
      createAgentEntry("user-turn", {
        role: "user",
        meta: "You",
        fromLabel: "You",
        content: "Can you check this?",
        createdAt: "2026-03-13T10:00:00Z",
      }),
    ];

    const displayEntries = buildDisplayEntries({
      liveEntries,
      pendingEntries: [],
      hasLiveConversation: true,
      agentName: "aX",
      streamingEntry: {
        id: "streaming-space-agent",
        agentName: "aX",
        content: "Working on it",
        statusLabel: "streaming",
      },
      streamTiming: {
        sendStartedAt: Date.parse("2026-03-13T10:00:00Z"),
        firstAgentProcessingAt: Date.parse("2026-03-13T10:00:01Z"),
        firstDeltaAt: Date.parse("2026-03-13T10:00:02Z"),
        finalMessageAt: null,
        deltaCount: 1,
        eventCount: 2,
        activeMessageId: "streaming-space-agent",
        lastErrorText: null,
      },
    });

    expect(displayEntries).toHaveLength(1);
    expect(displayEntries[0]?.id).toBe("user-turn");
  });

  it("maps agent_pause messages into visible non-card transcript notices", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "pause-1",
        sender_type: "agent",
        display_name: "aX",
        content: "🤐 aX chose not to reply.",
        message_type: "agent_pause",
        metadata: {
          reason: "no_reply",
          reason_text: "aX chose not to reply.",
          emoji: "🤐",
        },
        created_at: "2026-03-13T10:00:00Z",
      },
      "aX",
    );

    expect(entry.messageType).toBe("agent_pause");
    expect(entry.pauseReason).toBe("no_reply");
    expect(entry.pauseReasonText).toBe("aX chose not to reply.");
    expect(entry.pauseEmoji).toBe("🤐");
    expect(getCompactPauseTone(entry)).toBe("quiet");
    expect(shouldUseSummaryCard(entry)).toBe(false);
  });

  it("classifies app signal messages as card-only transcript entries", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "signal-1",
        sender_type: "agent",
        display_name: "chatgpt_dev",
        content: "Identity: Open agent identity widget",
        message_type: "system",
        metadata: {
          signal_only: true,
          app_signal: {
            app: "whoami",
            resource_uri: "ui://whoami/identity",
            tool_call_id: "tool-call-1",
            source: "axctl_apps_signal",
            signal_only: true,
          },
          ui: {
            cards: [
              {
                card_id: "app-signal:tool-call-1",
                type: "result",
                version: 1,
                payload: {
                  title: "Identity",
                  summary: "Open agent identity widget",
                  tool_name: "whoami",
                  resource_uri: "ui://whoami/identity",
                  source: "axctl_apps_signal",
                },
              },
            ],
            widget: {
              kind: "mcp_app",
              tool_name: "whoami",
              tool_call_id: "tool-call-1",
              resource_uri: "ui://whoami/identity",
              lifecycle: "complete",
              title: "Identity",
            },
          },
        },
        created_at: "2026-03-13T10:00:00Z",
      },
      "aX",
    );

    expect(entry.content).toBe("Identity: Open agent identity widget");
    expect(entry.surfaceCount).toBeGreaterThan(0);
    expect(isSignalOnlyTranscriptEntry(entry)).toBe(true);
  });

  it("classifies alert activity with alert cards as surface-only transcript entries", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "gateway-alert-1",
        sender_type: "human",
        display_name: "madtank",
        content:
          "@cli_god Gateway alert: @cli_god looks stale\nNo heartbeat for 8h 55m.",
        message_type: "alert",
        metadata: {
          ui: {
            cards: [
              {
                card_id: "gateway-alert:cli_god:stale",
                type: "alert",
                version: 1,
                payload: {
                  title: "@cli_god looks stale",
                  summary: "No heartbeat for 8h 55m.",
                },
              },
            ],
          },
        },
        created_at: "2026-03-13T10:00:00Z",
      },
      "madtank",
    );

    expect(entry.messageType).toBe("alert");
    expect(entry.surfaceCount).toBeGreaterThan(0);
    expect(shouldRenderActivityEntryAsSurfaceOnly(entry)).toBe(true);
  });

  it("keeps gateway reminder alert cards out of conversation summary card chrome", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "d326aefc-922c-4851-921f-eafaf26d310b",
        sender_type: "agent",
        display_name: "gateway_pulse_local",
        content: "Gateway product validation reminder smoke",
        message_type: "reminder",
        ui: {
          cards: [
            {
              card_id: "task-reminder:af11e8eb",
              type: "alert",
              version: 1,
              payload: {
                kind: "task_reminder",
                title: "Gateway product validation reminder smoke",
                resource_uri: "ui://tasks/af11e8eb-ecd3-4aa1-82a3-e8ffe181d394",
              },
            },
          ],
        },
        ai_summary: "Gateway product validation reminder smoke",
        created_at: "2026-05-02T16:22:41Z",
      },
      "aX",
    );
    const state = createThreadState([entry]);

    expect(entry.messageType).toBe("reminder");
    expect(shouldRenderActivityEntryAsSurfaceOnly(entry)).toBe(true);
    expect(
      shouldRenderEntryAsCard({
        entry,
        cardsEnabled: true,
        streamingThreadId: null,
        threadIdByEntryId: state.threadIdByEntryId,
        conversationCardsByThread: state.conversationCardsByThread,
        conversationCardAnchors: state.conversationCardAnchors,
        conversationGroups: state.conversationGroups,
      }),
    ).toBe(false);
  });

  it("keeps alert activity with no alert card in the normal transcript lane", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "gateway-alert-plain",
        sender_type: "human",
        display_name: "madtank",
        content: "Gateway alert without a card surface",
        message_type: "alert",
        metadata: {},
        created_at: "2026-03-13T10:00:00Z",
      },
      "madtank",
    );

    expect(entry.surfaceCount).toBe(0);
    expect(shouldRenderActivityEntryAsSurfaceOnly(entry)).toBe(false);
  });

  it("derives routed target labels from routing_story metadata when top-level routing is absent", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "user-routed-1",
        sender_type: "human",
        display_name: "madtank",
        content: "Please check this thread.",
        metadata: {
          routing: {
            mode: "reply_target",
          },
          routing_story: {
            targets: [
              {
                agent_name: "aX",
                display_name: "@aX",
              },
            ],
          },
        },
      },
      "aX",
    );

    expect(entry.toLabel).toBe("aX");
    expect(entry.statusLabel).toBe("reply_target");
  });

  it("prefers stable routing target fields over humanized display labels", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "user-routed-2",
        sender_type: "human",
        display_name: "madtank",
        content: "Please ask the specialist.",
        metadata: {
          routing_story: {
            targets: [
              {
                display_name: "Code Weaver",
                agent_name: "code_weaver",
              },
            ],
          },
        },
      },
      "aX",
    );

    expect(entry.toLabel).toBe("code_weaver");
  });

  it("falls back to a leading explicit mention for user-directed messages when routing metadata is missing", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "user-routed-3",
        sender_type: "human",
        display_name: "madtank",
        content: "@ghost_agent can you take this?",
      },
      "aX",
    );

    expect(entry.toLabel).toBe("ghost_agent");
  });

  it("detects no-reply pause notices for compact rendering", () => {
    const entry = createAgentEntry("pause-hidden", {
      parentId: "user-seed",
      conversationId: "user-seed",
      messageType: "agent_pause",
      pauseReason: "no_reply",
      pauseReasonText: "aX chose not to reply.",
      content: "🤐 aX chose not to reply.",
    });

    expect(isNoReplyPauseEntry(entry)).toBe(true);
    expect(getCompactPauseTone(entry)).toBe("quiet");
  });

  it("treats no_reply_requested pause notices as attached no-reply indicators", () => {
    const entry = createAgentEntry("pause-requested", {
      parentId: "user-seed",
      conversationId: "user-seed",
      messageType: "agent_pause",
      pauseReason: "no_reply_requested",
      pauseReasonText: "Agent testing no-reply directive.",
      content: "🤐 aX chose not to reply.",
    });

    expect(isNoReplyPauseEntry(entry)).toBe(true);
    expect(getCompactPauseTone(entry)).toBe("quiet");
  });

  it("treats ack signal metadata as a compact pause indicator", () => {
    const entry = createAgentEntry("pause-ack", {
      parentId: "user-seed",
      conversationId: "user-seed",
      messageType: "agent_pause",
      content: "Request processed.",
      metadata: {
        signal_kind: "ack",
        signal_only: true,
      },
    });

    expect(isNoReplyPauseEntry(entry)).toBe(true);
    expect(getCompactPauseTone(entry)).toBe("ack");
  });

  it("does not classify non-agent-pause entries as compact pause signals", () => {
    const entry = createAgentEntry("normal-message", {
      messageType: null,
      content: "No reply needed here.",
      pauseReasonText: "No reply needed here.",
    });

    expect(isNoReplyPauseEntry(entry)).toBe(false);
    expect(getCompactPauseTone(entry)).toBe(null);
  });

  it("attaches no-reply notices to their parent message", () => {
    const parent = createAgentEntry("user-seed", {
      role: "user",
      meta: "You",
      fromLabel: "You",
      content: "Can you help?",
    });
    const child = createAgentEntry("pause-hidden", {
      parentId: "user-seed",
      conversationId: "user-seed",
      messageType: "agent_pause",
      fromLabel: "aX",
      pauseReason: "no_reply",
      pauseReasonText: "aX chose not to reply.",
      content: "🤐 aX chose not to reply.",
      createdAt: "2026-03-13T10:01:00Z",
    });

    const entries = [parent, child];
    const entryLookup = buildEntryLookup(entries);
    const indicators = buildAttachedNoReplyIndicators(entries, entryLookup);

    expect(indicators.get("user-seed")).toEqual([
      {
        id: "pause-hidden",
        parentId: "user-seed",
        createdAt: "2026-03-13T10:01:00Z",
        agentLabel: "aX",
        pauseReasonText: "aX chose not to reply.",
        pauseEmoji: null,
        tone: "quiet",
      },
    ]);
  });

  it("rebuilds attached no-reply notices from parent metadata after refresh", () => {
    const parent = createAgentEntry("user-seed", {
      role: "user",
      meta: "You",
      fromLabel: "You",
      content: "Can you help?",
      metadata: {
        ui: {
          signals: {
            agent_skipped: [
              {
                id: "signal-no-reply:user-seed:agent-1",
                agent_id: "agent-1",
                agent_name: "aX",
                reason: "no reply",
                label: "no reply",
                reason_code: "no_reply",
                signal_kind: "no_reply_requested",
                emoji: "",
                created_at: "2026-03-13T10:01:00Z",
                signal_only: true,
              },
            ],
          },
        },
      },
    });

    const entries = [parent];
    const entryLookup = buildEntryLookup(entries);
    const indicators = buildAttachedNoReplyIndicators(entries, entryLookup);

    expect(indicators.get("user-seed")).toEqual([
      {
        id: "signal-no-reply:user-seed:agent-1",
        parentId: "user-seed",
        createdAt: "2026-03-13T10:01:00Z",
        agentLabel: "aX",
        pauseReasonText: "no reply",
        pauseEmoji: null,
        tone: "quiet",
      },
    ]);
  });

  it("treats stored ack skip signals as ack tone when reason_code carries the signal", () => {
    const parent = createAgentEntry("user-seed", {
      role: "user",
      meta: "You",
      fromLabel: "You",
      content: "Can you help?",
      metadata: {
        ui: {
          signals: {
            agent_skipped: [
              {
                id: "signal-ack:user-seed:agent-1",
                agent_id: "agent-1",
                agent_name: "aX",
                reason: "Request processed.",
                reason_code: "ack",
                created_at: "2026-03-13T10:01:00Z",
                signal_only: true,
              },
            ],
          },
        },
      },
    });

    const indicators = buildAttachedNoReplyIndicators(
      [parent],
      buildEntryLookup([parent]),
    );

    expect(indicators.get("user-seed")).toEqual([
      {
        id: "signal-ack:user-seed:agent-1",
        parentId: "user-seed",
        createdAt: "2026-03-13T10:01:00Z",
        agentLabel: "aX",
        pauseReasonText: "Request processed.",
        pauseEmoji: null,
        tone: "ack",
      },
    ]);
  });

  it("maps message_metadata signals into entry metadata for refresh durability", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "user-seed",
        sender_type: "user",
        display_name: "madtank",
        content: "Can you help?",
        message_metadata: {
          ui: {
            signals: {
              agent_skipped: [
                {
                  id: "signal-no-reply:user-seed:agent-1",
                  agent_id: "agent-1",
                  agent_name: "aX",
                  reason: "no reply",
                  label: "no reply",
                  reason_code: "no_reply",
                  signal_kind: "no_reply_requested",
                  emoji: "",
                  created_at: "2026-03-13T10:01:00Z",
                  signal_only: true,
                },
              ],
            },
          },
        },
      },
      "aX",
    );

    expect(
      (
        entry.metadata as {
          ui?: { signals?: { agent_skipped?: Array<{ id?: string }> } };
        }
      )?.ui?.signals?.agent_skipped?.[0]?.id,
    ).toBe("signal-no-reply:user-seed:agent-1");
  });

  it("does not count agent_pause notices as grouped agent replies", () => {
    const entries = [
      createAgentEntry("user-seed", {
        role: "user",
        meta: "You",
        fromLabel: "You",
        content: "Can you help?",
      }),
      createAgentEntry("pause-2", {
        parentId: "user-seed",
        conversationId: "user-seed",
        messageType: "agent_pause",
        content: "🤐 aX chose not to reply.",
      }),
    ];

    const entryLookup = buildEntryLookup(entries);
    const threadIdByEntryId = buildThreadIdByEntryId(entries, entryLookup);
    const conversationGroups = buildConversationGroups(
      entries,
      threadIdByEntryId,
    );
    const group = conversationGroups.get("user-seed");

    expect(group?.replyCount).toBe(0);
  });

  it("builds a local no-reply entry from an SSE skip signal", () => {
    const entry = buildNoReplySignalEntry(
      {
        agent_id: "agent-1",
        agent_name: "aX",
        message_id: "user-msg-1",
        reason: "aX chose not to reply.",
        reason_code: "no_reply",
        emoji: "🤐",
        created_at: "2026-03-13T10:02:00Z",
      },
      "aX",
    );

    expect(entry).not.toBeNull();
    expect(entry?.id).toBe("signal-no-reply:user-msg-1:agent-1");
    expect(entry?.messageType).toBe("agent_pause");
    expect(entry?.parentId).toBe("user-msg-1");
    expect(entry?.pauseReason).toBe("no_reply");
  });

  it("builds a local no-reply entry from a no_reply_requested SSE skip signal", () => {
    const entry = buildNoReplySignalEntry(
      {
        agent_id: "agent-1",
        agent_name: "aX",
        message_id: "user-msg-1",
        reason: "Agent testing no-reply directive.",
        reason_code: "no_reply_requested",
        emoji: "🤐",
        created_at: "2026-03-13T10:02:00Z",
      },
      "aX",
    );

    expect(entry).not.toBeNull();
    expect(entry?.id).toBe("signal-no-reply:user-msg-1:agent-1");
    expect(entry?.messageType).toBe("agent_pause");
    expect(entry?.parentId).toBe("user-msg-1");
    expect(entry?.pauseReason).toBe("no_reply_requested");
  });

  it("ignores non-no-reply skip signals for transcript attachment", () => {
    const entry = buildNoReplySignalEntry(
      {
        agent_id: "agent-1",
        agent_name: "aX",
        message_id: "user-msg-1",
        reason: "Rate limited",
        reason_code: "rate_limited",
      },
      "aX",
    );

    expect(entry).toBeNull();
  });

  it("keeps unrelated landed agent messages visible while the in-flight response stays deferred", () => {
    const liveEntries = [
      createAgentEntry("other", {
        content: "Unrelated response",
        parentId: "different-parent",
        createdAt: "2026-03-13T10:00:05Z",
      }),
    ];

    const displayEntries = buildDisplayEntries({
      liveEntries,
      pendingEntries: [],
      hasLiveConversation: true,
      agentName: "aX",
      streamingEntry: {
        id: "streaming-space-agent",
        agentName: "aX",
        parentId: "user-msg-1",
        content: "Still streaming",
        statusLabel: "tool_use",
        toolName: "tasks",
      },
      streamTiming: {
        sendStartedAt: Date.parse("2026-03-13T10:00:00Z"),
        firstAgentProcessingAt: Date.parse("2026-03-13T10:00:01Z"),
        firstDeltaAt: Date.parse("2026-03-13T10:00:02Z"),
        finalMessageAt: null,
        deltaCount: 1,
        eventCount: 2,
        activeMessageId: "streaming-space-agent",
        lastErrorText: null,
      },
    });

    expect(displayEntries).toHaveLength(1);
    expect(displayEntries[0]?.id).toBe("other");
  });

  it("detects non-final persisted streaming reply progress entries", () => {
    const entry = createAgentEntry("progress-reply", {
      parentId: "user-msg-1",
      content: "Generating preview image... (1 tool)",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: false,
          runtime: "hermes_sdk",
        },
        processing: {
          status: "tool_call",
          tool_name: "image.generate",
          activity: "Generating preview image",
        },
      },
    });

    expect(isPendingStreamingReplyEntry(entry)).toBe(true);
  });

  it("hides persisted progress replies when the parent can show the activity monitor", () => {
    const parent = createAgentEntry("agent-msg-1", {
      content: "Forwarding this to @frontend_sentinel",
    });
    const progressReply = createAgentEntry("progress-reply", {
      parentId: parent.id,
      content: "Working... (2 tools)",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: false,
          runtime: "hermes_sdk",
        },
      },
    });

    expect(
      shouldHidePendingStreamingReplyEntry({
        entry: progressReply,
        entryLookup: new Map([[parent.id, parent]]),
        hiddenEntryIds: new Set(),
      }),
    ).toBe(true);
  });

  it("keeps persisted progress replies visible when their parent is hidden in a card group", () => {
    const parent = createAgentEntry("agent-msg-1", {
      content: "Forwarding this to @frontend_sentinel",
    });
    const progressReply = createAgentEntry("progress-reply", {
      parentId: parent.id,
      content: "Working... (2 tools)",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: false,
          runtime: "hermes_sdk",
        },
      },
    });

    expect(
      shouldHidePendingStreamingReplyEntry({
        entry: progressReply,
        entryLookup: new Map([[parent.id, parent]]),
        hiddenEntryIds: new Set([parent.id]),
      }),
    ).toBe(false);
  });

  it("does not attach stale pending replies after a newer final sibling lands", () => {
    const parent = createAgentEntry("agent-msg-1", {
      content: "Forwarding this to @frontend_sentinel",
      createdAt: "2026-03-13T10:00:00Z",
    });
    const progressReply = createAgentEntry("progress-reply", {
      parentId: parent.id,
      content: "Generating preview image... (1 tool)",
      createdAt: "2026-03-13T10:00:05Z",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: false,
          runtime: "hermes_sdk",
        },
        processing: {
          status: "tool_call",
          tool_name: "image.generate",
          activity: "Generating preview image",
        },
      },
    });
    const finalReply = createAgentEntry("final-reply", {
      parentId: parent.id,
      content: "Finished image preview.",
      createdAt: "2026-03-13T10:00:30Z",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: true,
          runtime: "hermes_sdk",
        },
      },
    });

    const pendingByParent = buildPendingStreamingReplyByParentId([
      parent,
      progressReply,
      finalReply,
    ]);

    expect(pendingByParent.has(parent.id)).toBe(false);
  });

  it("still attaches a newer pending reply when an older final sibling exists", () => {
    const parent = createAgentEntry("agent-msg-1", {
      content: "Forwarding this to @frontend_sentinel",
      createdAt: "2026-03-13T10:00:00Z",
    });
    const earlierFinalReply = createAgentEntry("final-reply", {
      parentId: parent.id,
      content: "Earlier reply finished.",
      createdAt: "2026-03-13T10:00:05Z",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: true,
          runtime: "hermes_sdk",
        },
      },
    });
    const laterProgressReply = createAgentEntry("progress-reply", {
      parentId: parent.id,
      content: "Searching workspace context... (1 tool)",
      createdAt: "2026-03-13T10:00:30Z",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: false,
          runtime: "hermes_sdk",
        },
        processing: {
          status: "tool_call",
          tool_name: "context.search",
          activity: "Searching workspace context",
        },
      },
    });

    const pendingByParent = buildPendingStreamingReplyByParentId([
      parent,
      earlierFinalReply,
      laterProgressReply,
    ]);

    expect(pendingByParent.get(parent.id)).toBe(laterProgressReply);
  });

  it("uses immutable creation time when comparing edited final siblings to newer pending replies", () => {
    const parent = createAgentEntry("agent-msg-1", {
      content: "Forwarding this to @frontend_sentinel",
      createdAt: "2026-03-13T10:00:00Z",
    });
    const editedFinalReply = mapLiveMessageToEntry(
      {
        id: "final-reply",
        sender_type: "agent",
        display_name: "chatgpt_dev",
        parent_id: parent.id,
        content: "Earlier reply finished.",
        created_at: "2026-03-13T10:00:05Z",
        updated_at: "2026-03-13T10:05:00Z",
        metadata: {
          streaming_reply: {
            enabled: true,
            final: true,
            runtime: "hermes_sdk",
          },
        },
      },
      "aX",
    );
    const laterProgressReply = createAgentEntry("progress-reply", {
      parentId: parent.id,
      content: "Searching workspace context... (1 tool)",
      createdAt: "2026-03-13T10:03:00Z",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: false,
          runtime: "hermes_sdk",
        },
        processing: {
          status: "tool_call",
          tool_name: "context.search",
          activity: "Searching workspace context",
        },
      },
    });

    expect(editedFinalReply.createdAt).toBe("2026-03-13T10:05:00Z");

    const pendingByParent = buildPendingStreamingReplyByParentId([
      parent,
      editedFinalReply,
      laterProgressReply,
    ]);

    expect(pendingByParent.get(parent.id)).toBe(laterProgressReply);
  });

  it("does not treat final streaming replies as pending progress", () => {
    const entry = createAgentEntry("final-reply", {
      parentId: "user-msg-1",
      content: "Finished response",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: true,
          runtime: "hermes_sdk",
        },
      },
    });

    expect(isPendingStreamingReplyEntry(entry)).toBe(false);
  });

  it("does not hide edited final content when backend leaves final false", () => {
    const entry = createAgentEntry("edited-final-reply", {
      parentId: "user-msg-1",
      content: "Ran the frontend streaming UI smoke check successfully.",
      metadata: {
        streaming_reply: {
          enabled: true,
          final: false,
          runtime: "hermes_sdk",
        },
      },
    });

    expect(isPendingStreamingReplyEntry(entry)).toBe(false);
  });

  it("uses updated_at for edited final streaming replies", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "edited-final-reply",
        sender_type: "agent",
        display_name: "chatgpt_dev",
        parent_id: "user-msg-1",
        content: "Finished after several tool calls.",
        created_at: "2026-03-13T10:00:00Z",
        updated_at: "2026-03-13T10:05:00Z",
        metadata: {
          streaming_reply: {
            enabled: true,
            final: false,
            runtime: "hermes_sdk",
          },
        },
      },
      "aX",
    );

    expect(entry.createdAt).toBe("2026-03-13T10:05:00Z");
  });

  it("keeps progress-shaped streaming replies anchored to created_at", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "progress-reply",
        sender_type: "agent",
        display_name: "chatgpt_dev",
        parent_id: "user-msg-1",
        content: "Working... (2 tools)",
        created_at: "2026-03-13T10:00:00Z",
        updated_at: "2026-03-13T10:05:00Z",
        metadata: {
          streaming_reply: {
            enabled: true,
            final: false,
            runtime: "hermes_sdk",
          },
        },
      },
      "aX",
    );

    expect(entry.createdAt).toBe("2026-03-13T10:00:00Z");
  });

  it("keeps metadata-marked pending replies anchored to created_at even with activity text", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "progress-reply",
        sender_type: "agent",
        display_name: "chatgpt_dev",
        parent_id: "user-msg-1",
        content: "Generating preview image... (1 tool)",
        created_at: "2026-03-13T10:00:00Z",
        updated_at: "2026-03-13T10:05:00Z",
        metadata: {
          streaming_reply: {
            enabled: true,
            final: false,
            runtime: "hermes_sdk",
          },
          processing: {
            status: "tool_call",
            tool_name: "image.generate",
            activity: "Generating preview image",
          },
        },
      },
      "aX",
    );

    expect(entry.createdAt).toBe("2026-03-13T10:00:00Z");
  });

  it("prefers durable processing metadata for agent-to-agent status chips", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "agent-processing-1",
        sender_type: "agent",
        display_name: "code_weaver",
        content: "On it.",
        metadata: {
          processing: { status: "working", tool_name: "tasks" },
        },
      } as any,
      "aX",
    );

    expect(entry.statusLabel).toBe("working");
    expect(entry.toolName).toBe("tasks");
  });

  it("infers routed targets from leading mentions on agent-to-agent messages", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "agent-processing-mention-1",
        sender_type: "agent",
        display_name: "code_weaver",
        content: "@frontend_sentinel can you check mobile?",
        metadata: {
          processing: { status: "working", tool_name: "messages" },
        },
      } as any,
      "aX",
    );

    expect(entry.toLabel).toBe("frontend_sentinel");
    expect(entry.statusLabel).toBe("working");
    expect(entry.toolName).toBe("messages");
    expect(entry.hasProcessingState).toBe(true);
  });

  it("maps send receipt processing metadata without depending on live message scope", () => {
    const entry = mapSendReceiptToEntry(
      {
        id: "receipt-1",
        sender_type: "user",
        display_name: "Jacob",
        content: "@frontend_sentinel can you check mobile?",
        metadata: {
          processing: { status: "working", tool_name: "messages" },
        },
      } as any,
      "madtank",
      "aX",
    );

    expect(entry.statusLabel).toBe("working");
    expect(entry.toolName).toBe("messages");
    expect(entry.toLabel).toBe("frontend_sentinel");
  });
});

describe("ax_intel stripping", () => {
  const baseMessage = {
    id: "msg-1",
    content: "Here is your answer",
    sender_type: "agent",
    display_name: "aX",
    created_at: "2026-03-15T05:00:00Z",
  };

  it("strips ax_intel JSON from end of message content", () => {
    const msg = {
      ...baseMessage,
      content:
        'Here is your answer\n\n{"ax_intel": {"routing_decision": "handle", "intent": "question", "urgency": "can_wait", "visible": true}}',
    };
    const entry = mapLiveMessageToEntry(msg as any, "aX");
    expect(entry.content).toBe("Here is your answer");
  });

  it("preserves content without ax_intel", () => {
    const entry = mapLiveMessageToEntry(baseMessage as any, "aX");
    expect(entry.content).toBe("Here is your answer");
  });

  it("strips ax_intel with extra whitespace", () => {
    const msg = {
      ...baseMessage,
      content:
        'Response text   \n\n  { "ax_intel" : {"routing_decision": "handle"} }  ',
    };
    const entry = mapLiveMessageToEntry(msg as any, "aX");
    expect(entry.content).toBe("Response text");
  });
});

describe("user attribution (fromHandle)", () => {
  it("derives fromHandle from display_name for user-sent live messages", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "msg-attr-1",
        sender_type: "user",
        display_name: "@madtank",
        content: "hello",
        created_at: "2026-05-07T00:00:00Z",
      } as any,
      "aX",
    );
    expect(entry.role).toBe("user");
    expect(entry.fromHandle).toBe("madtank");
  });

  it("derives fromHandle from display_name without leading @", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "msg-attr-2",
        sender_type: "human",
        display_name: "codex_uat",
        content: "hi",
        created_at: "2026-05-07T00:00:00Z",
      } as any,
      "aX",
    );
    expect(entry.role).toBe("user");
    expect(entry.fromHandle).toBe("codex_uat");
  });

  it("omits fromHandle for agent-sent messages", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "msg-attr-3",
        sender_type: "agent",
        display_name: "@protocol_sage_738",
        content: "ack",
        created_at: "2026-05-07T00:00:00Z",
      } as any,
      "aX",
    );
    expect(entry.role).toBe("agent");
    expect(entry.fromHandle).toBeUndefined();
  });

  it("omits fromHandle for user message lacking display_name", () => {
    const entry = mapLiveMessageToEntry(
      {
        id: "msg-attr-4",
        sender_type: "user",
        content: "anonymous",
        created_at: "2026-05-07T00:00:00Z",
      } as any,
      "aX",
    );
    expect(entry.role).toBe("user");
    expect(entry.fromHandle).toBeUndefined();
  });

  it("derives fromHandle from username arg for send-receipt user entries", () => {
    const entry = mapSendReceiptToEntry(
      {
        id: "rcpt-attr-1",
        message_id: "rcpt-attr-1",
        sender_type: "user",
        content: "ok",
        created_at: "2026-05-07T00:00:00Z",
      } as any,
      "madtank",
      "aX",
    );
    expect(entry.role).toBe("user");
    expect(entry.fromHandle).toBe("madtank");
  });

  it("prefers receipt display_name over username arg when present", () => {
    const entry = mapSendReceiptToEntry(
      {
        id: "rcpt-attr-2",
        message_id: "rcpt-attr-2",
        sender_type: "user",
        display_name: "@codex_uat",
        content: "ok",
        created_at: "2026-05-07T00:00:00Z",
      } as any,
      "madtank",
      "aX",
    );
    expect(entry.fromHandle).toBe("codex_uat");
  });
});
