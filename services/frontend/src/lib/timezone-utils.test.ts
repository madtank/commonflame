import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import { formatRelativeTime, formatTimestamp } from "./timezone-utils";

describe("timezone-utils", () => {
  const NOW = new Date("2025-08-07T12:00:00Z");

  beforeAll(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterAll(() => {
    vi.useRealTimers();
  });

  describe("formatRelativeTime", () => {
    it("handles naive UTC timestamp by assuming Z (UTC)", () => {
      const ts = "2025-08-07T11:59:00"; // no Z
      const out = formatRelativeTime(ts);
      expect(typeof out).toBe("string");
      // Avoid strict string equality; just ensure it indicates a past time
      expect(out.toLowerCase()).toContain("ago");
    });

    it("handles explicit Z timestamp", () => {
      const ts = "2025-08-07T11:00:00Z";
      const out = formatRelativeTime(ts);
      expect(out.toLowerCase()).toContain("ago");
    });

    it("returns Invalid date on unparsable input", () => {
      const out = formatRelativeTime("not-a-date");
      expect(out).toBe("Invalid date");
    });

    it("returns Unknown time on missing input", () => {
      const out = formatRelativeTime(undefined as unknown as string);
      expect(out).toBe("Unknown time");
    });
  });

  describe("formatTimestamp", () => {
    it("formats to yyyy-MM-dd HH:mm:ss for valid timestamps", () => {
      const ts = "2025-08-07T10:15:30"; // naive UTC
      const out = formatTimestamp(ts);
      expect(out).toBe("2025-08-07 10:15:30");
    });

    it("returns Invalid date on bad input", () => {
      const out = formatTimestamp("bad");
      expect(out).toBe("Invalid date");
    });
  });
});
