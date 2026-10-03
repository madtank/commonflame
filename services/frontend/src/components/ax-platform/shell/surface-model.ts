import type { SpaceAgentConversationCard } from "@/lib/space-agent-api";
import type { SpaceAgentNormalizedSurface } from "@/lib/space-agent-surfaces";
import type { ChatEntry, ConversationGroup } from "./transcript-model";
import {
  getConversationCardSummary,
  getEntryThreadId,
  shouldUseSummaryCard,
} from "./transcript-model";

type SurfaceGroupingState = {
  cardsEnabled: boolean;
  entry: ChatEntry;
  threadIdByEntryId: Map<string, string>;
  conversationGroups: Map<string, ConversationGroup>;
  conversationCardsByThread: Map<string, SpaceAgentConversationCard>;
  conversationCardAnchors: Map<string, ChatEntry>;
};

function getSurfaceDedupKey(surface: SpaceAgentNormalizedSurface) {
  return [
    surface.kind,
    surface.id,
    surface.sourceMessageId || "",
    surface.kind === "widget" ? surface.widget.tool_call_id || "" : "",
  ].join(":");
}

export function collectGroupedSurfaces(
  entries: ChatEntry[],
): SpaceAgentNormalizedSurface[] {
  const deduped = new Map<string, SpaceAgentNormalizedSurface>();

  for (const entry of entries) {
    for (const surface of entry.surfaces || []) {
      const key = getSurfaceDedupKey(surface);
      if (!deduped.has(key)) {
        deduped.set(key, surface);
      }
    }
  }

  return [...deduped.values()];
}

export function shouldSuppressInlineEntrySurfaces({
  cardsEnabled,
  entry,
  threadIdByEntryId,
  conversationGroups,
  conversationCardsByThread,
  conversationCardAnchors,
}: SurfaceGroupingState) {
  if (!cardsEnabled || !entry.surfaces?.length) return false;

  const threadId = getEntryThreadId(entry, threadIdByEntryId);
  if (!threadId) return false;

  const group = conversationGroups.get(threadId);
  const threadCard = conversationCardsByThread.get(threadId);
  const anchor = conversationCardAnchors.get(threadId);
  if (!group || !threadCard || !anchor || anchor.id === entry.id) return false;

  const groupedAgentEntries = group.entries.filter(
    (candidate) => candidate.role === "agent" && !candidate.isStreaming,
  );
  if (groupedAgentEntries.length <= 1) return false;

  const hasThreadSummary = Boolean(getConversationCardSummary(threadCard));
  if (!hasThreadSummary && !shouldUseSummaryCard(group.latestEntry, true)) {
    return false;
  }

  return collectGroupedSurfaces(group.entries).length > 0;
}
