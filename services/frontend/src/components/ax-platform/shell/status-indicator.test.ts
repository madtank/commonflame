import { describe, expect, it } from "vitest";
import { getStatusDisplay, type StatusDisplayConfig } from "./status-indicator";

describe("getStatusDisplay", () => {
  it("returns null when statusLabel is null or undefined", () => {
    expect(getStatusDisplay(null, null)).toBeNull();
    expect(getStatusDisplay(undefined, null)).toBeNull();
    expect(getStatusDisplay("", null)).toBeNull();
  });

  describe("thinking status", () => {
    it("returns purple/violet styling with pulse animation", () => {
      const result = getStatusDisplay("thinking", null)!;
      expect(result).not.toBeNull();
      expect(result.text).toBe("Thinking...");
      expect(result.icon).toBe("sparkles");
      expect(result.colorClass).toContain("purple");
      expect(result.animation).toBe("pulse");
    });
  });

  describe("tool_use status", () => {
    it("shows tool name when provided", () => {
      const result = getStatusDisplay("tool_use", "search_documents")!;
      expect(result).not.toBeNull();
      expect(result.text).toBe("Calling search documents...");
      expect(result.icon).toBe("wrench");
      expect(result.colorClass).toContain("cyan");
      expect(result.animation).toBe("spin");
    });

    it("falls back to generic text when toolName is null", () => {
      const result = getStatusDisplay("tool_use", null)!;
      expect(result.text).toBe("Using tool...");
    });

    it("is case-insensitive for status", () => {
      const result = getStatusDisplay("TOOL_USE", "my_tool")!;
      expect(result.text).toBe("Calling my tool...");
      expect(result.icon).toBe("wrench");
    });

    it("also matches tool_call status", () => {
      const result = getStatusDisplay("tool_call", "fetch_data")!;
      expect(result.text).toBe("Calling fetch data...");
      expect(result.icon).toBe("wrench");
    });

    it("normalizes dotted and dashed tool names", () => {
      const result = getStatusDisplay("tool_call", "messages.check-latest")!;
      expect(result.text).toBe("Calling messages check latest...");
    });
  });

  describe("streaming status", () => {
    it("returns green styling with pulse animation", () => {
      const result = getStatusDisplay("streaming", null)!;
      expect(result.text).toBe("Streaming...");
      expect(result.icon).toBe("radio");
      expect(result.colorClass).toContain("emerald");
      expect(result.animation).toBe("pulse");
    });
  });

  describe("queued status", () => {
    it("returns amber styling with no animation", () => {
      const result = getStatusDisplay("queued", null)!;
      expect(result.text).toBe("Queued");
      expect(result.icon).toBe("clock");
      expect(result.colorClass).toContain("amber");
      expect(result.animation).toBeNull();
    });
  });

  describe("sending status", () => {
    it("returns subtle styling", () => {
      const result = getStatusDisplay("Sending", null)!;
      expect(result.text).toBe("Sending");
      expect(result.icon).toBe("send");
      expect(result.animation).toBeNull();
    });
  });

  describe("completed/done statuses", () => {
    it.each(["completed", "done", "finished", "idle", "delivered"])(
      "returns done styling for '%s'",
      (status) => {
        const result = getStatusDisplay(status, null)!;
        expect(result.text).toBe("Done");
        expect(result.icon).toBe("check");
        expect(result.colorClass).toContain("emerald");
        expect(result.animation).toBeNull();
      },
    );
  });

  describe("started status", () => {
    it("returns starting indicator", () => {
      const result = getStatusDisplay("started", null)!;
      expect(result.text).toBe("Starting...");
      expect(result.icon).toBe("sparkles");
      expect(result.animation).toBe("pulse");
    });
  });

  describe("unknown status", () => {
    it("falls back to displaying raw status", () => {
      const result = getStatusDisplay("some_unknown_status", null)!;
      expect(result.text).toBe("some_unknown_status");
      expect(result.icon).toBe("activity");
      expect(result.animation).toBeNull();
    });
  });

  describe("composer delivery feedback", () => {
    it("styles delivery confirmations as done with the recipient list text", () => {
      const result = getStatusDisplay("Delivered to @nyx · @canary", null)!;
      expect(result.text).toBe("Delivered to @nyx · @canary");
      expect(result.icon).toBe("check");
      expect(result.colorClass).toContain("emerald");
    });

    it("styles woke-count delivery summaries as done", () => {
      const result = getStatusDisplay("Delivered · woke 5 agents", null)!;
      expect(result.text).toBe("Delivered · woke 5 agents");
      expect(result.icon).toBe("check");
    });

    it("styles send failures with the error treatment", () => {
      const result = getStatusDisplay(
        "Send failed — message kept in queue",
        null,
      )!;
      expect(result.text).toBe("Send failed — message kept in queue");
      expect(result.icon).toBe("activity");
      expect(result.colorClass).toContain("rose");
    });

    it("styles unconfirmed sends with the error treatment", () => {
      const result = getStatusDisplay(
        "Send not confirmed — check before retrying",
        null,
      )!;
      expect(result.colorClass).toContain("rose");
    });
  });
});
