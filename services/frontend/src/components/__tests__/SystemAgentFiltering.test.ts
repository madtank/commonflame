import { describe, it, expect } from "vitest";

/**
 * Tests for system agent filtering in quick mentions.
 *
 * System agents (like __ai_validator__) should be filtered from:
 * 1. recentAgentActivityFromApi (App.tsx)
 * 2. recentAgentActivity from LiveMonitor (LiveMonitor.tsx)
 * 3. agents query results (App.tsx)
 *
 * The filter rule: usernames starting with "__" (double underscore) are hidden.
 */

// Helper function that mirrors the filtering logic used in the codebase
const isSystemAgent = (username: string): boolean => {
  return username.startsWith("__");
};

describe("System Agent Filtering", () => {
  describe("Prefix Accuracy (Mid-String Test)", () => {
    it("should filter usernames starting with __", () => {
      expect(isSystemAgent("__ai_validator__")).toBe(true);
      expect(isSystemAgent("__system_bot")).toBe(true);
      expect(isSystemAgent("__internal")).toBe(true);
    });

    it("should NOT filter usernames with __ in the middle", () => {
      expect(isSystemAgent("agent__with__underscores")).toBe(false);
      expect(isSystemAgent("my__agent")).toBe(false);
      expect(isSystemAgent("test__bot__123")).toBe(false);
    });

    it("should NOT filter usernames ending with __", () => {
      expect(isSystemAgent("agent__")).toBe(false);
      expect(isSystemAgent("bot__")).toBe(false);
    });
  });

  describe("Single vs Double Underscore", () => {
    it("should NOT filter usernames starting with single underscore", () => {
      expect(isSystemAgent("_experimental_agent")).toBe(false);
      expect(isSystemAgent("_private_bot")).toBe(false);
      expect(isSystemAgent("_test")).toBe(false);
    });

    it("should filter usernames starting with double underscore", () => {
      expect(isSystemAgent("__experimental_agent")).toBe(true);
      expect(isSystemAgent("__private_bot")).toBe(true);
    });
  });

  describe("Case Sensitivity", () => {
    it("should filter lowercase system agents", () => {
      expect(isSystemAgent("__ai_validator__")).toBe(true);
    });

    it("should filter uppercase system agents", () => {
      // Note: startsWith is case-sensitive, so __AI_VALIDATOR__ starts with "__"
      expect(isSystemAgent("__AI_VALIDATOR__")).toBe(true);
    });

    it("should filter mixed case system agents", () => {
      expect(isSystemAgent("__Ai_Validator__")).toBe(true);
    });
  });

  describe("Edge Cases", () => {
    it("should handle empty strings", () => {
      expect(isSystemAgent("")).toBe(false);
    });

    it("should handle just underscores", () => {
      expect(isSystemAgent("__")).toBe(true);
      expect(isSystemAgent("_")).toBe(false);
      expect(isSystemAgent("___")).toBe(true);
    });

    it("should handle usernames with spaces (edge case)", () => {
      expect(isSystemAgent(" __ai_validator__")).toBe(false); // Space before
      expect(isSystemAgent("__ai validator__")).toBe(true); // Space in middle
    });
  });
});

describe("recentAgentActivityFromApi filtering", () => {
  // Simulates the filtering logic in App.tsx recentAgentActivityFromApi
  const filterRecentAgents = (
    agents: Array<{ name?: string; display_name?: string }>,
  ) => {
    return agents
      .map((agent) => {
        const rawName = String(agent.name || agent.display_name || "").trim();
        if (!rawName) return null;
        // Filter out internal system agents (e.g., __ai_validator__)
        if (rawName.startsWith("__")) return null;
        return { username: rawName };
      })
      .filter(Boolean);
  };

  it("should filter out __ai_validator__ from API response", () => {
    const apiResponse = [
      { name: "__ai_validator__" },
      { name: "sphinxz_910" },
      { name: "madtank_ai" },
      { name: "__system_bot" },
    ];

    const filtered = filterRecentAgents(apiResponse);

    expect(filtered).toHaveLength(2);
    expect(filtered.map((a) => a?.username)).toEqual([
      "sphinxz_910",
      "madtank_ai",
    ]);
  });

  it("should preserve agents with __ in middle of name", () => {
    const apiResponse = [
      { name: "agent__test" },
      { name: "__hidden_agent" },
      { name: "normal_agent" },
    ];

    const filtered = filterRecentAgents(apiResponse);

    expect(filtered).toHaveLength(2);
    expect(filtered.map((a) => a?.username)).toContain("agent__test");
    expect(filtered.map((a) => a?.username)).toContain("normal_agent");
  });

  it("should handle display_name fallback", () => {
    const apiResponse = [
      { display_name: "__ai_validator__" },
      { display_name: "visible_agent" },
    ];

    const filtered = filterRecentAgents(apiResponse);

    expect(filtered).toHaveLength(1);
    expect(filtered[0]?.username).toBe("visible_agent");
  });
});

describe("LiveMonitor recentAgentActivity filtering", () => {
  // Simulates the filtering logic in LiveMonitor.tsx
  const extractAgentsFromPosts = (
    posts: Array<{ username: string; uploaded_at: string }>,
  ) => {
    const stats = new Map<string, { lastMessageAt: number; count: number }>();

    posts.forEach((post) => {
      if (!post?.username) return;
      // Filter out internal system agents (e.g., __ai_validator__)
      if (post.username.startsWith("__")) return;

      const timestamp = Date.parse(post.uploaded_at);
      if (!Number.isFinite(timestamp)) return;

      const existing = stats.get(post.username) ?? {
        lastMessageAt: timestamp,
        count: 0,
      };
      existing.lastMessageAt = Math.max(existing.lastMessageAt, timestamp);
      existing.count += 1;
      stats.set(post.username, existing);
    });

    return Array.from(stats.keys());
  };

  it("should filter out posts from __ai_validator__", () => {
    const posts = [
      { username: "__ai_validator__", uploaded_at: "2025-01-01T12:00:00Z" },
      { username: "sphinxz_910", uploaded_at: "2025-01-01T12:01:00Z" },
      { username: "__ai_validator__", uploaded_at: "2025-01-01T12:02:00Z" },
      { username: "madtank", uploaded_at: "2025-01-01T12:03:00Z" },
    ];

    const agents = extractAgentsFromPosts(posts);

    expect(agents).toHaveLength(2);
    expect(agents).toContain("sphinxz_910");
    expect(agents).toContain("madtank");
    expect(agents).not.toContain("__ai_validator__");
  });

  it("should handle human mention of system agent in content (username still filtered)", () => {
    // If a human mentions @__ai_validator__ in their message content,
    // the post's username is the human's name, not the mentioned agent.
    // The mentioned agent should not appear in quick mentions from this.
    const posts = [
      {
        username: "madtank",
        uploaded_at: "2025-01-01T12:00:00Z",
        // content: "Hey @__ai_validator__ can you help?" // content doesn't affect username extraction
      },
    ];

    const agents = extractAgentsFromPosts(posts);

    expect(agents).toContain("madtank");
    expect(agents).not.toContain("__ai_validator__");
  });

  it("should preserve single underscore agents", () => {
    const posts = [
      { username: "_experimental_agent", uploaded_at: "2025-01-01T12:00:00Z" },
      { username: "__hidden_agent", uploaded_at: "2025-01-01T12:01:00Z" },
    ];

    const agents = extractAgentsFromPosts(posts);

    expect(agents).toHaveLength(1);
    expect(agents).toContain("_experimental_agent");
  });
});

describe("agents query filtering", () => {
  // Simulates the filtering logic in App.tsx agents query
  const filterAgentsQuery = (
    agents: Array<{ agent_name?: string; username?: string }>,
  ) => {
    return agents
      .map((agent) => ({
        username: agent.agent_name || agent.username || "",
      }))
      .filter((a) => !a.username.startsWith("__"));
  };

  it("should filter out system agents from query results", () => {
    const queryResult = [
      { agent_name: "__ai_validator__" },
      { agent_name: "sphinxz_910" },
      { username: "__system_monitor" },
      { username: "delta_storm_408" },
    ];

    const filtered = filterAgentsQuery(queryResult);

    expect(filtered).toHaveLength(2);
    expect(filtered.map((a) => a.username)).toEqual([
      "sphinxz_910",
      "delta_storm_408",
    ]);
  });
});
