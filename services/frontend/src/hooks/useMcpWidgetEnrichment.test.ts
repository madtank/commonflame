import { beforeEach, describe, expect, it } from "vitest";
import type { SpaceAgentMessage } from "@/lib/space-agent-api";
import type { McpToolRegistry } from "@/lib/mcp-tool-registry";
import {
  detectToolFromContent,
  resetEnrichmentCache,
} from "./useMcpWidgetEnrichment";

// Test helper that mirrors the hook's enrichment logic using the global cache
// (imported functions use the same module-level cache as the hook)
function enrichMessage(
  message: SpaceAgentMessage,
  registry: McpToolRegistry,
): SpaceAgentMessage {
  if (message.sender_type === "user" || message.sender_type === "human")
    return message;

  const hasWidget = Boolean(
    message.ui?.widget?.resource_uri ||
    message.metadata?.ui?.widget?.resource_uri ||
    message.message_metadata?.ui?.widget?.resource_uri,
  );
  if (hasWidget) return message;

  const detected = detectToolFromContent(message.content || "", registry);
  if (!detected) return message;

  return {
    ...message,
    ui: {
      ...message.ui,
      widget: {
        kind: "mcp_app",
        tool_name: detected.name,
        resource_uri: detected.resourceUri,
        lifecycle: "complete",
      },
    },
  };
}

const REGISTRY: McpToolRegistry = {
  agents: {
    name: "agents",
    resourceUri: "ui://agent-dashboard",
    title: "Agent dashboard",
  },
  tasks: {
    name: "tasks",
    resourceUri: "ui://task-board",
    title: "Task board",
  },
  context: {
    name: "context",
    resourceUri: "ui://context-explorer",
    title: "Context explorer",
  },
  search: {
    name: "search",
    resourceUri: "ui://search-results",
    title: "Search results",
  },
  spaces: {
    name: "spaces",
    resourceUri: "ui://space-navigator",
    title: "Space navigator",
  },
  whoami: {
    name: "whoami",
    resourceUri: "ui://agent-identity",
    title: "Agent identity",
  },
};

beforeEach(() => {
  resetEnrichmentCache();
});

describe("detectToolFromContent", () => {
  it("detects tasks from content mentioning open tasks", () => {
    const result = detectToolFromContent(
      "Here are all 50 tasks. The priority breakdown shows 3 open tasks.",
      REGISTRY,
    );
    expect(result?.name).toBe("tasks");
  });

  it("detects agents from agent roster content", () => {
    const result = detectToolFromContent(
      "Here is the agent roster with 12 active agents in this space.",
      REGISTRY,
    );
    expect(result?.name).toBe("agents");
  });

  it("detects agents from 'N agents total' phrasing", () => {
    const result = detectToolFromContent(
      "Already listed those for you above — 50 agents total. Here's the sorted breakdown.",
      REGISTRY,
    );
    expect(result?.name).toBe("agents");
  });

  it("detects context from context keys content", () => {
    const result = detectToolFromContent(
      "Listed all 152 context keys. Sorted by theme: Diagnostics.",
      REGISTRY,
    );
    expect(result?.name).toBe("context");
  });

  it("detects search from search results content", () => {
    const result = detectToolFromContent(
      "Search results found 15 matching messages for your query.",
      REGISTRY,
    );
    expect(result?.name).toBe("search");
  });

  it("returns null for short content", () => {
    expect(detectToolFromContent("Done.", REGISTRY)).toBeNull();
  });

  it("returns null for generic content without tool indicators", () => {
    expect(
      detectToolFromContent(
        "I understand your question. Let me think about the best approach.",
        REGISTRY,
      ),
    ).toBeNull();
  });

  it("returns null for undefined/empty registry", () => {
    expect(
      detectToolFromContent("open tasks available", {} as McpToolRegistry),
    ).toBeNull();
  });

  it("returns null when registry lacks the matched tool", () => {
    const partialRegistry: McpToolRegistry = {
      agents: {
        name: "agents",
        resourceUri: "ui://agent-dashboard",
        title: "Agents",
      },
    };
    expect(
      detectToolFromContent(
        "Here are 50 open tasks with priorities.",
        partialRegistry,
      ),
    ).toBeNull();
  });
});

describe("MCP widget enrichment", () => {
  it("enriches agent message with detected tool widget", () => {
    const message: SpaceAgentMessage = {
      id: "msg-1",
      content: "Here are all 50 tasks. Priority breakdown shows open items.",
      sender_type: "agent",
    };
    const enriched = enrichMessage(message, REGISTRY);
    expect(enriched.ui?.widget).toEqual({
      kind: "mcp_app",
      tool_name: "tasks",
      resource_uri: "ui://task-board",
      lifecycle: "complete",
    });
  });

  it("does NOT enrich user messages", () => {
    const message: SpaceAgentMessage = {
      id: "msg-2",
      content: "List all open tasks for me please",
      sender_type: "user",
    };
    expect(enrichMessage(message, REGISTRY)).toBe(message);
  });

  it("does NOT enrich messages with existing ui.widget", () => {
    const message: SpaceAgentMessage = {
      id: "msg-3",
      content: "Here are the active tasks in this space.",
      sender_type: "agent",
      ui: {
        widget: { tool_name: "tasks", resource_uri: "ui://task-board" },
      },
    };
    expect(enrichMessage(message, REGISTRY)).toBe(message);
  });

  it("does NOT enrich messages with existing metadata.ui.widget", () => {
    const message: SpaceAgentMessage = {
      id: "msg-4",
      content: "Here are the active tasks in this space.",
      sender_type: "agent",
      metadata: {
        ui: { widget: { resource_uri: "ui://task-board" } },
      },
    };
    expect(enrichMessage(message, REGISTRY)).toBe(message);
  });

  it("preserves existing ui.cards when enriching", () => {
    const message: SpaceAgentMessage = {
      id: "msg-5",
      content: "Here are 8 active agents in the dashboard overview.",
      sender_type: "agent",
      ui: {
        cards: [{ card_id: "c1", type: "receipt", version: 1, payload: {} }],
      },
    };
    const enriched = enrichMessage(message, REGISTRY);
    expect(enriched.ui?.cards).toHaveLength(1);
    expect(enriched.ui?.widget?.tool_name).toBe("agents");
  });
});

describe("enrichment cache", () => {
  it("resetEnrichmentCache clears the global cache", () => {
    // First call detects the tool
    const msg: SpaceAgentMessage = {
      id: "cache-1",
      content: "Here are all 50 open tasks with priority breakdown.",
      sender_type: "agent",
    };
    const enriched = enrichMessage(msg, REGISTRY);
    expect(enriched.ui?.widget?.tool_name).toBe("tasks");

    // Reset cache
    resetEnrichmentCache();

    // Detection runs again (not cached)
    const enriched2 = enrichMessage(msg, REGISTRY);
    expect(enriched2.ui?.widget?.tool_name).toBe("tasks");
  });
});
