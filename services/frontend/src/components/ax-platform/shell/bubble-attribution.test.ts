import { describe, expect, it } from "vitest";
import {
  classifyBubbleAttribution,
  getBubbleTestId,
  getBubbleClassName,
} from "./bubble-attribution";

describe("classifyBubbleAttribution", () => {
  it("returns 'agent' for agent-role entries regardless of viewer", () => {
    expect(
      classifyBubbleAttribution(
        { role: "agent", fromHandle: "anything" },
        "madtank",
      ),
    ).toBe("agent");
  });

  it("returns 'self' when fromHandle matches viewer handle", () => {
    expect(
      classifyBubbleAttribution(
        { role: "user", fromHandle: "madtank" },
        "madtank",
      ),
    ).toBe("self");
  });

  it("ignores leading @ on either handle when comparing", () => {
    expect(
      classifyBubbleAttribution(
        { role: "user", fromHandle: "@madtank" },
        "madtank",
      ),
    ).toBe("self");
    expect(
      classifyBubbleAttribution(
        { role: "user", fromHandle: "madtank" },
        "@madtank",
      ),
    ).toBe("self");
  });

  it("compares handles case-insensitively", () => {
    expect(
      classifyBubbleAttribution(
        { role: "user", fromHandle: "MadTank" },
        "madtank",
      ),
    ).toBe("self");
  });

  it("returns 'peer' when handles differ", () => {
    expect(
      classifyBubbleAttribution(
        { role: "user", fromHandle: "codex_uat" },
        "madtank",
      ),
    ).toBe("peer");
  });

  it("falls back to 'self' when viewer handle is unknown to preserve current styling", () => {
    expect(
      classifyBubbleAttribution(
        { role: "user", fromHandle: "codex_uat" },
        null,
      ),
    ).toBe("self");
    expect(
      classifyBubbleAttribution({ role: "user", fromHandle: "codex_uat" }, ""),
    ).toBe("self");
  });

  it("falls back to 'self' when fromHandle is missing", () => {
    expect(
      classifyBubbleAttribution(
        { role: "user", fromHandle: undefined },
        "madtank",
      ),
    ).toBe("self");
    expect(
      classifyBubbleAttribution({ role: "user", fromHandle: null }, "madtank"),
    ).toBe("self");
  });
});

describe("getBubbleTestId", () => {
  it("maps each attribution to its data-testid", () => {
    expect(getBubbleTestId("self")).toBe("ax-user-bubble");
    expect(getBubbleTestId("peer")).toBe("ax-peer-bubble");
    expect(getBubbleTestId("agent")).toBe("ax-agent-bubble");
  });
});

describe("getBubbleClassName", () => {
  it("right-aligns self bubbles with the blue treatment", () => {
    const dark = getBubbleClassName("self", true);
    const light = getBubbleClassName("self", false);
    expect(dark).toContain("ml-auto");
    expect(light).toContain("ml-auto");
    expect(light).toContain("bg-blue-50");
    expect(dark).toMatch(/#[0-9a-f]{6}/i); // gradient color stops
  });

  it("renders peer bubbles left-aligned, neutral, and not blue", () => {
    const dark = getBubbleClassName("peer", true);
    const light = getBubbleClassName("peer", false);
    expect(dark).not.toContain("ml-auto");
    expect(light).not.toContain("ml-auto");
    expect(dark.toLowerCase()).not.toContain("blue");
    expect(light.toLowerCase()).not.toContain("blue");
    expect(light).toContain("slate");
    expect(dark).toContain("slate");
  });

  it("keeps agent bubbles distinct from peer (no slate-100 fill in light)", () => {
    const peerLight = getBubbleClassName("peer", false);
    const agentLight = getBubbleClassName("agent", false);
    expect(agentLight).not.toContain("ml-auto");
    expect(agentLight).not.toBe(peerLight);
    // agent uses bg-white in light mode; peer uses bg-slate-100/80
    expect(agentLight).toContain("bg-white");
    expect(peerLight).not.toContain("bg-white");
  });
});
