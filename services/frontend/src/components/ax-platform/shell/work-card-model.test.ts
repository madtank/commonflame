import { describe, expect, it } from "vitest";
import type { SpaceAgentConversationCard } from "@/lib/space-agent-api";
import type { ChatEntry } from "./transcript-model";
import {
  buildEntryLookup,
  buildThreadIdByEntryId,
  buildConversationGroups,
} from "./transcript-model";
import { buildWorkCardModel } from "./work-card-model";

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

function createUserEntry(
  id: string,
  overrides: Partial<ChatEntry> = {},
): ChatEntry {
  return {
    id,
    role: "user",
    meta: "You",
    fromLabel: "You",
    content: `content-${id}`,
    createdAt: "2026-03-13T10:00:00Z",
    ...overrides,
  };
}

function createState(entries: ChatEntry[], card?: SpaceAgentConversationCard) {
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

  return {
    threadIdByEntryId,
    conversationGroups,
    conversationCardsByThread,
  };
}

function formatCardMetaList(values: string[], expanded: boolean, limit = 3) {
  if (!values.length) return null;
  if (expanded || values.length <= limit) return values.join(", ");
  return `${values.slice(0, limit).join(", ")} +${values.length - limit} more`;
}

describe("work card model", () => {
  it("builds a summarized thread card with merged participants, mentions, and surfaces", () => {
    const entries = [
      createAgentEntry("root", {
        aiSummary: "Root summary",
        surfaces: [
          {
            id: "s1",
            kind: "widget",
            placement: "inline",
            status: "ready",
            widget: {},
          },
        ],
      }),
      createAgentEntry("child", {
        parentId: "root",
        conversationId: "root",
        aiSummary: "Child summary",
        content: "Working with @protocol on it",
        surfaces: [
          {
            id: "s2",
            kind: "widget",
            placement: "inline",
            status: "ready",
            widget: {},
          },
        ],
      }),
    ];
    const card = {
      id: "card-1",
      root_message_id: "root",
      summary: "Thread summary",
      status: "working",
      message_count: 4,
      participants: ["madtank", "aX"],
      metadata: {
        mentions: ["stack"],
        signal_emojis: ["🧩"],
      },
    } satisfies SpaceAgentConversationCard;
    const state = createState(entries, card);

    const model = buildWorkCardModel({
      entry: entries[1],
      expanded: false,
      threadIdByEntryId: state.threadIdByEntryId,
      conversationGroups: state.conversationGroups,
      conversationCardsByThread: state.conversationCardsByThread,
      resolveIdentityLabel: (value) => value || "unknown",
      formatCardMetaList,
      buildSignalEmojis: () => ["📬"],
    });

    expect(model.isThreadSummaryCard).toBe(true);
    expect(model.cardId).toBe("group-root");
    expect(model.summary).toBe("Thread summary");
    expect(model.participantLabels).toEqual(["madtank", "aX"]);
    expect(model.mentionLabels).toEqual(["stack", "@protocol"]);
    expect(model.statusValue).toBe("working");
    expect(model.groupedSurfaceCount).toBe(2);
    expect(model.signalEmojis).toEqual(["🧩", "📬"]);
  });

  it("falls back to the latest card entry for a single-message card", () => {
    const entry = createAgentEntry("solo", {
      aiSummary: "Concise summary",
      statusLabel: "delivered",
      surfaces: [
        {
          id: "card",
          kind: "cards",
          placement: "card",
          status: "ready",
          cards: [],
        },
      ],
    });
    const state = createState([entry]);

    const model = buildWorkCardModel({
      entry,
      expanded: true,
      threadIdByEntryId: state.threadIdByEntryId,
      conversationGroups: state.conversationGroups,
      conversationCardsByThread: state.conversationCardsByThread,
      resolveIdentityLabel: (value) => value || "unknown",
      formatCardMetaList,
      buildSignalEmojis: () => [],
    });

    expect(model.isThreadSummaryCard).toBe(false);
    expect(model.cardId).toBe("solo");
    expect(model.summary).toBe("Concise summary");
    expect(model.participants).toBe("aX");
    expect(model.statusValue).toBe("delivered");
    expect(model.groupedSurfaceCount).toBe(0);
  });

  it("keeps message content as the collapsed summary when no AI summary exists", () => {
    const entry = createAgentEntry("seeded-message", {
      content:
        "Activity Stream message-card e2e seed should still show message context.",
      aiSummary: null,
      statusLabel: "delivered",
    });
    const state = createState([entry]);

    const model = buildWorkCardModel({
      entry,
      expanded: false,
      threadIdByEntryId: state.threadIdByEntryId,
      conversationGroups: state.conversationGroups,
      conversationCardsByThread: state.conversationCardsByThread,
      resolveIdentityLabel: (value) => value || "unknown",
      formatCardMetaList,
      buildSignalEmojis: () => [],
    });

    expect(model.summary).toBe(
      "Activity Stream message-card e2e seed should still show message context.",
    );
  });

  it("keeps summarized thread surfaces even when they land on the root user message", () => {
    const entries = [
      createUserEntry("root", {
        surfaces: [
          {
            id: "task-surface",
            kind: "widget",
            placement: "inline",
            status: "ready",
            widget: { tool_name: "tasks" },
          },
        ],
      }),
      createAgentEntry("child", {
        parentId: "root",
        conversationId: "root",
        aiSummary: "Done",
      }),
      createAgentEntry("latest", {
        parentId: "root",
        conversationId: "root",
        aiSummary: "Thread summary",
      }),
    ];
    const card = {
      id: "card-2",
      root_message_id: "root",
      summary: "Thread summary",
      status: "ready",
      message_count: 3,
      participants: ["You", "aX"],
    } satisfies SpaceAgentConversationCard;
    const state = createState(entries, card);

    const model = buildWorkCardModel({
      entry: entries[2],
      expanded: false,
      threadIdByEntryId: state.threadIdByEntryId,
      conversationGroups: state.conversationGroups,
      conversationCardsByThread: state.conversationCardsByThread,
      resolveIdentityLabel: (value) => value || "unknown",
      formatCardMetaList,
      buildSignalEmojis: () => [],
    });

    expect(model.isThreadSummaryCard).toBe(true);
    expect(model.groupedSurfaces.map((surface) => surface.id)).toEqual([
      "task-surface",
    ]);
    expect(model.groupedSurfaceCount).toBe(1);
  });
});
