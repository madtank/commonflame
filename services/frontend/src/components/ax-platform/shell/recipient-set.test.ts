import { describe, expect, it } from "vitest";
import {
  buildRailSuggestionHandles,
  parseStoredRecipientHandles,
  prependAgentMentionsIfNeeded,
  toggleRecipientHandle,
} from "./recipient-set";

describe("toggleRecipientHandle", () => {
  it("adds an unselected agent to the set", () => {
    expect(toggleRecipientHandle(["nyx"], "canary", "aX")).toEqual([
      "nyx",
      "canary",
    ]);
  });

  it("removes an already-selected agent (case-insensitive)", () => {
    expect(toggleRecipientHandle(["nyx", "canary"], "NYX", "aX")).toEqual([
      "canary",
    ]);
  });

  it("clears the whole set when the concierge is chosen", () => {
    expect(toggleRecipientHandle(["nyx", "canary"], "aX", "aX")).toEqual([]);
  });

  it("normalizes leading @ before toggling", () => {
    expect(toggleRecipientHandle([], "@nyx", "aX")).toEqual(["nyx"]);
  });
});

describe("prependAgentMentionsIfNeeded", () => {
  it("prepends every selected handle when the content has no mentions", () => {
    expect(
      prependAgentMentionsIfNeeded("standup in five", ["nyx", "peach"]),
    ).toBe("@nyx @peach standup in five");
  });

  it("leaves content untouched when it already has an explicit mention", () => {
    expect(prependAgentMentionsIfNeeded("@canary take this", ["nyx"])).toBe(
      "@canary take this",
    );
  });

  it("leaves content untouched when no recipients are selected", () => {
    expect(prependAgentMentionsIfNeeded("hello there", [])).toBe("hello there");
  });

  it("returns just the mentions for empty content", () => {
    expect(prependAgentMentionsIfNeeded("", ["nyx"])).toBe("@nyx ");
  });
});

describe("buildRailSuggestionHandles", () => {
  it("puts selected handles first, then fills with ranked suggestions up to the limit", () => {
    expect(
      buildRailSuggestionHandles({
        selected: ["peach"],
        ranked: ["nyx", "canary", "peach", "daimon", "atlas"],
        limit: 4,
      }),
    ).toEqual(["peach", "nyx", "canary", "daimon"]);
  });

  it("always includes every selected handle even past the limit", () => {
    expect(
      buildRailSuggestionHandles({
        selected: ["a", "b", "c", "d", "e"],
        ranked: ["nyx"],
        limit: 4,
      }),
    ).toEqual(["a", "b", "c", "d", "e"]);
  });

  it("dedupes ranked handles already selected, case-insensitively", () => {
    expect(
      buildRailSuggestionHandles({
        selected: ["NYX"],
        ranked: ["nyx", "canary"],
        limit: 3,
      }),
    ).toEqual(["NYX", "canary"]);
  });
});

describe("parseStoredRecipientHandles", () => {
  it("parses a stored JSON array of handles", () => {
    expect(parseStoredRecipientHandles('["nyx","peach"]', null)).toEqual([
      "nyx",
      "peach",
    ]);
  });

  it("migrates a legacy single default handle when the new key is empty", () => {
    expect(parseStoredRecipientHandles(null, "nyx")).toEqual(["nyx"]);
  });

  it("returns an empty set for garbage values", () => {
    expect(parseStoredRecipientHandles("not-json", null)).toEqual([]);
    expect(parseStoredRecipientHandles('{"a":1}', null)).toEqual([]);
    expect(parseStoredRecipientHandles("[1,2]", null)).toEqual([]);
  });
});
