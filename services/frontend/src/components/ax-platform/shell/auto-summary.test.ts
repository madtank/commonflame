import { describe, expect, it } from "vitest";

import {
  collectAutoSummaryCandidateIds,
  getAutoSummaryReplacement,
  getAutoSummarySignals,
  MESSAGE_STREAM_SUMMARY_THRESHOLD_CHARS,
} from "@/components/ax-platform/shell/auto-summary";
import type { ChatEntry } from "@/components/ax-platform/shell/transcript-model";

function makeEntry(overrides: Partial<ChatEntry>): ChatEntry {
  return {
    id: overrides.id || "entry",
    role: overrides.role || "agent",
    meta: overrides.meta || "meta",
    content: overrides.content || "content",
    ...overrides,
  };
}

describe("collectAutoSummaryCandidateIds", () => {
  it("treats the first transcript load as baseline only", () => {
    const entries = [
      makeEntry({ id: "a1", role: "agent" }),
      makeEntry({ id: "u1", role: "user" }),
    ];

    const result = collectAutoSummaryCandidateIds(
      entries,
      new Map(),
      new Set(),
    );

    expect(result.candidateIds).toEqual([]);
    expect([...result.knownIds.keys()]).toEqual(["a1", "u1"]);
  });

  it("selects only new unsummarized agent replies after baseline", () => {
    const baseline = collectAutoSummaryCandidateIds(
      [
        makeEntry({ id: "old-agent", role: "agent" }),
        makeEntry({ id: "old-user", role: "user" }),
      ],
      new Map(),
      new Set(),
    );
    const requestedIds = new Set(["already-requested"]);
    const entries = [
      makeEntry({ id: "old-agent", role: "agent" }),
      makeEntry({ id: "old-user", role: "user" }),
      makeEntry({ id: "already-requested", role: "agent" }),
      makeEntry({ id: "summarized", role: "agent", aiSummary: "done" }),
      makeEntry({ id: "streaming", role: "agent", isStreaming: true }),
      makeEntry({ id: "seed-agent", role: "agent" }),
      makeEntry({ id: "fresh-agent", role: "agent" }),
      makeEntry({ id: "fresh-user", role: "user" }),
    ];

    const result = collectAutoSummaryCandidateIds(
      entries,
      baseline.knownIds,
      requestedIds,
    );

    expect(result.candidateIds).toEqual(["fresh-agent"]);
    expect(result.knownIds.get("fresh-agent")).toBeTruthy();
    expect(result.knownIds.get("fresh-user")).toBeTruthy();
  });

  it("skips progress placeholders and re-queues finalized content for the same id", () => {
    const baseline = collectAutoSummaryCandidateIds(
      [makeEntry({ id: "old-agent", role: "agent" })],
      new Map(),
      new Set(),
    );

    const placeholder = collectAutoSummaryCandidateIds(
      [
        makeEntry({ id: "old-agent", role: "agent" }),
        makeEntry({
          id: "reply-1",
          role: "agent",
          content: "Working…",
          metadata: {
            streaming_reply: {
              enabled: true,
              final: false,
            },
          },
        }),
      ],
      baseline.knownIds,
      new Set(),
    );

    expect(placeholder.candidateIds).toEqual([]);

    const finalized = collectAutoSummaryCandidateIds(
      [
        makeEntry({ id: "old-agent", role: "agent" }),
        makeEntry({
          id: "reply-1",
          role: "agent",
          content:
            "Final gateway response with enough real content to summarize.",
        }),
      ],
      placeholder.knownIds,
      new Set(),
    );

    expect(finalized.candidateIds).toEqual(["reply-1"]);
  });

  it("returns a stream replacement summary only for completed agent replies", () => {
    const summarized = makeEntry({
      id: "fresh-agent",
      role: "agent",
      content: "x".repeat(MESSAGE_STREAM_SUMMARY_THRESHOLD_CHARS),
      aiSummary: "Concise summary",
    });

    expect(getAutoSummaryReplacement(summarized, true)).toBe("Concise summary");
    expect(
      getAutoSummaryReplacement(
        makeEntry({
          id: "streaming-agent",
          role: "agent",
          aiSummary: "Still working",
          isStreaming: true,
        }),
        true,
      ),
    ).toBeNull();
    expect(
      getAutoSummaryReplacement(
        makeEntry({
          id: "user-entry",
          role: "user",
          aiSummary: "Nope",
        }),
        true,
      ),
    ).toBeNull();
    expect(getAutoSummaryReplacement(summarized, false)).toBeNull();
  });

  it("keeps short replies rendered as full text even when they have summaries", () => {
    const shortReply = makeEntry({
      id: "short-agent",
      role: "agent",
      content: "Short reply",
      aiSummary: "Short summary",
    });

    expect(getAutoSummaryReplacement(shortReply, true)).toBeNull();
  });

  it("keeps full text when the summary is not actually shorter", () => {
    const content = "x".repeat(MESSAGE_STREAM_SUMMARY_THRESHOLD_CHARS + 20);
    const verboseSummary = "y".repeat(content.length + 10);
    const entry = makeEntry({
      id: "verbose-summary",
      role: "agent",
      content,
      aiSummary: verboseSummary,
    });

    expect(getAutoSummaryReplacement(entry, true)).toBeNull();
  });

  it("extracts full-response character count and unique emojis", () => {
    const entry = makeEntry({
      id: "emoji-signals",
      role: "agent",
      content:
        "✨ Launch is live for @orion and @aX 🚀✨ Great job team 🎉 @orion",
      toLabel: "aX",
    });

    expect(getAutoSummarySignals(entry)).toEqual({
      fullResponseCharCount:
        "✨ Launch is live for @orion and @aX 🚀✨ Great job team 🎉 @orion"
          .length,
      emojis: ["✨", "🚀", "✨", "🎉"],
      mentions: ["@orion", "@aX"],
      targetLabel: "aX",
    });
  });
});
