import { useEffect, useRef, useState } from "react";
import { SummarizerService } from "@/services/SummarizerService";
import type { Post } from "@/components/messages/types";

interface UseAutoSummariesOptions {
  posts: Post[];
  isGlobalCondensedMode: boolean;
  isPostExpanded: (id: string | number) => boolean;
}

const AUTO_SUMMARY_BATCH_SIZE = 3;
const AUTO_SUMMARY_COOLDOWN_MS = 5000;
const AUTO_SUMMARY_MAX_AGE_MS = 5 * 60 * 1000;
const AUTO_SUMMARY_MIN_AGE_MS = 10_000;

export function useAutoSummaries({
  posts,
  isGlobalCondensedMode,
  isPostExpanded,
}: UseAutoSummariesOptions) {
  const [summaries, setSummaries] = useState<Record<string, string>>({});
  const [loadingSummaries, setLoadingSummaries] = useState<
    Record<string, boolean>
  >({});
  const processedSummaryIds = useRef(new Set<number | string>());
  const lastAutoSummaryBatchRef = useRef<number>(0);
  const attemptedAutoSummaryIds = useRef(new Set<number | string>());

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      processedSummaryIds.current.clear();
      attemptedAutoSummaryIds.current.clear();
    };
  }, []);

  // Pre-populate summaries from database
  useEffect(() => {
    if (!posts || posts.length === 0) return;
    const newSummaries: Record<string, string> = {};
    posts.forEach((post) => {
      if (post.ai_summary && !processedSummaryIds.current.has(post.id)) {
        newSummaries[post.id] = post.ai_summary;
        processedSummaryIds.current.add(post.id);
      }
    });
    if (Object.keys(newSummaries).length > 0) {
      setSummaries((prev) => ({ ...prev, ...newSummaries }));
    }
  }, [posts]);

  // Auto-generate AI summaries
  useEffect(() => {
    if (!isGlobalCondensedMode || !posts || posts.length === 0) return;
    const now = Date.now();
    if (now - lastAutoSummaryBatchRef.current < AUTO_SUMMARY_COOLDOWN_MS)
      return;
    const eligiblePosts = posts.filter((post) => {
      const content = post.content || "";
      const isLong = content.length > 300 || content.split("\n").length > 5;
      const hasMedia =
        content.match(/!\[.*?\]\(.*?\)/) || content.match(/<img/);
      const expanded = isPostExpanded(post.id);
      const hasSummary = summaries[post.id] || post.ai_summary;
      const alreadyAttempted = attemptedAutoSummaryIds.current.has(post.id);
      let messageAge = Infinity;
      let isNew = false;
      if (post.uploaded_at) {
        const createdAt = new Date(post.uploaded_at).getTime();
        messageAge = now - createdAt;
        isNew = messageAge < AUTO_SUMMARY_MAX_AGE_MS;
      }
      const isTooRecent = messageAge < AUTO_SUMMARY_MIN_AGE_MS;
      return (
        (isLong || hasMedia) &&
        !expanded &&
        !hasSummary &&
        !alreadyAttempted &&
        isNew &&
        !isTooRecent
      );
    });
    if (eligiblePosts.length === 0) return;
    const batch = eligiblePosts.slice(0, AUTO_SUMMARY_BATCH_SIZE);
    lastAutoSummaryBatchRef.current = now;
    batch.forEach((post) => attemptedAutoSummaryIds.current.add(post.id));
    batch.forEach((post) => {
      SummarizerService.getSummary(post.id, post.content)
        .then((summary) => {
          if (!summary) return;
          setSummaries((prev) =>
            prev[post.id] === summary ? prev : { ...prev, [post.id]: summary },
          );
        })
        .catch((err) =>
          console.debug("Auto-summary gen failed for", post.id, err),
        );
    });
  }, [posts, isGlobalCondensedMode, isPostExpanded, summaries]);

  return {
    summaries,
    setSummaries,
    loadingSummaries,
    setLoadingSummaries,
  };
}
