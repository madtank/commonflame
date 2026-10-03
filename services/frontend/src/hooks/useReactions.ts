import { useMemo, useCallback } from "react";
import {
  normalizeParentId,
  classifyReactionText,
  isReactionReply,
  normalizeReactionContent,
} from "@/helpers/reactions";
import {
  emptyReactionCounts,
  type ReactionCounts,
  type UserReactionState,
} from "@/components/EmojiReactions";
import type { Post } from "@/components/messages/types";

interface UseReactionsOptions {
  posts: Post[];
  currentActor: string | undefined;
  demoMode: boolean;
  onDemoReaction?: (
    parentId: string | number,
    emojis: string[],
    actor: string,
  ) => void;
}

export function useReactions({
  posts,
  currentActor,
  demoMode,
  onDemoReaction,
}: UseReactionsOptions) {
  const aggregatedReactions = useMemo(() => {
    const seen = new Set<string>();
    const map: Record<
      string,
      { counts: ReactionCounts; user: UserReactionState }
    > = {};
    const serverCountsParents = new Set<string>();

    for (const p of posts) {
      const pid = normalizeParentId(p.id) ?? p.id;
      if (pid == null) continue;
      const key = String(pid);
      const rawCounts = p.reactions;
      if (!rawCounts || typeof rawCounts !== "object") continue;
      const counts: ReactionCounts = {};
      Object.entries(rawCounts).forEach(([emoji, count]) => {
        const n = typeof count === "number" ? count : Number(count);
        if (!Number.isFinite(n) || n <= 0) return;
        counts[emoji] = n;
      });
      if (Object.keys(counts).length === 0) continue;
      map[key] = { counts, user: {} };
      serverCountsParents.add(key);
    }

    for (const p of posts) {
      const pid = normalizeParentId(p.parent_id ?? p.response_to);
      if (!pid) continue;
      const parentKey = String(pid);
      const hasServerCounts = serverCountsParents.has(parentKey);
      const reactionType = classifyReactionText(p.content);
      if (!reactionType) continue;

      let emojis: string[] = [];
      if (reactionType === "emoji") {
        const normalized = normalizeReactionContent(p.content);
        const emojiPattern =
          /\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?(?:\u200D\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?)?/gu;
        const matches = normalized.match(emojiPattern);
        emojis = matches || [normalized];
      } else {
        const emojiMap: Record<string, string> = {
          kudos: "👍",
          abuse: "👎",
          flag: "🚩",
          helpful: "💡",
          excellent: "⭐",
        };
        emojis = [emojiMap[reactionType] || "👍"];
      }

      for (const emoji of emojis) {
        const key = `${parentKey}:${emoji}:${p.username}`;
        if (seen.has(key)) continue;
        seen.add(key);
        if (!map[parentKey]) map[parentKey] = { counts: {}, user: {} };
        if (!hasServerCounts) {
          if (!map[parentKey].counts[emoji]) map[parentKey].counts[emoji] = 0;
          map[parentKey].counts[emoji]++;
        }
        if (currentActor && p.username === currentActor)
          map[parentKey].user[emoji] = true;
      }
    }
    return map;
  }, [posts, currentActor]);

  const handleReact = useCallback(
    async (pid: string | number, emoji: string, toggledOn: boolean) => {
      const reactionKey = String(normalizeParentId(pid) ?? pid);
      const existingReactions = aggregatedReactions[reactionKey];
      if (toggledOn && existingReactions?.user[emoji]) return;
      if (!toggledOn) return;

      if (demoMode && onDemoReaction) {
        const parentId = normalizeParentId(pid);
        onDemoReaction(parentId!, [emoji], currentActor || "you");
        return;
      }
      try {
        const { api } = await import("@/lib/api-clean");
        const parentId = normalizeParentId(pid);
        await api.postMessage(emoji, { parentId });
      } catch (err) {
        console.error("Failed to send reaction command", err);
      }
    },
    [aggregatedReactions, demoMode, onDemoReaction, currentActor],
  );

  // Count non-reaction replies
  const repliesByParent = useMemo(() => {
    const m = new Map<string | number, number>();
    for (const p of posts) {
      const pid = normalizeParentId(p.parent_id ?? p.response_to);
      if (
        pid === undefined ||
        (typeof pid === "number" && !Number.isFinite(pid))
      )
        continue;
      if (!isReactionReply(p.content, pid)) m.set(pid, (m.get(pid) || 0) + 1);
    }
    return m;
  }, [posts]);

  const firstReplyIdByParent = useMemo(() => {
    const m = new Map<string | number, string | number>();
    for (const p of posts) {
      const pid = normalizeParentId(p.parent_id ?? p.response_to);
      if (
        pid === undefined ||
        (typeof pid === "number" && !Number.isFinite(pid))
      )
        continue;
      if (isReactionReply(p.content, pid)) continue;
      if (!m.has(pid)) m.set(pid, p.id);
    }
    return m;
  }, [posts]);

  return {
    aggregatedReactions,
    handleReact,
    repliesByParent,
    firstReplyIdByParent,
  };
}
