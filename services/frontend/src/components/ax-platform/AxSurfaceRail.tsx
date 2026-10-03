import { AxMcpAppWidget } from "@/components/ax-platform/AxMcpAppWidget";
import { AxMessageWidgets } from "@/components/ax-platform/AxMessageWidgets";
import type { PendingResponseState } from "@/components/ax-platform/shell/pending-response";
import type { SpaceAgentCardEnvelope } from "@/lib/space-agent-api";
import { type SpaceAgentNormalizedSurface } from "@/lib/space-agent-surfaces";
import {
  getCollapsedWidgetPreview,
  normalizeWidgetNoticeText,
} from "@/components/ax-platform/widget-fold";
import { formatTimestamp } from "@/components/ax-platform/shell/transcript-model";
import { cn } from "@/lib/utils";
import {
  ACTIVITY_STREAM_READY_BADGE_CLASSNAME,
  ACTIVITY_STREAM_STATUS_ERROR_CLASSNAME,
  ACTIVITY_STREAM_STATUS_PENDING_CLASSNAME,
} from "@/components/ax-platform/activity-stream-tokens";
import {
  AlertTriangle,
  ClipboardCheck,
  Loader2,
  Share2,
  Wrench,
} from "lucide-react";

export type AxSurfaceActionInput = {
  messageId: string;
  actionId: string;
  cardId: string;
  choiceId?: string;
  freeText?: string | null;
};

type WidgetSurface = Extract<SpaceAgentNormalizedSurface, { kind: "widget" }>;
type CardSurface = Extract<SpaceAgentNormalizedSurface, { kind: "cards" }>;
type LinkedWidgetSurfacesByCardId = Partial<Record<string, WidgetSurface>>;

export type AxWidgetPanelOpenInput = {
  messageId: string;
  surface: WidgetSurface;
  timestamp?: string | null;
};

// Task 48ae545f — Share seeds a composer context bar using the existing
// metadata.forward API contract. AxPlatformShell owns the receiving state;
// AxSurfaceRail just passes the handler through.
export type AxForwardInitInput = {
  messageId: string;
  cardId: string;
  card: import("@/lib/space-agent-api").SpaceAgentCardEnvelope;
};

type AxSurfaceRailProps = {
  messageId: string;
  spaceId?: string | null;
  surfaces: SpaceAgentNormalizedSurface[];
  activeActionKey?: string | null;
  onAction: (input: AxSurfaceActionInput) => void;
  onOpenWidgetPanel?: (input: AxWidgetPanelOpenInput) => void;
  onForwardInit?: (input: AxForwardInitInput) => void;
  onReplyInit?: (input: AxForwardInitInput) => void;
  timestamp?: string | null;
  widgetOnClose?: () => void;
  forceMountWidgets?: boolean;
  pendingResponsesBySourceId?: Record<string, PendingResponseState>;
};

function getWidgetSignalTitle(surface: WidgetSurface) {
  return (
    surface.widget.title ||
    surface.policyId ||
    surface.widget.tool_name ||
    "MCP app"
  );
}

function getWidgetSignalStateLabel(surface: WidgetSurface) {
  if (isReviewSignal(surface)) return "Needs review";
  if (surface.status === "error") return "Needs attention";
  if (surface.status === "pending") return "Working";
  return "Ready";
}

const REVIEW_SIGNAL_KEY_TOKENS = ["approval_required", "under_review"];

const REVIEW_SIGNAL_TEXT_TOKENS = [
  "approval required",
  "human in the loop",
  "human-in-the-loop",
  "human review",
  "needs review",
  "under_review",
];

const REVIEW_WORKFLOW_IDS = new Set(["agents.create", "spaces.create"]);
const REVIEW_TOOL_NAMES = new Set([
  "agents",
  "agents.create",
  "spaces",
  "spaces.create",
]);
const REVIEW_DRAFT_ACTIONS = new Set([
  "approve_draft",
  "cancel_draft",
  "create_draft",
  "dismiss_draft",
  "edit_draft",
  "get_draft",
  "reject_draft",
  "update_draft",
]);

function includesAnyToken(value: string | null | undefined, tokens: string[]) {
  const normalized = (value || "").toLowerCase();
  return tokens.some((token) => normalized.includes(token));
}

function keyIncludesReviewSignal(value?: string | null) {
  return includesAnyToken(value, REVIEW_SIGNAL_KEY_TOKENS);
}

function textIncludesReviewSignal(value?: string | null) {
  return includesAnyToken(value, REVIEW_SIGNAL_TEXT_TOKENS);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown) {
  return typeof value === "string" ? value.toLowerCase() : "";
}

function asDisplayString(value: unknown) {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function asExactString(value: unknown) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function firstExactString(
  record: Record<string, unknown> | null,
  keys: string[],
) {
  for (const key of keys) {
    const value = asExactString(record?.[key]);
    if (value) return value;
  }
  return null;
}

function firstDisplayString(
  record: Record<string, unknown> | null,
  keys: string[],
) {
  for (const key of keys) {
    const value = asDisplayString(record?.[key]);
    if (value) return value;
  }
  return null;
}

function namesMatch(cardToolName: string, widgetToolName: string) {
  const cardName = cardToolName.toLowerCase();
  const widgetName = widgetToolName.toLowerCase();
  return (
    cardName === widgetName ||
    widgetName.startsWith(`${cardName}.`) ||
    widgetName.startsWith(`${cardName}:`)
  );
}

function getWidgetResultData(surface: WidgetSurface) {
  const result =
    asRecord(surface.widget.tool_result) ||
    asRecord(surface.widget.initial_data) ||
    null;
  const structured =
    asRecord(result?.structuredContent) ||
    asRecord(result?.structured_content) ||
    result;
  return asRecord(structured?.data) || structured;
}

type TaskSignalFact = {
  label: string;
  value: string;
};

function getTaskSignalRecord(surface: WidgetSurface) {
  const resultData = getWidgetResultData(surface);
  const task =
    asRecord(resultData?.task) ||
    asRecord(resultData?.item) ||
    (Array.isArray(resultData?.items) ? asRecord(resultData.items[0]) : null) ||
    resultData;
  return task || resultData;
}

function getNamedActor(record: Record<string, unknown> | null, keys: string[]) {
  for (const key of keys) {
    const direct = firstDisplayString(record, [key]);
    if (direct) return direct;
    const nested = asRecord(record?.[key]);
    const nestedName = firstDisplayString(nested, [
      "display_name",
      "name",
      "handle",
      "username",
      "email",
      "id",
    ]);
    if (nestedName) return nestedName;
  }
  return null;
}

function getReminderCadence(
  task: Record<string, unknown> | null,
  resultData: Record<string, unknown> | null,
) {
  const policy =
    asRecord(task?.reminder_policy) ||
    asRecord(task?.reminder) ||
    asRecord(resultData?.reminder_policy) ||
    asRecord(resultData?.reminder);
  const intervalMinutes =
    firstDisplayString(policy, ["interval_minutes", "every_minutes"]) ||
    firstDisplayString(task, ["reminder_interval_minutes"]) ||
    firstDisplayString(resultData, ["reminder_interval_minutes"]);
  if (intervalMinutes) return `every ${intervalMinutes}m`;
  return (
    firstDisplayString(policy, [
      "cadence",
      "frequency",
      "interval",
      "repeat",
    ]) ||
    firstDisplayString(task, [
      "reminder_cadence",
      "reminder_frequency",
      "reminder_repeat",
    ]) ||
    firstDisplayString(resultData, [
      "reminder_cadence",
      "reminder_frequency",
      "reminder_repeat",
    ])
  );
}

function getCreatedAgo(
  task: Record<string, unknown> | null,
  resultData: Record<string, unknown> | null,
) {
  const raw =
    firstDisplayString(task, ["created_at", "createdAt", "created"]) ||
    firstDisplayString(resultData, ["created_at", "createdAt", "created"]);
  return formatTimestamp(raw) || raw;
}

function getTaskSignalFacts(surface: WidgetSurface): TaskSignalFact[] {
  const toolName = (
    surface.widget.tool_name ||
    surface.policyId ||
    ""
  ).toLowerCase();
  const isTaskSurface =
    toolName === "tasks" ||
    toolName.startsWith("tasks.") ||
    toolName.startsWith("tasks:");
  if (!isTaskSurface) return [];

  const resultData = getWidgetResultData(surface);
  const task = getTaskSignalRecord(surface);
  const facts: TaskSignalFact[] = [];
  const push = (label: string, value?: string | null) => {
    const normalized = value?.trim();
    if (!normalized) return;
    if (facts.some((fact) => fact.label === label)) return;
    facts.push({ label, value: normalized });
  };

  push(
    "Status",
    firstDisplayString(task, ["status", "state", "lifecycle"]) ||
      firstDisplayString(resultData, ["status", "state", "lifecycle"]),
  );

  const assignee = getNamedActor(task, [
    "assignee",
    "assigned_to",
    "worker",
    "owner_agent",
  ]);
  push("Assigned", assignee || "Unassigned");

  push("Reminder", getReminderCadence(task, resultData) || "No reminder");

  const reminderPolicy =
    asRecord(task?.reminder_policy) ||
    asRecord(task?.reminder) ||
    asRecord(resultData?.reminder_policy) ||
    asRecord(resultData?.reminder);
  push(
    "Next",
    firstDisplayString(reminderPolicy, [
      "next_fire_at",
      "next_run_at",
      "next_at",
      "next",
    ]) ||
      firstDisplayString(task, [
        "next_reminder_at",
        "reminder_next_fire_at",
        "next_fire_at",
      ]) ||
      firstDisplayString(resultData, [
        "next_reminder_at",
        "reminder_next_fire_at",
        "next_fire_at",
      ]),
  );
  if (!facts.some((fact) => fact.label === "Next")) {
    push(
      "Due",
      firstDisplayString(task, ["due_at", "deadline", "due"]) ||
        firstDisplayString(resultData, ["due_at", "deadline", "due"]),
    );
  }

  push("Created", getCreatedAgo(task, resultData));

  push(
    "Creator",
    getNamedActor(task, ["creator", "created_by", "created_by_user"]) ||
      getNamedActor(resultData, ["creator", "created_by", "created_by_user"]),
  );

  const wake =
    asRecord(resultData?.wake_up) ||
    asRecord(resultData?.notification) ||
    asRecord(task?.wake_up) ||
    asRecord(task?.notification);
  push(
    "Wake-up",
    firstDisplayString(wake, ["status", "state"]) ||
      firstDisplayString(resultData, [
        "wake_up_status",
        "notification_status",
        "nudge_status",
      ]),
  );
  if (!facts.some((fact) => fact.label === "Wake-up")) {
    push(
      "Notified",
      getNamedActor(wake, ["target", "recipient"]) ||
        getNamedActor(resultData, ["notified", "notification_target"]),
    );
  }

  return facts.slice(0, 7);
}

function widgetHasContextKey(surface: WidgetSurface, contextKey: string) {
  const input = asRecord(surface.widget.tool_input);
  const args = asRecord(surface.widget.arguments);
  const resultData = getWidgetResultData(surface);
  return [
    firstExactString(input, ["context_key", "key", "selected_key"]),
    firstExactString(args, ["context_key", "key", "selected_key"]),
    firstExactString(resultData, ["context_key", "key", "selected_key"]),
  ].some((value) => value === contextKey);
}

function widgetHasTaskId(surface: WidgetSurface, taskId: string) {
  const input = asRecord(surface.widget.tool_input);
  const args = asRecord(surface.widget.arguments);
  const resultData = getWidgetResultData(surface);
  return [
    firstExactString(input, ["task_id", "taskId", "selected_task_id", "id"]),
    firstExactString(args, ["task_id", "taskId", "selected_task_id", "id"]),
    firstExactString(resultData, [
      "task_id",
      "taskId",
      "selected_task_id",
      "id",
    ]),
  ].some((value) => value === taskId);
}

function cardMatchesWidgetSurface(
  card: CardSurface["cards"][number],
  surface: WidgetSurface,
) {
  const payload = asRecord(card.payload);
  const toolCallId = firstExactString(payload, ["tool_call_id", "toolCallId"]);
  if (toolCallId && toolCallId === surface.widget.tool_call_id) return true;

  const resourceUri = firstExactString(payload, [
    "resource_uri",
    "widget_resource_uri",
  ]);
  if (resourceUri && resourceUri === surface.widget.resource_uri) return true;

  const contextKey = firstExactString(payload, [
    "context_key",
    "key",
    "selected_key",
  ]);
  if (contextKey && widgetHasContextKey(surface, contextKey)) return true;

  const taskId = firstExactString(payload, ["task_id", "taskId", "id"]);
  if (taskId && widgetHasTaskId(surface, taskId)) return true;

  const toolName = firstExactString(payload, ["tool_name", "tool"]);
  if (
    toolName &&
    surface.widget.tool_name &&
    namesMatch(toolName, surface.widget.tool_name)
  ) {
    return true;
  }

  return false;
}

function buildLinkedWidgetSurfacesByCardId(
  cards: CardSurface["cards"],
  panelWidgetSurfaces: WidgetSurface[],
): LinkedWidgetSurfacesByCardId {
  const linkedSurfaces: LinkedWidgetSurfacesByCardId = {};
  if (!cards.length || !panelWidgetSurfaces.length) return linkedSurfaces;

  const claimedWidgetIds = new Set<string>();
  const singleWidgetFallback =
    panelWidgetSurfaces.length === 1 ? panelWidgetSurfaces[0] : null;

  cards.forEach((card, index) => {
    const matched = panelWidgetSurfaces.find(
      (surface) =>
        !claimedWidgetIds.has(surface.id) &&
        cardMatchesWidgetSurface(card, surface),
    );
    const orderedFallbackCandidate =
      panelWidgetSurfaces.length === cards.length
        ? panelWidgetSurfaces[index]
        : null;
    const orderedFallback =
      orderedFallbackCandidate &&
      !claimedWidgetIds.has(orderedFallbackCandidate.id)
        ? orderedFallbackCandidate
        : panelWidgetSurfaces.find(
            (surface) => !claimedWidgetIds.has(surface.id),
          ) || null;
    const surface = matched || singleWidgetFallback || orderedFallback || null;
    if (!surface) return;

    linkedSurfaces[card.card_id] = surface;
    if (!singleWidgetFallback) claimedWidgetIds.add(surface.id);
  });

  return linkedSurfaces;
}

function recordHasReviewSignal(value: unknown): boolean {
  const record = asRecord(value);
  if (!record) return false;

  const structured =
    asRecord(record.structuredContent) ||
    asRecord(record.structured_content) ||
    record;
  const payload = asRecord(structured.data) || structured;
  const scope = asString(payload.scope ?? structured.scope);
  const state = asString(
    payload.state ??
      payload.status ??
      payload.lifecycle ??
      structured.state ??
      structured.status,
  );
  const draft = asRecord(payload.draft ?? structured.draft);

  if ((scope === "create" || scope === "draft") && draft) return true;
  if (state === "under_review" || state === "approval_required") return true;
  if (scope === "under_review" || scope === "approval_required") return true;

  for (const key of Object.keys(record)) {
    if (keyIncludesReviewSignal(key)) return true;
  }

  return false;
}

function isReviewSignal(surface: WidgetSurface) {
  const widget = surface.widget;
  const lifecycle = (widget.lifecycle || "").toLowerCase();
  if (lifecycle === "approval_required") return true;
  if (
    surface.workflowIds?.some((id) => REVIEW_WORKFLOW_IDS.has(id.toLowerCase()))
  ) {
    return true;
  }

  const toolName = (widget.tool_name || surface.policyId || "").toLowerCase();
  const toolAction = (widget.tool_action || "").toLowerCase();
  if (REVIEW_TOOL_NAMES.has(toolName) && REVIEW_DRAFT_ACTIONS.has(toolAction)) {
    return true;
  }

  return Boolean(
    textIncludesReviewSignal(widget.fallback_text) ||
    recordHasReviewSignal(widget.structured_content) ||
    recordHasReviewSignal(widget.tool_result),
  );
}

function getReviewSignalPreview(surface: WidgetSurface) {
  const widget = surface.widget;
  const fallback = normalizeWidgetNoticeText(widget.fallback_text);
  if (fallback) return fallback;

  const preview = getCollapsedWidgetPreview(
    widget,
    getWidgetSignalTitle(surface),
  );
  return preview === "Open for details."
    ? "Human review required. Open to inspect, edit, approve, or dismiss."
    : preview;
}

function buildWidgetSignalForwardCard({
  surface,
  title,
  preview,
  stateLabel,
  toolName,
}: {
  surface: WidgetSurface;
  title: string;
  preview: string;
  stateLabel: string;
  toolName: string;
}): SpaceAgentCardEnvelope {
  const widget = surface.widget;
  return {
    card_id: `widget:${surface.id}`,
    type: "result",
    version: 1,
    payload: {
      title,
      summary: preview,
      status: stateLabel.toLowerCase().replace(/\s+/g, "_"),
      source: "mcp_widget_signal",
      tool_name: toolName,
      ...(widget.tool_action ? { tool_action: widget.tool_action } : {}),
      ...(widget.tool_call_id ? { tool_call_id: widget.tool_call_id } : {}),
      ...(widget.resource_uri ? { resource_uri: widget.resource_uri } : {}),
      ...(widget.lifecycle ? { lifecycle: widget.lifecycle } : {}),
    },
  };
}

function AxMcpAppSignalCard({
  messageId,
  surface,
  timestamp,
  onOpen,
  onForwardInit,
}: {
  messageId: string;
  surface: WidgetSurface;
  timestamp?: string | null;
  onOpen: (input: AxWidgetPanelOpenInput) => void;
  onForwardInit?: (input: AxForwardInitInput) => void;
}) {
  const title = getWidgetSignalTitle(surface);
  const stateLabel = getWidgetSignalStateLabel(surface);
  const isReview = isReviewSignal(surface);
  const preview = isReview
    ? getReviewSignalPreview(surface)
    : getCollapsedWidgetPreview(surface.widget, title);
  const toolName = surface.widget.tool_name || surface.policyId || "tool";
  const isError = surface.status === "error";
  const isPending = surface.status === "pending";
  const openSignal = () => onOpen({ messageId, surface, timestamp });
  const taskFacts = getTaskSignalFacts(surface);
  const forwardCard = buildWidgetSignalForwardCard({
    surface,
    title,
    preview,
    stateLabel,
    toolName,
  });

  return (
    <section
      data-testid="ax-mcp-app-signal"
      role="button"
      tabIndex={0}
      aria-label={`Open ${title}`}
      onClick={openSignal}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        openSignal();
      }}
      className={cn(
        "group mt-3 min-h-[7rem] w-full cursor-pointer rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-left text-slate-900 shadow-[0_18px_42px_-30px_rgba(8,47,73,0.75)] outline-none transition focus-visible:ring-2 focus-visible:ring-cyan-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:border-cyan-300/18 dark:bg-slate-950/70 dark:text-white dark:focus-visible:ring-cyan-300/60 dark:focus-visible:ring-offset-slate-950",
        isReview
          ? "border-amber-300/45 bg-amber-50/85 hover:border-amber-300 hover:bg-amber-100/75 dark:border-amber-200/35 dark:bg-[radial-gradient(circle_at_0%_0%,rgba(251,191,36,0.13),transparent_40%),rgba(15,23,42,0.76)] dark:shadow-[0_18px_48px_-36px_rgba(251,191,36,0.7)] dark:hover:border-amber-100/55 dark:hover:bg-amber-300/[0.08]"
          : isError
            ? "border-rose-300/45 bg-rose-50/80 hover:border-rose-300 hover:bg-rose-100/70 dark:border-rose-300/25 dark:bg-slate-950/75 dark:hover:border-rose-200/45"
            : "border-cyan-300/25 bg-slate-50 hover:border-cyan-300/55 hover:bg-white dark:border-cyan-300/20 dark:bg-slate-950/70 dark:hover:border-cyan-200/45 dark:hover:bg-slate-900/85",
      )}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border",
            isReview
              ? ACTIVITY_STREAM_STATUS_PENDING_CLASSNAME
              : isError
                ? ACTIVITY_STREAM_STATUS_ERROR_CLASSNAME
                : ACTIVITY_STREAM_READY_BADGE_CLASSNAME,
          )}
        >
          {isReview ? (
            <ClipboardCheck className="h-4 w-4" />
          ) : isError ? (
            <AlertTriangle className="h-4 w-4" />
          ) : isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Wrench className="h-4 w-4" />
          )}
        </span>
        <span className="min-w-0 flex-1 pr-11">
          <span className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-semibold text-slate-900 dark:text-slate-50">
              {title}
            </span>
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-[0.16em]",
                isReview
                  ? ACTIVITY_STREAM_STATUS_PENDING_CLASSNAME
                  : isError
                    ? ACTIVITY_STREAM_STATUS_ERROR_CLASSNAME
                    : ACTIVITY_STREAM_READY_BADGE_CLASSNAME,
              )}
            >
              {stateLabel}
            </span>
          </span>
          <span className="mt-2 block text-sm leading-6 text-slate-700 dark:text-slate-300">
            {preview}
          </span>
          {taskFacts.length ? (
            <span className="mt-3 flex flex-wrap gap-2">
              {taskFacts.map((fact) => (
                <span
                  key={`${fact.label}-${fact.value}`}
                  className="inline-flex min-w-0 max-w-full items-center gap-1 rounded-full border border-slate-200 bg-slate-100/80 px-2.5 py-1 text-[11px] text-slate-700 shadow-[inset_0_1px_0_rgba(255,255,255,0.75)] dark:border-white/10 dark:bg-slate-950/55 dark:text-slate-300 dark:shadow-none"
                >
                  {fact.label === "Assigned" && fact.value === "Unassigned" ? (
                    <span className="font-medium text-slate-800 dark:text-slate-100">
                      Unassigned
                    </span>
                  ) : (
                    <>
                      <span className="text-slate-500 dark:text-slate-400">
                        {fact.label}:
                      </span>
                      <span aria-hidden="true"> </span>
                      <span className="min-w-0 font-medium text-slate-900 [overflow-wrap:anywhere] dark:text-slate-100">
                        {fact.value}
                      </span>
                    </>
                  )}
                </span>
              ))}
            </span>
          ) : null}
        </span>
        {onForwardInit ? (
          <button
            type="button"
            data-testid="ax-mcp-app-signal-share"
            data-card-interactive="true"
            className="ml-auto inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-slate-50 text-slate-600 transition hover:border-slate-300 hover:bg-white hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/60 dark:border-white/10 dark:bg-white/[0.06] dark:text-white/70 dark:hover:border-white/20 dark:hover:text-white sm:h-8 sm:w-8"
            aria-label="Share"
            title="Share or ask about this"
            onClick={(event) => {
              event.stopPropagation();
              onForwardInit({
                messageId,
                cardId: forwardCard.card_id,
                card: forwardCard,
              });
            }}
          >
            <Share2 className="h-4 w-4 sm:h-3.5 sm:w-3.5" />
          </button>
        ) : null}
      </div>
    </section>
  );
}

export function AxSurfaceRail({
  messageId,
  spaceId,
  surfaces,
  activeActionKey,
  onAction,
  onOpenWidgetPanel,
  onForwardInit,
  onReplyInit,
  timestamp,
  widgetOnClose,
  forceMountWidgets,
  pendingResponsesBySourceId,
}: AxSurfaceRailProps) {
  if (!surfaces.length) return null;

  const panelWidgetSurfaces = surfaces.filter(
    (surface): surface is WidgetSurface =>
      surface.kind === "widget" && surface.placement !== "none",
  );
  const shouldLinkWidgetToCards = Boolean(
    onOpenWidgetPanel &&
    panelWidgetSurfaces.length &&
    surfaces.some((surface) => surface.kind === "cards"),
  );
  const linkedWidgetSurfacesBySurfaceId = new Map<
    string,
    LinkedWidgetSurfacesByCardId
  >();
  const linkedWidgetSurfaceIds = new Set<string>();

  if (shouldLinkWidgetToCards) {
    surfaces.forEach((surface) => {
      if (surface.kind !== "cards") return;
      const linkedSurfaces = buildLinkedWidgetSurfacesByCardId(
        surface.cards,
        panelWidgetSurfaces,
      );
      linkedWidgetSurfacesBySurfaceId.set(surface.id, linkedSurfaces);
      Object.values(linkedSurfaces).forEach((linkedSurface) => {
        if (linkedSurface) linkedWidgetSurfaceIds.add(linkedSurface.id);
      });
    });
  }

  return (
    <>
      {surfaces.map((surface, index) => {
        const surfaceMessageId = surface.sourceMessageId || messageId;

        if (surface.kind === "cards") {
          const linkedWidgetSurfacesByCardId =
            linkedWidgetSurfacesBySurfaceId.get(surface.id);
          return (
            <AxMessageWidgets
              key={`${messageId}-surface-cards-${index}`}
              cards={surface.cards}
              messageId={surfaceMessageId}
              activeActionKey={activeActionKey}
              linkedWidgetSurfacesByCardId={linkedWidgetSurfacesByCardId}
              pendingResponsesBySourceId={pendingResponsesBySourceId}
              timestamp={timestamp}
              onOpenWidgetPanel={onOpenWidgetPanel}
              onForwardInit={
                onForwardInit
                  ? (input) =>
                      onForwardInit({
                        ...input,
                        messageId: surfaceMessageId,
                      })
                  : undefined
              }
              onReplyInit={
                onReplyInit
                  ? (input) =>
                      onReplyInit({
                        ...input,
                        messageId: surfaceMessageId,
                      })
                  : undefined
              }
              onAction={(input) =>
                onAction({
                  ...input,
                  messageId: surfaceMessageId,
                })
              }
            />
          );
        }

        if (!spaceId || surface.placement === "none") return null;

        if (shouldLinkWidgetToCards && linkedWidgetSurfaceIds.has(surface.id)) {
          return null;
        }

        if (onOpenWidgetPanel) {
          return (
            <AxMcpAppSignalCard
              key={`${messageId}-surface-widget-signal-${index}-${surface.id}`}
              messageId={surfaceMessageId}
              surface={surface}
              timestamp={timestamp}
              onOpen={onOpenWidgetPanel}
              onForwardInit={onForwardInit}
            />
          );
        }

        return (
          <AxMcpAppWidget
            key={`${messageId}-surface-widget-${index}-${surface.id}`}
            messageId={surfaceMessageId}
            spaceId={spaceId}
            widget={surface.widget}
            timestamp={timestamp}
            forceMount={forceMountWidgets}
            onClose={widgetOnClose}
          />
        );
      })}
    </>
  );
}
