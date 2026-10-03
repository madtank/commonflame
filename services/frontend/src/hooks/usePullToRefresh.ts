/**
 * usePullToRefresh.ts
 *
 * Lightweight pull-to-refresh for scrollable containers on mobile.
 * Triggers onRefresh when user pulls down from scroll-top.
 * No dependencies beyond React.
 */
import { useRef, useCallback, useEffect, useState } from "react";

interface PullToRefreshOptions {
  /** The scrollable container ref */
  containerRef: React.RefObject<HTMLElement>;
  /** Called when pull threshold is reached and released */
  onRefresh: () => void | Promise<void>;
  /** Pull distance (px) to trigger refresh. Default 80 */
  threshold?: number;
  /** Max pull distance (px). Default 120 */
  maxPull?: number;
  /** Disable on desktop. Default true */
  mobileOnly?: boolean;
}

interface PullToRefreshState {
  /** Current pull distance (0 when idle) */
  pullDistance: number;
  /** True while onRefresh is executing */
  isRefreshing: boolean;
  /** True when pulled past threshold */
  isPastThreshold: boolean;
}

export function usePullToRefresh({
  containerRef,
  onRefresh,
  threshold = 80,
  maxPull = 120,
  mobileOnly = true,
}: PullToRefreshOptions): PullToRefreshState {
  const [pullDistance, setPullDistance] = useState(0);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const touchStartY = useRef(0);
  const isPulling = useRef(false);

  const isMobile = useCallback(() => {
    if (!mobileOnly) return true;
    return "ontouchstart" in window || navigator.maxTouchPoints > 0;
  }, [mobileOnly]);

  const handleTouchStart = useCallback(
    (e: TouchEvent) => {
      const el = containerRef.current;
      if (!el || isRefreshing) return;
      // Only activate when scrolled to top
      if (el.scrollTop <= 0) {
        touchStartY.current = e.touches[0].clientY;
        isPulling.current = true;
      }
    },
    [containerRef, isRefreshing],
  );

  const handleTouchMove = useCallback(
    (e: TouchEvent) => {
      if (!isPulling.current || isRefreshing) return;
      const el = containerRef.current;
      if (!el || el.scrollTop > 0) {
        isPulling.current = false;
        setPullDistance(0);
        return;
      }

      const deltaY = e.touches[0].clientY - touchStartY.current;
      if (deltaY > 0) {
        // Dampen the pull (feels more natural)
        const dampened = Math.min(maxPull, deltaY * 0.5);
        setPullDistance(dampened);
        // Prevent native scroll/refresh while pulling
        if (dampened > 10) {
          e.preventDefault();
        }
      } else {
        setPullDistance(0);
        isPulling.current = false;
      }
    },
    [containerRef, isRefreshing, maxPull],
  );

  const handleTouchEnd = useCallback(async () => {
    if (!isPulling.current) return;
    isPulling.current = false;

    if (pullDistance >= threshold && !isRefreshing) {
      setIsRefreshing(true);
      setPullDistance(threshold * 0.5); // Shrink to spinner position
      try {
        await onRefresh();
      } catch {
        // Swallow — caller handles errors
      }
      setIsRefreshing(false);
    }
    setPullDistance(0);
  }, [pullDistance, threshold, isRefreshing, onRefresh]);

  useEffect(() => {
    if (!isMobile()) return;
    const el = containerRef.current;
    if (!el) return;

    el.addEventListener("touchstart", handleTouchStart, { passive: true });
    el.addEventListener("touchmove", handleTouchMove, { passive: false });
    el.addEventListener("touchend", handleTouchEnd);

    return () => {
      el.removeEventListener("touchstart", handleTouchStart);
      el.removeEventListener("touchmove", handleTouchMove);
      el.removeEventListener("touchend", handleTouchEnd);
    };
  }, [
    containerRef,
    handleTouchStart,
    handleTouchMove,
    handleTouchEnd,
    isMobile,
  ]);

  return {
    pullDistance,
    isRefreshing,
    isPastThreshold: pullDistance >= threshold,
  };
}
