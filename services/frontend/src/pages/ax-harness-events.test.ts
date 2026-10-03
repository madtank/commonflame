import { describe, expect, it } from "vitest";
import { matchesHarnessRunEvent } from "./ax-harness-events";

describe("matchesHarnessRunEvent", () => {
  it("matches the root user message directly", () => {
    expect(
      matchesHarnessRunEvent(
        { message_id: "root-1", conversation_id: "root-1" },
        "root-1",
        "root-1",
      ),
    ).toBe(true);
  });

  it("matches assistant child events by parent id", () => {
    expect(
      matchesHarnessRunEvent(
        {
          message_id: "child-1",
          parent_id: "root-1",
          conversation_id: "root-1",
        },
        "root-1",
        "root-1",
      ),
    ).toBe(true);
  });

  it("matches nested message payloads", () => {
    expect(
      matchesHarnessRunEvent(
        {
          message: {
            id: "child-1",
            parent_id: "root-1",
            conversation_id: "root-1",
          },
        },
        "root-1",
        "root-1",
      ),
    ).toBe(true);
  });

  it("rejects unrelated events", () => {
    expect(
      matchesHarnessRunEvent(
        {
          message_id: "other-1",
          parent_id: "other-root",
          conversation_id: "other-root",
        },
        "root-1",
        "root-1",
      ),
    ).toBe(false);
  });
});
