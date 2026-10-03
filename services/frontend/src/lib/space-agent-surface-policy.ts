import type {
  SpaceAgentCardEnvelope,
  SpaceAgentWidgetDescriptor,
} from "@/lib/space-agent-api";

export type SpaceAgentSurfacePlacement = "inline" | "card" | "panel" | "none";

export type SpaceAgentSurfacePolicy = {
  enabled: boolean;
  placement: SpaceAgentSurfacePlacement;
  allowFullscreen: boolean;
  collapseNarration: boolean;
};

type SurfacePolicyRule = {
  id: string;
  match: {
    toolNames?: string[];
    resourceUriPrefixes?: string[];
  };
  policy: SpaceAgentSurfacePolicy;
};

const DEFAULT_CARD_POLICY: SpaceAgentSurfacePolicy = {
  enabled: true,
  placement: "card",
  allowFullscreen: false,
  collapseNarration: true,
};

const DEFAULT_WIDGET_POLICY: SpaceAgentSurfacePolicy = {
  enabled: true,
  placement: "panel",
  allowFullscreen: true,
  collapseNarration: true,
};

export const MCP_SURFACE_POLICY_RULES: SurfacePolicyRule[] = [
  {
    id: "tasks",
    match: {
      toolNames: ["tasks", "tasks.list", "tasks.get"],
      resourceUriPrefixes: ["ui://task-board", "ui://task-detail"],
    },
    policy: {
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
      collapseNarration: true,
    },
  },
  {
    id: "agents",
    match: {
      toolNames: ["agents", "agents.list", "agents.get"],
      resourceUriPrefixes: ["ui://agent-dashboard", "ui://agent-profile"],
    },
    policy: {
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
      collapseNarration: true,
    },
  },
  {
    id: "whoami",
    match: {
      toolNames: ["whoami", "whoami.get", "auth.whoami"],
      resourceUriPrefixes: ["ui://whoami", "ui://identity-card"],
    },
    policy: {
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
      collapseNarration: true,
    },
  },
  {
    id: "spaces",
    match: {
      toolNames: ["spaces", "spaces.list", "spaces.get"],
      resourceUriPrefixes: ["ui://space-navigator", "ui://space-browser"],
    },
    policy: {
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
      collapseNarration: true,
    },
  },
  {
    id: "context",
    match: {
      toolNames: ["context", "context.list", "context.get"],
      resourceUriPrefixes: ["ui://context-browser", "ui://context-explorer"],
    },
    policy: {
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
      collapseNarration: true,
    },
  },
  {
    id: "search",
    match: {
      toolNames: ["search", "search.query"],
      resourceUriPrefixes: ["ui://search-results", "ui://search-browser"],
    },
    policy: {
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
      collapseNarration: true,
    },
  },
  {
    id: "messages",
    match: {
      toolNames: ["messages", "messages.list", "messages.check"],
      resourceUriPrefixes: ["ui://message-timeline", "ui://messages"],
    },
    policy: {
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
      collapseNarration: true,
    },
  },
];

/**
 * Widgets hidden by default in the conversation UI.
 *
 * These tools still work — they just don't render transcript app signals.
 * Users can re-enable them in Settings → Widgets.
 *
 * To hide a new widget: add its policy rule `id` to this set.
 */
export const DEFAULT_HIDDEN_WIDGETS = new Set([
  "messages", // sendMessage / readMessages — redundant in the conversation view
]);

function normalizeMatchKey(value?: string | null) {
  return (value || "").trim().toLowerCase();
}

function getWidgetPlacementFromDescriptor(
  widget: SpaceAgentWidgetDescriptor,
): SpaceAgentSurfacePlacement {
  const mode = normalizeMatchKey(widget.display_mode);
  if (mode === "fullscreen" || mode === "pip") return "panel";
  if (mode === "none") return "none";
  return "inline";
}

function matchesRule(
  widget: SpaceAgentWidgetDescriptor,
  rule: SurfacePolicyRule,
) {
  const toolName = normalizeMatchKey(widget.tool_name);
  const resourceUri = normalizeMatchKey(widget.resource_uri);

  const matchesTool =
    rule.match.toolNames?.some((candidate) => {
      const normalizedCandidate = normalizeMatchKey(candidate);
      return (
        toolName === normalizedCandidate ||
        toolName.startsWith(`${normalizedCandidate}.`)
      );
    }) || false;

  const matchesResource =
    rule.match.resourceUriPrefixes?.some((candidate) =>
      resourceUri.startsWith(normalizeMatchKey(candidate)),
    ) || false;

  return matchesTool || matchesResource;
}

export function resolveSpaceAgentSurfacePolicy(
  widget: SpaceAgentWidgetDescriptor,
  hiddenWidgets?: Set<string>,
): SpaceAgentSurfacePolicy {
  const matchedRule = MCP_SURFACE_POLICY_RULES.find((rule) =>
    matchesRule(widget, rule),
  );

  // Check frontend hide-list: rule id or tool_name
  const policyId = matchedRule?.id || normalizeMatchKey(widget.tool_name);
  if (policyId && hiddenWidgets?.has(policyId)) {
    return {
      enabled: false,
      placement: "none",
      allowFullscreen: false,
      collapseNarration: false,
    };
  }

  if (!matchedRule) {
    const descriptorPlacement = getWidgetPlacementFromDescriptor(widget);
    if (descriptorPlacement === "none") {
      return {
        ...DEFAULT_WIDGET_POLICY,
        placement: "none",
      };
    }
    return DEFAULT_WIDGET_POLICY;
  }

  const descriptorPlacement = getWidgetPlacementFromDescriptor(widget);
  return {
    ...matchedRule.policy,
    placement:
      descriptorPlacement !== "inline"
        ? descriptorPlacement
        : matchedRule.policy.placement,
  };
}

export function getSpaceAgentSurfacePolicyId(
  widget: SpaceAgentWidgetDescriptor,
) {
  const matchedRule = MCP_SURFACE_POLICY_RULES.find((rule) =>
    matchesRule(widget, rule),
  );
  return matchedRule?.id || normalizeMatchKey(widget.tool_name) || "widget";
}

export function getSpaceAgentCardSurfacePolicy(
  cards: SpaceAgentCardEnvelope[],
): SpaceAgentSurfacePolicy {
  if (!cards.length) {
    return {
      enabled: false,
      placement: "none",
      allowFullscreen: false,
      collapseNarration: false,
    };
  }

  return DEFAULT_CARD_POLICY;
}
