import {
  useRef,
  useCallback,
  useEffect,
  useState,
  useLayoutEffect,
  useMemo,
} from "react";
import { Activity, RefreshCw, ArrowDown } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import { storage } from "@/lib/storage";
import { agentControlService } from "@/services/agentControlService";
import { isFromInternalAgent } from "@/helpers/agents";
import { normalizeParentId } from "@/helpers/reactions";
import { emptyReactionCounts } from "./EmojiReactions";
import { AgentPauseMessage } from "@/components/AgentPauseMessage";
import { usePullToRefresh } from "@/hooks/usePullToRefresh";
import { PullToRefreshIndicator } from "@/components/ui/PullToRefreshIndicator";
import { USE_FLEX_CHAT_LAYOUT } from "@/config/features";

import type { Post, MessageListProps } from "./messages/types";
import { TIME_FILTER_WINDOWS } from "./messages/utils";
import { MessageBubble } from "./messages/MessageBubble";
import { useScrollManager } from "./messages/ScrollManager";
import { useMessageStream } from "@/hooks/useMessageStream";
import { StreamingBubble } from "./messages/StreamingBubble";
import { RouterResponseGroup } from "./messages/RouterResponseGroup";
import { useWaitingPosts } from "@/hooks/useWaitingPosts";
import { useAutoSummaries } from "@/hooks/useAutoSummaries";
import { useReactions } from "@/hooks/useReactions";
import { isInternalHandle } from "@/helpers/agents";

export type { Post, MessageListProps };

export function MessageList({
  posts,
  isLoading,
  demoMode = false,
  onDemoReaction,
  onDemoReply,
  timeRange,
  displayLimit,
  acknowledgedBlocked,
  setAcknowledgedBlocked,
  onMessageRead,
  onPostExpand,
  isMessageRead,
  isPostExpanded,
  isGlobalCondensedMode,
  onHashtagClick,
  onAgentClick,
  onReplyToPost,
  messagesContainerRef,
  messagesEndRef,
  onScroll,
  newMessagesCount = 0,
  onScrollToBottom,
  isInHistoryMode = false,
  onJumpToLatest,
  viewerUsername,
  spaceId,
  totalAvailable,
  messagesShowing,
  hasOlderMessages,
  onLoadMore,
  pageSize,
  isLoadingOlder = false,
  maxRecentMessages = 100,
  rosterLookup,
  onRefresh,
  pendingCloudAgentPosts = new Set(),
  sentNonCloudAgentPosts = new Set(),
  agentActivityByPost = new Map(),
  failedCloudAgentPosts = new Map(),
  skippedCloudAgentPosts = new Map(),
  onRetryCloudAgent,
  routerStreamState,
  messagesTopInsetPx = 0,
}: MessageListProps) {
  // SSE streaming messages (contract v1)
  const streamingMessages = useMessageStream();

  // Resolve agent display name from streaming agent_id
  const getStreamingAgentName = useCallback(
    (agentId: string) => {
      if (!rosterLookup) return undefined;
      for (const [, member] of Object.entries(rosterLookup)) {
        if (
          (member as any)?.agent_id === agentId ||
          (member as any)?.id === agentId
        ) {
          return (
            (member as any)?.display_name ||
            (member as any)?.username ||
            (member as any)?.name
          );
        }
      }
      return undefined;
    },
    [rosterLookup],
  );

  // Pull-to-refresh for mobile
  const {
    pullDistance,
    isRefreshing: isPullRefreshing,
    isPastThreshold,
  } = usePullToRefresh({
    containerRef: messagesContainerRef,
    onRefresh: onRefresh ?? (() => {}),
  });

  const timeFilter = timeRange || "all";
  const [filterText] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState<string>("");
  const [savingEdit, setSavingEdit] = useState<boolean>(false);
  const [pauseResumePending, setPauseResumePending] = useState<
    Record<string, boolean>
  >({});
  const [clearedPauseMessages, setClearedPauseMessages] = useState<
    Record<string, boolean>
  >({});
  const [activeReplyTo, setActiveReplyTo] = useState<{
    id: number | string;
    username: string;
  } | null>(null);
  const messageRowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const { toast } = useToast();

  const isUserSelectingText = useCallback((): boolean => {
    if (typeof window === "undefined") return false;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return false;
    const container = messagesContainerRef.current;
    if (!container) return false;
    const isInside = (node: Node | null): boolean => {
      if (!node) return false;
      const element =
        node.nodeType === Node.ELEMENT_NODE
          ? (node as Element)
          : node.parentElement;
      return !!element && container.contains(element);
    };
    return isInside(selection.anchorNode) || isInside(selection.focusNode);
  }, [messagesContainerRef]);

  const isSelectionWithinElement = useCallback(
    (element: HTMLElement | null) => {
      if (typeof window === "undefined") return false;
      if (!element) return false;
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed) return false;
      const isInside = (node: Node | null): boolean => {
        if (!node) return false;
        const el =
          node.nodeType === Node.ELEMENT_NODE
            ? (node as Element)
            : node.parentElement;
        return !!el && element.contains(el);
      };
      return isInside(selection.anchorNode) || isInside(selection.focusNode);
    },
    [],
  );

  // Robust timestamp parser
  const parseTimestamp = useCallback((s: string): number => {
    if (!s) return 0;
    const raw = s.trim();
    const hasExplicitOffset = /([zZ]|[+-]\d{2}:?\d{2})$/.test(raw);
    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) {
      if (!hasExplicitOffset)
        return parsed.getTime() - parsed.getTimezoneOffset() * 60_000;
      return parsed.getTime();
    }
    const fallback = Date.parse(`${raw}${hasExplicitOffset ? "" : "Z"}`);
    return Number.isNaN(fallback) ? 0 : fallback;
  }, []);

  const hiddenStreamingAgentIds = useMemo(() => {
    const hiddenIds = new Set<string>();

    for (const [messageId, stream] of streamingMessages.entries()) {
      const content = (stream.content || "").trim();
      if (!content) continue;
      if (!/^\[tool:/i.test(content)) continue;
      hiddenIds.add(messageId);
    }

    return hiddenIds;
  }, [streamingMessages]);

  const processingByPostId = useMemo(() => {
    const map = new Map<string, Set<string>>();

    for (const [postId, activity] of agentActivityByPost.entries()) {
      if (!postId) continue;
      if (!activity) continue;
      const status = String(activity.status || "").toLowerCase();
      if (
        !status ||
        [
          "completed",
          "complete",
          "done",
          "resolved",
          "error",
          "failed",
          "cancelled",
          "canceled",
          "timeout",
          "timed_out",
        ].includes(status)
      ) {
        continue;
      }
      const agentLabel = (activity.tool_name || "").trim();
      if (!agentLabel) continue;
      const normalized = agentLabel.replace(/^@/, "").trim();
      if (!normalized || isInternalHandle(normalized)) continue;
      const key = String(postId);
      const current = map.get(key) || new Set();
      current.add(normalized);
      map.set(key, current);
    }

    return map;
  }, [agentActivityByPost]);

  // Filter out internal system agents and deduplicate
  const visiblePosts = useMemo(() => {
    const orderedIds: string[] = [];
    const byId = new Map<string, Post>();
    posts.forEach((post, index) => {
      const idKey =
        post.id != null && String(post.id) ? String(post.id) : `idx-${index}`;
      const existing = byId.get(idKey);
      if (!existing) {
        orderedIds.push(idKey);
        byId.set(idKey, post);
        return;
      }
      const existingTs = existing.uploaded_at
        ? parseTimestamp(existing.uploaded_at)
        : 0;
      const nextTs = post.uploaded_at ? parseTimestamp(post.uploaded_at) : 0;
      if (nextTs >= existingTs) byId.set(idKey, post);
    });
    const orderedPosts = orderedIds
      .map((id) => byId.get(id))
      .filter(Boolean) as Post[];
    return orderedPosts.filter((p) => !isFromInternalAgent(p as any));
  }, [posts, parseTimestamp]);

  // Time-filter posts
  const timeFilteredPosts = useMemo(() => {
    const win = TIME_FILTER_WINDOWS[timeFilter];
    if (win == null) return visiblePosts;
    const cutoff = Date.now() - win;
    return visiblePosts.filter((p) => parseTimestamp(p.uploaded_at) >= cutoff);
  }, [visiblePosts, timeFilter, parseTimestamp]);

  const qaSeedMessagesEnabled =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("qa_seed_messages") === "1";

  const qaSeedPosts = useMemo<Post[]>(() => {
    if (!qaSeedMessagesEnabled) return [];
    const base = Date.now();
    return Array.from({ length: 45 }, (_, index) => {
      const n = index + 1;
      return {
        id: n,
        username: "qa_seed",
        content: `qa_seed overflow message ${n} — deterministic capture content for scroll/placement validation`,
        uploaded_at: new Date(base - (45 - n) * 1000).toISOString(),
      } as Post;
    });
  }, [qaSeedMessagesEnabled]);

  // Apply displayLimit
  const renderPostsBase =
    displayLimit === 0
      ? timeFilteredPosts
      : timeFilteredPosts.slice(-displayLimit);

  const renderPosts =
    renderPostsBase.length === 0 && qaSeedMessagesEnabled
      ? qaSeedPosts
      : renderPostsBase;

  // Memoized filtered posts (text filter)
  const filteredPosts = useMemo(() => {
    const ft = filterText.trim().toLowerCase();
    if (!ft) return renderPosts;
    return renderPosts.filter((p) => {
      if (p.username?.toLowerCase().includes(ft)) return true;
      if (p.content?.toLowerCase().includes(ft)) return true;
      if (ft.startsWith("@")) return p.username?.toLowerCase() === ft.slice(1);
      if (ft.startsWith("#")) return p.content?.toLowerCase().includes(ft);
      return false;
    });
  }, [renderPosts, filterText]);

  // Use scroll manager hook
  const { isScrolledToTop, isScrolledUp, isIPhone, bottomInsetPx } =
    useScrollManager({
      messagesContainerRef,
      messagesEndRef,
      onScroll,
      hasOlderMessages,
      onLoadMore,
      isInHistoryMode,
      postCount: filteredPosts.length,
      isUserSelectingText,
    });

  // Extracted hooks for waiting posts and auto-summaries
  const { now, latestWaitingByAgent, waitExpiryMsRef } = useWaitingPosts({
    renderPosts,
    parseTimestamp,
    agentActivityByPost,
    pendingCloudAgentPosts,
    isUserSelectingText,
  });

  const { summaries, setSummaries, loadingSummaries, setLoadingSummaries } =
    useAutoSummaries({
      posts,
      isGlobalCondensedMode,
      isPostExpanded,
    });

  // Pause resume handler
  const handlePauseResume = useCallback(
    async (pauseMessageKey: string, agentId: string) => {
      setPauseResumePending((prev) => ({ ...prev, [pauseMessageKey]: true }));
      try {
        await agentControlService.updateControl(agentId, {
          scope: "agent",
          disabled: false,
        });
        setClearedPauseMessages((prev) => ({
          ...prev,
          [pauseMessageKey]: true,
        }));
      } catch (err) {
        console.error("Failed to resume agent after stop action", err);
        toast({
          title: "Failed to resume agent",
          description: err instanceof Error ? err.message : "Please try again.",
          variant: "destructive",
        });
      } finally {
        setPauseResumePending((prev) => {
          const next = { ...prev };
          delete next[pauseMessageKey];
          return next;
        });
      }
    },
    [toast],
  );

  // Current actor resolution
  const currentActor = useMemo(() => {
    const fromProp = (viewerUsername || "").trim();
    if (fromProp) return fromProp;
    try {
      const meta = storage.getUserMetadata?.();
      if (meta?.username) return String(meta.username);
      const sUser = storage.getUsername?.();
      if (sUser) return sUser;
      const legacy =
        localStorage.getItem("current_username") ||
        localStorage.getItem("ax_username") ||
        "";
      return legacy.trim() || undefined;
    } catch {
      return undefined;
    }
  }, [viewerUsername]);

  // Extracted hook for reactions and replies
  const {
    aggregatedReactions,
    handleReact,
    repliesByParent,
    firstReplyIdByParent,
  } = useReactions({
    posts,
    currentActor,
    demoMode,
    onDemoReaction,
  });

  // Reply handler
  const handleSendReply = useCallback(
    async (content: string, parentId: number | string) => {
      try {
        if (demoMode) {
          const EMOJI_CLUSTER =
            /(?:\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?(?:\u200D\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?)?)/gu;
          const matches = (content || "").match(EMOJI_CLUSTER) || [];
          const nonEmoji = (content || "")
            .replace(EMOJI_CLUSTER, "")
            .replace(/\s+/g, "");
          const isEmojiOnly = matches.length > 0 && nonEmoji.length === 0;
          if (isEmojiOnly && onDemoReaction) {
            onDemoReaction(
              normalizeParentId(parentId)!,
              Array.from(new Set(matches)),
              currentActor || "you",
            );
            setActiveReplyTo(null);
            toast({
              title: "Reaction added",
              description: "Emoji reply counted (demo)",
            });
            return;
          }
          if (typeof onDemoReply === "function") {
            onDemoReply((content || "").trim(), normalizeParentId(parentId)!);
            setActiveReplyTo(null);
            toast({ title: "Reply sent (demo)" });
            return;
          }
        }
        const { api } = await import("@/lib/api-clean");
        await api.postMessage(content, { parentId });
        setActiveReplyTo(null);
        toast({
          title: "Reply sent",
          description: "Your reply has been posted",
          duration: 2000,
        });
        if (onRefresh) onRefresh();
      } catch (err) {
        console.error("Failed to send reply", err);
        toast({
          title: "Reply failed",
          description: "Unable to send reply. Please try again.",
          variant: "destructive",
        });
      }
    },
    [toast, demoMode, onDemoReaction, onDemoReply, currentActor, onRefresh],
  );

  // Auto-scroll when opening reply composer
  const CHAT_INPUT_EST_HEIGHT = 120;
  const BOTTOM_BUFFER = 16;
  useLayoutEffect(() => {
    if (!activeReplyTo) return;
    const container = messagesContainerRef.current;
    const rowEl = messageRowRefs.current[String(activeReplyTo.id)];
    if (!rowEl || !container) return;
    const getChatInputHeight = () => {
      const varVal = getComputedStyle(
        document.documentElement,
      ).getPropertyValue("--chat-input-height");
      const parsed = parseInt(varVal || "", 10);
      return !isNaN(parsed) && parsed > 0 ? parsed : CHAT_INPUT_EST_HEIGHT;
    };
    const ensureVisible = () => {
      const chatH = getChatInputHeight();
      const rowRect = rowEl.getBoundingClientRect();
      const viewportBottomLimit = window.innerHeight - chatH - BOTTOM_BUFFER;
      if (rowRect.bottom > viewportBottomLimit) {
        container.scrollBy({
          top: rowRect.bottom - viewportBottomLimit + 8,
          behavior: "smooth",
        });
      } else if (rowRect.top < 0) {
        rowEl.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    };
    ensureVisible();
    requestAnimationFrame(ensureVisible);
    const timeoutId = window.setTimeout(ensureVisible, 120);
    return () => window.clearTimeout(timeoutId);
  }, [activeReplyTo, messagesContainerRef]);

  // Auto-mark messages as read with IntersectionObserver
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const el = entry.target as HTMLElement;
          if (entry.isIntersecting && entry.intersectionRatio > 0.6) {
            if (isUserSelectingText()) return;
            const pid = el.dataset.postId;
            if (pid) {
              const messageId = `post-${pid}`;
              if (!isMessageRead(messageId)) onMessageRead(messageId);
            }
          }
        });
      },
      { root: container, threshold: [0.6] },
    );
    filteredPosts.forEach((p) => {
      const el = messageRowRefs.current[String(p.id)];
      if (el) io.observe(el);
    });
    return () => io.disconnect();
  }, [
    messagesContainerRef,
    isMessageRead,
    onMessageRead,
    filteredPosts,
    isUserSelectingText,
  ]);

  return (
    <div className="flex-1 flex flex-col min-h-0 min-w-0 overflow-hidden max-w-full">
      {/* Load Older Messages */}
      {hasOlderMessages && isScrolledToTop && (
        <div className="text-center py-1">
          <button
            onClick={onLoadMore}
            disabled={isLoadingOlder}
            className={`text-xs px-2 py-1 rounded transition-colors ${
              isLoadingOlder
                ? "text-gray-400 dark:text-gray-500 cursor-wait"
                : "text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
          >
            {isLoadingOlder ? (
              <>
                <RefreshCw className="w-3 h-3 inline animate-spin mr-1" />
                Loading older messages...
              </>
            ) : (
              "↑ Load older messages"
            )}
          </button>
        </div>
      )}

      {/* Pull-to-Refresh Indicator */}
      <PullToRefreshIndicator
        pullDistance={pullDistance}
        isRefreshing={isPullRefreshing}
        isPastThreshold={isPastThreshold}
      />

      {/* Scrollable Messages Area - column-reverse layout */}
      <div
        className={`flex-1 overflow-y-auto overflow-x-hidden relative min-h-0 scroll-invisible messages-scroll ${isIPhone ? "iphone-scroll" : ""}`}
        ref={messagesContainerRef}
        data-messages-container
        style={{
          overflowAnchor: "auto",
          scrollBehavior: "auto",
          overscrollBehaviorY: "contain",
          // NOTE: No paddingBottom here — clearance is handled entirely by the
          // bottomInsetPx spacer div at the end of the message list.
          // Adding paddingBottom *and* the spacer caused double-stacking: the last
          // message sat ~chatInputHeight too high (an extra blank gap above the input).
          // The spacer approach is preferred because it gives scrollIntoView a real
          // DOM target. See b34c5ac for spacer initialization fix.
        }}
      >
        {/* Messages wrapper — justify-end pushes content to bottom even when list is short */}
        <div
          data-messages-content
          className="flex min-h-full max-w-full min-w-0 flex-col justify-end divide-y divide-gray-200 overflow-x-hidden pb-4 transition-transform duration-75 dark:divide-gray-700"
          style={{
            transform:
              pullDistance > 0 ? `translateY(${pullDistance}px)` : undefined,
          }}
        >
          {isLoading ? (
            <div className="p-6 text-center text-gray-500">
              <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2" />
              Loading messages...
            </div>
          ) : filteredPosts && filteredPosts.length > 0 ? (
            filteredPosts.map((post) => {
              const messageId = `post-${post.id}`;
              const rowKey = String(post.id);
              const isRead = isMessageRead(messageId);

              if (post.message_type === "agent_pause") {
                const pauseMessageKey = rowKey;
                const agentIdForPause =
                  post.agent_id ?? post.metadata?.agent_id;
                const isPauseCleared = !!clearedPauseMessages[pauseMessageKey];
                const isResumePending = !!pauseResumePending[pauseMessageKey];
                const resumeHandler = agentIdForPause
                  ? () =>
                      handlePauseResume(
                        pauseMessageKey,
                        String(agentIdForPause),
                      )
                  : undefined;

                return (
                  <div
                    key={post.id}
                    data-post-id={post.id}
                    ref={(el) => {
                      messageRowRefs.current[rowKey] = el;
                    }}
                    className="p-3"
                    onClick={(e) => {
                      if (isSelectionWithinElement(e.currentTarget)) return;
                      if (!isRead) onMessageRead(messageId);
                    }}
                  >
                    <AgentPauseMessage
                      message={post}
                      onResume={resumeHandler}
                      resumePending={isResumePending}
                      resumeDisabled={!agentIdForPause}
                      isCleared={isPauseCleared}
                    />
                  </div>
                );
              }

              const reactionKey = String(normalizeParentId(post.id) ?? post.id);
              const entry = aggregatedReactions[reactionKey] || {
                counts: emptyReactionCounts(),
                user: {},
              };

              return (
                <div key={post.id} className="w-full min-w-0">
                  <MessageBubble
                    post={post}
                    posts={posts}
                    isRead={isRead}
                    isExpanded={isPostExpanded(post.id)}
                    isGlobalCondensedMode={isGlobalCondensedMode}
                    onMessageRead={onMessageRead}
                    onPostExpand={onPostExpand}
                    onHashtagClick={onHashtagClick}
                    onAgentClick={onAgentClick}
                    onReplyToPost={onReplyToPost}
                    reactionEntry={entry}
                    handleReact={handleReact}
                    repliesByParent={repliesByParent}
                    firstReplyIdByParent={firstReplyIdByParent}
                    messageRowRefs={messageRowRefs}
                    rosterLookup={rosterLookup}
                    demoMode={demoMode}
                    viewerUsername={viewerUsername}
                    spaceId={spaceId}
                    currentActor={currentActor}
                    parseTimestamp={parseTimestamp}
                    now={
                      post.waiting_since ||
                      agentActivityByPost.has(String(post.id)) ||
                      pendingCloudAgentPosts.has(post.id)
                        ? now
                        : 0
                    }
                    latestWaitingByAgent={latestWaitingByAgent}
                    waitExpiryMsRef={waitExpiryMsRef}
                    pendingCloudAgentPosts={pendingCloudAgentPosts}
                    sentNonCloudAgentPosts={sentNonCloudAgentPosts}
                    agentActivityByPost={agentActivityByPost}
                    failedCloudAgentPosts={failedCloudAgentPosts}
                    skippedCloudAgentPosts={skippedCloudAgentPosts}
                    onRetryCloudAgent={onRetryCloudAgent}
                    summaries={summaries}
                    setSummaries={setSummaries}
                    loadingSummaries={loadingSummaries}
                    setLoadingSummaries={setLoadingSummaries}
                    editingId={editingId}
                    setEditingId={setEditingId}
                    editValue={editValue}
                    setEditValue={setEditValue}
                    savingEdit={savingEdit}
                    setSavingEdit={setSavingEdit}
                    activeReplyTo={activeReplyTo}
                    setActiveReplyTo={setActiveReplyTo}
                    handleSendReply={handleSendReply}
                    isSelectionWithinElement={isSelectionWithinElement}
                    onRefresh={onRefresh}
                  />
                </div>
              );
            })
          ) : (
            <div className="flex-1 flex items-center justify-center p-8">
              <div className="text-center text-gray-500">
                <Activity className="w-12 h-12 mx-auto mb-4 text-gray-300" />
                <p>No messages yet</p>
                <p className="text-xs mt-1">
                  Start a conversation by typing a message below
                </p>
              </div>
            </div>
          )}

          {/* Streaming messages (in-flight agent responses) */}
          {streamingMessages.size > 0 &&
            Array.from(streamingMessages.values())
              .filter((s) => s.status === "streaming" || s.status === "error")
              .map((stream) => (
                <div
                  key={`stream-${stream.message_id}`}
                  className="w-full min-w-0"
                >
                  <StreamingBubble
                    stream={stream}
                    agentName={getStreamingAgentName(stream.agent_id)}
                    onHashtagClick={onHashtagClick}
                    onAgentClick={onAgentClick}
                  />
                </div>
              ))}

          {/* Router response group (active router fan-out) */}
          {routerStreamState && routerStreamState.phase !== "idle" && (
            <div className="w-full min-w-0">
              <RouterResponseGroup state={routerStreamState} />
            </div>
          )}

          {/* Pagination info */}
          {renderPosts && timeFilteredPosts.length > renderPosts.length && (
            <div className="p-4 text-center border-t">
              <span className="text-sm text-gray-500">
                Showing {filteredPosts.length} of {timeFilteredPosts.length}{" "}
                messages{" "}
                {displayLimit === 0 ? "" : `(limited to ${displayLimit})`}
              </span>
            </div>
          )}

          {/* Bottom anchor — messages stack on top of the input bar */}
          <div
            ref={messagesEndRef}
            data-messages-end
            className="flex-shrink-0"
            style={{ height: `${Math.max(4, bottomInsetPx)}px` }}
          />
        </div>

        {/* Scroll to Bottom Bar */}
        {(isScrolledUp || newMessagesCount > 0) && (
          <div
            role="button"
            tabIndex={0}
            style={{ bottom: 4 }}
            className={`sticky z-20 backdrop-blur-sm border-t cursor-pointer transition-all rounded-b-lg mx-1 ${
              isScrolledUp && isInHistoryMode
                ? "bg-gradient-to-t from-blue-900/95 to-blue-900/80 border-blue-700 hover:bg-blue-800"
                : "bg-gradient-to-t from-gray-900/95 to-gray-900/80 dark:from-gray-800/95 dark:to-gray-800/80 border-gray-700 dark:border-gray-600 hover:bg-gray-900 dark:hover:bg-gray-800"
            }`}
            onClick={
              isScrolledUp && isInHistoryMode && onJumpToLatest
                ? onJumpToLatest
                : onScrollToBottom
            }
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                (isScrolledUp && isInHistoryMode && onJumpToLatest
                  ? onJumpToLatest
                  : onScrollToBottom)?.();
              }
            }}
            aria-label={
              isScrolledUp && isInHistoryMode
                ? "Return to latest messages"
                : newMessagesCount > 0
                  ? `${newMessagesCount} new messages, click to jump to bottom`
                  : "Jump to bottom of messages"
            }
          >
            <div className="flex items-center justify-center gap-2 py-2 text-sm">
              <ArrowDown className="w-4 h-4 text-gray-400" />
              {isScrolledUp && isInHistoryMode ? (
                <span className="text-blue-300 font-semibold">
                  ⚡ Jump to Latest
                </span>
              ) : newMessagesCount > 0 ? (
                <>
                  <span className="text-orange-400 font-semibold">
                    {newMessagesCount} new message
                    {newMessagesCount !== 1 ? "s" : ""}
                  </span>
                  <span className="text-gray-400">
                    • Click to jump to bottom
                  </span>
                </>
              ) : (
                <span className="text-gray-400">Jump to bottom</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
