import { useCallback, useRef } from "react";
import type {
  McpToolRegistryEntry,
  McpToolRegistry,
} from "@/lib/mcp-tool-registry";
import type { SpaceAgentMessage } from "@/lib/space-agent-api";
import { getToolCallContext } from "@/lib/realtime-manager";

// Content patterns that indicate a tool was used.
// Each pattern maps to the MCP tool name from the registry.
// Ordered by specificity — first match wins.
export const TOOL_CONTENT_PATTERNS: Array<{
  tool: string;
  patterns: RegExp[];
}> = [
  {
    tool: "tasks",
    patterns: [
      /\btask(?:s|board)?\b.*\b(?:open|active|complete|assigned|created|priority|total|breakdown)\b/i,
      /\b(?:open|active|assigned)\b.*\btasks?\b/i,
      /\btask.*(?:list|board|overview|breakdown|summary)\b/i,
      /\b\d+\s+tasks?\b.*\b(?:total|here|priority|breakdown)\b/i,
    ],
  },
  {
    tool: "agents",
    patterns: [
      /\bagent(?:s)?\b.*\b(?:roster|list|active|dashboard|available|total|breakdown)\b/i,
      /\b(?:active|available|roster)\b.*\bagents?\b/i,
      /\bagent.*(?:dashboard|overview|directory)\b/i,
      /\b\d+\s+agents?\s+total\b/i,
    ],
  },
  {
    tool: "context",
    patterns: [
      /\bcontext\b.*\b(?:key|keys|list|explorer|store|entries|values)\b/i,
      /\b(?:context|key-value|kv)\b.*\b(?:stored|set|get)\b/i,
    ],
  },
  {
    tool: "search",
    patterns: [
      /\bsearch\b.*\b(?:result|found|match|messages?)\b/i,
      /\b(?:found|results?)\b.*\bsearch\b/i,
    ],
  },
  {
    tool: "spaces",
    patterns: [
      /\bspace(?:s)?\b.*\b(?:list|member|navigator|browse)\b/i,
      /\b(?:workspace|space)\b.*\b(?:members?|overview)\b/i,
    ],
  },
  {
    tool: "whoami",
    patterns: [
      /\b(?:identity|who\s*am\s*i|your\s+(?:name|profile|identity))\b/i,
      /\bagent\s+identity\b/i,
    ],
  },
];

// Module-level cache: survives across hook re-renders and transcript refetches.
// Maps messageId → toolName (string) or null (checked, no match).
const globalEnrichmentCache = new Map<string, string | null>();
const MAX_ENRICHMENT_CACHE = 500;

function pruneCache() {
  if (globalEnrichmentCache.size <= MAX_ENRICHMENT_CACHE) return;
  const toDelete = globalEnrichmentCache.size - MAX_ENRICHMENT_CACHE;
  const iter = globalEnrichmentCache.keys();
  for (let i = 0; i < toDelete; i++) {
    const key = iter.next().value;
    if (key) globalEnrichmentCache.delete(key);
  }
}

function hasExistingWidget(message: SpaceAgentMessage): boolean {
  return Boolean(
    message.ui?.widget?.resource_uri ||
    message.metadata?.ui?.widget?.resource_uri ||
    message.message_metadata?.ui?.widget?.resource_uri,
  );
}

export function detectToolFromContent(
  content: string,
  registry: McpToolRegistry,
): McpToolRegistryEntry | null {
  if (!content || content.length < 20) return null;
  if (!registry || typeof registry !== "object") return null;

  for (const { tool, patterns } of TOOL_CONTENT_PATTERNS) {
    const entry = registry[tool];
    if (!entry) continue;

    for (const pattern of patterns) {
      if (pattern.test(content)) {
        return entry;
      }
    }
  }

  return null;
}

/** Reset cache — exposed for tests and space switches */
export function resetEnrichmentCache() {
  globalEnrichmentCache.clear();
}

/**
 * Build the widget descriptor, merging tool call context from SSE events
 * when available. This enables context-specific rendering (e.g. showing
 * a single task detail instead of the full task list).
 */
function buildWidgetDescriptor(
  toolName: string,
  resourceUri: string,
  messageId: string,
) {
  const ctx = getToolCallContext(messageId, toolName);
  return {
    kind: "mcp_app" as const,
    tool_name: toolName,
    resource_uri: ctx?.resource_uri || resourceUri,
    lifecycle: "complete" as const,
    ...(ctx?.arguments ? { tool_input: ctx.arguments } : {}),
  };
}

export function useMcpWidgetEnrichment(registry: McpToolRegistry | undefined) {
  // Use ref to hold registry so callback reference stays stable
  const registryRef = useRef(registry);
  registryRef.current = registry;

  // Stable callback — never changes identity, so useMemo in AxPlatformShell
  // won't invalidate on every transcript refetch
  const enrichMessage = useCallback(
    (message: SpaceAgentMessage): SpaceAgentMessage => {
      const reg = registryRef.current;
      if (!reg) return message;
      if (message.sender_type === "user" || message.sender_type === "human")
        return message;

      // Widget attachment is now driven by backend audit records.
      // If the backend attached a widget (from audited tool calls),
      // use it directly. No content-regex guessing — a missing widget
      // is better than a wrong widget.
      return message;
    },
    [], // stable — uses registryRef internally
  );

  return enrichMessage;
}
