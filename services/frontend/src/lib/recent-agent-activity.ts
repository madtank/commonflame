import { deriveAuthorRole } from "@/helpers/author-type";
import type { Post } from "@/types/message";

export interface RecentAgentActivity {
  username: string;
  lastMessageAt: string;
  messageCount: number;
}

interface RecentAgentStats {
  lastMessageAt: number;
  recentCount: number;
  totalCount: number;
}

export const RECENT_AGENT_ACTIVITY_WINDOW_MS = 24 * 60 * 60 * 1000; // 24 hours
export const RECENT_AGENT_ACTIVITY_MAX_CANDIDATES = 10;

const normalizeHandle = (value: string) =>
  value.trim().toLowerCase().replace(/^@/, "");

export function buildRecentAgentActivity(
  posts: Post[],
  knownAgentHandles?: Iterable<string>,
  now: number = Date.now(),
): RecentAgentActivity[] {
  if (!Array.isArray(posts) || posts.length === 0) {
    return [];
  }

  const knownAgents = new Set(
    Array.from(knownAgentHandles ?? [], (handle) =>
      normalizeHandle(handle),
    ).filter(Boolean),
  );
  const cutoff = now - RECENT_AGENT_ACTIVITY_WINDOW_MS;
  const stats = new Map<string, RecentAgentStats>();

  posts.forEach((post) => {
    const username = String(post?.username || "").trim();
    if (!username || username.startsWith("__")) return;

    const normalized = normalizeHandle(username);
    const isKnownAgent = normalized ? knownAgents.has(normalized) : false;
    const role = deriveAuthorRole(post);
    if (role !== "AGENT" && !isKnownAgent) return;

    const timestamp = Date.parse(post.uploaded_at || post.created_at || "");
    if (!Number.isFinite(timestamp)) return;

    const existing = stats.get(normalized) ?? {
      lastMessageAt: timestamp,
      recentCount: 0,
      totalCount: 0,
    };

    existing.lastMessageAt = Math.max(existing.lastMessageAt, timestamp);
    existing.totalCount += 1;
    if (timestamp >= cutoff) {
      existing.recentCount += 1;
    }

    stats.set(normalized, existing);
  });

  if (stats.size === 0) {
    return [];
  }

  const entries = Array.from(stats.entries()).map(([normalized, data]) => ({
    normalized,
    username: normalized,
    lastMessageAt: new Date(data.lastMessageAt).toISOString(),
    messageCount: data.recentCount,
    totalCount: data.totalCount,
  }));

  // Don't filter out agents — show all known agents, sorted by recency.
  // Agents with no recent activity sort to the bottom but still appear.
  const filtered = entries;

  filtered.sort((a, b) => {
    const recency = Date.parse(b.lastMessageAt) - Date.parse(a.lastMessageAt);
    if (recency !== 0) return recency;
    const recentCount = b.messageCount - a.messageCount;
    if (recentCount !== 0) return recentCount;
    const totalCount = b.totalCount - a.totalCount;
    if (totalCount !== 0) return totalCount;
    return a.normalized.localeCompare(b.normalized);
  });

  return filtered
    .slice(0, RECENT_AGENT_ACTIVITY_MAX_CANDIDATES)
    .map(({ username, lastMessageAt, messageCount }) => ({
      username,
      lastMessageAt,
      messageCount,
    }));
}
