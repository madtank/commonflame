import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { copyToClipboard } from "@/lib/clipboard-utils";
import { cn } from "@/lib/utils";
import {
  type SpaceAgentCardEnvelope,
  type SpaceAgentCardType,
} from "@/lib/space-agent-api";
import {
  getPendingResponseDisplay,
  type PendingResponseDisplay,
  type PendingResponseState,
} from "@/components/ax-platform/shell/pending-response";
import type { SpaceAgentNormalizedSurface } from "@/lib/space-agent-surfaces";
import {
  formatAbsoluteTimestamp,
  formatTimestamp,
} from "@/components/ax-platform/shell/transcript-model";
import { ExternalMarkdownLink } from "@/components/ui/external-markdown-link";
import {
  ACTIVITY_STREAM_NEUTRAL_CHIP_CLASSNAME,
  ACTIVITY_STREAM_NEUTRAL_LABEL_CLASSNAME,
  ACTIVITY_STREAM_NEUTRAL_VALUE_CLASSNAME,
  ACTIVITY_STREAM_RESULT_BADGE_CLASSNAME,
  ACTIVITY_STREAM_TASK_BADGE_CLASSNAME,
} from "@/components/ax-platform/activity-stream-tokens";
import {
  Activity,
  Bell,
  BriefcaseBusiness,
  CheckCircle2,
  CircleDashed,
  Copy,
  Clock3,
  ExternalLink,
  FileText,
  Home,
  Info,
  MessageSquareShare,
  Orbit,
  Search,
  Share2,
  ShieldAlert,
  CornerUpLeft,
  TriangleAlert,
} from "lucide-react";

type CardActionInput = {
  actionId: string;
  cardId: string;
  choiceId?: string;
  freeText?: string | null;
};

type WidgetSurface = Extract<SpaceAgentNormalizedSurface, { kind: "widget" }>;

type AxMessageWidgetsProps = {
  cards: SpaceAgentCardEnvelope[];
  messageId: string;
  activeActionKey?: string | null;
  onAction: (input: CardActionInput) => void;
  linkedWidgetSurface?: WidgetSurface | null;
  linkedWidgetSurfacesByCardId?: Partial<Record<string, WidgetSurface>>;
  onOpenWidgetPanel?: (input: {
    messageId: string;
    surface: WidgetSurface;
    timestamp?: string | null;
  }) => void;
  // Task 48ae545f — Share mirrors Reply: the card Share button seeds a
  // "Sharing: …" context bar in the main composer using the existing
  // metadata.forward API contract.
  onForwardInit?: (input: {
    cardId: string;
    card: SpaceAgentCardEnvelope;
    messageId: string;
  }) => void;
  onReplyInit?: (input: {
    cardId: string;
    card: SpaceAgentCardEnvelope;
    messageId: string;
  }) => void;
  timestamp?: string | null;
  pendingResponsesBySourceId?: Record<string, PendingResponseState>;
};

type CardChrome = {
  surfaceClassName: string;
  badgeClassName: string;
  icon: typeof FileText;
  label: string;
};

type SignalFact = {
  label: string;
  value: string;
  title?: string;
  dateTime?: string;
};

type CardIntent = "alert" | "signal" | "context" | "review";

const NEUTRAL_CHIP_CLASSNAME = ACTIVITY_STREAM_NEUTRAL_CHIP_CLASSNAME;
const NEUTRAL_CHIP_LABEL_CLASSNAME = ACTIVITY_STREAM_NEUTRAL_LABEL_CLASSNAME;
const NEUTRAL_CHIP_VALUE_CLASSNAME = ACTIVITY_STREAM_NEUTRAL_VALUE_CLASSNAME;
const TASK_BADGE_CLASSNAME = ACTIVITY_STREAM_TASK_BADGE_CLASSNAME;
const AGENT_BADGE_CLASSNAME =
  "border-amber-300/40 bg-amber-500/10 text-amber-800 dark:border-amber-300/30 dark:bg-amber-400/12 dark:text-amber-100";
const ALERT_BADGE_CLASSNAME =
  "border-sky-300/40 bg-sky-500/10 text-sky-800 dark:border-sky-300/30 dark:bg-sky-400/12 dark:text-sky-100";
const REVIEW_BADGE_CLASSNAME =
  "border-orange-300/40 bg-orange-500/10 text-orange-800 dark:border-orange-300/30 dark:bg-orange-400/12 dark:text-orange-100";
const NOTICE_BADGE_CLASSNAME =
  "border-lime-300/40 bg-lime-500/10 text-lime-800 dark:border-lime-300/30 dark:bg-lime-400/12 dark:text-lime-100";
const ATTENTION_BADGE_CLASSNAME =
  "border-amber-300/40 bg-amber-500/10 text-amber-800 dark:border-amber-300/30 dark:bg-amber-400/12 dark:text-amber-100";
const DANGER_BADGE_CLASSNAME =
  "border-rose-300/40 bg-rose-500/10 text-rose-800 dark:border-rose-300/30 dark:bg-rose-400/12 dark:text-rose-100";
const CONTEXT_BADGE_CLASSNAME =
  "border-violet-300/40 bg-violet-500/10 text-violet-800 dark:border-violet-300/30 dark:bg-violet-400/12 dark:text-violet-100";
const SUCCESS_BADGE_CLASSNAME =
  "border-emerald-300/40 bg-emerald-500/10 text-emerald-800 dark:border-emerald-300/30 dark:bg-emerald-400/12 dark:text-emerald-100";
const MUTED_BADGE_CLASSNAME =
  "border-slate-300 bg-slate-500/10 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200";
const CARD_ACTION_BUTTON_CLASSNAME =
  "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border transition sm:h-7 sm:w-7";
const CARD_ACTION_ICON_CLASSNAME = "h-4 w-4 sm:h-3.5 sm:w-3.5";

function shouldIgnoreLaunchClick(
  target: EventTarget | null,
  currentTarget: EventTarget | null,
): boolean {
  if (!(target instanceof Element)) return false;
  const interactive = target.closest(
    'button,a,input,textarea,select,summary,[role="button"],[data-card-interactive="true"]',
  );
  return Boolean(interactive && interactive !== currentTarget);
}

function hasActiveTextSelectionWithin(container: Element | null): boolean {
  const selection = window.getSelection?.();
  if (!selection || selection.isCollapsed || !selection.toString().trim()) {
    return false;
  }
  const anchor = selection.anchorNode;
  const focus = selection.focusNode;
  if (!anchor && !focus) return true;
  return Boolean(
    container &&
    ((anchor && container.contains(anchor)) ||
      (focus && container.contains(focus))),
  );
}

function buildChrome(
  surfaceClassName: string,
  badgeClassName: string,
  icon: typeof FileText,
  label: string,
): CardChrome {
  return {
    surfaceClassName,
    badgeClassName,
    icon,
    label,
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown) {
  return typeof value === "string" ? value : null;
}

function asRecordOrNull(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asNumberishString(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return asString(value);
}

function asBoolean(value: unknown) {
  return typeof value === "boolean" ? value : null;
}

function firstLifecycleString(
  payload: Record<string, unknown>,
  keys: string[],
) {
  for (const key of keys) {
    const value = asString(payload[key]);
    if (value) return value;
  }
  return null;
}

function isStaleTaskPayload(payload: Record<string, unknown>) {
  const lifecycle = firstLifecycleString(payload, [
    "lifecycle_status",
    "lifecycle",
    "freshness",
    "task_lifecycle",
  ])?.toLowerCase();
  return (
    asBoolean(payload.is_stale) === true ||
    asBoolean(payload.stale) === true ||
    lifecycle === "stale" ||
    lifecycle === "archived"
  );
}

function getQueuePosition(payload: Record<string, unknown>) {
  const position =
    asNumberishString(payload.queue_position) ||
    asNumberishString(payload.active_queue_position) ||
    asNumberishString(payload.wip_position);
  const total =
    asNumberishString(payload.queue_total) ||
    asNumberishString(payload.active_queue_total);
  if (!position) return null;
  return total ? `${position}/${total}` : `#${position}`;
}

function getTaskWorkState(payload: Record<string, unknown>) {
  return firstLifecycleString(payload, [
    "work_status",
    "wip_status",
    "lifecycle_status",
    "task_lifecycle",
  ])?.replace(/_/g, " ");
}

function looksLikeOpaqueIdentifier(value?: string | null) {
  if (!value) return true;
  const trimmed = value.trim();
  if (!trimmed) return true;
  if (/^[0-9a-f]{8,}$/i.test(trimmed)) return true;
  if (/^[0-9a-f-]{24,}$/i.test(trimmed)) return true;
  return false;
}

function looksLikeRawContextTitle(value?: string | null) {
  if (!value) return false;
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (looksLikeOpaqueIdentifier(trimmed)) return true;

  const colonParts = trimmed.split(":");
  if (colonParts.length >= 2) {
    const tail = colonParts[colonParts.length - 1]?.trim();
    if (looksLikeOpaqueIdentifier(tail)) return true;
  }

  return /^\S{40,}$/.test(trimmed);
}

function compactContextLabel(value?: string | null) {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;

  const colonParts = trimmed.split(":");
  const head = colonParts[0]?.trim();
  if (head && !looksLikeOpaqueIdentifier(head) && /[a-z]/i.test(head)) {
    return head;
  }

  const slashParts = trimmed.split(/[/\\]/).filter(Boolean);
  const basename = slashParts[slashParts.length - 1]?.trim();
  if (basename && !looksLikeOpaqueIdentifier(basename)) {
    return basename;
  }

  return null;
}

function resolveContextCardTitle(
  payload: Record<string, unknown>,
  fallbackLabel: string,
) {
  const title = asString(payload.title) || asString(payload.label);
  if (title && !looksLikeRawContextTitle(title)) return title;

  const reference = Array.isArray(payload.references)
    ? payload.references
        .map((item) => asRecordOrNull(item))
        .find((item) => item && (asString(item.label) || asString(item.path)))
    : null;
  const referenceLabel =
    asString(reference?.label) ||
    compactContextLabel(asString(reference?.path));
  if (referenceLabel) return referenceLabel;

  const contextKey =
    asString(payload.context_key) ||
    asString(payload.key) ||
    asString(payload.path) ||
    asString(payload.resource_id) ||
    title;
  const compact = compactContextLabel(contextKey);
  return compact || fallbackLabel;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function titleCase(value: string) {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatByteSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return String(bytes);
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  const fixed = value >= 100 ? value.toFixed(0) : value.toFixed(1);
  return `${fixed} ${units[idx]}`;
}

function prettyContentType(mime: string): string {
  // "text/markdown" -> "Markdown", "image/png" -> "PNG", fallback to the
  // full mime unchanged.
  const trimmed = mime.trim();
  if (!trimmed) return trimmed;
  if (trimmed.includes("/")) {
    const sub = trimmed.split("/", 2)[1];
    if (!sub) return trimmed;
    const clean = sub.split(";")[0].trim();
    if (!clean) return trimmed;
    if (clean.length <= 4) return clean.toUpperCase();
    return titleCase(clean.replace(/^x-/, ""));
  }
  return trimmed;
}

// v2 card contract — every type except `agent` exposes the top-right Share
// action. Agent cards are excluded because "share an agent" reads as
// "introduce them" and the natural user action is @mention in the composer;
// Open still takes you to the agent profile. Share opens the composer with
// the card attached (no auto-@mention — the user writes their own intro).
function supportsShare(card: SpaceAgentCardEnvelope): boolean {
  return card.type !== "agent";
}

function supportsReply(card: SpaceAgentCardEnvelope): boolean {
  return card.type === "agent";
}

function signalTimestampFact(
  label: string,
  value?: string | null,
): SignalFact | null {
  if (!value) return null;
  return {
    label,
    value: formatTimestamp(value) || value,
    title: formatAbsoluteTimestamp(value) || value,
    dateTime: value,
  };
}

function cardHeaderTimestamp(
  payload: Record<string, unknown>,
  fallbackTimestamp?: string | null,
): SignalFact | null {
  const alert = asRecordOrNull(payload.alert);
  return signalTimestampFact(
    "Time",
    fallbackTimestamp ||
      asString(payload.created_at) ||
      asString(alert?.fired_at),
  );
}

function getCardIntent(
  type: SpaceAgentCardType,
  payload: Record<string, unknown>,
): CardIntent | null {
  const intent = asString(payload.intent);
  if (
    intent === "alert" ||
    intent === "signal" ||
    intent === "context" ||
    intent === "review"
  ) {
    return intent;
  }
  if (type === "alert") return "alert";
  if (type === "context") return "context";
  if (type === "confirmation" || type === "handoff_select") return "review";
  if (type === "task" || type === "agent" || type === "result") {
    return "signal";
  }
  return null;
}

function getTaskSignalChrome(
  payload: Record<string, unknown>,
): CardChrome | null {
  const alert = asRecordOrNull(payload.alert);
  const kind = asString(alert?.kind) || asString(payload.kind);
  const toolName = asString(payload.tool_name)?.toLowerCase() || "";
  const resourceUri = asString(payload.resource_uri)?.toLowerCase() || "";
  const taskPayload = asRecordOrNull(payload.task);
  const hasTaskSignal = Boolean(
    taskPayload ||
    asString(payload.task_id) ||
    asString(payload.source_task_id) ||
    asString(payload.task_title) ||
    toolName.startsWith("tasks") ||
    resourceUri.startsWith("ui://tasks/") ||
    resourceUri.startsWith("ui://task-board"),
  );
  if (!hasTaskSignal && kind !== "task_reminder") return null;

  if (kind === "task_reminder" || asString(payload.source_task_id)) {
    return buildChrome(
      "border-sky-300/25 bg-[radial-gradient(circle_at_0%_0%,rgba(56,189,248,0.18),transparent_44%),rgba(14,165,233,0.08)] shadow-[0_18px_44px_-30px_rgba(56,189,248,0.65)]",
      "border-sky-200 bg-sky-100 text-sky-700 dark:border-sky-300/30 dark:bg-sky-400/12 dark:text-sky-200",
      Bell,
      "Reminder",
    );
  }

  return buildChrome(
    "border-slate-200 bg-slate-50/80 shadow-sm dark:border-slate-700 dark:bg-slate-900/70 dark:shadow-none",
    TASK_BADGE_CLASSNAME,
    BriefcaseBusiness,
    "Task",
  );
}

function getAppSurfaceChrome(payload: Record<string, unknown>) {
  const toolName = asString(payload.tool_name)?.toLowerCase();
  const resourceUri = asString(payload.resource_uri)?.toLowerCase() || "";
  const surface =
    toolName || resourceUri.split("ui://", 2)[1]?.split("/", 1)[0];

  // v2: singular resource-type labels (Task / Agent / Space / Context /
  // Identity / Message / Search) so the badge reads as "what this card is
  // about" rather than a tool name.
  switch (surface) {
    case "tasks":
      return buildChrome(
        "border-slate-200 bg-slate-50/80 shadow-sm dark:border-slate-700 dark:bg-slate-900/70 dark:shadow-none",
        TASK_BADGE_CLASSNAME,
        BriefcaseBusiness,
        "Task",
      );
    case "agents":
      return buildChrome(
        "border-amber-300/20 bg-amber-400/10 shadow-[0_18px_40px_-28px_rgba(251,191,36,0.55)]",
        AGENT_BADGE_CLASSNAME,
        Orbit,
        "Agent",
      );
    case "spaces":
      return buildChrome(
        "border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:shadow-[0_18px_40px_-28px_rgba(15,23,42,0.8)]",
        MUTED_BADGE_CLASSNAME,
        Home,
        "Space",
      );
    case "context":
      return buildChrome(
        "border-violet-300/20 bg-violet-400/10 shadow-[0_18px_40px_-28px_rgba(167,139,250,0.55)]",
        CONTEXT_BADGE_CLASSNAME,
        Search,
        "Context",
      );
    case "whoami":
      return buildChrome(
        "border-amber-300/20 bg-amber-400/10 shadow-[0_18px_40px_-28px_rgba(251,191,36,0.55)]",
        AGENT_BADGE_CLASSNAME,
        Orbit,
        "Identity",
      );
    case "messages":
      return buildChrome(
        "border-emerald-300/20 bg-emerald-400/10 shadow-[0_18px_40px_-28px_rgba(52,211,153,0.55)]",
        SUCCESS_BADGE_CLASSNAME,
        MessageSquareShare,
        "Message",
      );
    case "search":
      return buildChrome(
        "border-emerald-300/20 bg-emerald-400/10 shadow-[0_18px_40px_-28px_rgba(52,211,153,0.55)]",
        SUCCESS_BADGE_CLASSNAME,
        Search,
        "Search",
      );
    default:
      return null;
  }
}

// v2 alert icon derivation — read from payload.alert.kind +
// payload.alert.security_sensitive instead of a flat type->icon. Keeps
// color as severity, icon as meaning. ShieldAlert is reserved for
// security-sensitive cases only (kill_switch / token_misuse / policy).
function getAlertChrome(
  kind: string | null,
  securitySensitive: boolean,
): CardChrome {
  if (securitySensitive) {
    return buildChrome(
      "border-rose-300/30 bg-[radial-gradient(circle_at_0%_0%,rgba(251,113,133,0.18),transparent_44%),rgba(220,38,38,0.08)] shadow-[0_18px_44px_-30px_rgba(251,113,133,0.65)]",
      DANGER_BADGE_CLASSNAME,
      ShieldAlert,
      "Alert",
    );
  }

  switch (kind) {
    case "notice":
    case "task_completed":
      return buildChrome(
        "border-lime-300/25 bg-[radial-gradient(circle_at_0%_0%,rgba(163,230,53,0.15),transparent_44%),rgba(132,204,22,0.06)] shadow-[0_18px_44px_-30px_rgba(163,230,53,0.55)]",
        NOTICE_BADGE_CLASSNAME,
        kind === "task_completed" ? CheckCircle2 : Info,
        "Notice",
      );
    case "sla_breach":
    case "hitl_required":
    case "escalation":
      return buildChrome(
        "border-amber-300/28 bg-[radial-gradient(circle_at_0%_0%,rgba(251,191,36,0.16),transparent_44%),rgba(217,119,6,0.06)] shadow-[0_18px_44px_-30px_rgba(251,191,36,0.6)]",
        ATTENTION_BADGE_CLASSNAME,
        TriangleAlert,
        "Alert",
      );
    case "health":
    case "agent_state":
      return buildChrome(
        "border-sky-300/25 bg-[radial-gradient(circle_at_0%_0%,rgba(56,189,248,0.14),transparent_44%),rgba(14,165,233,0.06)] shadow-[0_18px_44px_-30px_rgba(56,189,248,0.55)]",
        ALERT_BADGE_CLASSNAME,
        Activity,
        "Alert",
      );
    case "task_reminder":
      return buildChrome(
        "border-sky-300/25 bg-[radial-gradient(circle_at_0%_0%,rgba(56,189,248,0.18),transparent_44%),rgba(14,165,233,0.08)] shadow-[0_18px_44px_-30px_rgba(56,189,248,0.65)]",
        ALERT_BADGE_CLASSNAME,
        Bell,
        "Reminder",
      );
    case "kill_switch":
    case "token_misuse":
    case "policy_violation":
      return buildChrome(
        "border-rose-300/30 bg-[radial-gradient(circle_at_0%_0%,rgba(251,113,133,0.18),transparent_44%),rgba(220,38,38,0.08)] shadow-[0_18px_44px_-30px_rgba(251,113,133,0.65)]",
        DANGER_BADGE_CLASSNAME,
        ShieldAlert,
        "Alert",
      );
    default:
      return buildChrome(
        "border-sky-300/25 bg-[radial-gradient(circle_at_0%_0%,rgba(56,189,248,0.18),transparent_44%),rgba(14,165,233,0.08)] shadow-[0_18px_44px_-30px_rgba(56,189,248,0.65)]",
        ALERT_BADGE_CLASSNAME,
        Bell,
        "Alert",
      );
  }
}

function getCardChrome(
  type: SpaceAgentCardType,
  payload: Record<string, unknown>,
): CardChrome {
  const intent = getCardIntent(type, payload);
  switch (type) {
    case "alert": {
      const alert = asRecord(payload.alert);
      const kind = asString(alert.kind);
      const securitySensitive = asBoolean(alert.security_sensitive) === true;
      return getAlertChrome(kind, securitySensitive);
    }
    case "task":
      return buildChrome(
        "border-slate-200 bg-slate-50/80 shadow-sm dark:border-slate-700 dark:bg-slate-900/70 dark:shadow-none",
        TASK_BADGE_CLASSNAME,
        BriefcaseBusiness,
        "Task",
      );
    case "agent":
      return buildChrome(
        "border-amber-300/20 bg-amber-400/10 shadow-[0_18px_40px_-28px_rgba(251,191,36,0.55)]",
        AGENT_BADGE_CLASSNAME,
        Orbit,
        "Agent",
      );
    case "context":
      return buildChrome(
        "border-violet-300/20 bg-violet-400/10 shadow-[0_18px_40px_-28px_rgba(167,139,250,0.55)]",
        CONTEXT_BADGE_CLASSNAME,
        Search,
        "Context",
      );
    case "confirmation":
      return buildChrome(
        "border-orange-300/20 bg-orange-400/10 shadow-[0_18px_40px_-28px_rgba(251,146,60,0.55)]",
        REVIEW_BADGE_CLASSNAME,
        TriangleAlert,
        "Review",
      );
    case "handoff_select":
      return buildChrome(
        "border-lime-300/20 bg-lime-400/10 shadow-[0_18px_40px_-28px_rgba(132,204,22,0.55)]",
        NOTICE_BADGE_CLASSNAME,
        MessageSquareShare,
        "Handoff",
      );
    case "receipt":
      return buildChrome(
        "border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:shadow-[0_18px_40px_-28px_rgba(15,23,42,0.8)]",
        MUTED_BADGE_CLASSNAME,
        CheckCircle2,
        "Receipt",
      );
    case "result":
    default: {
      const taskSignalChrome = getTaskSignalChrome(payload);
      if (taskSignalChrome) return taskSignalChrome;
      const appChrome = getAppSurfaceChrome(payload);
      if (appChrome) return appChrome;
      // v2: drop the generic "Signal" badge — when a resource type isn't
      // known, fall back to "Result". Signal is an intent, not a visible
      // badge (see getCardIntent).
      void intent;
      return buildChrome(
        "border-emerald-300/20 bg-emerald-400/10 shadow-[0_18px_40px_-28px_rgba(52,211,153,0.55)]",
        ACTIVITY_STREAM_RESULT_BADGE_CLASSNAME,
        FileText,
        "Result",
      );
    }
  }
}

function buildActionKey(
  messageId: string,
  cardId: string,
  actionId: string,
  choiceId?: string,
) {
  return [messageId, cardId, actionId, choiceId || ""].join(":");
}

function buildCardCopyText(
  label: string,
  title: string,
  summary: string,
  payload: Record<string, unknown>,
): string {
  const bodyMarkdown = asString(payload.body_markdown);
  const facts = [
    asString(payload.status) ? `Status: ${asString(payload.status)}` : null,
    asString(payload.priority)
      ? `Priority: ${asString(payload.priority)}`
      : null,
    asString(payload.assignee_name)
      ? `Assignee: ${asString(payload.assignee_name)}`
      : null,
  ].filter(Boolean);

  return [label, title, summary, bodyMarkdown, ...facts]
    .filter((part): part is string => Boolean(part && part.trim()))
    .join("\n\n");
}

type AgentActivitySurface = {
  agentName: string | null;
  status: string | null;
  summary: string | null;
};

const CARD_ACTIVITY_SOURCE_KEYS = [
  "id",
  "message_id",
  "parent_message_id",
  "source_message_id",
  "conversation_id",
  "dispatch_id",
  "request_id",
  "alert_id",
  "source_alert_id",
  "originating_alert_id",
  "trigger_alert_id",
  "trigger_message_id",
  "originating_message_id",
  "source_card_id",
  "card_id",
];

function addStringCandidate(set: Set<string>, value: unknown) {
  if (typeof value !== "string") return;
  const trimmed = value.trim();
  if (trimmed) set.add(trimmed);
}

function collectCardActivitySourceIds(
  card: SpaceAgentCardEnvelope,
  messageId: string,
  payload: Record<string, unknown>,
) {
  const candidates = new Set<string>();
  addStringCandidate(candidates, messageId);
  addStringCandidate(candidates, card.card_id);

  const alert = asRecordOrNull(payload.alert);
  const task = asRecordOrNull(alert?.task) || asRecordOrNull(payload.task);
  const nestedRecords = [payload, alert, task].filter(
    (record): record is Record<string, unknown> => Boolean(record),
  );

  for (const record of nestedRecords) {
    for (const key of CARD_ACTIVITY_SOURCE_KEYS) {
      addStringCandidate(candidates, record[key]);
    }
  }

  return candidates;
}

function resolveCardPendingResponse(
  card: SpaceAgentCardEnvelope,
  messageId: string,
  payload: Record<string, unknown>,
  pendingResponsesBySourceId?: Record<string, PendingResponseState>,
) {
  if (!pendingResponsesBySourceId) return null;
  for (const candidate of collectCardActivitySourceIds(
    card,
    messageId,
    payload,
  )) {
    const response = pendingResponsesBySourceId[candidate];
    if (response) return response;
  }
  return null;
}

function reminderActivitySource(
  payload: Record<string, unknown>,
): AgentActivitySurface | null {
  const alert = asRecordOrNull(payload.alert);
  const task = asRecordOrNull(alert?.task) || asRecordOrNull(payload.task);
  const notificationTarget =
    asRecordOrNull(payload.notification_target) ||
    asRecordOrNull(alert?.notification_target) ||
    asRecordOrNull(task?.notification_target);
  const activity =
    asRecordOrNull(payload.agent_activity) ||
    asRecordOrNull(payload.activity) ||
    asRecordOrNull(alert?.agent_activity) ||
    asRecordOrNull(alert?.activity) ||
    asRecordOrNull(task?.agent_activity) ||
    asRecordOrNull(task?.activity) ||
    notificationTarget;

  if (!activity) return null;

  return {
    agentName:
      asString(activity.agent_name) ||
      asString(activity.agent) ||
      asString(activity.name) ||
      asString(activity.owner) ||
      null,
    status:
      asString(activity.status) ||
      asString(activity.state) ||
      asString(activity.delivery_state) ||
      asString(activity.lifecycle) ||
      null,
    summary:
      asString(activity.summary) ||
      asString(activity.message) ||
      asString(activity.detail) ||
      (asString(activity.delivery_state)
        ? `Delivery: ${asString(activity.delivery_state)?.replace(/_/g, " ")}`
        : null),
  };
}

function hasReminderNotificationTarget(
  payload: Record<string, unknown>,
  alert: Record<string, unknown> | null,
  task: Record<string, unknown> | null,
) {
  return Boolean(
    asRecordOrNull(payload.notification_target) ||
    asRecordOrNull(alert?.notification_target) ||
    asRecordOrNull(task?.notification_target),
  );
}

function isReminderCapableCard(
  type: SpaceAgentCardType,
  payload: Record<string, unknown>,
) {
  const alert = asRecordOrNull(payload.alert);
  const alertTask = asRecordOrNull(alert?.task);
  const task = alertTask || asRecordOrNull(payload.task);
  const kind = asString(alert?.kind) || asString(payload.kind);
  const hasNotificationTarget = hasReminderNotificationTarget(
    payload,
    alert,
    task,
  );

  if (kind === "task_reminder") return true;
  if (type === "task") {
    return Boolean(
      asString(payload.event) === "reminder_due" ||
      hasNotificationTarget ||
      asString(payload.next_reminder_at) ||
      asString(payload.reminder_at) ||
      asString(payload.remind_at) ||
      asString(payload.reminder_target) ||
      asRecordOrNull(payload.reminder_policy) ||
      asString(payload.notify_action_id) ||
      asString(payload.action_id) ||
      asBoolean(payload.can_notify_agent) === true ||
      reminderActivitySource(payload),
    );
  }

  return Boolean(
    asString(alert?.event) === "reminder_due" ||
    hasNotificationTarget ||
    asString(alert?.next_reminder_at) ||
    asString(alertTask?.next_reminder_at) ||
    asRecordOrNull(alert?.reminder_policy) ||
    asRecordOrNull(alertTask?.reminder_policy) ||
    reminderActivitySource(payload),
  );
}

function resolveOpenWidgetLabel(label: string): string {
  if (label === "Task" || label === "Reminder") return "Open Task Detail";
  return `Open ${label} widget`;
}

export function AxMessageWidgets({
  cards,
  messageId,
  activeActionKey,
  onAction,
  linkedWidgetSurface,
  linkedWidgetSurfacesByCardId,
  onOpenWidgetPanel,
  onForwardInit,
  onReplyInit,
  timestamp,
  pendingResponsesBySourceId,
}: AxMessageWidgetsProps) {
  const [handoffSelections, setHandoffSelections] = useState<
    Record<string, string>
  >({});
  const [copiedCardId, setCopiedCardId] = useState<string | null>(null);

  const [includeStaleTasks, setIncludeStaleTasks] = useState(false);

  const sortedCards = useMemo(
    () =>
      [...cards].sort((left, right) => {
        if (left.replace_in_place === right.replace_in_place) return 0;
        return left.replace_in_place ? 1 : -1;
      }),
    [cards],
  );
  const visibleCards = includeStaleTasks
    ? sortedCards
    : sortedCards.filter(
        (card) =>
          card.type !== "task" || !isStaleTaskPayload(asRecord(card.payload)),
      );
  const hiddenStaleTaskCount = sortedCards.length - visibleCards.length;

  if (!sortedCards.length) return null;

  return (
    <div className="mt-4 space-y-3" data-testid="ax-widget-stack">
      {hiddenStaleTaskCount > 0 ? (
        <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300">
          <span>
            {hiddenStaleTaskCount} stale task
            {hiddenStaleTaskCount === 1 ? "" : "s"} hidden
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => setIncludeStaleTasks(true)}
          >
            Include stale
          </Button>
        </div>
      ) : null}
      {visibleCards.map((card) => {
        const payload = asRecord(card.payload);
        const chrome = getCardChrome(card.type, payload);
        const Icon = chrome.icon;
        const isAlertCard = card.type === "alert";
        const cardLinkedWidgetSurface =
          linkedWidgetSurfacesByCardId?.[card.card_id] || linkedWidgetSurface;
        const opensLinkedWidget = Boolean(
          cardLinkedWidgetSurface && onOpenWidgetPanel,
        );
        const headerTimestamp = cardHeaderTimestamp(payload, timestamp);
        const openWidgetLabel = resolveOpenWidgetLabel(chrome.label);
        const openLinkedWidget = () => {
          if (!cardLinkedWidgetSurface || !onOpenWidgetPanel) return;
          onOpenWidgetPanel({
            messageId,
            surface: cardLinkedWidgetSurface,
            timestamp,
          });
        };

        const alertPayload = asRecordOrNull(payload.alert);
        const alertTask =
          asRecordOrNull(alertPayload?.task) || asRecordOrNull(payload.task);
        const isTaskReminder =
          isAlertCard &&
          (asString(alertPayload?.kind) === "task_reminder" ||
            Boolean(asString(alertPayload?.source_task_id)) ||
            Boolean(asString(payload.source_task_id)));
        const pendingResponse = resolveCardPendingResponse(
          card,
          messageId,
          payload,
          pendingResponsesBySourceId,
        );
        const pendingResponseDisplay =
          getPendingResponseDisplay(pendingResponse);
        const title = isTaskReminder
          ? asString(alertTask?.title) ||
            asString(payload.task_title) ||
            asString(alertPayload?.title) ||
            asString(payload.title) ||
            chrome.label
          : card.type === "context"
            ? resolveContextCardTitle(payload, chrome.label)
            : asString(payload.title) ||
              asString(payload.label) ||
              chrome.label;
        const summary = isTaskReminder
          ? asString(alertPayload?.reason) ||
            asString(payload.summary) ||
            asString(alertPayload?.summary) ||
            asString(payload.body_markdown) ||
            ""
          : asString(payload.summary) || asString(payload.body_markdown) || "";
        const status = asString(payload.status);
        const actionId =
          asString(payload.action_id) || asString(payload.submit_action_id);
        const selectedChoiceId = handoffSelections[card.card_id];
        const submitActionKey =
          actionId && selectedChoiceId
            ? buildActionKey(
                messageId,
                card.card_id,
                actionId,
                selectedChoiceId,
              )
            : actionId
              ? buildActionKey(messageId, card.card_id, actionId)
              : null;
        const isSubmitting =
          submitActionKey !== null && activeActionKey === submitActionKey;
        const copyText = buildCardCopyText(
          chrome.label,
          title,
          summary,
          payload,
        );
        const cardIsCopied = copiedCardId === card.card_id;
        const copyCardText = async () => {
          if (!copyText) return;
          const copied = await copyToClipboard(copyText);
          if (copied) {
            setCopiedCardId(card.card_id);
            window.setTimeout(() => {
              setCopiedCardId((current) =>
                current === card.card_id ? null : current,
              );
            }, 1600);
          }
        };

        return (
          <section
            key={card.card_id}
            data-testid="ax-widget-card"
            data-widget-type={card.type}
            onClick={(event) => {
              if (!opensLinkedWidget) return;
              if (shouldIgnoreLaunchClick(event.target, event.currentTarget))
                return;
              if (hasActiveTextSelectionWithin(event.currentTarget)) return;
              openLinkedWidget();
            }}
            onKeyDown={(event) => {
              if (!opensLinkedWidget) return;
              if (event.key !== "Enter" && event.key !== " ") return;
              if (shouldIgnoreLaunchClick(event.target, event.currentTarget))
                return;
              event.preventDefault();
              openLinkedWidget();
            }}
            className={cn(
              "relative min-w-0 max-w-full overflow-x-hidden rounded-[20px] border border-gray-200 bg-white px-3 py-3 shadow-sm backdrop-blur-xl outline-none sm:rounded-[24px] sm:px-4 sm:py-4 dark:border-slate-700 dark:bg-slate-900",
              isAlertCard ? "transition hover:border-sky-100/45" : "",
              opensLinkedWidget ? "cursor-pointer hover:shadow-md" : "",
              chrome.surfaceClassName,
            )}
          >
            {opensLinkedWidget ? (
              <button
                type="button"
                data-testid="ax-card-open-surface"
                aria-label={openWidgetLabel}
                onClick={(event) => {
                  event.stopPropagation();
                  openLinkedWidget();
                }}
                className="absolute inset-0 z-0 rounded-[20px] outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-white sm:rounded-[24px] dark:focus-visible:ring-cyan-300/60 dark:focus-visible:ring-offset-slate-950"
              />
            ) : null}
            <div className="relative z-10 min-w-0">
              <div className="flex min-w-0 items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
                    <div
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em] sm:gap-2 sm:px-2.5 sm:py-1 sm:text-[11px] sm:tracking-[0.18em]",
                        chrome.badgeClassName,
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />
                      {chrome.label}
                    </div>
                    {headerTimestamp ? (
                      <time
                        dateTime={headerTimestamp.dateTime}
                        title={headerTimestamp.title}
                        className="text-[11px] text-gray-500 sm:text-xs dark:text-gray-400"
                      >
                        {headerTimestamp.value}
                      </time>
                    ) : null}
                  </div>
                  <div
                    className="mt-2 line-clamp-2 overflow-hidden break-words text-[15px] font-semibold leading-5 text-gray-900 sm:mt-3 sm:text-base sm:leading-normal dark:text-white [overflow-wrap:anywhere]"
                    title={title}
                  >
                    {title}
                  </div>
                </div>
                {/*
                  Mobile cleanup: keep forward/reply/share in the header rail
                  instead of wrapping them onto a separate row. The buttons keep
                  40px mobile touch boxes, then collapse to the existing compact
                  desktop icons at sm+.
                */}
                <div
                  data-testid="ax-card-actions"
                  className="-mr-1 -mt-1 flex shrink-0 items-center justify-end gap-1 sm:mr-0 sm:mt-0 sm:gap-1.5"
                >
                  {status && !opensLinkedWidget ? (
                    <div
                      className={cn(
                        "max-w-[7rem] truncate rounded-full border px-2 py-1 text-[10px] uppercase tracking-[0.14em] sm:max-w-full sm:px-3 sm:text-[11px] sm:tracking-[0.18em]",
                        NEUTRAL_CHIP_CLASSNAME,
                      )}
                    >
                      {status.replace(/_/g, " ")}
                    </div>
                  ) : null}
                  {copyText ? (
                    <button
                      type="button"
                      data-testid="ax-card-copy"
                      aria-label={
                        cardIsCopied ? "Copied card text" : "Copy card text"
                      }
                      title={cardIsCopied ? "Copied" : "Copy card text"}
                      onClick={(event) => {
                        event.stopPropagation();
                        void copyCardText();
                      }}
                      className={cn(
                        CARD_ACTION_BUTTON_CLASSNAME,
                        cardIsCopied
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-300/40 dark:bg-emerald-400/10 dark:text-emerald-100"
                          : "border-gray-200 bg-gray-100 text-gray-700 hover:border-gray-300 hover:text-gray-900 dark:border-slate-700 dark:bg-slate-800 dark:text-gray-300 dark:hover:border-slate-600 dark:hover:text-white",
                      )}
                    >
                      <Copy className={CARD_ACTION_ICON_CLASSNAME} />
                    </button>
                  ) : null}
                  {supportsShare(card) && onForwardInit ? (
                    <button
                      type="button"
                      data-testid="ax-card-share"
                      aria-label="Share"
                      title="Share — attach this card to a new message"
                      onClick={(event) => {
                        event.stopPropagation();
                        onForwardInit({
                          cardId: card.card_id,
                          card,
                          messageId,
                        });
                      }}
                      className={cn(
                        CARD_ACTION_BUTTON_CLASSNAME,
                        "border-gray-200 bg-gray-100 text-gray-700 hover:border-gray-300 hover:text-gray-900 dark:border-slate-700 dark:bg-slate-800 dark:text-gray-300 dark:hover:border-slate-600 dark:hover:text-white",
                      )}
                    >
                      <Share2 className={CARD_ACTION_ICON_CLASSNAME} />
                    </button>
                  ) : null}
                  {supportsReply(card) && onReplyInit ? (
                    <button
                      type="button"
                      data-testid="ax-card-reply"
                      aria-label="Reply to message"
                      title="Reply to message"
                      onClick={(event) => {
                        event.stopPropagation();
                        onReplyInit({
                          cardId: card.card_id,
                          card,
                          messageId,
                        });
                      }}
                      className={cn(
                        CARD_ACTION_BUTTON_CLASSNAME,
                        "border-gray-200 bg-gray-100 text-gray-700 hover:border-gray-300 hover:text-gray-900 dark:border-slate-700 dark:bg-slate-800 dark:text-gray-300 dark:hover:border-slate-600 dark:hover:text-white",
                      )}
                    >
                      <CornerUpLeft className={CARD_ACTION_ICON_CLASSNAME} />
                    </button>
                  ) : null}
                </div>
              </div>
              {summary ? (
                <div
                  className={cn(
                    "mt-1.5 break-words text-sm leading-5 text-gray-900 sm:mt-2 sm:leading-6 dark:text-gray-100",
                    isAlertCard ? "line-clamp-2" : "line-clamp-3",
                  )}
                  title={summary}
                >
                  {summary}
                </div>
              ) : null}
            </div>

            {card.type === "alert" ? (
              <AlertCardBody
                payload={payload}
                pendingResponseDisplay={pendingResponseDisplay}
              />
            ) : null}
            {card.type !== "alert" ? (
              <SignalContextStrip payload={payload} />
            ) : null}
            {card.type === "task" ? <TaskCardBody payload={payload} /> : null}
            {card.type === "agent" ? <AgentCardBody payload={payload} /> : null}
            {card.type === "context" ? (
              <ContextCardBody payload={payload} />
            ) : null}
            {card.type === "result" && isSpacePayload(payload) ? (
              <SpaceCardBody payload={payload} />
            ) : null}
            {card.type === "result" ? (
              <ResultCardBody payload={payload} />
            ) : null}
            {isReminderCapableCard(card.type, payload) ||
            pendingResponseDisplay ? (
              <AgentActivityRegion
                payload={payload}
                pendingResponse={pendingResponse}
                pendingResponseDisplay={pendingResponseDisplay}
              />
            ) : null}
            {card.type === "receipt" ? (
              <ReceiptCardBody payload={payload} />
            ) : null}
            {card.type === "confirmation" && !opensLinkedWidget ? (
              <ConfirmationCardBody
                messageId={messageId}
                cardId={card.card_id}
                payload={payload}
                activeActionKey={activeActionKey}
                onAction={onAction}
              />
            ) : null}
            {card.type === "handoff_select" ? (
              <HandoffSelectCardBody
                handoffId={card.handoff_id}
                payload={payload}
                selectedChoiceId={selectedChoiceId}
                isSubmitting={isSubmitting}
                onChangeSelection={(choiceId) =>
                  setHandoffSelections((current) => ({
                    ...current,
                    [card.card_id]: choiceId,
                  }))
                }
                onSubmit={(choiceId) => {
                  const nextActionId =
                    actionId ||
                    asString(payload.handoff_action_id) ||
                    card.handoff_id;
                  if (!nextActionId) return;
                  onAction({
                    actionId: nextActionId,
                    cardId: card.card_id,
                    choiceId,
                  });
                }}
              />
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

function SignalContextStrip({ payload }: { payload: Record<string, unknown> }) {
  const alert = asRecordOrNull(payload.alert);
  const fromLabel =
    asString(payload.sender_label) ||
    asString(payload.display_name) ||
    asString(alert?.triggered_by_agent_name) ||
    asString(alert?.agent);
  const source =
    asString(payload.source) ||
    asString(alert?.source) ||
    asString(payload.tool_name);
  const origin = asString(payload.origin) || asString(alert?.origin);
  const delivery = asString(payload.delivery) || asString(alert?.delivery);
  const status = asString(payload.status);
  const facts = [
    fromLabel ? { label: "From", value: fromLabel } : null,
    source ? { label: "Source", value: source } : null,
    origin ? { label: "Via", value: origin } : null,
    delivery ? { label: "Delivery", value: delivery } : null,
    status ? { label: "Status", value: status } : null,
  ].filter((item): item is SignalFact => Boolean(item));

  if (!facts.length) return null;

  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {facts.map((item) => (
        <span
          key={`${item.label}-${item.value}`}
          title={item.title}
          className={cn(
            "min-w-0 max-w-full rounded-full border px-2.5 py-1 text-[11px]",
            NEUTRAL_CHIP_CLASSNAME,
          )}
        >
          <span className={NEUTRAL_CHIP_LABEL_CLASSNAME}>{item.label}:</span>{" "}
          <span className={NEUTRAL_CHIP_VALUE_CLASSNAME}>{item.value}</span>
        </span>
      ))}
    </div>
  );
}

function AlertCardBody({
  payload,
  pendingResponseDisplay,
}: {
  payload: Record<string, unknown>;
  pendingResponseDisplay?: PendingResponseDisplay | null;
}) {
  const alert = asRecordOrNull(payload.alert) || payload;
  const kind = asString(alert.kind);
  const alertTask = asRecordOrNull(alert.task);
  const cycle = asRecordOrNull(alert.cycle);
  const attachments = [
    ...asArray(alert.attachments),
    ...asArray(payload.attachments),
  ];
  const severity =
    asString(alert.severity) || asString(payload.severity) || "info";
  const target =
    asString(alert.target_agent) ||
    asString(alert.target_group) ||
    asString(alert.target) ||
    null;
  const current = asNumberishString(cycle?.current);
  const max = asNumberishString(cycle?.max);
  const cadence = asString(alert.cadence);
  const cycleLabel =
    current && max
      ? `${current}/${max}${cadence ? ` · ${cadence}` : ""}`
      : cadence;
  const completionPromise = asString(alert.completion_promise);
  const expectedResponse =
    asString(alert.expected_response) || completionPromise;
  const responseRequired = asBoolean(alert.response_required);
  const responseLabel =
    responseRequired === true || expectedResponse
      ? "Required"
      : responseRequired === false
        ? "Optional"
        : null;
  const contextKey =
    asString(alert.context_key) || asString(payload.context_key);
  const evidenceLabel = attachments.length
    ? `${attachments.length} attachment${attachments.length === 1 ? "" : "s"}`
    : contextKey
      ? "Context attached"
      : null;
  const sourceLabel = asString(alert.source) || asString(payload.source);
  const dueAt =
    asString(alert.due_at) ||
    asString(alert.deadline) ||
    asString(alertTask?.due_at) ||
    asString(alertTask?.deadline) ||
    asString(payload.due_at) ||
    asString(payload.deadline);
  const dueFact =
    kind === "task_reminder" ? signalTimestampFact("Due", dueAt) : null;
  const queueFact = alertTask
    ? { label: "Queue", value: getQueuePosition(alertTask) }
    : null;
  const workFact = alertTask
    ? {
        label: "Work",
        value: getTaskWorkState(alertTask) || pendingResponseDisplay?.title,
      }
    : pendingResponseDisplay?.title
      ? { label: "Work", value: pendingResponseDisplay.title }
      : null;

  const visibleFacts = [
    dueFact,
    queueFact,
    workFact,
    target ? { label: "Target", value: target } : null,
    sourceLabel ? { label: "Source", value: sourceLabel } : null,
    responseLabel ? { label: "Response", value: responseLabel } : null,
    evidenceLabel ? { label: "Evidence", value: evidenceLabel } : null,
    cycleLabel ? { label: "Cycle", value: cycleLabel } : null,
  ].filter((item): item is SignalFact => Boolean(item?.value));

  return (
    <div className="mt-3">
      <div className="flex flex-wrap gap-2">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold",
            ALERT_BADGE_CLASSNAME,
          )}
        >
          <ShieldAlert className="h-3.5 w-3.5" />
          {titleCase(severity)}
        </span>
        {visibleFacts.map((item) => (
          <span
            key={`${item.label}-${item.value}`}
            title={item.title}
            className={cn(
              "min-w-0 max-w-full rounded-full border px-3 py-1.5 text-xs",
              NEUTRAL_CHIP_CLASSNAME,
            )}
          >
            <span className={NEUTRAL_CHIP_LABEL_CLASSNAME}>{item.label}:</span>{" "}
            <span className={NEUTRAL_CHIP_VALUE_CLASSNAME}>{item.value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function AgentActivityRegion({
  payload,
  pendingResponse,
  pendingResponseDisplay,
}: {
  payload: Record<string, unknown>;
  pendingResponse?: PendingResponseState | null;
  pendingResponseDisplay?: PendingResponseDisplay | null;
}) {
  if (pendingResponseDisplay) {
    const routeLabel =
      pendingResponse?.targetLabel ||
      pendingResponse?.activeAgentLabel ||
      pendingResponse?.targetHandle ||
      "agent";
    return (
      <div
        data-testid="ax-card-activity-monitor"
        className="mt-3 flex max-w-full flex-col gap-2 rounded-2xl border border-cyan-300/25 bg-cyan-400/[0.10] px-3 py-2 text-xs text-cyan-950 dark:text-cyan-50"
        title={pendingResponseDisplay.detail || pendingResponseDisplay.title}
      >
        <div className="flex max-w-full flex-wrap items-center gap-2 uppercase tracking-[0.16em]">
          <span>To {routeLabel}</span>
          <span className="h-1 w-1 rounded-full bg-current opacity-50" />
          <span className="inline-flex min-w-0 items-center gap-1.5 normal-case tracking-normal">
            <CircleDashed className="h-3 w-3 shrink-0 animate-spin" />
            <span className="min-w-0 break-words">
              {pendingResponseDisplay.title}
            </span>
          </span>
        </div>
        {pendingResponseDisplay.signals.length ? (
          <div className="flex max-w-full flex-wrap gap-1.5">
            {pendingResponseDisplay.signals.map((signal) => (
              <span
                key={signal}
                className="min-w-0 max-w-full rounded-full border border-cyan-300/20 bg-white/35 px-2 py-0.5 text-cyan-900 dark:bg-black/15 dark:text-cyan-100"
              >
                {signal}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  const activity = reminderActivitySource(payload);
  const status = activity?.status?.replace(/_/g, " ") || null;
  const actorLabel = activity?.agentName
    ? status === "processing"
      ? `${activity.agentName} is ${status}`
      : status
        ? `${activity.agentName} ${status}`
        : activity.agentName
    : null;

  return (
    <div
      data-testid="ax-card-agent-activity"
      className="mt-3 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900/70 dark:text-slate-300"
    >
      {actorLabel ? (
        <div className="font-medium text-slate-800 dark:text-slate-100">
          {actorLabel}
        </div>
      ) : (
        <div className="font-medium text-slate-800 dark:text-slate-100">
          Agent activity
        </div>
      )}
      <div className="mt-1 break-words text-slate-600 dark:text-slate-300">
        {activity?.summary || "No recent agent activity"}
      </div>
    </div>
  );
}

function TaskCardBody({ payload }: { payload: Record<string, unknown> }) {
  const assignee = asRecord(payload.assignee);
  const taskUrl = asString(payload.url);
  // Per activity-stream-contract rule §4: raw IDs live in Details only —
  // Task ID is intentionally omitted from the visible chip strip. v2 polish
  // adds Status here so it sits next to Priority/Assignee in the same
  // gallery-style row rather than split across SignalContextStrip.
  const metadata = [
    {
      label: "Priority",
      value: asString(payload.priority),
    },
    {
      label: "Assignee",
      value:
        asString(assignee.name) ||
        asString(payload.assignee_name) ||
        asString(payload.assigned_to),
    },
    {
      label: "Status",
      value: asString(payload.status)?.replace(/_/g, " "),
    },
    {
      label: "Work",
      value: getTaskWorkState(payload),
    },
    {
      label: "Queue",
      value: getQueuePosition(payload),
    },
    signalTimestampFact(
      "Reminder",
      asString(payload.reminder_at) ||
        asString(payload.next_reminder_at) ||
        asString(payload.remind_at),
    ),
  ].filter((item): item is SignalFact => Boolean(item?.value));

  return (
    <div className="mt-4 space-y-3">
      {metadata.length ? (
        <div className="flex flex-wrap gap-2">
          {metadata.map((item) => (
            <div
              key={item.label}
              className="min-w-0 max-w-full flex-1 basis-[180px] rounded-2xl border border-gray-200 bg-white px-3 py-2 shadow-sm dark:border-slate-700 dark:bg-slate-900"
            >
              <div className="truncate text-[11px] uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
                {item.label}
              </div>
              <div className="mt-1 break-words overflow-wrap-anywhere text-sm font-medium text-gray-900 dark:text-white">
                {item.value}
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {taskUrl ? (
        <a
          href={taskUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 text-sm font-medium text-gray-700 transition hover:text-gray-900 dark:text-gray-300 dark:hover:text-white"
        >
          Open task
          <ExternalLink className="h-4 w-4" />
        </a>
      ) : null}
    </div>
  );
}

function AgentCardBody({ payload }: { payload: Record<string, unknown> }) {
  const agentRecord = asRecord(payload.agent);
  const chipFacts = [
    {
      label: "Status",
      value: asString(payload.status) || asString(agentRecord.status),
    },
    {
      label: "Lane",
      value: asString(payload.lane) || asString(agentRecord.lane),
    },
    {
      label: "Role",
      value: asString(payload.role) || asString(agentRecord.role),
    },
  ].filter((item): item is { label: string; value: string } =>
    Boolean(item.value),
  );

  const actionHint = asString(payload.action_hint);

  if (!chipFacts.length && !actionHint) return null;

  return (
    <div className="mt-4 space-y-3">
      {chipFacts.length ? (
        <div className="flex flex-wrap gap-2">
          {chipFacts.map((item) => (
            <span
              key={item.label}
              className={cn(
                "min-w-0 max-w-full rounded-full border px-3 py-1.5 text-xs",
                NEUTRAL_CHIP_CLASSNAME,
              )}
            >
              <span className={NEUTRAL_CHIP_LABEL_CLASSNAME}>
                {item.label}:
              </span>{" "}
              <span className={NEUTRAL_CHIP_VALUE_CLASSNAME}>
                {titleCase(item.value)}
              </span>
            </span>
          ))}
        </div>
      ) : null}
      {actionHint ? (
        <div className="text-sm text-gray-800 dark:text-gray-200">
          <span className={NEUTRAL_CHIP_LABEL_CLASSNAME}>Why this agent:</span>{" "}
          {actionHint}
        </div>
      ) : null}
    </div>
  );
}

function ContextCardBody({ payload }: { payload: Record<string, unknown> }) {
  // v2 polish — render content-type / size / storage as labeled fact chips,
  // matching the gallery. Fall back to the legacy scope/source/key strip
  // only when none of the richer fields are present.
  const contentType =
    asString(payload.content_type) ||
    asString(payload.type) ||
    asString(payload.mime);
  const rawSize = payload.size_bytes ?? payload.size;
  const formattedSize =
    typeof rawSize === "number" && Number.isFinite(rawSize)
      ? formatByteSize(rawSize)
      : asString(rawSize);
  const storage = asString(payload.storage) || asString(payload.storage_tier);

  const labeledFacts = [
    contentType
      ? { label: "Type", value: prettyContentType(contentType) }
      : null,
    formattedSize ? { label: "Size", value: formattedSize } : null,
    storage ? { label: "Storage", value: titleCase(storage) } : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));

  const legacyFacts = [
    asString(payload.scope),
    asString(payload.source),
    asString(payload.key),
  ].filter((item): item is string => Boolean(item));

  const references = Array.isArray(payload.references)
    ? payload.references
        .map((item) => asRecord(item))
        .map((item) => ({
          label: asString(item.label) || asString(item.path) || "Reference",
          path: asString(item.path),
        }))
    : [];

  if (!labeledFacts.length && !legacyFacts.length && !references.length) {
    return null;
  }

  return (
    <div className="mt-4 space-y-3">
      {labeledFacts.length ? (
        <div className="flex flex-wrap gap-2">
          {labeledFacts.map((item) => (
            <span
              key={`${item.label}-${item.value}`}
              className={cn(
                "min-w-0 max-w-full rounded-full border px-3 py-1.5 text-xs",
                NEUTRAL_CHIP_CLASSNAME,
              )}
            >
              <span className={NEUTRAL_CHIP_LABEL_CLASSNAME}>
                {item.label}:
              </span>{" "}
              <span className={NEUTRAL_CHIP_VALUE_CLASSNAME}>{item.value}</span>
            </span>
          ))}
        </div>
      ) : null}
      {!labeledFacts.length && legacyFacts.length ? (
        <div className="flex flex-wrap gap-2 text-xs uppercase tracking-[0.18em] text-gray-700 dark:text-gray-300">
          {legacyFacts.map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
      ) : null}
      {references.length ? (
        <div className="space-y-2">
          {references.map((reference) => (
            <div
              key={`${reference.label}-${reference.path || ""}`}
              className="rounded-2xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-gray-200"
            >
              <div className={NEUTRAL_CHIP_VALUE_CLASSNAME}>
                {reference.label}
              </div>
              {reference.path ? (
                <div className="mt-1 font-mono text-xs text-gray-600 dark:text-gray-400">
                  {reference.path}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ResultCardBody({ payload }: { payload: Record<string, unknown> }) {
  const bodyMarkdown = asString(payload.body_markdown);
  const artifacts = Array.isArray(payload.artifacts)
    ? payload.artifacts
        .map((item) => asRecord(item))
        .map((item) => ({
          label: asString(item.label) || asString(item.path) || "Artifact",
          path: asString(item.path),
        }))
    : [];

  return (
    <div className="mt-4 space-y-3">
      {bodyMarkdown ? (
        <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-950/40">
          <div className="prose prose-slate dark:prose-invert max-w-none text-sm leading-6 [&_p]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:bg-slate-900 [&_pre]:p-3 dark:[&_pre]:bg-slate-950 [&_code]:rounded [&_code]:bg-slate-200 [&_code]:px-1 [&_code]:py-0.5 dark:[&_code]:bg-white/10">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ children, href, ...props }) => (
                  <ExternalMarkdownLink
                    href={href}
                    className="text-cyan-700 hover:underline dark:text-cyan-200"
                    {...props}
                  >
                    {children}
                  </ExternalMarkdownLink>
                ),
              }}
            >
              {bodyMarkdown}
            </ReactMarkdown>
          </div>
        </div>
      ) : null}
      {artifacts.length ? (
        <div className="flex flex-wrap gap-2">
          {artifacts.map((artifact) => (
            <div
              key={`${artifact.label}-${artifact.path || ""}`}
              className={cn(
                "rounded-full border px-3 py-1.5 text-xs",
                NEUTRAL_CHIP_CLASSNAME,
              )}
            >
              {artifact.label}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function isSpacePayload(payload: Record<string, unknown>): boolean {
  // Result cards backed by the spaces app — identified either by tool_name
  // or by the presence of space-shaped facts on the payload.
  const toolName = asString(payload.tool_name)?.toLowerCase();
  if (toolName === "spaces" || toolName === "space") return true;
  const resourceUri = asString(payload.resource_uri)?.toLowerCase() || "";
  if (resourceUri.includes("ui://spaces")) return true;
  return (
    payload.agents_count !== undefined ||
    payload.agent_count !== undefined ||
    payload.tasks_open !== undefined ||
    payload.open_tasks !== undefined
  );
}

function SpaceCardBody({ payload }: { payload: Record<string, unknown> }) {
  const agentsCount =
    asNumberishString(payload.agents_count) ??
    asNumberishString(payload.agent_count) ??
    asNumberishString(asRecord(payload.counts).agents);
  const tasksOpen =
    asNumberishString(payload.tasks_open) ??
    asNumberishString(payload.open_tasks) ??
    asNumberishString(asRecord(payload.counts).tasks_open);
  const visibility = asString(payload.visibility) || asString(payload.scope);
  const owner =
    asString(payload.owner_handle) ||
    asString(asRecord(payload.owner).handle) ||
    asString(payload.owner);

  const chips = [
    agentsCount ? { label: "Agents", value: agentsCount } : null,
    tasksOpen ? { label: "Tasks open", value: tasksOpen } : null,
    visibility ? { label: "Visibility", value: titleCase(visibility) } : null,
    owner
      ? { label: "Owner", value: owner.startsWith("@") ? owner : `@${owner}` }
      : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));

  if (!chips.length) return null;

  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {chips.map((item) => (
        <span
          key={item.label}
          className={cn(
            "min-w-0 max-w-full rounded-full border px-3 py-1.5 text-xs",
            NEUTRAL_CHIP_CLASSNAME,
          )}
        >
          <span className={NEUTRAL_CHIP_LABEL_CLASSNAME}>{item.label}:</span>{" "}
          <span className={NEUTRAL_CHIP_VALUE_CLASSNAME}>{item.value}</span>
        </span>
      ))}
    </div>
  );
}

function ReceiptCardBody({ payload }: { payload: Record<string, unknown> }) {
  const icon = asString(payload.icon);
  const Icon =
    icon === "watch"
      ? Clock3
      : icon === "alert"
        ? ShieldAlert
        : icon === "check"
          ? CheckCircle2
          : CircleDashed;

  return (
    <div
      className={cn(
        "mt-4 inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs uppercase tracking-[0.18em]",
        NEUTRAL_CHIP_CLASSNAME,
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      {asString(payload.status)?.replace(/_/g, " ") || "received"}
    </div>
  );
}

function ConfirmationCardBody({
  messageId,
  cardId,
  payload,
  activeActionKey,
  onAction,
}: {
  messageId: string;
  cardId: string;
  payload: Record<string, unknown>;
  activeActionKey?: string | null;
  onAction: (input: CardActionInput) => void;
}) {
  const actionEnabled = asBoolean(payload.action_enabled);
  if (actionEnabled === false) return null;

  const actionId = asString(payload.action_id);
  if (!actionId) return null;

  const confirmActionKey = buildActionKey(
    messageId,
    cardId,
    actionId,
    "confirm",
  );
  const cancelActionKey = buildActionKey(messageId, cardId, actionId, "cancel");

  return (
    <div className="mt-4 flex flex-wrap gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        data-testid="ax-card-confirm-approve"
        onClick={() =>
          onAction({
            actionId,
            cardId,
            choiceId: "confirm",
          })
        }
        disabled={activeActionKey === confirmActionKey}
        className="border-cyan-300/30 bg-cyan-400/12 text-cyan-100 hover:bg-cyan-400/20 hover:text-white"
      >
        {activeActionKey === confirmActionKey
          ? "Working..."
          : asString(payload.confirm_label) || "Confirm"}
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        data-testid="ax-card-confirm-deny"
        onClick={() =>
          onAction({
            actionId,
            cardId,
            choiceId: "cancel",
          })
        }
        disabled={activeActionKey === cancelActionKey}
        className="border-gray-300 bg-transparent text-gray-900 hover:bg-gray-100 hover:text-gray-900 dark:border-slate-600 dark:text-white dark:hover:bg-white/[0.08] dark:hover:text-white"
      >
        {activeActionKey === cancelActionKey
          ? "Working..."
          : asString(payload.cancel_label) || "Cancel"}
      </Button>
    </div>
  );
}

function HandoffSelectCardBody({
  handoffId,
  payload,
  selectedChoiceId,
  isSubmitting,
  onChangeSelection,
  onSubmit,
}: {
  handoffId?: string | null;
  payload: Record<string, unknown>;
  selectedChoiceId?: string;
  isSubmitting: boolean;
  onChangeSelection: (choiceId: string) => void;
  onSubmit: (choiceId: string) => void;
}) {
  const options = Array.isArray(payload.options)
    ? payload.options
        .map((item) => asRecord(item))
        .map((item) => ({
          id: asString(item.id),
          label: asString(item.label),
          description: asString(item.description),
        }))
        .filter(
          (
            item,
          ): item is {
            id: string;
            label: string;
            description: string | null;
          } => Boolean(item.id && item.label),
        )
    : [];
  const hasAction = Boolean(
    asString(payload.action_id) ||
    asString(payload.handoff_action_id) ||
    handoffId,
  );

  if (!options.length) return null;

  return (
    <div className="mt-4 space-y-3">
      <div className="space-y-2">
        {options.map((option) => {
          const selected = option.id === selectedChoiceId;
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => onChangeSelection(option.id)}
              className={cn(
                "w-full rounded-2xl border px-3 py-3 text-left transition",
                selected
                  ? "border-cyan-300 bg-cyan-50 dark:border-cyan-400/40 dark:bg-cyan-400/10"
                  : "border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:border-slate-600 dark:hover:bg-slate-800",
              )}
            >
              <div className="text-sm font-medium text-gray-900 dark:text-white">
                {option.label}
              </div>
              {option.description ? (
                <div className="mt-1 text-sm text-gray-700 dark:text-gray-300">
                  {option.description}
                </div>
              ) : null}
            </button>
          );
        })}
      </div>
      <Button
        type="button"
        size="sm"
        onClick={() => selectedChoiceId && onSubmit(selectedChoiceId)}
        disabled={!selectedChoiceId || !hasAction || isSubmitting}
        className="bg-white text-slate-950 hover:bg-white/90"
      >
        {isSubmitting
          ? "Working..."
          : asString(payload.submit_label) || "Send handoff"}
      </Button>
    </div>
  );
}
