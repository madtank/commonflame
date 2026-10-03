/**
 * useMessageStream — React hook for SSE message streaming
 *
 * Subscribes to `message_stream` SSE events and manages in-flight
 * streaming messages with 50ms debounced DOM updates.
 *
 * Contract: SSE Streaming Contract v1 (cipher, 2026-02-16)
 * Event payload: { message_id, conversation_id, agent_id, type, delta, sequence }
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { getRealtimeManager } from "@/lib/realtime-manager";
import { useQueryClient } from "@tanstack/react-query";

export interface StreamChunk {
  message_id: string;
  conversation_id: string;
  agent_id: string;
  type: "chunk" | "done" | "error";
  delta: string;
  sequence: number;
}

export interface StreamingMessage {
  message_id: string;
  conversation_id: string;
  agent_id: string;
  content: string;
  lastSequence: number;
  hasGaps: boolean;
  status: "streaming" | "done" | "error";
  errorText?: string;
  startedAt: number;
}

/**
 * Hook that tracks all in-flight streaming messages.
 * Returns a Map<message_id, StreamingMessage> that updates
 * at most every ~50ms to avoid hammering React's reconciler.
 */
export function useMessageStream() {
  const queryClient = useQueryClient();
  const [streamingMessages, setStreamingMessages] = useState<
    Map<string, StreamingMessage>
  >(new Map());

  // Mutable accumulator — writes happen on every chunk,
  // but we only flush to React state on a 50ms debounce.
  const accRef = useRef<Map<string, StreamingMessage>>(new Map());
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(() => {
    flushTimer.current = null;
    // Shallow-copy so React sees a new reference
    setStreamingMessages(new Map(accRef.current));
  }, []);

  const scheduleFlush = useCallback(() => {
    if (flushTimer.current === null) {
      flushTimer.current = setTimeout(flush, 50);
    }
  }, [flush]);

  useEffect(() => {
    const manager = getRealtimeManager(queryClient);

    const unsubscribe = manager.subscribeToMessageStream(
      (chunk: StreamChunk) => {
        const { message_id, conversation_id, agent_id, type, delta, sequence } =
          chunk;

        const existing = accRef.current.get(message_id);

        if (type === "chunk") {
          if (existing) {
            // Detect sequence gaps
            const hasGaps =
              existing.hasGaps || sequence !== existing.lastSequence + 1;

            accRef.current.set(message_id, {
              ...existing,
              content: existing.content + delta,
              lastSequence: sequence,
              hasGaps,
            });
          } else {
            // First chunk for this message — create placeholder
            accRef.current.set(message_id, {
              message_id,
              conversation_id,
              agent_id,
              content: delta,
              lastSequence: sequence,
              hasGaps: sequence !== 0,
              status: "streaming",
              startedAt: Date.now(),
            });
          }
          scheduleFlush();
        } else if (type === "done") {
          if (existing) {
            accRef.current.set(message_id, {
              ...existing,
              status: "done",
            });
          }
          // Flush immediately on done so the UI can reconcile
          if (flushTimer.current) {
            clearTimeout(flushTimer.current);
            flushTimer.current = null;
          }
          flush();

          // Invalidate posts so we fetch the persisted message
          queryClient.invalidateQueries({ queryKey: ["posts"] });

          // Clean up after a short delay (let the persisted message arrive)
          setTimeout(() => {
            accRef.current.delete(message_id);
            setStreamingMessages(new Map(accRef.current));
          }, 2000);
        } else if (type === "error") {
          if (existing) {
            accRef.current.set(message_id, {
              ...existing,
              status: "error",
              errorText: delta,
            });
          }
          if (flushTimer.current) {
            clearTimeout(flushTimer.current);
            flushTimer.current = null;
          }
          flush();

          // Clean up error state after 10s
          setTimeout(() => {
            accRef.current.delete(message_id);
            setStreamingMessages(new Map(accRef.current));
          }, 10000);
        }
      },
    );

    return () => {
      unsubscribe();
      if (flushTimer.current) {
        clearTimeout(flushTimer.current);
      }
    };
  }, [queryClient, flush, scheduleFlush]);

  return streamingMessages;
}
