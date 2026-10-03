import { describe, expect, it } from "vitest";
import {
  applyMentionSelections,
  deriveDraftRecipients,
  removeMentionFromDraft,
  describeDeliveryOutcome,
  toggleHandleSelection,
} from "./composer-recipients";

const KNOWN = ["nyx", "canary", "peach", "daimon"];

describe("deriveDraftRecipients", () => {
  it("returns explicit known mentions in order of appearance", () => {
    expect(
      deriveDraftRecipients({
        draft: "hey @canary and @nyx please review",
        knownHandles: KNOWN,
        defaultAgentHandle: "peach",
      }),
    ).toEqual([
      { handle: "canary", source: "explicit" },
      { handle: "nyx", source: "explicit" },
    ]);
  });

  it("dedupes repeated mentions of the same agent", () => {
    expect(
      deriveDraftRecipients({
        draft: "@nyx ping @nyx again",
        knownHandles: KNOWN,
        defaultAgentHandle: null,
      }),
    ).toEqual([{ handle: "nyx", source: "explicit" }]);
  });

  it("ignores mentions of unknown handles", () => {
    expect(
      deriveDraftRecipients({
        draft: "@ghost_agent @canary hello",
        knownHandles: KNOWN,
        defaultAgentHandle: null,
      }),
    ).toEqual([{ handle: "canary", source: "explicit" }]);
  });

  it("falls back to the sticky default agent when no explicit mentions exist", () => {
    expect(
      deriveDraftRecipients({
        draft: "standup in five",
        knownHandles: KNOWN,
        defaultAgentHandle: "peach",
      }),
    ).toEqual([{ handle: "peach", source: "default" }]);
  });

  it("returns no recipients when there are no mentions and no default", () => {
    expect(
      deriveDraftRecipients({
        draft: "standup in five",
        knownHandles: KNOWN,
        defaultAgentHandle: null,
      }),
    ).toEqual([]);
  });

  it("matches handles case-insensitively but reports directory casing", () => {
    expect(
      deriveDraftRecipients({
        draft: "@NYX hello",
        knownHandles: KNOWN,
        defaultAgentHandle: null,
      }),
    ).toEqual([{ handle: "nyx", source: "explicit" }]);
  });

  it("shows the default chip for an empty draft", () => {
    expect(
      deriveDraftRecipients({
        draft: "",
        knownHandles: KNOWN,
        defaultAgentHandle: "nyx",
      }),
    ).toEqual([{ handle: "nyx", source: "default" }]);
  });
});

describe("removeMentionFromDraft", () => {
  it("removes a standalone mention token and collapses whitespace", () => {
    expect(removeMentionFromDraft("@nyx @canary review this", "nyx")).toBe(
      "@canary review this",
    );
  });

  it("removes mentions case-insensitively", () => {
    expect(removeMentionFromDraft("hey @NYX review", "nyx")).toBe("hey review");
  });

  it("does not clobber longer handles sharing a prefix", () => {
    expect(removeMentionFromDraft("@nyx_dev @nyx hi", "nyx")).toBe(
      "@nyx_dev hi",
    );
  });

  it("removes every occurrence of the mention", () => {
    expect(removeMentionFromDraft("@nyx start @nyx end", "nyx")).toBe(
      "start end",
    );
  });

  it("preserves newlines when removing a mention from a multi-line draft", () => {
    expect(
      removeMentionFromDraft("@nyx status update\nline two stays", "nyx"),
    ).toBe("status update\nline two stays");
  });

  it("keeps surrounding lines intact when the removed mention sat on its own line", () => {
    expect(removeMentionFromDraft("line one\n@nyx\nline three", "nyx")).toBe(
      "line one\n\nline three",
    );
  });

  it("collapses doubled spaces from mid-line removal without touching newlines", () => {
    expect(removeMentionFromDraft("hey @nyx  team\nsecond line", "nyx")).toBe(
      "hey team\nsecond line",
    );
  });

  it("leaves drafts without the mention untouched", () => {
    expect(removeMentionFromDraft("plain message", "nyx")).toBe(
      "plain message",
    );
  });
});

describe("toggleHandleSelection", () => {
  it("adds a handle that is not selected", () => {
    expect(toggleHandleSelection(["nyx"], "canary")).toEqual(["nyx", "canary"]);
  });

  it("removes a handle that is already selected (case-insensitive)", () => {
    expect(toggleHandleSelection(["nyx", "canary"], "NYX")).toEqual(["canary"]);
  });
});

describe("applyMentionSelections", () => {
  it("replaces an in-progress @query token with the selected mentions", () => {
    expect(applyMentionSelections("hey @ca", ["canary", "nyx"])).toBe(
      "hey @canary @nyx ",
    );
  });

  it("replaces a bare @ trigger with the selected mentions", () => {
    expect(applyMentionSelections("@", ["canary"])).toBe("@canary ");
  });

  it("appends mentions when there is no in-progress token", () => {
    expect(applyMentionSelections("standup in five", ["nyx"])).toBe(
      "standup in five @nyx ",
    );
  });

  it("skips handles already mentioned in the draft", () => {
    expect(applyMentionSelections("@canary please @", ["canary", "nyx"])).toBe(
      "@canary please @nyx ",
    );
  });

  it("returns the draft unchanged when every selection is already mentioned", () => {
    expect(applyMentionSelections("@nyx hello", ["nyx"])).toBe("@nyx hello");
  });

  it("works on an empty draft", () => {
    expect(applyMentionSelections("", ["nyx", "peach"])).toBe("@nyx @peach ");
  });
});

describe("describeDeliveryOutcome", () => {
  it("reports delivered with the receiving agents when the receipt names them", () => {
    expect(
      describeDeliveryOutcome({
        receipt: { message_id: "m1", received_by: ["nyx", "canary"] },
      }),
    ).toEqual({
      state: "delivered",
      label: "Delivered to @nyx · @canary",
      wokeCount: 2,
    });
  });

  it("summarizes large recipient lists instead of listing every handle", () => {
    expect(
      describeDeliveryOutcome({
        receipt: {
          message_id: "m1",
          received_by: ["nyx", "canary", "peach", "daimon", "atlas"],
        },
      }),
    ).toEqual({
      state: "delivered",
      label: "Delivered · woke 5 agents",
      wokeCount: 5,
    });
  });

  it("reports sent when the receipt confirms the message without recipients", () => {
    expect(
      describeDeliveryOutcome({
        receipt: { message_id: "m1", received_by: null },
      }),
    ).toEqual({ state: "sent", label: "Sent", wokeCount: 0 });
  });

  it("treats a receipt without a message id as an unconfirmed send, not success", () => {
    expect(describeDeliveryOutcome({ receipt: { message_id: "" } })).toEqual({
      state: "failed",
      label: "Send not confirmed — check before retrying",
      wokeCount: 0,
    });
  });

  it("treats a receipt the API layer flagged unconfirmed as failed even when an id was synthesized", () => {
    expect(
      describeDeliveryOutcome({
        receipt: {
          message_id: "message-1780000000000",
          server_confirmed: false,
        },
      }),
    ).toEqual({
      state: "failed",
      label: "Send not confirmed — check before retrying",
      wokeCount: 0,
    });
  });

  it("treats a missing receipt as an unconfirmed send", () => {
    expect(describeDeliveryOutcome({ receipt: null })).toEqual({
      state: "failed",
      label: "Send not confirmed — check before retrying",
      wokeCount: 0,
    });
  });

  it("reports an explicit failure when the send threw", () => {
    expect(
      describeDeliveryOutcome({ error: new Error("network down") }),
    ).toEqual({
      state: "failed",
      label: "Send failed — message kept in queue",
      wokeCount: 0,
    });
  });
});
