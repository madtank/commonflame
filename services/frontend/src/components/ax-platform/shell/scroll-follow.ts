export const DEFAULT_SCROLL_FOLLOW_THRESHOLD_PX = 72;
export const DEFAULT_BOTTOM_LOCK_TOLERANCE_PX = 2;
export const DEFAULT_COMPOSER_TO_LATEST_GAP_PX = 16;
export const SCROLL_BOTTOM_EPSILON_PX = 2;

export type ScrollMetrics = {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
};

export type LatestEntrySnapshot = {
  latestEntryId: string | null;
  latestEntryVersionKey: string | null;
};

export type ScrollAnchorSnapshot = {
  entryId: string;
  offsetTop: number;
  scrollTop: number;
  scrollHeight: number;
};

export type ScrollAnchorOptions = {
  selector?: string;
  thresholdPx?: number;
  reservedBottomPx?: number;
  viewportInsetPx?: number;
};

export type ShouldAutoSnapToBottomOptions = {
  isOnBottomMessage: boolean;
  isUserSelectingText: boolean;
};

export type ShouldShowJumpToLatestOptions = {
  isOnBottomMessage: boolean;
  hasUnreadLatest?: boolean;
};

const DEFAULT_ANCHOR_SELECTOR = "[data-entry-id]";
const DEFAULT_VIEWPORT_INSET_PX = 4;

export function getRemainingScrollPx(node: ScrollMetrics) {
  return Math.max(0, node.scrollHeight - node.scrollTop - node.clientHeight);
}

export function isNearBottom(
  node: ScrollMetrics,
  thresholdPx = DEFAULT_SCROLL_FOLLOW_THRESHOLD_PX,
  reservedBottomPx = 0,
) {
  const remainingPx = getRemainingScrollPx(node);
  const visualThresholdPx =
    Math.max(0, thresholdPx) +
    Math.max(0, reservedBottomPx) +
    SCROLL_BOTTOM_EPSILON_PX;

  return remainingPx <= visualThresholdPx;
}

export function isBottomLocked(
  node: ScrollMetrics,
  tolerancePx = DEFAULT_BOTTOM_LOCK_TOLERANCE_PX,
) {
  return getRemainingScrollPx(node) <= Math.max(0, tolerancePx);
}

export function shouldAutoSnapToBottom({
  isOnBottomMessage,
  isUserSelectingText,
}: ShouldAutoSnapToBottomOptions) {
  return isOnBottomMessage && !isUserSelectingText;
}

export function shouldShowJumpToLatest({
  isOnBottomMessage,
  hasUnreadLatest = false,
}: ShouldShowJumpToLatestOptions) {
  return hasUnreadLatest || !isOnBottomMessage;
}

export function getComposerBottomSafeAreaPx(
  composerHeight: number,
  gapPx = DEFAULT_COMPOSER_TO_LATEST_GAP_PX,
) {
  return Math.max(0, composerHeight) + Math.max(0, gapPx);
}

export function getLatestEntrySnapshot(
  entries: readonly {
    id: string | null | undefined;
    aiSummary?: string | null;
    content?: string | null;
    isStreaming?: boolean | null;
    surfaceCount?: number | null;
  }[],
): LatestEntrySnapshot {
  const latestEntry = entries.length > 0 ? entries[entries.length - 1] : null;
  const latestEntryId =
    typeof latestEntry?.id === "string" && latestEntry.id.trim()
      ? latestEntry.id
      : null;
  return {
    latestEntryId,
    latestEntryVersionKey: latestEntryId
      ? [
          latestEntry.content ?? "",
          latestEntry.aiSummary ?? "",
          latestEntry.surfaceCount ?? 0,
          latestEntry.isStreaming ? "stream" : "final",
        ].join("\u001f")
      : null,
  };
}

export function hasNewLatestEntry(
  previous: LatestEntrySnapshot | null,
  current: LatestEntrySnapshot,
) {
  if (!previous?.latestEntryId || !current.latestEntryId) return false;
  return (
    previous.latestEntryId !== current.latestEntryId ||
    previous.latestEntryVersionKey !== current.latestEntryVersionKey
  );
}

export function chooseBottomFollowScrollBehavior({
  hasAnchoredInitialTranscript,
  hasNewLatestEntry,
}: {
  hasAnchoredInitialTranscript: boolean;
  hasNewLatestEntry: boolean;
}): ScrollBehavior {
  if (!hasAnchoredInitialTranscript) return "auto";
  return hasNewLatestEntry ? "smooth" : "auto";
}

function getEntryId(element: Element) {
  return element instanceof HTMLElement
    ? element.dataset.entryId || null
    : null;
}

export function captureVisibleScrollAnchor(
  viewport: HTMLElement,
  options: ScrollAnchorOptions = {},
): ScrollAnchorSnapshot | null {
  const thresholdPx = options.thresholdPx ?? DEFAULT_SCROLL_FOLLOW_THRESHOLD_PX;
  const reservedBottomPx = options.reservedBottomPx ?? 0;
  if (isNearBottom(viewport, thresholdPx, reservedBottomPx)) return null;

  const selector = options.selector ?? DEFAULT_ANCHOR_SELECTOR;
  const viewportInsetPx = options.viewportInsetPx ?? DEFAULT_VIEWPORT_INSET_PX;
  const viewportRect = viewport.getBoundingClientRect();
  const visibleTop = viewportRect.top + Math.max(0, viewportInsetPx);
  const visibleBottom = viewportRect.bottom - Math.max(0, viewportInsetPx);

  let best: {
    entryId: string;
    offsetTop: number;
    distanceFromTop: number;
  } | null = null;

  for (const element of Array.from(viewport.querySelectorAll(selector))) {
    const entryId = getEntryId(element);
    if (!entryId) continue;

    const rect = element.getBoundingClientRect();
    if (rect.bottom <= visibleTop || rect.top >= visibleBottom) continue;

    const offsetTop = rect.top - viewportRect.top;
    const distanceFromTop = Math.abs(rect.top - visibleTop);
    if (!best || distanceFromTop < best.distanceFromTop) {
      best = { entryId, offsetTop, distanceFromTop };
    }
  }

  if (!best) return null;
  return {
    entryId: best.entryId,
    offsetTop: best.offsetTop,
    scrollTop: viewport.scrollTop,
    scrollHeight: viewport.scrollHeight,
  };
}

export function restoreVisibleScrollAnchor(
  viewport: HTMLElement,
  snapshot: ScrollAnchorSnapshot | null,
  options: ScrollAnchorOptions = {},
) {
  if (!snapshot) return false;

  const selector = options.selector ?? DEFAULT_ANCHOR_SELECTOR;
  const viewportRect = viewport.getBoundingClientRect();
  const anchor = Array.from(viewport.querySelectorAll(selector)).find(
    (element) => getEntryId(element) === snapshot.entryId,
  );
  if (!anchor) return false;

  const nextOffsetTop = anchor.getBoundingClientRect().top - viewportRect.top;
  const delta = nextOffsetTop - snapshot.offsetTop;
  if (Math.abs(delta) < 0.5) return false;

  viewport.scrollTop += delta;
  return true;
}
