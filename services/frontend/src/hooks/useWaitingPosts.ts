import { useMemo, useEffect, useRef, useState, useCallback } from "react";
import type { Post } from "@/components/messages/types";
import {
  resolveWaitState,
  resolveWaitTtlSeconds,
  getWaitMetadata,
  getWaitAgentKey,
} from "@/components/messages/utils";

interface UseWaitingPostsOptions {
  renderPosts: Post[];
  parseTimestamp: (s: string) => number;
  agentActivityByPost: Map<string, any>;
  pendingCloudAgentPosts: Set<string | number>;
  isUserSelectingText: () => boolean;
}

export function useWaitingPosts({
  renderPosts,
  parseTimestamp,
  agentActivityByPost,
  pendingCloudAgentPosts,
  isUserSelectingText,
}: UseWaitingPostsOptions) {
  const [now, setNow] = useState(() => Date.now());
  const waitTtlSecondsRef = useRef<Record<string, number>>({});
  const waitExpiryMsRef = useRef<Record<string, number>>({});

  const latestWaitingByAgent = useMemo(() => {
    const latest = new Map<string, { id: number; time: number }>();
    renderPosts.forEach((post) => {
      if (!post) return;
      const waitState = resolveWaitState(post);
      if (!waitState.isWaiting) return;
      const key = getWaitAgentKey(post);
      if (!key) return;
      const sortSource =
        waitState.waitStart ||
        post.uploaded_at ||
        post.created_at ||
        (post as any).timestamp ||
        "";
      const sortTime = parseTimestamp(sortSource);
      const existing = latest.get(key);
      if (!existing || sortTime >= existing.time)
        latest.set(key, { id: post.id, time: sortTime });
    });
    return latest;
  }, [renderPosts, parseTimestamp]);

  const hasWaitingPosts = useMemo(() => {
    if (latestWaitingByAgent.size > 0) return true;
    return renderPosts.some((post) => post && resolveWaitState(post).isWaiting);
  }, [latestWaitingByAgent.size, renderPosts]);

  const needsTimer =
    hasWaitingPosts ||
    agentActivityByPost.size > 0 ||
    pendingCloudAgentPosts.size > 0;

  useEffect(() => {
    if (!needsTimer) return;
    const timer = window.setInterval(() => {
      if (isUserSelectingText()) return;
      setNow(Date.now());
    }, 5000);
    return () => window.clearInterval(timer);
  }, [needsTimer, isUserSelectingText]);

  // Track wait TTL refs
  useEffect(() => {
    if (!renderPosts.length) return;
    const seenIds = new Set<string>();
    const timestamp = Date.now();
    renderPosts.forEach((post) => {
      const idKey = String(post.id);
      if (!idKey) return;
      seenIds.add(idKey);
      const meta = getWaitMetadata(post);
      const ttlValue = resolveWaitTtlSeconds(post, meta);
      if (ttlValue == null) return;
      const normalized = Math.max(0, ttlValue);
      const previousTtl = waitTtlSecondsRef.current[idKey];
      if (previousTtl == null || normalized > previousTtl) {
        waitTtlSecondsRef.current[idKey] = normalized;
        waitExpiryMsRef.current[idKey] = timestamp + normalized * 1000;
      }
    });
    Object.keys(waitExpiryMsRef.current).forEach((key) => {
      if (!seenIds.has(key)) {
        delete waitExpiryMsRef.current[key];
        delete waitTtlSecondsRef.current[key];
      }
    });
  }, [renderPosts]);

  return {
    now,
    latestWaitingByAgent,
    hasWaitingPosts,
    waitExpiryMsRef,
  };
}
