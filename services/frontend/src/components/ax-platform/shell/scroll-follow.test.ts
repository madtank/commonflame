import { describe, expect, it } from "vitest";

import {
  captureVisibleScrollAnchor,
  chooseBottomFollowScrollBehavior,
  getComposerBottomSafeAreaPx,
  getLatestEntrySnapshot,
  getRemainingScrollPx,
  hasNewLatestEntry,
  isBottomLocked,
  isNearBottom,
  restoreVisibleScrollAnchor,
  shouldAutoSnapToBottom,
  shouldShowJumpToLatest,
} from "./scroll-follow";

describe("scroll-follow", () => {
  it("treats the raw scroll end as near the bottom", () => {
    expect(
      isNearBottom({ scrollHeight: 1200, scrollTop: 492, clientHeight: 650 }),
    ).toBe(true);
  });

  it("does not auto-follow when the user has clearly scrolled up", () => {
    expect(
      isNearBottom({ scrollHeight: 1200, scrollTop: 360, clientHeight: 650 }),
    ).toBe(false);
  });

  it("accounts for fixed composer clearance when deciding visual bottom", () => {
    expect(
      isNearBottom(
        { scrollHeight: 1200, scrollTop: 360, clientHeight: 650 },
        72,
        120,
      ),
    ).toBe(true);
  });

  it("distinguishes exact bottom-lock from near-bottom affordance", () => {
    const slightlyAboveBottom = {
      scrollHeight: 1200,
      scrollTop: 526,
      clientHeight: 650,
    };

    expect(isNearBottom(slightlyAboveBottom, 72)).toBe(true);
    expect(isBottomLocked(slightlyAboveBottom)).toBe(false);
    expect(
      shouldAutoSnapToBottom({
        isOnBottomMessage: isBottomLocked(slightlyAboveBottom),
        isUserSelectingText: false,
      }),
    ).toBe(false);
  });

  it("shows the jump affordance when near-bottom is true but bottom-lock is false", () => {
    const slightlyAboveBottom = {
      scrollHeight: 1200,
      scrollTop: 526,
      clientHeight: 650,
    };

    expect(isNearBottom(slightlyAboveBottom, 72)).toBe(true);
    expect(isBottomLocked(slightlyAboveBottom)).toBe(false);
    expect(
      shouldShowJumpToLatest({
        isOnBottomMessage: isBottomLocked(slightlyAboveBottom),
      }),
    ).toBe(true);
  });

  it("hides the jump affordance only when bottom-locked with no unread latest", () => {
    expect(
      shouldShowJumpToLatest({
        isOnBottomMessage: true,
        hasUnreadLatest: false,
      }),
    ).toBe(false);

    expect(
      shouldShowJumpToLatest({
        isOnBottomMessage: true,
        hasUnreadLatest: true,
      }),
    ).toBe(true);
  });

  it("clamps overscroll to zero remaining distance", () => {
    expect(
      getRemainingScrollPx({
        scrollHeight: 1200,
        scrollTop: 700,
        clientHeight: 650,
      }),
    ).toBe(0);
  });

  it("auto-snaps only while explicitly bottom-locked", () => {
    expect(
      shouldAutoSnapToBottom({
        isOnBottomMessage: true,
        isUserSelectingText: false,
      }),
    ).toBe(true);

    expect(
      shouldAutoSnapToBottom({
        isOnBottomMessage: false,
        isUserSelectingText: false,
      }),
    ).toBe(false);
  });

  it("does not auto-snap while the user is selecting text", () => {
    expect(
      shouldAutoSnapToBottom({
        isOnBottomMessage: true,
        isUserSelectingText: true,
      }),
    ).toBe(false);
  });

  it("keeps the latest message tight above the fixed composer", () => {
    expect(getComposerBottomSafeAreaPx(132)).toBe(148);
  });

  it("clamps composer clearance inputs", () => {
    expect(getComposerBottomSafeAreaPx(-12, -8)).toBe(0);
  });

  it("detects a new latest entry without treating older prepends as unread", () => {
    const previous = getLatestEntrySnapshot([
      { id: "old-1" },
      { id: "old-2" },
      { id: "latest", content: "complete" },
    ]);

    expect(
      hasNewLatestEntry(
        previous,
        getLatestEntrySnapshot([
          { id: "older-prepended" },
          { id: "old-1" },
          { id: "old-2" },
          { id: "latest", content: "complete" },
        ]),
      ),
    ).toBe(false);

    expect(
      hasNewLatestEntry(
        previous,
        getLatestEntrySnapshot([
          { id: "old-1" },
          { id: "old-2" },
          { id: "latest", content: "complete" },
          { id: "new-latest", content: "new" },
        ]),
      ),
    ).toBe(true);
  });

  it("detects latest-entry content changes for off-bottom streaming unread affordance", () => {
    const previous = getLatestEntrySnapshot([
      { id: "old-1", content: "done" },
      { id: "latest", content: "streaming", isStreaming: true },
    ]);

    expect(
      hasNewLatestEntry(
        previous,
        getLatestEntrySnapshot([
          { id: "old-1", content: "done" },
          { id: "latest", content: "streaming update", isStreaming: true },
        ]),
      ),
    ).toBe(true);
  });

  it("detects same-length latest-entry content replacement for off-bottom unread affordance", () => {
    const previous = getLatestEntrySnapshot([
      { id: "latest", content: "draft one", isStreaming: true },
    ]);

    expect(
      hasNewLatestEntry(
        previous,
        getLatestEntrySnapshot([
          { id: "latest", content: "draft two", isStreaming: true },
        ]),
      ),
    ).toBe(true);
  });

  it("detects latest-entry surface changes for off-bottom unread affordance", () => {
    const previous = getLatestEntrySnapshot([
      { id: "old-1", content: "done" },
      { id: "latest", content: "same", surfaceCount: 0 },
    ]);

    expect(
      hasNewLatestEntry(
        previous,
        getLatestEntrySnapshot([
          { id: "old-1", content: "done" },
          { id: "latest", content: "same", surfaceCount: 1 },
        ]),
      ),
    ).toBe(true);
  });

  it("uses smooth bottom follow after the initial transcript anchor", () => {
    expect(
      chooseBottomFollowScrollBehavior({
        hasAnchoredInitialTranscript: false,
        hasNewLatestEntry: true,
      }),
    ).toBe("auto");

    expect(
      chooseBottomFollowScrollBehavior({
        hasAnchoredInitialTranscript: true,
        hasNewLatestEntry: true,
      }),
    ).toBe("smooth");

    expect(
      chooseBottomFollowScrollBehavior({
        hasAnchoredInitialTranscript: true,
        hasNewLatestEntry: false,
      }),
    ).toBe("auto");
  });

  it("preserves a visible reading anchor when content above it changes height", () => {
    const viewport = document.createElement("div");
    Object.defineProperties(viewport, {
      scrollHeight: { configurable: true, value: 1800 },
      scrollTop: { configurable: true, writable: true, value: 500 },
      clientHeight: { configurable: true, value: 600 },
    });
    viewport.getBoundingClientRect = () =>
      ({ top: 100, bottom: 700 }) as DOMRect;

    const entry = document.createElement("article");
    entry.dataset.entryId = "entry-2";
    entry.getBoundingClientRect = () => ({ top: 240, bottom: 420 }) as DOMRect;
    viewport.append(entry);

    const snapshot = captureVisibleScrollAnchor(viewport);
    expect(snapshot).toEqual(
      expect.objectContaining({ entryId: "entry-2", offsetTop: 140 }),
    );

    entry.getBoundingClientRect = () => ({ top: 315, bottom: 495 }) as DOMRect;

    expect(restoreVisibleScrollAnchor(viewport, snapshot)).toBe(true);
    expect(viewport.scrollTop).toBe(575);
  });

  it("does not capture a manual reading anchor while already pinned near bottom", () => {
    const viewport = document.createElement("div");
    Object.defineProperties(viewport, {
      scrollHeight: { configurable: true, value: 1200 },
      scrollTop: { configurable: true, writable: true, value: 532 },
      clientHeight: { configurable: true, value: 600 },
    });
    viewport.getBoundingClientRect = () => ({ top: 0, bottom: 600 }) as DOMRect;

    const entry = document.createElement("article");
    entry.dataset.entryId = "latest";
    entry.getBoundingClientRect = () => ({ top: 480, bottom: 580 }) as DOMRect;
    viewport.append(entry);

    expect(captureVisibleScrollAnchor(viewport)).toBeNull();
  });

  it("tolerates sub-pixel mobile layout differences at the bottom threshold", () => {
    expect(
      isNearBottom(
        { scrollHeight: 1000.4, scrollTop: 328, clientHeight: 600 },
        72,
      ),
    ).toBe(true);
  });
});
