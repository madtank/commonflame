const TERMINAL_PROCESSING_STATUSES = new Set([
  "complete",
  "completed",
  "done",
  "resolved",
  "failed",
  "error",
  "cancelled",
  "canceled",
  "timeout",
  "timed_out",
]);

const ACTIVE_PROCESSING_STATUSES = new Set([
  "queued",
  "processing",
  "working",
  "thinking",
  "started",
  "claimed",
  "forwarded",
  "streaming",
]);

export function normalizeProcessingStatus(status?: string | null) {
  return (status || "").trim().toLowerCase();
}

export function isTerminalProcessingStatus(status?: string | null) {
  return TERMINAL_PROCESSING_STATUSES.has(normalizeProcessingStatus(status));
}

export function isActiveProcessingStatus(status?: string | null) {
  return ACTIVE_PROCESSING_STATUSES.has(normalizeProcessingStatus(status));
}

export function isSuppressedProcessingPayload(
  payload: Record<string, unknown> | null | undefined,
) {
  return payload?.suppress === true;
}
