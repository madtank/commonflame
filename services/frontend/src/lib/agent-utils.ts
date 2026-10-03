/**
 * Agent utility functions
 *
 * Shared utilities for agent-related operations.
 */

/**
 * Pattern to detect V2 engine from cloud_function_url (fallback)
 * Matches URLs containing "agent-runner-v2" or "agent_runner_v2"
 */
export const V2_ENGINE_URL_PATTERN = /agent[_-]runner[_-]v2/i;

/**
 * Check if an agent is using the V2 engine.
 * Prefers explicit engine_version field, falls back to URL pattern matching.
 *
 * @param engineVersion - The engine_version field from the agent (preferred)
 * @param cloudFunctionUrl - The cloud_function_url from the agent (fallback)
 * @returns true if the agent is using V2 engine
 */
export const isV2Engine = (
  engineVersion: "v1" | "v2" | string | null | undefined,
  cloudFunctionUrl?: string | null,
): boolean => {
  // Prefer explicit engine_version field when available
  if (engineVersion === "v2") return true;
  if (engineVersion === "v1") return false;

  // Fall back to URL pattern matching for backward compatibility
  return cloudFunctionUrl
    ? V2_ENGINE_URL_PATTERN.test(cloudFunctionUrl)
    : false;
};

type ExternalAgentLike = {
  origin?: string;
  runtime_kind?: string | null;
  runtime_label?: string | null;
  runtime?: string | null;
  runner_type?: string | null;
  execution_method?: string | null;
  sub_type?: string;
  subtype?: string;
  external_sub_type?: string;
  webhook_url?: string;
  webhookUrl?: string;
  external_webhook_url?: string;
  externalWebhookUrl?: string;
  settings?:
    | {
        follow_user?: boolean;
        sub_type?: string;
        subtype?: string;
        external_sub_type?: string;
        webhook_url?: string;
        webhookUrl?: string;
        external_webhook_url?: string;
        externalWebhookUrl?: string;
      }
    | string
    | null;
  capabilities?: {
    sub_type?: string;
  };
  metadata?:
    | {
        sub_type?: string;
        external_sub_type?: string;
        webhook_url?: string;
        external_webhook_url?: string;
      }
    | string
    | null;
  agent_type?: string;
  cloud_function_url?: string | null;
  enable_cloud_agent?: boolean;
};

const resolveString = (...values: Array<unknown>): string | null => {
  for (const value of values) {
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed.length > 0) return trimmed;
    }
  }
  return null;
};

const parseObject = (value: unknown): Record<string, any> | null => {
  if (!value || typeof value !== "string") return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
};

export const resolveExternalSubType = (
  agent: ExternalAgentLike | null | undefined,
): string | null => {
  if (!agent) return null;
  const parsedSettings = parseObject(agent.settings);
  const parsedMetadata = parseObject(agent.metadata);
  return resolveString(
    agent.capabilities?.sub_type,
    agent.sub_type,
    agent.subtype,
    agent.external_sub_type,
    (agent.settings as any)?.sub_type,
    (agent.settings as any)?.subtype,
    (agent.settings as any)?.external_sub_type,
    parsedSettings?.sub_type,
    parsedSettings?.subtype,
    parsedSettings?.external_sub_type,
    (agent.metadata as any)?.sub_type,
    (agent.metadata as any)?.external_sub_type,
    parsedMetadata?.sub_type,
    parsedMetadata?.external_sub_type,
  );
};

export const resolveExternalWebhookUrl = (
  agent: ExternalAgentLike | null | undefined,
): string | null => {
  if (!agent) return null;
  const parsedSettings = parseObject(agent.settings);
  const parsedMetadata = parseObject(agent.metadata);
  return resolveString(
    agent.webhook_url,
    agent.webhookUrl,
    agent.external_webhook_url,
    agent.externalWebhookUrl,
    (agent.settings as any)?.webhook_url,
    (agent.settings as any)?.webhookUrl,
    (agent.settings as any)?.external_webhook_url,
    (agent.settings as any)?.externalWebhookUrl,
    parsedSettings?.webhook_url,
    parsedSettings?.webhookUrl,
    parsedSettings?.external_webhook_url,
    parsedSettings?.externalWebhookUrl,
    (agent.metadata as any)?.webhook_url,
    (agent.metadata as any)?.external_webhook_url,
    parsedMetadata?.webhook_url,
    parsedMetadata?.external_webhook_url,
  );
};

export const isExternalAgent = (
  agent: ExternalAgentLike | null | undefined,
): boolean => {
  if (!agent) return false;
  const type = (agent.agent_type || "").toLowerCase();
  const origin = (agent.origin || "").toLowerCase();
  return Boolean(
    resolveExternalSubType(agent) ||
    resolveExternalWebhookUrl(agent) ||
    origin === "external_gateway" ||
    type === "external_gateway" ||
    type === "external",
  );
};

export const isNativeCloudAgent = (
  agent: ExternalAgentLike | null | undefined,
): boolean => {
  if (!agent) return false;
  const type = (agent.agent_type || "").toLowerCase();
  const origin = (agent.origin || "").toLowerCase();
  return Boolean(
    agent.enable_cloud_agent ||
    agent.cloud_function_url ||
    origin === "cloud" ||
    type === "cloud_gcp",
  );
};

export const isCloudLikeAgent = (
  agent: ExternalAgentLike | null | undefined,
): boolean => isNativeCloudAgent(agent) || isExternalAgent(agent);

export function getAgentRuntimeDisplay(
  agent: ExternalAgentLike | null | undefined,
): { label: string; title: string } {
  if (!agent) {
    return {
      label: "Agent",
      title: "Agent runtime",
    };
  }

  if (isExternalAgent(agent)) {
    return {
      label: "Webhook",
      title: "Webhook agent - connected through an external gateway",
    };
  }

  if (isNativeCloudAgent(agent)) {
    return {
      label: "Cloud",
      title: "Cloud agent - runs on hosted infrastructure",
    };
  }

  const rawRuntime = resolveString(
    agent.runtime_label,
    agent.runtime_kind,
    agent.runtime,
    agent.runner_type,
    agent.execution_method,
    agent.origin,
    agent.agent_type,
  )?.toLowerCase();

  if (rawRuntime) {
    if (rawRuntime.includes("cli")) {
      return {
        label: "CLI",
        title: "CLI agent - runs through the Waystation CLI",
      };
    }

    if (rawRuntime.includes("mcp")) {
      return {
        label: "MCP",
        title: "MCP agent - runs locally through Model Context Protocol",
      };
    }
  }

  return {
    label: "Local",
    title: "Local agent runtime",
  };
}
