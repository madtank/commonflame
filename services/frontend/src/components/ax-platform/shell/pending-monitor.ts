import type { ChatEntry } from "./transcript-model";
import {
  getPendingResponseDisplay,
  type PendingResponseDisplay,
  type PendingResponseState,
} from "./pending-response";
import { isTerminalProcessingStatus } from "./processing-lifecycle";

export type PendingMonitorState = {
  persistedPendingReply: ChatEntry | null;
  inlinePendingDisplay: PendingResponseDisplay | null;
  showPendingInline: boolean;
  pendingRouteLabel: string | null;
  pendingChipStatus: string | null;
  inlinePendingSignals: string[];
  visibleInlinePendingSignals: string[];
  hiddenInlinePendingSignalCount: number;
};

function getPendingChipStatus(display: PendingResponseDisplay) {
  const title = display.title.trim().replace(/\s+/g, " ");
  if (!title) return "working";

  const waitingMatch = title.match(/^Waiting for\s+(.+)$/i);
  if (waitingMatch) return "waiting";

  const workingMatch = title.match(/^(.+?)\s+is\s+working$/i);
  if (workingMatch?.[1]) return `${workingMatch[1]} working`;

  const respondingMatch = title.match(/^(.+?)\s+is\s+responding$/i);
  if (respondingMatch?.[1]) return `${respondingMatch[1]} responding`;

  const usingMatch = title.match(/^(.+?)\s+is\s+using\s+(.+?)(?:\.\.\.)?$/i);
  if (usingMatch?.[2]) return `using ${usingMatch[2]}`;

  return title;
}

export function getPendingMonitorState({
  entry,
  pendingStreamingReplyByParentId,
  inlinePendingResponseEntryId,
  pendingResponse,
  pendingResponseDisplay,
  getSafeRouteLabel,
  getPersistedPendingReplyDisplay,
}: {
  entry: ChatEntry;
  pendingStreamingReplyByParentId: Map<string, ChatEntry>;
  inlinePendingResponseEntryId: string | null;
  pendingResponse: PendingResponseState | null;
  pendingResponseDisplay: PendingResponseDisplay | null;
  getSafeRouteLabel: (value?: string | null) => string | null;
  getPersistedPendingReplyDisplay: (entry: ChatEntry) => PendingResponseDisplay;
}): PendingMonitorState {
  const routeLabel = entry.toLabel
    ? getSafeRouteLabel(entry.toLabel) || "thread"
    : null;
  const persistedPendingReply =
    pendingStreamingReplyByParentId.get(entry.id) || null;
  const persistedPendingDisplay = persistedPendingReply
    ? getPersistedPendingReplyDisplay(persistedPendingReply)
    : null;
  const livePendingDisplay =
    entry.id === inlinePendingResponseEntryId ? pendingResponseDisplay : null;
  const durableProcessingDisplay =
    !persistedPendingDisplay &&
    !livePendingDisplay &&
    entry.hasProcessingState &&
    entry.statusLabel &&
    !isTerminalProcessingStatus(entry.statusLabel) &&
    routeLabel
      ? getPendingResponseDisplay({
          sourceEntryId: entry.id,
          targetHandle: null,
          targetLabel: routeLabel,
          activeAgentLabel:
            entry.statusLabel.toLowerCase() === "queued" ? null : routeLabel,
          statusLabel: entry.statusLabel,
          toolName: entry.toolName || null,
          activity: entry.activity || null,
          toolCount: null,
          stepLabel: null,
          commandLabels: [],
          progress: null,
          reason: null,
          errorMessage: null,
          retryAfterSeconds: null,
          isKnownTarget: true,
          hasPresenceSignal: false,
          presenceStatus: null,
        })
      : null;
  const showPendingInline =
    Boolean(persistedPendingDisplay) ||
    Boolean(livePendingDisplay) ||
    Boolean(durableProcessingDisplay);
  const inlinePendingDisplay =
    persistedPendingDisplay ||
    livePendingDisplay ||
    durableProcessingDisplay;
  const inlinePendingSignals = inlinePendingDisplay?.signals || [];
  const visibleInlinePendingSignals = inlinePendingSignals.slice(0, 2);
  const hiddenInlinePendingSignalCount = Math.max(
    0,
    inlinePendingSignals.length - visibleInlinePendingSignals.length,
  );
  const pendingRouteLabel =
    routeLabel ||
    (entry.id === inlinePendingResponseEntryId
      ? pendingResponse?.targetLabel
      : null) ||
    (persistedPendingReply
      ? getSafeRouteLabel(
          persistedPendingReply.fromLabel || persistedPendingReply.meta,
        )
      : null);
  const pendingChipStatus = inlinePendingDisplay
    ? getPendingChipStatus(inlinePendingDisplay)
    : null;

  return {
    persistedPendingReply,
    inlinePendingDisplay,
    showPendingInline,
    pendingRouteLabel,
    pendingChipStatus,
    inlinePendingSignals,
    visibleInlinePendingSignals,
    hiddenInlinePendingSignalCount,
  };
}
