import type { PresenceStatus } from "@/hooks/usePresence";

export type PendingProgressShape = {
  current: number;
  total: number;
  unit: string;
};

export type PendingResponseState = {
  sourceEntryId: string | null;
  targetHandle: string | null;
  targetLabel: string;
  activeAgentLabel: string | null;
  statusLabel: string | null;
  toolName: string | null;
  activity: string | null;
  toolCount: number | null;
  stepLabel: string | null;
  commandLabels: string[];
  progress: PendingProgressShape | null;
  reason: string | null;
  errorMessage: string | null;
  retryAfterSeconds: number | null;
  isKnownTarget: boolean;
  hasPresenceSignal: boolean;
  presenceStatus: PresenceStatus | null;
};

export type PendingResponseDisplay = {
  title: string;
  detail: string | null;
  signals: string[];
};

function normalizeIdentityKey(value: string | null | undefined) {
  return (value || "")
    .trim()
    .replace(/^@/, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

function formatToolLabel(toolName: string) {
  return toolName
    .trim()
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ");
}

function humanizeStatus(status: string) {
  return status
    .trim()
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ");
}

function formatSignalLabel(value: string) {
  return value.trim().replace(/\s+/g, " ");
}

function formatProgressSignal(progress: PendingProgressShape): string | null {
  const current = Number(progress.current);
  const total = Number(progress.total);
  const unit = (progress.unit || "").trim();
  if (!Number.isFinite(current) || !Number.isFinite(total)) return null;
  if (total <= 0) return unit ? `${current} ${unit}` : `${current}`;
  return unit ? `${current}/${total} ${unit}` : `${current}/${total}`;
}

export type AgentPhaseTitleInput = {
  actorLabel: string;
  status: string | null | undefined;
  toolName?: string | null;
  activity?: string | null;
  reason?: string | null;
  errorMessage?: string | null;
  retryAfterSeconds?: number | null;
  // Optional routing hint — when provided the "queued" phase prefers
  // "Queued for <routedLabel>" instead of "<actorLabel> queued by Gateway"
  // if no active actor has claimed the work yet.
  routedLabel?: string | null;
  hasActiveActor?: boolean;
};

/**
 * Maps a status phase + optional progress fields to a human-readable title.
 * Used by both the ephemeral pending bubble and the streaming-placeholder
 * renderer so Gateway-emitted phases render consistently wherever they
 * surface in the UI.
 */
export function computeAgentPhaseTitle(input: AgentPhaseTitleInput): string {
  const { actorLabel, toolName, activity, reason, errorMessage } = input;
  const normalizedStatus = (input.status || "waiting").toLowerCase();
  const routedLabel = input.routedLabel || actorLabel;
  const hasActiveActor =
    typeof input.hasActiveActor === "boolean"
      ? input.hasActiveActor
      : Boolean(actorLabel);
  const activityText = activity ? activity.trim() : null;

  if (normalizedStatus === "error") {
    const msg = (errorMessage || reason || "").trim();
    return msg
      ? `${actorLabel} hit an error: ${msg}`
      : `${actorLabel} hit an error`;
  }
  if (normalizedStatus === "rate_limited") {
    const retry =
      typeof input.retryAfterSeconds === "number" && input.retryAfterSeconds > 0
        ? `, retrying in ${input.retryAfterSeconds}s`
        : "";
    return `${actorLabel} rate limited${retry}`;
  }
  if (normalizedStatus === "tool_use" || normalizedStatus === "tool_call") {
    const toolLabel = toolName ? formatToolLabel(toolName) : null;
    if (toolLabel && activityText) {
      return `${actorLabel}: ${activityText}`;
    }
    if (toolLabel) {
      return `${actorLabel} is using ${toolLabel}...`;
    }
    if (activityText) {
      return `${actorLabel}: ${activityText}`;
    }
    return `${actorLabel} is using tools...`;
  }
  if (normalizedStatus === "tool_complete") {
    return `${actorLabel} is working`;
  }
  if (normalizedStatus === "streaming") {
    return `${actorLabel} is responding`;
  }
  if (normalizedStatus === "accepted") {
    return `${actorLabel} picked it up`;
  }
  if (normalizedStatus === "started" || normalizedStatus === "claimed") {
    return activityText
      ? `${actorLabel}: ${activityText}`
      : `${actorLabel} started working`;
  }
  if (
    normalizedStatus === "processing" ||
    normalizedStatus === "thinking" ||
    normalizedStatus === "working" ||
    normalizedStatus === "forwarded"
  ) {
    return activityText
      ? `${actorLabel}: ${activityText}`
      : `${actorLabel} is working`;
  }
  if (normalizedStatus === "queued") {
    return hasActiveActor
      ? `${actorLabel} queued by Gateway`
      : `Queued for ${routedLabel}`;
  }
  if (
    normalizedStatus === "waiting" ||
    normalizedStatus === "queued locally" ||
    normalizedStatus === "sending"
  ) {
    return `Waiting for ${routedLabel}`;
  }
  return `${actorLabel} is ${humanizeStatus(normalizedStatus)}`;
}

export function getPendingResponseDisplay(
  state: PendingResponseState | null,
): PendingResponseDisplay | null {
  if (!state) return null;

  const routedLabel = state.targetLabel || state.activeAgentLabel || null;
  const actorLabel = state.activeAgentLabel || routedLabel;

  // No confirmed target and no active agent — don't show a misleading bubble.
  if (!routedLabel) return null;
  const sameActor =
    normalizeIdentityKey(actorLabel) === normalizeIdentityKey(routedLabel);

  const title = computeAgentPhaseTitle({
    actorLabel: actorLabel || routedLabel,
    status: state.statusLabel,
    toolName: state.toolName,
    activity: state.activity,
    reason: state.reason,
    errorMessage: state.errorMessage,
    retryAfterSeconds: state.retryAfterSeconds,
    routedLabel,
    hasActiveActor: Boolean(state.activeAgentLabel),
  });

  const signals: string[] = [];
  if (typeof state.toolCount === "number" && state.toolCount > 0) {
    signals.push(
      `${state.toolCount} tool${state.toolCount === 1 ? "" : "s"} active`,
    );
  }
  if (state.progress) {
    const progressSignal = formatProgressSignal(state.progress);
    if (progressSignal) signals.push(progressSignal);
  }
  if (state.stepLabel) {
    signals.push(formatSignalLabel(state.stepLabel));
  }
  for (const commandLabel of state.commandLabels || []) {
    const formatted = formatSignalLabel(commandLabel);
    if (!formatted) continue;
    signals.push(formatted);
  }

  // Keep the inline pending chip to a single stable line. Space/presence
  // availability hints were often stale for routed agents and caused the
  // message box to jump when they briefly appeared then disappeared.
  if (!state.isKnownTarget) return { title, detail: null, signals };

  if (
    state.hasPresenceSignal &&
    state.presenceStatus === "offline" &&
    !state.activeAgentLabel
  ) {
    return { title, detail: null, signals };
  }

  if (!sameActor && state.activeAgentLabel) {
    return { title, detail: null, signals };
  }

  return { title, detail: null, signals };
}
