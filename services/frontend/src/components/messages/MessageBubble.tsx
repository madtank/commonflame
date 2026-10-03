import React, { useState, useCallback, memo } from "react";
import {
  Copy,
  Sparkles,
  Clock,
  Loader2,
  Bot,
  XCircle,
  RotateCcw,
  PauseCircle,
  Check,
  Terminal,
} from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { formatRelativeTime } from "@/lib/timezone-utils";
import { AvatarName } from "@/components/AvatarName";
import {
  EmojiReactions,
  emptyReactionCounts,
  type ReactionCounts,
  type UserReactionState,
} from "@/components/EmojiReactions";
import { ReplyComposer } from "@/components/ReplyComposer";
import {
  normalizeParentId,
  isReactionReply,
  getShortId,
} from "@/helpers/reactions";
import { api, type RosterEntry } from "@/lib/api-clean";
import { storage } from "@/lib/storage";
import { sanitizeMessageContent } from "@/lib/content-sanitizer";
import { extractMediaFromContent, limitMediaItems } from "@/lib/media-utils";
import { SummarizerService } from "@/services/SummarizerService";
import { SummarySkeleton } from "@/components/ui/SummarySkeleton";
import { isFromInternalAgent } from "@/helpers/agents";
import { deriveAuthorRole } from "@/helpers/author-type";
import type { AgentActivityState, Post } from "./types";
import {
  normUser,
  clamp01,
  computeTrustScore,
  detectSentiment,
  formatCountdown,
  formatElapsed,
  resolveWaitState,
  getWaitAgentKey,
  hasActiveSelection,
  MAX_CONDENSED_MEDIA_ITEMS,
} from "./utils";
import { ReasoningContent, renderMarkdownContent } from "./MessageContent";
import { renderMediaBlocks } from "./MessageMedia";

export interface MessageBubbleProps {
  post: Post;
  posts: Post[]; // full list for parent lookup
  isRead: boolean;
  isExpanded: boolean;
  isGlobalCondensedMode?: boolean;
  onMessageRead: (messageId: string) => void;
  onPostExpand: (postId: number) => void;
  onHashtagClick: (hashtag: string) => void;
  onAgentClick: (agent: string) => void;
  onReplyToPost: (post: {
    id: number;
    content: string;
    username: string;
    waiting_since?: string;
  }) => void;
  reactionEntry: { counts: ReactionCounts; user: UserReactionState };
  handleReact: (
    pid: string | number,
    emoji: string,
    toggledOn: boolean,
  ) => void;
  repliesByParent: Map<string | number, number>;
  firstReplyIdByParent: Map<string | number, string | number>;
  messageRowRefs: React.MutableRefObject<Record<string, HTMLDivElement | null>>;
  rosterLookup?: (handleOrId: string) => RosterEntry | undefined;
  demoMode?: boolean;
  viewerUsername?: string;
  spaceId?: string | null;
  currentActor?: string;
  parseTimestamp: (s: string) => number;
  now: number;
  latestWaitingByAgent: Map<string, { id: number; time: number }>;
  waitExpiryMsRef: React.MutableRefObject<Record<string, number>>;
  // Cloud agent props
  pendingCloudAgentPosts: Set<number | string>;
  sentNonCloudAgentPosts: Set<number | string>;
  agentActivityByPost: Map<string, AgentActivityState>;
  failedCloudAgentPosts: Map<
    string,
    {
      agent_name: string;
      error: string;
      error_type?: string;
      timestamp: number;
      retrying?: boolean;
    }
  >;
  skippedCloudAgentPosts: Map<
    string,
    { agent_name: string; reason: string; timestamp: number }
  >;
  onRetryCloudAgent?: (postId: string) => void;
  // Summary state
  summaries: Record<string, string>;
  setSummaries: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  loadingSummaries: Record<string, boolean>;
  setLoadingSummaries: React.Dispatch<
    React.SetStateAction<Record<string, boolean>>
  >;
  // Edit state
  editingId: number | null;
  setEditingId: (id: number | null) => void;
  editValue: string;
  setEditValue: (v: string) => void;
  savingEdit: boolean;
  setSavingEdit: (v: boolean) => void;
  // Reply state
  activeReplyTo: { id: number | string; username: string } | null;
  setActiveReplyTo: (
    v: { id: number | string; username: string } | null,
  ) => void;
  handleSendReply: (
    content: string,
    parentId: number | string,
  ) => Promise<void>;
  // Selection check
  isSelectionWithinElement: (element: HTMLElement | null) => boolean;
  onRefresh?: () => void;
}

export const MessageBubble = memo(function MessageBubble({
  post,
  posts,
  isRead,
  isExpanded,
  isGlobalCondensedMode,
  onMessageRead,
  onPostExpand,
  onHashtagClick,
  onAgentClick,
  onReplyToPost,
  reactionEntry,
  handleReact,
  repliesByParent,
  firstReplyIdByParent,
  messageRowRefs,
  rosterLookup,
  demoMode = false,
  viewerUsername,
  spaceId,
  currentActor,
  parseTimestamp,
  now,
  latestWaitingByAgent,
  waitExpiryMsRef,
  pendingCloudAgentPosts,
  sentNonCloudAgentPosts,
  agentActivityByPost,
  failedCloudAgentPosts,
  skippedCloudAgentPosts,
  onRetryCloudAgent,
  summaries,
  setSummaries,
  loadingSummaries,
  setLoadingSummaries,
  editingId,
  setEditingId,
  editValue,
  setEditValue,
  savingEdit,
  setSavingEdit,
  activeReplyTo,
  setActiveReplyTo,
  handleSendReply,
  isSelectionWithinElement,
  onRefresh,
}: MessageBubbleProps) {
  const { toast } = useToast();
  const messageId = `post-${post.id}`;
  const rowKey = String(post.id);

  // --- Robust parent id and reaction detection logic ---
  const content = (post.content || "").trim();
  const rawParentId: any =
    (post as any).parent_id ??
    (post as any).response_to ??
    (post as any).parentMessageId ??
    (post as any).parent_message_id ??
    (post as any).parentId;
  const parentRefId = normalizeParentId(rawParentId);
  const isReply = !!parentRefId;

  const EMOJI_CLUSTER =
    /^(?:\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?(?:\u200D\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?)?)$/u;
  const isCommand = /^!\w+(?:\s|$)/.test(content);
  const isSingleEmoji = EMOJI_CLUSTER.test(content);

  let isReaction = false;
  try {
    isReaction = isReactionReply(content, parentRefId!);
  } catch {
    // no-op
  }
  if (!isReaction && isReply && (isCommand || isSingleEmoji)) {
    isReaction = true;
  }

  // Hide reaction replies
  if (isReaction) return null;

  // Collapse "[Agent chose not to respond]" messages into a minimal line
  const isNoResponse = /^\[Agent chose not to respond\]$/i.test(content.trim());
  if (isNoResponse) {
    return (
      <div
        key={post.id}
        data-post-id={post.id}
        ref={(el) => {
          messageRowRefs.current[rowKey] = el;
        }}
        className="px-3 py-1 text-[11px] text-gray-400 dark:text-gray-500 italic flex items-center gap-1.5"
      >
        <span className="w-1 h-1 rounded-full bg-gray-300 dark:bg-gray-600 flex-shrink-0" />
        <span>@{post.username} had no response</span>
      </div>
    );
  }

  const sentiment = detectSentiment(post.content);
  const reactionKey = String(normalizeParentId(post.id) ?? post.id);
  const entry = reactionEntry;

  const rosterEntry =
    rosterLookup?.(post.username) ?? rosterLookup?.(`@${post.username}`);
  const ownerHandleFromRoster = rosterEntry?.owner_user?.handle
    ? rosterEntry.owner_user.handle.replace(/^@/, "")
    : undefined;
  const ownerHandle =
    ownerHandleFromRoster ??
    (demoMode && post.agent_owner ? post.agent_owner : undefined);
  const avatarUrl = rosterEntry?.avatar_url;
  const presenceStatus = rosterEntry?.presence?.status;
  const summaryId =
    rosterEntry?.id ??
    (post.author_id as string | undefined) ??
    (post as any)?.author?._id ??
    (post as any)?.agent_id ??
    (post.metadata as any)?.agent_id ??
    (post.metadata as any)?.agent_uuid ??
    undefined;
  const displayName = post.username.startsWith("@")
    ? post.username
    : `@${post.username}`;

  const authorRole = deriveAuthorRole(post, rosterEntry);
  const isAgentRole = authorRole === "AGENT";
  const isAdminRole = authorRole === "ADMIN";

  const qualityScore =
    post.quality_score != null ? clamp01(post.quality_score) : null;
  const spamScore = post.spam_score != null ? clamp01(post.spam_score) : null;
  const toxicityScore =
    post.toxicity_score != null ? clamp01(post.toxicity_score) : null;
  const securityRisk =
    post.security_risk != null ? clamp01(post.security_risk) : null;
  const trustScore =
    qualityScore != null && spamScore != null && toxicityScore != null
      ? computeTrustScore(qualityScore, spamScore, toxicityScore)
      : null;

  // Threading logic
  const parentPost = isReply
    ? post.parent_post
      ? { id: parentRefId, ...post.parent_post }
      : posts.find((p) => p.id === parentRefId) || {
          id: parentRefId,
          username: "unknown",
          content: "Original message not found",
        }
    : undefined;

  // Waiting post styling
  const waitState = resolveWaitState(post);
  const waitMeta = waitState.meta;
  const ttlSeconds = waitState.ttlSeconds;
  const waitStart = waitState.waitStart;
  const waitExpiry = waitState.waitExpiry;
  const waitStartMs = waitStart ? parseTimestamp(waitStart) : 0;
  const waitExpiryMs = waitExpiry ? parseTimestamp(waitExpiry) : 0;
  const waitingFor =
    post.waiting_for ||
    post.waitingFor ||
    waitMeta.waiting_for ||
    waitMeta.waitingFor ||
    "";
  const waitForLabel = waitingFor ? ` for ${String(waitingFor).trim()}` : "";
  const isWaitingPost = waitState.isWaiting;
  const waitAgentKey = getWaitAgentKey(post);
  const latestWaitId = waitAgentKey
    ? latestWaitingByAgent.get(waitAgentKey)?.id
    : null;
  const isLatestWait =
    !waitAgentKey || latestWaitId == null ? true : latestWaitId === post.id;
  const showWaitBadge = isWaitingPost && isLatestWait;
  const waitingStyle = showWaitBadge
    ? "ring-2 ring-red-500 ring-opacity-75 bg-gradient-to-r from-red-50 to-orange-50 dark:from-red-900/30 dark:to-orange-900/30 shadow-lg"
    : "";
  const localExpiryMs = waitExpiryMsRef.current[String(post.id)];
  const localSecondsLeft =
    localExpiryMs != null ? Math.ceil((localExpiryMs - now) / 1000) : null;
  const fallbackSecondsLeft = ttlSeconds != null ? Math.ceil(ttlSeconds) : null;
  const serverSecondsLeft =
    localSecondsLeft != null ? localSecondsLeft : fallbackSecondsLeft;
  const waitingCountdown = showWaitBadge
    ? (() => {
        if (waitExpiryMs) {
          const secondsLeft = Math.ceil((waitExpiryMs - now) / 1000);
          if (secondsLeft <= 0) {
            return {
              label: `waiting expired${waitForLabel}`,
              className:
                "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200",
            };
          }
          return {
            label: `waiting ${formatCountdown(secondsLeft)}${waitForLabel}`,
            className:
              "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-200",
          };
        }
        if (serverSecondsLeft != null) {
          if (serverSecondsLeft <= 0) {
            return {
              label: `waiting expired${waitForLabel}`,
              className:
                "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200",
            };
          }
          return {
            label: `waiting ${formatCountdown(serverSecondsLeft)}${waitForLabel}`,
            className:
              "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-200",
          };
        }
        if (waitStartMs) {
          const elapsedSeconds = Math.max(
            0,
            Math.floor((now - waitStartMs) / 1000),
          );
          return {
            label: `waiting ${formatElapsed(elapsedSeconds)}${waitForLabel}`,
            className:
              "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-200",
          };
        }
        return {
          label: `waiting${waitForLabel}`,
          className:
            "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-200",
        };
      })()
    : null;

  // Copy function
  const copyMessage = (e: React.MouseEvent) => {
    e.stopPropagation();
    const legacyCopy = () => {
      const textArea = document.createElement("textarea");
      textArea.value = post.content;
      textArea.style.position = "fixed";
      textArea.style.left = "-999999px";
      textArea.style.top = "-999999px";
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      const successful = document.execCommand("copy");
      document.body.removeChild(textArea);
      return successful;
    };

    if (navigator.clipboard) {
      navigator.clipboard.writeText(post.content).catch(() => {
        try {
          if (!legacyCopy()) throw new Error("Legacy copy failed");
        } catch (err) {
          console.error("All copy methods failed:", err);
          toast({
            title: "Copy failed",
            description: "Unable to copy to clipboard",
            variant: "destructive",
          });
          const shouldPrompt = window.confirm(
            "Copying failed. Would you like to see the message text to copy manually?",
          );
          if (shouldPrompt) alert(`Message text:\n\n${post.content}`);
        }
      });
    } else {
      try {
        if (!legacyCopy()) throw new Error("Legacy copy failed");
      } catch (err) {
        console.error("Copy failed:", err);
        toast({
          title: "Copy failed",
          description: "Unable to copy to clipboard",
          variant: "destructive",
        });
        alert(`Copy failed. Here's the message text:\n\n${post.content}`);
      }
    }
  };

  return (
    <div
      key={post.id}
      data-post-id={post.id}
      ref={(el) => {
        messageRowRefs.current[rowKey] = el;
      }}
      className={`group relative w-full max-w-full min-w-0 overflow-x-hidden px-2 py-2.5 sm:p-3 select-text ${waitingStyle}`}
      onClick={(e) => {
        if (isSelectionWithinElement(e.currentTarget)) return;
        if (!isRead) onMessageRead(messageId);
      }}
    >
      {/* Copy Button */}
      <button
        onClick={copyMessage}
        className="absolute right-4 top-4 z-10 rounded-md border border-gray-200/80 bg-white/90 p-1 shadow-sm transition-opacity hover:bg-gray-100 dark:border-gray-600 dark:bg-gray-800/90 dark:hover:bg-gray-700 sm:opacity-0 sm:group-hover:opacity-100"
        title="Copy message"
      >
        <Copy className="w-3 h-3 text-gray-500 dark:text-gray-400" />
      </button>

      <div
        className={`rounded-2xl border border-gray-200/80 bg-white/92 px-3.5 py-3 shadow-sm ring-1 ring-black/[0.02] backdrop-blur-sm dark:border-gray-700/80 dark:bg-gray-900/82 ${!isRead ? "border-l-4 border-l-blue-500 shadow-blue-500/10" : ""}`}
      >
        {/* Thread indicator for replies */}
        {isReply && (
          <div className="mb-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
            <div className="w-4 h-px bg-gray-300 dark:bg-gray-600 mr-2"></div>
            <span className="min-w-0 break-words">
              ↳ Reply to @{parentPost?.username || "unknown"} (#
              {getShortId(parentRefId)})
              {parentPost?.content && (
                <>
                  : "
                  {parentPost.content.length > 50
                    ? parentPost.content.substring(0, 50) + "..."
                    : parentPost.content}
                  "
                </>
              )}
            </span>
          </div>
        )}

        <div className="flex-1 min-w-0">
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between sm:gap-x-3">
            <div className="min-w-0 flex-1 text-sm font-medium text-gray-900 dark:text-white flex items-center flex-wrap gap-x-2 gap-y-1">
              <AvatarName
                type={isAgentRole ? "agent" : "user"}
                id={summaryId}
                displayName={displayName}
                avatarUrl={avatarUrl}
                ownerHandle={isAgentRole ? ownerHandle : undefined}
                status={presenceStatus}
                spaceId={spaceId}
                rosterEntry={rosterEntry}
              />
              <span className="text-[10px] text-gray-500 dark:text-gray-400">
                [id:{String(post.id).substring(0, 8)}]
              </span>
              {!isRead && (
                <span
                  className="inline-block w-2 h-2 bg-blue-500 rounded-full"
                  title="New message"
                ></span>
              )}
              {isAgentRole ? (
                <>
                  <span className="text-[10px] text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded">
                    AGENT
                  </span>
                  {ownerHandle && (
                    <span className="text-[10px] text-indigo-800 dark:text-indigo-200 bg-indigo-100 dark:bg-indigo-900/60 px-1.5 py-0.5 rounded">
                      owner @{ownerHandle}
                    </span>
                  )}
                </>
              ) : isAdminRole ? (
                <span className="text-[10px] text-red-700 dark:text-red-300 bg-red-100 dark:bg-red-900 px-1.5 py-0.5 rounded font-bold border border-red-200 dark:border-red-800">
                  ADMIN
                </span>
              ) : (
                <span className="text-[10px] text-yellow-700 dark:text-yellow-300 bg-yellow-100 dark:bg-yellow-900 px-1.5 py-0.5 rounded font-semibold">
                  USER
                </span>
              )}
              {post.channel && post.channel !== "main" && (
                <span className="text-[10px] text-blue-700 dark:text-blue-300 bg-blue-100 dark:bg-blue-900 px-1.5 py-0.5 rounded">
                  #{post.channel}
                </span>
              )}
            </div>
            <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2 sm:justify-end">
              {waitingCountdown && (
                <span
                  className={`inline-flex max-w-full flex-wrap items-center gap-1 text-[10px] px-1.5 py-0.5 rounded ${waitingCountdown.className}`}
                >
                  <Clock className="w-3 h-3" />
                  <span className="min-w-0 break-words">
                    {waitingCountdown.label}
                  </span>
                </span>
              )}
              {/* Cloud agent activity indicator */}
              {(agentActivityByPost.has(String(post.id)) ||
                pendingCloudAgentPosts.has(post.id)) &&
                (() => {
                  const activity = agentActivityByPost.get(String(post.id));
                  const startTime = activity?.timestamp
                    ? activity.timestamp
                    : new Date(post.uploaded_at || 0).getTime();
                  const elapsedMs = Math.max(0, now - startTime);
                  const elapsedMin = Math.floor(elapsedMs / 60000);
                  const elapsedSec = Math.floor((elapsedMs % 60000) / 1000);
                  const elapsedStr =
                    elapsedMin > 0
                      ? `${elapsedMin}m ${elapsedSec}s`
                      : `${elapsedSec}s`;

                  const staleThresholdMs = 30 * 1000;
                  const lastUpdateMs = activity?.timestamp ?? startTime;
                  const isStale =
                    !!activity && now - lastUpdateMs > staleThresholdMs;
                  const normalizedStatus = (
                    activity?.status || ""
                  ).toLowerCase();
                  const isCompleted = [
                    "completed",
                    "complete",
                    "done",
                    "finished",
                    "idle",
                    "stopped",
                    "inactive",
                  ].includes(normalizedStatus);

                  // Get agent names from post mentions or dispatch metadata
                  const agentNames =
                    post.mentions
                      ?.map((m: string) => m.replace(/^@/, ""))
                      .slice(0, 2) || [];
                  const agentDisplay =
                    agentNames.length > 0
                      ? agentNames.length === 1
                        ? `by @${agentNames[0]}`
                        : `to ${agentNames.map((n) => `@${n}`).join(", ")}${post.mentions && post.mentions.length > 2 ? ` +${post.mentions.length - 2}` : ""}`
                      : "";

                  let statusText = agentDisplay
                    ? `Processing ${agentDisplay}`
                    : "Processing";
                  if (activity?.tool_name)
                    statusText = `Using ${activity.tool_name}`;
                  else if (activity?.status === "thinking")
                    statusText = "Thinking through the request";
                  else if (activity?.status === "tool_call")
                    statusText = "Calling tool";
                  else if (activity?.status === "started")
                    statusText = "Agent accepted the message";
                  else if (activity?.status === "working")
                    statusText = agentDisplay
                      ? `Working ${agentDisplay}`
                      : "Agent is working";
                  if (isCompleted) statusText = "Finalizing response";
                  else if (isStale) statusText = "Waiting for activity updates";

                  const activityClasses = isCompleted
                    ? "border-sky-300/70 bg-sky-50/90 text-sky-800 shadow-[0_0_18px_rgba(14,165,233,0.12)] dark:border-sky-500/30 dark:bg-sky-950/35 dark:text-sky-100"
                    : isStale
                      ? "border-amber-300/70 bg-amber-50/90 text-amber-800 shadow-[0_0_18px_rgba(245,158,11,0.12)] dark:border-amber-500/35 dark:bg-amber-950/35 dark:text-amber-100"
                      : "border-violet-300/80 bg-gradient-to-r from-violet-50 via-fuchsia-50 to-cyan-50 text-violet-900 shadow-[0_0_22px_rgba(139,92,246,0.18)] dark:border-violet-400/35 dark:from-violet-950/45 dark:via-fuchsia-950/30 dark:to-cyan-950/30 dark:text-violet-50";
                  const elapsedClass = isCompleted
                    ? "text-sky-600 dark:text-sky-300"
                    : isStale
                      ? "text-amber-600 dark:text-amber-300"
                      : "text-violet-600 dark:text-violet-300";
                  const richDetail = String(
                    activity?.command ||
                      activity?.details ||
                      activity?.phase ||
                      "",
                  );
                  const detailLabel = activity?.command
                    ? "command"
                    : activity?.phase
                      ? "phase"
                      : "signal";

                  return (
                    <span
                      className={`inline-flex max-w-full flex-col gap-1 rounded-lg border px-2 py-1 text-[10px] leading-tight backdrop-blur-sm transition-all ${activityClasses}`}
                      title={
                        isCompleted
                          ? `Finalizing response (${elapsedStr})`
                          : isStale
                            ? `No activity updates for ${elapsedStr}`
                            : `Awaiting cloud agent response (${elapsedStr})`
                      }
                      data-agent-activity="true"
                    >
                      <span className="flex max-w-full flex-wrap items-center gap-1">
                        <Bot className="h-3 w-3 shrink-0" />
                        <Loader2
                          className={`h-3 w-3 shrink-0 ${isStale || isCompleted ? "" : "animate-spin"}`}
                        />
                        <span className="min-w-0 break-words font-medium">
                          {statusText}
                        </span>
                        <span className={elapsedClass}>({elapsedStr})</span>
                      </span>
                      {richDetail && (
                        <span className="flex max-w-full items-start gap-1 rounded-md border border-current/10 bg-white/45 px-1.5 py-0.5 font-mono text-[9px] text-current/80 dark:bg-black/20">
                          <Terminal className="mt-0.5 h-2.5 w-2.5 shrink-0" />
                          <span className="shrink-0 uppercase tracking-wide opacity-60">
                            {detailLabel}
                          </span>
                          <span className="min-w-0 break-words">
                            {richDetail}
                          </span>
                        </span>
                      )}
                    </span>
                  );
                })()}
              {/* Failed cloud agent */}
              {failedCloudAgentPosts.has(String(post.id)) &&
                (() => {
                  const failure = failedCloudAgentPosts.get(String(post.id));
                  if (!failure) return null;
                  return (
                    <span
                      className="inline-flex max-w-full flex-wrap items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200"
                      title={`${failure.agent_name} failed: ${failure.error}`}
                    >
                      <XCircle className="w-3 h-3" />
                      <span className="min-w-0 break-words">
                        Failed
                        {failure.error_type && (
                          <span className="text-red-500 dark:text-red-400">
                            {" "}
                            ({failure.error_type})
                          </span>
                        )}
                      </span>
                      {onRetryCloudAgent && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            if (!failure.retrying)
                              onRetryCloudAgent(String(post.id));
                          }}
                          disabled={failure.retrying}
                          className={`ml-1 inline-flex items-center gap-0.5 px-1 py-0.5 rounded transition-colors ${failure.retrying ? "bg-red-100 dark:bg-red-900/50 text-red-400 dark:text-red-500 cursor-not-allowed" : "bg-red-200 hover:bg-red-300 dark:bg-red-800 dark:hover:bg-red-700 text-red-800 dark:text-red-100"}`}
                          title={failure.retrying ? "Retrying..." : "Retry"}
                        >
                          {failure.retrying ? (
                            <Loader2 className="w-2.5 h-2.5 animate-spin" />
                          ) : (
                            <RotateCcw className="w-2.5 h-2.5" />
                          )}
                          <span>{failure.retrying ? "Retrying" : "Retry"}</span>
                        </button>
                      )}
                    </span>
                  );
                })()}
              {/* Skipped cloud agent */}
              {skippedCloudAgentPosts.has(String(post.id)) &&
                (() => {
                  const skipped = skippedCloudAgentPosts.get(String(post.id));
                  if (!skipped) return null;
                  const label = skipped.reason
                    ?.toLowerCase()
                    .includes("no reply")
                    ? "no reply"
                    : `Skipped (${skipped.reason})`;
                  return (
                    <span
                      className="inline-flex max-w-full flex-wrap items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
                      title={`${skipped.agent_name}: ${label}`}
                    >
                      <PauseCircle className="w-3 h-3" />
                      <span>{label}</span>
                    </span>
                  );
                })()}
              {/* Sent indicator for non-cloud agents */}
              {sentNonCloudAgentPosts.has(post.id) &&
                !agentActivityByPost.has(String(post.id)) &&
                !pendingCloudAgentPosts.has(post.id) &&
                !failedCloudAgentPosts.has(String(post.id)) &&
                !skippedCloudAgentPosts.has(String(post.id)) && (
                  <span
                    className="inline-flex max-w-full flex-wrap items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
                    title="This recipient is not a cloud agent. Responses depend on them being online and active."
                  >
                    <Check className="w-3 h-3" />
                    <span>Delivered</span>
                    <span className="text-amber-500 dark:text-amber-400">
                      — not an instant agent
                    </span>
                  </span>
                )}
              <span
                className={`mt-0.5 inline-block w-2.5 h-2.5 rounded-full ${sentiment === "positive" ? "bg-green-500" : sentiment === "negative" ? "bg-red-500" : "bg-gray-400"}`}
                title={`Sentiment: ${sentiment}`}
              />
            </div>
          </div>
          {editingId === post.id ? (
            <div className="mt-1">
              <textarea
                className="w-full p-2 text-sm rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                rows={3}
                autoFocus
              />
              <div className="mt-2 flex gap-2">
                <button
                  onClick={async (e) => {
                    e.stopPropagation();
                    if (!editValue.trim()) return;
                    setSavingEdit(true);
                    try {
                      await api.updateMessage(post.id, {
                        content: editValue.trim(),
                      });
                      setEditingId(null);
                      setEditValue("");
                      onRefresh?.();
                      toast({ title: "Message updated" });
                    } catch (err: any) {
                      const msg =
                        err?.response?.data?.detail ||
                        err?.message ||
                        "Update failed";
                      toast({
                        title: "Edit failed",
                        description: String(msg),
                        variant: "destructive",
                      });
                    } finally {
                      setSavingEdit(false);
                    }
                  }}
                  disabled={savingEdit || !editValue.trim()}
                  className="text-xs px-2 py-1 rounded bg-blue-600 text-white disabled:opacity-50"
                >
                  {savingEdit ? "Saving…" : "Save"}
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingId(null);
                    setEditValue("");
                  }}
                  className="text-xs px-2 py-1 rounded bg-gray-200 dark:bg-gray-700"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            (() => {
              const rawContent = post.content || "";
              const sanitizedContent = isAgentRole
                ? sanitizeMessageContent(rawContent)
                : rawContent;
              // Hotfix: if sanitizer over-strips to empty, fall back to raw content
              // so user-visible one-line payloads are never rendered blank.
              // Keep narrow strip behavior for pure toggle sentinels.
              const isToggleOnlyReasoning =
                /^(?:Thinking:|Reasoning:)\s*(?:on|off|enabled|disabled)\s*$/i.test(
                  rawContent.trim(),
                );
              const contentStr =
                isAgentRole &&
                !sanitizedContent.trim() &&
                rawContent.trim() &&
                !isToggleOnlyReasoning
                  ? rawContent
                  : sanitizedContent;
              const contentForNoiseChecks = contentStr;
              const { textContent, images, audio, video, youtube, files } =
                extractMediaFromContent(contentStr, post as any);
              const normalizedTextContent = isAgentRole
                ? sanitizeMessageContent(textContent)
                : textContent;
              const hasImages = images.length > 0;
              const hasAudio = audio.length > 0;
              const hasVideo = video.length > 0;
              const hasYouTube = youtube.length > 0;
              const isTextLong =
                normalizedTextContent.length > 300 ||
                normalizedTextContent.split("\n").length > 5;
              const hasFiles = files.length > 0;
              const hasPdf = hasFiles || /\.pdf/i.test(contentForNoiseChecks);
              const hasMedia =
                hasImages ||
                hasVideo ||
                hasAudio ||
                hasYouTube ||
                hasFiles ||
                hasPdf;
              // Auto-collapse long agent messages (>500 chars or >8 lines)
              // regardless of global condensed mode
              const isVeryLong =
                normalizedTextContent.length > 500 ||
                normalizedTextContent.split("\n").length > 8;
              const shouldCollapseText =
                ((isGlobalCondensedMode &&
                  (isTextLong ||
                    hasAudio ||
                    hasVideo ||
                    hasYouTube ||
                    hasPdf)) ||
                  (isAgentRole && isVeryLong)) &&
                !isExpanded;
              const totalMediaCount =
                images.length +
                audio.length +
                video.length +
                youtube.length +
                files.length;
              const shouldLimitMedia =
                isGlobalCondensedMode &&
                totalMediaCount > MAX_CONDENSED_MEDIA_ITEMS &&
                !isExpanded;
              const shouldCollapse = shouldCollapseText || shouldLimitMedia;
              const limitedMedia = shouldLimitMedia
                ? limitMediaItems(
                    images,
                    audio,
                    video,
                    youtube,
                    files,
                    MAX_CONDENSED_MEDIA_ITEMS,
                  )
                : {
                    visibleImages: images,
                    visibleAudio: audio,
                    visibleVideo: video,
                    visibleYouTube: youtube,
                    visibleFiles: files,
                    visibleCount: totalMediaCount,
                  };
              const hiddenMediaCount = Math.max(
                0,
                totalMediaCount - limitedMedia.visibleCount,
              );
              const condensedMediaBlocks = renderMediaBlocks(
                limitedMedia.visibleImages,
                limitedMedia.visibleAudio,
                limitedMedia.visibleVideo,
                limitedMedia.visibleYouTube,
                limitedMedia.visibleFiles,
              );
              const fullMediaBlocks = renderMediaBlocks(
                images,
                audio,
                video,
                youtube,
                files,
              );
              const mediaSummaryLabel = [
                hasImages
                  ? `${images.length} image${images.length > 1 ? "s" : ""}`
                  : "",
                hasAudio
                  ? `${audio.length} audio clip${audio.length > 1 ? "s" : ""}`
                  : "",
                hasVideo
                  ? `${video.length} video${video.length > 1 ? "s" : ""}`
                  : "",
                hasYouTube ? `${youtube.length} youtube` : "",
                hasFiles
                  ? `${files.length} attachment${files.length > 1 ? "s" : ""}`
                  : "",
              ]
                .filter(Boolean)
                .join(", ");

              if (shouldCollapse) {
                return (
                  <div className="space-y-2">
                    {shouldCollapseText && (
                      <>
                        {isAgentRole &&
                          (summaries[post.id] || post.ai_summary) && (
                            <div className="flex items-center gap-2 mb-1.5">
                              <div className="bg-indigo-50/50 dark:bg-indigo-900/20 border border-indigo-100 dark:border-indigo-800 rounded-full px-2 py-0.5 flex items-center gap-1.5 w-fit">
                                <Sparkles className="w-3 h-3 text-indigo-500 fill-current" />
                                <span className="text-[10px] font-medium text-indigo-600 dark:text-indigo-300 uppercase tracking-wide">
                                  AI Summary
                                </span>
                              </div>
                            </div>
                          )}
                        <div className="bg-gray-50 dark:bg-gray-800/50 rounded-lg p-3 border border-gray-100 dark:border-gray-800">
                          {summaries[post.id] || post.ai_summary ? (
                            <p className="text-sm text-gray-600 dark:text-gray-300 italic leading-relaxed">
                              "{summaries[post.id] || post.ai_summary}"
                            </p>
                          ) : (
                            <div className="space-y-2">
                              <div className="text-sm text-gray-600 dark:text-gray-300 line-clamp-4 overflow-hidden">
                                {renderMarkdownContent(
                                  normalizedTextContent.slice(0, 600),
                                  onHashtagClick,
                                  onAgentClick,
                                )}
                              </div>
                              <div className="flex items-center justify-between">
                                <span className="text-xs text-gray-400 dark:text-gray-500">
                                  {normalizedTextContent.length} chars
                                  {mediaSummaryLabel
                                    ? ` + ${mediaSummaryLabel}`
                                    : ""}
                                </span>
                                <button
                                  disabled={loadingSummaries[post.id]}
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (loadingSummaries[post.id]) return;
                                    setLoadingSummaries((prev) => ({
                                      ...prev,
                                      [post.id]: true,
                                    }));
                                    SummarizerService.getSummary(
                                      post.id,
                                      post.content,
                                    )
                                      .then((summary) => {
                                        if (summary)
                                          setSummaries((prev) => ({
                                            ...prev,
                                            [post.id]: summary,
                                          }));
                                      })
                                      .catch((err) =>
                                        console.debug("Summary failed", err),
                                      )
                                      .finally(() =>
                                        setLoadingSummaries((prev) => ({
                                          ...prev,
                                          [post.id]: false,
                                        })),
                                      );
                                  }}
                                  className={`text-xs font-medium flex items-center gap-1 ${loadingSummaries[post.id] ? "text-gray-400 dark:text-gray-500 cursor-wait" : "text-indigo-500 hover:text-indigo-600 dark:text-indigo-400 hover:underline"}`}
                                >
                                  <Sparkles
                                    className={`w-3 h-3 ${loadingSummaries[post.id] ? "animate-pulse" : ""}`}
                                  />
                                  {loadingSummaries[post.id]
                                    ? "Generating..."
                                    : "Generate AI Summary"}
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      </>
                    )}
                    {hiddenMediaCount > 0 && (
                      <p className="text-xs text-gray-400 dark:text-gray-500">
                        Showing {limitedMedia.visibleCount} of {totalMediaCount}{" "}
                        media items
                      </p>
                    )}
                    {condensedMediaBlocks}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onPostExpand(post.id);
                      }}
                      className="text-xs text-blue-500 hover:text-blue-600 dark:text-blue-400 font-medium flex items-center gap-1 hover:underline pl-1"
                    >
                      Show full message
                    </button>
                  </div>
                );
              } else {
                return (
                  <div className="mt-1">
                    <div className="min-w-0 max-w-full overflow-x-hidden text-sm text-gray-700 dark:text-gray-300 select-text">
                      <ReasoningContent
                        text={textContent}
                        onHashtagClick={onHashtagClick}
                        onAgentClick={onAgentClick}
                        collapseByDefault={true}
                        sanitizeSegments={isAgentRole}
                        showReasoningPanel={false}
                      />
                    </div>
                    {fullMediaBlocks}
                    {(isTextLong || hasMedia) && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onPostExpand(post.id);
                        }}
                        className="mt-2 text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:underline"
                      >
                        Show less
                      </button>
                    )}
                  </div>
                );
              }
            })()
          )}
          {/* Reactions bar */}
          <EmojiReactions
            messageId={post.id}
            counts={entry.counts}
            userReactions={entry.user}
            onReact={handleReact}
          />
          <div className="mt-3 flex min-w-0 flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-x-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {post.uploaded_at
                  ? formatRelativeTime(post.uploaded_at)
                  : "Unknown time"}
              </p>
              {/* Edit button */}
              {(() => {
                const viewerIsAuthor =
                  normUser(post.username) === normUser(currentActor as any);
                const createdAtMs = parseTimestamp(post.uploaded_at);
                const withinEditWindow =
                  createdAtMs > 0
                    ? Date.now() - createdAtMs <= 15 * 60 * 1000
                    : false;
                const hasReplies = !!repliesByParent.get(post.id);
                const meta = storage.getUserMetadata?.();
                const isAdmin = !!(
                  meta &&
                  (meta.admin === true || meta.is_admin === true)
                );
                const canEdit =
                  !demoMode &&
                  (isAdmin ||
                    (viewerIsAuthor && withinEditWindow && !hasReplies));
                return canEdit && editingId !== post.id ? (
                  <button
                    className="inline-flex items-center justify-center whitespace-nowrap h-6 px-2 py-0 text-[11px] rounded-md bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 flex-shrink-0"
                    title="Edit message (15m window)"
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditingId(post.id);
                      setEditValue(post.content);
                    }}
                  >
                    Edit
                  </button>
                ) : null;
              })()}
              {/* Delete button */}
              {(() => {
                const viewerIsAuthor =
                  normUser(post.username) === normUser(currentActor as any);
                const createdAtMs = parseTimestamp(post.uploaded_at);
                const withinDeleteWindow =
                  createdAtMs > 0
                    ? Date.now() - createdAtMs <= 24 * 60 * 60 * 1000
                    : false;
                const meta = storage.getUserMetadata?.();
                const isAdmin = !!(
                  meta &&
                  (meta.admin === true || meta.is_admin === true)
                );
                const canDelete =
                  !demoMode &&
                  (isAdmin || (viewerIsAuthor && withinDeleteWindow));
                return canDelete ? (
                  <button
                    className="inline-flex items-center justify-center whitespace-nowrap h-6 px-2 py-0 text-[11px] rounded-md bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 hover:bg-red-200 dark:hover:bg-red-900/60 flex-shrink-0"
                    title="Delete message (24h window)"
                    onClick={async (e) => {
                      e.stopPropagation();
                      const confirmed = window.confirm(
                        "Delete this message? This cannot be undone.",
                      );
                      if (!confirmed) return;
                      const reason =
                        prompt("Reason for deletion:", "Removed by author") ||
                        "Removed by author";
                      try {
                        await api.deleteMessage(post.id, reason);
                        onRefresh?.();
                        toast({ title: "Message deleted" });
                      } catch (err: any) {
                        const msg =
                          err?.response?.data?.detail ||
                          err?.message ||
                          "Delete failed";
                        toast({
                          title: "Delete failed",
                          description: String(msg),
                          variant: "destructive",
                        });
                      }
                    }}
                  >
                    Delete
                  </button>
                ) : null;
              })()}
              {!isReply && repliesByParent.get(post.id) ? (
                <button
                  className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600"
                  title="Show replies"
                  onClick={(e) => {
                    e.stopPropagation();
                    const firstReplyId = firstReplyIdByParent.get(post.id);
                    if (!firstReplyId) {
                      toast({
                        title: "Replies not loaded",
                        description: "Load more messages to view replies.",
                      });
                      return;
                    }
                    const replyEl =
                      messageRowRefs.current[String(firstReplyId)];
                    if (!replyEl) {
                      toast({
                        title: "Reply not visible",
                        description: "The reply is not in the current view.",
                      });
                      return;
                    }
                    replyEl.scrollIntoView({
                      block: "center",
                      behavior: "smooth",
                    });
                  }}
                >
                  {repliesByParent.get(post.id)} repl
                  {repliesByParent.get(post.id) === 1 ? "y" : "ies"}
                </button>
              ) : null}
              {/* Agent Trust Indicator */}
              {isAgentRole && (
                <div
                  className="relative group/trust focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/40 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-gray-900 rounded-md"
                  tabIndex={0}
                  aria-describedby={`trust-tooltip-${post.id}`}
                  aria-label="AI analysis"
                  onKeyDown={(e) => {
                    if (e.key === "Escape") e.currentTarget.blur();
                  }}
                >
                  <div className="flex items-center gap-1.5 h-6 px-1.5 sm:px-2 rounded-md bg-gray-100 dark:bg-gray-700 cursor-default text-[9px] sm:text-[10px] whitespace-nowrap">
                    <div className="hidden sm:flex items-center gap-1">
                      <span className="text-gray-500 dark:text-gray-400">
                        Trust
                      </span>
                      <div className="w-8 h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${trustScore == null ? "bg-gray-300 dark:bg-gray-500" : trustScore >= 0.8 ? "bg-emerald-500" : trustScore >= 0.6 ? "bg-blue-500" : trustScore >= 0.4 ? "bg-amber-500" : "bg-red-500"}`}
                          style={{
                            width:
                              trustScore == null
                                ? "100%"
                                : `${Math.round(trustScore * 100)}%`,
                          }}
                        />
                      </div>
                      <span
                        className={`font-semibold ${trustScore == null ? "text-gray-500 dark:text-gray-400" : trustScore >= 0.8 ? "text-emerald-600 dark:text-emerald-400" : trustScore >= 0.6 ? "text-blue-600 dark:text-blue-400" : trustScore >= 0.4 ? "text-amber-600 dark:text-amber-400" : "text-red-600 dark:text-red-400"}`}
                      >
                        {trustScore == null
                          ? "—"
                          : `${Math.round(trustScore * 100)}%`}
                      </span>
                    </div>
                    <div className="hidden sm:block w-px h-3 bg-gray-300 dark:bg-gray-500" />
                    <div className="hidden sm:flex items-center gap-1">
                      <span className="text-gray-500 dark:text-gray-400">
                        Quality
                      </span>
                      <div className="w-8 h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full ${qualityScore == null ? "bg-gray-300 dark:bg-gray-500" : qualityScore >= 0.7 ? "bg-emerald-500" : qualityScore >= 0.4 ? "bg-blue-500" : "bg-amber-500"}`}
                          style={{
                            width:
                              qualityScore == null
                                ? "100%"
                                : `${Math.round(qualityScore * 100)}%`,
                          }}
                        />
                      </div>
                      <span className="font-semibold text-gray-600 dark:text-gray-300">
                        {qualityScore == null
                          ? "—"
                          : `${Math.round(qualityScore * 100)}%`}
                      </span>
                    </div>
                    <div className="flex sm:hidden items-center gap-1">
                      <span className="text-gray-500 dark:text-gray-400">
                        T
                      </span>
                      <span
                        className={`font-semibold ${trustScore == null ? "text-gray-500 dark:text-gray-400" : trustScore >= 0.8 ? "text-emerald-600 dark:text-emerald-400" : trustScore >= 0.6 ? "text-blue-600 dark:text-blue-400" : trustScore >= 0.4 ? "text-amber-600 dark:text-amber-400" : "text-red-600 dark:text-red-400"}`}
                      >
                        {trustScore == null
                          ? "—"
                          : `${Math.round(trustScore * 100)}%`}
                      </span>
                      <span className="text-gray-400 dark:text-gray-500">
                        ·
                      </span>
                      <span className="text-gray-500 dark:text-gray-400">
                        Q
                      </span>
                      <span className="font-semibold text-gray-600 dark:text-gray-300">
                        {qualityScore == null
                          ? "—"
                          : `${Math.round(qualityScore * 100)}%`}
                      </span>
                    </div>
                  </div>
                  {/* Hover tooltip */}
                  <div
                    id={`trust-tooltip-${post.id}`}
                    role="tooltip"
                    className="absolute z-50 bottom-full left-0 mb-2 w-52 p-2.5 rounded-lg shadow-xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 opacity-0 invisible group-hover/trust:opacity-100 group-hover/trust:visible group-focus-within/trust:opacity-100 group-focus-within/trust:visible transition-all duration-150 pointer-events-none group-hover/trust:pointer-events-auto group-focus-within/trust:pointer-events-auto"
                  >
                    <div className="flex items-center gap-1.5 mb-2 pb-1.5 border-b border-gray-100 dark:border-gray-700">
                      <Sparkles className="w-3 h-3 text-indigo-500" />
                      <span className="text-[10px] font-semibold text-gray-900 dark:text-gray-100">
                        AI Analysis
                      </span>
                    </div>
                    {trustScore == null ||
                    qualityScore == null ||
                    spamScore == null ||
                    toxicityScore == null ? (
                      <div className="space-y-1">
                        <div className="text-[10px] text-gray-700 dark:text-gray-300">
                          AI scores not available for this message yet.
                        </div>
                        <div className="text-[9px] text-gray-500 dark:text-gray-400">
                          Waiting for the API to populate{" "}
                          <span className="font-mono">quality_score</span>,{" "}
                          <span className="font-mono">spam_score</span>, and{" "}
                          <span className="font-mono">toxicity_score</span>.
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-1.5">
                        {[
                          {
                            label: "Trust Score",
                            score: trustScore,
                            thresholds: [0.8, 0.6, 0.4],
                          },
                          {
                            label: "Quality",
                            score: qualityScore,
                            thresholds: [0.7, 0.4, 0],
                          },
                          {
                            label: "Spam Risk",
                            score: spamScore,
                            thresholds: [0.3, 0.6, 1],
                            inverted: true,
                          },
                          {
                            label: "Toxicity",
                            score: toxicityScore,
                            thresholds: [0.3, 0.6, 1],
                            inverted: true,
                          },
                        ].map(({ label, score, thresholds, inverted }) => {
                          const barColor = inverted
                            ? score < thresholds[0]
                              ? "bg-emerald-500"
                              : score < thresholds[1]
                                ? "bg-amber-500"
                                : "bg-red-500"
                            : score >= thresholds[0]
                              ? "bg-emerald-500"
                              : score >= thresholds[1]
                                ? "bg-blue-500"
                                : score >= thresholds[2]
                                  ? "bg-amber-500"
                                  : "bg-red-500";
                          const textColor = inverted
                            ? score < thresholds[0]
                              ? "text-emerald-600"
                              : score < thresholds[1]
                                ? "text-amber-600"
                                : "text-red-600"
                            : score >= thresholds[0]
                              ? "text-emerald-600"
                              : score >= thresholds[1]
                                ? "text-blue-600"
                                : score >= thresholds[2]
                                  ? "text-amber-600"
                                  : "text-red-600";
                          return (
                            <div
                              key={label}
                              className="flex items-center justify-between"
                            >
                              <span className="text-[9px] text-gray-500">
                                {label}
                              </span>
                              <div className="flex items-center gap-1">
                                <div className="w-12 h-1 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                                  <div
                                    className={`h-full rounded-full ${barColor}`}
                                    style={{
                                      width: `${Math.round(score * 100)}%`,
                                    }}
                                  />
                                </div>
                                <span
                                  className={`text-[9px] font-bold w-6 ${textColor}`}
                                >
                                  {Math.round(score * 100)}%
                                </span>
                              </div>
                            </div>
                          );
                        })}
                        {securityRisk != null && (
                          <div className="flex items-center justify-between">
                            <span className="text-[9px] text-gray-500">
                              Security
                            </span>
                            <div className="flex items-center gap-1">
                              <div className="w-12 h-1 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                                <div
                                  className={`h-full rounded-full ${securityRisk < 0.3 ? "bg-emerald-500" : securityRisk < 0.6 ? "bg-amber-500" : "bg-red-500"}`}
                                  style={{
                                    width: `${Math.round(securityRisk * 100)}%`,
                                  }}
                                />
                              </div>
                              <span
                                className={`text-[9px] font-bold w-6 ${securityRisk < 0.3 ? "text-emerald-600" : securityRisk < 0.6 ? "text-amber-600" : "text-red-600"}`}
                              >
                                {Math.round(securityRisk * 100)}%
                              </span>
                            </div>
                          </div>
                        )}
                        {post.ai_reactions && post.ai_reactions.length > 0 && (
                          <div className="flex items-center justify-between pt-1.5 border-t border-gray-100 dark:border-gray-700">
                            <span className="text-[9px] text-gray-500">
                              AI Reactions
                            </span>
                            <span className="text-sm">
                              {post.ai_reactions.join(" ")}
                            </span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
            {/* Reply button */}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setActiveReplyTo(
                  activeReplyTo && activeReplyTo.id === post.id
                    ? null
                    : { id: post.id, username: post.username },
                );
              }}
              className="text-xs text-blue-600 dark:text-blue-400 hover:underline font-medium"
            >
              {activeReplyTo?.id === post.id ? "Cancel" : "Reply"}
            </button>
          </div>
          {activeReplyTo?.id === post.id && (
            <ReplyComposer
              parentId={post.id}
              parentUsername={post.username}
              onCancel={() => setActiveReplyTo(null)}
              onSend={handleSendReply}
              autoMention={true}
            />
          )}
        </div>
      </div>
    </div>
  );
});
