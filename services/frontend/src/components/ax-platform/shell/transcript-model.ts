import {
  type SpaceAgentNormalizedSurface,
  getSurfaceCount,
  normalizeSpaceAgentSurfaces,
} from "@/lib/space-agent-surfaces";
import type {
  SpaceAgentConversationCard,
  SpaceAgentMessage,
  SpaceAgentSendReceipt,
} from "@/lib/space-agent-api";
import {
  stripModelArtifacts,
  type StreamingState,
  type StreamTiming,
} from "@/hooks/useStreamBuffer";

/**
 * Strip `{"ax_intel": ...}` JSON blocks from message content.
 * These are internal routing metadata that should never be visible to users.
 * The backend strips them before saving, but during streaming they may leak
 * through before the cleaned `message_updated` event arrives.
 */
const AX_INTEL_PATTERN = /\s*\{["\s]*ax_intel["\s]*:[\s\S]*\}\s*$/;

function stripAxIntel(content: string): string {
  return content.replace(AX_INTEL_PATTERN, "").trimEnd();
}

function isWorkingProgressContent(content: string) {
  return /^Working[.…]/i.test(content.trim());
}

const PENDING_STREAMING_REPLY_STATUSES = new Set([
  "accepted",
  "claimed",
  "forwarded",
  "processing",
  "queued",
  "started",
  "streaming",
  "thinking",
  "tool_call",
  "tool_progress",
  "tool_use",
  "working",
]);

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asPositiveNumber(value: unknown): number | null {
  const numeric =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function extractLeadingMentionHandle(content?: string | null) {
  if (!content) return null;

  const match = content
    .trimStart()
    .match(/^@([a-z0-9][a-z0-9_-]{0,63})(?=\s|$)/i);
  return match?.[1] || null;
}

function mergeMessageMetadata(
  metadata: Record<string, unknown> | null,
  messageMetadata: Record<string, unknown> | null,
): Record<string, unknown> | null {
  if (!metadata && !messageMetadata) return null;

  const merged: Record<string, unknown> = {
    ...(messageMetadata || {}),
    ...(metadata || {}),
  };

  const primaryUi = asRecord(metadata?.ui);
  const secondaryUi = asRecord(messageMetadata?.ui);
  if (!primaryUi && !secondaryUi) return merged;

  const mergedUi: Record<string, unknown> = {
    ...(secondaryUi || {}),
    ...(primaryUi || {}),
  };
  const primarySignals = asRecord(primaryUi?.signals);
  const secondarySignals = asRecord(secondaryUi?.signals);
  if (primarySignals || secondarySignals) {
    mergedUi.signals = {
      ...(secondarySignals || {}),
      ...(primarySignals || {}),
    };
  }

  merged.ui = mergedUi;
  return merged;
}

function getRoutingTargetLabel(
  routing: SpaceAgentMessage["routing"] | SpaceAgentSendReceipt["routing"],
  metadata: Record<string, unknown> | null,
) {
  const directTarget = asString(routing?.target_name);
  if (directTarget) return directTarget;

  const metadataRouting = asRecord(metadata?.routing);
  const metadataTarget = asString(metadataRouting?.target_name);
  if (metadataTarget) return metadataTarget;

  const routingStory = asRecord(metadata?.routing_story);
  const targets = Array.isArray(routingStory?.targets)
    ? routingStory.targets
    : [];

  for (const rawTarget of targets) {
    const target = asRecord(rawTarget);
    if (!target) continue;

    const candidate =
      asString(target.target_name) ||
      asString(target.agent_name) ||
      asString(target.handle) ||
      asString(target.display_name) ||
      asString(target.name);

    if (candidate) return candidate;
  }

  return null;
}

function getRoutingModeLabel(
  routing: SpaceAgentMessage["routing"] | SpaceAgentSendReceipt["routing"],
  metadata: Record<string, unknown> | null,
) {
  return asString(routing?.mode) || asString(asRecord(metadata?.routing)?.mode);
}

function getEntryProcessingState(metadata: Record<string, unknown> | null) {
  const ui = asRecord(metadata?.ui);
  const widget = asRecord(ui?.widget);
  const processing = asRecord(metadata?.processing);
  const agentProcessing = asRecord(metadata?.agent_processing);
  const status =
    asString(processing?.status) ||
    asString(processing?.state) ||
    asString(processing?.lifecycle) ||
    asString(agentProcessing?.status) ||
    asString(agentProcessing?.state) ||
    asString(agentProcessing?.phase) ||
    asString(widget?.status) ||
    asString(widget?.lifecycle);
  const toolName =
    asString(processing?.tool_name) ||
    asString(agentProcessing?.tool_name) ||
    asString(widget?.tool_name);
  const activity =
    asString(processing?.activity) ||
    asString(processing?.message) ||
    asString(agentProcessing?.activity) ||
    asString(agentProcessing?.message) ||
    asString(widget?.activity);
  return { statusLabel: status, toolName, activity };
}

export type ChatEntryAttachment = {
  name: string;
  id?: string;
  url?: string;
  contentType?: string;
  sizeBytes?: number;
  contextKey?: string;
};

export type ChatEntry = {
  id: string;
  role: "user" | "agent";
  meta: string;
  content: string;
  fromHandle?: string | null;
  messageType?: string | null;
  metadata?: Record<string, unknown> | null;
  pauseReason?: string | null;
  pauseReasonText?: string | null;
  pauseDuration?: number | null;
  pauseExpiresAt?: string | null;
  pauseEmoji?: string | null;
  aiSummary?: string | null;
  summarizedAt?: string | null;
  attachments?: ChatEntryAttachment[];
  surfaces?: SpaceAgentNormalizedSurface[];
  surfaceCount?: number;
  createdAt?: string | null;
  fromLabel?: string | null;
  toLabel?: string | null;
  statusLabel?: string | null;
  toolName?: string | null;
  activity?: string | null;
  hasProcessingState?: boolean;
  progress?: {
    current: number;
    total: number;
    unit: string;
  } | null;
  reason?: string | null;
  errorMessage?: string | null;
  retryAfterSeconds?: number | null;
  messageCreatedAt?: string | null;
  conversationId?: string | null;
  parentId?: string | null;
  replyToLabel?: string | null;
  replyToContent?: string | null;
  isStreaming?: boolean;
};

export type ConversationGroup = {
  id: string;
  entries: ChatEntry[];
  latestEntry: ChatEntry;
  replyCount: number;
  participants: string[];
};

export type AttachedNoReplyIndicator = {
  id: string;
  parentId: string;
  createdAt?: string | null;
  agentLabel: string;
  pauseReasonText?: string | null;
  pauseEmoji?: string | null;
  tone?: "ack" | "quiet" | null;
};

export type NoReplySignalPayload = {
  id?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  message_id?: string | null;
  reason?: string | null;
  reason_code?: string | null;
  signal_kind?: string | null;
  detail_reason_text?: string | null;
  emoji?: string | null;
  created_at?: string | null;
};

export { getSurfaceCount };

// A fresh workspace has no fabricated agent messages or liveness claims.
export function buildSeedTranscript(_agentName: string): ChatEntry[] { return []; }

export function isUserSenderType(senderType?: string | null) {
  return senderType === "user" || senderType === "human";
}

export function normalizeUserHandle(value?: string | null): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.replace(/^@/, "").trim();
  if (!trimmed) return undefined;
  if (trimmed === "Space Agent") return undefined;
  return trimmed;
}

export function mapLiveMessageToEntry(
  message: SpaceAgentMessage,
  agentName: string,
  enrichFn?: (msg: SpaceAgentMessage) => SpaceAgentMessage,
  hiddenWidgets?: Set<string>,
): ChatEntry {
  const enrichedMessage = enrichFn ? enrichFn(message) : message;
  const surfaces = normalizeSpaceAgentSurfaces(
    enrichedMessage,
    enrichedMessage.id,
    hiddenWidgets,
  );
  const senderType = isUserSenderType(message.sender_type)
    ? "user"
    : message.sender_type;
  const metadata = mergeMessageMetadata(
    asRecord(message.metadata),
    asRecord(message.message_metadata),
  );
  const content = stripAxIntel(message.content || "");
  const displayCreatedAt =
    asRecord(metadata?.streaming_reply)?.enabled === true &&
    !isPendingStreamingReplyContent(metadata, content)
      ? message.updated_at || message.created_at || null
      : message.created_at || null;
  const fromLabel =
    message.display_name === "Space Agent"
      ? agentName
      : message.display_name || (senderType === "user" ? "You" : agentName);
  const routedTargetLabel =
    getRoutingTargetLabel(message.routing, metadata) ||
    extractLeadingMentionHandle(message.content || "");
  const {
    statusLabel: processingStatus,
    toolName,
    activity,
  } = getEntryProcessingState(metadata);
  const rawAttachments = Array.isArray(message.attachments)
    ? message.attachments
    : Array.isArray(message.accepted_attachments)
      ? message.accepted_attachments
      : Array.isArray(metadata?.accepted_attachments)
        ? (metadata.accepted_attachments as unknown[])
        : [];

  const fromHandle =
    senderType === "user"
      ? normalizeUserHandle(message.display_name)
      : undefined;

  return {
    id: message.id,
    role: senderType === "user" ? "user" : "agent",
    meta: fromLabel,
    content,
    fromHandle,
    messageType: message.message_type || null,
    metadata,
    pauseReason:
      message.pause_reason ||
      asString(metadata?.pause_reason) ||
      asString(metadata?.reason),
    pauseReasonText:
      message.pause_reason_text ||
      asString(metadata?.pause_reason_text) ||
      asString(metadata?.reason_text),
    pauseDuration:
      asPositiveNumber(message.pause_duration) ??
      asPositiveNumber(metadata?.pause_duration),
    pauseExpiresAt:
      message.pause_expires_at || asString(metadata?.pause_expires_at),
    pauseEmoji:
      message.pause_emoji ||
      asString(metadata?.pause_emoji) ||
      asString(metadata?.emoji),
    aiSummary: message.ai_summary || null,
    summarizedAt: message.summarized_at || null,
    surfaces,
    surfaceCount: getSurfaceCount(surfaces),
    createdAt: displayCreatedAt,
    fromLabel,
    toLabel: routedTargetLabel,
    statusLabel:
      processingStatus || getRoutingModeLabel(message.routing, metadata),
    toolName: toolName || null,
    activity: activity || null,
    hasProcessingState: Boolean(processingStatus),
    messageCreatedAt: message.created_at || null,
    conversationId: message.conversation_id || null,
    parentId: message.parent_id || null,
    replyToLabel: null,
    replyToContent: null,
    attachments: rawAttachments.length
      ? rawAttachments
          .map((raw): ChatEntryAttachment | null => {
            const attachment = raw as Record<string, unknown> | null;
            if (!attachment) return null;
            const name =
              typeof attachment.name === "string"
                ? attachment.name
                : typeof attachment.filename === "string"
                  ? attachment.filename
                  : typeof attachment.file_id === "string"
                    ? attachment.file_id
                    : null;
            if (!name) return null;
            return {
              name,
              id:
                (typeof attachment.id === "string"
                  ? attachment.id
                  : undefined) ||
                (typeof attachment.file_id === "string"
                  ? attachment.file_id
                  : undefined),
              url:
                typeof attachment.url === "string" ? attachment.url : undefined,
              contentType:
                typeof attachment.content_type === "string"
                  ? attachment.content_type
                  : typeof attachment.contentType === "string"
                    ? attachment.contentType
                    : undefined,
              sizeBytes:
                typeof attachment.size_bytes === "number"
                  ? attachment.size_bytes
                  : typeof attachment.sizeBytes === "number"
                    ? attachment.sizeBytes
                    : undefined,
              contextKey:
                typeof attachment.context_key === "string"
                  ? attachment.context_key
                  : typeof attachment.contextKey === "string"
                    ? attachment.contextKey
                    : undefined,
            };
          })
          .filter((value): value is ChatEntryAttachment => Boolean(value))
      : [],
  };
}

export function mapStreamingEntryToEntry(
  streamingEntry: NonNullable<StreamingState>,
): ChatEntry {
  return {
    id: streamingEntry.id,
    role: "agent",
    meta: streamingEntry.agentName,
    content: stripAxIntel(streamingEntry.content),
    messageType: "message",
    metadata: null,
    pauseReason: null,
    pauseReasonText: null,
    pauseDuration: null,
    pauseExpiresAt: null,
    pauseEmoji: null,
    aiSummary: null,
    summarizedAt: null,
    attachments: [],
    surfaces: [],
    surfaceCount: 0,
    createdAt: new Date().toISOString(),
    fromLabel: streamingEntry.agentName,
    toLabel: null,
    statusLabel: streamingEntry.statusLabel || "streaming",
    toolName: streamingEntry.toolName ?? null,
    activity: streamingEntry.activity ?? null,
    progress: streamingEntry.progress ?? null,
    reason: streamingEntry.reason ?? null,
    errorMessage: streamingEntry.errorMessage ?? null,
    retryAfterSeconds: streamingEntry.retryAfterSeconds ?? null,
    conversationId: null,
    parentId: streamingEntry.parentId ?? null,
    replyToLabel: null,
    replyToContent: null,
    isStreaming: true,
  };
}

export function mapSendReceiptToEntry(
  receipt: SpaceAgentSendReceipt,
  username: string | null | undefined,
  agentName: string,
  hiddenWidgets?: Set<string>,
): ChatEntry {
  const id = receipt.id || receipt.message_id || `receipt-${Date.now()}`;
  const surfaces = normalizeSpaceAgentSurfaces(receipt, id, hiddenWidgets);
  const senderType = isUserSenderType(receipt.sender_type)
    ? "user"
    : receipt.sender_type;
  const fromLabel =
    (receipt.display_name === "Space Agent"
      ? agentName
      : receipt.display_name) ||
    (senderType === "user" ? (username ? `You · ${username}` : "You") : null);
  const metadata = mergeMessageMetadata(
    asRecord(receipt.metadata),
    asRecord(receipt.message_metadata),
  );
  const {
    statusLabel: receiptStatus,
    toolName: receiptToolName,
    activity: receiptActivity,
  } = getEntryProcessingState(metadata);
  const routedTargetLabel =
    getRoutingTargetLabel(receipt.routing, metadata) ||
    extractLeadingMentionHandle(receipt.content || "");

  const fromHandle =
    senderType === "user"
      ? (normalizeUserHandle(receipt.display_name) ??
        normalizeUserHandle(username))
      : undefined;

  return {
    id,
    role: senderType === "user" ? "user" : "agent",
    meta: fromLabel || (username ? `You · ${username}` : "You"),
    content: stripAxIntel(receipt.content || ""),
    fromHandle,
    messageType: receipt.message_type || null,
    metadata,
    pauseReason:
      receipt.pause_reason ||
      asString(metadata?.pause_reason) ||
      asString(metadata?.reason),
    pauseReasonText:
      receipt.pause_reason_text ||
      asString(metadata?.pause_reason_text) ||
      asString(metadata?.reason_text),
    pauseDuration:
      asPositiveNumber(receipt.pause_duration) ??
      asPositiveNumber(metadata?.pause_duration),
    pauseExpiresAt:
      receipt.pause_expires_at || asString(metadata?.pause_expires_at),
    pauseEmoji:
      receipt.pause_emoji ||
      asString(metadata?.pause_emoji) ||
      asString(metadata?.emoji),
    aiSummary: receipt.ai_summary || null,
    summarizedAt: receipt.summarized_at || null,
    surfaces,
    surfaceCount: getSurfaceCount(surfaces),
    createdAt: receipt.created_at || null,
    fromLabel: fromLabel || null,
    toLabel: routedTargetLabel,
    statusLabel:
      receiptStatus || getRoutingModeLabel(receipt.routing, metadata),
    toolName: receiptToolName || null,
    activity: receiptActivity || null,
    hasProcessingState: Boolean(receiptStatus),
    messageCreatedAt: receipt.created_at || null,
    conversationId: receipt.conversation_id || null,
    parentId: receipt.parent_id || null,
    replyToLabel: null,
    replyToContent: null,
  };
}

function normalizeComparableContent(value?: string | null) {
  return stripModelArtifacts(value || "")
    .replace(/\s+/g, " ")
    .trim();
}

export function mergeEntries(
  liveEntries: ChatEntry[],
  pendingEntries: ChatEntry[],
): ChatEntry[] {
  const ids = new Set(liveEntries.map((entry) => entry.id));
  const mergedPending = pendingEntries.filter((entry) => !ids.has(entry.id));
  return [...liveEntries, ...mergedPending];
}

export function isPendingStreamingReplyEntry(entry: ChatEntry) {
  if (entry.role !== "agent" || !entry.parentId) return false;

  const streamingReply = asRecord(entry.metadata?.streaming_reply);
  if (!streamingReply) return false;
  if (streamingReply.enabled !== true || streamingReply.final === true) {
    return false;
  }

  const processing =
    asRecord(entry.metadata?.processing) ||
    asRecord(entry.metadata?.agent_processing);
  const status = (
    asString(processing?.status) ||
    asString(processing?.state) ||
    asString(processing?.phase) ||
    ""
  ).toLowerCase();

  return (
    isWorkingProgressContent(entry.content) ||
    PENDING_STREAMING_REPLY_STATUSES.has(status)
  );
}

function isPendingStreamingReplyContent(
  metadata: Record<string, unknown> | null,
  content: string,
) {
  const streamingReply = asRecord(metadata?.streaming_reply);
  if (!streamingReply) return false;
  if (streamingReply.enabled !== true || streamingReply.final === true) {
    return false;
  }
  const processing =
    asRecord(metadata?.processing) || asRecord(metadata?.agent_processing);
  const status = (
    asString(processing?.status) ||
    asString(processing?.state) ||
    asString(processing?.phase) ||
    ""
  ).toLowerCase();
  return (
    isWorkingProgressContent(content) ||
    PENDING_STREAMING_REPLY_STATUSES.has(status)
  );
}

export function shouldHidePendingStreamingReplyEntry({
  entry,
  entryLookup,
  hiddenEntryIds,
}: {
  entry: ChatEntry;
  entryLookup: Map<string, ChatEntry>;
  hiddenEntryIds: Set<string>;
}) {
  if (!isPendingStreamingReplyEntry(entry)) return false;
  if (!entry.parentId) return false;

  return entryLookup.has(entry.parentId) && !hiddenEntryIds.has(entry.parentId);
}

function getMessageCreatedAtMs(entry: ChatEntry) {
  return getTimestampMs(entry.messageCreatedAt || entry.createdAt);
}

export function buildPendingStreamingReplyByParentId(
  displayEntries: ChatEntry[],
) {
  const latestTerminalReplyMsByParentId = new Map<string, number>();

  for (const entry of displayEntries) {
    if (entry.role !== "agent" || !entry.parentId) continue;
    if (entry.messageType === "agent_pause") continue;
    if (isPendingStreamingReplyEntry(entry)) continue;

    latestTerminalReplyMsByParentId.set(
      entry.parentId,
      Math.max(
        latestTerminalReplyMsByParentId.get(entry.parentId) || 0,
        getMessageCreatedAtMs(entry),
      ),
    );
  }

  const byParentId = new Map<string, ChatEntry>();

  for (const entry of displayEntries) {
    if (!isPendingStreamingReplyEntry(entry) || !entry.parentId) continue;

    const terminalReplyMs =
      latestTerminalReplyMsByParentId.get(entry.parentId) || 0;
    const pendingMs = getMessageCreatedAtMs(entry);
    if (terminalReplyMs && (!pendingMs || terminalReplyMs >= pendingMs)) {
      continue;
    }

    byParentId.set(entry.parentId, entry);
  }

  return byParentId;
}

export function isSignalOnlyTranscriptEntry(
  entry: Pick<ChatEntry, "metadata" | "surfaceCount">,
) {
  if (!entry.surfaceCount) return false;

  const metadata = asRecord(entry.metadata);
  const appSignal = asRecord(metadata?.app_signal);
  if (!appSignal) return false;

  return (
    metadata?.signal_only === true ||
    appSignal.signal_only === true ||
    asString(appSignal.source) === "axctl_apps_signal"
  );
}

export function shouldRenderActivityEntryAsSurfaceOnly(
  entry: Pick<ChatEntry, "messageType" | "surfaceCount" | "surfaces">,
) {
  const messageType = (entry.messageType || "").toLowerCase();
  if (messageType !== "alert" && messageType !== "reminder") return false;
  if (!entry.surfaceCount || !entry.surfaces?.length) return false;

  return entry.surfaces.some(
    (surface) =>
      surface.kind === "cards" &&
      surface.cards.some((card) => card.type === "alert"),
  );
}

const QUIET_EXIT_REASON_CODES = new Set([
  "no_reply",
  "no_reply_requested",
  "user_requested_silence",
  "not_best_fit",
  "ack",
]);

export function getCompactPauseTone(
  entry: Pick<
    ChatEntry,
    "messageType" | "pauseReason" | "pauseReasonText" | "content" | "metadata"
  >,
): "ack" | "quiet" | null {
  if (entry.messageType !== "agent_pause") return null;

  const normalizedReason = (entry.pauseReason || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
  if (normalizedReason === "ack") return "ack";
  if (QUIET_EXIT_REASON_CODES.has(normalizedReason)) return "quiet";

  const metadata = asRecord(entry.metadata);
  const signalKind = (asString(metadata?.signal_kind) || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
  if (signalKind === "ack") return "ack";
  if (QUIET_EXIT_REASON_CODES.has(signalKind)) return "quiet";

  const pauseText = `${entry.pauseReasonText || ""} ${entry.content || ""}`
    .trim()
    .toLowerCase();
  if (
    pauseText.includes("chose not to reply") ||
    pauseText.includes("chose not to respond") ||
    pauseText.includes("no reply")
  ) {
    return "quiet";
  }

  return null;
}

export function isNoReplyPauseEntry(entry: ChatEntry) {
  return getCompactPauseTone(entry) !== null;
}

export function buildAttachedNoReplyIndicators(
  displayEntries: ChatEntry[],
  entryLookup: Map<string, ChatEntry>,
) {
  const byParentId = new Map<string, Map<string, AttachedNoReplyIndicator>>();

  const attachIndicator = (
    parentId: string,
    indicator: AttachedNoReplyIndicator,
  ) => {
    const existing = byParentId.get(parentId) || new Map();
    existing.set(indicator.id, indicator);
    byParentId.set(parentId, existing);
  };

  for (const entry of displayEntries) {
    const metadata = asRecord(entry.metadata);
    const ui = asRecord(metadata?.ui);
    const signals = asRecord(ui?.signals);
    const storedSignals = Array.isArray(signals?.agent_skipped)
      ? signals.agent_skipped
      : [];

    for (const rawSignal of storedSignals) {
      const signal = asRecord(rawSignal);
      if (!signal) continue;

      const reasonCode = (asString(signal.reason_code) || "")
        .toLowerCase()
        .replace(/\s+/g, "_");
      const signalKind = (asString(signal.signal_kind) || "")
        .toLowerCase()
        .replace(/\s+/g, "_");
      const reasonText =
        asString(signal.reason) || asString(signal.detail_reason_text);
      const normalizedReasonText = (reasonText || "").toLowerCase();
      const looksLikeNoReply =
        QUIET_EXIT_REASON_CODES.has(reasonCode) ||
        QUIET_EXIT_REASON_CODES.has(signalKind) ||
        normalizedReasonText.includes("chose not to reply") ||
        normalizedReasonText.includes("chose not to respond") ||
        normalizedReasonText.includes("no reply");

      if (!looksLikeNoReply) continue;

      const agentLabel = asString(signal.agent_name) || entry.fromLabel || "Commonflame";
      const signalKey =
        asString(signal.agent_id) ||
        agentLabel.toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
      attachIndicator(entry.id, {
        id: asString(signal.id) || `signal-no-reply:${entry.id}:${signalKey}`,
        parentId: entry.id,
        createdAt: asString(signal.created_at) || entry.createdAt || null,
        agentLabel,
        pauseReasonText: reasonText || "no reply",
        pauseEmoji: asString(signal.emoji) || null,
        tone:
          reasonCode === "ack" || signalKind === "ack"
            ? "ack"
            : QUIET_EXIT_REASON_CODES.has(reasonCode) ||
                QUIET_EXIT_REASON_CODES.has(signalKind)
              ? "quiet"
              : null,
      });
    }
  }

  for (const entry of displayEntries) {
    if (!isNoReplyPauseEntry(entry) || !entry.parentId) continue;
    if (!entryLookup.has(entry.parentId)) continue;

    const next: AttachedNoReplyIndicator = {
      id: entry.id,
      parentId: entry.parentId,
      createdAt: entry.createdAt,
      agentLabel: entry.fromLabel || entry.meta,
      pauseReasonText: entry.pauseReasonText || entry.content || null,
      pauseEmoji: entry.pauseEmoji || null,
      tone: getCompactPauseTone(entry),
    };
    attachIndicator(entry.parentId, next);
  }

  return new Map(
    Array.from(byParentId.entries()).map(([parentId, indicators]) => [
      parentId,
      Array.from(indicators.values()).sort((a, b) =>
        (a.createdAt || "").localeCompare(b.createdAt || ""),
      ),
    ]),
  );
}

export function buildNoReplySignalEntry(
  signal: NoReplySignalPayload,
  fallbackAgentName: string,
): ChatEntry | null {
  const parentId = asString(signal.message_id);
  if (!parentId) return null;

  const reasonCode = (asString(signal.reason_code) || "")
    .toLowerCase()
    .replace(/\s+/g, "_");
  const reasonText = asString(signal.reason);
  const normalizedReasonText = (reasonText || "").toLowerCase();

  const looksLikeNoReply =
    QUIET_EXIT_REASON_CODES.has(reasonCode) ||
    normalizedReasonText.includes("chose not to reply") ||
    normalizedReasonText.includes("chose not to respond") ||
    normalizedReasonText.includes("no reply");

  if (!looksLikeNoReply) return null;

  const agentLabel = asString(signal.agent_name) || fallbackAgentName;
  const signalKey =
    asString(signal.agent_id) ||
    agentLabel.toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  const createdAt = asString(signal.created_at) || new Date().toISOString();

  return {
    id: asString(signal.id) || `signal-no-reply:${parentId}:${signalKey}`,
    role: "agent",
    meta: agentLabel,
    fromLabel: agentLabel,
    content: reasonText || "no reply",
    messageType: "agent_pause",
    pauseReason: reasonCode || "no_reply",
    pauseReasonText: reasonText || "no reply",
    pauseEmoji: asString(signal.emoji) || null,
    createdAt,
    parentId,
    conversationId: parentId,
    replyToLabel: null,
    replyToContent: null,
    attachments: [],
    surfaces: [],
    surfaceCount: 0,
  };
}

export function parseMessageDate(value?: string | null) {
  if (!value) return null;
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return null;
  return date;
}

export function formatAbsoluteTimestamp(value?: string | null) {
  const date = parseMessageDate(value);
  if (!date) return null;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function formatTimestamp(value?: string | null) {
  const date = parseMessageDate(value);
  if (!date) return null;

  const diffMs = date.getTime() - Date.now();
  const absMs = Math.abs(diffMs);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

  if (absMs < minute) return "just now";
  if (absMs < hour) return rtf.format(Math.round(diffMs / minute), "minute");
  if (absMs < day) return rtf.format(Math.round(diffMs / hour), "hour");
  return rtf.format(Math.round(diffMs / day), "day");
}

export function getTimestampMs(value?: string | null) {
  return parseMessageDate(value)?.getTime() || 0;
}

export function hasLandedAgentReply(
  entries: ChatEntry[],
  streamingEntry: NonNullable<StreamingState> | null,
  streamTiming: StreamTiming,
) {
  if (!streamingEntry) return false;

  const normalizedStreamingContent = normalizeComparableContent(
    streamingEntry.content,
  );
  const activeParentId = streamingEntry.parentId || null;
  const streamStartedAt =
    streamTiming.firstDeltaAt ||
    streamTiming.firstAgentProcessingAt ||
    streamTiming.sendStartedAt ||
    0;

  return entries.some((entry) => {
    if (
      entry.role !== "agent" ||
      entry.isStreaming ||
      entry.id === "seed-agent"
    ) {
      return false;
    }

    if (entry.id === streamingEntry.id) return true;

    if (activeParentId) {
      if (entry.parentId === activeParentId) return true;

      if (!normalizedStreamingContent) return false;

      const entryTimestamp = getTimestampMs(entry.createdAt);
      return Boolean(
        streamStartedAt &&
        entryTimestamp &&
        entryTimestamp >= streamStartedAt - 1000 &&
        normalizeComparableContent(entry.content) ===
          normalizedStreamingContent,
      );
    }

    if (
      normalizedStreamingContent &&
      normalizeComparableContent(entry.content) === normalizedStreamingContent
    ) {
      return true;
    }

    const entryTimestamp = getTimestampMs(entry.createdAt);
    return Boolean(
      streamStartedAt &&
      entryTimestamp &&
      entryTimestamp >= streamStartedAt - 1000,
    );
  });
}

export function sanitizeAiSummary(value?: string | null) {
  if (!value) return "";

  return value
    .trim()
    .replace(/^\s*@[\w-]+\s+summary:\s*/i, "")
    .replace(/^\s*summary:\s*/i, "")
    .replace(/^\s*\d+\s*-\s*word\s+[a-z][a-z\s_-]*?\s+from\s+[^:]+:\s*/i, "")
    .replace(/^\s*\d+\s*word\s+[a-z][a-z\s_-]*?:\s*/i, "")
    .replace(/^\s*team discussion from [^:]+:\s*/i, "")
    .replace(/\s*\.\.\.\s*$/, "")
    .trim();
}

export function getConversationCardThreadId(
  card?: SpaceAgentConversationCard | null,
) {
  return card?.root_message_id || card?.conversation_id || card?.id || null;
}

export function getConversationCardSummary(
  card?: SpaceAgentConversationCard | null,
) {
  return sanitizeAiSummary(card?.summary);
}

export function getConversationCardParticipants(
  card?: SpaceAgentConversationCard | null,
) {
  const participants = Array.isArray(card?.participants)
    ? card.participants
    : [];

  return Array.from(
    new Set(
      participants
        .map((participant) => {
          if (typeof participant === "string") return participant;
          if (!participant || typeof participant !== "object") return null;
          return (
            participant.display_name ||
            participant.name ||
            participant.handle ||
            participant.id ||
            null
          );
        })
        .filter((value): value is string => Boolean(value)),
    ),
  );
}

export function mergeOrderedLabels(
  ...groups: Array<Array<string | null | undefined>>
) {
  const seen = new Set<string>();
  const merged: string[] = [];

  for (const group of groups) {
    for (const value of group) {
      if (!value) continue;
      const trimmed = value.trim();
      if (!trimmed) continue;
      const key = trimmed.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(trimmed);
    }
  }

  return merged;
}

export function extractMentionHandles(value?: string | null) {
  if (!value) return [];

  return Array.from(
    new Set(
      Array.from(
        value.matchAll(/@([a-zA-Z0-9_-]+)/g),
        (match) => match[1]?.toLowerCase() || null,
      ).filter((handle): handle is string => Boolean(handle)),
    ),
  );
}

export function getConversationCardMentions(
  metadata?: Record<string, unknown> | null,
) {
  if (!metadata || typeof metadata !== "object") return [];

  const directLists = [
    (metadata as { mentions?: unknown }).mentions,
    (metadata as { mention_handles?: unknown }).mention_handles,
    (metadata as { targets?: unknown }).targets,
  ];
  const labels: string[] = [];

  for (const value of directLists) {
    if (!Array.isArray(value)) continue;
    for (const item of value) {
      if (typeof item === "string") {
        labels.push(item);
        continue;
      }
      if (!item || typeof item !== "object") continue;
      const candidate =
        (item as { display_name?: unknown }).display_name ||
        (item as { name?: unknown }).name ||
        (item as { handle?: unknown }).handle ||
        (item as { agent_name?: unknown }).agent_name ||
        (item as { id?: unknown }).id;
      if (typeof candidate === "string") labels.push(candidate);
    }
  }

  const routingStory = (metadata as { routing_story?: unknown }).routing_story;
  if (routingStory && typeof routingStory === "object") {
    const targets = (routingStory as { targets?: unknown }).targets;
    if (Array.isArray(targets)) {
      for (const item of targets) {
        if (!item || typeof item !== "object") continue;
        const candidate =
          (item as { display_name?: unknown }).display_name ||
          (item as { agent_name?: unknown }).agent_name ||
          (item as { target_name?: unknown }).target_name;
        if (typeof candidate === "string") labels.push(candidate);
      }
    }
  }

  return mergeOrderedLabels(labels);
}

export function getConversationCardMetadataEmojis(
  metadata?: Record<string, unknown> | null,
) {
  if (!metadata || typeof metadata !== "object") return [];

  const emojis: string[] = [];
  const signalEmojis = [
    (metadata as { signal_emojis?: unknown }).signal_emojis,
    (metadata as { signals?: unknown }).signals,
  ];

  for (const value of signalEmojis) {
    if (!Array.isArray(value)) continue;
    for (const item of value) {
      if (typeof item === "string" && item.trim()) emojis.push(item.trim());
    }
  }

  const emojiCounts = (metadata as { emoji_counts?: unknown }).emoji_counts;
  if (emojiCounts && typeof emojiCounts === "object") {
    for (const [emoji, rawCount] of Object.entries(emojiCounts)) {
      const count = Number(rawCount);
      const repeatCount =
        Number.isFinite(count) && count > 0
          ? Math.min(Math.round(count), 8)
          : 1;
      for (let index = 0; index < repeatCount; index += 1) {
        emojis.push(emoji);
      }
    }
  }

  return emojis;
}

export function summarizeEntryContent(entry: ChatEntry) {
  const summary = sanitizeAiSummary(entry.aiSummary);
  if (summary) return summary;
  return sanitizeAiSummary(entry.content);
}

export function shouldUseSummaryCard(entry: ChatEntry, isGrouped = false) {
  const summary = sanitizeAiSummary(entry.aiSummary);
  if (
    entry.role !== "agent" ||
    entry.messageType === "agent_pause" ||
    entry.isStreaming ||
    entry.id === "seed-agent" ||
    !summary
  ) {
    return false;
  }

  if (isGrouped) return true;

  const contentLength = (entry.content || "").trim().length;
  const summaryLength = summary.length;

  if (contentLength === 0) return true;

  return summaryLength < contentLength;
}

export function sortMessagesOldestFirst(
  messages: SpaceAgentMessage[],
): SpaceAgentMessage[] {
  return messages
    .map((message, index) => ({ message, index }))
    .sort((a, b) => {
      const aTime = a.message.created_at
        ? new Date(a.message.created_at).getTime()
        : 0;
      const bTime = b.message.created_at
        ? new Date(b.message.created_at).getTime()
        : 0;

      if (aTime && bTime && aTime !== bTime) {
        return aTime - bTime;
      }

      return a.index - b.index;
    })
    .map(({ message }) => message);
}

export function buildDisplayEntries({
  liveEntries,
  pendingEntries,
  streamingEntry,
  streamTiming,
  hasLiveConversation,
  agentName,
}: {
  liveEntries: ChatEntry[];
  pendingEntries: ChatEntry[];
  streamingEntry: NonNullable<StreamingState> | null;
  streamTiming: StreamTiming;
  hasLiveConversation: boolean;
  agentName: string;
}) {
  const baseEntries = hasLiveConversation
    ? mergeEntries(liveEntries, pendingEntries)
    : mergeEntries([], pendingEntries);

  if (!streamingEntry) return baseEntries;
  if (hasLandedAgentReply(baseEntries, streamingEntry, streamTiming)) {
    return baseEntries;
  }

  // Stream state is still tracked for lifecycle/status, but the transcript now
  // waits to create the agent message row until the final message lands.
  return baseEntries;
}

export function buildEntryLookup(displayEntries: ChatEntry[]) {
  return new Map(displayEntries.map((entry) => [entry.id, entry]));
}

export function buildThreadIdByEntryId(
  displayEntries: ChatEntry[],
  entryLookup: Map<string, ChatEntry>,
) {
  const resolved = new Map<string, string>();

  const resolveThreadId = (entry: ChatEntry) => {
    const existing = resolved.get(entry.id);
    if (existing) return existing;

    const visited = new Set<string>();
    let current: ChatEntry | undefined = entry;

    while (current && current.parentId && !visited.has(current.id)) {
      visited.add(current.id);
      const parent = entryLookup.get(current.parentId);
      if (!parent) break;
      current = parent;
    }

    const threadId =
      current?.id || entry.conversationId || entry.parentId || entry.id;

    resolved.set(entry.id, threadId);
    for (const visitedId of visited) {
      resolved.set(visitedId, threadId);
    }

    return threadId;
  };

  for (const entry of displayEntries) {
    resolveThreadId(entry);
  }

  return resolved;
}

export function getEntryThreadId(
  entry: ChatEntry | null | undefined,
  threadIdByEntryId: Map<string, string>,
) {
  if (!entry) return null;
  return threadIdByEntryId.get(entry.id) || entry.conversationId || entry.id;
}

export function buildConversationGroups(
  displayEntries: ChatEntry[],
  threadIdByEntryId: Map<string, string>,
) {
  const map = new Map<string, ConversationGroup>();

  for (const entry of displayEntries) {
    const threadId = getEntryThreadId(entry, threadIdByEntryId);
    if (!threadId || entry.isStreaming) continue;
    const existing = map.get(threadId);
    if (existing) {
      existing.entries.push(entry);
      existing.latestEntry = entry;
      if (entry.role === "agent" && entry.messageType !== "agent_pause") {
        existing.replyCount += 1;
      }
      if (entry.fromLabel) existing.participants.push(entry.fromLabel);
    } else {
      map.set(threadId, {
        id: threadId,
        entries: [entry],
        latestEntry: entry,
        replyCount:
          entry.role === "agent" && entry.messageType !== "agent_pause" ? 1 : 0,
        participants: entry.fromLabel ? [entry.fromLabel] : [],
      });
    }
  }

  for (const group of map.values()) {
    group.participants = Array.from(
      new Set(group.participants.filter(Boolean)),
    );
  }

  return map;
}

export function buildConversationCardAnchors(
  conversationGroups: Map<string, ConversationGroup>,
  conversationCardsByThread: Map<string, SpaceAgentConversationCard>,
) {
  const map = new Map<string, ChatEntry>();

  for (const group of conversationGroups.values()) {
    const threadCard = conversationCardsByThread.get(group.id);
    const groupedAgentEntries = group.entries.filter(
      (entry) =>
        entry.role === "agent" &&
        entry.messageType !== "agent_pause" &&
        !entry.isStreaming,
    );
    const latestAgentEntry =
      groupedAgentEntries[groupedAgentEntries.length - 1] || null;
    const fallbackSummaryEntry =
      [...group.entries]
        .reverse()
        .find((entry) => shouldUseSummaryCard(entry, true)) || null;

    const anchor =
      (getConversationCardSummary(threadCard)
        ? latestAgentEntry || fallbackSummaryEntry || group.latestEntry
        : null) ||
      (groupedAgentEntries.length > 1
        ? fallbackSummaryEntry || latestAgentEntry
        : null);

    if (anchor) map.set(group.id, anchor);
  }

  return map;
}

export function buildOrderedEntries({
  displayEntries,
}: {
  displayEntries: ChatEntry[];
  /** @deprecated No longer used — kept for call-site compatibility */
  conversationGroups?: Map<string, ConversationGroup>;
  /** @deprecated No longer used — kept for call-site compatibility */
  conversationCardsByThread?: Map<string, SpaceAgentConversationCard>;
  /** @deprecated No longer used — kept for call-site compatibility */
  threadIdByEntryId?: Map<string, string>;
}) {
  return [...displayEntries].sort((a, b) => {
    // Streaming entry always goes last
    if (a.isStreaming && !b.isStreaming) return 1;
    if (!a.isStreaming && b.isStreaming) return -1;

    // Stable chronological order — messages stay where they first appeared.
    // Using createdAt (not last_activity_at) prevents cards/messages from
    // jumping position when threads receive new replies.
    const aMs = getTimestampMs(a.createdAt);
    const bMs = getTimestampMs(b.createdAt);
    if (aMs !== bMs) return aMs - bMs;

    return 0;
  });
}

export function buildHiddenEntryIds({
  cardsEnabled,
  conversationGroups,
  conversationCardsByThread,
  conversationCardAnchors,
}: {
  cardsEnabled: boolean;
  conversationGroups: Map<string, ConversationGroup>;
  conversationCardsByThread: Map<string, SpaceAgentConversationCard>;
  conversationCardAnchors: Map<string, ChatEntry>;
}) {
  const ids = new Set<string>();
  if (!cardsEnabled) return ids;

  for (const group of conversationGroups.values()) {
    const threadCard = conversationCardsByThread.get(group.id);
    const anchor = conversationCardAnchors.get(group.id);
    const groupedAgentEntries = group.entries.filter(
      (entry) => entry.role === "agent" && entry.messageType !== "agent_pause",
    );
    if (groupedAgentEntries.length <= 1) continue;
    const hasThreadSummary = Boolean(getConversationCardSummary(threadCard));
    if (!hasThreadSummary && !shouldUseSummaryCard(group.latestEntry, true)) {
      continue;
    }
    for (const entry of groupedAgentEntries) {
      if (!entry.parentId) continue;
      if (entry.id !== anchor?.id) ids.add(entry.id);
    }
  }

  return ids;
}

export function getStreamingThreadId(
  streamingEntry: NonNullable<StreamingState> | null,
  threadIdByEntryId: Map<string, string>,
) {
  if (!streamingEntry) return null;
  return threadIdByEntryId.get(streamingEntry.id) || streamingEntry.id;
}

export function shouldRenderEntryAsCard({
  entry,
  cardsEnabled,
  streamingThreadId,
  threadIdByEntryId,
  conversationCardsByThread,
  conversationCardAnchors,
  conversationGroups,
}: {
  entry: ChatEntry;
  cardsEnabled: boolean;
  streamingThreadId: string | null;
  threadIdByEntryId: Map<string, string>;
  conversationCardsByThread: Map<string, SpaceAgentConversationCard>;
  conversationCardAnchors: Map<string, ChatEntry>;
  conversationGroups: Map<string, ConversationGroup>;
}) {
  if (!cardsEnabled) return false;

  // Alert/reminder messages with alert card surfaces are activity-stream
  // events, not chat replies. They should render through AxSurfaceRail as
  // system cards and must not be promoted into conversation summary card chrome.
  if (shouldRenderActivityEntryAsSurfaceOnly(entry)) return false;

  const threadId = getEntryThreadId(entry, threadIdByEntryId);
  if (streamingThreadId && threadId === streamingThreadId) return false;

  const threadCard = threadId ? conversationCardsByThread.get(threadId) : null;
  const threadAnchor = threadId ? conversationCardAnchors.get(threadId) : null;
  const threadGroup = threadId ? conversationGroups.get(threadId) : null;
  const groupedAgentEntries =
    threadGroup?.entries.filter(
      (candidate) =>
        candidate.role === "agent" &&
        candidate.messageType !== "agent_pause" &&
        !candidate.isStreaming,
    ) || [];
  const userLatest = threadGroup?.latestEntry.role === "user";
  const groupedThreadOwnedByAnchor =
    groupedAgentEntries.length > 1 &&
    Boolean(threadAnchor) &&
    threadAnchor?.id !== entry.id;

  if (groupedThreadOwnedByAnchor) return false;

  return (
    shouldUseSummaryCard(entry) ||
    Boolean(
      entry.role === "agent" &&
      entry.messageType !== "agent_pause" &&
      !entry.isStreaming &&
      (userLatest
        ? summarizeEntryContent(entry)
        : getConversationCardSummary(threadCard)) &&
      (!threadAnchor || threadAnchor.id === entry.id),
    )
  );
}
