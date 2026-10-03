import {
  type SpaceAgentCardEnvelope,
  type SpaceAgentMessage,
  type SpaceAgentSendReceipt,
  type SpaceAgentWidgetDescriptor,
} from "@/lib/space-agent-api";
import {
  getSpaceAgentCardSurfacePolicy,
  getSpaceAgentSurfacePolicyId,
  resolveSpaceAgentSurfacePolicy,
  type SpaceAgentSurfacePlacement,
} from "@/lib/space-agent-surface-policy";
import { resolveSpaceAgentWorkflowIds } from "@/lib/space-agent-workflow-registry";

export type SpaceAgentNormalizedSurface =
  | {
      id: string;
      sourceMessageId?: string;
      policyId?: string;
      workflowIds?: string[];
      kind: "cards";
      placement: "card";
      cards: SpaceAgentCardEnvelope[];
      status: "ready";
    }
  | {
      id: string;
      sourceMessageId?: string;
      policyId?: string;
      workflowIds?: string[];
      kind: "widget";
      placement: SpaceAgentSurfacePlacement;
      widget: SpaceAgentWidgetDescriptor;
      status: "pending" | "ready" | "error";
    };

type SurfaceContainer =
  | (Pick<SpaceAgentMessage, "ui" | "metadata" | "message_metadata"> & {
      id?: string | null;
      sender_type?: string | null;
      sender_id?: string | null;
      user_id?: string | null;
      agent_id?: string | null;
      display_name?: string | null;
      created_at?: string | null;
    })
  | (Pick<SpaceAgentSendReceipt, "ui" | "metadata" | "message_metadata"> & {
      id?: string | null;
      sender_type?: string | null;
      sender_id?: string | null;
      user_id?: string | null;
      agent_id?: string | null;
      display_name?: string | null;
      created_at?: string | null;
    });

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function hasKey(payload: Record<string, unknown>, key: string) {
  return asString(payload[key]) !== null;
}

function inferCardIntent(card: SpaceAgentCardEnvelope) {
  const payload = asRecord(card.payload) || {};
  const explicit = asString(payload.intent);
  if (
    explicit === "alert" ||
    explicit === "signal" ||
    explicit === "context" ||
    explicit === "review"
  ) {
    return explicit;
  }
  if (card.type === "alert") return "alert";
  if (card.type === "context") return "context";
  if (card.type === "confirmation" || card.type === "handoff_select")
    return "review";
  if (
    card.type === "task" ||
    card.type === "agent" ||
    card.type === "result" ||
    card.type === "receipt"
  ) {
    return "signal";
  }
  return "signal";
}

function inferEvidenceMode(
  card: SpaceAgentCardEnvelope,
  alert: Record<string, unknown> | null,
) {
  const payload = asRecord(card.payload) || {};
  const explicit = asString(payload.evidence_mode);
  if (
    explicit === "none" ||
    explicit === "preview" ||
    explicit === "widget" ||
    explicit === "preview_widget"
  ) {
    return explicit;
  }

  const attachments = [
    ...asArray(payload.attachments),
    ...asArray(alert?.attachments),
  ];
  const hasPreview =
    attachments.length > 0 ||
    asArray(payload.references).length > 0 ||
    asArray(payload.artifacts).length > 0 ||
    hasKey(payload, "body_markdown") ||
    hasKey(payload, "context_key") ||
    hasKey(payload, "preview_url") ||
    hasKey(payload, "preview_markdown");
  const hasWidget =
    hasKey(payload, "resource_uri") ||
    hasKey(payload, "tool_name") ||
    hasKey(payload, "widget_resource_uri");

  if (hasPreview && hasWidget) return "preview_widget";
  if (hasPreview) return "preview";
  if (hasWidget) return "widget";
  return "none";
}

function getCards(entity: SurfaceContainer): SpaceAgentCardEnvelope[] {
  return (
    entity.ui?.cards ||
    entity.metadata?.ui?.cards ||
    entity.message_metadata?.ui?.cards ||
    []
  );
}

function getAlertMetadata(entity: SurfaceContainer) {
  return (
    asRecord(entity.metadata?.alert) ||
    asRecord(entity.message_metadata?.alert) ||
    null
  );
}

function hasTaskSpecificEvidence(
  evidenceCard?: SpaceAgentCardEnvelope | null,
): boolean {
  if (!evidenceCard) return false;
  const payload = asRecord(evidenceCard.payload) || {};

  return Boolean(
    evidenceCard.type === "task" ||
    hasKey(payload, "task_id") ||
    hasKey(payload, "task_title") ||
    hasKey(payload, "task_summary") ||
    hasKey(payload, "task_status") ||
    hasKey(payload, "task_priority"),
  );
}

function buildAlertCard(
  entity: SurfaceContainer,
  alert: Record<string, unknown>,
  evidenceCard?: SpaceAgentCardEnvelope | null,
): SpaceAgentCardEnvelope {
  const evidencePayload = asRecord(evidenceCard?.payload) || {};
  const kind = asString(alert.kind) || "alert";
  const source = asString(alert.source);
  const origin = asString(alert.origin);
  const triggeredBy =
    asString(alert.triggered_by_agent_name) ||
    asString(alert.agent) ||
    asString(entity.display_name);
  const title =
    asString(alert.title) ||
    `${kind.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}`;
  const summary =
    asString(alert.summary) ||
    [
      triggeredBy ? `Triggered by ${triggeredBy}` : null,
      source ? `source ${source}` : null,
      origin ? `via ${origin}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
  const contextKey =
    asString(alert.context_key) || asString(evidencePayload.context_key);
  const taskEvidencePayload = hasTaskSpecificEvidence(evidenceCard)
    ? evidencePayload
    : {};

  const taskTitle =
    asString(alert.task_title) ||
    asString(taskEvidencePayload.task_title) ||
    (evidenceCard?.type === "task"
      ? asString(taskEvidencePayload.title)
      : null);
  const taskSummary =
    asString(alert.task_summary) ||
    asString(taskEvidencePayload.task_summary) ||
    (evidenceCard?.type === "task"
      ? asString(taskEvidencePayload.summary)
      : null);
  const taskId =
    asString(alert.task_id) || asString(taskEvidencePayload.task_id);
  const taskStatus =
    asString(alert.task_status) ||
    asString(taskEvidencePayload.task_status) ||
    (evidenceCard?.type === "task"
      ? asString(taskEvidencePayload.status)
      : null);
  const taskPriority =
    asString(alert.task_priority) ||
    asString(taskEvidencePayload.task_priority) ||
    (evidenceCard?.type === "task"
      ? asString(taskEvidencePayload.priority)
      : null);
  const taskAssignee =
    asRecord(alert.assignee) || asRecord(taskEvidencePayload.assignee);
  const taskDueAt =
    asString(alert.due_at) ||
    asString(taskEvidencePayload.due_at) ||
    asString(taskEvidencePayload.task_due_at);
  const toolCallId =
    asString(alert.tool_call_id) || asString(evidencePayload.tool_call_id);
  const resourceUri =
    asString(alert.resource_uri) || asString(evidencePayload.resource_uri);
  const toolName =
    asString(alert.tool_name) || asString(evidencePayload.tool_name);

  return {
    card_id: `alert:${entity.id || kind}:${source || "signal"}`,
    type: "alert",
    version: 1,
    payload: {
      title,
      summary:
        asString(alert.summary) || asString(evidencePayload.summary) || summary,
      alert,
      ...(contextKey ? { context_key: contextKey } : {}),
      ...(toolCallId ? { tool_call_id: toolCallId } : {}),
      ...(resourceUri ? { resource_uri: resourceUri } : {}),
      ...(toolName ? { tool_name: toolName } : {}),
      ...(taskTitle ? { task_title: taskTitle } : {}),
      ...(taskSummary ? { task_summary: taskSummary } : {}),
      ...(taskId ? { task_id: taskId } : {}),
      ...(taskStatus ? { task_status: taskStatus } : {}),
      ...(taskPriority ? { task_priority: taskPriority } : {}),
      ...(taskAssignee ? { assignee: taskAssignee } : {}),
      ...(taskDueAt ? { due_at: taskDueAt } : {}),
      sender_label: entity.display_name,
      sender_type: entity.sender_type,
      sender_id: entity.sender_id || entity.user_id,
      agent_id: entity.agent_id,
      created_at: entity.created_at,
      intent: "alert",
      evidence_mode: inferEvidenceMode(
        evidenceCard || {
          card_id: "alert",
          type: "alert",
          version: 1,
          payload: {
            ...alert,
            ...(contextKey ? { context_key: contextKey } : {}),
          },
        },
        alert,
      ),
    },
  };
}

function isDuplicateAlertResultCard(
  card: SpaceAgentCardEnvelope,
  alert: Record<string, unknown>,
) {
  if (card.type !== "result") return false;
  const payload = asRecord(card.payload) || {};
  const alertSource = asString(alert.source);
  const payloadSource = asString(payload.source);
  const alertSeverity = asString(alert.severity);
  const payloadSeverity = asString(payload.severity);

  return Boolean(
    alertSource &&
    payloadSource &&
    alertSource === payloadSource &&
    (!alertSeverity || !payloadSeverity || alertSeverity === payloadSeverity),
  );
}

function isDuplicateAlertEvidenceCard(
  card: SpaceAgentCardEnvelope,
  alert: Record<string, unknown>,
) {
  if (card.type === "alert") return false;
  if (isDuplicateAlertResultCard(card, alert)) return true;

  const payload = asRecord(card.payload) || {};
  const alertSource = asString(alert.source);
  const payloadSource = asString(payload.source);
  const alertContextKey = asString(alert.context_key);
  const payloadContextKey = asString(payload.context_key);
  const alertToolCallId = asString(alert.tool_call_id);
  const payloadToolCallId = asString(payload.tool_call_id);
  const alertTaskId = asString(alert.task_id);
  const payloadTaskId = asString(payload.task_id);
  const opensWidget =
    hasKey(payload, "resource_uri") ||
    hasKey(payload, "tool_name") ||
    Boolean(payloadContextKey) ||
    Boolean(payloadTaskId);

  if (!opensWidget) return false;
  if (alertContextKey && payloadContextKey) {
    return alertContextKey === payloadContextKey;
  }
  if (alertToolCallId && payloadToolCallId) {
    return alertToolCallId === payloadToolCallId;
  }
  if (alertTaskId && payloadTaskId) {
    return alertTaskId === payloadTaskId;
  }
  return Boolean(alertSource && payloadSource && alertSource === payloadSource);
}

function decorateCardsWithSignalContext(
  cards: SpaceAgentCardEnvelope[],
  entity: SurfaceContainer,
  alert: Record<string, unknown> | null,
) {
  const decoratedCards = cards.map((card) => ({
    ...card,
    payload: {
      ...card.payload,
      ...(alert ? { alert: card.payload.alert || alert } : {}),
      intent: inferCardIntent(card),
      evidence_mode: inferEvidenceMode(card, alert),
      sender_label: card.payload.sender_label || entity.display_name,
      sender_type: card.payload.sender_type || entity.sender_type,
      sender_id: card.payload.sender_id || entity.sender_id || entity.user_id,
      agent_id: card.payload.agent_id || entity.agent_id,
      created_at: card.payload.created_at || entity.created_at,
    },
  }));
  const duplicateAlertEvidenceCards = alert
    ? decoratedCards.filter((card) => isDuplicateAlertEvidenceCard(card, alert))
    : [];
  const visibleCards = alert
    ? decoratedCards.filter(
        (card) => !isDuplicateAlertEvidenceCard(card, alert),
      )
    : decoratedCards;
  const hasAlertCard = visibleCards.some((card) => card.type === "alert");
  return alert && !hasAlertCard
    ? [
        buildAlertCard(entity, alert, duplicateAlertEvidenceCards[0]),
        ...visibleCards,
      ]
    : visibleCards;
}

type RawWidgetDescriptor = SpaceAgentWidgetDescriptor & {
  arguments?: Record<string, unknown>;
  initial_data?: Record<string, unknown>;
};

function normalizeWidgetFields(
  raw: RawWidgetDescriptor,
): SpaceAgentWidgetDescriptor {
  // Normalize backend field names to frontend conventions:
  // backend sends "arguments", frontend expects "tool_input"
  // backend sends "initial_data", frontend can use as "tool_result"
  if (raw.arguments && !raw.tool_input) {
    raw.tool_input = raw.arguments;
  }
  if (raw.initial_data && !raw.tool_result) {
    raw.tool_result = raw.initial_data;
  }
  normalizeDraftInitialData(raw);
  return raw;
}

function normalizeDraftInitialData(raw: RawWidgetDescriptor) {
  const initialData = asRecord(raw.initial_data) || asRecord(raw.tool_result);
  if (!initialData) return;

  const data = asRecord(initialData.data);
  if (data?.scope === "create" && data.draft) return;

  const action = asString(initialData.action ?? raw.tool_action);
  const draft = asRecord(initialData.draft);
  if (action !== "create_draft" || !draft) return;

  const toolName = asString(raw.tool_name);
  const resourceUri = asString(raw.resource_uri);
  const isAgentDraft =
    toolName === "agents" || Boolean(resourceUri?.startsWith("ui://agents/"));
  const isSpaceDraft =
    toolName === "spaces" || Boolean(resourceUri?.startsWith("ui://spaces/"));
  if (!isAgentDraft && !isSpaceDraft) return;

  const normalized = {
    kind: isSpaceDraft ? "space_collection" : "agent_collection",
    version: initialData.version || 2,
    state: initialData.state || "ready",
    data: {
      scope: "create",
      draft,
      required_fields:
        initialData.required_fields ||
        (isSpaceDraft ? ["name"] : ["name", "description", "agent_mode"]),
      hint:
        initialData.hint ||
        `Review this draft before creating the ${isSpaceDraft ? "space" : "agent"}.`,
    },
  };

  raw.initial_data = normalized;
  raw.tool_result = normalized;
}

function getWidgets(entity: SurfaceContainer): SpaceAgentWidgetDescriptor[] {
  const raw =
    entity.ui?.widget ||
    entity.metadata?.ui?.widget ||
    entity.message_metadata?.ui?.widget ||
    null;
  if (!raw) return [];

  const rawWidget = raw as RawWidgetDescriptor;

  // If the backend sent a widgets[] array (multi-tool turns), expand each
  // entry into its own descriptor. Otherwise fall back to the single widget.
  const rawWidgets = rawWidget.widgets;
  if (Array.isArray(rawWidgets) && rawWidgets.length > 0) {
    return rawWidgets.map((entry) => {
      const rawEntry = entry as RawWidgetDescriptor;
      const isPrimaryWidget =
        !rawEntry.tool_call_id ||
        !rawWidget.tool_call_id ||
        rawEntry.tool_call_id === rawWidget.tool_call_id;
      const w: RawWidgetDescriptor = {
        ...rawEntry,
        // Inherit top-level fields the individual entry may lack. Some
        // backend payloads keep durable initial_data on the primary widget
        // while widgets[] carries the renderable resource descriptor.
        tool_call_id:
          rawEntry.tool_call_id ||
          (isPrimaryWidget ? rawWidget.tool_call_id : undefined),
        resource_uri:
          rawEntry.resource_uri ||
          (isPrimaryWidget ? rawWidget.resource_uri : undefined),
        lifecycle: rawEntry.lifecycle || raw.lifecycle,
        title: rawEntry.title || raw.title,
        arguments:
          rawEntry.arguments ||
          (isPrimaryWidget ? rawWidget.arguments : undefined),
        initial_data:
          rawEntry.initial_data ||
          (isPrimaryWidget ? rawWidget.initial_data : undefined),
        tool_input:
          rawEntry.tool_input ||
          (isPrimaryWidget ? rawWidget.tool_input : undefined),
        tool_result:
          rawEntry.tool_result ||
          (isPrimaryWidget ? rawWidget.tool_result : undefined),
      };
      return normalizeWidgetFields(w);
    });
  }

  return [normalizeWidgetFields(rawWidget)];
}

function getWidgetStatus(
  widget: SpaceAgentWidgetDescriptor,
): "pending" | "ready" | "error" {
  const lifecycle = (widget.lifecycle || "").toLowerCase();
  if (lifecycle === "error") return "error";
  if (lifecycle === "complete") return "ready";
  return "pending";
}

export function normalizeSpaceAgentSurfaces(
  entity: SurfaceContainer,
  sourceMessageId?: string,
  hiddenWidgets?: Set<string>,
): SpaceAgentNormalizedSurface[] {
  const surfaces: SpaceAgentNormalizedSurface[] = [];
  const alert = getAlertMetadata(entity);
  const cards = decorateCardsWithSignalContext(getCards(entity), entity, alert);
  const widgets = getWidgets(entity);
  const cardPolicy = getSpaceAgentCardSurfacePolicy(cards);

  if (cardPolicy.enabled && cards.length > 0) {
    surfaces.push({
      id: `cards:${cards.map((card) => card.card_id).join(",")}`,
      sourceMessageId,
      policyId: "cards",
      workflowIds: [],
      kind: "cards",
      placement: "card",
      cards,
      status: "ready",
    });
  }

  for (let widgetIndex = 0; widgetIndex < widgets.length; widgetIndex++) {
    const widget = widgets[widgetIndex];
    const widgetPolicy = resolveSpaceAgentSurfacePolicy(widget, hiddenWidgets);

    if (!widgetPolicy.enabled || widgetPolicy.placement === "none") {
      continue;
    }

    // Use tool_call_id when available (unique per call). Fall back to
    // tool_name + index to prevent ID collisions when the same tool is
    // called multiple times in one turn (e.g. 5 createTask calls).
    const baseId =
      widget.tool_call_id ||
      widget.resource_uri ||
      widget.tool_name ||
      "widget";
    const widgetId = widget.tool_call_id ? baseId : `${baseId}:${widgetIndex}`;

    surfaces.push({
      id: widgetId,
      sourceMessageId,
      policyId: getSpaceAgentSurfacePolicyId(widget),
      workflowIds: resolveSpaceAgentWorkflowIds(widget),
      kind: "widget",
      placement: widgetPolicy.placement,
      widget,
      status: getWidgetStatus(widget),
    });
  }

  return surfaces;
}

export function getSurfaceCount(surfaces: SpaceAgentNormalizedSurface[]) {
  return surfaces.reduce((count, surface) => {
    if (surface.kind === "cards") return count + surface.cards.length;
    if (surface.placement === "none") return count;
    return count + 1;
  }, 0);
}
