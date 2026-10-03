import { useCallback, useEffect, useRef, useState } from "react";

import type { PendingProgressShape } from "@/components/ax-platform/shell/pending-response";

export type StreamingState = {
  id: string;
  content: string;
  agentName: string;
  parentId?: string | null;
  statusLabel?: string | null;
  toolName?: string | null;
  activity?: string | null;
  progress?: PendingProgressShape | null;
  reason?: string | null;
  errorMessage?: string | null;
  retryAfterSeconds?: number | null;
} | null;

export type StreamTiming = {
  sendStartedAt: number | null;
  firstAgentProcessingAt: number | null;
  firstDeltaAt: number | null;
  finalMessageAt: number | null;
  eventCount: number;
  deltaCount: number;
  activeMessageId: string | null;
  lastErrorText: string | null;
};

const AUTO_RESET_DELAY_MS = 750;
const BASE_CHARS_PER_FRAME = 8;
const MAX_CHARS_PER_FRAME = 48;
const FINALIZING_MIN_CHARS_PER_FRAME = 20;
const FINALIZING_MAX_CHARS_PER_FRAME = 96;
const STREAM_RENDER_BATCH_MS = 48;

/**
 * Strip model artifacts from streamed content:
 * - <thinking>...</thinking> blocks (Nova Pro leaks these)
 * - Trailing {ax_intel: ...} JSON metadata
 * - Incomplete opening <thinking> or {ax_intel tags at the end
 */
export function stripModelArtifacts(raw: string): string {
  // Remove complete <thinking>...</thinking> blocks
  let cleaned = raw.replace(/<thinking>[\s\S]*?<\/thinking>\s*/g, "");
  // Remove incomplete <thinking> block at the end (still being streamed)
  cleaned = cleaned.replace(/<thinking>[\s\S]*$/, "");
  // Remove partial <thinking tag being typed out char by char (e.g. "<t", "<thin", "<thinking")
  cleaned = cleaned.replace(
    /<t(?:h(?:i(?:n(?:k(?:i(?:n(?:g)?)?)?)?)?)?)?$/,
    "",
  );
  // Remove trailing {ax_intel... JSON (complete or partial)
  cleaned = cleaned.replace(/\s*\{["']?ax_intel["']?\s*:[\s\S]*$/, "");
  return cleaned;
}

/**
 * Hold the streaming entry visible for this long after onFinalMessage
 * before allowing the transition to the persisted/summary message.
 */
const FINAL_HOLD_MS = 150;

function initialTiming(): StreamTiming {
  return {
    sendStartedAt: null,
    firstAgentProcessingAt: null,
    firstDeltaAt: null,
    finalMessageAt: null,
    eventCount: 0,
    deltaCount: 0,
    activeMessageId: null,
    lastErrorText: null,
  };
}

export function useStreamBuffer() {
  const [streamingEntry, setStreamingEntry] = useState<StreamingState>(null);
  const [streamTiming, setStreamTiming] = useState<StreamTiming>(initialTiming);

  // Raw accumulated content (all received text)
  const accumulatorRef = useRef<StreamingState>(null);
  const renderedContentRef = useRef("");
  const timingRef = useRef<StreamTiming>(initialTiming());
  const finalTimeoutRef = useRef<number | null>(null);
  const renderBatchTimeoutRef = useRef<number | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const hasRenderedDeltaRef = useRef(false);
  const lastVisibleRenderAtRef = useRef(0);
  const finishingRef = useRef(false);
  const catchingUpRef = useRef(false);
  // When true, the final message arrived but we're holding for FINAL_HOLD_MS
  const holdingFinalRef = useRef(false);

  const clearFinalTimeout = useCallback(() => {
    if (finalTimeoutRef.current !== null) {
      window.clearTimeout(finalTimeoutRef.current);
      finalTimeoutRef.current = null;
    }
  }, []);

  const clearAnimationFrame = useCallback(() => {
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
  }, []);

  const clearRenderBatchTimeout = useCallback(() => {
    if (renderBatchTimeoutRef.current !== null) {
      window.clearTimeout(renderBatchTimeoutRef.current);
      renderBatchTimeoutRef.current = null;
    }
  }, []);

  const commitVisibleEntry = useCallback(() => {
    const current = accumulatorRef.current;
    if (!current) {
      setStreamingEntry(null);
      return;
    }

    setStreamingEntry({
      ...current,
      content: stripModelArtifacts(renderedContentRef.current),
    });
    if (renderedContentRef.current) {
      hasRenderedDeltaRef.current = true;
      lastVisibleRenderAtRef.current = Date.now();
    }
    setStreamTiming({ ...timingRef.current });
  }, []);

  const reset = useCallback(() => {
    clearFinalTimeout();
    clearAnimationFrame();
    clearRenderBatchTimeout();
    accumulatorRef.current = null;
    renderedContentRef.current = "";
    hasRenderedDeltaRef.current = false;
    lastVisibleRenderAtRef.current = 0;
    finishingRef.current = false;
    catchingUpRef.current = false;
    holdingFinalRef.current = false;
    setStreamingEntry(null);
    setStreamTiming(initialTiming());
    timingRef.current = initialTiming();
  }, [clearAnimationFrame, clearFinalTimeout, clearRenderBatchTimeout]);

  const armAutoReset = useCallback(() => {
    clearFinalTimeout();
    finalTimeoutRef.current = window.setTimeout(() => {
      holdingFinalRef.current = false;

      finalTimeoutRef.current = window.setTimeout(() => {
        if (finishingRef.current) {
          finalTimeoutRef.current = null;
          reset();
        }
      }, AUTO_RESET_DELAY_MS);
    }, FINAL_HOLD_MS);
  }, [clearFinalTimeout, reset]);

  const scheduleRender = useCallback(() => {
    if (
      animationFrameRef.current !== null ||
      renderBatchTimeoutRef.current !== null
    ) {
      return;
    }

    const tick = () => {
      animationFrameRef.current = null;

      const current = accumulatorRef.current;
      if (!current) return;

      const target = current.content || "";
      const visible = renderedContentRef.current;
      const remaining = target.length - visible.length;

      if (remaining <= 0) {
        commitVisibleEntry();
        if (finishingRef.current && !holdingFinalRef.current) {
          catchingUpRef.current = false;
          holdingFinalRef.current = true;
          armAutoReset();
        }
        return;
      }

      const step = finishingRef.current
        ? Math.max(
            FINALIZING_MIN_CHARS_PER_FRAME,
            Math.min(FINALIZING_MAX_CHARS_PER_FRAME, Math.ceil(remaining / 4)),
          )
        : Math.max(
            BASE_CHARS_PER_FRAME,
            Math.min(MAX_CHARS_PER_FRAME, Math.ceil(remaining / 10)),
          );

      renderedContentRef.current = target.slice(0, visible.length + step);
      commitVisibleEntry();

      if (renderedContentRef.current.length < target.length) {
        scheduleRender();
      } else if (finishingRef.current && !holdingFinalRef.current) {
        catchingUpRef.current = false;
        holdingFinalRef.current = true;
        armAutoReset();
      }
    };

    const queueFrame = () => {
      animationFrameRef.current = window.requestAnimationFrame(tick);
    };

    const shouldBatch = hasRenderedDeltaRef.current && !finishingRef.current;
    if (shouldBatch) {
      const elapsedMs = Date.now() - lastVisibleRenderAtRef.current;
      const delayMs = Math.max(0, STREAM_RENDER_BATCH_MS - elapsedMs);
      if (delayMs > 0) {
        renderBatchTimeoutRef.current = window.setTimeout(() => {
          renderBatchTimeoutRef.current = null;
          queueFrame();
        }, delayMs);
        return;
      }
    }

    queueFrame();
  }, [armAutoReset, commitVisibleEntry]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      clearFinalTimeout();
      clearAnimationFrame();
      clearRenderBatchTimeout();
    };
  }, [clearAnimationFrame, clearFinalTimeout, clearRenderBatchTimeout]);

  const onProcessing = useCallback(
    (payload: {
      id: string;
      agentName: string;
      parentId?: string | null;
      statusLabel?: string;
      toolName?: string | null;
      activity?: string | null;
      progress?: PendingProgressShape | null;
      reason?: string | null;
      errorMessage?: string | null;
      retryAfterSeconds?: number | null;
    }) => {
      // Cancel any pending auto-reset from a previous stream
      clearFinalTimeout();

      const current = accumulatorRef.current;
      if (current?.id && current.id !== payload.id) {
        clearAnimationFrame();
        clearRenderBatchTimeout();
        renderedContentRef.current = "";
        hasRenderedDeltaRef.current = false;
        lastVisibleRenderAtRef.current = 0;
      }
      const sameStream = current?.id === payload.id;
      const entry: StreamingState = {
        id: sameStream ? current!.id : payload.id,
        content: sameStream ? current!.content || "" : "",
        agentName: sameStream
          ? current!.agentName || payload.agentName
          : payload.agentName,
        parentId: sameStream
          ? (payload.parentId ?? current!.parentId ?? null)
          : (payload.parentId ?? null),
        statusLabel: payload.statusLabel || "queued",
        toolName: payload.toolName ?? (sameStream ? current!.toolName : null),
        activity: payload.activity ?? (sameStream ? current!.activity : null),
        progress: payload.progress ?? (sameStream ? current!.progress : null),
        reason: payload.reason ?? (sameStream ? current!.reason : null),
        errorMessage:
          payload.errorMessage ?? (sameStream ? current!.errorMessage : null),
        retryAfterSeconds:
          payload.retryAfterSeconds ??
          (sameStream ? current!.retryAfterSeconds : null),
      };
      accumulatorRef.current = entry;
      finishingRef.current = false;
      catchingUpRef.current = false;
      holdingFinalRef.current = false;

      timingRef.current = {
        ...timingRef.current,
        firstAgentProcessingAt:
          timingRef.current.firstAgentProcessingAt || Date.now(),
        eventCount: timingRef.current.eventCount + 1,
        activeMessageId: payload.id,
      };

      // Processing events flush immediately for instant feedback
      commitVisibleEntry();
    },
    [
      clearAnimationFrame,
      clearFinalTimeout,
      clearRenderBatchTimeout,
      commitVisibleEntry,
    ],
  );

  const onDelta = useCallback(
    (payload: {
      id: string;
      delta: string;
      agentName?: string;
      parentId?: string | null;
    }) => {
      const current = accumulatorRef.current;
      const isSameId = current?.id === payload.id;
      if (!isSameId) {
        clearAnimationFrame();
        clearRenderBatchTimeout();
        renderedContentRef.current = "";
        hasRenderedDeltaRef.current = false;
        lastVisibleRenderAtRef.current = 0;
      }

      const nextEntry = {
        id: payload.id,
        content: `${isSameId ? current.content : ""}${payload.delta}`,
        agentName: isSameId
          ? current.agentName || payload.agentName || "agent"
          : payload.agentName || "agent",
        parentId: isSameId
          ? (payload.parentId ?? current.parentId ?? null)
          : (payload.parentId ?? null),
        statusLabel: "streaming",
        toolName: isSameId ? (current.toolName ?? null) : null,
      };
      accumulatorRef.current = nextEntry;

      timingRef.current = {
        ...timingRef.current,
        firstDeltaAt: timingRef.current.firstDeltaAt || Date.now(),
        eventCount: timingRef.current.eventCount + 1,
        deltaCount: timingRef.current.deltaCount + 1,
        activeMessageId: payload.id,
      };

      scheduleRender();
    },
    [clearAnimationFrame, clearRenderBatchTimeout, scheduleRender],
  );

  const onFinalMessage = useCallback(() => {
    finishingRef.current = true;

    timingRef.current = {
      ...timingRef.current,
      finalMessageAt: timingRef.current.finalMessageAt || Date.now(),
      eventCount: timingRef.current.eventCount + 1,
    };

    const current = accumulatorRef.current;
    if (!current) {
      catchingUpRef.current = false;
      holdingFinalRef.current = true;
      armAutoReset();
      setStreamTiming({ ...timingRef.current });
      return;
    }

    if (renderedContentRef.current.length >= current.content.length) {
      catchingUpRef.current = false;
      holdingFinalRef.current = true;
      commitVisibleEntry();
      armAutoReset();
      return;
    }

    catchingUpRef.current = true;
    clearRenderBatchTimeout();
    scheduleRender();
  }, [
    armAutoReset,
    clearRenderBatchTimeout,
    commitVisibleEntry,
    scheduleRender,
  ]);

  const markSendStarted = useCallback(() => {
    const next = initialTiming();
    next.sendStartedAt = Date.now();
    timingRef.current = next;
    setStreamTiming(next);
  }, []);

  const setError = useCallback((text: string) => {
    timingRef.current = {
      ...timingRef.current,
      lastErrorText: text,
    };
    setStreamTiming({ ...timingRef.current });
  }, []);

  const incrementEventCount = useCallback(() => {
    timingRef.current = {
      ...timingRef.current,
      eventCount: timingRef.current.eventCount + 1,
    };
  }, []);

  return {
    streamingEntry,
    streamTiming,
    isFinishing: finishingRef.current,
    isCatchingUp: catchingUpRef.current,
    holdingFinal: holdingFinalRef.current,
    onProcessing,
    onDelta,
    onFinalMessage,
    markSendStarted,
    setError,
    incrementEventCount,
    reset,
  };
}
