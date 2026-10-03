/**
 * Author Type Detection Tests
 *
 * These tests are CRITICAL for preventing regressions in user vs agent detection.
 * The logic has regressed multiple times, causing:
 * - Human users showing as AGENT with trust scores
 * - Human users showing "owner @username" badge
 *
 * DO NOT MODIFY without understanding the full impact.
 */

import { describe, it, expect } from "vitest";
import {
  deriveAuthorRole,
  shouldShowTrustScores,
  shouldShowOwnerInfo,
  type AuthorTypeInput,
  type RosterEntryInput,
} from "./author-type";

describe("deriveAuthorRole", () => {
  describe("Priority 1: author_type field", () => {
    it('returns AGENT when author_type is "agent"', () => {
      const post: AuthorTypeInput = { author_type: "agent" };
      expect(deriveAuthorRole(post)).toBe("AGENT");
    });

    it('returns USER when author_type is "human"', () => {
      const post: AuthorTypeInput = { author_type: "human" };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it('returns USER when author_type is "user"', () => {
      const post: AuthorTypeInput = { author_type: "user" };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it('returns ADMIN when author_type is "admin"', () => {
      const post: AuthorTypeInput = { author_type: "admin" };
      expect(deriveAuthorRole(post)).toBe("ADMIN");
    });

    it("handles case insensitivity for author_type", () => {
      expect(deriveAuthorRole({ author_type: "AGENT" })).toBe("AGENT");
      expect(deriveAuthorRole({ author_type: "Agent" })).toBe("AGENT");
      expect(deriveAuthorRole({ author_type: "HUMAN" })).toBe("USER");
      expect(deriveAuthorRole({ author_type: "Human" })).toBe("USER");
      expect(deriveAuthorRole({ author_type: "USER" })).toBe("USER");
      expect(deriveAuthorRole({ author_type: "User" })).toBe("USER");
      expect(deriveAuthorRole({ author_type: "ADMIN" })).toBe("ADMIN");
      expect(deriveAuthorRole({ author_type: "Admin" })).toBe("ADMIN");
    });

    it("handles whitespace in author_type", () => {
      expect(deriveAuthorRole({ author_type: " agent " })).toBe("AGENT");
      expect(deriveAuthorRole({ author_type: " human " })).toBe("USER");
    });
  });

  describe("Priority 2: nested author.type field", () => {
    it('returns AGENT when author.type is "agent"', () => {
      const post: AuthorTypeInput = { author: { type: "agent" } };
      expect(deriveAuthorRole(post)).toBe("AGENT");
    });

    it('returns USER when author.type is "human"', () => {
      const post: AuthorTypeInput = { author: { type: "human" } };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it('returns USER when author.type is "user"', () => {
      const post: AuthorTypeInput = { author: { type: "user" } };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it('returns ADMIN when author.type is "admin"', () => {
      const post: AuthorTypeInput = { author: { type: "admin" } };
      expect(deriveAuthorRole(post)).toBe("ADMIN");
    });

    it("author_type takes priority over author.type", () => {
      const post: AuthorTypeInput = {
        author_type: "human",
        author: { type: "agent" },
      };
      expect(deriveAuthorRole(post)).toBe("USER");
    });
  });

  describe("Priority 3: sender_type field", () => {
    it('returns AGENT when sender_type is "agent"', () => {
      const post: AuthorTypeInput = { sender_type: "agent" };
      expect(deriveAuthorRole(post)).toBe("AGENT");
    });

    it('returns USER when sender_type is "human"', () => {
      const post: AuthorTypeInput = { sender_type: "human" };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it('returns USER when sender_type is "user"', () => {
      const post: AuthorTypeInput = { sender_type: "user" };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it("author_type takes priority over sender_type", () => {
      const post: AuthorTypeInput = {
        author_type: "agent",
        sender_type: "human",
      };
      expect(deriveAuthorRole(post)).toBe("AGENT");
    });
  });

  describe("Priority 4: roster entry type", () => {
    it('returns AGENT when rosterEntry.type is "agent"', () => {
      const post: AuthorTypeInput = {};
      const roster: RosterEntryInput = { type: "agent" };
      expect(deriveAuthorRole(post, roster)).toBe("AGENT");
    });

    it('returns ADMIN when rosterEntry.metadata.role is "admin"', () => {
      const post: AuthorTypeInput = {};
      const roster: RosterEntryInput = {
        type: "human",
        metadata: { role: "admin" },
      };
      expect(deriveAuthorRole(post, roster)).toBe("ADMIN");
    });

    it('returns ADMIN when rosterEntry.tags includes "admin"', () => {
      const post: AuthorTypeInput = {};
      const roster: RosterEntryInput = { type: "human", tags: ["admin"] };
      expect(deriveAuthorRole(post, roster)).toBe("ADMIN");
    });

    it('returns USER when rosterEntry.type is "human"', () => {
      const post: AuthorTypeInput = {};
      const roster: RosterEntryInput = { type: "human" };
      expect(deriveAuthorRole(post, roster)).toBe("USER");
    });

    it("author_type takes priority over roster entry", () => {
      const post: AuthorTypeInput = { author_type: "human" };
      const roster: RosterEntryInput = { type: "agent" };
      expect(deriveAuthorRole(post, roster)).toBe("USER");
    });
  });

  describe("Priority 5: agent_type field (legacy)", () => {
    it('returns AGENT when agent_type is "agent"', () => {
      const post: AuthorTypeInput = { agent_type: "agent" };
      expect(deriveAuthorRole(post)).toBe("AGENT");
    });

    it('returns USER when agent_type is "human"', () => {
      const post: AuthorTypeInput = { agent_type: "human" };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it('returns USER when agent_type is "user"', () => {
      const post: AuthorTypeInput = { agent_type: "user" };
      expect(deriveAuthorRole(post)).toBe("USER");
    });
  });

  describe("Default behavior", () => {
    it("defaults to USER when no type information is present", () => {
      const post: AuthorTypeInput = {};
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it("defaults to USER when only agent_id is present (CRITICAL REGRESSION TEST)", () => {
      // This is the bug that kept regressing!
      // Human users sometimes have agent_id set, and we were incorrectly
      // treating them as agents.
      const post: AuthorTypeInput = {
        agent_id: "some-uuid-123",
      };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it("defaults to USER when agent_id equals author_id", () => {
      const post: AuthorTypeInput = {
        agent_id: "uuid-123",
        author_id: "uuid-123",
      };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it("defaults to USER when null values are present", () => {
      const post: AuthorTypeInput = {
        author_type: null,
        sender_type: null,
        agent_type: null,
        agent_id: null,
      };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it("defaults to USER when empty strings are present", () => {
      const post: AuthorTypeInput = {
        author_type: "",
        sender_type: "",
        agent_type: "",
      };
      expect(deriveAuthorRole(post)).toBe("USER");
    });
  });

  describe("Real-world scenarios (regression tests)", () => {
    it("correctly identifies human user posting from UI", () => {
      // Typical shape when a human posts via the web UI
      const post: AuthorTypeInput = {
        author_type: "human",
        author_id: "user-uuid-123",
        user_id: "user-uuid-123",
      };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it("correctly identifies agent message", () => {
      // Typical shape when an agent posts via MCP
      const post: AuthorTypeInput = {
        author_type: "agent",
        agent_id: "agent-uuid-456",
        author: {
          type: "agent",
          id: "agent-uuid-456",
        },
      };
      expect(deriveAuthorRole(post)).toBe("AGENT");
    });

    it("correctly identifies human even when agent_id is present (BUG SCENARIO)", () => {
      // This was the actual bug - backend sometimes sets agent_id on human messages
      const post: AuthorTypeInput = {
        author_type: "human",
        agent_id: "some-id", // This should NOT make it an agent
        author_id: "user-uuid-123",
      };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it("correctly identifies human when author.type is missing but author_type is set", () => {
      const post: AuthorTypeInput = {
        author_type: "user",
        author: { id: "some-id" }, // type is missing
      };
      expect(deriveAuthorRole(post)).toBe("USER");
    });

    it("correctly handles MCP message with sender_type", () => {
      const post: AuthorTypeInput = {
        sender_type: "agent",
        agent_id: "agent-uuid",
      };
      expect(deriveAuthorRole(post)).toBe("AGENT");
    });

    it("correctly handles roster-based detection for agents", () => {
      const post: AuthorTypeInput = {
        // No explicit type fields
        agent_id: "agent-uuid",
      };
      const roster: RosterEntryInput = { type: "agent" };
      expect(deriveAuthorRole(post, roster)).toBe("AGENT");
    });

    it("correctly handles roster-based detection for humans", () => {
      const post: AuthorTypeInput = {
        // No explicit type fields
        user_id: "user-uuid",
      };
      const roster: RosterEntryInput = { type: "human" };
      expect(deriveAuthorRole(post, roster)).toBe("USER");
    });
  });
});

describe("shouldShowTrustScores", () => {
  it("returns true for AGENT role", () => {
    expect(shouldShowTrustScores("AGENT")).toBe(true);
  });

  it("returns false for USER role", () => {
    expect(shouldShowTrustScores("USER")).toBe(false);
  });
});

describe("shouldShowOwnerInfo", () => {
  it("returns true for AGENT role", () => {
    expect(shouldShowOwnerInfo("AGENT")).toBe(true);
  });

  it("returns false for USER role", () => {
    expect(shouldShowOwnerInfo("USER")).toBe(false);
  });
});
