import { describe, expect, it, vi } from "vitest";

import {
  appendAgentMentionToCompose,
  normalizeAgentHandle,
  prependAgentMentionIfNeeded,
} from "./agent-compose";

describe("agent compose helpers", () => {
  it("normalizes handles consistently", () => {
    expect(normalizeAgentHandle("@canvas")).toBe("canvas");
    expect(normalizeAgentHandle("  @@logic_runner ")).toBe("logic_runner");
    expect(normalizeAgentHandle("")).toBeNull();
    expect(normalizeAgentHandle(undefined)).toBeNull();
  });

  it("dispatches compose append event for valid handles", () => {
    const listener = vi.fn();
    window.addEventListener(
      "ax:agent-mention-append",
      listener as EventListener,
    );

    expect(appendAgentMentionToCompose("@canvas")).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
    const event = listener.mock.calls[0]?.[0] as CustomEvent<{
      handle: string;
    }>;
    expect(event.detail.handle).toBe("canvas");

    window.removeEventListener(
      "ax:agent-mention-append",
      listener as EventListener,
    );
  });

  it("returns false for empty handles", () => {
    expect(appendAgentMentionToCompose("   ")).toBe(false);
  });

  it("prepends a target mention when no explicit mention exists", () => {
    expect(prependAgentMentionIfNeeded("can you check this?", "@canvas")).toBe(
      "@canvas can you check this?",
    );
  });

  it("does not duplicate the same target mention", () => {
    expect(
      prependAgentMentionIfNeeded("@canvas can you check this?", "@canvas"),
    ).toBe("@canvas can you check this?");
  });

  it("preserves an explicit different mention", () => {
    expect(
      prependAgentMentionIfNeeded("@relay can you check this?", "@canvas"),
    ).toBe("@relay can you check this?");
  });
});
