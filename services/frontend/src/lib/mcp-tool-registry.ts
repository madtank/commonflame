import { useQuery } from "@tanstack/react-query";
import {
  postMcpJsonRpcDirect,
  resolveMcpAppsUrl,
} from "@/lib/space-agent-api";
import { config } from "@/config/environment";

export type McpToolRegistryEntry = {
  name: string;
  resourceUri: string;
  title: string;
};

export type McpToolRegistry = Record<string, McpToolRegistryEntry>;

type McpToolDefinition = {
  name?: string;
  description?: string;
  meta?: Record<string, unknown>;
  _meta?: Record<string, unknown>;
};

type McpToolsListResult = {
  tools?: McpToolDefinition[];
};

type McpAppsRegistryEntry = {
  tool_name?: string;
  resource_uri?: string;
  title?: string;
};

type McpAppsRegistryResult = Record<string, McpAppsRegistryEntry>;

function extractResourceUri(tool: McpToolDefinition): string | null {
  // Check meta["openai/outputTemplate"] (primary)
  const meta = tool.meta || tool._meta;
  if (!meta) return null;

  const outputTemplate = meta["openai/outputTemplate"];
  if (
    typeof outputTemplate === "string" &&
    outputTemplate.startsWith("ui://")
  ) {
    return outputTemplate;
  }

  // Check meta.ui.resourceUri (MCP Apps shape)
  const ui = meta.ui as Record<string, unknown> | undefined;
  if (ui && typeof ui.resourceUri === "string") {
    return ui.resourceUri;
  }

  return null;
}

function extractTitle(tool: McpToolDefinition): string {
  const meta = tool.meta || tool._meta;
  const invoked = meta?.["openai/toolInvocation/invoked"];
  if (typeof invoked === "string") return invoked;
  return tool.name || "Widget";
}

export async function fetchMcpToolRegistry(): Promise<McpToolRegistry> {
  try {
    return await fetchMcpAppsRegistry();
  } catch {
    return fetchMcpToolsListRegistry();
  }
}

async function fetchMcpAppsRegistry(): Promise<McpToolRegistry> {
  const response = await fetch(resolveMcpAppsUrl(config.mcpUrl), {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(`MCP app registry returned ${response.status}`);
  }

  const result = (await response.json()) as McpAppsRegistryResult;
  const registry: McpToolRegistry = {};

  for (const [key, entry] of Object.entries(result)) {
    const name = entry.tool_name || key;
    const resourceUri = entry.resource_uri;
    if (!name || !resourceUri) continue;

    const item = {
      name,
      resourceUri,
      title: entry.title || name,
    };

    registry[key] = item;
    registry[name] ??= item;
  }

  return registry;
}

async function fetchMcpToolsListRegistry(): Promise<McpToolRegistry> {
  const result = await postMcpJsonRpcDirect<McpToolsListResult>(
    "tools/list",
    undefined,
    { authenticated: true },
  );
  const tools = result?.tools || [];
  const registry: McpToolRegistry = {};

  for (const tool of tools) {
    if (!tool.name) continue;
    const resourceUri = extractResourceUri(tool);
    if (!resourceUri) continue;

    registry[tool.name] = {
      name: tool.name,
      resourceUri,
      title: extractTitle(tool),
    };
  }

  return registry;
}

export function useMcpToolRegistry() {
  return useQuery({
    queryKey: ["mcp-tool-registry"],
    queryFn: fetchMcpToolRegistry,
    staleTime: 5 * 60 * 1000,
    retry: 2,
    refetchOnWindowFocus: false,
  });
}
