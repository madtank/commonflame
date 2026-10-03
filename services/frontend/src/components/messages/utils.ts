import type { Post, MessageMetadata } from "./types";

// Normalize usernames for reliable author checks
export const normUser = (s?: string | null) =>
  (s || "").trim().toLowerCase().replace(/^@/, "");

export const clamp01 = (value: number): number =>
  Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;

// Compute trust score from post's intelligence fields (high quality + low spam/toxicity = high trust)
export const computeTrustScore = (
  quality: number,
  spam: number,
  toxicity: number,
): number => {
  const q = clamp01(quality);
  const s = clamp01(spam);
  const t = clamp01(toxicity);
  return clamp01(q * (1 - s) * (1 - t));
};

export const parseNumber = (value: unknown): number | null => {
  if (value == null) return null;
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

export const formatCountdown = (secondsLeft: number) => {
  const clamped = Math.max(0, secondsLeft);
  const hours = Math.floor(clamped / 3600);
  const minutes = Math.floor((clamped % 3600) / 60);
  const seconds = clamped % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
};

export const formatElapsed = (secondsElapsed: number) => {
  const minutes = Math.floor(secondsElapsed / 60);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d`;
};

export const detectSentiment = (
  text: string,
): "positive" | "neutral" | "negative" => {
  const positiveWords = [
    "great",
    "awesome",
    "excellent",
    "good",
    "love",
    "amazing",
    "perfect",
    "🎉",
    "✅",
    "🚀",
    "💪",
    "🌟",
    "👍",
  ];
  const negativeWords = [
    "error",
    "fail",
    "problem",
    "issue",
    "bug",
    "broken",
    "wrong",
    "❌",
    "🔥",
    "💥",
    "😞",
    "👎",
  ];

  const lowerText = text.toLowerCase();
  const positiveCount = positiveWords.filter((word) =>
    lowerText.includes(word),
  ).length;
  const negativeCount = negativeWords.filter((word) =>
    lowerText.includes(word),
  ).length;

  if (positiveCount > negativeCount && positiveCount > 0) return "positive";
  if (negativeCount > positiveCount && negativeCount > 0) return "negative";
  return "neutral";
};

export const sentimentColors: Record<string, string> = {
  positive: "bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300",
  negative: "bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300",
  neutral: "bg-gray-100 dark:bg-gray-900 text-gray-700 dark:text-gray-300",
};

export const sentimentIcons: Record<string, string> = {
  positive: "😊",
  negative: "😟",
  neutral: "😐",
};

export const MAX_CONDENSED_MEDIA_ITEMS = 5;

// Helper functions extracted from LiveMonitor
export const extractEmojis = (text: string): string[] => {
  const emojiRegex = /(\p{Emoji_Presentation}|\p{Extended_Pictographic})/gu;
  return text.match(emojiRegex) || [];
};

export const extractMentionsAndTags = (text: string) => {
  const mentions = text.match(/@[a-zA-Z0-9_-]+/g) || [];
  const hashtags = text.match(/#\w+/g) || [];
  return { mentions, hashtags };
};

export const getWaitMetadata = (post: Post): MessageMetadata => {
  const raw = post.metadata ?? post.meta ?? null;
  if (raw && typeof raw === "object") {
    return raw as MessageMetadata;
  }
  return {};
};

export const isWaitingFlag = (value: unknown): boolean =>
  value === true || value === 1 || value === "true";

export const resolveWaitStart = (
  post: Post,
  meta: MessageMetadata,
): string | null =>
  post.waiting_since ||
  meta.waiting_since ||
  meta.waiting_started_at ||
  meta.wait_started_at ||
  post.waiting_at ||
  post.wait_started_at ||
  null;

export const resolveWaitExpiry = (
  post: Post,
  meta: MessageMetadata,
): string | null => {
  const direct =
    post.waiting_expires_at ||
    post.wait_expires_at ||
    post.waiting_until ||
    post.wait_until ||
    meta.waiting_expires_at ||
    meta.wait_expires_at ||
    meta.waiting_until ||
    meta.wait_until;

  return direct ? String(direct) : null;
};

export const resolveWaitFlag = (post: Post, meta: MessageMetadata): boolean =>
  isWaitingFlag(post.waiting_for_response) ||
  isWaitingFlag(meta.waiting_for_response) ||
  isWaitingFlag(meta.waiting) ||
  isWaitingFlag(post.waiting);

export const resolveWaitTtlSeconds = (
  post: Post,
  meta: MessageMetadata,
): number | null => {
  const ttlSeconds =
    parseNumber(post.waiting_ttl_seconds) ??
    parseNumber(post.wait_ttl_seconds) ??
    parseNumber(post.waiting_ttl_seconds) ??
    parseNumber(post.wait_ttl) ??
    parseNumber(post.waiting_ttl) ??
    parseNumber(meta.waiting_ttl_seconds) ??
    parseNumber(meta.wait_ttl_seconds) ??
    parseNumber(meta.waiting_ttl) ??
    parseNumber(meta.wait_ttl) ??
    null;
  const ttlMs =
    parseNumber(post.wait_ttl_ms) ??
    parseNumber(post.waiting_ttl_ms) ??
    parseNumber(meta.wait_ttl_ms) ??
    parseNumber(meta.waiting_ttl_ms) ??
    null;
  if (ttlMs != null) return Math.ceil(ttlMs / 1000);
  return ttlSeconds;
};

export const resolveWaitState = (post: Post) => {
  const meta = getWaitMetadata(post);
  const hasWaitFlag = resolveWaitFlag(post, meta);
  const ttlSeconds = resolveWaitTtlSeconds(post, meta);
  const waitStartRaw = resolveWaitStart(post, meta);
  const fallbackStart =
    post.uploaded_at ||
    post.created_at ||
    meta.created_at ||
    meta.uploaded_at ||
    null;
  const waitStart =
    waitStartRaw || (hasWaitFlag || ttlSeconds != null ? fallbackStart : null);
  const waitExpiry = resolveWaitExpiry(post, meta);
  const isWaiting =
    hasWaitFlag || !!waitStart || !!waitExpiry || ttlSeconds != null;
  return { meta, hasWaitFlag, ttlSeconds, waitStart, waitExpiry, isWaiting };
};

export const getWaitAgentKey = (post: Post): string | null => {
  const rawId =
    post.author_id ??
    (post as any).author?.id ??
    (post as any).agent_id ??
    (post as any).agentId ??
    null;
  if (rawId != null && String(rawId).trim()) {
    return `id:${String(rawId)}`;
  }

  const rawName =
    post.username ??
    (post as any).author?.name ??
    (post as any).agent_name ??
    (post as any).agentName ??
    "";
  const normalized = normUser(rawName);
  return normalized ? `name:${normalized}` : null;
};

export const hasActiveSelection = (): boolean => {
  if (typeof window === "undefined") return false;
  const selection = window.getSelection();
  return !!selection && !selection.isCollapsed;
};

// Time ranges for display
export const TIME_RANGES = [
  { value: "all", label: "All Time", hours: null },
  { value: "15m", label: "Last 15 Minutes", hours: 0.25 },
  { value: "30m", label: "Last 30 Minutes", hours: 0.5 },
  { value: "1h", label: "Last Hour", hours: 1 },
  { value: "2h", label: "Last 2 Hours", hours: 2 },
  { value: "6h", label: "Last 6 Hours", hours: 6 },
  { value: "12h", label: "Last 12 Hours", hours: 12 },
  { value: "24h", label: "Last 24 Hours", hours: 24 },
  { value: "3d", label: "Last 3 Days", hours: 24 * 3 },
  { value: "7d", label: "Last 7 Days", hours: 24 * 7 },
  { value: "30d", label: "Last 30 Days", hours: 24 * 30 },
] as const;

// Map of durations for time filtering
export const TIME_FILTER_WINDOWS: Record<string, number | null> = {
  all: null,
  "5m": 5 * 60 * 1000,
  "15m": 15 * 60 * 1000,
  "30m": 30 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "8h": 8 * 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "3d": 3 * 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
};
