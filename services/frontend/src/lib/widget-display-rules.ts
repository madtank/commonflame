/**
 * Widget display rules.
 *
 * Spec: WIDGET-002 §5.1 — Universal Display Rules
 * - "Hide: technical transport/runtime metadata"
 * - Developer-facing notices like "Tool call completed: X" must not persist
 *   in the user-facing UI.
 */

export type HostNotice = {
  tone: "info" | "error";
  text: string;
};

/** Patterns for notices that are developer/transport metadata, not user-facing. */
const SUPPRESSED_INFO_PATTERNS = [/^Tool call completed:/i];

/**
 * Determine whether a host notice should be rendered to the user.
 * Suppresses developer-facing transport notices per WIDGET-002 §5.1.
 */
export function shouldShowHostNotice(notice: HostNotice | null): boolean {
  if (!notice) return false;

  // Always show errors — users need to see failures
  if (notice.tone === "error") return true;

  // Suppress info notices that match transport/runtime patterns
  return !SUPPRESSED_INFO_PATTERNS.some((pattern) => pattern.test(notice.text));
}

/**
 * Set up auto-dismiss for transient info notices.
 * Error notices are never auto-dismissed.
 * Returns a cleanup function to cancel the timer.
 */
export function createAutoDismissNotice(
  notice: HostNotice | null,
  onClear: () => void,
  timeoutMs: number = 3000,
): () => void {
  if (!notice || notice.tone === "error") {
    return () => {};
  }

  const timer = setTimeout(onClear, timeoutMs);
  return () => clearTimeout(timer);
}
