import { describe, it, expect } from "vitest";
import { normalizeAgentSummary } from "./api-clean";

// Logic extracted from api-clean.ts for regression testing
// We mock the detection logic here to ensure it behaves as expected
function determineRole(msg: any) {
  const str = (v: any) =>
    String(v || "")
      .toLowerCase()
      .trim();
  const authorType = str(msg.author?.type);
  const agentType = str(msg.agent_type);
  const senderType = str(msg.sender_type);
  const msgAuthorType = str(msg.author_type);

  // check explicit agent flags
  const isExplicitAgent =
    authorType === "agent" ||
    agentType === "agent" ||
    senderType === "agent" ||
    msgAuthorType === "agent";

  // Check if agent_type is present and clearly NOT human
  // This catches 'general', 'specialist', 'coding', etc.
  const isLikelyAgentType =
    agentType &&
    agentType !== "human" &&
    agentType !== "user" &&
    agentType !== "unknown";

  // FIX APPLIED: Ensure author_id is present before checking mismatch
  const hasAgentIdMismatch =
    msg.agent_id != null &&
    msg.author_id != null &&
    msg.agent_id !== msg.author_id;

  const isAgent = isExplicitAgent || isLikelyAgentType || hasAgentIdMismatch;

  const resolvedAuthorType =
    msg.author?.type ?? msg.author_type ?? msg.sender_type;
  return resolvedAuthorType || (isAgent ? "agent" : "human");
}

describe("api-clean agent detection logic", () => {
  const cases = [
    {
      name: "User Madtank (Original Bug)",
      // Case: User ID matches Agent ID (often happens for owners/users)
      // Should be HUMAN
      msg: { username: "madtank", agent_id: "123", author_id: "123" },
      expected: "human",
    },
    {
      name: "Agent Canvas (Regression)",
      // Case: Agent has a specific type like 'general' but not explicitly 'agent'
      // Should be AGENT
      msg: {
        username: "canvas_builder",
        agent_type: "general",
        agent_id: "999",
        author_id: "999",
      },
      expected: "agent",
    },
    {
      name: "Explicit Agent (Standard)",
      // Case: Explicitly marked as agent
      // Should be AGENT
      msg: { username: "bot", author_type: "agent" },
      expected: "agent",
    },
    {
      name: "Human Explicit",
      // Should be HUMAN
      msg: { username: "user", author_type: "human" },
      expected: "human",
    },
    {
      name: "Sender Type Agent",
      // Should be AGENT
      msg: { username: "bot2", sender_type: "agent" },
      expected: "agent",
    },
    {
      name: "Missing Author ID (Security Fix)",
      // Case: agent_id is present, but author_id is missing.
      // Should not default to agent via mismatch check.
      msg: { username: "human_user", agent_id: "123" },
      expected: "human",
    },
  ];

  cases.forEach((c) => {
    it(c.name, () => {
      const result = determineRole(c.msg);
      expect(result).toBe(c.expected);
    });
  });
});

describe("normalizeAgentSummary", () => {
  it("maps owned-agent API fields into the settings picker shape", () => {
    const normalized = normalizeAgentSummary({
      id: "agent-123",
      agent_name: "relay",
      status: "active",
      last_seen: "2026-03-21T00:00:00Z",
      visibility_level: "private",
      owner_id: "user-123",
      owner_username: "madtank",
      owner_full_name: "Jacob Taunton",
    });

    expect(normalized).toMatchObject({
      id: "agent-123",
      name: "relay",
      status: "active",
      last_active_at: "2026-03-21T00:00:00Z",
      visibility: "private",
      owner: {
        id: "user-123",
        handle: "madtank",
        name: "Jacob Taunton",
      },
    });
  });

  it("preserves explicit frontend fields when they are already present", () => {
    const normalized = normalizeAgentSummary({
      id: "agent-456",
      name: "canvas",
      avatar: "https://example.com/avatar.png",
      last_active_at: "2026-03-21T01:00:00Z",
      visibility: "public",
      status: "online",
    });

    expect(normalized).toMatchObject({
      id: "agent-456",
      name: "canvas",
      avatar: "https://example.com/avatar.png",
      last_active_at: "2026-03-21T01:00:00Z",
      visibility: "public",
      status: "online",
    });
  });
});
