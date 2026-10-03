import {
  useRef,
  useCallback,
  useEffect,
  useState,
  useLayoutEffect,
} from "react";
import { USE_FLEX_CHAT_LAYOUT } from "@/config/features";
import { shouldAutoSnapToBottom } from "@/components/ax-platform/shell/scroll-follow";

interface UseScrollManagerOptions {
  messagesContainerRef: React.RefObject<HTMLDivElement>;
  messagesEndRef: React.RefObject<HTMLDivElement>;
  onScroll: () => void;
  hasOlderMessages: boolean;
  onLoadMore: () => void;
  isInHistoryMode?: boolean;
  postCount: number;
  isUserSelectingText: () => boolean;
}

interface ScrollManagerState {
  isScrolledToTop: boolean;
  isScrolledUp: boolean;
  isIPhone: boolean;
  bottomInsetPx: number;
}

/**
 * Check if the user is viewing the last (bottom) message.
 * "Viewing" = the bottom edge of the last message element is within the visible
 * scroll area (i.e. the user hasn't scrolled up away from it).
 */
function isViewingLastMessage(container: HTMLElement): boolean {
  // Find the last message element
  const allPosts = container.querySelectorAll("[data-post-id]");
  if (allPosts.length === 0) return true;
  const lastPost = allPosts[allPosts.length - 1] as HTMLElement;

  const containerRect = container.getBoundingClientRect();
  const lastPostRect = lastPost.getBoundingClientRect();

  // Strict check: bottom of last message must be above or near the bottom of the container
  // We allow a 40px buffer to account for the bottom spacer and typical "bottom-ish" position.
  return lastPostRect.bottom <= containerRect.bottom + 40;
}

export function useScrollManager({
  messagesContainerRef,
  messagesEndRef,
  onScroll,
  hasOlderMessages,
  onLoadMore,
  isInHistoryMode = false,
  postCount,
  isUserSelectingText,
}: UseScrollManagerOptions): ScrollManagerState {
  const [isScrolledToTop, setIsScrolledToTop] = useState(false);
  const [isScrolledUp, setIsScrolledUp] = useState(false);
  const [isIPhone, setIsIPhone] = useState(false);
  // In flex layout the ChatInput is a flex sibling BELOW the scroll container —
  // it does not overlap the message area, so no inset is needed.
  // In fixed/legacy layout the input overlays messages, so we need the full height.
  const bottomInsetPxRef = useRef<number>(80);
  const [bottomInsetPx, setBottomInsetPx] = useState(() => {
    if (USE_FLEX_CHAT_LAYOUT) return 4; // tiny breathing room only
    if (typeof document !== "undefined") {
      const v = getComputedStyle(document.documentElement).getPropertyValue(
        "--chat-input-height",
      );
      const px = parseInt(v, 10);
      if (px > 0) return px;
    }
    return 80; // safe fallback for fixed/legacy layout
  });
  // Keep ref in sync so scroll handler always reads the current inset (no stale closure)
  useEffect(() => {
    bottomInsetPxRef.current = bottomInsetPx;
  }, [bottomInsetPx]);
  // True when the user can see the last message (even partially)
  const isOnBottomMessageRef = useRef<boolean>(true);
  const didInitialScrollRef = useRef(false);
  const prevPostsLenRef = useRef<number>(0);
  const prevScrollHeightRef = useRef<number>(0);

  // Track previous isInHistoryMode to detect when exiting history mode
  const prevHistoryModeRef = useRef(isInHistoryMode);
  useEffect(() => {
    if (prevHistoryModeRef.current === true && isInHistoryMode === false) {
      const el = messagesContainerRef.current;
      if (el) {
        el.style.scrollBehavior = "auto";
        el.scrollTop = el.scrollHeight;
      }
      const endMarker =
        messagesEndRef.current || document.querySelector("[data-messages-end]");
      if (endMarker) {
        endMarker.scrollIntoView({ behavior: "auto", block: "end" });
      }
    }
    prevHistoryModeRef.current = isInHistoryMode;
  }, [isInHistoryMode, messagesContainerRef, messagesEndRef]);

  // iPhone Safari viewport handling
  useEffect(() => {
    const ua = navigator.userAgent || "";
    const isIOS = /iPhone|iPod/.test(ua);
    const isIOSChrome = /CriOS/.test(ua);
    const isMobileSafari = /Safari/.test(ua) && /Mobile/.test(ua);
    const smallViewport =
      typeof window.screen?.width === "number" &&
      window.screen.width > 0 &&
      window.screen.width <= 430;
    const supportsSafeArea = Boolean(
      window.CSS &&
      CSS.supports &&
      CSS.supports("padding-bottom", "env(safe-area-inset-bottom)"),
    );

    const isIPhoneDevice =
      (isIOS && "ontouchend" in document) ||
      isIOSChrome ||
      (smallViewport && (isIOS || isMobileSafari)) ||
      (supportsSafeArea && smallViewport && isIOS);

    setIsIPhone(isIPhoneDevice);

    if (isIPhoneDevice) {
      const setVH = () => {
        const vh = window.visualViewport
          ? window.visualViewport.height
          : window.innerHeight;
        document.documentElement.style.setProperty("--vh", `${vh * 0.01}px`);
      };

      setVH();

      if (window.visualViewport) {
        window.visualViewport.addEventListener("resize", setVH);
        return () => window.visualViewport.removeEventListener("resize", setVH);
      } else {
        window.addEventListener("resize", setVH);
        return () => window.removeEventListener("resize", setVH);
      }
    }
  }, []);

  // Bottom inset calculation (throttled via rAF)
  const rafPendingRef = useRef(false);
  const recalcBottomInset = useCallback(() => {
    // In flex layout, ChatInput is a sibling below — no overlap, no inset needed.
    if (USE_FLEX_CHAT_LAYOUT) {
      setBottomInsetPx(4);
      return;
    }
    if (rafPendingRef.current) return;
    rafPendingRef.current = true;
    requestAnimationFrame(() => {
      rafPendingRef.current = false;
      const container = messagesContainerRef.current;
      if (!container) return;

      const containerRect = container.getBoundingClientRect();

      const findComposerEl = (): HTMLElement | null => {
        const selectors = [
          "[data-chat-input-root]",
          "#chat-input-root",
          ".chat-input-root",
          '[data-role="chat-input"]',
          '[data-testid="chat-input"]',
        ];
        for (const sel of selectors) {
          const el = document.querySelector(sel) as HTMLElement | null;
          if (el) return el;
        }
        return null;
      };

      const composerEl = findComposerEl();
      if (composerEl) {
        const cs = getComputedStyle(composerEl);
        const mt = parseFloat(cs.marginTop || "0");
        const mb = parseFloat(cs.marginBottom || "0");
        const composerRect = composerEl.getBoundingClientRect();
        const composerH = composerRect.height + mt + mb;
        // Increased threshold slightly to catch near-touching flex elements
        const overlaps = containerRect.bottom > composerRect.top - 2;
        // Increased max cap to support very tall multiline inputs + reply context
        const inset = Math.max(12, Math.min(800, Math.round(composerH)));
        setBottomInsetPx(overlaps ? inset : 12);
        return;
      }

      const varVal = getComputedStyle(
        document.documentElement,
      ).getPropertyValue("--chat-input-height");
      const fromVar = parseInt(varVal || "", 10);
      if (!isNaN(fromVar) && fromVar > 0) {
        const vv: any = (window as any).visualViewport;
        const viewportH = vv ? vv.height : window.innerHeight;
        const touchesBottom = !(containerRect.bottom < viewportH - 2);
        const inset = Math.max(12, Math.min(800, Math.round(fromVar)));
        setBottomInsetPx(touchesBottom ? inset : 12);
        return;
      }

      setBottomInsetPx(12);
    });
  }, [messagesContainerRef]);

  // Initial scroll to bottom + auto-scroll on new messages
  const snapToBottom = useCallback(
    (behavior: ScrollBehavior = "auto") => {
      const el = messagesContainerRef.current;
      if (!el) return;

      // Primary: use the end marker ref if available
      if (messagesEndRef.current) {
        messagesEndRef.current.scrollIntoView({ behavior, block: "end" });
        return;
      }

      // Fallback: direct scrollTop
      const prev = el.style.scrollBehavior;
      el.style.scrollBehavior = behavior;
      el.scrollTop = el.scrollHeight;
      if (behavior === "auto") {
        requestAnimationFrame(() => {
          try {
            el.style.scrollBehavior = prev || "smooth";
          } catch {
            /* no-op */
          }
        });
      }
    },
    [messagesContainerRef, messagesEndRef],
  );

  const pendingBottomSnapRafRef = useRef<number | null>(null);
  const queueBottomSnap = useCallback(() => {
    if (pendingBottomSnapRafRef.current != null) return;
    pendingBottomSnapRafRef.current = requestAnimationFrame(() => {
      pendingBottomSnapRafRef.current = null;
      snapToBottom("auto");
    });
  }, [snapToBottom]);

  useEffect(
    () => () => {
      if (pendingBottomSnapRafRef.current != null) {
        cancelAnimationFrame(pendingBottomSnapRafRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    recalcBottomInset();
    const onResize = () => recalcBottomInset();
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);

    // Also watch for ChatInput growth specifically using ResizeObserver
    const composer = document.querySelector("[data-chat-input-root]");
    let composerObserver: ResizeObserver | null = null;
    if (composer) {
      composerObserver = new ResizeObserver(() => recalcBottomInset());
      composerObserver.observe(composer);
    }

    // Watch the messages container itself.
    // In flex layout, when ChatInput grows, the container shrinks.
    // We need to re-scroll to bottom if we were already there.
    const container = messagesContainerRef.current;
    let containerObserver: ResizeObserver | null = null;
    // Watch the rendered message content height too.
    // New messages (or markdown/media expansion) can increase content height
    // *after* initial paint; if user is on bottom, keep it bottom-anchored.
    let contentObserver: ResizeObserver | null = null;

    const scheduleBottomSnap = () => {
      if (isOnBottomMessageRef.current && !isUserSelectingText()) {
        queueBottomSnap();
      }
    };

    if (container) {
      containerObserver = new ResizeObserver(() => {
        scheduleBottomSnap();
      });
      containerObserver.observe(container);

      const content = container.querySelector(
        "[data-messages-content]",
      ) as HTMLElement | null;
      if (content) {
        contentObserver = new ResizeObserver(() => {
          scheduleBottomSnap();
        });
        contentObserver.observe(content);
      }
    }

    if ((window as any).visualViewport) {
      (window as any).visualViewport.addEventListener("resize", onResize);
      (window as any).visualViewport.addEventListener("scroll", onResize);
    }
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", onResize);
      composerObserver?.disconnect();
      containerObserver?.disconnect();
      contentObserver?.disconnect();
      if ((window as any).visualViewport) {
        (window as any).visualViewport.removeEventListener("resize", onResize);
        (window as any).visualViewport.removeEventListener("scroll", onResize);
      }
    };
  }, [recalcBottomInset, queueBottomSnap, isUserSelectingText]);

  // Disable browser scroll restoration on mount
  useEffect(() => {
    if (typeof window !== "undefined" && window.history.scrollRestoration) {
      window.history.scrollRestoration = "manual";
    }
  }, []);

  // Track scroll position — message-based threshold
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const handleScroll = () => {
      const isAtTop = container.scrollTop <= 50;
      setIsScrolledToTop(isAtTop);

      const scrollBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      // "Scrolled up" indicator (for "scroll to bottom" button).
      // Threshold must exceed the bottom spacer (bottomInsetPx) or the button
      // fires spuriously when the spacer > 100px (e.g. tall multiline input).
      const scrolledUpThreshold = Math.max(bottomInsetPxRef.current + 30, 80);
      setIsScrolledUp(scrollBottom > scrolledUpThreshold);

      // Key decision: auto-scroll lock only when truly pinned to bottom.
      // Use a slightly wider tolerance than 24px so tiny layout shifts (composer resize,
      // async content expansion) do not incorrectly unlock bottom-lock and leave users
      // manually scrolling after each new message.
      const bottomTolerancePx = Math.max(
        48,
        Math.min(160, bottomInsetPxRef.current + 36),
      );
      isOnBottomMessageRef.current =
        scrollBottom <= bottomTolerancePx && isViewingLastMessage(container);

      onScroll();
    };

    container.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();

    return () => container.removeEventListener("scroll", handleScroll);
  }, [messagesContainerRef, onScroll]);

  // Load more when scrolled to top
  const loadMoreDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(
    null,
  );
  const lastLoadTimeRef = useRef<number>(0);

  useEffect(() => {
    if (!hasOlderMessages) return;
    const container = messagesContainerRef.current;
    if (!container) return;

    const handleScrollForLoading = () => {
      if (container.scrollTop > 100) return;

      const now = Date.now();
      if (now - lastLoadTimeRef.current < 250) return;

      if (loadMoreDebounceRef.current) return;

      loadMoreDebounceRef.current = setTimeout(() => {
        if (container.scrollTop <= 100) {
          lastLoadTimeRef.current = Date.now();
          onLoadMore();
        }
        loadMoreDebounceRef.current = null;
      }, 100);
    };

    container.addEventListener("scroll", handleScrollForLoading);
    return () => {
      container.removeEventListener("scroll", handleScrollForLoading);
      if (loadMoreDebounceRef.current) {
        clearTimeout(loadMoreDebounceRef.current);
      }
    };
  }, [hasOlderMessages, onLoadMore, messagesContainerRef]);

  // Manual scroll anchoring for Safari (no overflow-anchor support)
  // When the user is scrolled up reading and content height changes above them,
  // adjust scrollTop so their reading position doesn't jump.
  useLayoutEffect(() => {
    const el = messagesContainerRef.current;
    if (!el || !didInitialScrollRef.current) return;

    const prevHeight = prevScrollHeightRef.current;
    const newHeight = el.scrollHeight;

    // Only anchor if user is scrolled up past the bottom message and height grew
    if (
      prevHeight > 0 &&
      newHeight !== prevHeight &&
      !isOnBottomMessageRef.current
    ) {
      const delta = newHeight - prevHeight;
      if (delta > 0) {
        el.scrollTop += delta;
      }
    }
    prevScrollHeightRef.current = newHeight;
  });

  useLayoutEffect(() => {
    if (postCount === 0) return;

    if (!didInitialScrollRef.current) {
      // Synchronously measure ChatInput height before first snap (fixed/legacy layout only).
      // In flex layout, the input is a sibling — no inset needed, skip measurement.
      if (!USE_FLEX_CHAT_LAYOUT) {
        const composer = document.querySelector(
          "[data-chat-input-root]",
        ) as HTMLElement | null;
        if (composer) {
          const h = Math.round(composer.getBoundingClientRect().height);
          if (h > 0) setBottomInsetPx(Math.max(12, Math.min(800, h)));
        }
      }

      snapToBottom("auto");
      didInitialScrollRef.current = true;

      // Retry to beat layout churn (increased retry window)
      const ids = [100, 300, 800, 1500].map((d) =>
        setTimeout(() => snapToBottom("auto"), d),
      );
      return () => ids.forEach(clearTimeout);
    }

    // Auto-scroll only while the viewport is explicitly bottom-locked.
    // Do not fall back to `!isScrolledUp`: that state is thresholded/async and can
    // stay false for a moment while the user starts scrolling upward, causing the
    // next activity-stream resize/message paint to snap them back to bottom.
    let retryIds: ReturnType<typeof setTimeout>[] = [];
    if (
      shouldAutoSnapToBottom({
        isOnBottomMessage: isOnBottomMessageRef.current,
        isUserSelectingText: isUserSelectingText(),
      })
    ) {
      queueBottomSnap();
      retryIds = [90, 220, 420].map((delay) =>
        setTimeout(() => queueBottomSnap(), delay),
      );
    }
    prevPostsLenRef.current = postCount;

    return () => {
      retryIds.forEach(clearTimeout);
    };
  }, [postCount, queueBottomSnap, isUserSelectingText, bottomInsetPx]);

  return { isScrolledToTop, isScrolledUp, isIPhone, bottomInsetPx };
}
