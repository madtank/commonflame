import {
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  lazy,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AxSurfaceRail,
  type AxSurfaceActionInput,
  type AxWidgetPanelOpenInput,
  type AxForwardInitInput,
} from "@/components/ax-platform/AxSurfaceRail";
import { AxMcpAppWidget } from "@/components/ax-platform/AxMcpAppWidget";
import { AxQuickMenu } from "@/components/ax-platform/AxQuickMenu";
import { Logo } from "@/components/Logo";
import {
  useStreamBuffer,
  type StreamingState,
  type StreamTiming,
} from "@/hooks/useStreamBuffer";
import { AgentHoverCard } from "@/components/AgentHoverCard";
import type { Agent } from "@/types/agent";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { humanizeHandle } from "@/lib/display-utils";
import { DEFAULT_HIDDEN_WIDGETS } from "@/lib/space-agent-surface-policy";
import config from "@/config/environment";
import { SUMMARY_CARDS_FEATURE_ENABLED } from "@/config/summaryCards";
import { storage } from "@/lib/storage";
import { useMcpToolRegistry } from "@/lib/mcp-tool-registry";
import {
  classifySpaceForSwitcher,
  getSpaceSwitcherDisplayName,
  getSpaceSwitcherTitle,
  getSpaceSwitcherTypeTag,
  normalizeStoredSpaceId,
  resolveProvisionedHomeSpaceId,
  truncateSpaceSwitcherName,
  selectStoredOrCurrentSpace,
} from "@/lib/current-space";
import { isRemoteLoopbackApiTarget } from "@/lib/runtime-origin";
import { extractMediaFromContent } from "@/lib/media-utils";
import { renderMediaBlocks } from "@/components/messages/MessageMedia";
import { ExternalMarkdownLink } from "@/components/ui/external-markdown-link";
import {
  extractExplicitMentionHandles,
  normalizeAgentHandle,
  prependAgentMentionIfNeeded,
} from "@/lib/agent-compose";
import {
  applyMentionSelections,
  describeDeliveryOutcome,
  toggleHandleSelection,
} from "@/components/ax-platform/shell/composer-recipients";
import { ComposerRoutingPlaceholder } from "@/components/ax-platform/shell/ComposerRoutingPlaceholder";
import { ComposerAgentRail } from "@/components/ax-platform/shell/ComposerAgentRail";
import {
  buildRailSuggestionHandles,
  parseStoredRecipientHandles,
  prependAgentMentionsIfNeeded,
  toggleRecipientHandle,
} from "@/components/ax-platform/shell/recipient-set";
import {
  getSpaceAgentConversationCards,
  getSpaceAgentSpaces,
  getSpaceAgentDirectory,
  getSpaceAgentTranscript,
  postSpaceAgentAction,
  proxyMcpToolCall,
  sendSpaceAgentMessage,
  summarizeSpaceAgentMessage,
  switchSpaceAgentSpace,
  uploadSpaceAgentFile,
  storeUploadInContext,
  type SpaceAgentAttachment,
  type SpaceAgentConversationCard,
  type SpaceAgentConversationCardsResponse,
  type SpaceAgentMember,
  type SpaceAgentMessage,
  type SpaceAgentSpace,
  type SpaceAgentWidgetDescriptor,
} from "@/lib/space-agent-api";
import {
  AVAILABILITY_META,
  availabilityPriority,
  deriveAvailabilityKey,
  type AvailabilityKey,
} from "@/lib/agent-availability";
import {
  type NoReplySignalPayload,
  type ChatEntry,
  type ChatEntryAttachment,
  buildConversationCardAnchors,
  buildAttachedNoReplyIndicators,
  buildConversationGroups,
  buildDisplayEntries,
  buildEntryLookup,
  buildPendingStreamingReplyByParentId,
  buildNoReplySignalEntry,
  buildHiddenEntryIds,
  buildThreadIdByEntryId,
  buildOrderedEntries,
  formatAbsoluteTimestamp,
  formatTimestamp,
  getCompactPauseTone,
  getConversationCardSummary,
  getConversationCardThreadId,
  getEntryThreadId,
  getStreamingThreadId,
  hasLandedAgentReply,
  isNoReplyPauseEntry,
  isPendingStreamingReplyEntry,
  isSignalOnlyTranscriptEntry,
  mapLiveMessageToEntry,
  mapSendReceiptToEntry,
  shouldHidePendingStreamingReplyEntry,
  shouldRenderActivityEntryAsSurfaceOnly,
  isUserSenderType,
  sanitizeAiSummary,
  shouldRenderEntryAsCard as shouldRenderTranscriptEntryAsCard,
  sortMessagesOldestFirst,
} from "@/components/ax-platform/shell/transcript-model";
import {
  classifyBubbleAttribution,
  getBubbleClassName,
  getBubbleTestId,
} from "@/components/ax-platform/shell/bubble-attribution";
import {
  isActiveProcessingStatus,
  isSuppressedProcessingPayload,
  isTerminalProcessingStatus,
} from "@/components/ax-platform/shell/processing-lifecycle";
import { getStatusDisplay } from "@/components/ax-platform/shell/status-indicator";
import { AgentBadgeWithCard } from "@/components/ax-platform/shell/AgentBadgeWithCard";
import { usePresence } from "@/hooks/usePresence";
import {
  useGlobalSearch,
  type GlobalSearchScope,
  type GlobalSearchResult,
} from "@/hooks/useGlobalSearch";
import {
  getStoredUserSettings,
  USER_SETTINGS_CHANGED_EVENT,
  type UserSettings,
} from "@/hooks/useUserSettings";
import { getStoredThemeState, THEME_CHANGE_EVENT } from "@/lib/theme";
import { shouldSuppressInlineEntrySurfaces } from "@/components/ax-platform/shell/surface-model";
import { buildWorkCardModel } from "@/components/ax-platform/shell/work-card-model";
import { resolveSpaceAgentWorkflowTitle } from "@/lib/space-agent-workflow-registry";
import {
  collectAutoSummaryCandidateIds,
  getAutoSummaryReplacement,
  getAutoSummarySignals,
} from "@/components/ax-platform/shell/auto-summary";
import {
  computeAgentPhaseTitle,
  getPendingResponseDisplay,
  type PendingProgressShape,
  type PendingResponseDisplay,
  type PendingResponseState,
} from "@/components/ax-platform/shell/pending-response";
import { getPendingMonitorState } from "@/components/ax-platform/shell/pending-monitor";
import {
  captureVisibleScrollAnchor,
  chooseBottomFollowScrollBehavior,
  getComposerBottomSafeAreaPx,
  getLatestEntrySnapshot,
  hasNewLatestEntry,
  isBottomLocked,
  isNearBottom,
  restoreVisibleScrollAnchor,
  shouldAutoSnapToBottom,
  shouldShowJumpToLatest,
  type LatestEntrySnapshot,
  type ScrollAnchorSnapshot,
} from "@/components/ax-platform/shell/scroll-follow";
import { MessageAttachmentPreview } from "@/components/ax-platform/shell/MessageAttachmentPreview";
import {
  Activity,
  BriefcaseBusiness,
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  CornerUpLeft,
  Copy,
  Globe2,
  Home,
  ImageIcon,
  Lock,
  Paperclip,
  Radio,
  Search,
  Send,
  Share2,
  Sparkles,
  Loader2,
  Users,
  Wrench,
  X,
} from "lucide-react";

// StreamingState and StreamTiming types are imported from useStreamBuffer

export const TASKS_ALL_HOST_COMMAND = "tasks/all";
export const CONTEXT_ALL_HOST_COMMAND = "context/all";
export const AX_SHELL_MOBILE_VIEWPORT_CLASS =
  "fixed inset-0 h-auto min-h-0 overflow-hidden sm:relative sm:h-[100dvh]";
export const AX_SHELL_HEADER_SAFE_AREA_CLASS =
  "pt-[calc(env(safe-area-inset-top,0px)+1rem)]";
const CONTEXT_EXPLORER_RESOURCE_URI = "ui://context-explorer";

type MainShellEventLogItem = {
  id: string;
  event: string;
  receivedAt: string;
  preview: string;
};

type PendingProgressPayload = {
  toolCount: number | null;
  stepLabel: string | null;
  commandLabels: string[];
};

type RoutingTraceItem = {
  id: string;
  action:
    | "quick_action_select"
    | "quick_action_reset"
    | "explicit_mention"
    | "send";
  timestamp: string;
  defaultAgentHandle: string | null;
  replyTargetHandle: string | null;
  explicitMentions: string[];
  finalTargetHandle: string | null;
  contentPreview: string;
};

type ReplyTarget = {
  id: string;
  label: string;
  content: string;
  conversationId?: string | null;
  targetHandle?: string | null;
};

// Task 48ae545f — Share mirrors Reply UX. The Share button on a card seeds a
// "Sharing: …" context bar above the composer so the existing @mention
// autocomplete drives target selection. The source card reference (cardId +
// messageId + resource_uri) rides along through the existing metadata.forward
// contract so the receiver can render the shared object natively.
type ForwardTarget = {
  cardId: string;
  cardType?: string | null;
  sourceMessageId?: string | null;
  title: string;
  summary?: string | null;
  resourceType?: string | null;
  resourceId?: string | null;
  resourceUri?: string | null;
  taskId?: string | null;
  contextKey?: string | null;
  attachments?: unknown[] | null;
  references?: unknown[] | null;
};

function hasForwardList(value: unknown): value is unknown[] {
  return Array.isArray(value) && value.length > 0;
}

function firstForwardString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value !== "string") continue;
    const trimmed = value.trim();
    if (trimmed) return trimmed;
  }
  return null;
}

function buildForwardMessageMetadata(
  target: ForwardTarget | null,
): Record<string, unknown> | null {
  if (!target) return null;
  return {
    forward: {
      intent: "share",
      source_card_id: target.cardId,
      ...(target.cardType ? { card_type: target.cardType } : {}),
      ...(target.sourceMessageId
        ? { source_message_id: target.sourceMessageId }
        : {}),
      ...(target.resourceType ? { resource_type: target.resourceType } : {}),
      ...(target.resourceId ? { resource_id: target.resourceId } : {}),
      ...(target.resourceUri ? { resource_uri: target.resourceUri } : {}),
      ...(target.taskId ? { task_id: target.taskId } : {}),
      ...(target.contextKey ? { context_key: target.contextKey } : {}),
      title: target.title,
      ...(target.summary ? { summary: target.summary } : {}),
      ...(hasForwardList(target.attachments)
        ? { attachments: target.attachments }
        : {}),
      ...(hasForwardList(target.references)
        ? { references: target.references }
        : {}),
    },
  };
}

function buildShareIntro(target: ForwardTarget | null) {
  if (!target) return "";
  const resourceLabel =
    target.resourceType || target.cardType || target.cardId.split(":", 1)[0];
  const readable = resourceLabel.replace(/[_-]+/g, " ");
  return `Shared ${readable}: ${target.title}`;
}

type ComposerAttachment = {
  localId: string;
  file?: File;
  filename: string;
  contentType: string;
  sizeBytes: number;
  previewUrl?: string;
  uploadId?: string;
  url?: string;
  contextKey?: string;
  uploading: boolean;
  error?: string;
  source: "paste" | "picker";
  stagedAt: string;
  uploadedBy?: string;
};

type ComposerDraftState = {
  draft: string;
  attachedFiles: string[];
  replyTarget: ReplyTarget | null;
};

type OutboundQueueItem = {
  optimisticEntryId: string;
  content: string;
  attachments: string[];
  uploadedAttachments: SpaceAgentAttachment[];
  metadata?: Record<string, unknown> | null;
  parentId: string | null;
  targetHandle: string | null;
  targetLabel: string;
  isKnownTarget: boolean;
};

function asProgressRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function asProgressString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asProgressNumber(value: unknown): number | null {
  const numeric =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value)
        : Number.NaN;
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function extractCommandLabels(value: unknown): string[] {
  const seen = new Set<string>();
  const labels: string[] = [];

  const push = (raw: unknown) => {
    const record = asProgressRecord(raw);
    const candidate =
      asProgressString(raw) ||
      asProgressString(record?.command) ||
      asProgressString(record?.cmd) ||
      asProgressString(record?.label) ||
      asProgressString(record?.name) ||
      asProgressString(record?.title) ||
      asProgressString(record?.text) ||
      null;
    if (!candidate) return;
    const normalized = candidate.replace(/\s+/g, " ").trim();
    const key = normalized.toLowerCase();
    if (!normalized || seen.has(key)) return;
    seen.add(key);
    labels.push(normalized);
  };

  if (Array.isArray(value)) {
    value.forEach(push);
  } else {
    push(value);
  }

  return labels;
}

function extractPendingProgressSignals(
  payload: unknown,
): PendingProgressPayload {
  const record = asProgressRecord(payload);
  const details = asProgressRecord(record?.details);
  const metadata = asProgressRecord(record?.metadata);
  const progress = asProgressRecord(record?.progress);
  const nested = [record, details, metadata, progress].filter(Boolean) as Array<
    Record<string, unknown>
  >;

  const toolCount =
    nested
      .map(
        (candidate) =>
          asProgressNumber(candidate.tool_count) ||
          asProgressNumber(candidate.toolCount) ||
          asProgressNumber(candidate.active_tool_count) ||
          asProgressNumber(candidate.activeToolCount) ||
          (Array.isArray(candidate.active_tools)
            ? candidate.active_tools.length
            : null) ||
          (Array.isArray(candidate.tools) ? candidate.tools.length : null),
      )
      .find((value): value is number => typeof value === "number") || null;

  const stepLabel =
    nested
      .map(
        (candidate) =>
          asProgressString(candidate.step_label) ||
          asProgressString(candidate.stepLabel) ||
          asProgressString(candidate.current_step) ||
          asProgressString(candidate.currentStep) ||
          asProgressString(candidate.step) ||
          asProgressString(candidate.status_text) ||
          asProgressString(candidate.statusText) ||
          (typeof candidate.details === "string"
            ? candidate.details.trim()
            : null),
      )
      .find((value): value is string => Boolean(value)) || null;

  const commandLabels = Array.from(
    new Set(
      nested.flatMap((candidate) => [
        ...extractCommandLabels(candidate.command),
        ...extractCommandLabels(candidate.commands),
        ...extractCommandLabels(candidate.command_preview),
        ...extractCommandLabels(candidate.commandPreview),
      ]),
    ),
  ).slice(0, 3);

  return {
    toolCount,
    stepLabel,
    commandLabels,
  };
}

function extractStructuredProgress(
  payload: unknown,
): PendingProgressShape | null {
  if (!payload || typeof payload !== "object") return null;
  const progress = (payload as Record<string, unknown>).progress;
  if (!progress || typeof progress !== "object") return null;
  const record = progress as Record<string, unknown>;
  const current = Number(record.current);
  const total = Number(record.total);
  const unit = typeof record.unit === "string" ? record.unit : "";
  if (!Number.isFinite(current) || !Number.isFinite(total)) return null;
  return { current, total, unit };
}

function extractActivityText(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  const activity =
    typeof record.activity === "string" ? record.activity.trim() : "";
  if (activity) return activity;
  // `agent_progress` uses `message` as the human-readable activity line
  // instead of `activity`; fall back so both event shapes converge on the
  // same phase-title pipeline.
  const message =
    typeof record.message === "string" ? record.message.trim() : "";
  return message || null;
}

function extractStringField(payload: unknown, key: string): string | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  const value = record[key];
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function extractRetryAfterSeconds(payload: unknown): number | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  const raw = record.retry_after_seconds ?? record.retryAfterSeconds;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function getPersistedPendingReplyDisplay(
  entry: ChatEntry,
): PendingResponseDisplay {
  const agentLabel = entry.fromLabel || entry.meta || "Agent";
  const processing =
    asProgressRecord(entry.metadata?.processing) ||
    asProgressRecord(entry.metadata?.agent_processing);
  const status =
    asProgressString(processing?.status) ||
    asProgressString(processing?.state) ||
    entry.statusLabel ||
    "working";
  const toolName =
    asProgressString(processing?.tool_name) ||
    asProgressString(processing?.tool) ||
    entry.toolName ||
    null;
  const activity =
    asProgressString(processing?.activity) ||
    asProgressString(processing?.message) ||
    null;
  const lines = entry.content
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean);
  const firstLine = lines[0] || "";
  const toolCountMatch = firstLine.match(/\((\d+)\s+tools?\)/i);
  const signals = lines
    .slice(1)
    .map((line) => line.replace(/^[›>•\-\s]+/, "").trim())
    .filter(Boolean)
    .slice(0, 3);

  if (toolCountMatch?.[1]) {
    const count = Number(toolCountMatch[1]);
    if (Number.isFinite(count) && count > 0) {
      signals.unshift(`${count} tool${count === 1 ? "" : "s"} active`);
    }
  }

  return {
    title: computeAgentPhaseTitle({
      actorLabel: agentLabel,
      status,
      toolName,
      activity,
      hasActiveActor: true,
    }),
    detail: null,
    signals,
  };
}

type LauncherItem = {
  id: "tasks" | "agents" | "context" | "search" | "spaces" | "me";
  title: string;
  description: string;
  toolCandidates: string[];
  toolInput?: Record<string, unknown>;
  icon: typeof BriefcaseBusiness;
};

type LauncherItemLaunchOptions = {
  toolInput?: Record<string, unknown>;
};

type ActiveMcpAppPanel = {
  id: string;
  source: "quick_action" | "transcript";
  launcherItemId?: LauncherItem["id"];
  title: string;
  messageId: string;
  spaceId: string;
  widget: SpaceAgentWidgetDescriptor;
  timestamp?: string | null;
};

type McpAppPanelHostCommand = {
  type: string;
  nonce: number;
};

type DirectHtmlContextPanel = {
  title: string;
  widget: SpaceAgentWidgetDescriptor;
};

type AxMcpAppScreenChromeProps = {
  title: string;
  isTasks: boolean;
  isContext: boolean;
  isSearch: boolean;
  isDarkMode: boolean;
  immersive?: boolean;
  bottomInset?: number;
  searchSpaceId?: string | null;
  children: ReactNode;
  onBackToActivity: () => void;
  onShowAllContext: () => void;
  onShowAllTasks: () => void;
};

const MCP_APP_LAUNCHER_NOTCH_OVERLAP = 16;

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function parseMaybeJsonObject(value: unknown): Record<string, unknown> | null {
  if (typeof value === "string") {
    try {
      return recordValue(JSON.parse(value));
    } catch {
      return null;
    }
  }
  return recordValue(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function getContextInitialDataCandidates(
  widget: SpaceAgentWidgetDescriptor,
): Array<Record<string, unknown>> {
  const candidates: unknown[] = [
    widget.initial_data,
    widget.tool_result,
    widget.structured_content,
  ];
  const records: Array<Record<string, unknown>> = [];

  for (const candidate of candidates) {
    const root = parseMaybeJsonObject(candidate);
    if (!root) continue;
    records.push(root);

    const structured = parseMaybeJsonObject(root.structuredContent);
    if (structured) records.push(structured);

    const data = parseMaybeJsonObject(root.data);
    if (data) records.push(data);
  }

  return records;
}

function getSelectedContextItem(
  widget: SpaceAgentWidgetDescriptor,
): Record<string, unknown> | null {
  for (const data of getContextInitialDataCandidates(widget)) {
    const selectedKey =
      stringValue(data.selected_key) ||
      stringValue(data.context_key) ||
      stringValue(data.key);
    const items = Array.isArray(data.items) ? data.items : [];
    const item =
      (selectedKey
        ? items.find((candidate) => {
            const record = recordValue(candidate);
            return record?.key === selectedKey;
          })
        : items[0]) || null;
    const record = recordValue(item);
    if (record) return record;
  }

  return null;
}

function getContextItemHtmlArtifact(
  item: Record<string, unknown> | null,
): Record<string, unknown> | null {
  if (!item) return null;
  const value = parseMaybeJsonObject(item.value) || item;
  const html = stringValue(value.html);
  if (!html) return null;

  const type = stringValue(value.type)?.toLowerCase();
  const contentType =
    stringValue(value.content_type)?.toLowerCase() ||
    stringValue(value.mime_type)?.toLowerCase() ||
    "";
  if (type !== "html" && !contentType.includes("text/html")) return null;

  return value;
}

function isContextWidgetDescriptor(widget: SpaceAgentWidgetDescriptor) {
  const toolName = (widget.tool_name || "").toLowerCase();
  const resourceUri = (widget.resource_uri || "").toLowerCase();
  return (
    toolName === "context" ||
    toolName.startsWith("context.") ||
    resourceUri.includes("context")
  );
}

export function isDirectHtmlContextPanelWidget(
  widget: SpaceAgentWidgetDescriptor,
) {
  return Boolean(
    isContextWidgetDescriptor(widget) &&
    widget.html &&
    widget.tool_action === "render_html",
  );
}

export function buildContextExplorerPanelWidgetFromDirectHtml(
  widget: SpaceAgentWidgetDescriptor,
): SpaceAgentWidgetDescriptor {
  if (!isDirectHtmlContextPanelWidget(widget)) return widget;

  const { html: _html, resource_url: _resourceUrl, ...rest } = widget;
  return {
    ...rest,
    title: "Context",
    resource_uri: CONTEXT_EXPLORER_RESOURCE_URI,
    tool_action: "list",
    lifecycle: "complete",
    display_mode: "fullscreen",
  };
}

export function buildDirectHtmlContextPanelWidget(
  widget: SpaceAgentWidgetDescriptor,
): DirectHtmlContextPanel | null {
  if (!isContextWidgetDescriptor(widget)) return null;

  const artifact = getContextItemHtmlArtifact(getSelectedContextItem(widget));
  const html = stringValue(artifact?.html);
  if (!artifact || !html) return null;

  const title =
    stringValue(artifact.title) ||
    stringValue(artifact.filename) ||
    widget.title ||
    "HTML context";

  return {
    title,
    widget: {
      ...widget,
      title,
      html,
      resource_uri: undefined,
      resource_url: undefined,
      tool_action: "render_html",
      lifecycle: "complete",
      display_mode: "fullscreen",
    },
  };
}

function AxHostedSearchPanel({
  isDarkMode,
  spaceId,
}: {
  isDarkMode: boolean;
  spaceId?: string | null;
}) {
  const { results, total, loading, error, fallback, search, clear } =
    useGlobalSearch();
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<GlobalSearchScope>("all");

  const hasSearched = query.trim().length > 0 || results.length > 0 || !!error;

  const runSearch = useCallback(() => {
    void search({
      query: query.trim() || "*",
      scope,
      limit: 20,
      spaceId: spaceId ?? null,
    });
  }, [query, scope, search, spaceId]);

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      runSearch();
    },
    [runSearch],
  );

  const scopes: Array<{ value: GlobalSearchScope; label: string }> = [
    { value: "all", label: "All" },
    { value: "messages", label: "Messages" },
    { value: "tasks", label: "Tasks" },
    { value: "agents", label: "Agents" },
  ];

  return (
    <div
      className={cn(
        "flex h-full flex-col gap-4 overflow-auto p-4 sm:p-6",
        isDarkMode
          ? "bg-[#040914] text-slate-100"
          : "bg-slate-50 text-slate-950",
      )}
      data-testid="ax-mcp-search-host-fallback"
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
        <div>
          <p
            className={cn(
              "text-xs font-semibold uppercase tracking-[0.22em]",
              isDarkMode ? "text-cyan-200/75" : "text-cyan-700",
            )}
          >
            Unified search
          </p>
          <h2 className="mt-1 text-2xl font-semibold">Search Waystation</h2>
          <p
            className={cn(
              "mt-1 text-sm",
              isDarkMode ? "text-slate-400" : "text-slate-600",
            )}
          >
            Search messages, tasks, and agents in the current workspace.
          </p>
        </div>
        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <div
            className={cn(
              "flex items-center gap-2 rounded-2xl border px-3 py-2 shadow-sm",
              isDarkMode
                ? "border-white/10 bg-white/[0.04]"
                : "border-slate-200 bg-white",
            )}
          >
            <Search
              className={cn(
                "h-5 w-5 shrink-0",
                isDarkMode ? "text-cyan-200" : "text-cyan-700",
              )}
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search messages, tasks, agents…"
              aria-label="Search Waystation"
              className={cn(
                "min-w-0 flex-1 bg-transparent px-1 py-2 text-sm outline-none placeholder:text-slate-400",
                isDarkMode ? "text-white" : "text-slate-950",
              )}
            />
            <button
              type="submit"
              className={cn(
                "rounded-xl px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-60",
                isDarkMode
                  ? "bg-cyan-400 text-slate-950 hover:bg-cyan-300"
                  : "bg-cyan-600 text-white hover:bg-cyan-500",
              )}
              disabled={loading}
            >
              {loading ? "Searching…" : "Search"}
            </button>
          </div>
          <div
            className="flex flex-wrap gap-2"
            role="list"
            aria-label="Search scope"
          >
            {scopes.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => {
                  setScope(item.value);
                  clear();
                }}
                className={cn(
                  "rounded-full border px-3 py-1.5 text-xs font-medium transition",
                  scope === item.value
                    ? isDarkMode
                      ? "border-cyan-300/60 bg-cyan-300/15 text-cyan-100"
                      : "border-cyan-500 bg-cyan-50 text-cyan-800"
                    : isDarkMode
                      ? "border-white/10 bg-white/[0.03] text-slate-300 hover:border-white/20"
                      : "border-slate-200 bg-white text-slate-600 hover:border-slate-300",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        </form>

        {error ? (
          <div className="rounded-xl border border-rose-300/40 bg-rose-500/10 p-3 text-sm text-rose-200">
            {error}
          </div>
        ) : null}
        {fallback ? (
          <div
            className={cn(
              "rounded-xl border p-3 text-xs",
              isDarkMode
                ? "border-amber-300/20 bg-amber-300/10 text-amber-100"
                : "border-amber-200 bg-amber-50 text-amber-800",
            )}
          >
            Showing message-search fallback results because unified search was
            unavailable.
          </div>
        ) : null}
        {hasSearched && !loading ? (
          <p
            className={cn(
              "text-sm",
              isDarkMode ? "text-slate-400" : "text-slate-600",
            )}
          >
            {total || results.length} result
            {(total || results.length) === 1 ? "" : "s"}
          </p>
        ) : null}
        <div className="flex flex-col gap-3">
          {results.map((result) => (
            <AxHostedSearchResult
              key={`${result.type}:${result.id}`}
              result={result}
              isDarkMode={isDarkMode}
            />
          ))}
          {hasSearched && !loading && !error && results.length === 0 ? (
            <div
              className={cn(
                "rounded-2xl border p-6 text-center text-sm",
                isDarkMode
                  ? "border-white/10 bg-white/[0.03] text-slate-400"
                  : "border-slate-200 bg-white text-slate-600",
              )}
            >
              No matching results yet. Try a broader query or another scope.
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function AxHostedSearchResult({
  result,
  isDarkMode,
}: {
  result: GlobalSearchResult;
  isDarkMode: boolean;
}) {
  const title =
    result.title ||
    result.name ||
    result.handle ||
    result.content?.slice(0, 80) ||
    "Untitled result";
  const body =
    result.snippet ||
    result.summary ||
    result.content ||
    result.description ||
    result.specialization;
  const meta = [
    result.type,
    result.spaceName,
    result.author || result.assigned_to,
    result.status,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <article
      className={cn(
        "rounded-2xl border p-4 shadow-sm",
        isDarkMode
          ? "border-white/10 bg-white/[0.04]"
          : "border-slate-200 bg-white",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{title}</p>
          {meta ? (
            <p
              className={cn(
                "mt-1 text-xs capitalize",
                isDarkMode ? "text-slate-400" : "text-slate-500",
              )}
            >
              {meta}
            </p>
          ) : null}
        </div>
        {typeof result.score === "number" ? (
          <span
            className={cn(
              "rounded-full px-2 py-1 text-[11px]",
              isDarkMode
                ? "bg-cyan-300/10 text-cyan-100"
                : "bg-cyan-50 text-cyan-700",
            )}
          >
            {Math.round(result.score * 100)}%
          </span>
        ) : null}
      </div>
      {body ? (
        <p
          className={cn(
            "mt-3 line-clamp-3 text-sm",
            isDarkMode ? "text-slate-300" : "text-slate-700",
          )}
        >
          {body}
        </p>
      ) : null}
    </article>
  );
}

export function AxMcpAppScreenChrome({
  title,
  isTasks,
  isContext,
  isSearch,
  isDarkMode,
  immersive = false,
  bottomInset = 0,
  searchSpaceId,
  children,
  onBackToActivity,
  onShowAllContext,
  onShowAllTasks,
}: AxMcpAppScreenChromeProps) {
  const screenBottomInset = Math.max(
    0,
    bottomInset - MCP_APP_LAUNCHER_NOTCH_OVERLAP,
  );
  const panelStyle = {
    "--mcp-app-panel-mobile-bottom": `${screenBottomInset}px`,
  } as CSSProperties & Record<"--mcp-app-panel-mobile-bottom", string>;

  return (
    <div
      data-testid="ax-mcp-app-panel"
      data-active-screen="mcp-app"
      style={panelStyle}
      className={cn(
        "fixed inset-x-0 top-0 bottom-[var(--mcp-app-panel-mobile-bottom)] z-[65] flex min-h-0 flex-col overflow-hidden sm:bottom-0 sm:z-[80]",
        isDarkMode ? "bg-[#040914] text-white" : "bg-slate-50 text-slate-950",
      )}
    >
      <header
        data-testid="ax-mcp-app-screen-header"
        className={cn(
          "flex shrink-0 items-center justify-between gap-3 border-b px-4 sm:px-6 lg:px-8",
          immersive ? "h-12" : "h-16",
          isDarkMode
            ? "border-white/10 bg-slate-950/95"
            : "border-slate-200 bg-white/95",
        )}
      >
        <div className="flex min-w-0 items-center gap-3">
          {!isContext ? (
            <button
              type="button"
              onClick={onBackToActivity}
              data-testid="ax-mcp-app-back"
              className={cn(
                "inline-flex items-center gap-2 rounded-lg border text-sm font-medium transition",
                immersive ? "h-8 px-2.5" : "h-9 px-3",
                isDarkMode
                  ? "border-white/10 bg-white/[0.04] text-slate-200 hover:border-cyan-300/30 hover:text-white"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:text-slate-950",
              )}
              aria-label="Back to activity stream"
              title="Back to activity stream"
            >
              <CornerUpLeft className="h-4 w-4" />
              <span className="hidden sm:inline">Activity</span>
            </button>
          ) : null}
          <div className="min-w-0">
            <div
              data-testid="ax-mcp-app-screen-title"
              className={cn(
                "truncate font-semibold",
                immersive ? "text-[13px]" : "text-sm",
              )}
            >
              {title}
            </div>
            {!immersive ? (
              <div
                data-testid="ax-mcp-app-screen-mode"
                className={cn(
                  "mt-0.5 truncate text-[11px] uppercase tracking-[0.18em]",
                  isDarkMode ? "text-slate-400" : "text-slate-500",
                )}
              >
                MCP App
              </div>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isTasks ? (
            <button
              type="button"
              onClick={onShowAllTasks}
              data-testid="ax-mcp-app-all-tasks"
              className={cn(
                "inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-sm font-medium transition",
                isDarkMode
                  ? "border-white/10 bg-white/[0.04] text-slate-200 hover:border-cyan-300/30 hover:text-white"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:text-slate-950",
              )}
              aria-label="Show all tasks"
              title="All tasks"
            >
              <Home className="h-4 w-4" />
              <span className="hidden sm:inline">All tasks</span>
            </button>
          ) : null}
          {isContext ? (
            <button
              type="button"
              onClick={onShowAllContext}
              data-testid="ax-mcp-app-all-context"
              className={cn(
                "inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-sm font-medium transition",
                isDarkMode
                  ? "border-white/10 bg-white/[0.04] text-slate-200 hover:border-cyan-300/30 hover:text-white"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:text-slate-950",
              )}
              aria-label="Show all context"
              title="All context"
            >
              <Home className="h-4 w-4" />
              <span className="hidden sm:inline">All context</span>
            </button>
          ) : null}
          <button
            type="button"
            onClick={onBackToActivity}
            data-testid="ax-mcp-app-close"
            className={cn(
              "inline-flex shrink-0 items-center justify-center rounded-lg border transition hover:border-rose-300/35 hover:bg-rose-400/[0.08]",
              immersive ? "h-8 w-8" : "h-9 w-9",
              isDarkMode
                ? "border-white/10 bg-white/[0.04] text-slate-300 hover:text-white"
                : "border-slate-200 bg-white text-slate-500 hover:text-slate-900",
            )}
            aria-label="Close app panel"
            title="Close app panel"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>
      <main
        data-testid="ax-mcp-app-screen-body"
        className="min-h-0 flex-1 overflow-hidden"
      >
        {isSearch ? (
          <div
            className={cn(
              "h-full overflow-auto",
              isDarkMode ? "bg-[#040914]" : "bg-slate-50",
            )}
            data-testid="ax-mcp-search-host-fallback"
          >
            <AxHostedSearchPanel
              isDarkMode={isDarkMode}
              spaceId={searchSpaceId}
            />
          </div>
        ) : (
          children
        )}
      </main>
    </div>
  );
}

const GENERIC_MCP_APP_PANEL_TITLES = new Set([
  "request processed",
  "request process needs review",
  "mcp app",
]);

function resolveMcpAppPanelTitle(surface: AxWidgetPanelOpenInput["surface"]) {
  const rawTitle = (surface.widget.title || "").trim();
  const workflowTitle = resolveSpaceAgentWorkflowTitle(surface.widget);
  if (!rawTitle || GENERIC_MCP_APP_PANEL_TITLES.has(rawTitle.toLowerCase())) {
    return (
      workflowTitle || surface.policyId || surface.widget.tool_name || "MCP app"
    );
  }
  return rawTitle;
}

type AgentProfile = {
  id: string;
  handle: string;
  mentionLabel: string;
  name: string;
  emoji?: string;
  status: string;
  location: string;
  capabilitySummary: string;
  active: boolean;
  /** Real availability tier (online/idle/dormant/…) for label + live-first sort. */
  availability: AvailabilityKey;
};

type HoverCardAgent = Partial<Agent> & { username: string; color?: string };

const helperTips = [
  "Type help for ideas",
  "Ask about tasks, agents, files, or context",
  "Use @agentname only when you want to direct a message yourself",
] as const;

const starterPrompts = [
  "Review my tasks and tell me what needs attention",
  "Show me which agents are available in this space",
  "Find the right agent for this request",
  "Help me understand the current context here",
] as const;

declare global {
  interface Window {
    __AX_MAIN_DEBUG__?: {
      debugVersion: string;
      currentSpaceId: string | null;
      isResponding: boolean;
      isSending: boolean;
      queuedMessageCount: number;
      defaultAgentHandle: string | null;
      defaultAgentHandles: string[];
      currentRoutingLabel: string;
      pendingResponse: PendingResponseState | null;
      pendingResponsesBySourceId: Record<string, PendingResponseState>;
      quickActionAgentHandles: string[];
      routingTrace: RoutingTraceItem[];
      streamingEntry: StreamingState;
      streamTiming: StreamTiming;
      streamEventLog: MainShellEventLogItem[];
    };
  }
}

export const launcherItems = [
  {
    id: "tasks",
    title: "Tasks",
    description: "Board view of active work",
    toolCandidates: ["tasks.list", "tasks"],
    icon: BriefcaseBusiness,
  },
  {
    id: "agents",
    title: "Agents",
    description: "Who's available and what they do",
    toolCandidates: ["agents.list", "agents"],
    icon: Users,
  },
  {
    id: "context",
    title: "Context",
    description: "Shared knowledge in this space",
    toolCandidates: ["context.list", "context"],
    icon: Globe2,
  },
  {
    id: "search",
    title: "Search",
    description: "Find messages and artifacts",
    toolCandidates: [
      "search.query",
      "search",
      "messages.check",
      "messages.list",
      "messages",
    ],
    icon: Search,
  },
  {
    id: "spaces",
    title: "Spaces",
    description: "Browse and switch workspaces",
    toolCandidates: ["spaces.list", "spaces"],
    icon: Home,
  },
  {
    id: "me",
    title: "Identity",
    description: "Your profile and permissions",
    toolCandidates: ["whoami"],
    icon: Sparkles,
  },
] satisfies LauncherItem[];

const agentsLauncherItem =
  launcherItems.find((item) => item.id === "agents") || null;

const HIDDEN_TRANSCRIPT_PATTERNS = [
  /^\s*\[tool:[^\]]+\]\s*$/i,
  /^\s*\[tool:[^\]]+\][\s\S]*$/i,
] as const;

function isHiddenToolTranscriptContent(content?: string | null) {
  const normalized = (content || "").trim();
  if (!normalized) return false;
  return HIDDEN_TRANSCRIPT_PATTERNS.some((pattern) => pattern.test(normalized));
}

const AX_REPLY_WAIT_MS = 30_000;
const AX_STREAM_FINISH_GRACE_MS = 4_000;
const AX_CONCIERGE_HANDLE = "ax";
const QUICK_ACTION_AGENT_LIMIT = 8;
const MIN_COMPOSER_TEXTAREA_HEIGHT = 64;
const MAX_COMPOSER_TEXTAREA_HEIGHT = 240;

const LazyAxSettingsDialog = lazy(() =>
  import("@/components/ax-platform/AxSettingsDialog").then((module) => ({
    default: module.AxSettingsDialog,
  })),
);

// Do not synthesize demo agents in the live directory. Empty backend data must render empty.

function getSpaceVisibilityLabel(
  space?: Pick<
    SpaceAgentSpace,
    | "id"
    | "name"
    | "visibility"
    | "description"
    | "is_personal"
    | "member_count"
  > | null,
  homeSpaceId?: string | null,
) {
  return getSpaceSwitcherTypeTag(space, homeSpaceId);
}

function getSpaceVisibilityHint(
  space?: Pick<
    SpaceAgentSpace,
    | "id"
    | "name"
    | "visibility"
    | "description"
    | "is_personal"
    | "member_count"
  > | null,
  homeSpaceId?: string | null,
) {
  switch (classifySpaceForSwitcher(space, homeSpaceId)) {
    case "home":
      return "Your home workspace for tasks, agents, and shared context.";
    case "team":
      return "Invited members and their agents can collaborate here under the same shared context.";
    case "community":
      return "Approved platform members can subscribe or join this shared community space.";
    case "private":
    default:
      return "Only you and the agents you add to this private space can see its context.";
  }
}

function getSpaceIdentityTitle(
  space: SpaceAgentSpace,
  homeSpaceId?: string | null,
) {
  return getSpaceSwitcherTitle(space, homeSpaceId);
}

function getCompactSpaceName(value?: string | null) {
  return getSpaceSwitcherDisplayName({ name: value });
}

function getSpaceOptionLabel(
  space: SpaceAgentSpace,
  spaces: SpaceAgentSpace[],
) {
  const hasDuplicateName =
    spaces.filter(
      (candidate) =>
        getCompactSpaceName(candidate.name) === getCompactSpaceName(space.name),
    ).length > 1;
  const compactName = getCompactSpaceName(space.name);

  if (hasDuplicateName && space.slug) {
    return `${truncateSpaceSwitcherName(
      compactName,
      14,
    )} (@${truncateSpaceSwitcherName(space.slug, 12)})`;
  }
  return truncateSpaceSwitcherName(compactName, 20);
}

export function getCompactSpaceSelectWidthCh(
  space: SpaceAgentSpace,
  spaces: SpaceAgentSpace[],
) {
  const label = getSpaceOptionLabel(space, spaces);
  return Math.min(Math.max(label.length, 1), 15);
}

export const SPACE_SWITCHER_NATIVE_SELECT_HIT_TARGET_CLASS =
  "absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0";
export const AX_SHELL_HEADER_ROW_CLASS =
  "mx-auto flex w-full max-w-6xl min-w-0 items-center justify-between gap-2 rounded-[28px] border px-2.5 py-3 backdrop-blur-2xl sm:gap-4 sm:px-4";
export const AX_SHELL_HEADER_BRAND_CLASS = "flex shrink-0 items-center gap-3";
export const AX_SHELL_HEADER_ACTIONS_CLASS =
  "flex min-w-0 flex-1 items-center justify-end gap-2 sm:flex-none sm:gap-3";
export const AX_SHELL_SPACE_SWITCHER_CLASS =
  "relative flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-2xl border px-2.5 py-2 text-sm focus-within:ring-2 focus-within:ring-cyan-300/70 sm:w-fit sm:max-w-[21rem] sm:flex-none sm:px-3";
export const AX_SHELL_SPACE_SWITCHER_LABEL_CLASS =
  "pointer-events-none min-w-0 flex-1 truncate text-sm sm:max-w-[10rem] sm:flex-none";
export const AX_SHELL_QUICK_MENU_WRAPPER_CLASS = "shrink-0";

function getSpaceTypeIcon(
  space?: Pick<
    SpaceAgentSpace,
    | "id"
    | "name"
    | "visibility"
    | "description"
    | "is_personal"
    | "member_count"
  > | null,
  homeSpaceId?: string | null,
) {
  switch (classifySpaceForSwitcher(space, homeSpaceId)) {
    case "home":
      return Home;
    case "team":
      return Users;
    case "community":
      return Globe2;
    case "private":
    default:
      return Lock;
  }
}

// How the agent is actually connected — more accurate than the generic
// origin. The ax-presence monitor listener is the real channel for live
// agents (it's what posts heartbeats), so a fresh heartbeat means "Monitor",
// not just "MCP". Prefers an explicit presence `source` when the backend
// exposes it (stack's lane); otherwise uses the online == monitored heuristic.
function deriveConnectionLabel(
  origin: string | null | undefined,
  availability: AvailabilityKey,
  presenceSource?: string | null,
): string {
  const kind = String(origin || "").toLowerCase();
  // Space agents (Waystation) are built-in, not connected via a runtime — label first
  // so the monitor heuristic below doesn't mislabel an always-on space agent.
  if (kind === "space_agent") return "Waystation";
  const source = String(presenceSource || "").toLowerCase();
  if (source.includes("monitor") || source.includes("host")) return "Monitor";
  if (availability === "online") return "Monitor";
  if (kind === "cloud") return "Cloud";
  if (kind === "agentcore") return "AgentCore";
  if (kind === "external_gateway") return "Gateway";
  if (kind === "mcp") return "MCP";
  return origin ? humanizeHandle(origin) : "Workspace";
}

function mapMemberToAgentProfile(member: SpaceAgentMember): AgentProfile {
  const capabilities = member.capabilities || member.capabilities_list || [];
  const normalizedHandle = normalizeMentionHandle(member.handle);
  const normalizedDisplayHandle = normalizeMentionHandle(member.display_name);
  const mentionHandle =
    (normalizedHandle && !looksLikeOpaqueIdentifier(normalizedHandle)
      ? normalizedHandle
      : null) ||
    (normalizedDisplayHandle &&
    !looksLikeOpaqueIdentifier(normalizedDisplayHandle)
      ? normalizedDisplayHandle
      : null) ||
    "agent";
  const displayName =
    member.display_name ||
    member.handle?.replace(/^@/, "") ||
    mentionHandle ||
    "Waystation";
  // Real availability from the canonical roster fields (not the static
  // `member.active` enrollment flag, which made every row read "ACTIVE").
  const availability = deriveAvailabilityKey({
    status: member.status,
    lifecycle_state: member.lifecycle_state,
    presence_fresh: member.presence_fresh,
    presence_age_seconds: member.presence_age_seconds,
    last_heartbeat: member.last_heartbeat,
    last_heartbeat_at: member.last_heartbeat_at,
    last_seen: member.last_seen,
    is_online: member.is_online,
    origin: member.runtime_location?.kind,
  });
  const isLive = availability === "online" || availability === "idle";

  return {
    id: member.id || mentionHandle,
    handle: mentionHandle,
    mentionLabel: humanizeHandle(mentionHandle),
    name: displayName,
    emoji: getAgentEmoji(mentionHandle, displayName),
    status: AVAILABILITY_META[availability].text,
    location: deriveConnectionLabel(
      member.runtime_location?.kind,
      availability,
      member.presence_source,
    ),
    capabilitySummary:
      member.capability_summary ||
      (capabilities.length > 0
        ? capabilities.slice(0, 3).join(", ")
        : "Capabilities available from the space directory"),
    active: isLive,
    availability,
  };
}

function getAgentEmoji(handle?: string | null, displayName?: string | null) {
  const key = (handle || displayName || "").toLowerCase();

  if (!key) return "🤖";
  if (key === "ax" || key === "a_x" || key.includes("space agent")) return "✨";
  // Distinct icons for cloud agents and known legacy handles so the directory /
  // @mention / quick-action surfaces show a unique glyph instead of the default robot.
  if (key.includes("claude")) return "🖥️"; // legacy claude_prime handle
  if (key.includes("atlas")) return "🗺️"; // PM / routing
  if (key.includes("daimon")) return "🛰️"; // presence / lifecycle
  if (key.includes("zephyr")) return "🌬️"; // spec / proof / demo (zephyr = wind)
  if (key.includes("peach")) return "🍑"; // onboarding / provisioning
  if (key.includes("hermes")) return "🪽"; // hermes adapter (before codex)
  if (key.includes("nyx")) return "🌘"; // backend / API / deploy
  if (key.includes("code_weaver") || key.includes("codex")) return "🛠️";
  if (key.includes("stack")) return "🧱"; // legacy stack handle
  if (key.includes("canary")) return "🐤"; // canary / smoke checks
  if (key.includes("anvil")) return "⚒️"; // infra / ops / build
  if (key.includes("god")) return "🔱"; // api_god
  if (key.includes("audit")) return "🔎"; // artifact_audit
  if (key.includes("protocol")) return "🧭";
  if (key.includes("canvas")) return "🎨";
  if (key.includes("cipher")) return "🧠";
  if (key.includes("ranger")) return "🏹";
  if (key.includes("sentinel")) return "🛡️";
  return "🤖";
}

function buildFallbackAgentProfile(
  handle: string,
  options?: Partial<Pick<AgentProfile, "name" | "mentionLabel" | "active">>,
): AgentProfile {
  const normalizedHandle = normalizeMentionHandle(handle) || handle;
  const resolvedName =
    options?.name ||
    (normalizedHandle === AX_CONCIERGE_HANDLE ? "Waystation" : humanizeHandle(handle));
  const resolvedMentionLabel =
    options?.mentionLabel ||
    (normalizedHandle === AX_CONCIERGE_HANDLE
      ? "Waystation"
      : humanizeHandle(normalizedHandle));

  return {
    id: normalizedHandle,
    handle: normalizedHandle,
    mentionLabel: resolvedMentionLabel,
    name: resolvedName,
    emoji: getAgentEmoji(normalizedHandle, resolvedName),
    // Fallback profiles come from recent routing history with no roster
    // presence data — treat as idle (recently seen, liveness unknown) unless
    // explicitly inactive.
    status:
      options?.active === false
        ? AVAILABILITY_META.dormant.text
        : AVAILABILITY_META.idle.text,
    location:
      normalizedHandle === AX_CONCIERGE_HANDLE
        ? "Workspace concierge"
        : "Workspace runtime",
    capabilitySummary:
      normalizedHandle === AX_CONCIERGE_HANDLE
        ? "Default concierge routing and coordination"
        : "Available from recent routing history",
    active: options?.active ?? true,
    availability: options?.active === false ? "dormant" : "idle",
  };
}

function extractEmojiTokens(value?: string | null) {
  if (!value) return [];
  return Array.from(
    value.matchAll(/\p{Extended_Pictographic}(?:\uFE0F)?/gu),
    (match) => match[0],
  );
}

function buildSignalEmojis({
  summary,
  content,
  participants,
  statusLabel,
  surfaceCount,
  replyCount,
}: {
  summary?: string | null;
  content?: string | null;
  participants?: string[];
  statusLabel?: string | null;
  surfaceCount?: number;
  replyCount?: number;
}) {
  const extracted = [
    ...extractEmojiTokens(summary),
    ...extractEmojiTokens(content),
  ];

  const derived: string[] = [];
  const status = (statusLabel || "").toLowerCase();

  if (status.includes("wait")) derived.push("🤔");
  if (status.includes("routing") || status.includes("forward"))
    derived.push("🧭");
  if (status.includes("response")) derived.push("📬");
  if (status.includes("resolved")) derived.push("✅");
  if (status.includes("deliver")) derived.push("📨");

  if ((replyCount || 0) > 1) derived.push("💬");
  if ((surfaceCount || 0) > 0) derived.push("🧩");

  for (const participant of participants || []) {
    derived.push(getAgentEmoji(participant, participant));
  }

  const combined = [...extracted, ...derived].filter(Boolean);
  return combined.slice(0, 8);
}

function AgentIdentityBadge({
  name,
  emoji,
  subtle = false,
  className,
}: {
  name: string;
  emoji?: string;
  subtle?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "inline-flex max-w-full min-w-0 items-center gap-2 rounded-full border px-2.5 py-1",
        subtle
          ? "border-white/10 bg-white/[0.03] text-slate-300"
          : "border-cyan-300/15 bg-cyan-400/[0.08] text-cyan-50",
        className,
      )}
    >
      <span className="text-sm leading-none">{emoji || "🤖"}</span>
      <span className="max-w-[min(42vw,12rem)] truncate text-[11px] font-semibold tracking-[0.08em] sm:max-w-none">
        {name}
      </span>
    </div>
  );
}

function aggregateSignalEmojis(emojis: string[]) {
  const counts = new Map<string, number>();
  const ordered: Array<{ emoji: string; count: number }> = [];

  for (const emoji of emojis) {
    const current = counts.get(emoji) || 0;
    const next = current + 1;
    counts.set(emoji, next);

    if (current === 0) {
      ordered.push({ emoji, count: next });
      continue;
    }

    const existing = ordered.find((entry) => entry.emoji === emoji);
    if (existing) existing.count = next;
  }

  return ordered;
}

function SignalRail({ emojis }: { emojis: string[] }) {
  if (!emojis.length) return null;

  const grouped = aggregateSignalEmojis(emojis);

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      {grouped.map(({ emoji, count }) => (
        <span
          key={emoji}
          className="inline-flex h-8 min-w-8 items-center justify-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 text-sm shadow-[0_10px_30px_-22px_rgba(7,11,24,0.95)]"
          aria-hidden="true"
        >
          {emoji}
          {count > 1 ? (
            <span className="text-[11px] font-semibold text-slate-200/85">
              {count}
            </span>
          ) : null}
        </span>
      ))}
    </div>
  );
}

function normalizeMentionHandle(value?: string | null) {
  if (!value) return null;

  const normalized = value
    .trim()
    .replace(/^@/, "")
    .replace(/\s+/g, "_")
    .replace(/[^a-zA-Z0-9_-]/g, "")
    .replace(/^_+|_+$/g, "");

  return normalized ? normalized.toLowerCase() : null;
}

function looksLikeOpaqueIdentifier(value?: string | null) {
  if (!value) return true;
  if (/^[0-9a-f]{8,}$/i.test(value)) return true;
  if (/^[0-9a-f-]{24,}$/i.test(value)) return true;
  return false;
}

function extractMessagePayload(payload: unknown): SpaceAgentMessage | null {
  if (!payload || typeof payload !== "object") return null;

  const candidate = payload as {
    id?: unknown;
    message?: unknown;
    data?: unknown;
  };

  if (candidate.message && typeof candidate.message === "object") {
    return candidate.message as SpaceAgentMessage;
  }

  if (candidate.data && typeof candidate.data === "object") {
    const nested = candidate.data as { message?: unknown; id?: unknown };
    if (nested.message && typeof nested.message === "object") {
      return nested.message as SpaceAgentMessage;
    }
    if (typeof nested.id === "string") {
      return nested as SpaceAgentMessage;
    }
  }

  if (typeof candidate.id === "string") {
    return candidate as SpaceAgentMessage;
  }

  return null;
}

function extractConversationCardPayload(
  payload: unknown,
): SpaceAgentConversationCard | null {
  if (!payload || typeof payload !== "object") return null;

  const candidate = payload as {
    card?: unknown;
    data?: unknown;
    id?: unknown;
    root_message_id?: unknown;
    conversation_id?: unknown;
  };

  if (candidate.card && typeof candidate.card === "object") {
    return candidate.card as SpaceAgentConversationCard;
  }

  if (candidate.data && typeof candidate.data === "object") {
    const nested = candidate.data as {
      card?: unknown;
      id?: unknown;
      root_message_id?: unknown;
      conversation_id?: unknown;
    };
    if (nested.card && typeof nested.card === "object") {
      return nested.card as SpaceAgentConversationCard;
    }
    if (
      typeof nested.id === "string" ||
      typeof nested.root_message_id === "string" ||
      typeof nested.conversation_id === "string"
    ) {
      return nested as SpaceAgentConversationCard;
    }
  }

  if (
    typeof candidate.id === "string" ||
    typeof candidate.root_message_id === "string" ||
    typeof candidate.conversation_id === "string"
  ) {
    return candidate as SpaceAgentConversationCard;
  }

  return null;
}

function getPreferredUserLabel(value?: string | null) {
  if (!value) return "You";

  const trimmed = value.trim();
  if (!trimmed) return "You";
  if (trimmed.startsWith("@")) return trimmed.replace(/^@/, "");

  const normalized = normalizeMentionHandle(trimmed);
  if (
    normalized &&
    !looksLikeOpaqueIdentifier(normalized) &&
    !trimmed.includes(" ")
  ) {
    return normalized;
  }

  return "You";
}

function formatAttachmentFileType(contentType: string, filename: string) {
  if (contentType) {
    if (contentType.startsWith("image/")) {
      return contentType.replace("image/", "").toUpperCase();
    }
    const subtype = contentType.split("/")[1];
    if (subtype) return subtype.toUpperCase();
  }

  const extension = filename.split(".").pop();
  return extension ? extension.toUpperCase() : "FILE";
}

function getUploadErrorMessage(err: unknown) {
  const response = (err as { response?: { status?: unknown } } | null)
    ?.response;
  const status =
    typeof response?.status === "number" ? response.status : undefined;

  if (status === 413) {
    return "File is too large for the current upload limit.";
  }
  if (status === 401 || status === 403) {
    return "You are not authorized to upload this file.";
  }

  return "Upload failed";
}

function extractSafeHandle(value?: string | null) {
  if (!value) return null;

  const trimmed = value.trim();
  if (!trimmed) return null;

  const explicitMention = trimmed.match(/@([a-zA-Z0-9_-]+)/);
  if (explicitMention?.[1]) {
    return normalizeMentionHandle(explicitMention[1]);
  }

  if (/[,\s]/.test(trimmed)) return null;

  const normalized = normalizeMentionHandle(trimmed);
  if (!normalized || looksLikeOpaqueIdentifier(normalized)) return null;

  return normalized;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function stripLeadingDirectedMention(
  content: string,
  routeLabel?: string | null,
) {
  // The route label may list several recipients ("@a · @b") when the rail
  // fanned a send out to a multi-recipient set — strip each leading mention
  // that belongs to the label, in any order.
  const labelMentions = Array.from(
    (routeLabel || "").matchAll(/@([a-zA-Z0-9_-]+)/g),
    (match) => normalizeMentionHandle(match[1]),
  ).filter((handle): handle is string => Boolean(handle));
  const routeHandles =
    labelMentions.length > 0
      ? labelMentions
      : [extractSafeHandle(routeLabel)].filter((handle): handle is string =>
          Boolean(handle),
        );
  if (routeHandles.length === 0) return content;

  let stripped = content.trimStart();
  let changed = true;
  while (changed) {
    changed = false;
    for (const handle of routeHandles) {
      const mentionPattern = new RegExp(
        `^@${escapeRegExp(handle)}(?=\\s|$)\\s*`,
        "i",
      );
      if (mentionPattern.test(stripped)) {
        stripped = stripped.replace(mentionPattern, "");
        changed = true;
      }
    }
  }

  if (stripped === content.trimStart()) return content;
  // Strip all or strip none: if a leading mention survives (the label only
  // covered some of the recipients — e.g. a server-echoed label naming just
  // the primary target of a fan-out), show exactly what was sent. Removing
  // a subset makes the message read as if it went to fewer agents.
  if (/^@[a-zA-Z0-9_-]+(\s|$)/.test(stripped)) return content;
  return stripped.trim().length > 0 ? stripped : content;
}

function getUserBadgeLabel(value?: string | null) {
  const preferred = getPreferredUserLabel(value);
  if (preferred === "You") return preferred;
  return `@${preferred}`;
}

function buildCardCopyText(
  summary: string,
  participantLabel: string | null | undefined,
  mentionLabel: string | null | undefined,
  timestamp: string | null,
  statusLabel: string | null | undefined,
  entries: ChatEntry[],
) {
  const sections = [
    `Summary: ${summary}`,
    `Participants: ${participantLabel || "Waystation"}`,
    mentionLabel ? `Mentions: ${mentionLabel}` : null,
    `When: ${timestamp || "just now"}`,
    `Status: ${statusLabel || "Delivered"}`,
    "",
    "Messages:",
    ...entries.map((entry) => {
      const lines = [
        `- ${entry.fromLabel || entry.meta}`,
        entry.summarizedAt
          ? `  Summary generated: ${entry.summarizedAt}`
          : null,
        entry.aiSummary
          ? `  Summary: ${sanitizeAiSummary(entry.aiSummary)}`
          : null,
        entry.content ? `  Content: ${entry.content}` : null,
      ].filter(Boolean);
      return lines.join("\n");
    }),
  ];

  return sections.filter(Boolean).join("\n");
}

function formatCardMetaList(
  values: string[],
  expanded: boolean,
  limit = 3,
): string | null {
  if (!values.length) return null;
  if (expanded || values.length <= limit) {
    return values.join(", ");
  }

  const visible = values.slice(0, limit).join(", ");
  const remaining = values.length - limit;
  return `${visible} +${remaining} more`;
}

function getMentionSearchRank(agent: AgentProfile, rawQuery: string) {
  const query = rawQuery.trim().toLowerCase();
  const handle = agent.handle.toLowerCase();
  const mentionLabel = agent.mentionLabel.toLowerCase();
  const name = agent.name.toLowerCase();
  const opaqueId = agent.id.toLowerCase();

  // Base score by availability tier so live agents rank above quiet ones:
  // online 400, idle 300, needs_setup 200, dormant 100, disabled 0.
  let score = (4 - availabilityPriority(agent.availability)) * 100;

  if (!query) return score;
  if (handle === query || mentionLabel === query || name === query)
    score += 300;
  if (handle.startsWith(query) || mentionLabel.startsWith(query)) score += 220;
  if (name.startsWith(query)) score += 180;
  if (handle.includes(query) || mentionLabel.includes(query)) score += 120;
  if (name.includes(query)) score += 90;
  if (opaqueId.includes(query)) score += 20;

  return score;
}

function matchesMentionQuery(agent: AgentProfile, rawQuery: string) {
  const query = rawQuery.trim().toLowerCase();
  if (!query) return true;

  return (
    agent.handle.toLowerCase().includes(query) ||
    agent.mentionLabel.toLowerCase().includes(query) ||
    agent.name.toLowerCase().includes(query) ||
    agent.id.toLowerCase().includes(query)
  );
}

function getComposerDraftStorageKey(spaceId?: string | null) {
  return `ax-space-agent-draft:${spaceId || "default"}`;
}

function getDefaultAgentStorageKey(spaceId?: string | null) {
  return `ax-space-agent-default:${spaceId || "default"}`;
}

function getRecipientSetStorageKey(spaceId?: string | null) {
  return `ax-space-agent-recipients:${spaceId || "default"}`;
}

const AGENT_RAIL_PINNED_KEY = "ax-agent-rail-pinned";

export function shouldShowExpandedMentionShortcuts(
  agentRailPinned: boolean,
  quickActionAgentCount: number,
) {
  return !agentRailPinned && quickActionAgentCount > 0;
}

function parseComposerDraftState(raw: string | null): ComposerDraftState {
  if (!raw) {
    return { draft: "", attachedFiles: [], replyTarget: null };
  }

  try {
    const parsed = JSON.parse(raw) as Partial<ComposerDraftState>;
    return {
      draft: typeof parsed.draft === "string" ? parsed.draft : raw,
      attachedFiles: Array.isArray(parsed.attachedFiles)
        ? parsed.attachedFiles.filter(
            (value): value is string => typeof value === "string",
          )
        : [],
      replyTarget:
        parsed.replyTarget &&
        typeof parsed.replyTarget === "object" &&
        typeof parsed.replyTarget.id === "string"
          ? {
              id: parsed.replyTarget.id,
              label:
                typeof parsed.replyTarget.label === "string"
                  ? parsed.replyTarget.label
                  : "Selected message",
              content:
                typeof parsed.replyTarget.content === "string"
                  ? parsed.replyTarget.content
                  : "",
              conversationId:
                typeof parsed.replyTarget.conversationId === "string"
                  ? parsed.replyTarget.conversationId
                  : null,
              targetHandle:
                typeof parsed.replyTarget.targetHandle === "string"
                  ? parsed.replyTarget.targetHandle
                  : null,
            }
          : null,
    };
  } catch {
    return { draft: raw, attachedFiles: [], replyTarget: null };
  }
}

function readComposerDraftState(spaceId?: string | null): ComposerDraftState {
  if (typeof window === "undefined") {
    return { draft: "", attachedFiles: [], replyTarget: null };
  }

  const raw = window.sessionStorage.getItem(
    getComposerDraftStorageKey(spaceId),
  );
  return parseComposerDraftState(raw);
}

function getMentionQueryFromDraft(value: string) {
  const match = /(?:^|\s)@([\w-]*)$/.exec(value);
  return match ? (match[1]?.toLowerCase() ?? "") : null;
}

type AgentInteractionStats = {
  lastActivityAt: number;
  directMessageCount: number;
  agentReplyCount: number;
};

function getEntryTimestampMs(
  entry: Pick<ChatEntry, "createdAt">,
  fallback: number,
) {
  const parsed = Date.parse(entry.createdAt || "");
  return Number.isFinite(parsed) ? parsed : fallback;
}

function rankQuickActionAgents(
  agents: AgentProfile[],
  entries: ChatEntry[],
  username?: string | null,
) {
  const usernameHandle = normalizeMentionHandle(username);
  const knownHandles = new Set(
    agents.map((agent) => normalizeMentionHandle(agent.handle) || ""),
  );
  const stats = new Map<string, AgentInteractionStats>();

  const bump = (
    handle: string,
    kind: "direct" | "reply",
    timestamp: number,
  ) => {
    if (
      !handle ||
      handle === AX_CONCIERGE_HANDLE ||
      handle === usernameHandle ||
      !knownHandles.has(handle)
    ) {
      return;
    }

    const current = stats.get(handle) || {
      lastActivityAt: timestamp,
      directMessageCount: 0,
      agentReplyCount: 0,
    };
    current.lastActivityAt = Math.max(current.lastActivityAt, timestamp);
    if (kind === "direct") {
      current.directMessageCount += 1;
    } else {
      current.agentReplyCount += 1;
    }
    stats.set(handle, current);
  };

  entries.forEach((entry, index) => {
    const timestamp = getEntryTimestampMs(entry, index + 1);

    if (entry.role === "user") {
      const targetedHandles = new Set<string>();
      const routedHandle = extractSafeHandle(entry.toLabel);
      if (routedHandle) {
        targetedHandles.add(routedHandle);
      }
      for (const handle of extractExplicitMentionHandles(entry.content || "")) {
        const normalized = normalizeMentionHandle(handle);
        if (normalized) {
          targetedHandles.add(normalized);
        }
      }

      targetedHandles.forEach((handle) => bump(handle, "direct", timestamp));
      return;
    }

    const sourceHandle = extractSafeHandle(entry.fromLabel || entry.meta);
    if (sourceHandle) {
      bump(sourceHandle, "reply", timestamp);
    }
  });

  return [...agents]
    .filter((agent) => {
      const normalized = normalizeMentionHandle(agent.handle);
      return (
        normalized !== AX_CONCIERGE_HANDLE && normalized !== usernameHandle
      );
    })
    .sort((a, b) => {
      // Live-first: available agents on top, then recent-conversation stats as
      // a tiebreaker within the same availability tier.
      const availabilityDelta =
        availabilityPriority(a.availability) -
        availabilityPriority(b.availability);
      if (availabilityDelta !== 0) return availabilityDelta;

      const aHandle = normalizeMentionHandle(a.handle) || a.handle;
      const bHandle = normalizeMentionHandle(b.handle) || b.handle;
      const aStats = stats.get(aHandle);
      const bStats = stats.get(bHandle);

      const hasStatsDelta = Number(Boolean(bStats)) - Number(Boolean(aStats));
      if (hasStatsDelta !== 0) return hasStatsDelta;

      const directDelta =
        (bStats?.directMessageCount || 0) - (aStats?.directMessageCount || 0);
      if (directDelta !== 0) return directDelta;

      const lastActivityDelta =
        (bStats?.lastActivityAt || 0) - (aStats?.lastActivityAt || 0);
      if (lastActivityDelta !== 0) return lastActivityDelta;

      const replyDelta =
        (bStats?.agentReplyCount || 0) - (aStats?.agentReplyCount || 0);
      if (replyDelta !== 0) return replyDelta;

      const activityDelta = Number(b.active) - Number(a.active);
      if (activityDelta !== 0) return activityDelta;

      return a.name.localeCompare(b.name);
    });
}

function coerceLauncherToolResult(
  value: unknown,
): Record<string, unknown> | null {
  if (!value) return null;
  if (typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (Array.isArray(value)) {
    return { items: value };
  }
  return { value };
}

function isLauncherTranscriptEntry(entry: ChatEntry) {
  return entry.messageType === "launcher_app";
}

function getLauncherItemId(entry: ChatEntry) {
  const metadata = entry.metadata as { launcherItemId?: unknown } | null;
  return typeof metadata?.launcherItemId === "string"
    ? metadata.launcherItemId
    : null;
}

function buildLauncherWidgetDescriptor({
  item,
  toolName,
  resourceUri,
  toolInput,
  toolResult,
  lifecycle,
  errorText,
}: {
  item: LauncherItem;
  toolName: string;
  resourceUri?: string | null;
  toolInput?: Record<string, unknown>;
  toolResult?: Record<string, unknown> | null;
  lifecycle: "working" | "complete" | "error";
  errorText?: string | null;
}): SpaceAgentWidgetDescriptor {
  return {
    kind: "mcp_app",
    tool_name: toolName,
    tool_action:
      typeof toolInput?.action === "string" ? toolInput.action : null,
    resource_uri: resourceUri || null,
    title: item.title,
    display_mode: "fullscreen",
    lifecycle,
    fallback_text: errorText || undefined,
    tool_input: toolInput || {},
    tool_result:
      toolResult ||
      (errorText
        ? {
            error: errorText,
          }
        : null),
  };
}

export function AxPlatformShell({
  spaceName = "Current Space",
  agentName = "Waystation",
  username,
  onLogout,
}: {
  spaceName?: string;
  agentName?: string;
  username?: string | null;
  onLogout?: () => void;
}) {
  const queryClient = useQueryClient();
  const storedSpace = storage.getSpace?.();
  const storedSpaceId =
    normalizeStoredSpaceId(storage.getCurrentOrgId?.()) ??
    normalizeStoredSpaceId(storedSpace?.id) ??
    null;
  const [authToken, setAuthToken] = useState<string | null>(
    storage.getUserToken?.() ?? null,
  );
  const [currentSpaceId, setCurrentSpaceId] = useState(storedSpaceId ?? "");
  const {
    presence: presenceEntries,
    getStatus: getPresenceStatus,
    getFreshness: getPresenceFreshness,
  } = usePresence(currentSpaceId);
  const draftRef = useRef("");
  const [pendingEntries, setPendingEntries] = useState<ChatEntry[]>([]);
  const [olderTranscriptMessages, setOlderTranscriptMessages] = useState<
    SpaceAgentMessage[]
  >([]);
  const [isLoadingOlderTranscript, setIsLoadingOlderTranscript] =
    useState(false);
  const [hasOlderTranscript, setHasOlderTranscript] = useState(false);
  const streamBuffer = useStreamBuffer();
  const {
    streamingEntry,
    streamTiming,
    holdingFinal,
    isFinishing,
    isCatchingUp,
  } = streamBuffer;
  const streamBufferRef = useRef(streamBuffer);
  streamBufferRef.current = streamBuffer;
  const [attachedFiles, setAttachedFiles] = useState<string[]>([]);
  const [composerAttachments, setComposerAttachments] = useState<
    ComposerAttachment[]
  >([]);
  const [helperExpanded, setHelperExpanded] = useState(false);
  const [autoSummariesVisible, setAutoSummariesVisible] = useState(
    () => getStoredUserSettings().ai_auto_summarize,
  );
  const [isDarkMode, setIsDarkMode] = useState(
    () => getStoredThemeState().isDarkMode,
  );
  const [expandedSummaryEntries, setExpandedSummaryEntries] = useState<
    Record<string, boolean>
  >({});

  // Widget visibility — per-space, frontend-only
  const [hiddenWidgets, setHiddenWidgets] = useState<Set<string>>(
    () =>
      storage.getHiddenWidgets(currentSpaceId) ??
      new Set(DEFAULT_HIDDEN_WIDGETS),
  );
  useEffect(() => {
    setHiddenWidgets(
      storage.getHiddenWidgets(currentSpaceId) ??
        new Set(DEFAULT_HIDDEN_WIDGETS),
    );
  }, [currentSpaceId]);

  const toggleWidgetVisibility = useCallback(
    (policyId: string) => {
      setHiddenWidgets((prev) => {
        const next = new Set(prev);
        if (next.has(policyId)) {
          next.delete(policyId);
        } else {
          next.add(policyId);
        }
        storage.setHiddenWidgets(next, currentSpaceId);
        return next;
      });
    },
    [currentSpaceId],
  );

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const handleSettingsChanged = (event: Event) => {
      const next = (event as CustomEvent<UserSettings>).detail;
      setAutoSummariesVisible(Boolean(next?.ai_auto_summarize));
    };

    const handleThemeChange = (event: Event) => {
      const detail = (event as CustomEvent<{ isDarkMode?: boolean }>).detail;
      setIsDarkMode(
        typeof detail?.isDarkMode === "boolean"
          ? detail.isDarkMode
          : getStoredThemeState().isDarkMode,
      );
    };

    window.addEventListener(USER_SETTINGS_CHANGED_EVENT, handleSettingsChanged);
    window.addEventListener(THEME_CHANGE_EVENT, handleThemeChange);

    return () => {
      window.removeEventListener(
        USER_SETTINGS_CHANGED_EVENT,
        handleSettingsChanged,
      );
      window.removeEventListener(THEME_CHANGE_EVENT, handleThemeChange);
    };
  }, []);

  useEffect(() => {
    setExpandedSummaryEntries({});
  }, [currentSpaceId]);

  // Close launcher on Escape key
  useEffect(() => {
    if (!helperExpanded) return;
    const handle = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHelperExpanded(false);
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [helperExpanded]);
  const [isResponding, setIsResponding] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [queuedMessageCount, setQueuedMessageCount] = useState(0);
  const [streamEventLog, setStreamEventLog] = useState<MainShellEventLogItem[]>(
    [],
  );
  const [autoFollow, setAutoFollow] = useState(true);
  const [hasUnreadLatest, setHasUnreadLatest] = useState(false);
  const [composerHeight, setComposerHeight] = useState(90);
  const [expandedCards, setExpandedCards] = useState<Record<string, boolean>>(
    {},
  );
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [activeLauncherItemId, setActiveLauncherItemId] = useState<
    LauncherItem["id"] | null
  >(null);
  const [activeMcpAppPanel, setActiveMcpAppPanel] =
    useState<ActiveMcpAppPanel | null>(null);
  const [mcpAppPanelHostCommand, setMcpAppPanelHostCommand] =
    useState<McpAppPanelHostCommand | null>(null);
  const openLauncherItemIds = useMemo(
    () =>
      new Set(
        activeMcpAppPanel?.launcherItemId
          ? [activeMcpAppPanel.launcherItemId]
          : [],
      ),
    [activeMcpAppPanel?.launcherItemId],
  );
  const activePanelIsTasks = useMemo(() => {
    if (!activeMcpAppPanel) return false;
    const widget = activeMcpAppPanel.widget;
    const title = activeMcpAppPanel.title.trim().toLowerCase();
    return (
      activeMcpAppPanel.launcherItemId === "tasks" ||
      title === "tasks" ||
      title === "create task" ||
      widget.resource_uri === "ui://task-board" ||
      widget.resource_uri?.startsWith("ui://task-board@") ||
      widget.resource_uri?.startsWith("ui://tasks/") ||
      widget.tool_name === "tasks"
    );
  }, [activeMcpAppPanel]);

  const activePanelIsSearch = useMemo(() => {
    if (!activeMcpAppPanel) return false;
    const widget = activeMcpAppPanel.widget;
    const title = activeMcpAppPanel.title.trim().toLowerCase();
    return (
      activeMcpAppPanel.source === "quick_action" &&
      activeMcpAppPanel.launcherItemId === "search" &&
      (title === "search" ||
        widget.resource_uri?.startsWith("ui://search") ||
        widget.tool_name?.startsWith("search"))
    );
  }, [activeMcpAppPanel]);
  const activePanelIsContext = useMemo(() => {
    if (!activeMcpAppPanel) return false;
    const widget = activeMcpAppPanel.widget;
    const title = activeMcpAppPanel.title.trim().toLowerCase();
    return (
      activeMcpAppPanel.launcherItemId === "context" ||
      title === "context" ||
      title === "context explorer" ||
      widget.resource_uri?.startsWith("ui://context") ||
      widget.resource_uri?.startsWith("ui://context-explorer") ||
      widget.tool_name === "context" ||
      widget.tool_name?.startsWith("context.")
    );
  }, [activeMcpAppPanel]);

  useEffect(() => {
    setMcpAppPanelHostCommand(null);
  }, [activeMcpAppPanel?.id]);
  const [copiedCardId, setCopiedCardId] = useState<string | null>(null);
  const [replyTarget, setReplyTarget] = useState<ReplyTarget | null>(null);
  // Task 48ae545f — separate state from reply so the user can compose a share
  // without clobbering an in-flight reply context, and vice versa.
  const [forwardTarget, setForwardTarget] = useState<ForwardTarget | null>(
    null,
  );
  const [defaultAgentHandles, setDefaultAgentHandles] = useState<string[]>([]);
  const defaultAgentHandle = defaultAgentHandles[0] ?? null;
  const [agentRailPinned, setAgentRailPinned] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.localStorage.getItem(AGENT_RAIL_PINNED_KEY) !== "false";
  });
  const [routingTrace, setRoutingTrace] = useState<RoutingTraceItem[]>([]);
  const [pendingResponse, setPendingResponse] =
    useState<PendingResponseState | null>(null);
  const [pendingResponsesBySourceId, setPendingResponsesBySourceId] = useState<
    Record<string, PendingResponseState>
  >({});
  // Ref mirror of pendingResponse for use inside SSE event handlers (which
  // are defined inside useEffect and would otherwise capture a stale closure
  // of the state variable). Kept in sync below.
  const pendingResponseRef = useRef<PendingResponseState | null>(null);
  useEffect(() => {
    pendingResponseRef.current = pendingResponse;
  }, [pendingResponse]);
  const [activeWidgetActionKey, setActiveWidgetActionKey] = useState<
    string | null
  >(null);
  const [hasDraft, setHasDraft] = useState(false);
  const [hasDraftText, setHasDraftText] = useState(false);
  const [multiSelectedHandles, setMultiSelectedHandles] = useState<string[]>(
    [],
  );
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [selectedMentionIndex, setSelectedMentionIndex] = useState(0);
  const [cardsEnabled, setCardsEnabled] = useState(() => {
    if (!SUMMARY_CARDS_FEATURE_ENABLED) return false;
    try {
      return window.localStorage.getItem("ax_cards_enabled") === "true";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    if (SUMMARY_CARDS_FEATURE_ENABLED || typeof window === "undefined") return;
    setCardsEnabled(false);
    try {
      window.localStorage.setItem("ax_cards_enabled", "false");
    } catch {
      // ignore
    }
  }, []);

  const toggleCards = useCallback(() => {
    if (!SUMMARY_CARDS_FEATURE_ENABLED) return;
    setCardsEnabled((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem("ax_cards_enabled", String(next));
      } catch {
        // ignore
      }
      return next;
    });
  }, []);
  const toggleSummaryExpansion = useCallback((entryId: string) => {
    setExpandedSummaryEntries((current) => ({
      ...current,
      [entryId]: !current[entryId],
    }));
  }, []);

  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const activityStreamContentRef = useRef<HTMLDivElement | null>(null);
  const hasAnchoredInitialTranscriptRef = useRef(false);
  const latestEntrySnapshotRef = useRef<LatestEntrySnapshot | null>(null);
  const scrollAnchorSnapshotRef = useRef<ScrollAnchorSnapshot | null>(null);
  const pendingAutoScrollFrameRef = useRef<number | null>(null);
  const olderTranscriptAnchorRef = useRef<{
    scrollHeight: number;
    scrollTop: number;
  } | null>(null);
  const composerRef = useRef<HTMLDivElement | null>(null);
  const composerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const requestedSummaryIdsRef = useRef<Set<string>>(new Set());
  const knownTranscriptMessageIdsRef = useRef<Map<string, string>>(new Map());
  const summaryQueueRef = useRef<string[]>([]);
  const isFlushingSummaryQueueRef = useRef(false);
  const landedMessageIdsRef = useRef<Set<string>>(new Set());
  const responseWatchdogRef = useRef<number | null>(null);
  const outboundQueueRef = useRef<OutboundQueueItem[]>([]);
  const isFlushingQueueRef = useRef(false);
  const currentSpaceIdRef = useRef(currentSpaceId);
  const composerDraftPersistTimeoutRef = useRef<number | null>(null);
  const lastComposeMentionAppendRef = useRef<{
    handle: string;
    timestamp: number;
  } | null>(null);

  const appendStreamEventLog = useCallback((event: string, raw: string) => {
    const preview = raw.length > 180 ? `${raw.slice(0, 180)}…` : raw;
    setStreamEventLog((current) =>
      [
        {
          id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
          event,
          receivedAt: new Date().toISOString(),
          preview,
        },
        ...current,
      ].slice(0, 30),
    );
  }, []);

  const appendRoutingTrace = useCallback(
    (
      event: Omit<RoutingTraceItem, "id" | "timestamp" | "contentPreview"> & {
        contentPreview?: string | null;
      },
    ) => {
      const preview = (event.contentPreview || "").trim();
      setRoutingTrace((current) =>
        [
          {
            id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
            timestamp: new Date().toISOString(),
            contentPreview:
              preview.length > 180 ? `${preview.slice(0, 180)}…` : preview,
            ...event,
          },
          ...current,
        ].slice(0, 20),
      );
    },
    [],
  );

  const clearStreamingEntryState = useCallback(() => {
    streamBufferRef.current.reset();
  }, []);

  const syncComposerDraftUiState = useCallback(
    (nextDraft: string, nextHasAttachments = false) => {
      const nextHasDraftText = nextDraft.trim().length > 0;
      const nextHasDraft = nextHasDraftText || nextHasAttachments;
      const nextMentionQuery = getMentionQueryFromDraft(nextDraft);
      setHasDraft((current) =>
        current === nextHasDraft ? current : nextHasDraft,
      );
      setHasDraftText((current) =>
        current === nextHasDraftText ? current : nextHasDraftText,
      );
      setMentionQuery((current) =>
        current === nextMentionQuery ? current : nextMentionQuery,
      );
    },
    [],
  );

  const persistComposerDraftState = useCallback(
    (
      nextDraft: string,
      nextAttachedFiles = attachedFiles,
      nextReplyTarget = replyTarget,
    ) => {
      if (typeof window === "undefined") return;
      const key = getComposerDraftStorageKey(currentSpaceId);

      if (nextDraft.trim() || nextAttachedFiles.length || nextReplyTarget) {
        window.sessionStorage.setItem(
          key,
          JSON.stringify({
            draft: nextDraft,
            attachedFiles: nextAttachedFiles,
            replyTarget: nextReplyTarget,
          } satisfies ComposerDraftState),
        );
      } else {
        window.sessionStorage.removeItem(key);
      }
    },
    [attachedFiles, currentSpaceId, replyTarget],
  );

  const scheduleComposerDraftPersist = useCallback(
    (nextDraft: string) => {
      if (composerDraftPersistTimeoutRef.current !== null) {
        window.clearTimeout(composerDraftPersistTimeoutRef.current);
      }

      composerDraftPersistTimeoutRef.current = window.setTimeout(() => {
        persistComposerDraftState(nextDraft);
        composerDraftPersistTimeoutRef.current = null;
      }, 120);
    },
    [persistComposerDraftState],
  );

  const syncComposerInputHeight = useCallback(() => {
    const node = composerInputRef.current;
    if (!node) return;

    node.style.height = "0px";
    const nextHeight = Math.min(
      Math.max(node.scrollHeight, MIN_COMPOSER_TEXTAREA_HEIGHT),
      MAX_COMPOSER_TEXTAREA_HEIGHT,
    );
    node.style.height = `${nextHeight}px`;
    node.style.overflowY =
      node.scrollHeight > MAX_COMPOSER_TEXTAREA_HEIGHT ? "auto" : "hidden";
  }, []);

  const setComposerDraft = useCallback(
    (nextDraft: string | ((current: string) => string)) => {
      const resolvedDraft =
        typeof nextDraft === "function"
          ? nextDraft(draftRef.current)
          : nextDraft;

      draftRef.current = resolvedDraft;
      if (
        composerInputRef.current &&
        composerInputRef.current.value !== resolvedDraft
      ) {
        composerInputRef.current.value = resolvedDraft;
      }
      syncComposerInputHeight();
      syncComposerDraftUiState(resolvedDraft);
      scheduleComposerDraftPersist(resolvedDraft);
    },
    [
      scheduleComposerDraftPersist,
      syncComposerDraftUiState,
      syncComposerInputHeight,
    ],
  );

  useEffect(() => {
    currentSpaceIdRef.current = currentSpaceId;
  }, [currentSpaceId]);

  const defaultAgentStorageKey = getDefaultAgentStorageKey(currentSpaceId);
  const recipientSetStorageKey = getRecipientSetStorageKey(currentSpaceId);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const stored = parseStoredRecipientHandles(
      window.localStorage.getItem(recipientSetStorageKey),
      window.localStorage.getItem(defaultAgentStorageKey),
    ).filter((handle) => handle !== AX_CONCIERGE_HANDLE);
    setDefaultAgentHandles(stored);
  }, [defaultAgentStorageKey, recipientSetStorageKey]);

  // Persistence is write-through from the mutation handlers rather than a
  // state-sync effect: an effect that removes keys whenever state is empty
  // races the restore pass on mount (StrictMode runs effects twice) and
  // wipes the stored selection before it can be read.
  const persistRecipientHandles = useCallback(
    (next: string[]) => {
      if (typeof window === "undefined") return;
      if (next.length > 0) {
        window.localStorage.setItem(
          recipientSetStorageKey,
          JSON.stringify(next),
        );
        // Keep the legacy single-agent key in sync for older clients.
        window.localStorage.setItem(defaultAgentStorageKey, next[0]);
        return;
      }
      window.localStorage.removeItem(recipientSetStorageKey);
      window.localStorage.removeItem(defaultAgentStorageKey);
    },
    [defaultAgentStorageKey, recipientSetStorageKey],
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(
      AGENT_RAIL_PINNED_KEY,
      agentRailPinned ? "true" : "false",
    );
  }, [agentRailPinned]);

  useEffect(() => {
    return () => {
      if (composerDraftPersistTimeoutRef.current !== null) {
        window.clearTimeout(composerDraftPersistTimeoutRef.current);
      }
    };
  }, []);

  const clearResponseWatchdog = useCallback(() => {
    if (responseWatchdogRef.current !== null) {
      window.clearTimeout(responseWatchdogRef.current);
      responseWatchdogRef.current = null;
    }
  }, []);

  const clearPendingResponse = useCallback(() => {
    setPendingResponse(null);
    setPendingResponsesBySourceId({});
  }, []);

  const armResponseWatchdog = useCallback(
    (delayMs = AX_REPLY_WAIT_MS) => {
      clearResponseWatchdog();
      responseWatchdogRef.current = window.setTimeout(() => {
        setIsResponding(false);
        clearPendingResponse();
        clearStreamingEntryState();
        void queryClient.invalidateQueries({
          queryKey: ["space-agent-transcript", currentSpaceId],
        });
        responseWatchdogRef.current = null;
      }, delayMs);
    },
    [
      clearPendingResponse,
      clearResponseWatchdog,
      clearStreamingEntryState,
      currentSpaceId,
      queryClient,
    ],
  );

  const updatePendingEntry = useCallback(
    (entryId: string, updater: (entry: ChatEntry) => ChatEntry | null) => {
      setPendingEntries((current) =>
        current.flatMap((entry) => {
          if (entry.id !== entryId) return [entry];
          const nextEntry = updater(entry);
          return nextEntry ? [nextEntry] : [];
        }),
      );
    },
    [],
  );

  const liveSpacesQuery = useQuery({
    queryKey: ["space-agent-spaces"],
    queryFn: getSpaceAgentSpaces,
    retry: false,
    staleTime: 30000,
  });

  const availableSpaces = useMemo(() => {
    const liveSpaces = liveSpacesQuery.data || [];
    if (liveSpaces.length > 0) return liveSpaces;

    const normalizedStoredSpaceId = normalizeStoredSpaceId(storedSpace?.id);
    if (normalizedStoredSpaceId) {
      return [
        {
          id: normalizedStoredSpaceId,
          name: storedSpace?.name || spaceName || "Workspace",
          visibility:
            typeof (storedSpace as { visibility?: string | null })
              .visibility === "string"
              ? (storedSpace as { visibility?: string | null }).visibility
              : "private",
          is_current: true,
        } satisfies SpaceAgentSpace,
      ];
    }

    const normalizedCurrentSpaceId = normalizeStoredSpaceId(currentSpaceId);
    return normalizedCurrentSpaceId
      ? ([
          {
            id: normalizedCurrentSpaceId,
            name: spaceName || "Workspace",
            visibility: "private",
            is_current: true,
          } satisfies SpaceAgentSpace,
        ] as SpaceAgentSpace[])
      : [];
  }, [currentSpaceId, liveSpacesQuery.data, spaceName, storedSpace]);

  const currentSpace =
    availableSpaces.find((space) => space.id === currentSpaceId) ??
    availableSpaces.find((space) => space.is_current) ??
    availableSpaces[0] ??
    ({
      id: normalizeStoredSpaceId(currentSpaceId) || "",
      name:
        liveSpacesQuery.isLoading || liveSpacesQuery.isFetching
          ? "Loading workspace…"
          : "No workspace selected",
      visibility: "private",
      is_current: false,
    } satisfies SpaceAgentSpace);

  const homeSpaceId = resolveProvisionedHomeSpaceId(availableSpaces);
  const hasSelectableSpaces = availableSpaces.length > 0;
  const workspaceSelectLabel = liveSpacesQuery.isError
    ? "Workspace unavailable"
    : liveSpacesQuery.isLoading || liveSpacesQuery.isFetching
      ? "Loading workspace…"
      : hasSelectableSpaces
        ? currentSpace.name
        : "No workspace selected";

  const currentSpaceVisibilityLabel = getSpaceVisibilityLabel(
    currentSpace,
    homeSpaceId,
  );
  const currentSpaceSelectWidthCh = getCompactSpaceSelectWidthCh(
    currentSpace,
    availableSpaces,
  );
  const currentSpaceVisibilityHint = getSpaceVisibilityHint(
    currentSpace,
    homeSpaceId,
  );
  const CurrentSpaceIcon = getSpaceTypeIcon(currentSpace, homeSpaceId);

  // Sync currentSpaceId when the spaces list first loads.
  // IMPORTANT: Do NOT call storage.setSpace here — that dispatches
  // "spaces:current-changed" which re-renders App.tsx, which re-renders
  // this shell, which recalculates storedSpaceId, triggering this effect
  // again in a rapid loop. Storage is written only during explicit user
  // actions (login via AuthCallback, space switch via OrganizationSwitcher).
  useEffect(() => {
    if (!availableSpaces.length) return;

    const preferredSpace = selectStoredOrCurrentSpace(
      availableSpaces,
      normalizeStoredSpaceId(storedSpaceId),
    );

    if (preferredSpace?.id && preferredSpace.id !== currentSpaceId) {
      setCurrentSpaceId(preferredSpace.id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- currentSpaceId
    // and storedSpaceId are intentionally omitted. currentSpaceId is only read
    // for comparison; storedSpaceId is stable after login. Including either
    // causes a rapid setState/storage dispatch loop.
  }, [availableSpaces]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const restoredState = readComposerDraftState(currentSpaceId);
    draftRef.current = restoredState.draft;
    if (composerInputRef.current) {
      composerInputRef.current.value = restoredState.draft;
    }
    syncComposerInputHeight();
    syncComposerDraftUiState(restoredState.draft);
    setAttachedFiles(restoredState.attachedFiles);
    setReplyTarget(restoredState.replyTarget);
  }, [currentSpaceId, syncComposerDraftUiState, syncComposerInputHeight]);

  useEffect(() => {
    persistComposerDraftState(draftRef.current, attachedFiles, replyTarget);
  }, [attachedFiles, persistComposerDraftState, replyTarget]);

  useEffect(() => {
    syncComposerDraftUiState(
      draftRef.current,
      composerAttachments.some((attachment) => !attachment.error),
    );
  }, [composerAttachments, syncComposerDraftUiState]);

  const liveTranscriptQuery = useQuery({
    queryKey: ["space-agent-transcript", currentSpaceId],
    queryFn: () => getSpaceAgentTranscript(currentSpaceId),
    enabled: Boolean(currentSpaceId),
    retry: false,
    staleTime: 5000,
    refetchInterval: false,
    refetchIntervalInBackground: false,
  });
  const liveConversationCardsQuery = useQuery({
    queryKey: ["space-agent-conversation-cards", currentSpaceId],
    queryFn: () => getSpaceAgentConversationCards(currentSpaceId),
    enabled: Boolean(currentSpaceId),
    retry: false,
    staleTime: 5000,
    refetchInterval: false,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    const messages = liveTranscriptQuery.data?.messages || [];
    const backendHasOlder =
      liveTranscriptQuery.data?.has_older ?? liveTranscriptQuery.data?.has_more;
    setHasOlderTranscript(
      typeof backendHasOlder === "boolean"
        ? backendHasOlder
        : messages.length >= 50,
    );
  }, [liveTranscriptQuery.data]);

  const getOlderTranscriptCursor = useCallback(() => {
    const explicitCursor =
      liveTranscriptQuery.data?.next_before ||
      liveTranscriptQuery.data?.oldest_cursor;
    if (explicitCursor) return explicitCursor;

    const candidates = [
      ...olderTranscriptMessages,
      ...(liveTranscriptQuery.data?.messages || []),
    ];
    const oldest = sortMessagesOldestFirst(candidates)[0];
    return oldest?.created_at || oldest?.id || null;
  }, [liveTranscriptQuery.data, olderTranscriptMessages]);

  const handleLoadOlderTranscript = useCallback(async () => {
    if (!currentSpaceId || isLoadingOlderTranscript || !hasOlderTranscript) {
      return;
    }
    const before = getOlderTranscriptCursor();
    if (!before) return;

    const scrollNode = scrollViewportRef.current;
    if (scrollNode) {
      olderTranscriptAnchorRef.current = {
        scrollHeight: scrollNode.scrollHeight,
        scrollTop: scrollNode.scrollTop,
      };
    }

    setIsLoadingOlderTranscript(true);
    try {
      const response = await getSpaceAgentTranscript(currentSpaceId, {
        before,
        limit: 50,
      });
      const messages = response.messages || response.posts || [];
      setOlderTranscriptMessages((current) => {
        const byId = new Map<string, SpaceAgentMessage>();
        for (const message of [...current, ...messages]) {
          byId.set(message.id, message);
        }
        return sortMessagesOldestFirst([...byId.values()]);
      });
      const backendHasOlder = response.has_older ?? response.has_more;
      setHasOlderTranscript(
        typeof backendHasOlder === "boolean"
          ? backendHasOlder
          : messages.length >= 50,
      );
    } finally {
      setIsLoadingOlderTranscript(false);
    }
  }, [
    currentSpaceId,
    getOlderTranscriptCursor,
    hasOlderTranscript,
    isLoadingOlderTranscript,
  ]);
  const mcpToolRegistryQuery = useMcpToolRegistry();

  const liveDirectoryQuery = useQuery({
    queryKey: ["space-agent-directory", currentSpaceId],
    queryFn: () => getSpaceAgentDirectory(currentSpaceId),
    enabled: Boolean(currentSpaceId),
    retry: false,
    staleTime: 30000,
  });

  const summarizeMessage = useMutation({
    mutationFn: (messageId: string) => summarizeSpaceAgentMessage(messageId),
  });

  const flushSummaryQueue = useCallback(async () => {
    if (isFlushingSummaryQueueRef.current || !currentSpaceId) return;

    isFlushingSummaryQueueRef.current = true;

    try {
      let invalidated = false;

      while (summaryQueueRef.current.length > 0) {
        const nextMessageId = summaryQueueRef.current[0];

        try {
          await summarizeMessage.mutateAsync(nextMessageId);
          invalidated = true;
        } catch (error) {
          requestedSummaryIdsRef.current.delete(nextMessageId);
          console.error("Failed to prefetch summary", error);
        } finally {
          summaryQueueRef.current.shift();
        }

        if (currentSpaceIdRef.current !== currentSpaceId) {
          summaryQueueRef.current = [];
          return;
        }
      }

      if (invalidated) {
        void queryClient.invalidateQueries({
          queryKey: ["space-agent-transcript", currentSpaceId],
        });
      }
    } finally {
      isFlushingSummaryQueueRef.current = false;
    }
  }, [currentSpaceId, queryClient, summarizeMessage]);

  const switchSpaceMutation = useMutation({
    mutationFn: (spaceId: string) => switchSpaceAgentSpace(spaceId),
  });
  const submitWidgetAction = useMutation({
    mutationFn: postSpaceAgentAction,
  });

  const flushOutboundQueue = useCallback(async () => {
    if (isFlushingQueueRef.current || !currentSpaceId) return;

    isFlushingQueueRef.current = true;
    setIsSending(true);

    try {
      while (outboundQueueRef.current.length > 0) {
        const nextMessage = outboundQueueRef.current[0];

        updatePendingEntry(nextMessage.optimisticEntryId, (entry) => ({
          ...entry,
          statusLabel: "Sending",
        }));

        try {
          const receipt = await sendSpaceAgentMessage(
            currentSpaceId,
            nextMessage.content,
            {
              parent_id: nextMessage.parentId,
              metadata: nextMessage.metadata,
              attachments: nextMessage.uploadedAttachments.length
                ? nextMessage.uploadedAttachments
                : undefined,
            },
          );

          if (currentSpaceIdRef.current !== currentSpaceId) {
            return;
          }

          outboundQueueRef.current.shift();
          setQueuedMessageCount(outboundQueueRef.current.length);

          const receiptEntry = mapSendReceiptToEntry(
            receipt,
            username,
            agentName,
            hiddenWidgets,
          );
          // Delivery-feedback gate: every send must surface a visible
          // outcome, including the "API answered but confirmed nothing"
          // case that previously read as success.
          const deliveryOutcome = describeDeliveryOutcome({ receipt });

          updatePendingEntry(nextMessage.optimisticEntryId, (entry) => ({
            ...receiptEntry,
            statusLabel: deliveryOutcome.label,
            toLabel: receiptEntry.toLabel || entry.toLabel || null,
            attachments: nextMessage.uploadedAttachments.map((a) => ({
              name: a.filename,
              id: a.id,
              url: a.url,
              contentType: a.content_type,
              sizeBytes: a.size_bytes,
              contextKey: a.context_key,
            })),
          }));

          const presenceKey = normalizeMentionHandle(nextMessage.targetHandle);
          const presenceEntry = presenceKey
            ? presenceEntries[presenceKey]
            : undefined;

          setPendingResponse((current) => {
            // Race-safety: `agent_processing` SSE events may arrive before
            // the send POST resolves. If SSE has already advanced pending
            // state past the pre-pickup defaults, preserve the richer phase
            // fields here instead of clobbering them back to "waiting".
            // The receipt path only owns the binding fields (sourceEntryId,
            // target*, presence) — phase state belongs to SSE.
            const presenceStatusFromPresence = presenceEntry
              ? getPresenceStatus(nextMessage.targetHandle || "")
              : null;
            return {
              sourceEntryId: receiptEntry.id,
              targetHandle: nextMessage.targetHandle,
              targetLabel: nextMessage.targetLabel,
              activeAgentLabel: current?.activeAgentLabel ?? null,
              statusLabel: current?.statusLabel ?? "waiting",
              toolName: current?.toolName ?? null,
              activity: current?.activity ?? null,
              toolCount: current?.toolCount ?? null,
              stepLabel: current?.stepLabel ?? null,
              commandLabels: current?.commandLabels ?? [],
              progress: current?.progress ?? null,
              reason: current?.reason ?? null,
              errorMessage: current?.errorMessage ?? null,
              retryAfterSeconds: current?.retryAfterSeconds ?? null,
              isKnownTarget:
                current?.isKnownTarget || nextMessage.isKnownTarget,
              hasPresenceSignal:
                current?.hasPresenceSignal || Boolean(presenceEntry),
              presenceStatus:
                presenceStatusFromPresence ?? current?.presenceStatus ?? null,
            };
          });
          setIsResponding(true);
          armResponseWatchdog(AX_REPLY_WAIT_MS);
          void queryClient.invalidateQueries({
            queryKey: ["space-agent-transcript", currentSpaceId],
          });
          void queryClient.invalidateQueries({
            queryKey: ["space-agent-conversation-cards", currentSpaceId],
          });
        } catch (error) {
          console.error(
            "Space Agent send failed; leaving message queued",
            error,
          );
          updatePendingEntry(nextMessage.optimisticEntryId, (entry) => ({
            ...entry,
            statusLabel: describeDeliveryOutcome({ error }).label,
          }));
          break;
        }
      }
    } finally {
      isFlushingQueueRef.current = false;
      setIsSending(false);
      setQueuedMessageCount(outboundQueueRef.current.length);
    }
  }, [
    agentName,
    armResponseWatchdog,
    currentSpaceId,
    getPresenceStatus,
    hiddenWidgets,
    presenceEntries,
    queryClient,
    updatePendingEntry,
    username,
  ]);

  const handleWidgetAction = useCallback(
    async ({
      messageId,
      cardId,
      actionId,
      choiceId,
      freeText,
    }: {
      messageId: string;
      cardId: string;
      actionId: string;
      choiceId?: string;
      freeText?: string | null;
    }) => {
      if (!currentSpaceId) return;

      const actionKey = [messageId, cardId, actionId, choiceId || ""].join(":");
      setActiveWidgetActionKey(actionKey);

      try {
        await submitWidgetAction.mutateAsync({
          space_id: currentSpaceId,
          message_id: messageId,
          card_id: cardId,
          action_id: actionId,
          ...(choiceId ? { choice_id: choiceId } : {}),
          ...(typeof freeText === "string" ? { free_text: freeText } : {}),
        });
        void queryClient.invalidateQueries({
          queryKey: ["space-agent-transcript", currentSpaceId],
        });
      } catch (error) {
        console.error("Failed to submit widget action", error);
      } finally {
        setActiveWidgetActionKey((current) =>
          current === actionKey ? null : current,
        );
      }
    },
    [currentSpaceId, queryClient, submitWidgetAction],
  );

  const liveEntries = useMemo(() => {
    const latestMessages =
      liveTranscriptQuery.data?.messages ||
      liveTranscriptQuery.data?.posts ||
      [];
    const byId = new Map<string, SpaceAgentMessage>();
    for (const message of [...olderTranscriptMessages, ...latestMessages]) {
      byId.set(message.id, message);
    }
    return sortMessagesOldestFirst([...byId.values()])
      .map((message) =>
        mapLiveMessageToEntry(message, agentName, undefined, hiddenWidgets),
      )
      .filter((entry) => !isHiddenToolTranscriptContent(entry.content));
  }, [
    agentName,
    hiddenWidgets,
    liveTranscriptQuery.data,
    olderTranscriptMessages,
  ]);
  const liveConversationCards =
    liveConversationCardsQuery.data?.cards ||
    liveConversationCardsQuery.data?.conversation_cards ||
    [];
  const conversationCardsByThread = useMemo(() => {
    const map = new Map<string, SpaceAgentConversationCard>();

    for (const card of liveConversationCards) {
      const threadId = getConversationCardThreadId(card);
      if (!threadId) continue;
      map.set(threadId, card);
      if (card.conversation_id && card.conversation_id !== threadId) {
        map.set(card.conversation_id, card);
      }
    }

    return map;
  }, [liveConversationCards]);

  useEffect(() => {
    const { candidateIds, knownIds } = collectAutoSummaryCandidateIds(
      liveEntries,
      knownTranscriptMessageIdsRef.current,
      requestedSummaryIdsRef.current,
    );

    knownTranscriptMessageIdsRef.current = knownIds;

    if (!candidateIds.length) return;

    for (const messageId of candidateIds) {
      requestedSummaryIdsRef.current.add(messageId);
      if (!summaryQueueRef.current.includes(messageId)) {
        summaryQueueRef.current.push(messageId);
      }
    }

    void flushSummaryQueue();
  }, [flushSummaryQueue, liveEntries]);

  const directoryAgents = useMemo(() => {
    const members = liveDirectoryQuery.data?.members || [];
    return members.map(mapMemberToAgentProfile);
  }, [liveDirectoryQuery.data]);

  const conciergeProfile = useMemo(() => {
    return (
      directoryAgents.find(
        (agent) => normalizeMentionHandle(agent.handle) === AX_CONCIERGE_HANDLE,
      ) ||
      buildFallbackAgentProfile(AX_CONCIERGE_HANDLE, {
        name: agentName,
        mentionLabel: "Waystation",
      })
    );
  }, [agentName, directoryAgents]);

  const defaultAgentProfile = useMemo(() => {
    if (!defaultAgentHandle) return null;

    return (
      directoryAgents.find(
        (agent) => normalizeMentionHandle(agent.handle) === defaultAgentHandle,
      ) || buildFallbackAgentProfile(defaultAgentHandle)
    );
  }, [defaultAgentHandle, directoryAgents]);

  const currentRouteProfile = defaultAgentProfile || conciergeProfile;
  const isRoutingToConcierge = !defaultAgentHandle;

  const currentRoutingLabel = isRoutingToConcierge
    ? conciergeProfile.name
    : `@${currentRouteProfile.handle}`;
  const pendingResponseDisplay = useMemo(
    () => getPendingResponseDisplay(pendingResponse),
    [pendingResponse],
  );

  const quickActionAgents = useMemo(() => {
    const rankedAgents = rankQuickActionAgents(
      directoryAgents,
      liveEntries,
      username,
    );
    const seen = new Set<string>();
    const curated: AgentProfile[] = [];

    const pushUnique = (agent?: AgentProfile | null) => {
      if (!agent) return;
      const normalized = normalizeMentionHandle(agent.handle) || agent.handle;
      if (seen.has(normalized)) return;
      seen.add(normalized);
      curated.push(agent);
    };

    pushUnique(currentRouteProfile);
    pushUnique(isRoutingToConcierge ? null : conciergeProfile);
    rankedAgents.forEach(pushUnique);

    return curated.slice(0, QUICK_ACTION_AGENT_LIMIT);
  }, [
    conciergeProfile,
    currentRouteProfile,
    directoryAgents,
    isRoutingToConcierge,
    liveEntries,
    username,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.__AX_MAIN_DEBUG__ = {
      debugVersion: "ax-shell-pending-route-v1",
      currentSpaceId: currentSpaceId || null,
      isResponding,
      isSending,
      queuedMessageCount,
      defaultAgentHandle,
      defaultAgentHandles,
      currentRoutingLabel,
      pendingResponse,
      pendingResponsesBySourceId,
      quickActionAgentHandles: quickActionAgents.map((agent) => agent.handle),
      routingTrace,
      streamingEntry,
      streamTiming,
      streamEventLog,
    };
  }, [
    currentSpaceId,
    currentRoutingLabel,
    defaultAgentHandle,
    defaultAgentHandles,
    isResponding,
    isSending,
    pendingResponse,
    pendingResponsesBySourceId,
    quickActionAgents,
    routingTrace,
    queuedMessageCount,
    streamingEntry,
    streamTiming,
    streamEventLog,
  ]);

  const getAgentProfile = (label?: string | null) => {
    if (!label) return null;
    const normalized = label.replace(/^@/, "").trim().toLowerCase();
    return (
      directoryAgents.find(
        (agent) =>
          agent.handle.toLowerCase() === normalized ||
          agent.id.toLowerCase() === normalized ||
          agent.name.toLowerCase() === normalized,
      ) || null
    );
  };

  // Pinned rail: selected agents always shown first, then the same smart
  // ranking the launcher quick actions use, concierge excluded (clearing
  // the set routes to the concierge and the placeholder reflects that).
  const railAgents = useMemo(() => {
    const selectedKeys = new Set(
      defaultAgentHandles.map((handle) => handle.toLowerCase()),
    );
    const ranked = quickActionAgents
      .map((agent) => normalizeMentionHandle(agent.handle) || agent.handle)
      .filter((handle) => handle !== AX_CONCIERGE_HANDLE);
    return buildRailSuggestionHandles({
      selected: defaultAgentHandles,
      ranked,
      limit: QUICK_ACTION_AGENT_LIMIT,
    }).map((handle) => {
      const profile =
        getAgentProfile(handle) || buildFallbackAgentProfile(handle);
      return {
        handle: profile.handle,
        name: profile.name,
        emoji: profile.emoji,
        availability: profile.availability,
        selected: selectedKeys.has(handle.toLowerCase()),
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultAgentHandles, directoryAgents, quickActionAgents]);

  const getSafeAgentLabel = (value?: string | null) => {
    if (!value) return agentName;

    const profile = getAgentProfile(value);
    if (profile) return profile.mentionLabel;

    const handle = extractSafeHandle(value);
    if (handle) return humanizeHandle(handle);

    if (value.toLowerCase().includes("space agent")) return agentName;
    return "Agent";
  };

  const getSafeUserLabel = (value?: string | null) => {
    const handle = extractSafeHandle(value);
    if (handle) return `@${handle}`;
    return "@user";
  };

  const getSafeIdentityLabel = (
    value?: string | null,
    role: ChatEntry["role"] | "participant" = "participant",
  ) => {
    if (role === "agent") return getSafeAgentLabel(value);
    if (role === "user") return getSafeUserLabel(value);

    const profile = getAgentProfile(value);
    if (profile) return profile.mentionLabel;

    const handle = extractSafeHandle(value);
    if (handle) {
      if (handle === normalizeMentionHandle(username)) return `@${handle}`;
      return humanizeHandle(handle);
    }

    if ((value || "").trim().match(/[\s,]/)) return "@user";
    return "Participant";
  };

  const getSafeRouteLabel = (value?: string | null) => {
    if (!value) return null;

    const profile = getAgentProfile(value);
    if (profile) return profile.mentionLabel;

    const handle = extractSafeHandle(value);
    if (handle === "ax") return "Waystation";
    if (handle) return `@${handle}`;

    if (value.toLowerCase().includes("space agent")) return agentName;
    return "thread";
  };

  const getRenderedEntryContent = useCallback(
    (entry: Pick<ChatEntry, "role" | "content" | "toLabel">) => {
      if (entry.role !== "user") return entry.content;
      return stripLeadingDirectedMention(entry.content, entry.toLabel);
    },
    [],
  );

  const getCompactPauseText = (
    label: string,
    tone: "ack" | "quiet" | null | undefined,
  ) => (tone === "ack" ? `${label} acknowledged` : "no reply");

  const renderCompactPauseBadge = (tone: "ack" | "quiet" | null | undefined) =>
    tone === "ack" ? (
      <span className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-emerald-300/30 bg-emerald-400/10 text-emerald-200">
        <Check className="h-2.5 w-2.5" strokeWidth={3} aria-hidden="true" />
      </span>
    ) : null;

  // Auto-clear streaming entry once its ID appears in liveEntries (transcript refresh).
  // This replaces the old pattern where handleFinalMessage would immediately clear
  // the streaming entry, causing progressive rendering to be lost.
  // Also clear isResponding as a safety net — the message has landed, we're done.
  // Guards:
  //   - holdingFinal: don't reset during the 1-second hold after streaming completes
  //   - isFinishing: don't reset while still actively receiving deltas — the backend
  //     creates the message record before streaming starts, so liveEntries may contain
  //     the streaming ID before the stream is complete
  useEffect(() => {
    if (!streamingEntry) return;
    if (!isFinishing) return; // Still actively streaming — don't reset yet
    if (holdingFinal) return; // Wait for the hold period to finish
    if (isCatchingUp) return; // Final message landed, but the visible text is still catching up
    if (hasLandedAgentReply(liveEntries, streamingEntry, streamTiming)) {
      streamBufferRef.current.reset();
      clearPendingResponse();
      setIsResponding(false);
    }
  }, [
    clearPendingResponse,
    holdingFinal,
    isCatchingUp,
    isFinishing,
    liveEntries,
    streamTiming,
    streamingEntry,
  ]);

  const hasLiveConversation =
    liveEntries.length > 0 ||
    pendingEntries.length > 0 ||
    Boolean(streamingEntry);
  const baseDisplayEntries = useMemo(() => {
    return buildDisplayEntries({
      liveEntries,
      pendingEntries,
      streamingEntry,
      streamTiming,
      hasLiveConversation,
      agentName,
    });
  }, [
    agentName,
    hasLiveConversation,
    liveEntries,
    pendingEntries,
    streamTiming,
    streamingEntry,
  ]);
  const displayEntries = baseDisplayEntries;
  const queuedBacklogCount = Math.max(
    0,
    queuedMessageCount - (isSending ? 1 : 0),
  );
  const entryLookup = useMemo(
    () => buildEntryLookup(displayEntries),
    [displayEntries],
  );
  const attachedNoReplyIndicatorsByParentId = useMemo(
    () => buildAttachedNoReplyIndicators(displayEntries, entryLookup),
    [displayEntries, entryLookup],
  );
  const pendingStreamingReplyByParentId = useMemo(() => {
    return buildPendingStreamingReplyByParentId(displayEntries);
  }, [displayEntries]);
  const threadIdByEntryId = useMemo(
    () => buildThreadIdByEntryId(displayEntries, entryLookup),
    [displayEntries, entryLookup],
  );
  const resolvePendingSourceEntryId = useCallback(
    (payload: Record<string, unknown>) => {
      const candidateIds = [
        payload.parent_id,
        payload.parent_message_id,
        payload.source_message_id,
        payload.message_id,
        payload.conversation_id,
      ];

      for (const candidate of candidateIds) {
        if (typeof candidate === "string" && entryLookup.has(candidate)) {
          return candidate;
        }
      }

      for (const candidate of candidateIds) {
        if (typeof candidate === "string" && candidate.trim()) {
          return candidate;
        }
      }

      return null;
    },
    [entryLookup],
  );
  const updatePendingResponseFromSignal = useCallback(
    ({
      payload,
      activeAgentLabel,
      statusLabel,
      toolName,
      activity,
      progress,
      structuredProgress,
      reason,
      errorMessage,
      retryAfterSeconds,
    }: {
      payload: Record<string, unknown>;
      activeAgentLabel: string | null;
      statusLabel: string;
      toolName?: string | null;
      activity?: string | null;
      progress?: PendingProgressPayload;
      structuredProgress?: PendingProgressShape | null;
      reason?: string | null;
      errorMessage?: string | null;
      retryAfterSeconds?: number | null;
    }) => {
      const buildNextPendingResponse = (
        base: PendingResponseState | null,
      ): PendingResponseState | null => {
        const sourceEntryId =
          resolvePendingSourceEntryId(payload) || base?.sourceEntryId || null;
        const sourceEntry = sourceEntryId
          ? entryLookup.get(sourceEntryId) || null
          : null;
        const rawTargetLabel =
          sourceEntry?.toLabel || base?.targetLabel || activeAgentLabel;
        const targetLabel =
          getSafeRouteLabel(rawTargetLabel) ||
          (typeof rawTargetLabel === "string" && rawTargetLabel.trim()
            ? rawTargetLabel.trim()
            : null) ||
          activeAgentLabel ||
          base?.targetLabel ||
          "";

        if (!base && !sourceEntry && !targetLabel && !activeAgentLabel) {
          return base;
        }

        const nextTargetHandle =
          normalizeMentionHandle(
            extractSafeHandle(
              sourceEntry?.toLabel || base?.targetHandle || targetLabel,
            ),
          ) ||
          base?.targetHandle ||
          null;
        const presenceEntry = nextTargetHandle
          ? presenceEntries[nextTargetHandle]
          : undefined;

        return {
          sourceEntryId,
          targetHandle: nextTargetHandle,
          targetLabel,
          activeAgentLabel: activeAgentLabel || base?.activeAgentLabel || null,
          statusLabel,
          toolName: toolName ?? base?.toolName ?? null,
          activity: activity ?? base?.activity ?? null,
          toolCount: progress?.toolCount ?? base?.toolCount ?? null,
          stepLabel: progress?.stepLabel ?? base?.stepLabel ?? null,
          commandLabels: progress?.commandLabels?.length
            ? progress.commandLabels
            : base?.commandLabels || [],
          progress: structuredProgress ?? base?.progress ?? null,
          reason: reason ?? base?.reason ?? null,
          errorMessage: errorMessage ?? base?.errorMessage ?? null,
          retryAfterSeconds:
            retryAfterSeconds ?? base?.retryAfterSeconds ?? null,
          isKnownTarget:
            base?.isKnownTarget ||
            Boolean(nextTargetHandle && getAgentProfile(nextTargetHandle)),
          hasPresenceSignal: Boolean(presenceEntry),
          presenceStatus: presenceEntry
            ? getPresenceStatus(nextTargetHandle || "")
            : base?.presenceStatus || null,
        };
      };

      const scopedSourceEntryId = resolvePendingSourceEntryId(payload);
      if (scopedSourceEntryId) {
        setPendingResponsesBySourceId((currentBySource) => {
          const nextScoped = buildNextPendingResponse(
            currentBySource[scopedSourceEntryId] || null,
          );
          if (!nextScoped) return currentBySource;
          return {
            ...currentBySource,
            [scopedSourceEntryId]: nextScoped,
          };
        });
      }

      setPendingResponse((current) => {
        const next = buildNextPendingResponse(current);
        if (
          current?.sourceEntryId &&
          next?.sourceEntryId &&
          current.sourceEntryId !== next.sourceEntryId
        ) {
          return current;
        }

        return next;
      });
    },
    [
      entryLookup,
      getAgentProfile,
      getPresenceStatus,
      getSafeRouteLabel,
      presenceEntries,
      resolvePendingSourceEntryId,
    ],
  );
  const getReplyPreview = useCallback(
    (entry: ChatEntry) => {
      if (!entry.parentId) return null;

      const parent = entryLookup.get(entry.parentId);
      const label =
        entry.replyToLabel || parent?.fromLabel || parent?.meta || null;
      const parentSummary = parent?.aiSummary
        ? sanitizeAiSummary(parent.aiSummary)
        : null;
      const content =
        entry.replyToContent ||
        parentSummary ||
        (parent ? getRenderedEntryContent(parent) : null) ||
        null;

      if (!label && !content) return null;

      return {
        label:
          parent?.role === "user"
            ? getSafeUserLabel(label)
            : getSafeIdentityLabel(label, parent?.role || "participant"),
        content: content || "Selected message",
      };
    },
    [entryLookup, getRenderedEntryContent],
  );

  const conversationGroups = useMemo(
    () => buildConversationGroups(displayEntries, threadIdByEntryId),
    [displayEntries, threadIdByEntryId],
  );
  const conversationCardAnchors = useMemo(
    () =>
      buildConversationCardAnchors(
        conversationGroups,
        conversationCardsByThread,
      ),
    [conversationCardsByThread, conversationGroups],
  );

  const orderedEntries = useMemo(
    () =>
      buildOrderedEntries({
        displayEntries,
        conversationGroups,
        conversationCardsByThread,
        threadIdByEntryId,
      }),
    [
      conversationCardsByThread,
      conversationGroups,
      displayEntries,
      threadIdByEntryId,
    ],
  );

  const hiddenEntryIds = useMemo(
    () =>
      buildHiddenEntryIds({
        cardsEnabled,
        conversationGroups,
        conversationCardsByThread,
        conversationCardAnchors,
      }),
    [
      cardsEnabled,
      conversationCardAnchors,
      conversationCardsByThread,
      conversationGroups,
    ],
  );
  const inlinePendingResponseEntryId = useMemo(() => {
    if (!isResponding || !pendingResponseDisplay) return null;
    if (
      pendingResponse?.sourceEntryId &&
      entryLookup.has(pendingResponse.sourceEntryId)
    ) {
      return pendingResponse.sourceEntryId;
    }
    return (
      [...orderedEntries]
        .reverse()
        .find((entry) => entry.role === "user" && !entry.isStreaming)?.id ||
      null
    );
  }, [
    entryLookup,
    isResponding,
    orderedEntries,
    pendingResponse,
    pendingResponseDisplay,
  ]);
  const getPendingResponseStateForEntry = useCallback(
    (entry: ChatEntry) => {
      const entryPendingResponse =
        pendingResponsesBySourceId[entry.id] ||
        (entry.id === inlinePendingResponseEntryId ? pendingResponse : null);
      return {
        entryPendingResponse,
        entryPendingResponseDisplay:
          getPendingResponseDisplay(entryPendingResponse),
        entryInlinePendingResponseEntryId: entryPendingResponse
          ? entry.id
          : null,
      };
    },
    [inlinePendingResponseEntryId, pendingResponse, pendingResponsesBySourceId],
  );

  const bottomSafeArea = getComposerBottomSafeAreaPx(composerHeight);
  const latestEntry = orderedEntries[orderedEntries.length - 1] || null;
  const latestEntryScrollKey = latestEntry
    ? `${orderedEntries.length}:${latestEntry.id}:${latestEntry.content.length}:${
        latestEntry.aiSummary?.length || 0
      }:${latestEntry.surfaceCount || 0}:${latestEntry.isStreaming ? "stream" : "final"}`
    : "empty";

  const autoFollowReservedBottomRef = useRef(bottomSafeArea);
  useEffect(() => {
    autoFollowReservedBottomRef.current = bottomSafeArea;
  }, [bottomSafeArea]);

  const captureReadingScrollAnchor = useCallback(() => {
    const node = scrollViewportRef.current;
    if (!node) {
      scrollAnchorSnapshotRef.current = null;
      return null;
    }

    const snapshot = captureVisibleScrollAnchor(node, {
      thresholdPx: 72,
      reservedBottomPx: autoFollowReservedBottomRef.current,
    });
    scrollAnchorSnapshotRef.current = snapshot;
    return snapshot;
  }, []);

  const restoreReadingScrollAnchor = useCallback(() => {
    const node = scrollViewportRef.current;
    if (!node) {
      scrollAnchorSnapshotRef.current = null;
      return false;
    }

    const restored = restoreVisibleScrollAnchor(
      node,
      scrollAnchorSnapshotRef.current,
    );
    captureReadingScrollAnchor();
    return restored;
  }, [captureReadingScrollAnchor]);

  const autoFollowRef = useRef(autoFollow);
  const bottomLockedRef = useRef(true);
  const [isOnBottomMessage, setIsOnBottomMessage] = useState(true);
  const showJumpToLatest = shouldShowJumpToLatest({
    isOnBottomMessage,
    hasUnreadLatest,
  });
  const setBottomLockedState = useCallback((next: boolean) => {
    bottomLockedRef.current = next;
    setIsOnBottomMessage((current) => (current === next ? current : next));
  }, []);
  const setAutoFollowState = useCallback((next: boolean) => {
    autoFollowRef.current = next;
    setAutoFollow((current) => (current === next ? current : next));
  }, []);
  useEffect(() => {
    autoFollowRef.current = autoFollow;
  }, [autoFollow]);

  const isUserSelectingText = useCallback(() => {
    if (typeof window === "undefined") return false;
    const selection = window.getSelection?.();
    return Boolean(
      selection && selection.type === "Range" && selection.toString(),
    );
  }, []);

  const scrollToLatest = useCallback(
    (behavior: ScrollBehavior = "auto") => {
      const node = scrollViewportRef.current;
      if (!node) return;
      scrollAnchorSnapshotRef.current = null;
      if (behavior === "auto") {
        node.scrollTop = node.scrollHeight;
        setBottomLockedState(true);
        setAutoFollowState(true);
        setHasUnreadLatest(false);
        return;
      }
      node.scrollTo({ top: node.scrollHeight, behavior });
      setBottomLockedState(true);
      setAutoFollowState(true);
      setHasUnreadLatest(false);
    },
    [setAutoFollowState, setBottomLockedState],
  );

  const queueScrollToLatest = useCallback(
    (behavior: ScrollBehavior = "auto") => {
      if (pendingAutoScrollFrameRef.current != null) {
        window.cancelAnimationFrame(pendingAutoScrollFrameRef.current);
      }
      pendingAutoScrollFrameRef.current = window.requestAnimationFrame(() => {
        pendingAutoScrollFrameRef.current = null;
        scrollToLatest(behavior);
      });
    },
    [scrollToLatest],
  );

  useEffect(
    () => () => {
      if (pendingAutoScrollFrameRef.current != null) {
        window.cancelAnimationFrame(pendingAutoScrollFrameRef.current);
      }
    },
    [],
  );

  const previousHelperExpandedRef = useRef(helperExpanded);
  useLayoutEffect(() => {
    const wasExpanded = previousHelperExpandedRef.current;
    previousHelperExpandedRef.current = helperExpanded;
    if (wasExpanded === helperExpanded) return undefined;

    const shouldStickToLatest = bottomLockedRef.current || wasExpanded;
    if (!shouldStickToLatest) return undefined;

    setAutoFollowState(true);
    let secondFrame = 0;
    const firstFrame = window.requestAnimationFrame(() => {
      scrollToLatest("auto");
      secondFrame = window.requestAnimationFrame(() => {
        scrollToLatest("auto");
      });
    });
    return () => {
      window.cancelAnimationFrame(firstFrame);
      if (secondFrame) window.cancelAnimationFrame(secondFrame);
    };
  }, [helperExpanded, scrollToLatest, setAutoFollowState]);

  useLayoutEffect(() => {
    if (olderTranscriptAnchorRef.current) {
      const node = scrollViewportRef.current;
      const anchor = olderTranscriptAnchorRef.current;
      olderTranscriptAnchorRef.current = null;
      if (node) {
        const heightDelta = node.scrollHeight - anchor.scrollHeight;
        if (heightDelta > 0) {
          node.scrollTop = anchor.scrollTop + heightDelta;
        }
      }
      captureReadingScrollAnchor();
      return;
    }

    const currentSnapshot = getLatestEntrySnapshot(orderedEntries);
    const latestChanged = hasNewLatestEntry(
      latestEntrySnapshotRef.current,
      currentSnapshot,
    );
    latestEntrySnapshotRef.current = currentSnapshot;

    if (!orderedEntries.length) {
      captureReadingScrollAnchor();
      return;
    }

    const shouldFollowLatest = shouldAutoSnapToBottom({
      isOnBottomMessage: bottomLockedRef.current,
      isUserSelectingText: isUserSelectingText(),
    });

    if (!shouldFollowLatest) {
      restoreReadingScrollAnchor();
      if (latestChanged) setHasUnreadLatest(true);
      return;
    }

    scrollAnchorSnapshotRef.current = null;
    setHasUnreadLatest(false);
    queueScrollToLatest(
      chooseBottomFollowScrollBehavior({
        hasAnchoredInitialTranscript: hasAnchoredInitialTranscriptRef.current,
        hasNewLatestEntry: latestChanged,
      }),
    );
    const isInitialAnchor = !hasAnchoredInitialTranscriptRef.current;
    hasAnchoredInitialTranscriptRef.current = true;

    if (isInitialAnchor) {
      const retries = [120, 360, 900].map((delay) =>
        window.setTimeout(() => {
          if (
            shouldAutoSnapToBottom({
              isOnBottomMessage: bottomLockedRef.current,
              isUserSelectingText: isUserSelectingText(),
            })
          ) {
            queueScrollToLatest("auto");
          }
        }, delay),
      );
      return () => retries.forEach(window.clearTimeout);
    }
  }, [
    latestEntryScrollKey,
    orderedEntries,
    composerHeight,
    queueScrollToLatest,
    captureReadingScrollAnchor,
    restoreReadingScrollAnchor,
    isUserSelectingText,
  ]);

  useEffect(() => {
    const node = composerRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;

    const update = () => {
      setComposerHeight((current) => {
        const next = Math.round(node.getBoundingClientRect().height);
        return Math.abs(current - next) < 1 ? current : next;
      });
    };

    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = activityStreamContentRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      if (
        shouldAutoSnapToBottom({
          isOnBottomMessage: bottomLockedRef.current,
          isUserSelectingText: isUserSelectingText(),
        })
      ) {
        scrollAnchorSnapshotRef.current = null;
        queueScrollToLatest("auto");
        return;
      }
      restoreReadingScrollAnchor();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [queueScrollToLatest, restoreReadingScrollAnchor, isUserSelectingText]);

  useEffect(() => {
    const node = scrollViewportRef.current;
    if (!node) return;

    let scrollFrame: number | null = null;
    const updateAutoFollow = () => {
      scrollFrame = null;
      const nextBottomLocked = isBottomLocked(node);
      const nextAutoFollow = isNearBottom(
        node,
        72,
        autoFollowReservedBottomRef.current,
      );
      setBottomLockedState(nextBottomLocked);
      setAutoFollowState(nextAutoFollow);
      if (nextAutoFollow) {
        scrollAnchorSnapshotRef.current = null;
        setHasUnreadLatest(false);
      } else {
        captureReadingScrollAnchor();
      }
    };
    const handleScroll = () => {
      setBottomLockedState(isBottomLocked(node));
      if (scrollFrame != null) return;
      scrollFrame = window.requestAnimationFrame(updateAutoFollow);
    };

    updateAutoFollow();
    node.addEventListener("scroll", handleScroll, { passive: true });
    return () => {
      node.removeEventListener("scroll", handleScroll);
      if (scrollFrame != null) window.cancelAnimationFrame(scrollFrame);
    };
  }, [captureReadingScrollAnchor, setAutoFollowState, setBottomLockedState]);

  useEffect(() => {
    setPendingEntries([]);
    setOlderTranscriptMessages([]);
    setHasOlderTranscript(false);
    setIsLoadingOlderTranscript(false);
    clearStreamingEntryState();
    setComposerDraft("");
    setAttachedFiles([]);
    clearPendingResponse();
    setIsResponding(false);
    setIsSending(false);
    setQueuedMessageCount(0);
    hasAnchoredInitialTranscriptRef.current = false;
    clearResponseWatchdog();
    outboundQueueRef.current = [];
    isFlushingQueueRef.current = false;
    summaryQueueRef.current = [];
    isFlushingSummaryQueueRef.current = false;
    knownTranscriptMessageIdsRef.current.clear();
    landedMessageIdsRef.current.clear();
    requestedSummaryIdsRef.current.clear();
    setActiveWidgetActionKey(null);
    setHelperExpanded(false);
    setReplyTarget(null);
    setAutoFollowState(true);
    setHasUnreadLatest(false);
    latestEntrySnapshotRef.current = null;
    scrollAnchorSnapshotRef.current = null;
    setBottomLockedState(true);
  }, [
    agentName,
    clearResponseWatchdog,
    clearPendingResponse,
    clearStreamingEntryState,
    currentSpaceId,
    setAutoFollowState,
    setBottomLockedState,
  ]);

  useEffect(() => {
    if (!normalizeStoredSpaceId(currentSpace?.id) || !storage.setSpace) return;
    storage.setSpace({
      id: currentSpace.id,
      name: currentSpace.name,
      slug: currentSpace.slug || undefined,
      visibility: currentSpace.visibility || undefined,
      description: currentSpace.description || undefined,
      member_count: currentSpace.member_count ?? undefined,
      is_member: currentSpace.is_member ?? undefined,
      is_current: currentSpace.is_current ?? true,
      is_personal: currentSpace.is_personal ?? undefined,
    });
  }, [currentSpace]);

  useEffect(() => {
    if (!currentSpaceId) return;

    // SSE needs a direct backend origin. In local development that means the
    // backend port; in deployed builds it should stay same-origin unless an
    // explicit API origin is configured.
    const sseBaseUrl = config.directApiUrl || window.location.origin;
    if (isRemoteLoopbackApiTarget(sseBaseUrl, window.location.origin)) {
      const errorMessage = `Realtime stream disabled: remote page ${window.location.origin} cannot use loopback backend ${sseBaseUrl}`;
      appendStreamEventLog("sse_error", errorMessage);
      streamBufferRef.current.setError(
        "Realtime stream unavailable due to invalid backend origin.",
      );
      return;
    }
    const params = new URLSearchParams({ space_id: currentSpaceId });
    if (authToken) params.set("token", authToken);

    const source = new EventSource(
      `${sseBaseUrl}/api/v1/sse/messages?${params.toString()}`,
      { withCredentials: false },
    );

    const refreshTranscript = () => {
      void queryClient.invalidateQueries({
        queryKey: ["space-agent-transcript", currentSpaceId],
      });
    };
    const refreshConversationCards = () => {
      void queryClient.invalidateQueries({
        queryKey: ["space-agent-conversation-cards", currentSpaceId],
      });
    };

    const handleFinalMessage = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        if (import.meta.env.DEV) {
          const msg = payload?.message || payload?.data || payload;
          console.debug(
            "[Shell SSE] final message:",
            event.type,
            msg?.id?.slice(0, 8),
            msg?.display_name,
            msg?.sender_type,
          );
        }
        appendStreamEventLog("message", event.data || "");
        const message = extractMessagePayload(payload);
        const eventSpaceId =
          message?.space_id ||
          (payload && typeof payload === "object"
            ? (payload as { space_id?: string; org_id?: string }).space_id ||
              (payload as { space_id?: string; org_id?: string }).org_id
            : null);
        if (eventSpaceId && eventSpaceId !== currentSpaceId) return;

        if (typeof message?.id === "string") {
          landedMessageIdsRef.current.add(message.id);
          setPendingEntries((current) =>
            current.filter((entry) => entry.id !== message.id),
          );
        }

        if (message?.sender_type && !isUserSenderType(message.sender_type)) {
          const activeStream = streamBufferRef.current.streamingEntry;
          const matchesActiveStream =
            !activeStream ||
            message?.id === activeStream.id ||
            (activeStream.parentId &&
              message?.parent_id === activeStream.parentId);

          // Fallback: also clear the pending bubble if the incoming reply's
          // author matches the current pendingResponse.targetHandle. This is
          // the "non-streaming bot finally replied" path (e.g. ping_bot via
          // ax listen) that the matchesActiveStream gate above misses when
          // streamBufferRef is holding a stale entry from a different agent.
          // Without this, the bubble can hang as "Waiting for @ping_bot"
          // even though ping_bot's reply already landed in the transcript.
          let matchesPendingTarget = false;
          const pending = pendingResponseRef.current;
          if (pending?.targetHandle) {
            const msgAny = message as unknown as Record<string, unknown>;
            const authorRaw = msgAny.author;
            const authorFromObj =
              authorRaw && typeof authorRaw === "object"
                ? (authorRaw as { name?: string }).name
                : null;
            const authorStr =
              authorFromObj ||
              (typeof msgAny.display_name === "string"
                ? (msgAny.display_name as string)
                : null) ||
              (typeof msgAny.sender_name === "string"
                ? (msgAny.sender_name as string)
                : null) ||
              "";
            const normalize = (v: string) =>
              v.replace(/^@/, "").trim().toLowerCase();
            if (
              authorStr &&
              normalize(authorStr) === normalize(pending.targetHandle)
            ) {
              matchesPendingTarget = true;
            }
          }

          if (matchesActiveStream || matchesPendingTarget) {
            streamBufferRef.current.onFinalMessage();
            clearResponseWatchdog();
            clearPendingResponse();
            setIsResponding(false);
          }
        }

        refreshTranscript();
      } catch {
        // ignore malformed SSE data
      }
    };

    const handleMessageUpdate = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        appendStreamEventLog("message_update", event.data || "");
        streamBufferRef.current.incrementEventCount();
        const data =
          payload && typeof payload === "object" && "data" in payload
            ? (payload as { data?: Record<string, unknown> }).data
            : payload;
        const field =
          data && typeof data === "object"
            ? (data as { field?: string }).field
            : undefined;

        // For content edits (e.g. sentinel streaming), patch the message
        // directly in the React Query cache instead of re-fetching the
        // entire transcript. This makes streaming feel instant.
        const messageId =
          (data as Record<string, unknown> | undefined)?.message_id ??
          (data as Record<string, unknown> | undefined)?.id;
        if (field === "content" && messageId && typeof messageId === "string") {
          const content = (data as Record<string, unknown>)?.content;
          if (typeof content === "string") {
            queryClient.setQueryData(
              ["space-agent-transcript", currentSpaceId],
              (old: unknown) => {
                if (!old || typeof old !== "object") return old;
                const transcript = old as {
                  messages?: Array<Record<string, unknown>>;
                };
                if (!Array.isArray(transcript.messages)) return old;
                return {
                  ...transcript,
                  messages: transcript.messages.map((msg) =>
                    msg.id === messageId ? { ...msg, content } : msg,
                  ),
                };
              },
            );
            return; // Skip full refresh — cache is already updated
          }
        }

        if (
          field === "ai_summary" ||
          field === "ui" ||
          field === "ui.cards" ||
          field === "ui.widget" ||
          field?.startsWith("ui.widget.") ||
          field === "metadata" ||
          field === "metadata.ui" ||
          field === "metadata.ui.widget" ||
          field?.startsWith("metadata.ui.widget.") ||
          field === "message_metadata" ||
          field === "message_metadata.ui" ||
          field === "message_metadata.ui.widget" ||
          field?.startsWith("message_metadata.ui.widget.") ||
          field === "cards" ||
          field === "content" ||
          field === "status" ||
          field === "lifecycle"
        ) {
          refreshTranscript();
        }
      } catch (error) {
        console.error("Failed to parse message_update event", error);
      }
    };

    const handleAgentSkipped = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(
          event.data || "{}",
        ) as NoReplySignalPayload & {
          space_id?: string | null;
          org_id?: string | null;
        };
        appendStreamEventLog("agent_skipped", event.data || "");
        const eventSpaceId = payload.space_id || payload.org_id;
        if (eventSpaceId && eventSpaceId !== currentSpaceId) return;

        const nextEntry = buildNoReplySignalEntry(payload, agentName);
        if (!nextEntry) return;

        clearResponseWatchdog();
        clearPendingResponse();
        setIsResponding(false);
        setPendingEntries((current) => {
          if (current.some((entry) => entry.id === nextEntry.id))
            return current;
          return [...current, nextEntry];
        });
      } catch (error) {
        console.error("Failed to parse agent_skipped event", error);
      }
    };

    const handleConversationCardUpdated = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        const card = extractConversationCardPayload(payload);
        if (!card) {
          refreshConversationCards();
          return;
        }

        queryClient.setQueryData<SpaceAgentConversationCardsResponse>(
          ["space-agent-conversation-cards", currentSpaceId],
          (current) => {
            const currentCards = current?.cards || [];
            const nextThreadId = getConversationCardThreadId(card);

            if (!nextThreadId) {
              return {
                cards: currentCards,
                count: current?.count || currentCards.length,
              };
            }

            const nextCards = currentCards.some(
              (existingCard) =>
                getConversationCardThreadId(existingCard) === nextThreadId,
            )
              ? currentCards.map((existingCard) =>
                  getConversationCardThreadId(existingCard) === nextThreadId
                    ? { ...existingCard, ...card }
                    : existingCard,
                )
              : [card, ...currentCards];

            return {
              cards: nextCards,
              count:
                typeof current?.count === "number"
                  ? Math.max(current.count, nextCards.length)
                  : nextCards.length,
            };
          },
        );
      } catch (error) {
        console.error("Failed to parse conversation_card_updated event", error);
        refreshConversationCards();
      }
    };

    const handleMessageLifecycle = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        appendStreamEventLog("message_lifecycle", event.data || "");
        streamBufferRef.current.incrementEventCount();
        const eventSpaceId = payload?.space_id || payload?.org_id;
        if (eventSpaceId && eventSpaceId !== currentSpaceId) return;

        const lifecycle =
          typeof payload?.lifecycle === "string"
            ? payload.lifecycle
            : typeof payload?.state === "string"
              ? payload.state
              : null;

        if (
          lifecycle === "forwarded" ||
          lifecycle === "claimed" ||
          lifecycle === "waiting"
        ) {
          const explicitAgentLabel =
            typeof payload?.agent_name === "string" && payload.agent_name.trim()
              ? payload.agent_name.trim()
              : typeof payload?.display_name === "string" &&
                  payload.display_name.trim()
                ? payload.display_name.trim()
                : null;
          updatePendingResponseFromSignal({
            payload,
            activeAgentLabel: explicitAgentLabel,
            statusLabel: lifecycle,
          });
          setIsResponding(true);
          armResponseWatchdog();
        } else if (lifecycle === "resolved" || lifecycle === "failed") {
          clearResponseWatchdog();
          streamBufferRef.current.onFinalMessage();
          clearPendingResponse();
          setIsResponding(false);
        }

        refreshTranscript();
      } catch (error) {
        console.error("Failed to parse message lifecycle event", error);
      }
    };

    const handleAgentProcessing = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        const sseDebug =
          typeof window !== "undefined" &&
          new URLSearchParams(window.location.search).has("debug_sse");
        if (import.meta.env.DEV || sseDebug) {
          const eventType =
            (event as MessageEvent & { type?: string }).type ||
            "agent_processing";
          console.log(
            `[SSE:${eventType}] status=${payload?.status} msg=${payload?.message_id?.slice(0, 8)} agent=${payload?.agent_name} activity=${payload?.activity || payload?.message || "—"} space_match=${!payload?.space_id || payload.space_id === currentSpaceId}`,
          );
        }
        appendStreamEventLog("agent_processing", event.data || "");
        const eventSpaceId = payload?.space_id || payload?.org_id;
        if (eventSpaceId && eventSpaceId !== currentSpaceId) return;

        if (isSuppressedProcessingPayload(payload)) {
          streamBufferRef.current.reset();
          clearResponseWatchdog();
          clearPendingResponse();
          setIsResponding(false);
          return;
        }

        const nextId =
          payload?.message_id ||
          payload?.dispatch_id ||
          payload?.request_id ||
          "streaming-space-agent";
        const explicitAgentLabel =
          typeof payload?.agent_name === "string" && payload.agent_name.trim()
            ? payload.agent_name.trim()
            : typeof payload?.display_name === "string" &&
                payload.display_name.trim()
              ? payload.display_name.trim()
              : null;
        const nextAgentName = explicitAgentLabel || agentName;
        const rawStatus =
          typeof payload?.status === "string"
            ? payload.status
            : typeof payload?.state === "string"
              ? payload.state
              : typeof payload?.phase === "string"
                ? payload.phase
                : "queued";
        // Gateway `agent_progress` carries status="tool_progress" which is
        // semantically a mid-tool-call update — map it into the tool_call
        // phase so the pending bubble shows "X: {activity}" instead of the
        // generic "is tool progress" fallback.
        const nextStatus =
          rawStatus.toLowerCase() === "tool_progress" ? "tool_call" : rawStatus;
        const nextToolName =
          typeof payload?.tool_name === "string"
            ? payload.tool_name
            : typeof payload?.tool === "string"
              ? payload.tool
              : null;
        const nextProgress = extractPendingProgressSignals(payload);
        const nextActivity = extractActivityText(payload);
        const nextStructuredProgress = extractStructuredProgress(payload);
        const nextReason = extractStringField(payload, "reason");
        const nextErrorMessage = extractStringField(payload, "error_message");
        const nextRetryAfterSeconds = extractRetryAfterSeconds(payload);

        // NOTE: we intentionally do NOT early-return on
        // `landedMessageIdsRef.current.has(nextId)` here. For `agent_processing`
        // and its sibling events (`agent_progress`, `tool_call_completed`),
        // `payload.message_id` is the id of the TRIGGERING user message —
        // which is always in `landedMessageIdsRef` by the time phases arrive
        // (the user message lands via SSE `message` handler milliseconds after
        // POST resolves, before any Gateway phase event). Dropping on that
        // match silenced every single phase update. The `isTerminalProcessingStatus`
        // branch below correctly handles the post-stream-completion case this
        // guard was originally trying to protect against.
        if (isTerminalProcessingStatus(nextStatus)) {
          streamBufferRef.current.onFinalMessage();
          clearResponseWatchdog();
          clearPendingResponse();
          setIsResponding(false);
        } else {
          updatePendingResponseFromSignal({
            payload,
            activeAgentLabel: explicitAgentLabel,
            statusLabel: nextStatus,
            toolName: nextToolName,
            activity: nextActivity,
            progress: nextProgress,
            structuredProgress: nextStructuredProgress,
            reason: nextReason,
            errorMessage: nextErrorMessage,
            retryAfterSeconds: nextRetryAfterSeconds,
          });
          streamBufferRef.current.onProcessing({
            id: nextId,
            agentName: nextAgentName,
            parentId:
              typeof payload?.parent_id === "string" ? payload.parent_id : null,
            statusLabel: nextStatus,
            toolName: nextToolName,
            activity: nextActivity,
            progress: nextStructuredProgress,
            reason: nextReason,
            errorMessage: nextErrorMessage,
            retryAfterSeconds: nextRetryAfterSeconds,
          });
          setIsResponding(true);
          // Arm the watchdog for active lifecycle states, including CLI
          // channel "working" receipts. Repeated tool_use updates should not
          // keep pushing the timeout out — if the backend never sends a final
          // message, the watchdog needs to fire and recover.
          if (isActiveProcessingStatus(nextStatus)) {
            armResponseWatchdog();
          }
        }
      } catch {
        // ignore malformed SSE data
      }
    };

    const handleMessageStream = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        appendStreamEventLog("message_stream", event.data || "");
        const nextId = payload?.message_id || "streaming-space-agent";
        const explicitAgentLabel =
          typeof payload?.agent_name === "string" && payload.agent_name.trim()
            ? payload.agent_name.trim()
            : typeof payload?.display_name === "string" &&
                payload.display_name.trim()
              ? payload.display_name.trim()
              : null;
        const nextAgentName = explicitAgentLabel || agentName;
        const nextType = payload?.type || payload?.phase || payload?.event;
        const nextDelta =
          typeof payload?.delta === "string"
            ? payload.delta
            : typeof payload?.content_delta === "string"
              ? payload.content_delta
              : typeof payload?.text === "string"
                ? payload.text
                : "";

        if (nextType === "start") {
          const nextProgress = extractPendingProgressSignals(payload);
          updatePendingResponseFromSignal({
            payload,
            activeAgentLabel: explicitAgentLabel,
            statusLabel: "streaming",
            progress: nextProgress,
          });
          streamBufferRef.current.onProcessing({
            id: nextId,
            agentName: nextAgentName,
            parentId:
              typeof payload?.parent_id === "string" ? payload.parent_id : null,
            statusLabel: "streaming",
          });
          setIsResponding(true);
          armResponseWatchdog();
          return;
        }

        if (nextType === "delta") {
          const nextProgress = extractPendingProgressSignals(payload);
          updatePendingResponseFromSignal({
            payload,
            activeAgentLabel: explicitAgentLabel,
            statusLabel: "streaming",
            progress: nextProgress,
          });
          streamBufferRef.current.onDelta({
            id: nextId,
            delta: nextDelta,
            agentName: nextAgentName,
            parentId:
              typeof payload?.parent_id === "string" ? payload.parent_id : null,
          });
          setIsResponding(true);
          armResponseWatchdog();
          return;
        }

        if (nextType === "done") {
          streamBufferRef.current.onFinalMessage();
          setIsResponding(true);
          armResponseWatchdog(AX_STREAM_FINISH_GRACE_MS);
        }
      } catch (error) {
        console.error("Failed to parse message_stream event", error);
      }
    };

    const handleMessageDelta = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data || "{}");
        appendStreamEventLog("message_delta", event.data || "");
        const eventSpaceId = payload?.space_id || payload?.org_id;
        if (eventSpaceId && eventSpaceId !== currentSpaceId) return;

        const nextId = payload?.message_id || "streaming-space-agent";
        const explicitAgentLabel =
          typeof payload?.agent_name === "string" && payload.agent_name.trim()
            ? payload.agent_name.trim()
            : typeof payload?.display_name === "string" &&
                payload.display_name.trim()
              ? payload.display_name.trim()
              : null;
        const nextAgentName = explicitAgentLabel || agentName;
        const nextDelta =
          typeof payload?.delta === "string"
            ? payload.delta
            : typeof payload?.content_delta === "string"
              ? payload.content_delta
              : typeof payload?.text === "string"
                ? payload.text
                : "";

        const nextProgress = extractPendingProgressSignals(payload);
        updatePendingResponseFromSignal({
          payload,
          activeAgentLabel: explicitAgentLabel,
          statusLabel: "streaming",
          progress: nextProgress,
        });
        streamBufferRef.current.onDelta({
          id: nextId,
          delta: nextDelta,
          agentName: nextAgentName,
          parentId:
            typeof payload?.parent_id === "string" ? payload.parent_id : null,
        });
        setIsResponding(true);
        armResponseWatchdog();
      } catch (error) {
        console.error("Failed to parse message_delta event", error);
      }
    };

    // Invalidate the per-space agent directory + global agents query when
    // the backend emits agent_roster_changed (created/updated/deleted/
    // approved). Without this, newly approved agents don't appear in the
    // composer or roster until a hard browser refresh.
    const handleAgentRosterChanged = () => {
      void queryClient.invalidateQueries({
        queryKey: ["space-agent-directory", currentSpaceId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["space-agent-directory"],
      });
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    };

    source.addEventListener("agent_processing", handleAgentProcessing);
    // Gateway-emitted variants carrying the same per-mention phase signal
    // but as distinct SSE event types. Route them through the same handler
    // so the pending bubble reflects them without duplicating logic.
    source.addEventListener("agent_progress", handleAgentProcessing);
    source.addEventListener("tool_call_completed", handleAgentProcessing);
    source.addEventListener("message_stream", handleMessageStream);
    source.addEventListener("message_delta", handleMessageDelta);
    source.addEventListener("message", handleFinalMessage);
    source.addEventListener("new_message", handleFinalMessage);
    source.addEventListener("message_created", handleFinalMessage);
    source.addEventListener("message_update", handleMessageUpdate);
    source.addEventListener("message_updated", handleMessageUpdate);
    source.addEventListener("agent_skipped", handleAgentSkipped);
    source.addEventListener("message.lifecycle", handleMessageLifecycle);
    source.addEventListener("message_lifecycle", handleMessageLifecycle);
    source.addEventListener(
      "conversation_card_updated",
      handleConversationCardUpdated,
    );
    source.addEventListener("agent_roster_changed", handleAgentRosterChanged);

    source.onerror = () => {
      appendStreamEventLog("sse_error", "EventSource connection error");
      streamBufferRef.current.setError("EventSource connection error");
      refreshTranscript();
    };

    return () => {
      clearResponseWatchdog();
      clearPendingResponse();
      streamBufferRef.current.reset();
      source.removeEventListener("agent_processing", handleAgentProcessing);
      source.removeEventListener("agent_progress", handleAgentProcessing);
      source.removeEventListener("tool_call_completed", handleAgentProcessing);
      source.removeEventListener("message_stream", handleMessageStream);
      source.removeEventListener("message_delta", handleMessageDelta);
      source.removeEventListener("message", handleFinalMessage);
      source.removeEventListener("new_message", handleFinalMessage);
      source.removeEventListener("message_created", handleFinalMessage);
      source.removeEventListener("message_update", handleMessageUpdate);
      source.removeEventListener("message_updated", handleMessageUpdate);
      source.removeEventListener("agent_skipped", handleAgentSkipped);
      source.removeEventListener("message.lifecycle", handleMessageLifecycle);
      source.removeEventListener("message_lifecycle", handleMessageLifecycle);
      source.removeEventListener(
        "conversation_card_updated",
        handleConversationCardUpdated,
      );
      source.removeEventListener(
        "agent_roster_changed",
        handleAgentRosterChanged,
      );
      source.close();
    };
  }, [
    agentName,
    armResponseWatchdog,
    appendStreamEventLog,
    authToken,
    clearPendingResponse,
    clearResponseWatchdog,
    currentSpaceId,
    queryClient,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const syncAuthToken = () => {
      setAuthToken(storage.getUserToken?.() ?? null);
    };

    window.addEventListener("auth:token-refreshed", syncAuthToken);
    window.addEventListener("visibility:resumed", syncAuthToken);
    window.addEventListener("network:online", syncAuthToken);

    return () => {
      window.removeEventListener("auth:token-refreshed", syncAuthToken);
      window.removeEventListener("visibility:resumed", syncAuthToken);
      window.removeEventListener("network:online", syncAuthToken);
    };
  }, []);

  const mentionResults =
    mentionQuery !== null
      ? [...directoryAgents]
          .filter((agent) => matchesMentionQuery(agent, mentionQuery))
          .sort((a, b) => {
            const scoreDelta =
              getMentionSearchRank(b, mentionQuery) -
              getMentionSearchRank(a, mentionQuery);
            if (scoreDelta !== 0) return scoreDelta;

            const availabilityDelta =
              availabilityPriority(a.availability) -
              availabilityPriority(b.availability);
            if (availabilityDelta !== 0) return availabilityDelta;

            return a.name.localeCompare(b.name);
          })
          .slice(0, 10)
      : [];

  const insertMention = useCallback(
    (handle: string) => {
      const normalizedHandle = normalizeMentionHandle(handle);
      if (!normalizedHandle) return;

      setComposerDraft((current) => {
        const replaced = current.replace(/(?:^|\s)@([\w-]*)$/, (match) => {
          const prefix = match.startsWith(" ") ? " " : "";
          return `${prefix}@${normalizedHandle} `;
        });

        if (replaced !== current) return replaced;
        if (!current.trim()) return `@${normalizedHandle} `;
        if (current.endsWith(" ") || current.endsWith("\n")) {
          return `${current}@${normalizedHandle} `;
        }

        return `${current} @${normalizedHandle} `;
      });

      setMentionQuery(null);

      // Focus and place cursor at end of the inserted mention
      window.requestAnimationFrame(() => {
        const el = composerInputRef.current;
        if (el) {
          el.focus();
          // Move cursor to end of the text
          const len = el.value.length;
          el.setSelectionRange(len, len);
        }
      });
    },
    [setComposerDraft, setMentionQuery],
  );

  const toggleMentionSelection = useCallback((handle: string) => {
    setMultiSelectedHandles((current) =>
      toggleHandleSelection(current, handle),
    );
  }, []);

  const applyMultiMentionSelection = useCallback(() => {
    if (multiSelectedHandles.length > 0) {
      setComposerDraft((draft) =>
        applyMentionSelections(draft, multiSelectedHandles),
      );
    }
    setMultiSelectedHandles([]);
    setMentionQuery(null);
    window.requestAnimationFrame(() => {
      const el = composerInputRef.current;
      if (el) {
        el.focus();
        const len = el.value.length;
        el.setSelectionRange(len, len);
      }
    });
  }, [multiSelectedHandles, setComposerDraft, setMentionQuery]);

  // Multi-select only lives while the mention dropdown is open.
  useEffect(() => {
    if (mentionQuery === null) {
      setMultiSelectedHandles((current) =>
        current.length === 0 ? current : [],
      );
    }
  }, [mentionQuery]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const handleComposeMentionAppend = (event: Event) => {
      const customEvent = event as CustomEvent<{ handle?: string | null }>;
      const handle = normalizeAgentHandle(customEvent.detail?.handle);
      if (!handle) return;

      const lastAppend = lastComposeMentionAppendRef.current;
      const now = Date.now();
      if (
        lastAppend &&
        lastAppend.handle === handle &&
        now - lastAppend.timestamp < 250
      ) {
        return;
      }

      lastComposeMentionAppendRef.current = {
        handle,
        timestamp: now,
      };
      insertMention(handle);
    };

    window.addEventListener(
      "ax:agent-mention-append",
      handleComposeMentionAppend as EventListener,
    );

    return () => {
      window.removeEventListener(
        "ax:agent-mention-append",
        handleComposeMentionAppend as EventListener,
      );
    };
  }, [insertMention]);

  const handleAgentBadgeMention = (label?: string | null) => {
    const profile = getAgentProfile(label);
    const handle = profile?.handle || extractSafeHandle(label);
    if (!handle) return;
    insertMention(handle);
  };

  const selectDefaultAgentRoute = useCallback(
    (
      handle: string,
      action: RoutingTraceItem["action"] = "quick_action_select",
    ) => {
      const normalized = normalizeMentionHandle(handle);
      if (!normalized) return;

      // Checkbox semantics: chips toggle agents in/out of the sticky
      // recipient set; choosing the concierge clears it.
      const next = toggleRecipientHandle(
        defaultAgentHandles,
        normalized,
        AX_CONCIERGE_HANDLE,
      );
      setDefaultAgentHandles(next);
      persistRecipientHandles(next);
      appendRoutingTrace({
        action,
        defaultAgentHandle: next[0] ?? null,
        replyTargetHandle: replyTarget?.targetHandle || null,
        explicitMentions: [],
        finalTargetHandle: next[0] ?? null,
      });
    },
    [
      appendRoutingTrace,
      defaultAgentHandles,
      persistRecipientHandles,
      replyTarget?.targetHandle,
    ],
  );

  const resetDefaultAgentRoute = useCallback(
    (action: RoutingTraceItem["action"] = "quick_action_reset") => {
      setDefaultAgentHandles([]);
      persistRecipientHandles([]);
      appendRoutingTrace({
        action,
        defaultAgentHandle: null,
        replyTargetHandle: replyTarget?.targetHandle || null,
        explicitMentions: [],
        finalTargetHandle: null,
      });
    },
    [appendRoutingTrace, persistRecipientHandles, replyTarget?.targetHandle],
  );

  const launchLauncherItem = useCallback(
    async (item: LauncherItem, options?: LauncherItemLaunchOptions) => {
      if (!currentSpaceId) return;

      const isAlreadyOpen =
        !options?.toolInput && activeMcpAppPanel?.launcherItemId === item.id;
      if (isAlreadyOpen) {
        setHelperExpanded(false);
        return;
      }

      const registry = mcpToolRegistryQuery.data || {};
      const registryEntry = item.toolCandidates
        .map((candidate) => registry[candidate])
        .find(Boolean);
      const toolInput = options?.toolInput ?? item.toolInput;

      if (!registryEntry) {
        console.error("Missing MCP launcher registry entry", {
          itemId: item.id,
          toolCandidates: item.toolCandidates,
        });
        return;
      }

      setHelperExpanded(false);
      setAutoFollow(false);
      setActiveLauncherItemId(item.id);
      const createdAt = new Date().toISOString();
      const panelId = `mcp-panel-${item.id}-${Date.now()}`;
      setActiveMcpAppPanel({
        id: panelId,
        source: "quick_action",
        launcherItemId: item.id,
        title: item.title,
        messageId: `launcher-${item.id}`,
        spaceId: currentSpaceId,
        timestamp: createdAt,
        widget: buildLauncherWidgetDescriptor({
          item,
          toolName: registryEntry.name,
          resourceUri: registryEntry.resourceUri,
          toolInput,
          lifecycle: "working",
        }),
      });

      try {
        const result = await proxyMcpToolCall(
          registryEntry.name,
          toolInput || {},
          {
            spaceId: currentSpaceId,
          },
        );
        setActiveMcpAppPanel((current) =>
          current?.id === panelId
            ? {
                ...current,
                widget: buildLauncherWidgetDescriptor({
                  item,
                  toolName: registryEntry.name,
                  resourceUri: registryEntry.resourceUri,
                  toolInput,
                  toolResult: coerceLauncherToolResult(result),
                  lifecycle: "complete",
                }),
              }
            : current,
        );
      } catch (error) {
        console.error("Failed to launch MCP app panel", {
          itemId: item.id,
          error,
        });
        const errorText =
          error instanceof Error && error.message.trim()
            ? error.message.trim()
            : `Could not open ${item.title}.`;
        setActiveMcpAppPanel((current) =>
          current?.id === panelId
            ? {
                ...current,
                widget: buildLauncherWidgetDescriptor({
                  item,
                  toolName: registryEntry.name,
                  resourceUri: registryEntry.resourceUri,
                  toolInput,
                  lifecycle: "error",
                  errorText,
                }),
              }
            : current,
        );
      } finally {
        setActiveLauncherItemId((current) =>
          current === item.id ? null : current,
        );
      }
    },
    [
      activeMcpAppPanel?.launcherItemId,
      currentSpaceId,
      mcpToolRegistryQuery.data,
    ],
  );

  const openWidgetPanelFromSurface = useCallback(
    ({ messageId, surface, timestamp }: AxWidgetPanelOpenInput) => {
      if (!currentSpaceId) return;
      const directHtmlPanel = buildDirectHtmlContextPanelWidget(surface.widget);
      setHelperExpanded(false);
      setAutoFollow(false);
      setActiveMcpAppPanel({
        id: `transcript-panel-${messageId}-${surface.id}`,
        source: "transcript",
        title: directHtmlPanel?.title || resolveMcpAppPanelTitle(surface),
        messageId,
        spaceId: currentSpaceId,
        timestamp: timestamp || null,
        widget: {
          ...(directHtmlPanel?.widget || surface.widget),
          display_mode: "fullscreen",
        },
      });
    },
    [currentSpaceId],
  );

  const openDirectHtmlPanelFromWidget = useCallback(
    ({
      title,
      html,
      key,
    }: {
      title: string;
      html: string;
      key?: string | null;
    }) => {
      const trimmedTitle = title.trim();
      setActiveMcpAppPanel((current) => {
        if (!current) return current;
        const nextTitle = trimmedTitle || current.title;
        return {
          ...current,
          id: `${current.id}-html-${key || Date.now()}`,
          title: nextTitle,
          widget: {
            ...current.widget,
            title: nextTitle,
            html,
            resource_uri: undefined,
            resource_url: undefined,
            tool_action: "render_html",
            lifecycle: "complete",
            display_mode: "fullscreen",
          },
        };
      });
    },
    [],
  );

  // Task 48ae545f — Share mirrors Reply. When a card Share button fires,
  // derive the context we need for the composer bar + send-time metadata,
  // focus the composer, and seed "@" so the existing mention autocomplete
  // (same list that drives normal sends) shows up without the user having
  // to type the trigger character.
  const handleCardReplyInit = useCallback(
    ({ messageId }: { messageId: string }) => {
      const entry = entryLookup.get(messageId);
      if (entry) startReply(entry, { summaryCard: false });
    },
    [entryLookup, startReply],
  );

  const handleForwardInit = useCallback(
    ({ cardId, card, messageId }: AxForwardInitInput) => {
      const payload =
        card.payload && typeof card.payload === "object"
          ? (card.payload as Record<string, unknown>)
          : {};
      const alertField =
        payload.alert && typeof payload.alert === "object"
          ? (payload.alert as Record<string, unknown>)
          : null;
      const title =
        (typeof payload.title === "string" && payload.title) ||
        (typeof payload.label === "string" && payload.label) ||
        (alertField &&
          typeof alertField.title === "string" &&
          alertField.title) ||
        "this";
      const summary =
        (typeof payload.summary === "string" && payload.summary) ||
        (alertField &&
          typeof alertField.summary === "string" &&
          alertField.summary) ||
        null;
      const resourceUri =
        (typeof payload.resource_uri === "string" && payload.resource_uri) ||
        (alertField &&
          typeof alertField.resource_uri === "string" &&
          alertField.resource_uri) ||
        null;
      const taskField =
        payload.task && typeof payload.task === "object"
          ? (payload.task as Record<string, unknown>)
          : null;
      const alertTaskField =
        alertField?.task && typeof alertField.task === "object"
          ? (alertField.task as Record<string, unknown>)
          : null;
      const contextKey = firstForwardString(
        payload.context_key,
        payload.key,
        payload.selected_key,
        alertField?.context_key,
      );
      const taskId = firstForwardString(
        payload.task_id,
        taskField?.id,
        alertField?.task_id,
        alertTaskField?.id,
        card.type === "task" ? payload.id : null,
      );
      const resourceType =
        firstForwardString(payload.resource_type, alertField?.resource_type) ||
        (card.type === "task" || taskId
          ? "task"
          : card.type === "context" || contextKey
            ? "context"
            : card.type);
      const resourceId = firstForwardString(
        payload.resource_id,
        alertField?.resource_id,
        taskId,
        contextKey,
      );
      const payloadAttachments = Array.isArray(payload.attachments)
        ? payload.attachments
        : [];
      const alertAttachments =
        alertField && Array.isArray(alertField.attachments)
          ? alertField.attachments
          : [];
      const references = Array.isArray(payload.references)
        ? payload.references
        : [];
      setForwardTarget({
        cardId,
        cardType: card.type,
        sourceMessageId: messageId || null,
        title,
        summary,
        resourceType,
        resourceId,
        resourceUri,
        taskId,
        contextKey,
        attachments: [...payloadAttachments, ...alertAttachments],
        references,
      });
      // Focus the composer only. Do NOT seed "@" — madtank feedback
      // 2026-04-16: auto-opening the mention dropdown on Share click was
      // jarring. The share bar signals the action; user types @
      // themselves when they're ready to pick a target.
      requestAnimationFrame(() => {
        const el = composerInputRef.current;
        if (!el) return;
        el.focus();
        const len = el.value.length;
        try {
          el.setSelectionRange(len, len);
        } catch {
          /* no-op */
        }
      });
    },
    [],
  );

  const uploadComposerAttachmentsForSend = useCallback(
    async (attachments: ComposerAttachment[]) => {
      if (!currentSpaceId) return [];

      const attachmentIds = new Set(attachments.map((a) => a.localId));
      setComposerAttachments((current) =>
        current.map((attachment) =>
          attachmentIds.has(attachment.localId) && !attachment.uploadId
            ? { ...attachment, uploading: true, error: undefined }
            : attachment,
        ),
      );

      const uploaded: ComposerAttachment[] = [];
      for (const attachment of attachments) {
        if (attachment.uploadId) {
          uploaded.push(attachment);
          continue;
        }

        if (!attachment.file) {
          setComposerAttachments((current) =>
            current.map((a) =>
              a.localId === attachment.localId
                ? {
                    ...a,
                    uploading: false,
                    error: "Attach again before sending",
                  }
                : a,
            ),
          );
          throw new Error(`Missing local file for ${attachment.filename}`);
        }

        try {
          const result = await uploadSpaceAgentFile(
            currentSpaceId,
            attachment.file,
          );
          const uploadedAt = new Date().toISOString();
          const contextKey = await storeUploadInContext(currentSpaceId, {
            id: result.id,
            filename: attachment.filename,
            content_type: result.content_type || attachment.contentType,
            size_bytes: result.size_bytes ?? attachment.sizeBytes,
            url: result.url,
            uploaded_at: uploadedAt,
            uploaded_by:
              attachment.uploadedBy || getPreferredUserLabel(username),
            upload_origin: attachment.source,
          });
          const nextAttachment: ComposerAttachment = {
            ...attachment,
            uploadId: result.id,
            url: result.url,
            contentType: result.content_type || attachment.contentType,
            sizeBytes: result.size_bytes ?? attachment.sizeBytes,
            uploading: false,
            error: undefined,
            contextKey: contextKey || undefined,
          };

          uploaded.push(nextAttachment);
          setComposerAttachments((current) =>
            current.map((a) =>
              a.localId === attachment.localId ? nextAttachment : a,
            ),
          );
        } catch (err) {
          console.error("Upload failed:", err);
          const uploadErrorMessage = getUploadErrorMessage(err);
          setComposerAttachments((current) =>
            current.map((a) =>
              a.localId === attachment.localId
                ? {
                    ...a,
                    uploading: false,
                    error: uploadErrorMessage,
                  }
                : a,
            ),
          );
          throw err;
        }
      }

      return uploaded;
    },
    [currentSpaceId, username],
  );

  const queueFiles = (
    files: FileList | null,
    source: "paste" | "picker" = "picker",
  ) => {
    if (!files?.length || !currentSpaceId) return;

    const uploaderLabel = getPreferredUserLabel(username);

    Array.from(files).forEach((file) => {
      const localId = `upload-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const stagedAt = new Date().toISOString();

      // Create preview URL for images
      const previewUrl = file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined;

      const attachment: ComposerAttachment = {
        localId,
        file,
        filename: file.name,
        contentType: file.type || "application/octet-stream",
        sizeBytes: file.size,
        previewUrl,
        uploading: false,
        source,
        stagedAt,
        uploadedBy: uploaderLabel,
      };

      setComposerAttachments((current) => [...current, attachment]);
      // Keep legacy list in sync for draft persistence
      setAttachedFiles((current) => [...current, file.name]);
    });
  };

  const handleSpaceChange = async (spaceId: string) => {
    const nextSpace = availableSpaces.find((space) => space.id === spaceId);
    setCurrentSpaceId(spaceId);
    setSettingsOpen(false);
    setActiveMcpAppPanel(null);

    if (nextSpace && storage.setSpace) {
      storage.setSpace({
        id: nextSpace.id,
        name: nextSpace.name,
        slug: nextSpace.slug || undefined,
        visibility: nextSpace.visibility || undefined,
        description: nextSpace.description || undefined,
        member_count: nextSpace.member_count ?? undefined,
        is_member: nextSpace.is_member ?? undefined,
        is_current: nextSpace.is_current ?? true,
        is_personal: nextSpace.is_personal ?? undefined,
      });
    }

    try {
      await switchSpaceMutation.mutateAsync(spaceId);
    } catch (error) {
      console.error(
        "Space switch API call failed; keeping local selection",
        error,
      );
    }

    void queryClient.invalidateQueries({
      queryKey: ["space-agent-transcript"],
    });
    void queryClient.invalidateQueries({
      queryKey: ["space-agent-conversation-cards"],
    });
    void queryClient.invalidateQueries({ queryKey: ["space-agent-directory"] });
    void queryClient.invalidateQueries({ queryKey: ["space-agent-spaces"] });
  };

  const submit = async (value = draftRef.current) => {
    const trimmed = value.trim();
    const hasPendingAttachments = composerAttachments.some(
      (attachment) => !attachment.error,
    );
    const hasUploadingAttachments = composerAttachments.some(
      (attachment) => attachment.uploading,
    );

    if (!trimmed && !hasPendingAttachments) return;
    if (hasUploadingAttachments) return;

    // Sending is an explicit request to resume following the live tail even if
    // the user was previously reading slightly above the bottom. Keep this
    // separate from passive near-bottom affordance state so passive resizes do
    // not fight the user, but the optimistic sent message still comes into view.
    setBottomLockedState(true);
    setAutoFollowState(true);
    setHelperExpanded(false);
    setMentionQuery(null);
    setSelectedMentionIndex(0);
    const attachmentsForSend = composerAttachments.filter((a) => !a.error);
    let readyAttachments: ComposerAttachment[];
    try {
      readyAttachments =
        attachmentsForSend.length > 0
          ? await uploadComposerAttachmentsForSend(attachmentsForSend)
          : [];
    } catch {
      return;
    }

    streamBufferRef.current.markSendStarted();
    setStreamEventLog([]);
    const optimisticAttachmentNames = readyAttachments.map((a) => a.filename);
    const optimisticAttachmentEntries: ChatEntryAttachment[] =
      readyAttachments.map((a) => ({
        name: a.filename,
        id: a.uploadId,
        url: a.url,
        contentType: a.contentType,
        sizeBytes: a.sizeBytes,
        contextKey: a.contextKey,
      }));
    const currentReplyTarget = replyTarget;
    const currentForwardTarget = forwardTarget;
    const forwardMetadata = buildForwardMessageMetadata(currentForwardTarget);
    const preferredUserLabel = getPreferredUserLabel(username);
    const optimisticEntryId = `pending-${Date.now()}`;
    const explicitMentions = extractExplicitMentionHandles(trimmed).map(
      (handle) => normalizeMentionHandle(handle) || handle,
    );
    const explicitRouteHandle =
      explicitMentions.length > 0
        ? explicitMentions[explicitMentions.length - 1]
        : null;
    const replyOverrideHandle = currentReplyTarget?.targetHandle || null;
    // Typed mentions are one-shot overrides: they route this message but do
    // not mutate the rail's sticky recipient set (the rail is the visible
    // routing state; silent mutation of it would feel like a switcher).
    const finalTargetHandle = explicitRouteHandle
      ? explicitRouteHandle
      : currentReplyTarget
        ? replyOverrideHandle
        : defaultAgentHandle;
    const shareIntro = buildShareIntro(currentForwardTarget);
    const baseContent = shareIntro
      ? [shareIntro, trimmed].filter(Boolean).join("\n\n")
      : trimmed;
    const isRailFanOut =
      !explicitRouteHandle &&
      !currentReplyTarget &&
      defaultAgentHandles.length > 1;
    const directedContent = isRailFanOut
      ? prependAgentMentionsIfNeeded(baseContent, defaultAgentHandles)
      : prependAgentMentionIfNeeded(baseContent, finalTargetHandle);
    const fanOutLabel = isRailFanOut
      ? defaultAgentHandles.map((handle) => `@${handle}`).join(" · ")
      : null;

    appendRoutingTrace({
      action: explicitRouteHandle ? "explicit_mention" : "send",
      defaultAgentHandle,
      replyTargetHandle: replyOverrideHandle,
      explicitMentions,
      finalTargetHandle: finalTargetHandle || null,
      contentPreview: directedContent,
    });

    const optimisticEntry: ChatEntry = {
      id: optimisticEntryId,
      role: "user",
      meta:
        preferredUserLabel === "You" ? "You" : `You · ${preferredUserLabel}`,
      content: directedContent,
      toLabel:
        fanOutLabel ??
        (finalTargetHandle && finalTargetHandle !== AX_CONCIERGE_HANDLE
          ? `@${finalTargetHandle}`
          : finalTargetHandle === AX_CONCIERGE_HANDLE
            ? agentName
            : null),
      attachments: optimisticAttachmentEntries,
      parentId: currentReplyTarget?.id || null,
      replyToLabel: currentReplyTarget?.label || null,
      replyToContent: currentReplyTarget?.content || null,
      conversationId:
        currentReplyTarget?.conversationId || currentReplyTarget?.id || null,
      statusLabel:
        isSending || outboundQueueRef.current.length > 0
          ? "Queued locally"
          : "Sending",
    };

    // Build uploaded attachment metadata for the API
    const uploadedAttachments: SpaceAgentAttachment[] = readyAttachments
      .filter((a) => a.uploadId && !a.error)
      .map((a) => ({
        id: a.uploadId!,
        filename: a.filename,
        content_type: a.contentType,
        size_bytes: a.sizeBytes,
        url: a.url,
        context_key: a.contextKey,
      }));

    // Clean up preview URLs
    readyAttachments.forEach((a) => {
      if (a.previewUrl) URL.revokeObjectURL(a.previewUrl);
    });

    setComposerDraft("");
    setAttachedFiles([]);
    setComposerAttachments([]);
    setReplyTarget(null);
    // Share context is one-shot, same as reply. Keep the structured source
    // reference on the queued send so the receiver can render it natively.
    setForwardTarget(null);
    setPendingEntries((current) => [...current, optimisticEntry]);
    outboundQueueRef.current.push({
      optimisticEntryId,
      content: directedContent,
      attachments: optimisticAttachmentNames,
      uploadedAttachments,
      metadata: forwardMetadata,
      parentId: currentReplyTarget?.id || null,
      targetHandle: finalTargetHandle || AX_CONCIERGE_HANDLE,
      targetLabel:
        fanOutLabel ??
        (finalTargetHandle && finalTargetHandle !== AX_CONCIERGE_HANDLE
          ? `@${finalTargetHandle}`
          : agentName),
      isKnownTarget:
        !finalTargetHandle ||
        finalTargetHandle === AX_CONCIERGE_HANDLE ||
        Boolean(getAgentProfile(finalTargetHandle)),
    });
    setQueuedMessageCount(outboundQueueRef.current.length);
    setActiveMcpAppPanel(null);
    void flushOutboundQueue();

    // Attachments render inline on the message. The context key is retained in
    // metadata for agents/recovery, but sending a file must not auto-open the
    // Context app and displace the conversation.
  };

  const toggleCard = (id: string) => {
    setExpandedCards((current) => ({ ...current, [id]: !current[id] }));
  };

  function startReply(entry: ChatEntry, options?: { summaryCard?: boolean }) {
    const cardModel = buildWorkCardModel({
      entry,
      expanded: false,
      threadIdByEntryId,
      conversationGroups,
      conversationCardsByThread,
      resolveIdentityLabel: getSafeIdentityLabel,
      formatCardMetaList,
      buildSignalEmojis,
    });
    const { threadRootId, latestCardEntry, isThreadSummaryCard, summary } =
      cardModel;
    const replyingToSummaryCard = Boolean(
      options?.summaryCard && isThreadSummaryCard,
    );
    const replySourceEntry = replyingToSummaryCard ? latestCardEntry : entry;
    const targetSource =
      replySourceEntry.fromLabel || replySourceEntry.meta || null;
    const targetProfile =
      !replyingToSummaryCard && replySourceEntry.role === "agent"
        ? getAgentProfile(targetSource)
        : null;
    const targetHandle = replyingToSummaryCard
      ? null
      : replySourceEntry.role === "agent"
        ? targetProfile?.handle || extractSafeHandle(targetSource)
        : extractSafeHandle(replySourceEntry.toLabel);

    setReplyTarget({
      id: replyingToSummaryCard ? threadRootId : entry.id,
      label: replyingToSummaryCard
        ? "Thread Summary"
        : getSafeIdentityLabel(
            replySourceEntry.fromLabel || replySourceEntry.meta || agentName,
            replySourceEntry.role,
          ),
      content:
        (replyingToSummaryCard ? summary : "") ||
        sanitizeAiSummary(replySourceEntry.aiSummary) ||
        getRenderedEntryContent(replySourceEntry) ||
        summary ||
        getRenderedEntryContent(entry) ||
        "",
      conversationId: threadRootId,
      targetHandle,
    });
  }

  function startShareMessage(
    entry: ChatEntry,
    options?: { summaryCard?: boolean },
  ) {
    const cardModel = buildWorkCardModel({
      entry,
      expanded: false,
      threadIdByEntryId,
      conversationGroups,
      conversationCardsByThread,
      resolveIdentityLabel: getSafeIdentityLabel,
      formatCardMetaList,
      buildSignalEmojis,
    });
    const { threadRootId, latestCardEntry, isThreadSummaryCard, summary } =
      cardModel;
    const sharingSummaryCard = Boolean(
      options?.summaryCard && isThreadSummaryCard,
    );
    const sourceEntry = sharingSummaryCard ? latestCardEntry : entry;
    const sourceLabel =
      sourceEntry.role === "user"
        ? "your message"
        : getSafeIdentityLabel(
            sourceEntry.fromLabel || sourceEntry.meta || agentName,
            sourceEntry.role,
          );
    const content =
      (sharingSummaryCard ? summary : "") ||
      sanitizeAiSummary(sourceEntry.aiSummary) ||
      getRenderedEntryContent(sourceEntry) ||
      summary ||
      getRenderedEntryContent(entry) ||
      "";

    setForwardTarget({
      cardId: `message:${sharingSummaryCard ? threadRootId : sourceEntry.id}`,
      cardType: "message",
      sourceMessageId: sharingSummaryCard ? threadRootId : sourceEntry.id,
      title: sharingSummaryCard ? "thread summary" : sourceLabel,
      summary: content,
      resourceType: "message",
      resourceId: sharingSummaryCard ? threadRootId : sourceEntry.id,
      resourceUri: "ui://messages/timeline",
    });
    requestAnimationFrame(() => {
      const el = composerInputRef.current;
      if (!el) return;
      el.focus();
    });
  }

  const ensureSummary = async (entry: ChatEntry) => {
    const threadId = getEntryThreadId(entry, threadIdByEntryId);
    const threadCard = threadId
      ? conversationCardsByThread.get(threadId)
      : null;
    if (
      entry.role !== "agent" ||
      entry.aiSummary ||
      getConversationCardSummary(threadCard) ||
      !entry.id
    ) {
      return;
    }
    try {
      await summarizeMessage.mutateAsync(entry.id);
      void queryClient.invalidateQueries({
        queryKey: ["space-agent-transcript", currentSpaceId],
      });
    } catch (error) {
      console.error("Failed to summarize message on demand", error);
    }
  };

  const copyCard = async (cardId: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedCardId(cardId);
      window.setTimeout(() => {
        setCopiedCardId((current) => (current === cardId ? null : current));
      }, 1800);
    } catch (error) {
      console.error("Failed to copy summary card", error);
    }
  };

  // While streaming is active, suppress cards for the streaming message's thread.
  // Cards only appear after the streaming bubble is dismissed.
  const streamingThreadId = getStreamingThreadId(
    streamingEntry,
    threadIdByEntryId,
  );

  const shouldRenderEntryAsCard = (entry: ChatEntry) => {
    return shouldRenderTranscriptEntryAsCard({
      entry,
      cardsEnabled,
      streamingThreadId,
      threadIdByEntryId,
      conversationCardsByThread,
      conversationCardAnchors,
      conversationGroups,
    });
  };

  const getMarkdownParts = (content: string) => {
    // Safety strip: remove ax_intel JSON that may have leaked through earlier layers
    const cleaned = content.replace(
      /\s*\{["']?ax_intel["']?\s*:[\s\S]*\}\s*$/,
      "",
    );
    return extractMediaFromContent(cleaned);
  };

  const renderMarkdown = (content: string, muted = false) => {
    const { textContent } = getMarkdownParts(content);
    if (!textContent) return null;

    return (
      <div
        className={cn(
          isDarkMode
            ? "prose prose-invert min-w-0 max-w-none overflow-x-hidden break-words text-[15px] leading-7 text-white [overflow-wrap:anywhere]"
            : "prose prose-slate min-w-0 max-w-none overflow-x-hidden break-words text-[15px] leading-7 text-slate-900 [overflow-wrap:anywhere]",
          muted && "prose-p:my-0",
          isDarkMode
            ? "[&_p]:my-3 [&_ul]:my-3 [&_ol]:my-3 [&_li]:my-1 [&_pre]:max-w-full [&_pre]:overflow-x-hidden [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_pre]:rounded-2xl [&_pre]:bg-slate-950/70 [&_pre]:p-4 [&_pre]:[overflow-wrap:anywhere] [&_code]:rounded [&_code]:bg-white/10 [&_code]:px-1 [&_code]:py-0.5 [&_code]:break-words [&_code]:[overflow-wrap:anywhere]"
            : "[&_p]:my-3 [&_ul]:my-3 [&_ol]:my-3 [&_li]:my-1 [&_pre]:max-w-full [&_pre]:overflow-x-hidden [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_pre]:rounded-2xl [&_pre]:bg-slate-950 [&_pre]:p-4 [&_pre]:text-slate-100 [&_pre]:[overflow-wrap:anywhere] [&_code]:rounded [&_code]:bg-slate-200 [&_code]:px-1 [&_code]:py-0.5 [&_code]:break-words [&_code]:[overflow-wrap:anywhere]",
          "[&_a]:break-all [&_table]:w-full [&_table]:max-w-full [&_table]:border-collapse [&_table]:text-sm [&_td]:break-words [&_td]:[overflow-wrap:anywhere] [&_th]:break-words [&_th]:[overflow-wrap:anywhere]",
          isDarkMode
            ? "[&_th]:border [&_th]:border-white/20 [&_th]:bg-white/[0.06] [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_td]:border [&_td]:border-white/10 [&_td]:px-3 [&_td]:py-2 [&_thead]:bg-white/[0.03]"
            : "[&_th]:border [&_th]:border-slate-300 [&_th]:bg-slate-100 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold [&_td]:border [&_td]:border-slate-200 [&_td]:px-3 [&_td]:py-2 [&_thead]:bg-slate-50",
          "[&_table]:overflow-x-auto",
        )}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ children, href, ...props }) => (
              <ExternalMarkdownLink
                href={href}
                className={cn(
                  "hover:underline",
                  isDarkMode ? "text-cyan-200" : "text-cyan-700",
                )}
                {...props}
              >
                {children}
              </ExternalMarkdownLink>
            ),
          }}
        >
          {textContent}
        </ReactMarkdown>
      </div>
    );
  };

  const renderMarkdownMediaSidecar = (content: string) => {
    const { images, audio, video, youtube, files } = getMarkdownParts(content);
    return renderMediaBlocks(images, audio, video, youtube, files);
  };

  return (
    <div
      data-theme={isDarkMode ? "dark" : "light"}
      className={cn(
        AX_SHELL_MOBILE_VIEWPORT_CLASS,
        "transition-colors duration-200",
        isDarkMode ? "bg-[#040914] text-white" : "bg-slate-50 text-slate-950",
      )}
    >
      <div
        className={cn(
          "absolute inset-0",
          isDarkMode
            ? "bg-[radial-gradient(circle_at_20%_18%,rgba(59,130,246,0.24),transparent_24%),radial-gradient(circle_at_84%_20%,rgba(99,102,241,0.22),transparent_24%),radial-gradient(circle_at_72%_72%,rgba(251,191,36,0.15),transparent_26%),radial-gradient(circle_at_16%_82%,rgba(56,189,248,0.14),transparent_24%),linear-gradient(180deg,#040914_0%,#091428_48%,#0b1020_100%)]"
            : "bg-[radial-gradient(circle_at_20%_18%,rgba(59,130,246,0.12),transparent_24%),radial-gradient(circle_at_84%_20%,rgba(99,102,241,0.12),transparent_24%),radial-gradient(circle_at_72%_72%,rgba(251,191,36,0.10),transparent_26%),radial-gradient(circle_at_16%_82%,rgba(56,189,248,0.10),transparent_24%),linear-gradient(180deg,#f8fbff_0%,#eef5ff_48%,#e8f0fb_100%)]",
        )}
      />
      <div
        className={cn(
          "absolute inset-0 [background-size:34px_34px]",
          isDarkMode
            ? "opacity-40 [background-image:radial-gradient(rgba(255,255,255,0.35)_0.7px,transparent_0.7px)]"
            : "opacity-30 [background-image:radial-gradient(rgba(15,23,42,0.12)_0.7px,transparent_0.7px)]",
        )}
      />
      <div className="absolute left-[10%] top-[12%] h-72 w-72 rounded-full bg-cyan-400/10 blur-3xl" />
      <div className="absolute bottom-[8%] right-[12%] h-80 w-80 rounded-full bg-orange-400/10 blur-3xl" />

      <div className="relative z-10 flex h-full min-h-0 flex-col">
        <div
          data-testid="ax-shell-header"
          className={cn(
            AX_SHELL_HEADER_SAFE_AREA_CLASS,
            "relative z-[60] shrink-0 px-4 pb-4 sm:z-40 sm:px-6 lg:px-8",
          )}
        >
          {/* Canonical new-UI header.
              Brand/polish changes should land here first.
              The legacy shell in App.tsx is fallback-only and should stay in sync. */}
          <div
            className={cn(
              AX_SHELL_HEADER_ROW_CLASS,
              isDarkMode
                ? "border-cyan-300/20 bg-slate-950/65"
                : "border-slate-200/80 bg-white/90 shadow-sm",
            )}
          >
            <div className={AX_SHELL_HEADER_BRAND_CLASS}>
              <Logo
                size="sm"
                showText
                className="border-white/10 bg-white/[0.04] shadow-[0_18px_42px_-28px_rgba(14,165,233,0.55)]"
              />
            </div>

            <div className={AX_SHELL_HEADER_ACTIONS_CLASS}>
              <label
                className={cn(
                  AX_SHELL_SPACE_SWITCHER_CLASS,
                  isDarkMode
                    ? "border-white/10 bg-white/[0.04] text-slate-200"
                    : "border-slate-200 bg-slate-50 text-slate-700",
                )}
                title={getSpaceIdentityTitle(currentSpace, homeSpaceId)}
              >
                <CurrentSpaceIcon
                  className={cn(
                    "pointer-events-none h-4 w-4 shrink-0",
                    isDarkMode ? "text-cyan-100" : "text-slate-500",
                  )}
                />
                <span
                  className={cn(
                    "pointer-events-none hidden shrink-0 text-[11px] font-semibold uppercase tracking-wide sm:inline",
                    isDarkMode ? "text-cyan-100/80" : "text-slate-500",
                  )}
                >
                  {currentSpaceVisibilityLabel}
                </span>
                <span
                  aria-hidden="true"
                  className={cn(
                    AX_SHELL_SPACE_SWITCHER_LABEL_CLASS,
                    isDarkMode ? "text-white" : "text-slate-900",
                  )}
                  style={{ width: `${currentSpaceSelectWidthCh}ch` }}
                >
                  {getSpaceOptionLabel(currentSpace, availableSpaces)}
                </span>
                <select
                  value={hasSelectableSpaces ? currentSpaceId : ""}
                  onChange={(event) => {
                    if (!event.target.value) return;
                    void handleSpaceChange(event.target.value);
                  }}
                  disabled={!hasSelectableSpaces || liveSpacesQuery.isError}
                  className={cn(
                    SPACE_SWITCHER_NATIVE_SELECT_HIT_TARGET_CLASS,
                    "appearance-none",
                  )}
                  aria-label="Current workspace"
                  title={getSpaceIdentityTitle(currentSpace, homeSpaceId)}
                >
                  {!hasSelectableSpaces ? (
                    <option value="">{workspaceSelectLabel}</option>
                  ) : null}
                  {availableSpaces.map((space) => (
                    <option
                      key={space.id}
                      value={space.id}
                      title={getSpaceIdentityTitle(space, homeSpaceId)}
                      className={
                        isDarkMode ? "bg-slate-950" : "bg-white text-slate-900"
                      }
                    >
                      {getSpaceOptionLabel(space, availableSpaces)}
                    </option>
                  ))}
                </select>
                <ChevronDown
                  aria-hidden="true"
                  className={cn(
                    "pointer-events-none h-4 w-4 shrink-0",
                    isDarkMode ? "text-cyan-100/80" : "text-slate-500",
                  )}
                />
              </label>
              <div className={AX_SHELL_QUICK_MENU_WRAPPER_CLASS}>
                <AxQuickMenu
                  username={username}
                  spaceName={currentSpace.name}
                  agentCount={directoryAgents.length}
                  cardsEnabled={cardsEnabled}
                  onToggleCards={toggleCards}
                  onOpenSettings={() => setSettingsOpen(true)}
                  onLogout={authToken && onLogout ? onLogout : undefined}
                  showSummaryCardsToggle={SUMMARY_CARDS_FEATURE_ENABLED}
                />
              </div>
            </div>
          </div>
        </div>

        {settingsOpen ? (
          <Suspense fallback={null}>
            <LazyAxSettingsDialog
              open={settingsOpen}
              onOpenChange={setSettingsOpen}
              currentSpaceId={currentSpace.id}
              currentSpaceName={currentSpace.name}
              username={username}
              onLogout={authToken && onLogout ? onLogout : undefined}
              hiddenWidgets={hiddenWidgets}
              onToggleWidget={toggleWidgetVisibility}
            />
          </Suspense>
        ) : null}

        <div
          ref={scrollViewportRef}
          data-scroll-viewport="true"
          className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain px-4 sm:px-6 lg:px-8"
          onPointerDown={
            helperExpanded ? () => setHelperExpanded(false) : undefined
          }
          style={{
            overflowAnchor: "none",
            paddingBottom: bottomSafeArea,
            scrollPaddingBottom: bottomSafeArea,
          }}
        >
          <div
            ref={activityStreamContentRef}
            data-activity-stream-content="true"
            className="mx-auto flex w-full min-w-0 max-w-5xl flex-col gap-4 overflow-x-hidden"
          >
            <section
              className={cn(
                "rounded-[26px] border px-5 py-4 backdrop-blur-xl",
                isDarkMode
                  ? "border-white/10 bg-slate-950/35"
                  : "border-slate-200/80 bg-white/88 shadow-sm",
              )}
            >
              <div
                className={cn(
                  "text-[11px] uppercase tracking-[0.22em]",
                  isDarkMode ? "text-cyan-100/80" : "text-cyan-700",
                )}
              >
                Your workspace
              </div>
              <p
                className={cn(
                  "mt-2 max-w-3xl text-sm leading-6",
                  isDarkMode ? "text-slate-300" : "text-slate-700",
                )}
              >
                Create a task, connect an agent, or start a conversation. Open the launcher below to explore your workspace.
              </p>
              <p
                className={cn(
                  "mt-2 text-sm leading-6",
                  isDarkMode ? "text-slate-400" : "text-slate-600",
                )}
              >
                <span
                  className={cn(
                    "font-medium",
                    isDarkMode ? "text-slate-200" : "text-slate-900",
                  )}
                >
                  {currentSpaceVisibilityLabel}:
                </span>{" "}
                {currentSpaceVisibilityHint}
              </p>
            </section>

            {liveTranscriptQuery.isLoading && !hasLiveConversation ? (
              <section
                className={cn(
                  "rounded-[26px] border px-5 py-4 text-sm backdrop-blur-xl",
                  isDarkMode
                    ? "border-white/10 bg-slate-950/35 text-slate-300"
                    : "border-slate-200/80 bg-white/88 text-slate-700 shadow-sm",
                )}
              >
                Loading conversation history...
              </section>
            ) : null}

            {liveTranscriptQuery.isError && !hasLiveConversation ? (
              <section className="rounded-[26px] border border-amber-300/20 bg-amber-400/10 px-5 py-4 text-sm text-amber-100 backdrop-blur-xl">
                Conversation history could not be loaded. Check the server connection and reload to retry.
              </section>
            ) : null}

            <section
              className={cn(
                "min-w-0 max-w-full overflow-x-hidden rounded-[30px] border p-5 backdrop-blur-xl",
                isDarkMode
                  ? "border-white/10 bg-slate-950/35"
                  : "border-slate-200/80 bg-white/88 shadow-sm",
              )}
            >
              <div className="space-y-5">
                {hasOlderTranscript ? (
                  <div className="flex justify-center">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={handleLoadOlderTranscript}
                      disabled={isLoadingOlderTranscript}
                      className={cn(
                        "rounded-full border px-4 py-2 text-xs font-semibold",
                        isDarkMode
                          ? "border-cyan-300/30 bg-cyan-400/10 text-cyan-100 hover:bg-cyan-400/20"
                          : "border-cyan-200 bg-cyan-50 text-cyan-800 hover:bg-cyan-100",
                      )}
                    >
                      {isLoadingOlderTranscript ? (
                        <>
                          <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                          Loading older messages
                        </>
                      ) : (
                        <>
                          <ArrowUp className="mr-2 h-3.5 w-3.5" />
                          Load older messages
                        </>
                      )}
                    </Button>
                  </div>
                ) : null}
                {!orderedEntries.length && !liveTranscriptQuery.isLoading && !liveTranscriptQuery.isError ? (
                  <div className="py-8 text-center">
                    <h2 className="text-lg font-semibold">Make this space your own</h2>
                    <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">Your conversations and agent activity will appear here. Start with a task or connect your first agent.</p>
                    <div className="mt-5 flex flex-wrap justify-center gap-3">
                      <Button type="button" variant="outline" onClick={() => setHelperExpanded(true)}>Open launcher</Button>
                      <a href="/auth.md" target="_blank" rel="noreferrer" className="inline-flex items-center rounded-md border px-4 py-2 text-sm font-medium">Connect an agent</a>
                    </div>
                  </div>
                ) : null}
                {orderedEntries.map((entry) => {
                  const renderSurfaceOnlyActivity =
                    shouldRenderActivityEntryAsSurfaceOnly(entry);

                  return hiddenEntryIds.has(entry.id) ||
                    shouldHidePendingStreamingReplyEntry({
                      entry,
                      entryLookup,
                      hiddenEntryIds,
                    }) ||
                    (isNoReplyPauseEntry(entry) &&
                      entry.parentId &&
                      entryLookup.has(entry.parentId)) ? null : (
                    <div
                      key={entry.id}
                      data-testid="ax-transcript-entry"
                      data-entry-id={entry.id}
                      data-entry-role={entry.role}
                      data-entry-type={entry.messageType || undefined}
                      data-activity-surface-only={
                        renderSurfaceOnlyActivity ? "true" : undefined
                      }
                      data-launcher-item-id={
                        getLauncherItemId(entry) || undefined
                      }
                      className={cn(
                        "flex min-w-0 max-w-full overflow-x-hidden",
                        renderSurfaceOnlyActivity || entry.role !== "user"
                          ? "justify-start"
                          : "justify-end",
                      )}
                    >
                      <div
                        className={cn(
                          "flex w-full min-w-0 max-w-full flex-col gap-3 overflow-x-hidden",
                          renderSurfaceOnlyActivity
                            ? "max-w-5xl"
                            : isLauncherTranscriptEntry(entry)
                              ? "max-w-5xl"
                              : entry.role === "user"
                                ? "max-w-2xl"
                                : "max-w-4xl",
                        )}
                      >
                        {shouldRenderEntryAsCard(entry) ? (
                          (() => {
                            const previewCardModel = buildWorkCardModel({
                              entry,
                              expanded: false,
                              threadIdByEntryId,
                              conversationGroups,
                              conversationCardsByThread,
                              resolveIdentityLabel: getSafeIdentityLabel,
                              formatCardMetaList,
                              buildSignalEmojis,
                            });
                            const expanded = Boolean(
                              expandedCards[previewCardModel.cardId],
                            );
                            const cardModel = expanded
                              ? buildWorkCardModel({
                                  entry,
                                  expanded,
                                  threadIdByEntryId,
                                  conversationGroups,
                                  conversationCardsByThread,
                                  resolveIdentityLabel: getSafeIdentityLabel,
                                  formatCardMetaList,
                                  buildSignalEmojis,
                                })
                              : previewCardModel;
                            const {
                              group,
                              threadCard,
                              groupedAgentEntries,
                              groupedCardEntries,
                              latestCardEntry,
                              isThreadSummaryCard,
                              cardId,
                              summary,
                              cardActivityAt,
                              participants,
                              participantSummary,
                              mentions,
                              mentionSummary,
                              statusValue,
                              groupedSurfaces,
                              groupedSurfaceCount,
                              signalEmojis,
                            } = cardModel;
                            const timestamp = formatTimestamp(cardActivityAt);
                            const headerProfile = getAgentProfile(
                              latestCardEntry.fromLabel || latestCardEntry.meta,
                            );
                            const {
                              entryPendingResponse,
                              entryPendingResponseDisplay,
                              entryInlinePendingResponseEntryId,
                            } = getPendingResponseStateForEntry(entry);
                            const cardPendingMonitor = getPendingMonitorState({
                              entry,
                              pendingStreamingReplyByParentId,
                              inlinePendingResponseEntryId:
                                entryInlinePendingResponseEntryId,
                              pendingResponse: entryPendingResponse,
                              pendingResponseDisplay:
                                entryPendingResponseDisplay,
                              getSafeRouteLabel,
                              getPersistedPendingReplyDisplay,
                            });
                            const copyText = buildCardCopyText(
                              summary,
                              participants,
                              mentions,
                              formatAbsoluteTimestamp(cardActivityAt),
                              statusValue,
                              groupedCardEntries.map((threadEntry) => {
                                const safeLabel = getSafeIdentityLabel(
                                  threadEntry.fromLabel || threadEntry.meta,
                                  threadEntry.role,
                                );
                                return {
                                  ...threadEntry,
                                  fromLabel: safeLabel,
                                  meta: safeLabel,
                                  toLabel: getSafeRouteLabel(
                                    threadEntry.toLabel,
                                  ),
                                };
                              }),
                            );

                            return (
                              <>
                                <div
                                  data-testid="ax-work-card"
                                  data-card-id={cardId}
                                  data-expanded={expanded ? "true" : "false"}
                                  className="relative min-w-0 max-w-full overflow-x-hidden rounded-[26px] border border-cyan-300/15 bg-slate-950/80 px-5 py-4 text-white shadow-[0_18px_40px_-28px_rgba(7,11,24,0.95)] transition hover:border-cyan-300/30"
                                >
                                  <div
                                    data-testid="ax-work-card-header"
                                    className="relative cursor-pointer"
                                    onClick={() => {
                                      if (!expanded) {
                                        void ensureSummary(entry);
                                      }
                                      toggleCard(cardId);
                                    }}
                                    role="button"
                                    tabIndex={0}
                                    onKeyDown={(event) => {
                                      if (
                                        event.key === "Enter" ||
                                        event.key === " "
                                      ) {
                                        event.preventDefault();
                                        if (!expanded) {
                                          void ensureSummary(entry);
                                        }
                                        toggleCard(cardId);
                                      }
                                    }}
                                  >
                                    <div className="absolute right-0 top-0 z-10 flex max-w-[240px] flex-wrap items-center justify-end gap-2">
                                      <button
                                        type="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          startShareMessage(entry, {
                                            summaryCard: isThreadSummaryCard,
                                          });
                                        }}
                                        data-testid="ax-share-message-button"
                                        className={cn(
                                          "rounded-full border p-2 transition",
                                          isDarkMode
                                            ? "border-white/10 bg-white/[0.04] text-slate-300 hover:border-cyan-300/35 hover:text-white"
                                            : "border-slate-200 bg-white text-slate-500 hover:border-cyan-300/45 hover:text-slate-900",
                                        )}
                                        aria-label="Share message"
                                        title="Share message"
                                      >
                                        <Share2 className="h-4 w-4" />
                                      </button>
                                      {entry.role === "user" &&
                                      !isThreadSummaryCard ? null : (
                                        <button
                                          type="button"
                                          onClick={(event) => {
                                            event.stopPropagation();
                                            startReply(entry, {
                                              summaryCard: isThreadSummaryCard,
                                            });
                                          }}
                                          data-testid="ax-reply-button"
                                          className={cn(
                                            "rounded-full border p-2 transition",
                                            isDarkMode
                                              ? "border-white/10 bg-white/[0.04] text-slate-300 hover:border-cyan-300/35 hover:text-white"
                                              : "border-slate-200 bg-white text-slate-500 hover:border-cyan-300/45 hover:text-slate-900",
                                          )}
                                          aria-label="Reply to message"
                                          title="Reply to message"
                                        >
                                          <CornerUpLeft className="h-4 w-4" />
                                        </button>
                                      )}
                                      <button
                                        type="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          void copyCard(cardId, copyText);
                                        }}
                                        data-testid="ax-copy-card-button"
                                        className={cn(
                                          "rounded-full border p-2 transition",
                                          isDarkMode
                                            ? "border-white/10 bg-white/[0.04] text-slate-300 hover:border-cyan-300/35 hover:text-white"
                                            : "border-slate-200 bg-white text-slate-500 hover:border-cyan-300/45 hover:text-slate-900",
                                        )}
                                        aria-label="Copy card contents"
                                        title="Copy card contents"
                                      >
                                        <Copy className="h-4 w-4" />
                                      </button>
                                      <button
                                        type="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          if (!expanded) {
                                            void ensureSummary(entry);
                                          }
                                          toggleCard(cardId);
                                        }}
                                        data-testid="ax-card-toggle-button"
                                        className={cn(
                                          "rounded-full border p-2 transition",
                                          isDarkMode
                                            ? "border-white/10 bg-white/[0.04] text-slate-300 hover:border-cyan-300/35 hover:text-white"
                                            : "border-slate-200 bg-white text-slate-500 hover:border-cyan-300/45 hover:text-slate-900",
                                        )}
                                        aria-label={
                                          expanded
                                            ? "Collapse message details"
                                            : "Expand message details"
                                        }
                                      >
                                        {expanded ? (
                                          <ChevronDown className="h-4 w-4" />
                                        ) : (
                                          <ChevronUp className="h-4 w-4" />
                                        )}
                                      </button>
                                    </div>

                                    <div className="min-h-[44px] pr-[152px] lg:pr-[240px]">
                                      <div className="flex items-center gap-2">
                                        <AgentIdentityBadge
                                          name={
                                            isThreadSummaryCard
                                              ? "Thread Summary"
                                              : getSafeIdentityLabel(
                                                  latestCardEntry.fromLabel ||
                                                    latestCardEntry.meta,
                                                  latestCardEntry.role,
                                                )
                                          }
                                          emoji={
                                            headerProfile?.emoji ||
                                            getAgentEmoji(
                                              headerProfile?.handle ||
                                                latestCardEntry.fromLabel ||
                                                latestCardEntry.meta,
                                              headerProfile?.name ||
                                                latestCardEntry.fromLabel ||
                                                latestCardEntry.meta,
                                            )
                                          }
                                        />
                                      </div>
                                    </div>

                                    <div
                                      className="mt-3 line-clamp-2 overflow-hidden text-base font-semibold text-white [overflow-wrap:anywhere]"
                                      title={summary}
                                    >
                                      {summary}
                                    </div>

                                    <div className="mt-3 flex flex-wrap gap-2">
                                      {[
                                        {
                                          label: isThreadSummaryCard
                                            ? "Participants"
                                            : "Sender",
                                          value:
                                            participantSummary ||
                                            latestCardEntry.fromLabel ||
                                            latestCardEntry.meta,
                                        },
                                        ...(mentionSummary
                                          ? [
                                              {
                                                label: "Mentions",
                                                value: mentionSummary,
                                              },
                                            ]
                                          : []),
                                        {
                                          label: "When",
                                          value: timestamp || "just now",
                                          title:
                                            formatAbsoluteTimestamp(
                                              cardActivityAt,
                                            ) || undefined,
                                        },
                                        {
                                          label: "Status",
                                          value: statusValue,
                                        },
                                      ].map((item) => (
                                        <span
                                          key={item.label}
                                          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs text-slate-300"
                                          title={item.title}
                                        >
                                          <span className="text-slate-500">
                                            {item.label}:
                                          </span>
                                          <span className="font-medium text-slate-100 [overflow-wrap:anywhere]">
                                            {item.value}
                                          </span>
                                        </span>
                                      ))}
                                    </div>
                                  </div>

                                  {cardPendingMonitor.pendingRouteLabel &&
                                  cardPendingMonitor.showPendingInline &&
                                  cardPendingMonitor.inlinePendingDisplay &&
                                  cardPendingMonitor.pendingChipStatus ? (
                                    <div
                                      data-testid="ax-card-activity-monitor"
                                      className="mt-3 inline-flex min-h-[3rem] max-w-full flex-wrap items-center gap-2 rounded-2xl border border-cyan-300/25 bg-cyan-400/[0.10] px-3 py-2 text-xs uppercase tracking-[0.16em] text-cyan-50"
                                      title={
                                        cardPendingMonitor.inlinePendingDisplay
                                          .detail ||
                                        cardPendingMonitor.inlinePendingDisplay
                                          .title ||
                                        undefined
                                      }
                                    >
                                      <span>
                                        To{" "}
                                        {cardPendingMonitor.pendingRouteLabel}
                                      </span>
                                      <span className="h-1 w-1 rounded-full bg-current opacity-50" />
                                      <span className="inline-flex items-center gap-1.5 normal-case tracking-normal">
                                        <Loader2 className="h-3 w-3 animate-spin" />
                                        {cardPendingMonitor.pendingChipStatus}
                                      </span>
                                      {cardPendingMonitor.visibleInlinePendingSignals.map(
                                        (signal) => (
                                          <span
                                            key={signal}
                                            className="normal-case tracking-normal text-cyan-100/80"
                                          >
                                            {signal}
                                          </span>
                                        ),
                                      )}
                                      {cardPendingMonitor.hiddenInlinePendingSignalCount ? (
                                        <span className="normal-case tracking-normal text-cyan-100/65">
                                          +
                                          {
                                            cardPendingMonitor.hiddenInlinePendingSignalCount
                                          }{" "}
                                          more
                                        </span>
                                      ) : null}
                                    </div>
                                  ) : null}

                                  <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                                    <div className="flex flex-wrap items-center gap-3">
                                      {isThreadSummaryCard ? (
                                        <div className="text-xs uppercase tracking-[0.18em] text-cyan-100/75">
                                          {threadCard?.message_count ||
                                            groupedAgentEntries.length}{" "}
                                          replies grouped in this thread
                                        </div>
                                      ) : null}

                                      {groupedSurfaceCount ? (
                                        <div className="text-xs uppercase tracking-[0.18em] text-cyan-100/75">
                                          {groupedSurfaceCount} structured
                                          update
                                          {groupedSurfaceCount === 1
                                            ? ""
                                            : "s"}{" "}
                                          available
                                        </div>
                                      ) : null}

                                      {copiedCardId === cardId ? (
                                        <div className="text-xs uppercase tracking-[0.18em] text-emerald-200/90">
                                          Copied card
                                        </div>
                                      ) : null}
                                    </div>

                                    <SignalRail
                                      emojis={signalEmojis.slice(0, 6)}
                                    />
                                  </div>

                                  {expanded ? (
                                    <div className="mt-4 space-y-4 border-t border-white/10 pt-4">
                                      {isThreadSummaryCard ? (
                                        <div className="rounded-[20px] border border-cyan-300/10 bg-cyan-400/5 px-4 py-3 text-sm text-cyan-50">
                                          <div className="text-[11px] uppercase tracking-[0.18em] text-cyan-100/70">
                                            Thread Summary
                                          </div>
                                          <div className="mt-2 font-medium">
                                            {summary}
                                          </div>
                                        </div>
                                      ) : null}

                                      {groupedCardEntries.map((threadEntry) => (
                                        <div
                                          key={threadEntry.id}
                                          className="rounded-[20px] border border-white/10 bg-white/[0.03] px-4 py-4"
                                        >
                                          {(() => {
                                            const profile = getAgentProfile(
                                              threadEntry.fromLabel ||
                                                threadEntry.meta,
                                            );
                                            const label = getSafeIdentityLabel(
                                              threadEntry.fromLabel ||
                                                threadEntry.meta,
                                              threadEntry.role,
                                            );
                                            const hoverAgent: HoverCardAgent = {
                                              username: profile?.id || label,
                                              bio:
                                                profile?.capabilitySummary ||
                                                undefined,
                                              status: profile?.status,
                                              capabilities:
                                                profile?.capabilitySummary
                                                  ? {
                                                      [profile.capabilitySummary]: true,
                                                    }
                                                  : undefined,
                                            };

                                            return (
                                              <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs uppercase tracking-[0.18em] text-slate-400">
                                                {threadEntry.role === "user" ? (
                                                  <AgentIdentityBadge
                                                    name={label}
                                                    emoji="🙂"
                                                    subtle
                                                  />
                                                ) : profile ? (
                                                  <AgentHoverCard
                                                    agent={hoverAgent}
                                                  >
                                                    <button
                                                      type="button"
                                                      onClick={() =>
                                                        insertMention(
                                                          profile.handle,
                                                        )
                                                      }
                                                      className="text-left transition hover:text-white"
                                                      title={`Mention @${profile.handle}`}
                                                      aria-label={`Mention @${profile.handle}`}
                                                    >
                                                      <AgentIdentityBadge
                                                        name={
                                                          profile.mentionLabel
                                                        }
                                                        emoji={profile.emoji}
                                                        subtle
                                                      />
                                                    </button>
                                                  </AgentHoverCard>
                                                ) : (
                                                  <AgentIdentityBadge
                                                    name={label}
                                                    emoji={getAgentEmoji(
                                                      profile?.handle || label,
                                                      profile?.name || label,
                                                    )}
                                                    subtle
                                                  />
                                                )}
                                                {threadEntry.toLabel ? (
                                                  <span>
                                                    to{" "}
                                                    {getSafeRouteLabel(
                                                      threadEntry.toLabel,
                                                    ) || "thread"}
                                                  </span>
                                                ) : null}
                                                {formatTimestamp(
                                                  threadEntry.createdAt,
                                                ) ? (
                                                  <span>
                                                    <span
                                                      title={
                                                        formatAbsoluteTimestamp(
                                                          threadEntry.createdAt,
                                                        ) || undefined
                                                      }
                                                    >
                                                      {formatTimestamp(
                                                        threadEntry.createdAt,
                                                      )}
                                                    </span>
                                                  </span>
                                                ) : null}
                                              </div>
                                            );
                                          })()}
                                          {group && threadEntry.aiSummary ? (
                                            <div className="mb-3 rounded-2xl border border-cyan-300/10 bg-cyan-400/5 px-3 py-2 text-sm text-cyan-50">
                                              {sanitizeAiSummary(
                                                threadEntry.aiSummary,
                                              )}
                                            </div>
                                          ) : null}
                                          <div className="mb-3 text-[11px] uppercase tracking-[0.18em] text-slate-500">
                                            Message ID:{" "}
                                            <span className="font-mono text-slate-300 normal-case tracking-normal">
                                              {threadEntry.id}
                                            </span>
                                          </div>
                                          {renderMarkdown(
                                            getRenderedEntryContent(
                                              threadEntry,
                                            ),
                                          )}
                                          {renderMarkdownMediaSidecar(
                                            getRenderedEntryContent(
                                              threadEntry,
                                            ),
                                          )}
                                        </div>
                                      ))}

                                      {group ? (
                                        <div className="flex justify-end">
                                          <button
                                            type="button"
                                            onClick={() => toggleCard(cardId)}
                                            data-testid="ax-collapse-thread-inline"
                                            className={cn(
                                              "inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm transition",
                                              isDarkMode
                                                ? "border-white/10 bg-white/[0.04] text-slate-300 hover:border-cyan-300/35 hover:text-white"
                                                : "border-slate-200 bg-slate-50 text-slate-700 hover:border-slate-300 hover:bg-white hover:text-slate-900",
                                            )}
                                          >
                                            <ChevronUp className="h-4 w-4" />
                                            Collapse thread
                                          </button>
                                        </div>
                                      ) : null}
                                    </div>
                                  ) : null}
                                </div>
                                <AxSurfaceRail
                                  surfaces={groupedSurfaces}
                                  messageId={entry.id}
                                  spaceId={currentSpaceId}
                                  activeActionKey={activeWidgetActionKey}
                                  pendingResponsesBySourceId={
                                    pendingResponsesBySourceId
                                  }
                                  timestamp={entry.createdAt}
                                  forceMountWidgets={isLauncherTranscriptEntry(
                                    entry,
                                  )}
                                  onOpenWidgetPanel={openWidgetPanelFromSurface}
                                  onForwardInit={handleForwardInit}
                                  onReplyInit={handleCardReplyInit}
                                  onAction={({
                                    messageId,
                                    actionId,
                                    cardId,
                                    choiceId,
                                    freeText,
                                  }: AxSurfaceActionInput) =>
                                    void handleWidgetAction({
                                      messageId,
                                      cardId,
                                      actionId,
                                      ...(choiceId ? { choiceId } : {}),
                                      ...(typeof freeText === "string"
                                        ? { freeText }
                                        : {}),
                                    })
                                  }
                                />
                              </>
                            );
                          })()
                        ) : (
                          <>
                            {entry.messageType === "agent_pause" ? (
                              isNoReplyPauseEntry(entry) ? (
                                entry.parentId &&
                                entryLookup.has(entry.parentId) ? null : (
                                  <div
                                    data-testid="ax-agent-no-reply-tag"
                                    className="flex flex-wrap items-center gap-2 pl-2 pt-1 text-[11px] text-slate-400"
                                  >
                                    <span
                                      className={cn(
                                        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1",
                                        getCompactPauseTone(entry) === "ack"
                                          ? "border border-emerald-300/20 bg-emerald-400/10 text-emerald-100"
                                          : "border border-white/10 bg-white/[0.04] text-slate-300",
                                      )}
                                    >
                                      {renderCompactPauseBadge(
                                        getCompactPauseTone(entry),
                                      )}
                                      <span>
                                        {getCompactPauseText(
                                          getSafeIdentityLabel(
                                            entry.fromLabel || entry.meta,
                                            entry.role,
                                          ),
                                          getCompactPauseTone(entry),
                                        )}
                                      </span>
                                    </span>
                                    {formatTimestamp(entry.createdAt) ? (
                                      <span
                                        title={
                                          formatAbsoluteTimestamp(
                                            entry.createdAt,
                                          ) || undefined
                                        }
                                      >
                                        {formatTimestamp(entry.createdAt)}
                                      </span>
                                    ) : null}
                                  </div>
                                )
                              ) : (
                                <div
                                  data-testid="ax-agent-pause-notice"
                                  className="rounded-[24px] border border-amber-300/20 bg-amber-400/10 px-5 py-4 text-amber-50 shadow-[0_18px_40px_-28px_rgba(7,11,24,0.95)]"
                                >
                                  <div className="flex flex-wrap items-center gap-2">
                                    {entry.pauseEmoji ? (
                                      <span
                                        className="text-lg"
                                        aria-hidden="true"
                                      >
                                        {entry.pauseEmoji}
                                      </span>
                                    ) : null}
                                    <div className="text-sm font-medium text-white">
                                      {entry.pauseReasonText ||
                                        entry.content ||
                                        `${getSafeIdentityLabel(
                                          entry.fromLabel || entry.meta,
                                          entry.role,
                                        )} chose not to reply.`}
                                    </div>
                                    {formatTimestamp(entry.createdAt) ? (
                                      <span className="text-xs uppercase tracking-[0.18em] text-amber-100/70">
                                        <span
                                          title={
                                            formatAbsoluteTimestamp(
                                              entry.createdAt,
                                            ) || undefined
                                          }
                                        >
                                          {formatTimestamp(entry.createdAt)}
                                        </span>
                                      </span>
                                    ) : null}
                                  </div>
                                  <div className="mt-3 flex flex-wrap gap-2">
                                    <span className="inline-flex items-center gap-2 rounded-full border border-amber-200/15 bg-black/10 px-3 py-1.5 text-xs uppercase tracking-[0.18em] text-amber-50/85">
                                      <Clock className="h-3.5 w-3.5" />
                                      {entry.pauseReason &&
                                      entry.pauseReason !== "no_reply"
                                        ? entry.pauseReason.replace(/_/g, " ")
                                        : "no reply"}
                                    </span>
                                    {entry.pauseDuration ? (
                                      <span className="inline-flex items-center rounded-full border border-amber-200/15 bg-black/10 px-3 py-1.5 text-xs uppercase tracking-[0.18em] text-amber-50/85">
                                        {Math.round(entry.pauseDuration)}s
                                      </span>
                                    ) : null}
                                    {entry.pauseExpiresAt ? (
                                      <span
                                        className="inline-flex items-center rounded-full border border-amber-200/15 bg-black/10 px-3 py-1.5 text-xs uppercase tracking-[0.18em] text-amber-50/85"
                                        title={
                                          formatAbsoluteTimestamp(
                                            entry.pauseExpiresAt,
                                          ) || undefined
                                        }
                                      >
                                        Until{" "}
                                        {formatTimestamp(
                                          entry.pauseExpiresAt,
                                        ) || "later"}
                                      </span>
                                    ) : null}
                                  </div>
                                </div>
                              )
                            ) : isLauncherTranscriptEntry(entry) ||
                              isSignalOnlyTranscriptEntry(entry) ||
                              renderSurfaceOnlyActivity ? null : (
                              (() => {
                                const bubbleAttribution =
                                  classifyBubbleAttribution(
                                    {
                                      role: entry.role,
                                      fromHandle: entry.fromHandle,
                                    },
                                    username,
                                  );
                                const bubbleTestId =
                                  getBubbleTestId(bubbleAttribution);
                                const bubbleClassName = getBubbleClassName(
                                  bubbleAttribution,
                                  isDarkMode,
                                );
                                return (
                                  <div
                                    data-testid={bubbleTestId}
                                    data-bubble-attribution={bubbleAttribution}
                                    data-streaming={
                                      entry.isStreaming ? "true" : "false"
                                    }
                                    className={cn(
                                      "min-w-0 max-w-full overflow-x-hidden rounded-[26px] px-5 py-4 shadow-[0_18px_40px_-28px_rgba(7,11,24,0.95)] [overflow-wrap:anywhere]",
                                      entry.isStreaming && "min-h-[8rem]",
                                      bubbleClassName,
                                    )}
                                  >
                                    <div className="mb-3 flex flex-wrap items-center gap-2">
                                      {entry.role === "agent" ? (
                                        isLauncherTranscriptEntry(entry) ? (
                                          <AgentIdentityBadge
                                            name={getSafeIdentityLabel(
                                              entry.fromLabel || entry.meta,
                                              entry.role,
                                            )}
                                            emoji="🧩"
                                            subtle
                                          />
                                        ) : (
                                          (() => {
                                            const agentLabel =
                                              entry.fromLabel || entry.meta;
                                            const agentProfile =
                                              getAgentProfile(agentLabel);
                                            const presenceKey =
                                              agentProfile?.handle ||
                                              agentLabel
                                                ?.replace(/^@/, "")
                                                .trim();

                                            return (
                                              <AgentBadgeWithCard
                                                name={getSafeIdentityLabel(
                                                  agentLabel,
                                                  entry.role,
                                                )}
                                                emoji={getAgentEmoji(
                                                  agentLabel,
                                                  agentLabel,
                                                )}
                                                subtle
                                                agentId={agentProfile?.id}
                                                spaceId={
                                                  currentSpaceId || undefined
                                                }
                                                onMention={(handle) =>
                                                  insertMention(handle)
                                                }
                                                presenceStatus={
                                                  presenceKey
                                                    ? getPresenceStatus(
                                                        presenceKey,
                                                      )
                                                    : undefined
                                                }
                                                presenceFreshness={
                                                  presenceKey
                                                    ? getPresenceFreshness(
                                                        presenceKey,
                                                      )
                                                    : undefined
                                                }
                                              />
                                            );
                                          })()
                                        )
                                      ) : (
                                        <AgentIdentityBadge
                                          name={getSafeIdentityLabel(
                                            entry.fromLabel || entry.meta,
                                            entry.role,
                                          )}
                                          emoji="👤"
                                          subtle
                                          className={
                                            isDarkMode
                                              ? undefined
                                              : "border-blue-200/70 bg-white/80 text-slate-700"
                                          }
                                        />
                                      )}
                                      <span
                                        data-testid={
                                          entry.role === "user"
                                            ? "ax-user-bubble-meta"
                                            : "ax-agent-bubble-meta"
                                        }
                                        className="sr-only"
                                      >
                                        {getSafeIdentityLabel(
                                          entry.fromLabel || entry.meta,
                                          entry.role,
                                        )}
                                      </span>
                                      {formatTimestamp(entry.createdAt) ? (
                                        <span
                                          className={cn(
                                            "max-w-full text-xs uppercase tracking-[0.18em]",
                                            entry.role === "user"
                                              ? isDarkMode
                                                ? "text-white/70"
                                                : "text-slate-500"
                                              : isDarkMode
                                                ? "text-white/45"
                                                : "text-slate-500",
                                          )}
                                        >
                                          <span
                                            title={
                                              formatAbsoluteTimestamp(
                                                entry.createdAt,
                                              ) || undefined
                                            }
                                          >
                                            {formatTimestamp(entry.createdAt)}
                                          </span>
                                        </span>
                                      ) : null}
                                      {(() => {
                                        const sd = getStatusDisplay(
                                          entry.statusLabel,
                                          entry.toolName,
                                        );
                                        if (!sd) return null;
                                        const IconMap = {
                                          sparkles: Sparkles,
                                          wrench: Wrench,
                                          radio: Radio,
                                          clock: Clock,
                                          send: Send,
                                          check: Check,
                                          activity: Activity,
                                        } as const;
                                        const Icon = IconMap[sd.icon];
                                        return (
                                          <span
                                            data-testid="ax-status-indicator"
                                            className={cn(
                                              "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] uppercase tracking-[0.18em]",
                                              sd.colorClass,
                                              sd.animation === "pulse" &&
                                                "animate-pulse",
                                            )}
                                          >
                                            <Icon
                                              className={cn(
                                                "h-3 w-3",
                                                sd.animation === "spin" &&
                                                  "animate-spin",
                                              )}
                                            />
                                            <span className="min-w-0 max-w-full [overflow-wrap:anywhere]">
                                              {sd.text}
                                            </span>
                                          </span>
                                        );
                                      })()}
                                      {isLauncherTranscriptEntry(
                                        entry,
                                      ) ? null : (
                                        <div className="ml-auto flex items-center gap-2">
                                          <button
                                            type="button"
                                            onClick={() =>
                                              startShareMessage(entry, {
                                                summaryCard: false,
                                              })
                                            }
                                            data-testid="ax-share-message-button"
                                            className={cn(
                                              "rounded-full border p-1.5 transition",
                                              entry.role === "user" &&
                                                !isDarkMode
                                                ? "border-blue-200/70 bg-white/80 text-slate-600 hover:border-blue-300 hover:bg-white hover:text-slate-900"
                                                : isDarkMode
                                                  ? "border-white/10 bg-white/[0.06] text-white/70 hover:border-white/20 hover:text-white"
                                                  : "border-slate-200 bg-slate-50 text-slate-600 hover:border-slate-300 hover:bg-white hover:text-slate-900",
                                            )}
                                            aria-label="Share message"
                                            title="Share message"
                                          >
                                            <Share2 className="h-3.5 w-3.5" />
                                          </button>
                                          {entry.role === "user" ? null : (
                                            <button
                                              type="button"
                                              onClick={() =>
                                                startReply(entry, {
                                                  summaryCard: false,
                                                })
                                              }
                                              data-testid="ax-reply-button"
                                              className={cn(
                                                "rounded-full border p-1.5 transition",
                                                isDarkMode
                                                  ? "border-white/10 bg-white/[0.06] text-white/70 hover:border-white/20 hover:text-white"
                                                  : "border-slate-200 bg-slate-50 text-slate-600 hover:border-slate-300 hover:bg-white hover:text-slate-900",
                                              )}
                                              aria-label="Reply to message"
                                              title="Reply to message"
                                            >
                                              <CornerUpLeft className="h-3.5 w-3.5" />
                                            </button>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                    {(() => {
                                      const replyPreview =
                                        getReplyPreview(entry);
                                      const {
                                        entryPendingResponse,
                                        entryPendingResponseDisplay,
                                        entryInlinePendingResponseEntryId,
                                      } =
                                        getPendingResponseStateForEntry(entry);

                                      const {
                                        inlinePendingDisplay,
                                        showPendingInline,
                                        pendingRouteLabel,
                                        pendingChipStatus,
                                        visibleInlinePendingSignals,
                                        hiddenInlinePendingSignalCount,
                                      } = getPendingMonitorState({
                                        entry,
                                        pendingStreamingReplyByParentId,
                                        inlinePendingResponseEntryId:
                                          entryInlinePendingResponseEntryId,
                                        pendingResponse: entryPendingResponse,
                                        pendingResponseDisplay:
                                          entryPendingResponseDisplay,
                                        getSafeRouteLabel,
                                        getPersistedPendingReplyDisplay,
                                      });
                                      const hasActivePendingSignal = Boolean(
                                        showPendingInline &&
                                        inlinePendingDisplay &&
                                        pendingChipStatus,
                                      );
                                      const pendingActivityTitle =
                                        inlinePendingDisplay?.detail ||
                                        inlinePendingDisplay?.title ||
                                        undefined;

                                      if (
                                        !pendingRouteLabel &&
                                        !replyPreview &&
                                        !showPendingInline
                                      ) {
                                        return null;
                                      }

                                      return (
                                        <div
                                          className={cn(
                                            "mb-3 flex flex-wrap gap-2",
                                            hasActivePendingSignal && "w-full",
                                          )}
                                        >
                                          {pendingRouteLabel ? (
                                            <div
                                              data-testid="ax-route-state-chip"
                                              className={cn(
                                                hasActivePendingSignal
                                                  ? "flex min-h-[6.5rem] w-full max-w-full flex-col gap-2 rounded-2xl border px-3.5 py-3 text-xs shadow-[0_18px_42px_-30px_rgba(8,47,73,0.95)] ring-1 sm:w-auto sm:min-w-[min(30rem,100%)]"
                                                  : "inline-flex max-w-full flex-wrap items-center gap-2 rounded-full border px-3 py-1.5 text-xs uppercase tracking-[0.16em]",
                                                hasActivePendingSignal
                                                  ? isDarkMode
                                                    ? "border-cyan-300/35 bg-cyan-400/[0.13] text-cyan-50 ring-cyan-300/15"
                                                    : "border-cyan-300 bg-cyan-50 text-cyan-950 ring-cyan-200/70"
                                                  : entry.role === "user" &&
                                                      !isDarkMode
                                                    ? "border-blue-200/70 bg-white/80 text-slate-700"
                                                    : isDarkMode
                                                      ? "border-white/10 bg-white/[0.08] text-white/75"
                                                      : "border-slate-200 bg-slate-50 text-slate-700",
                                              )}
                                              title={pendingActivityTitle}
                                            >
                                              {hasActivePendingSignal &&
                                              inlinePendingDisplay &&
                                              pendingChipStatus ? (
                                                <>
                                                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                                                    <span
                                                      className={cn(
                                                        "inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
                                                        isDarkMode
                                                          ? "border-cyan-200/20 bg-black/15 text-cyan-100"
                                                          : "border-cyan-200 bg-white/80 text-cyan-900",
                                                      )}
                                                    >
                                                      <Activity className="h-3 w-3" />
                                                      Activity
                                                    </span>
                                                    <span
                                                      className={cn(
                                                        "min-w-0 break-words text-[11px] font-semibold uppercase tracking-[0.16em]",
                                                        isDarkMode
                                                          ? "text-cyan-100/75"
                                                          : "text-cyan-800",
                                                      )}
                                                    >
                                                      To {pendingRouteLabel}
                                                    </span>
                                                  </div>
                                                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                                                    <span
                                                      className={cn(
                                                        "inline-flex min-w-0 items-center gap-2 rounded-full px-2.5 py-1.5 text-sm font-semibold normal-case tracking-normal",
                                                        isDarkMode
                                                          ? "bg-cyan-300/15 text-white"
                                                          : "bg-cyan-100 text-cyan-950",
                                                      )}
                                                    >
                                                      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                                                      <span className="min-w-0 break-words">
                                                        {pendingChipStatus}
                                                      </span>
                                                    </span>
                                                    {visibleInlinePendingSignals.map(
                                                      (signal) => (
                                                        <span
                                                          key={signal}
                                                          className={cn(
                                                            "rounded-full border px-2.5 py-1 text-xs font-medium normal-case tracking-normal",
                                                            isDarkMode
                                                              ? "border-cyan-200/15 bg-black/10 text-cyan-100/85"
                                                              : "border-cyan-200 bg-white/70 text-cyan-800",
                                                          )}
                                                        >
                                                          {signal}
                                                        </span>
                                                      ),
                                                    )}
                                                    {hiddenInlinePendingSignalCount ? (
                                                      <span
                                                        className={cn(
                                                          "rounded-full px-2.5 py-1 text-xs font-medium normal-case tracking-normal",
                                                          isDarkMode
                                                            ? "text-cyan-100/65"
                                                            : "text-cyan-700/80",
                                                        )}
                                                      >
                                                        +
                                                        {
                                                          hiddenInlinePendingSignalCount
                                                        }{" "}
                                                        more
                                                      </span>
                                                    ) : null}
                                                  </div>
                                                  {inlinePendingDisplay.detail &&
                                                  inlinePendingDisplay.detail !==
                                                    inlinePendingDisplay.title ? (
                                                    <span
                                                      className={cn(
                                                        "min-w-0 break-words text-xs normal-case tracking-normal",
                                                        isDarkMode
                                                          ? "text-cyan-50/70"
                                                          : "text-cyan-900/75",
                                                      )}
                                                    >
                                                      {
                                                        inlinePendingDisplay.detail
                                                      }
                                                    </span>
                                                  ) : null}
                                                </>
                                              ) : (
                                                <span>
                                                  To {pendingRouteLabel}
                                                </span>
                                              )}
                                            </div>
                                          ) : null}
                                          {replyPreview ? (
                                            <div
                                              title={
                                                replyPreview.content ||
                                                undefined
                                              }
                                              className={cn(
                                                "max-w-full rounded-full border px-3 py-1.5 text-xs uppercase tracking-[0.18em]",
                                                entry.role === "user" &&
                                                  !isDarkMode
                                                  ? "border-blue-200/70 bg-white/80 text-slate-700"
                                                  : isDarkMode
                                                    ? "border-white/10 bg-white/[0.08] text-white/75"
                                                    : "border-slate-200 bg-slate-50 text-slate-700",
                                              )}
                                            >
                                              <span className="font-medium">
                                                Replying to {replyPreview.label}
                                              </span>
                                            </div>
                                          ) : null}
                                        </div>
                                      );
                                    })()}
                                    {(() => {
                                      const summaryReplacement =
                                        getAutoSummaryReplacement(
                                          entry,
                                          autoSummariesVisible,
                                        );
                                      const summarySignals =
                                        getAutoSummarySignals(entry);
                                      const groupedSummaryEmojis =
                                        aggregateSignalEmojis(
                                          summarySignals.emojis,
                                        );
                                      const visibleSummaryEmojis =
                                        groupedSummaryEmojis.slice(0, 5);
                                      const hiddenSummaryEmojiCount = Math.max(
                                        0,
                                        groupedSummaryEmojis.length -
                                          visibleSummaryEmojis.length,
                                      );
                                      const emojiTitle = groupedSummaryEmojis
                                        .map(({ emoji, count }) =>
                                          count > 1
                                            ? `${emoji} x${count}`
                                            : emoji,
                                        )
                                        .join(" ");
                                      const visibleMentions =
                                        summarySignals.mentions.slice(0, 3);
                                      const hiddenMentionCount = Math.max(
                                        0,
                                        summarySignals.mentions.length -
                                          visibleMentions.length,
                                      );
                                      const summaryExpanded = Boolean(
                                        expandedSummaryEntries[entry.id],
                                      );
                                      const canExpandFullResponse =
                                        Boolean(summaryReplacement) &&
                                        Boolean(entry.content?.trim());

                                      if (
                                        entry.isStreaming &&
                                        !entry.content.trim()
                                      ) {
                                        const actorLabel =
                                          entry.fromLabel ||
                                          entry.meta ||
                                          "Agent";
                                        const phaseTitle =
                                          computeAgentPhaseTitle({
                                            actorLabel,
                                            status: entry.statusLabel,
                                            toolName: entry.toolName,
                                            activity: entry.activity,
                                            reason: entry.reason,
                                            errorMessage: entry.errorMessage,
                                            retryAfterSeconds:
                                              entry.retryAfterSeconds,
                                            hasActiveActor: true,
                                          });
                                        const progressSignal = entry.progress
                                          ? `${entry.progress.current}/${entry.progress.total} ${entry.progress.unit || ""}`.trim()
                                          : null;
                                        return (
                                          <div
                                            className={cn(
                                              "min-h-[5.5rem] text-[15px] leading-7",
                                              isDarkMode
                                                ? "text-white/70"
                                                : "text-slate-600",
                                            )}
                                          >
                                            <div>{phaseTitle}</div>
                                            {progressSignal ? (
                                              <div
                                                className={cn(
                                                  "text-xs mt-1",
                                                  isDarkMode
                                                    ? "text-white/50"
                                                    : "text-slate-500",
                                                )}
                                              >
                                                {progressSignal}
                                              </div>
                                            ) : null}
                                          </div>
                                        );
                                      }

                                      if (!summaryReplacement) {
                                        return renderMarkdown(
                                          getRenderedEntryContent(entry),
                                        );
                                      }

                                      return (
                                        <div className="space-y-3">
                                          <div
                                            className={cn(
                                              "rounded-2xl border px-4 py-3",
                                              isDarkMode
                                                ? "border-cyan-300/15 bg-cyan-400/5 text-cyan-50"
                                                : "border-cyan-200 bg-cyan-50 text-slate-900",
                                            )}
                                          >
                                            <div
                                              className={cn(
                                                "text-[11px] uppercase tracking-[0.18em]",
                                                isDarkMode
                                                  ? "text-cyan-100/80"
                                                  : "text-cyan-700",
                                              )}
                                            >
                                              Summary
                                            </div>
                                            <p
                                              className={cn(
                                                "mt-2 text-[15px] leading-7",
                                                isDarkMode
                                                  ? "text-white"
                                                  : "text-slate-900",
                                              )}
                                            >
                                              {summaryReplacement}
                                            </p>
                                          </div>
                                          {canExpandFullResponse ? (
                                            <div className="flex flex-wrap items-center gap-2">
                                              <button
                                                type="button"
                                                onClick={() =>
                                                  toggleSummaryExpansion(
                                                    entry.id,
                                                  )
                                                }
                                                className={cn(
                                                  "inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm transition",
                                                  isDarkMode
                                                    ? "border-white/10 bg-white/[0.04] text-slate-300 hover:border-cyan-300/35 hover:text-white"
                                                    : "border-slate-200 bg-slate-50 text-slate-700 hover:border-slate-300 hover:bg-white hover:text-slate-900",
                                                )}
                                              >
                                                {summaryExpanded ? (
                                                  <ArrowUp className="h-4 w-4" />
                                                ) : (
                                                  <ArrowDown className="h-4 w-4" />
                                                )}
                                                {summaryExpanded
                                                  ? "Hide full response"
                                                  : "Show full response"}
                                              </button>
                                              <span
                                                className={cn(
                                                  "inline-flex items-center rounded-full border px-3 py-2 text-xs",
                                                  isDarkMode
                                                    ? "border-white/10 bg-white/[0.04] text-slate-400"
                                                    : "border-slate-200 bg-slate-50 text-slate-600",
                                                )}
                                              >
                                                {summarySignals.fullResponseCharCount.toLocaleString()}{" "}
                                                chars
                                              </span>
                                              {summarySignals.targetLabel ? (
                                                <span
                                                  className={cn(
                                                    "inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs",
                                                    isDarkMode
                                                      ? "border-white/10 bg-white/[0.04] text-slate-300"
                                                      : "border-slate-200 bg-slate-50 text-slate-700",
                                                  )}
                                                >
                                                  <span
                                                    className={cn(
                                                      isDarkMode
                                                        ? "text-slate-400"
                                                        : "text-slate-500",
                                                    )}
                                                  >
                                                    To
                                                  </span>
                                                  <span>
                                                    {summarySignals.targetLabel}
                                                  </span>
                                                </span>
                                              ) : null}
                                              {visibleMentions.length ? (
                                                <span
                                                  title={summarySignals.mentions.join(
                                                    " ",
                                                  )}
                                                  className={cn(
                                                    "inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs",
                                                    isDarkMode
                                                      ? "border-white/10 bg-white/[0.04] text-slate-300"
                                                      : "border-slate-200 bg-slate-50 text-slate-700",
                                                  )}
                                                >
                                                  <span
                                                    className={cn(
                                                      isDarkMode
                                                        ? "text-slate-400"
                                                        : "text-slate-500",
                                                    )}
                                                  >
                                                    Mentions
                                                  </span>
                                                  <span className="truncate">
                                                    {visibleMentions.join(" ")}
                                                  </span>
                                                  {hiddenMentionCount > 0 ? (
                                                    <span
                                                      className={cn(
                                                        isDarkMode
                                                          ? "text-slate-400"
                                                          : "text-slate-500",
                                                      )}
                                                    >
                                                      +{hiddenMentionCount}
                                                    </span>
                                                  ) : null}
                                                </span>
                                              ) : null}
                                              {visibleSummaryEmojis.length ? (
                                                <span
                                                  title={
                                                    emojiTitle ||
                                                    "Emojis used in the full response"
                                                  }
                                                  className={cn(
                                                    "inline-flex items-center gap-2 rounded-full border px-3 py-2 text-xs",
                                                    isDarkMode
                                                      ? "border-white/10 bg-white/[0.04] text-slate-300"
                                                      : "border-slate-200 bg-slate-50 text-slate-700",
                                                  )}
                                                >
                                                  <span
                                                    className={cn(
                                                      isDarkMode
                                                        ? "text-slate-400"
                                                        : "text-slate-500",
                                                    )}
                                                  >
                                                    Emojis
                                                  </span>
                                                  <span className="inline-flex items-center gap-1.5">
                                                    {visibleSummaryEmojis.map(
                                                      ({ emoji, count }) => (
                                                        <span
                                                          key={`${emoji}-${count}`}
                                                          className={cn(
                                                            "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5",
                                                            isDarkMode
                                                              ? "border-white/10 bg-white/[0.04]"
                                                              : "border-slate-200 bg-white",
                                                          )}
                                                        >
                                                          <span>{emoji}</span>
                                                          {count > 1 ? (
                                                            <span
                                                              className={cn(
                                                                "text-[10px]",
                                                                isDarkMode
                                                                  ? "text-slate-400"
                                                                  : "text-slate-500",
                                                              )}
                                                            >
                                                              {count}
                                                            </span>
                                                          ) : null}
                                                        </span>
                                                      ),
                                                    )}
                                                    {hiddenSummaryEmojiCount >
                                                    0 ? (
                                                      <span
                                                        className={cn(
                                                          isDarkMode
                                                            ? "text-slate-400"
                                                            : "text-slate-500",
                                                        )}
                                                      >
                                                        +
                                                        {
                                                          hiddenSummaryEmojiCount
                                                        }
                                                      </span>
                                                    ) : null}
                                                  </span>
                                                </span>
                                              ) : null}
                                            </div>
                                          ) : null}
                                          {summaryExpanded
                                            ? renderMarkdown(
                                                getRenderedEntryContent(entry),
                                              )
                                            : null}
                                        </div>
                                      );
                                    })()}

                                    {renderMarkdownMediaSidecar(
                                      getRenderedEntryContent(entry),
                                    )}

                                    {entry.attachments?.length ? (
                                      <div className="mt-4 flex flex-wrap gap-2">
                                        {entry.attachments.map((attachment) => (
                                          <MessageAttachmentPreview
                                            key={
                                              attachment.id || attachment.name
                                            }
                                            attachment={attachment}
                                            userTone={entry.role === "user"}
                                          />
                                        ))}
                                      </div>
                                    ) : null}
                                    {(
                                      attachedNoReplyIndicatorsByParentId.get(
                                        entry.id,
                                      ) || []
                                    ).length ? (
                                      <div
                                        className={cn(
                                          "mt-3 flex flex-wrap gap-2",
                                          entry.role === "user"
                                            ? "justify-end"
                                            : "justify-start",
                                        )}
                                      >
                                        {(
                                          attachedNoReplyIndicatorsByParentId.get(
                                            entry.id,
                                          ) || []
                                        ).map((indicator) => (
                                          <span
                                            key={indicator.id}
                                            title={
                                              indicator.pauseReasonText ||
                                              undefined
                                            }
                                            data-testid="ax-attached-no-reply-tag"
                                            className={cn(
                                              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px]",
                                              indicator.tone === "ack"
                                                ? "border border-emerald-300/20 bg-emerald-400/10 text-emerald-100"
                                                : "border border-white/10 bg-white/[0.04] text-slate-300",
                                            )}
                                          >
                                            {renderCompactPauseBadge(
                                              indicator.tone,
                                            )}
                                            <span>
                                              {getCompactPauseText(
                                                getSafeIdentityLabel(
                                                  indicator.agentLabel,
                                                  "agent",
                                                ),
                                                indicator.tone,
                                              )}
                                            </span>
                                            {indicator.createdAt ? (
                                              <span
                                                className="text-slate-500"
                                                title={
                                                  formatAbsoluteTimestamp(
                                                    indicator.createdAt,
                                                  ) || undefined
                                                }
                                              >
                                                {formatTimestamp(
                                                  indicator.createdAt,
                                                ) || ""}
                                              </span>
                                            ) : null}
                                          </span>
                                        ))}
                                      </div>
                                    ) : null}
                                  </div>
                                );
                              })()
                            )}
                            <AxSurfaceRail
                              surfaces={
                                shouldSuppressInlineEntrySurfaces({
                                  cardsEnabled,
                                  entry,
                                  threadIdByEntryId,
                                  conversationGroups,
                                  conversationCardsByThread,
                                  conversationCardAnchors,
                                })
                                  ? []
                                  : entry.surfaces || []
                              }
                              messageId={entry.id}
                              spaceId={currentSpaceId}
                              activeActionKey={activeWidgetActionKey}
                              pendingResponsesBySourceId={
                                pendingResponsesBySourceId
                              }
                              timestamp={entry.createdAt}
                              forceMountWidgets={isLauncherTranscriptEntry(
                                entry,
                              )}
                              onOpenWidgetPanel={openWidgetPanelFromSurface}
                              onForwardInit={handleForwardInit}
                              onReplyInit={handleCardReplyInit}
                              onAction={({
                                messageId,
                                actionId,
                                cardId,
                                choiceId,
                                freeText,
                              }: AxSurfaceActionInput) =>
                                void handleWidgetAction({
                                  messageId,
                                  cardId,
                                  actionId,
                                  ...(choiceId ? { choiceId } : {}),
                                  ...(typeof freeText === "string"
                                    ? { freeText }
                                    : {}),
                                })
                              }
                            />
                          </>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          </div>
        </div>

        {showJumpToLatest ? (
          <div
            className="pointer-events-none fixed inset-x-0 z-30 px-4 sm:px-6 lg:px-8"
            style={{ bottom: `${composerHeight + 24}px` }}
          >
            <div className="mx-auto flex w-full max-w-5xl justify-end">
              <button
                type="button"
                data-testid="new-messages-affordance"
                aria-label="Scroll to new messages"
                onClick={() => {
                  setHasUnreadLatest(false);
                  setAutoFollowState(true);
                  scrollToLatest("smooth");
                }}
                className={cn(
                  "pointer-events-auto inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs shadow-[0_18px_40px_-28px_rgba(2,8,23,1)] backdrop-blur-xl transition",
                  isDarkMode
                    ? "border-cyan-300/20 bg-slate-950/90 text-cyan-100 hover:border-cyan-300/35"
                    : "border-slate-200 bg-white/95 text-cyan-700 hover:border-cyan-400/60",
                )}
              >
                <ArrowDown className="h-3 w-3" />
                New messages
              </button>
            </div>
          </div>
        ) : null}

        {activeMcpAppPanel ? (
          <AxMcpAppScreenChrome
            title={activeMcpAppPanel.title}
            isTasks={activePanelIsTasks}
            isContext={activePanelIsContext}
            isSearch={activePanelIsSearch}
            isDarkMode={isDarkMode}
            immersive={
              Boolean(activeMcpAppPanel.widget.html) &&
              activeMcpAppPanel.widget.tool_action === "render_html"
            }
            bottomInset={composerHeight}
            searchSpaceId={activeMcpAppPanel.spaceId}
            onBackToActivity={() => setActiveMcpAppPanel(null)}
            onShowAllContext={() => {
              if (isDirectHtmlContextPanelWidget(activeMcpAppPanel.widget)) {
                setActiveMcpAppPanel((current) =>
                  current
                    ? {
                        ...current,
                        id: `${current.id}-context-list`,
                        title: "Context",
                        widget: buildContextExplorerPanelWidgetFromDirectHtml(
                          current.widget,
                        ),
                      }
                    : current,
                );
                return;
              }

              setActiveMcpAppPanel((current) =>
                current ? { ...current, title: "Context" } : current,
              );
              setMcpAppPanelHostCommand({
                type: CONTEXT_ALL_HOST_COMMAND,
                nonce: Date.now(),
              });
            }}
            onShowAllTasks={() => {
              setActiveMcpAppPanel((current) =>
                current ? { ...current, title: "Tasks" } : current,
              );
              setMcpAppPanelHostCommand({
                type: TASKS_ALL_HOST_COMMAND,
                nonce: Date.now(),
              });
            }}
          >
            <AxMcpAppWidget
              key={activeMcpAppPanel.id}
              messageId={activeMcpAppPanel.messageId}
              spaceId={activeMcpAppPanel.spaceId}
              widget={activeMcpAppPanel.widget}
              timestamp={activeMcpAppPanel.timestamp}
              forceMount
              panelMode
              hostCommand={mcpAppPanelHostCommand}
              deferFrameUntilToolResult={
                activeMcpAppPanel.source === "quick_action"
              }
              onClose={() => setActiveMcpAppPanel(null)}
              onOpenDirectHtmlPanel={openDirectHtmlPanelFromWidget}
            />
          </AxMcpAppScreenChrome>
        ) : null}

        <div
          ref={composerRef}
          data-composer-root="true"
          className={cn(
            "fixed inset-x-0 bottom-0 px-4 pb-4 pt-3 sm:px-6 lg:px-8",
            isDarkMode
              ? "bg-gradient-to-t from-[#040914] from-80% to-transparent"
              : "bg-gradient-to-t from-slate-50 from-80% to-transparent",
            activeMcpAppPanel || helperExpanded || mentionResults.length > 0
              ? "z-[70]"
              : "z-20",
          )}
        >
          <div className="mx-auto w-full max-w-5xl">
            <div className="flex justify-center">
              <button
                type="button"
                onClick={() => setHelperExpanded((value) => !value)}
                className={cn(
                  "group relative z-[21] -mb-px inline-flex items-center justify-center rounded-t-md border border-b-0 px-14 py-0 leading-none shadow-[0_-10px_30px_-18px_rgba(251,191,36,0.7)] backdrop-blur-2xl transition hover:border-amber-200/55",
                  isDarkMode
                    ? "border-amber-300/30 bg-slate-950/95 text-amber-100/80 hover:text-white"
                    : "border-amber-300/40 bg-white/95 text-amber-700 hover:text-amber-900",
                )}
                style={{ height: 14, minHeight: 0 }}
                aria-label={helperExpanded ? "Close launcher" : "Open launcher"}
                title={helperExpanded ? "Close launcher" : "Open launcher"}
              >
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-x-4 top-0 h-px rounded-full bg-gradient-to-r from-transparent via-amber-300/85 to-transparent"
                />
                <span
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-x-6 bottom-0 h-[3px] rounded-full bg-gradient-to-r from-amber-400/0 via-amber-300/55 to-amber-400/0 opacity-80 transition group-hover:opacity-100"
                />
                {helperExpanded ? (
                  <ChevronDown className="h-2.5 w-2.5" />
                ) : (
                  <ChevronUp className="h-2.5 w-2.5" />
                )}
              </button>
            </div>
            <div
              className={cn(
                "relative rounded-[30px] border p-4 shadow-[0_28px_90px_-36px_rgba(2,8,23,1)] backdrop-blur-2xl",
                isDarkMode
                  ? "border-cyan-300/20 bg-slate-950/95"
                  : "border-slate-200 bg-white/95 shadow-[0_18px_50px_-28px_rgba(15,23,42,0.22)]",
              )}
            >
              {helperExpanded ? (
                <button
                  type="button"
                  onClick={() => setHelperExpanded(false)}
                  className={cn(
                    "absolute right-4 top-4 z-[22] inline-flex h-9 w-9 items-center justify-center rounded-lg border transition",
                    isDarkMode
                      ? "border-white/10 bg-white/[0.04] text-slate-300 hover:border-cyan-300/35 hover:text-white"
                      : "border-slate-200 bg-white text-slate-700 shadow-sm hover:border-slate-300 hover:text-slate-950",
                  )}
                  aria-label="Close launcher panel"
                  title="Close launcher"
                >
                  <X className="h-4 w-4" />
                </button>
              ) : null}
              {helperExpanded ? (
                <div className="mb-3 pr-12">
                  <div
                    className={cn(
                      "text-[11px] uppercase tracking-[0.24em]",
                      isDarkMode ? "text-cyan-100/80" : "text-cyan-700",
                    )}
                  >
                    Workspace launcher
                  </div>
                  <p
                    className={cn(
                      "mt-1 text-sm",
                      isDarkMode ? "text-slate-300" : "text-slate-600",
                    )}
                  >
                    Open tasks, agents, spaces, and context. Use @agentname in a message to choose a recipient.
                  </p>
                </div>
              ) : null}

              {helperExpanded ? (
                <div
                  className={cn(
                    "mb-3 mt-3 max-h-[60vh] overflow-y-auto rounded-[24px] border px-4 py-4",
                    isDarkMode
                      ? "border-white/10 bg-white/[0.03]"
                      : "border-slate-200 bg-white/95 shadow-[0_18px_45px_-32px_rgba(15,23,42,0.35)]",
                  )}
                >
                  <div
                    className={cn(
                      "mb-3 flex items-center justify-between gap-3 border-b pb-3",
                      isDarkMode ? "border-white/[0.06]" : "border-slate-200",
                    )}
                  >
                    <div>
                      <div
                        className={cn(
                          "text-[11px] uppercase tracking-[0.18em]",
                          isDarkMode ? "text-amber-100/80" : "text-amber-700",
                        )}
                      >
                        Widgets
                      </div>
                      <p
                        className={cn(
                          "mt-1 text-xs",
                          isDarkMode ? "text-slate-400" : "text-slate-600",
                        )}
                      >
                        Open one private app panel without leaving the composer.
                      </p>
                    </div>
                    <div
                      className={cn(
                        "rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
                        isDarkMode
                          ? "border-amber-300/20 bg-amber-400/10 text-amber-100/90"
                          : "border-amber-300 bg-amber-50 text-amber-800",
                      )}
                    >
                      Tap to expand
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                    {launcherItems.map((item) => {
                      const Icon = item.icon;
                      const registryEntry = item.toolCandidates
                        .map(
                          (candidate) => mcpToolRegistryQuery.data?.[candidate],
                        )
                        .find(Boolean);
                      const isLaunching = activeLauncherItemId === item.id;
                      const isOpen = openLauncherItemIds.has(item.id);
                      const isDisabled =
                        !currentSpaceId ||
                        !registryEntry ||
                        mcpToolRegistryQuery.isLoading ||
                        isLaunching;
                      const launcherDescription = item.description;

                      return (
                        <div key={item.id} className="relative">
                          <button
                            type="button"
                            onClick={() => void launchLauncherItem(item)}
                            disabled={isDisabled}
                            className={cn(
                              "group relative flex w-full flex-col items-center gap-1.5 rounded-xl border p-3 text-center transition-all",
                              isDisabled
                                ? isDarkMode
                                  ? "cursor-not-allowed border-white/[0.04] bg-white/[0.02] opacity-60"
                                  : "cursor-not-allowed border-slate-200 bg-slate-100 opacity-60"
                                : isOpen
                                  ? isDarkMode
                                    ? "border-amber-300/40 bg-amber-400/[0.06] shadow-[0_12px_30px_-24px_rgba(251,191,36,0.95)] hover:border-amber-200/55 hover:bg-amber-400/[0.1]"
                                    : "border-amber-300/45 bg-amber-50 text-amber-950 shadow-[0_12px_30px_-24px_rgba(245,158,11,0.35)] hover:border-amber-400/60 hover:bg-amber-100"
                                  : isDarkMode
                                    ? "border-white/[0.06] bg-white/[0.02] hover:border-cyan-300/20 hover:bg-white/[0.05]"
                                    : "border-slate-200 bg-white hover:border-cyan-300/45 hover:bg-cyan-50/70",
                            )}
                            title={
                              registryEntry
                                ? isOpen
                                  ? `${launcherDescription} • panel open`
                                  : launcherDescription
                                : "This app is not available in the current MCP registry yet."
                            }
                            aria-pressed={isOpen}
                          >
                            <div
                              className={cn(
                                "flex h-9 w-9 items-center justify-center rounded-lg border transition-colors",
                                isDisabled
                                  ? isDarkMode
                                    ? "border-white/[0.04] bg-white/[0.04] text-slate-500"
                                    : "border-slate-200 bg-slate-100 text-slate-500"
                                  : isOpen
                                    ? isDarkMode
                                      ? "border-amber-300/30 bg-amber-300/12 text-amber-100"
                                      : "border-amber-300 bg-amber-100 text-amber-700"
                                    : isDarkMode
                                      ? "border-white/[0.05] bg-white/[0.04] text-slate-400 group-hover:text-cyan-300"
                                      : "border-slate-200 bg-slate-50 text-slate-600 group-hover:border-cyan-300 group-hover:bg-cyan-50 group-hover:text-cyan-700",
                              )}
                            >
                              {isLaunching ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                <Icon className="h-4 w-4" />
                              )}
                            </div>
                            <span
                              className={cn(
                                "text-[11px] font-semibold leading-tight",
                                isDisabled
                                  ? isDarkMode
                                    ? "text-slate-500"
                                    : "text-slate-500"
                                  : isOpen
                                    ? isDarkMode
                                      ? "text-amber-100"
                                      : "text-amber-950"
                                    : isDarkMode
                                      ? "text-slate-200"
                                      : "text-slate-900",
                              )}
                            >
                              {item.title}
                            </span>
                            <span
                              className={cn(
                                "text-[10px] font-medium uppercase tracking-[0.16em]",
                                isOpen
                                  ? isDarkMode
                                    ? "text-amber-100/75"
                                    : "text-amber-700"
                                  : isDarkMode
                                    ? "text-slate-500"
                                    : "text-slate-600",
                              )}
                            >
                              {isOpen ? "Active" : "Launch"}
                            </span>
                          </button>
                          {isOpen ? (
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setActiveMcpAppPanel(null);
                              }}
                              className={cn(
                                "absolute -right-1.5 -top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full border shadow-lg transition",
                                isDarkMode
                                  ? "border-rose-200/25 bg-slate-950 text-rose-100 hover:border-rose-200/45 hover:bg-rose-400/15"
                                  : "border-rose-200 bg-white text-rose-700 hover:border-rose-300 hover:bg-rose-50",
                              )}
                              aria-label={`Close ${item.title} app panel`}
                              title="Close panel"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>

                  {shouldShowExpandedMentionShortcuts(
                    agentRailPinned,
                    quickActionAgents.length,
                  ) && (
                    <div
                      className={cn(
                        "mt-4 border-t pt-3",
                        isDarkMode ? "border-white/[0.06]" : "border-slate-200",
                      )}
                    >
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <div>
                          <div
                            className={cn(
                              "text-[11px] uppercase tracking-[0.18em]",
                              isDarkMode ? "text-slate-400" : "text-slate-700",
                            )}
                          >
                            @ mentions
                          </div>
                          <p
                            className={cn(
                              "mt-1 text-xs",
                              isDarkMode ? "text-slate-500" : "text-slate-600",
                            )}
                          >
                            Tap to add or remove agents from your routing set.
                          </p>
                        </div>
                        {!agentRailPinned ? (
                          <button
                            type="button"
                            data-testid="ax-rail-pin-button"
                            onClick={() => setAgentRailPinned(true)}
                            className={cn(
                              "text-[11px] font-semibold uppercase tracking-[0.16em] transition",
                              isDarkMode
                                ? "text-cyan-200/80 hover:text-white"
                                : "text-cyan-700 hover:text-cyan-900",
                            )}
                          >
                            Pin selector
                          </button>
                        ) : null}
                        {agentsLauncherItem ? (
                          <button
                            type="button"
                            onClick={() =>
                              void launchLauncherItem(agentsLauncherItem)
                            }
                            disabled={
                              activeLauncherItemId === agentsLauncherItem.id ||
                              mcpToolRegistryQuery.isLoading ||
                              !agentsLauncherItem.toolCandidates.some(
                                (candidate) =>
                                  mcpToolRegistryQuery.data?.[candidate],
                              )
                            }
                            className={cn(
                              "text-[11px] font-semibold uppercase tracking-[0.16em] transition disabled:cursor-not-allowed",
                              isDarkMode
                                ? "text-cyan-200/80 hover:text-white disabled:text-slate-500"
                                : "text-cyan-700 hover:text-cyan-900 disabled:text-slate-400",
                            )}
                          >
                            All agents
                          </button>
                        ) : null}
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {quickActionAgents.map((agent) => {
                          const normalizedHandle =
                            normalizeMentionHandle(agent.handle) ||
                            agent.handle;
                          const isSelected = isRoutingToConcierge
                            ? normalizedHandle === AX_CONCIERGE_HANDLE
                            : defaultAgentHandles.some(
                                (selectedHandle) =>
                                  selectedHandle.toLowerCase() ===
                                  normalizedHandle.toLowerCase(),
                              );

                          return (
                            <button
                              key={agent.id}
                              data-testid={`ax-route-chip-${normalizedHandle}`}
                              type="button"
                              onClick={() => {
                                // Keep the launcher open while toggling so
                                // multi-selection works the same as the rail
                                // (closing per click reads as a switcher).
                                if (normalizedHandle === AX_CONCIERGE_HANDLE) {
                                  resetDefaultAgentRoute();
                                  return;
                                }
                                selectDefaultAgentRoute(normalizedHandle);
                              }}
                              title={agent.capabilitySummary}
                              className={cn(
                                "flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition",
                                isSelected
                                  ? isDarkMode
                                    ? "border-cyan-300/35 bg-cyan-400/10 text-white"
                                    : "border-cyan-300 bg-cyan-50 text-cyan-900"
                                  : isDarkMode
                                    ? "border-white/[0.06] bg-white/[0.02] hover:border-cyan-300/20 hover:bg-white/[0.05]"
                                    : "border-slate-200 bg-white text-slate-800 hover:border-cyan-300 hover:bg-cyan-50",
                              )}
                            >
                              <span className="text-sm leading-none">
                                {agent.emoji}
                              </span>
                              <span
                                className={cn(
                                  "font-semibold",
                                  isSelected
                                    ? isDarkMode
                                      ? "text-white"
                                      : "text-cyan-950"
                                    : isDarkMode
                                      ? "text-slate-300"
                                      : "text-slate-900",
                                )}
                              >
                                {normalizedHandle === AX_CONCIERGE_HANDLE
                                  ? "@Waystation"
                                  : `@${agent.handle}`}
                              </span>
                              {isSelected ? (
                                <span
                                  className={cn(
                                    "inline-flex h-4 w-4 items-center justify-center rounded-full border",
                                    isDarkMode
                                      ? "border-cyan-300/35 bg-cyan-300/10 text-cyan-100"
                                      : "border-cyan-300 bg-white text-cyan-700",
                                  )}
                                >
                                  <Check
                                    className="h-2.5 w-2.5"
                                    strokeWidth={3}
                                    aria-hidden="true"
                                  />
                                </span>
                              ) : (
                                <span
                                  className="h-1.5 w-1.5 rounded-full"
                                  style={{
                                    backgroundColor:
                                      AVAILABILITY_META[agent.availability].dot,
                                  }}
                                  title={
                                    AVAILABILITY_META[agent.availability].text
                                  }
                                />
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              ) : null}

              {replyTarget ? (
                <div
                  data-testid="ax-reply-target"
                  className="mb-3 mt-3 flex items-start justify-between gap-3 rounded-[20px] border border-cyan-200 bg-cyan-50 px-4 py-3 text-gray-900 dark:border-cyan-300/25 dark:bg-cyan-950 dark:text-white"
                >
                  <div className="min-w-0">
                    <div className="text-[11px] uppercase tracking-[0.2em] text-cyan-700 dark:text-cyan-50">
                      Replying to {replyTarget.label}
                    </div>
                    <div className="mt-1 truncate text-sm text-gray-900 dark:text-white">
                      {replyTarget.content || "Selected message"}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setReplyTarget(null)}
                    className="rounded-full border border-cyan-200 bg-white px-3 py-1.5 text-xs text-gray-700 transition hover:border-cyan-300 hover:text-gray-900 dark:border-cyan-200/30 dark:bg-slate-950/70 dark:text-cyan-50 dark:hover:border-cyan-300/40 dark:hover:bg-cyan-900/60 dark:hover:text-white"
                  >
                    Cancel
                  </button>
                </div>
              ) : null}

              {forwardTarget ? (
                <div
                  data-testid="ax-forward-target"
                  className="mb-3 mt-3 flex items-start justify-between gap-3 rounded-[20px] border border-amber-200 bg-amber-50 px-4 py-3 text-gray-900 dark:border-amber-300/30 dark:bg-amber-950 dark:text-white"
                >
                  <div className="min-w-0">
                    <div className="text-[11px] uppercase tracking-[0.2em] text-amber-700 dark:text-amber-50">
                      Sharing {forwardTarget.title}
                    </div>
                    {forwardTarget.summary ? (
                      <div className="mt-1 line-clamp-2 text-sm text-gray-900 dark:text-white">
                        {forwardTarget.summary}
                      </div>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => setForwardTarget(null)}
                    className="rounded-full border border-amber-200 bg-white px-3 py-1.5 text-xs text-gray-700 transition hover:border-amber-300 hover:text-gray-900 dark:border-amber-200/30 dark:bg-slate-950/70 dark:text-amber-50 dark:hover:border-amber-300/40 dark:hover:bg-amber-900/60 dark:hover:text-white"
                    data-testid="ax-forward-target-cancel"
                  >
                    Cancel
                  </button>
                </div>
              ) : null}

              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => {
                  queueFiles(event.target.files, "picker");
                  event.currentTarget.value = "";
                }}
              />

              <ComposerAgentRail
                agents={railAgents}
                pinned={agentRailPinned}
                onToggleAgent={(handle) => selectDefaultAgentRoute(handle)}
                onTogglePinned={() => setAgentRailPinned((value) => !value)}
                isDarkMode={isDarkMode}
              />

              <div className="relative">
                <textarea
                  ref={composerInputRef}
                  data-testid="ax-composer-input"
                  rows={1}
                  defaultValue={draftRef.current}
                  onChange={(event) => {
                    const nextDraft = event.target.value;
                    draftRef.current = nextDraft;
                    syncComposerInputHeight();
                    syncComposerDraftUiState(nextDraft);
                    scheduleComposerDraftPersist(nextDraft);
                  }}
                  onPaste={(event) => {
                    const files = event.clipboardData?.files;
                    if (files?.length) {
                      queueFiles(files, "paste");
                    }
                  }}
                  onKeyDown={(event) => {
                    // Mention dropdown keyboard navigation
                    if (mentionResults.length > 0) {
                      if (event.key === "ArrowDown") {
                        event.preventDefault();
                        setSelectedMentionIndex((i) =>
                          i < mentionResults.length - 1 ? i + 1 : 0,
                        );
                        return;
                      }
                      if (event.key === "ArrowUp") {
                        event.preventDefault();
                        setSelectedMentionIndex((i) =>
                          i > 0 ? i - 1 : mentionResults.length - 1,
                        );
                        return;
                      }
                      if (
                        event.key === "Tab" ||
                        (event.key === "Enter" && !event.shiftKey)
                      ) {
                        event.preventDefault();
                        if (multiSelectedHandles.length > 0) {
                          applyMultiMentionSelection();
                          setSelectedMentionIndex(0);
                          return;
                        }
                        const selected = mentionResults[selectedMentionIndex];
                        if (selected) {
                          insertMention(selected.handle);
                          setSelectedMentionIndex(0);
                        }
                        return;
                      }
                      if (event.key === "Escape") {
                        event.preventDefault();
                        setMentionQuery(null);
                        setSelectedMentionIndex(0);
                        return;
                      }
                    }
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void submit();
                    }
                  }}
                  aria-label={
                    defaultAgentHandle
                      ? `Message @${currentRouteProfile.handle} by default. Use @agentname to override.`
                      : `Write to this space. Use @agentname to message an agent.`
                  }
                  className={cn(
                    "min-h-[64px] w-full resize-none rounded-[22px] border py-3 pl-4 pr-[88px] text-[15px] outline-none focus:border-cyan-300/40",
                    isDarkMode
                      ? "border-white/10 bg-white/[0.04] text-white"
                      : "border-slate-200 bg-slate-50 text-slate-950",
                  )}
                />

                {!hasDraftText ? (
                  <ComposerRoutingPlaceholder
                    routedAgents={defaultAgentHandles.map((handle) => {
                      const profile =
                        getAgentProfile(handle) ||
                        buildFallbackAgentProfile(handle);
                      return { handle: profile.handle, emoji: profile.emoji };
                    })}
                    conciergeName={agentName}
                    isDarkMode={isDarkMode}
                  />
                ) : null}

                <div className="absolute bottom-2 right-2 flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className={cn(
                      "rounded-full p-1.5 transition",
                      isDarkMode
                        ? "text-slate-400 hover:bg-white/[0.08] hover:text-slate-200"
                        : "text-slate-500 hover:bg-slate-100 hover:text-slate-900",
                    )}
                    title="Attach files"
                    aria-label="Attach files"
                  >
                    <Paperclip className="h-4 w-4" />
                  </button>
                  <Button
                    onClick={() => void submit()}
                    disabled={
                      !hasDraft || composerAttachments.some((a) => a.uploading)
                    }
                    data-testid="ax-send-button"
                    className="h-8 w-8 rounded-full bg-[linear-gradient(135deg,#2563eb_0%,#38bdf8_100%)] p-0 text-white transition-all hover:opacity-90 disabled:opacity-20 disabled:grayscale"
                    title="Send message"
                    aria-label="Send message"
                  >
                    <ArrowUp className="h-4 w-4" />
                  </Button>
                </div>

                {mentionResults.length ? (
                  <div
                    className={cn(
                      "absolute bottom-[calc(100%+12px)] left-0 right-0 max-h-[min(50vh,420px)] max-w-full overflow-x-hidden overflow-y-auto rounded-[24px] border p-2 shadow-[0_28px_90px_-36px_rgba(2,8,23,1)] backdrop-blur-2xl",
                      isDarkMode
                        ? "border-cyan-300/20 bg-slate-950/90"
                        : "border-slate-200 bg-white/98",
                    )}
                  >
                    {mentionResults.map((agent, index) => {
                      const isMultiSelected = multiSelectedHandles.some(
                        (handle) =>
                          handle.toLowerCase() === agent.handle.toLowerCase(),
                      );
                      return (
                        <div
                          key={agent.handle}
                          className={cn(
                            "flex w-full min-w-0 items-start gap-1 rounded-[18px] transition",
                            isDarkMode
                              ? "hover:bg-white/[0.05]"
                              : "hover:bg-cyan-50/80",
                            index === selectedMentionIndex
                              ? isDarkMode
                                ? "bg-white/[0.08] ring-1 ring-cyan-400/30"
                                : "bg-cyan-50 ring-1 ring-cyan-300"
                              : "",
                            isMultiSelected
                              ? isDarkMode
                                ? "bg-cyan-400/10"
                                : "bg-cyan-50"
                              : "",
                          )}
                        >
                          <button
                            type="button"
                            role="checkbox"
                            aria-checked={isMultiSelected}
                            aria-label={`Add @${agent.handle} to selection`}
                            data-testid={`ax-mention-toggle-${agent.handle}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              toggleMentionSelection(agent.handle);
                            }}
                            className={cn(
                              "ml-2 mt-3.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border text-[11px] transition",
                              isMultiSelected
                                ? "border-cyan-400 bg-cyan-500 text-white"
                                : isDarkMode
                                  ? "border-white/20 text-transparent hover:border-cyan-300/60"
                                  : "border-slate-300 text-transparent hover:border-cyan-400",
                            )}
                          >
                            ✓
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              if (multiSelectedHandles.length > 0) {
                                toggleMentionSelection(agent.handle);
                                return;
                              }
                              insertMention(agent.handle);
                              setSelectedMentionIndex(0);
                            }}
                            className="flex w-full min-w-0 items-start justify-between gap-3 rounded-[18px] py-3 pl-1 pr-3 text-left"
                          >
                            <div className="min-w-0 flex-1">
                              <div
                                className={cn(
                                  "break-words text-sm font-semibold [overflow-wrap:anywhere]",
                                  isDarkMode ? "text-white" : "text-slate-950",
                                )}
                              >
                                {agent.name}{" "}
                                <span
                                  className={
                                    isDarkMode
                                      ? "text-cyan-100/85"
                                      : "text-cyan-700"
                                  }
                                >
                                  @{agent.handle}
                                </span>
                              </div>
                              <div
                                className={cn(
                                  "mt-1 break-words text-sm [overflow-wrap:anywhere]",
                                  isDarkMode
                                    ? "text-slate-300"
                                    : "text-slate-700",
                                )}
                              >
                                {agent.capabilitySummary}
                              </div>
                            </div>
                            <div
                              className={cn(
                                "max-w-[35%] shrink-0 break-words text-right text-xs uppercase tracking-[0.18em] [overflow-wrap:anywhere]",
                                isDarkMode
                                  ? "text-slate-400"
                                  : "text-slate-600",
                              )}
                            >
                              <div>{agent.location}</div>
                              <div
                                className={cn(
                                  "mt-2 inline-flex items-center justify-end gap-1.5",
                                  isDarkMode
                                    ? "text-cyan-100/80"
                                    : "text-cyan-700",
                                )}
                              >
                                <span
                                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                                  style={{
                                    backgroundColor:
                                      AVAILABILITY_META[agent.availability].dot,
                                  }}
                                />
                                {agent.status}
                              </div>
                            </div>
                          </button>
                        </div>
                      );
                    })}
                    {multiSelectedHandles.length > 0 ? (
                      <button
                        type="button"
                        data-testid="ax-mention-multi-apply"
                        onClick={applyMultiMentionSelection}
                        className={cn(
                          "mt-1 flex w-full items-center justify-center gap-2 rounded-[18px] border px-3 py-2.5 text-sm font-medium transition",
                          isDarkMode
                            ? "border-cyan-300/35 bg-cyan-400/10 text-cyan-50 hover:bg-cyan-400/20"
                            : "border-cyan-300 bg-cyan-50 text-cyan-900 hover:bg-cyan-100",
                        )}
                      >
                        Mention {multiSelectedHandles.length}{" "}
                        {multiSelectedHandles.length === 1 ? "agent" : "agents"}{" "}
                        ⏎
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>

              {composerAttachments.some((a) => a.uploading) && (
                <div className="mt-1 flex items-center gap-2">
                  <span className="text-xs text-cyan-300/70 animate-pulse">
                    Adding attachments to context...
                  </span>
                  {queuedBacklogCount > 0 && (
                    <span
                      data-testid="ax-queue-count"
                      className="text-xs uppercase tracking-[0.18em] text-cyan-100/75"
                    >
                      {queuedBacklogCount} queued
                    </span>
                  )}
                </div>
              )}

              {composerAttachments.length ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  {composerAttachments.map((attachment) => {
                    const fileTypeLabel = formatAttachmentFileType(
                      attachment.contentType,
                      attachment.filename,
                    );
                    const statusLabel = attachment.uploading
                      ? "Adding to context..."
                      : attachment.error
                        ? attachment.error
                        : "Will add to context on send";

                    return (
                      <button
                        key={attachment.localId}
                        type="button"
                        onClick={() => {
                          if (attachment.previewUrl)
                            URL.revokeObjectURL(attachment.previewUrl);
                          setComposerAttachments((current) => {
                            const next = current.filter(
                              (a) => a.localId !== attachment.localId,
                            );
                            setAttachedFiles(next.map((a) => a.filename));
                            return next;
                          });
                        }}
                        className={cn(
                          "group flex max-w-full items-center gap-2 rounded-2xl border px-2.5 py-2 text-left transition hover:text-white",
                          attachment.error
                            ? "border-red-400/30 bg-red-400/10 text-red-300"
                            : attachment.uploading
                              ? "border-cyan-300/20 bg-cyan-400/5 text-cyan-100"
                              : "border-white/10 bg-white/[0.045] text-slate-200 hover:border-cyan-300/35",
                        )}
                        title="Remove attachment"
                      >
                        {attachment.previewUrl ? (
                          <img
                            src={attachment.previewUrl}
                            alt={attachment.filename}
                            className="h-9 w-9 shrink-0 rounded-xl object-cover"
                          />
                        ) : (
                          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04]">
                            <Paperclip className="h-4 w-4" />
                          </div>
                        )}
                        <div className="min-w-0">
                          <div className="flex min-w-0 items-center gap-1.5">
                            <span className="truncate text-sm font-medium text-white">
                              {attachment.filename}
                            </span>
                            <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-cyan-300/20 bg-cyan-400/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-cyan-100">
                              <Globe2 className="h-2.5 w-2.5" />
                              Context
                            </span>
                          </div>
                          <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[11px] text-slate-400">
                            {attachment.previewUrl ? (
                              <ImageIcon className="h-3 w-3 shrink-0" />
                            ) : (
                              <Paperclip className="h-3 w-3 shrink-0" />
                            )}
                            <span className="shrink-0">{fileTypeLabel}</span>
                            <span className="text-white/20">·</span>
                            <span className="truncate">{statusLabel}</span>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
