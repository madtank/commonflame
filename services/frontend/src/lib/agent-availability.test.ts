import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";

import {
  AVAILABILITY_META,
  availabilityPriority,
  deriveAvailabilityKey,
} from "./agent-availability";

describe("deriveAvailabilityKey", () => {
  const NOW = new Date("2026-05-30T20:00:00.000Z");

  beforeAll(() => {
    vi.spyOn(Date, "now").mockReturnValue(NOW.getTime());
  });

  afterAll(() => {
    vi.restoreAllMocks();
  });

  it("marks a disabled agent disabled regardless of heartbeat", () => {
    expect(
      deriveAvailabilityKey({ status: "disabled", presence_fresh: true }),
    ).toBe("disabled");
  });

  it("treats a fresh heartbeat (presence_fresh) as online", () => {
    expect(deriveAvailabilityKey({ presence_fresh: true })).toBe("online");
  });

  it("treats a heartbeat under 60s as online via age", () => {
    expect(deriveAvailabilityKey({ presence_age_seconds: 30 })).toBe("online");
  });

  it("treats a heartbeat between 60s and 5m as idle", () => {
    expect(deriveAvailabilityKey({ presence_age_seconds: 120 })).toBe("idle");
  });

  it("derives age from last_heartbeat ISO when presence_age is absent", () => {
    const tenSecAgo = new Date(NOW.getTime() - 10_000).toISOString();
    expect(deriveAvailabilityKey({ last_heartbeat: tenSecAgo })).toBe("online");
  });

  it("accepts backend availability aliases from /api/v1/agents", () => {
    const tenSecAgo = new Date(NOW.getTime() - 10_000).toISOString();
    expect(deriveAvailabilityKey({ last_heartbeat_at: tenSecAgo })).toBe(
      "online",
    );
    expect(deriveAvailabilityKey({ last_seen: tenSecAgo })).toBe("online");
    expect(deriveAvailabilityKey({ is_online: true })).toBe("online");
  });

  it("falls back to lifecycle when there is no heartbeat (coverage gap)", () => {
    expect(deriveAvailabilityKey({ lifecycle_state: "active" })).toBe("idle");
    expect(deriveAvailabilityKey({ lifecycle_state: "idle" })).toBe("idle");
    expect(deriveAvailabilityKey({ lifecycle_state: "dormant" })).toBe(
      "dormant",
    );
  });

  it("defaults to dormant when nothing is known", () => {
    expect(deriveAvailabilityKey({})).toBe("dormant");
  });

  it("treats a space agent (aX) as always-on regardless of heartbeat", () => {
    expect(deriveAvailabilityKey({ origin: "space_agent" })).toBe("online");
    expect(
      deriveAvailabilityKey({
        origin: "space_agent",
        lifecycle_state: "dormant",
      }),
    ).toBe("online");
  });

  it("still marks a disabled space agent disabled", () => {
    expect(
      deriveAvailabilityKey({ origin: "space_agent", status: "disabled" }),
    ).toBe("disabled");
  });

  it("keeps a recently-active agent idle even with a stale heartbeat (heartbeat only upgrades to online)", () => {
    expect(
      deriveAvailabilityKey({
        presence_age_seconds: 6000,
        lifecycle_state: "active",
      }),
    ).toBe("idle");
  });

  it("is dormant with a stale heartbeat and a dormant lifecycle", () => {
    expect(
      deriveAvailabilityKey({
        presence_age_seconds: 6000,
        lifecycle_state: "dormant",
      }),
    ).toBe("dormant");
  });
});

describe("availabilityPriority", () => {
  it("orders live agents first", () => {
    const order = (
      ["disabled", "dormant", "online", "idle", "needs_setup"] as const
    )
      .slice()
      .sort((a, b) => availabilityPriority(a) - availabilityPriority(b));
    expect(order).toEqual([
      "online",
      "idle",
      "needs_setup",
      "dormant",
      "disabled",
    ]);
  });

  it("matches the widget's label meta priorities", () => {
    expect(AVAILABILITY_META.online.priority).toBe(0);
    expect(AVAILABILITY_META.disabled.priority).toBe(4);
  });
});
