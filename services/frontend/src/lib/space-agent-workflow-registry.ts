import type { SpaceAgentWidgetDescriptor } from "@/lib/space-agent-api";

export type SpaceAgentWorkflowKind =
  | "list"
  | "create"
  | "review"
  | "inspect"
  | "confirm";

export type SpaceAgentWorkflowEntity =
  | "task"
  | "agent"
  | "context"
  | "memory"
  | "space"
  | "identity"
  | "message";

export type SpaceAgentWorkflowMode =
  | "inline-list"
  | "inline-review"
  | "inline-confirm"
  | "panel-edit";

export type SpaceAgentWorkflowDescriptor = {
  id: string;
  entity: SpaceAgentWorkflowEntity;
  kind: SpaceAgentWorkflowKind;
  mode: SpaceAgentWorkflowMode;
  title: string;
  description: string;
  toolNames?: string[];
  resourceUriPrefixes?: string[];
};

export const SPACE_AGENT_WORKFLOW_REGISTRY: SpaceAgentWorkflowDescriptor[] = [
  {
    id: "tasks.list",
    entity: "task",
    kind: "list",
    mode: "inline-list",
    title: "Task Board",
    description: "Browse and triage tasks inline without leaving chat.",
    toolNames: ["tasks", "tasks.list"],
    resourceUriPrefixes: ["ui://task-board"],
  },
  {
    id: "tasks.get",
    entity: "task",
    kind: "inspect",
    mode: "inline-review",
    title: "Task Detail",
    description: "Show a single task with full detail inline.",
    toolNames: ["tasks.get"],
    resourceUriPrefixes: ["ui://task-detail"],
  },
  {
    id: "tasks.create",
    entity: "task",
    kind: "create",
    mode: "inline-review",
    title: "Create Task",
    description:
      "Open a prepopulated task draft for quick review and confirmation.",
    toolNames: ["tasks.create"],
    resourceUriPrefixes: ["ui://task-create", "ui://task-editor"],
  },
  {
    id: "agents.list",
    entity: "agent",
    kind: "list",
    mode: "inline-list",
    title: "Agent Directory",
    description: "Show visible agents inline as a workflow-aware roster.",
    toolNames: ["agents", "agents.list"],
    resourceUriPrefixes: ["ui://agent-dashboard", "ui://agent-browser"],
  },
  {
    id: "agents.create",
    entity: "agent",
    kind: "create",
    mode: "panel-edit",
    title: "Create Agent",
    description:
      "Open a prepopulated agent draft so the user only has to accept or edit.",
    toolNames: ["agents.create", "agents.create_draft", "agents.register"],
    resourceUriPrefixes: ["ui://agent-create", "ui://agent-editor"],
  },
  {
    id: "context.list",
    entity: "context",
    kind: "list",
    mode: "inline-list",
    title: "Context Browser",
    description:
      "Inspect current context inline and drill deeper only if needed.",
    toolNames: ["context", "context.list"],
    resourceUriPrefixes: ["ui://context-browser", "ui://context-explorer"],
  },
  {
    id: "spaces.list",
    entity: "space",
    kind: "list",
    mode: "inline-list",
    title: "Space Navigator",
    description:
      "Browse spaces inline with chat still acting as the control layer.",
    toolNames: ["spaces", "spaces.list"],
    resourceUriPrefixes: ["ui://space-navigator", "ui://space-browser"],
  },
  {
    id: "spaces.create",
    entity: "space",
    kind: "create",
    mode: "panel-edit",
    title: "Create Space",
    description:
      "Open a prepopulated space draft so the user only has to accept or edit.",
    toolNames: ["spaces.create", "spaces.create_draft", "spaces.register"],
    resourceUriPrefixes: ["ui://space-create", "ui://space-editor"],
  },
  {
    id: "whoami.get",
    entity: "identity",
    kind: "inspect",
    mode: "inline-review",
    title: "Identity Card",
    description: "Show the current identity and access context inline.",
    toolNames: ["whoami", "whoami.get", "auth.whoami"],
    resourceUriPrefixes: ["ui://whoami", "ui://identity-card"],
  },
  {
    id: "context.inspect",
    entity: "context",
    kind: "inspect",
    mode: "inline-review",
    title: "Context",
    description:
      "Inspect or save context entries (memories, notes, knowledge) inline.",
    toolNames: [
      "context.get",
      "context.save",
      "context.update",
      "context.delete",
    ],
    resourceUriPrefixes: ["ui://context-detail", "ui://context-entry"],
  },
  {
    id: "messages.check",
    entity: "message",
    kind: "inspect",
    mode: "inline-review",
    title: "Message Timeline",
    description: "Inspect message activity inline without leaving chat.",
    toolNames: ["messages", "messages.list", "messages.check"],
    resourceUriPrefixes: ["ui://message-timeline", "ui://messages"],
  },
];

function normalizeKey(value?: string | null) {
  return (value || "").trim().toLowerCase();
}

function workflowMatchesToolKey(
  workflow: SpaceAgentWorkflowDescriptor,
  toolKey: string,
) {
  if (!toolKey) return false;
  return (
    workflow.toolNames?.some((candidate) => {
      const normalizedCandidate = normalizeKey(candidate);
      return toolKey === normalizedCandidate;
    }) || false
  );
}

function matchesWorkflow(
  widget: SpaceAgentWidgetDescriptor,
  workflow: SpaceAgentWorkflowDescriptor,
) {
  const toolName = normalizeKey(widget.tool_name);
  const toolAction = normalizeKey(widget.tool_action);
  const actionQualifiedToolName =
    toolName && toolAction ? `${toolName}.${toolAction}` : "";
  const resourceUri = normalizeKey(widget.resource_uri);

  const matchesTool =
    workflowMatchesToolKey(workflow, actionQualifiedToolName) ||
    workflowMatchesToolKey(workflow, toolName);

  const matchesResource =
    workflow.resourceUriPrefixes?.some((candidate) =>
      resourceUri.startsWith(normalizeKey(candidate)),
    ) || false;

  return matchesTool || matchesResource;
}

export function resolveSpaceAgentWorkflowIds(
  widget: SpaceAgentWidgetDescriptor,
) {
  const toolName = normalizeKey(widget.tool_name);
  const toolAction = normalizeKey(widget.tool_action);
  const actionQualifiedToolName =
    toolName && toolAction ? `${toolName}.${toolAction}` : "";

  if (actionQualifiedToolName) {
    const actionSpecificMatches = SPACE_AGENT_WORKFLOW_REGISTRY.filter(
      (workflow) => workflowMatchesToolKey(workflow, actionQualifiedToolName),
    );
    if (actionSpecificMatches.length > 0) {
      return actionSpecificMatches.map((workflow) => workflow.id);
    }
  }

  return SPACE_AGENT_WORKFLOW_REGISTRY.filter((workflow) =>
    matchesWorkflow(widget, workflow),
  ).map((workflow) => workflow.id);
}

export function resolveSpaceAgentWorkflowTitle(
  widget: SpaceAgentWidgetDescriptor,
) {
  const workflowIds = resolveSpaceAgentWorkflowIds(widget);
  const primaryWorkflow = SPACE_AGENT_WORKFLOW_REGISTRY.find((workflow) =>
    workflowIds.includes(workflow.id),
  );
  return primaryWorkflow?.title || null;
}
