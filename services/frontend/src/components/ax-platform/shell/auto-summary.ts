import {
  sanitizeAiSummary,
  type ChatEntry,
} from "@/components/ax-platform/shell/transcript-model";

export const MESSAGE_STREAM_SUMMARY_THRESHOLD_CHARS = 400;

const EMOJI_PATTERN = /\p{Extended_Pictographic}(?:\uFE0F)?/gu;
const MENTION_PATTERN = /(^|[^/\w])(@[a-z0-9][a-z0-9_-]{1,63})/giu;

type AutoSummarySelection = {
  candidateIds: string[];
  knownIds: Map<string, string>;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function isProgressPlaceholder(entry: ChatEntry) {
  const content = (entry.content || "").trim();
  if (/^Working[.…]/i.test(content)) return true;

  const metadata = asRecord(entry.metadata);
  const streamingReply = asRecord(metadata?.streaming_reply);
  return streamingReply?.enabled === true && streamingReply?.final !== true;
}

function getEntryFingerprint(entry: ChatEntry) {
  const content = (entry.content || "").trim();
  return [
    entry.aiSummary ? "summarized" : "pending",
    isProgressPlaceholder(entry) ? "progress" : "final",
    String(content.length),
    content.slice(0, 160),
  ].join(":");
}

export function collectAutoSummaryCandidateIds(
  entries: ChatEntry[],
  knownIds: Map<string, string>,
  requestedIds: Set<string>,
): AutoSummarySelection {
  const nextKnownIds = new Map(knownIds);
  const candidateIds: string[] = [];
  const initialLoad = knownIds.size === 0;

  for (const entry of entries) {
    const nextFingerprint = getEntryFingerprint(entry);
    const previousFingerprint = nextKnownIds.get(entry.id);
    const isNew = previousFingerprint === undefined;
    const changed =
      previousFingerprint !== undefined &&
      previousFingerprint !== nextFingerprint;
    nextKnownIds.set(entry.id, nextFingerprint);

    if (initialLoad || (!isNew && !changed)) continue;
    if (entry.role !== "agent") continue;
    if (entry.isStreaming) continue;
    if (entry.id === "seed-agent") continue;
    if (entry.aiSummary) continue;
    if (requestedIds.has(entry.id)) continue;
    if (isProgressPlaceholder(entry)) continue;

    candidateIds.push(entry.id);
  }

  return {
    candidateIds,
    knownIds: nextKnownIds,
  };
}

export function getAutoSummaryReplacement(
  entry: ChatEntry,
  autoSummarizeEnabled: boolean,
) {
  if (!autoSummarizeEnabled) return null;
  if (entry.role !== "agent") return null;
  if (entry.isStreaming) return null;
  const contentLength = (entry.content || "").trim().length;
  if (contentLength < MESSAGE_STREAM_SUMMARY_THRESHOLD_CHARS) {
    return null;
  }
  const summary = sanitizeAiSummary(entry.aiSummary);
  if (!summary) return null;
  return summary.length < contentLength ? summary : null;
}

export function getAutoSummarySignals(entry: ChatEntry) {
  const content = (entry.content || "").trim();
  const seenMention = new Set<string>();
  const emojis: string[] = [];
  const mentions: string[] = [];

  for (const match of content.matchAll(EMOJI_PATTERN)) {
    const emoji = match[0];
    if (!emoji) continue;
    emojis.push(emoji);
    if (emojis.length >= 48) break;
  }

  for (const match of content.matchAll(MENTION_PATTERN)) {
    const mention = match[2];
    if (!mention || seenMention.has(mention)) continue;
    seenMention.add(mention);
    mentions.push(mention);
    if (mentions.length >= 6) break;
  }

  return {
    fullResponseCharCount: content.length,
    emojis,
    mentions,
    targetLabel: entry.toLabel || null,
  };
}
