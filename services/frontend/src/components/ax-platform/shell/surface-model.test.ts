import { describe, expect, it } from "vitest";
import type { SpaceAgentConversationCard } from "@/lib/space-agent-api";
import type { ChatEntry } from "./transcript-model";
import {
  buildConversationCardAnchors,
  buildConversationGroups,
  buildEntryLookup,
  buildThreadIdByEntryId,
} from "./transcript-model";
import {
  collectGroupedSurfaces,
  shouldSuppressInlineEntrySurfaces,
} from "./surface-model";

function createEntry(
  id: string,
  role: ChatEntry["role"],
  overrides: Partial<ChatEntry> = {},
): ChatEntry {
  return {
    id,
    role,
    meta: role === "user" ? "You" : "aX",
    fromLabel: role === "user" ? "You" : "aX",
    content: `content-${id}`,
    createdAt: "2026-03-13T10:00:00Z",
    ...overrides,
  };
}

function createThreadState(
  entries: ChatEntry[],
  card?: SpaceAgentConversationCard,
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
    threadIdByEntryId,
    conversationGroups,
    conversationCardsByThread,
    conversationCardAnchors,
  };
}

describe("surface model", () => {
  it("collects unique surfaces across all entries in a summarized thread", () => {
    const surfaces = collectGroupedSurfaces([
      createEntry("root", "user", {
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
      createEntry("reply", "agent", {
        parentId: "root",
        conversationId: "root",
        surfaces: [
          {
            id: "task-surface",
            kind: "widget",
            placement: "inline",
            status: "ready",
            widget: { tool_name: "tasks" },
          },
          {
            id: "agent-surface",
            kind: "widget",
            placement: "inline",
            status: "ready",
            widget: { tool_name: "agents" },
          },
        ],
      }),
    ]);

    expect(surfaces.map((surface) => surface.id)).toEqual([
      "task-surface",
      "agent-surface",
    ]);
  });

  it("suppresses duplicate inline surfaces when a summary card owns the thread", () => {
    const entries = [
      createEntry("root", "user", {
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
      createEntry("reply-1", "agent", {
        parentId: "root",
        conversationId: "root",
        aiSummary: "Working on it",
      }),
      createEntry("reply-2", "agent", {
        parentId: "root",
        conversationId: "root",
        aiSummary: "Done",
      }),
    ];

    const state = createThreadState(entries, {
      id: "card-1",
      root_message_id: "root",
      summary: "Thread summary",
    });

    expect(
      shouldSuppressInlineEntrySurfaces({
        cardsEnabled: true,
        entry: entries[0],
        threadIdByEntryId: state.threadIdByEntryId,
        conversationGroups: state.conversationGroups,
        conversationCardsByThread: state.conversationCardsByThread,
        conversationCardAnchors: state.conversationCardAnchors,
      }),
    ).toBe(true);
  });

  it("keeps inline surfaces visible when no summary card owns the thread", () => {
    const entry = createEntry("solo", "user", {
      surfaces: [
        {
          id: "task-surface",
          kind: "widget",
          placement: "inline",
          status: "ready",
          widget: { tool_name: "tasks" },
        },
      ],
    });
    const state = createThreadState([entry]);

    expect(
      shouldSuppressInlineEntrySurfaces({
        cardsEnabled: true,
        entry,
        threadIdByEntryId: state.threadIdByEntryId,
        conversationGroups: state.conversationGroups,
        conversationCardsByThread: state.conversationCardsByThread,
        conversationCardAnchors: state.conversationCardAnchors,
      }),
    ).toBe(false);
  });
});
