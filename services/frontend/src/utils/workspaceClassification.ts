/**
 * Workspace Classification Utilities
 *
 * Classifies workspaces as Personal, Team, or Public based on their properties.
 * Used for security warnings and default behaviors for webhook agents.
 */

export type WorkspaceType = "personal" | "team" | "public";

export interface ClassifiableWorkspace {
  id: string;
  name: string;
  description?: string;
  visibility?: "public" | "private" | "team";
  is_personal?: boolean;
}

/**
 * Classifies a workspace based on its properties.
 *
 * Classification logic:
 * - Personal: description starts with "Personal workspace for" or is_personal is true
 * - Public: visibility is "public"
 * - Team: all other workspaces (private but not personal)
 */
export function getWorkspaceType(
  workspace: ClassifiableWorkspace,
): WorkspaceType {
  // Check is_personal flag first (backend may set this)
  if (workspace.is_personal === true) {
    return "personal";
  }

  // Personal workspace detection - check description pattern
  if (workspace.description?.startsWith("Personal workspace for")) {
    return "personal";
  }

  // Public workspace
  if (workspace.visibility === "public") {
    return "public";
  }

  // All other private workspaces are Team workspaces
  return "team";
}

/**
 * Security risk levels for webhook agents in different workspace types.
 */
export const WORKSPACE_SECURITY_RISK = {
  personal: {
    level: "low",
    title: "Personal Space",
    description: "Only you can interact with this agent.",
    recommendation: "Recommended for webhook agents.",
  },
  team: {
    level: "medium",
    title: "Team Space",
    description: "Team members can interact with this agent.",
    recommendation:
      "Acceptable if you trust all team members. Ensure your agent doesn't expose sensitive data.",
  },
  public: {
    level: "high",
    title: "Public Space",
    description: "Anyone can interact with this agent.",
    recommendation:
      "Not recommended. Public exposure could allow malicious actors to extract sensitive information from your agent.",
  },
} as const;

/**
 * Security warnings for webhook agents.
 */
export const WEBHOOK_SECURITY_WARNINGS = {
  general: {
    title: "Webhook Agent Security",
    description:
      "Webhook agents run on your infrastructure and may have access to sensitive data like environment variables, secrets, and local files. Anyone who can message your agent can potentially extract this information.",
  },
  personal: {
    title: "Personal Space (Recommended)",
    description:
      "Your personal space is private to you. This is the safest option for webhook agents.",
    variant: "default" as const,
  },
  team: {
    title: "Team Space",
    description:
      "Team members will be able to interact with this agent. Make sure your agent is configured to handle potentially sensitive requests appropriately, and that you trust all team members.",
    variant: "warning" as const,
  },
  public: {
    title: "Public Space (High Risk)",
    description:
      "Anyone can interact with agents in public spaces. This is strongly discouraged for webhook agents. Malicious users could potentially prompt your agent to reveal environment variables, secrets, or other sensitive data. Only proceed if your agent has robust protections and you accept the risk of data exposure.",
    variant: "destructive" as const,
  },
};

/**
 * Finds the user's personal workspace from a list of organizations.
 */
export function findPersonalWorkspace<T extends ClassifiableWorkspace>(
  workspaces: T[],
): T | undefined {
  return workspaces.find((ws) => getWorkspaceType(ws) === "personal");
}
