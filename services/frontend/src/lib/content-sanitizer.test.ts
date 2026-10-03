import { describe, it, expect } from "vitest";
import { sanitizeMessageContent, isSystemNoiseOnly } from "./content-sanitizer";

describe("sanitizeMessageContent", () => {
  it("returns empty string for empty input", () => {
    expect(sanitizeMessageContent("")).toBe("");
  });

  it("returns null/undefined as-is", () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(sanitizeMessageContent(null as any)).toBe(null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(sanitizeMessageContent(undefined as any)).toBe(undefined);
  });

  it("passes through normal text unchanged", () => {
    const text = "Hello, how are you today?";
    expect(sanitizeMessageContent(text)).toBe(text);
  });

  it("passes through markdown content unchanged", () => {
    const text =
      "## Heading\n\n- item 1\n- item 2\n\n```js\nconsole.log('hi');\n```";
    expect(sanitizeMessageContent(text)).toBe(text);
  });

  it("strips 🧭 Identity block", () => {
    const raw = `🧭 Identity
Channel: discord
User id: abc123
AllowFrom: all

Here is my actual response.`;
    expect(sanitizeMessageContent(raw)).toBe("Here is my actual response.");
  });

  it("strips 🧭 Identity block with varied fields", () => {
    const raw = `🧭 Identity
Channel: telegram
User id: xyz
AllowFrom: team
Session: main
Runtime: node
Host: ec2
Model: claude

Real content here.`;
    expect(sanitizeMessageContent(raw)).toBe("Real content here.");
  });

  it("strips 🦞 OpenClaw status blocks", () => {
    const raw = `🦞 OpenClaw v2.1.0
Status: running
Uptime: 3h 22m

Actual message content.`;
    expect(sanitizeMessageContent(raw)).toBe("Actual message content.");
  });

  it("strips standalone system metadata lines", () => {
    const raw = `Runtime: node v22
Channel: discord
Here is the real content.
Thinking: enabled`;
    expect(sanitizeMessageContent(raw)).toBe("Here is the real content.");
  });

  it("strips multiple system blocks from one message", () => {
    const raw = `🧭 Identity
Channel: slack
User id: u1

🦞 OpenClaw v3
Mode: auto

The actual answer is 42.`;
    expect(sanitizeMessageContent(raw)).toBe("The actual answer is 42.");
  });

  it("collapses excessive newlines to double", () => {
    const raw = `Hello\n\n\n\n\nWorld`;
    expect(sanitizeMessageContent(raw)).toBe("Hello\n\nWorld");
  });

  it("trims leading and trailing whitespace", () => {
    const raw = `   \n\nHello world\n\n   `;
    expect(sanitizeMessageContent(raw)).toBe("Hello world");
  });

  it("handles identity block at end of message", () => {
    const raw = `My response.\n\n🧭 Identity\nChannel: irc\nUser id: bot`;
    const result = sanitizeMessageContent(raw);
    expect(result).toBe("My response.");
  });

  it("does not strip emoji that look similar but aren't system blocks", () => {
    const raw = "🧭 I used a compass to navigate! 🦞 I love lobster.";
    // These don't match the full pattern (no "Identity" after 🧭, no "OpenClaw" after 🦞)
    expect(sanitizeMessageContent(raw)).toBe(raw);
  });

  it("handles Agent: line", () => {
    const raw = "Agent: react_ranger\nHere's my update.";
    expect(sanitizeMessageContent(raw)).toBe("Here's my update.");
  });

  it("strips toggle-only Reasoning/Thinking lines", () => {
    const raw = "Reasoning: on\nThinking: enabled\nThe answer is yes.";
    expect(sanitizeMessageContent(raw)).toBe("The answer is yes.");
  });

  it("strips italic-wrapped reasoning metadata line", () => {
    const raw = "_Reasoning:_ on\nThe answer is yes.";
    expect(sanitizeMessageContent(raw)).toBe("The answer is yes.");
  });

  it("strips bold/italic wrapped system metadata labels", () => {
    const raw = "*Runtime*: node v22\n**Channel**: discord\nReal content.";
    expect(sanitizeMessageContent(raw)).toBe("Real content.");
  });

  it("keeps one-line reasoning payloads (regression)", () => {
    const raw = "Reasoning: I checked all paths and the deploy is complete.";
    expect(sanitizeMessageContent(raw)).toBe(raw);
  });

  it("keeps reply tags (regression)", () => {
    const raw = "[[reply_to_current]] shipped";
    expect(sanitizeMessageContent(raw)).toBe(raw);
  });
});

describe("isSystemNoiseOnly", () => {
  it("returns true for pure system noise", () => {
    const raw = `🧭 Identity
Channel: discord
User id: abc123`;
    expect(isSystemNoiseOnly(raw)).toBe(true);
  });

  it("returns true for standalone metadata lines only", () => {
    const raw = `Runtime: node
Channel: telegram
Session: main`;
    expect(isSystemNoiseOnly(raw)).toBe(true);
  });

  it("returns false when real content exists", () => {
    const raw = `Runtime: node\nHello world`;
    expect(isSystemNoiseOnly(raw)).toBe(false);
  });

  it("returns true for empty string", () => {
    expect(isSystemNoiseOnly("")).toBe(true);
  });

  it("returns false for normal text", () => {
    expect(isSystemNoiseOnly("Just a normal message")).toBe(false);
  });
});
