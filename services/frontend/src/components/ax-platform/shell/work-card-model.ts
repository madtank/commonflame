import type { SpaceAgentConversationCard } from "@/lib/space-agent-api";
import {
  type ChatEntry,
  type ConversationGroup,
  extractMentionHandles,
  getConversationCardMentions,
  getConversationCardMetadataEmojis,
  getConversationCardParticipants,
  getConversationCardSummary,
  getConversationCardThreadId,
  getEntryThreadId,
  getSurfaceCount,
  mergeOrderedLabels,
  summarizeEntryContent,
} from "./transcript-model";
import { collectGroupedSurfaces } from "./surface-model";

type ParticipantRole = ChatEntry["role"] | "participant";

type BuildSignalEmojisInput = {
  summary?: string | null;
  content?: string | null;
  participants?: string[];
  statusLabel?: string | null;
  surfaceCount?: number;
  replyCount?: number;
};

export type WorkCardModel = {
  threadId: string | null;
  threadRootId: string;
  threadCard: SpaceAgentConversationCard | null;
  group: ConversationGroup | null;
  groupedAgentEntries: ChatEntry[];
  groupedCardEntries: ChatEntry[];
  latestCardEntry: ChatEntry;
  isThreadSummaryCard: boolean;
  userLatestInThread: boolean;
  cardId: string;
  expanded: boolean;
  summary: string;
  cardActivityAt: string | null | undefined;
  participantLabels: string[];
  participants: string;
  participantSummary: string | null;
  mentionLabels: string[];
  mentions: string | null;
  mentionSummary: string | null;
  statusValue: string;
  groupedSurfaces: NonNullable<ChatEntry["surfaces"]>;
  groupedSurfaceCount: number;
  signalEmojis: string[];
};

export function buildWorkCardModel({
  entry,
  expanded,
  threadIdByEntryId,
  conversationGroups,
  conversationCardsByThread,
  resolveIdentityLabel,
  formatCardMetaList,
  buildSignalEmojis,
}: {
  entry: ChatEntry;
  expanded: boolean;
  threadIdByEntryId: Map<string, string>;
  conversationGroups: Map<string, ConversationGroup>;
  conversationCardsByThread: Map<string, SpaceAgentConversationCard>;
  resolveIdentityLabel: (
    value?: string | null,
    role?: ParticipantRole,
  ) => string;
  formatCardMetaList: (
    values: string[],
    expanded: boolean,
    limit?: number,
  ) => string | null;
  buildSignalEmojis: (input: BuildSignalEmojisInput) => string[];
}): WorkCardModel {
  const threadId = getEntryThreadId(entry, threadIdByEntryId);
  const group = threadId ? conversationGroups.get(threadId) || null : null;
  const threadCard = threadId
    ? conversationCardsByThread.get(threadId) || null
    : null;
  const groupedAgentEntries = group?.entries.filter(
    (item) => item.role === "agent",
  ) || [entry];
  const groupedCardEntries =
    groupedAgentEntries.length > 0 ? groupedAgentEntries : [entry];
  const latestCardEntry =
    groupedCardEntries[groupedCardEntries.length - 1] || entry;
  const isThreadSummaryCard = Boolean(group) && groupedAgentEntries.length > 1;
  const userLatestInThread = group?.latestEntry.role === "user";
  const cardId = isThreadSummaryCard ? `group-${group?.id}` : entry.id;
  const threadSummary = getConversationCardSummary(threadCard);
  const summary =
    (isThreadSummaryCard && !userLatestInThread ? threadSummary : "") ||
    summarizeEntryContent(latestCardEntry);
  const cardActivityAt =
    isThreadSummaryCard && !userLatestInThread
      ? threadCard?.last_activity_at || latestCardEntry.createdAt
      : latestCardEntry.createdAt;

  const participantLabels = mergeOrderedLabels(
    getConversationCardParticipants(threadCard).map((label) =>
      resolveIdentityLabel(label, "participant"),
    ),
    groupedCardEntries.map((item) =>
      resolveIdentityLabel(item.fromLabel || item.meta, item.role),
    ),
  );
  const participants =
    isThreadSummaryCard && participantLabels.length > 0
      ? participantLabels.join(", ")
      : resolveIdentityLabel(
          latestCardEntry.fromLabel || latestCardEntry.meta,
          latestCardEntry.role,
        );
  const mentionLabels = mergeOrderedLabels(
    getConversationCardMentions(threadCard?.metadata).map((label) =>
      resolveIdentityLabel(label, "participant"),
    ),
    groupedCardEntries.flatMap((item) =>
      extractMentionHandles(item.content).map((handle) =>
        resolveIdentityLabel(`@${handle}`, "participant"),
      ),
    ),
  );
  const mentions = mentionLabels.length > 0 ? mentionLabels.join(", ") : null;
  const participantSummary =
    formatCardMetaList(
      isThreadSummaryCard ? participantLabels : [participants].filter(Boolean),
      expanded,
    ) || participants;
  const mentionSummary = formatCardMetaList(mentionLabels, expanded);
  const statusValue =
    (isThreadSummaryCard && !userLatestInThread ? threadCard?.status : null) ||
    latestCardEntry.statusLabel ||
    (isThreadSummaryCard
      ? `${groupedAgentEntries.length} replies`
      : "Delivered");
  const groupedSurfaces = isThreadSummaryCard
    ? collectGroupedSurfaces(group?.entries || groupedCardEntries)
    : latestCardEntry.surfaces || [];
  const groupedSurfaceCount = getSurfaceCount(groupedSurfaces);
  const signalEmojis = [
    ...getConversationCardMetadataEmojis(
      isThreadSummaryCard ? threadCard?.metadata : null,
    ),
    ...buildSignalEmojis({
      summary,
      content: latestCardEntry.content,
      participants: participantLabels,
      statusLabel: statusValue,
      surfaceCount: groupedSurfaceCount,
      replyCount:
        (isThreadSummaryCard && !userLatestInThread
          ? threadCard?.message_count
          : null) || groupedCardEntries.length,
    }),
  ];

  return {
    threadId,
    threadRootId:
      getConversationCardThreadId(threadCard) || threadId || entry.id,
    threadCard,
    group,
    groupedAgentEntries,
    groupedCardEntries,
    latestCardEntry,
    isThreadSummaryCard,
    userLatestInThread: Boolean(userLatestInThread),
    cardId,
    expanded,
    summary,
    cardActivityAt,
    participantLabels,
    participants,
    participantSummary,
    mentionLabels,
    mentions,
    mentionSummary,
    statusValue,
    groupedSurfaces,
    groupedSurfaceCount,
    signalEmojis,
  };
}
