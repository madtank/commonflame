import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Button } from "@/components/ui/button";
import {
  ACTIVITY_STREAM_STATUS_DONE_CLASSNAME,
  ACTIVITY_STREAM_STATUS_ERROR_CLASSNAME,
  ACTIVITY_STREAM_STATUS_PENDING_CLASSNAME,
  ACTIVITY_STREAM_STATUS_WORKING_CLASSNAME,
} from "@/components/ax-platform/activity-stream-tokens";
import { cn } from "@/lib/utils";
import {
  getSpaceAgentToolCall,
  proxyMcpResourceRead,
  proxyMcpToolCall,
  type SpaceAgentResolvedWidgetResource,
  type SpaceAgentWidgetDescriptor,
} from "@/lib/space-agent-api";
import { SPACE_AGENT_WORKFLOW_REGISTRY } from "@/lib/space-agent-workflow-registry";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ExternalLink,
  FileImage,
  FileText,
  LoaderCircle,
  Minimize2,
  UserRound,
  X,
} from "lucide-react";
import {
  postWidgetFrameMessage,
  setWidgetOpenAiGlobals,
  setWidgetFrameTheme,
  type WidgetFrameWindow,
} from "./widget-frame-bridge";
import {
  getWidgetHeightTransition,
  WIDGET_HEIGHT_TRANSITION_GROW,
} from "./widget-height";
import {
  resolveWidgetResource,
  clearWidgetCacheEntry,
} from "./widget-resource-resolver";
import { shouldShowHostNotice } from "@/lib/widget-display-rules";
import { ContextCatalogTrustedActionLane } from "@/components/ax-platform/shell/ContextCatalogTrustedActionLane";
import {
  extractContextCatalogEntryFromWidget,
  invokeContextCatalogAction,
} from "@/lib/context-catalog";
import {
  formatTimestamp,
  formatAbsoluteTimestamp,
} from "@/components/ax-platform/shell/transcript-model";
import {
  extractWidgetDetailContent,
  extractWidgetDetailLabel,
  getCollapsedWidgetPreview,
  normalizeWidgetNoticeText,
} from "@/components/ax-platform/widget-fold";
import { appendAgentMentionToCompose } from "@/lib/agent-compose";
import { getStoredThemeState, THEME_CHANGE_EVENT } from "@/lib/theme";

type AxMcpAppWidgetProps = {
  messageId: string;
  spaceId: string;
  widget: SpaceAgentWidgetDescriptor;
  timestamp?: string | null;
  /** When true, skip IntersectionObserver gating and resolve immediately. */
  forceMount?: boolean;
  /** Panel mode disables transcript-only folding/parking behaviors. */
  panelMode?: boolean;
  /** Quick actions should wait for their user-authored result before booting the app iframe. */
  deferFrameUntilToolResult?: boolean;
  hostCommand?: { type: string; nonce: number } | null;
  onClose?: () => void;
  onOpenDirectHtmlPanel?: (payload: {
    title: string;
    html: string;
    key?: string | null;
  }) => void;
};

type WidgetHostNotice = {
  tone: "info" | "error";
  text: string;
} | null;

type WidgetBridgeMessage = {
  id?: string | number | null;
  jsonrpc?: string;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: unknown;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown) {
  return typeof value === "string" ? value : null;
}

function sanitizeSystemActorLabel(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  if (/^(user:)?__system__$/i.test(trimmed)) return "System";
  return value;
}

function sanitizeWidgetActorFields(
  value: unknown,
  seen = new WeakSet<object>(),
): unknown {
  if (!value || typeof value !== "object") return value;
  if (seen.has(value)) return value;
  seen.add(value);
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeWidgetActorFields(item, seen));
  }
  const source = value as Record<string, unknown>;
  let changed = false;
  const next: Record<string, unknown> = {};
  for (const [key, fieldValue] of Object.entries(source)) {
    const normalizedKey = key.toLowerCase();
    const sanitizedValue = [
      "author",
      "user",
      "created_by",
      "uploaded_by",
      "updated_by",
    ].includes(normalizedKey)
      ? sanitizeSystemActorLabel(fieldValue)
      : sanitizeWidgetActorFields(fieldValue, seen);
    next[key] = sanitizedValue;
    if (sanitizedValue !== fieldValue) changed = true;
  }
  return changed ? next : value;
}

function normalizeFrameToolResult(
  value: unknown,
): Record<string, unknown> | null {
  const record = asObject(value);
  if (!record) return null;

  if (record.structuredContent || record.content) {
    return sanitizeWidgetActorFields(record) as Record<string, unknown>;
  }

  if (record.structured_content) {
    return sanitizeWidgetActorFields({
      ...record,
      structuredContent: record.structured_content,
    }) as Record<string, unknown>;
  }

  // Backend widget metadata stores durable initial_data as the raw
  // structuredContent object. The iframe widgets expect the MCP tool-result
  // envelope, so wrap it once at the host boundary.
  return sanitizeWidgetActorFields({
    structuredContent: record,
  }) as Record<string, unknown>;
}

function extractToolCallInitialData(
  value: unknown,
): Record<string, unknown> | null {
  const record = asObject(value);
  if (!record) return null;

  return normalizeFrameToolResult(
    record.initial_data ??
      record.structuredContent ??
      record.structured_content,
  );
}

function isDurableToolCallId(value: string | null | undefined) {
  const id = (value || "").trim();
  if (!id) return false;

  // Launcher panels create local UI IDs such as "mcp-panel-agents-...:tool".
  // Those are not backend tool-call records and must not be hydrated through
  // /api/v1/tool-calls/{id}; only durable backend IDs belong on that path.
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    id,
  );
}

const TOOL_CALL_HYDRATION_RETRY_DELAYS_MS = [120, 240, 480, 960, 1500];

function waitForToolCallHydrationRetry(delayMs: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, delayMs);
  });
}

function getFriendlyErrorMessage(error: unknown, fallback: string) {
  const raw = error instanceof Error ? error.message : null;
  return normalizeWidgetNoticeText(raw) || fallback;
}

function isBrowserRenderableUrl(value: string | null | undefined) {
  if (!value) return false;
  return (
    value.startsWith("/") ||
    value.startsWith("http://") ||
    value.startsWith("https://") ||
    value.startsWith("data:") ||
    value.startsWith("blob:")
  );
}

function isFrameHostSource(src: string | undefined) {
  if (!src) return false;
  return (
    src === "/ax-mcp-frame-host.html" ||
    src.startsWith("/ax-mcp-frame-host.html?")
  );
}

const FRAME_HOST_VERSION =
  (import.meta.env.VITE_COMMIT_SHA as string | undefined)?.slice(0, 12) ||
  "local";
const MAX_INLINE_FRAME_TOOL_RESULT_CHARS = 500_000;

function buildFrameHostSrc(payloadKey?: string | null) {
  const params = new URLSearchParams({ v: FRAME_HOST_VERSION });
  if (payloadKey) {
    params.set("payloadKey", payloadKey);
  }
  return `/ax-mcp-frame-host.html?${params.toString()}`;
}

function encodeFrameHostPayload(value: unknown) {
  if (typeof window === "undefined") return "";

  try {
    return window.btoa(unescape(encodeURIComponent(JSON.stringify(value))));
  } catch {
    return "";
  }
}

function encodeFrameHostHtml(html: string) {
  if (typeof window === "undefined") return "";

  try {
    return window.btoa(unescape(encodeURIComponent(html)));
  } catch {
    return "";
  }
}

function buildFrameHostPayloadStorageKey(
  widgetInstanceKey: string,
  length: number,
) {
  const safeInstanceKey = widgetInstanceKey.replace(/[^a-zA-Z0-9_-]/g, "-");
  return `ax-mcp-frame-${safeInstanceKey}-${length}`;
}

function storeFrameHostPayload(key: string, payloadName: string) {
  if (typeof window === "undefined") return false;
  try {
    window.sessionStorage.setItem(`ax-mcp-frame:${key}`, payloadName);
    return true;
  } catch (error) {
    console.warn("[AxMcpAppWidget] frame payload storage failed", {
      payloadKey: key,
      payloadLength: payloadName.length,
      errorName: error instanceof Error ? error.name : typeof error,
      errorMessage: error instanceof Error ? error.message : String(error),
    });
    return false;
  }
}

function readFrameHostPayload(key: string | null | undefined) {
  if (!key || typeof window === "undefined") return null;
  try {
    const payloadName = window.sessionStorage.getItem(`ax-mcp-frame:${key}`);
    return isFrameHostPayloadName(payloadName) ? payloadName : null;
  } catch {
    return null;
  }
}

function getFrameHostPayloadKey(src: string | undefined) {
  if (!src || !isFrameHostSource(src) || typeof window === "undefined") {
    return null;
  }

  try {
    return new URL(src, window.location.origin).searchParams.get("payloadKey");
  } catch {
    return null;
  }
}

function isFrameHostPayloadName(value: string | null | undefined) {
  return (
    typeof value === "string" &&
    (value.startsWith("ax-mcp-html:") || value.startsWith("ax-mcp-html-v2:"))
  );
}

function buildLifecycleLabel(lifecycle?: string | null) {
  switch ((lifecycle || "").toLowerCase()) {
    case "complete":
      return "Ready";
    case "approval_required":
      return "Needs Review";
    case "error":
      return "Error";
    case "ack":
    case "progress":
    case "working":
    case "":
      return "Loading";
    default:
      return lifecycle!.replace(/_/g, " ");
  }
}

function getLifecycleTone(lifecycle?: string | null) {
  switch (lifecycle) {
    case "complete":
      return ACTIVITY_STREAM_STATUS_DONE_CLASSNAME;
    case "error":
      return ACTIVITY_STREAM_STATUS_ERROR_CLASSNAME;
    case "approval_required":
      return ACTIVITY_STREAM_STATUS_PENDING_CLASSNAME;
    case "ack":
    case "progress":
    case "working":
    default:
      return ACTIVITY_STREAM_STATUS_WORKING_CLASSNAME;
  }
}

type ModelContextChip = {
  title: string;
  type: string | null;
  source: string | null;
  preview: string | null;
};

type ModelContextPreview = {
  text: string | null;
  chips: ModelContextChip[];
};

function stringifyModelContextValue(value: unknown) {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value == null) return null;

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "Model context updated";
  }
}

function compactPreview(value: string | null, maxLength = 180) {
  if (!value) return null;
  const compact = value.replace(/\s+/g, " ").trim();
  if (!compact) return null;
  return compact.length > maxLength
    ? `${compact.slice(0, maxLength - 1).trimEnd()}…`
    : compact;
}

function extractModelContextChip(value: unknown): ModelContextChip | null {
  const record = asObject(value);
  if (!record) return null;

  const title = pickFirstString(record, [
    "title",
    "name",
    "filename",
    "label",
    "id",
    "uri",
  ]);
  const preview = compactPreview(
    pickFirstString(record, [
      "preview",
      "summary",
      "description",
      "snippet",
      "text",
      "content",
    ]) || stringifyModelContextValue(record.value),
  );
  const type = pickFirstString(record, [
    "artifact_type",
    "type",
    "file_type",
    "content_type",
    "mime_type",
  ]);
  const source = pickFirstString(record, [
    "source",
    "server",
    "tool_name",
    "tool",
    "resource_uri",
  ]);

  if (!title && !preview) return null;

  return {
    title: title || "Context item",
    type,
    source,
    preview,
  };
}

function extractModelContextChips(value: unknown): ModelContextChip[] {
  const root = asObject(value);
  if (!root) return [];

  const candidates: unknown[] = [];
  for (const key of [
    "entries",
    "items",
    "results",
    "contexts",
    "context",
    "selected",
    "selected_context",
    "selectedContext",
    "modelContext",
    "structuredContent",
  ]) {
    const candidate = root[key];
    if (Array.isArray(candidate)) candidates.push(...candidate);
    else if (candidate) candidates.push(candidate);
  }
  if (!candidates.length) candidates.push(value);

  const unique = new Map<string, ModelContextChip>();
  for (const candidate of candidates) {
    const chip = extractModelContextChip(candidate);
    if (!chip) continue;
    unique.set(
      `${chip.title}-${chip.type || ""}-${chip.source || ""}-${chip.preview || ""}`,
      chip,
    );
  }
  return Array.from(unique.values()).slice(0, 6);
}

function getModelContextPreview(value: unknown): ModelContextPreview | null {
  if (value == null) return null;

  const chips = extractModelContextChips(value);
  const text = stringifyModelContextValue(value);
  if (!text && !chips.length) return null;
  return { text, chips };
}

type ContextUploadSummary = {
  filename: string;
  fileType: string;
  uploader: string;
  uploadedAt: string | null;
};

function isContextWidget(widget: SpaceAgentWidgetDescriptor) {
  const resourceUri = (widget.resource_uri || "").toLowerCase();
  const toolName = (widget.tool_name || "").toLowerCase();
  return resourceUri.includes("context") || toolName.startsWith("context");
}

function asObject(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function pickFirstString(
  record: Record<string, unknown> | null,
  keys: string[],
) {
  if (!record) return null;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function parseContextUploadSummary(
  value: unknown,
): ContextUploadSummary | null {
  const record = asObject(value);
  if (!record) return null;

  const nestedValue =
    typeof record.value === "string"
      ? (() => {
          try {
            return JSON.parse(record.value);
          } catch {
            return null;
          }
        })()
      : asObject(record.value);
  const payload = asObject(nestedValue) || record;
  const type = pickFirstString(payload, ["type", "entry_type"]);
  const filename = pickFirstString(payload, ["filename", "name", "title"]);
  if (type !== "file_upload" || !filename) return null;

  return {
    filename,
    fileType:
      pickFirstString(payload, ["file_type", "content_type", "mime_type"]) ||
      "File",
    uploader:
      pickFirstString(payload, [
        "uploaded_by",
        "author",
        "user",
        "created_by",
      ]) || "Unknown uploader",
    uploadedAt: pickFirstString(payload, [
      "uploaded_at",
      "created_at",
      "timestamp",
    ]),
  };
}

function extractContextUploadSummaries(value: unknown): ContextUploadSummary[] {
  const root = asObject(value);
  if (!root) return [];

  const candidates: unknown[] = [value];
  for (const key of [
    "entries",
    "items",
    "results",
    "content",
    "structuredContent",
  ]) {
    const candidate = root[key];
    if (Array.isArray(candidate)) candidates.push(...candidate);
    else if (candidate) candidates.push(candidate);
  }

  const unique = new Map<string, ContextUploadSummary>();
  for (const candidate of candidates) {
    const summary = parseContextUploadSummary(candidate);
    if (!summary) continue;
    unique.set(`${summary.filename}-${summary.uploadedAt || ""}`, summary);
  }
  return Array.from(unique.values()).slice(0, 4);
}

function debugWidgetHost(...args: unknown[]) {
  if (!import.meta.env.DEV) return;
  console.debug("[AxMcpAppWidget]", ...args);
}

const LAUNCHER_WIDGET_SCROLL_TOP_OFFSET = 24;
const LAUNCHER_WIDGET_SCROLL_BOTTOM_OFFSET = 24;
const LAUNCHER_WIDGET_PARK_DISTANCE_PX = 1400;
const LAUNCHER_WIDGET_PARK_BOTTOM_THRESHOLD_PX = 280;
const WHOAMI_CACHED_REPLAY_ACTIONS = new Set(["", "get", "list", "bootstrap"]);
const TRANSCRIPT_HITL_TOOLS = new Set(["agents", "spaces"]);
const TRANSCRIPT_HITL_DRAFT_ACTIONS = new Set([
  "approve_draft",
  "reject_draft",
  "cancel_draft",
  "dismiss_draft",
  "edit_draft",
  "update_draft",
  "get_draft",
]);
const TRANSCRIPT_VIEWER_READ_ACTIONS_BY_TOOL = new Map<string, Set<string>>([
  ["agents", new Set(["", "list", "get", "search"])],
  ["context", new Set(["", "list", "get"])],
  ["search", new Set(["", "search", "query", "list"])],
  ["spaces", new Set(["", "list", "get", "members", "discover"])],
  ["tasks", new Set(["", "list", "get"])],
]);

function getTranscriptScrollViewport(node: HTMLElement | null) {
  if (!node) return null;
  return node.closest('[data-scroll-viewport="true"]') as HTMLElement | null;
}

function getLauncherWidgetVisibleBounds(scrollViewport: HTMLElement) {
  const viewportRect = scrollViewport.getBoundingClientRect();
  const composerRoot = document.querySelector(
    '[data-composer-root="true"]',
  ) as HTMLElement | null;
  const composerRect = composerRoot?.getBoundingClientRect();
  const safeTop = viewportRect.top + LAUNCHER_WIDGET_SCROLL_TOP_OFFSET;
  const safeBottom =
    Math.min(viewportRect.bottom, composerRect?.top ?? viewportRect.bottom) -
    LAUNCHER_WIDGET_SCROLL_BOTTOM_OFFSET;

  return {
    safeTop,
    safeBottom: Math.max(safeTop + 120, safeBottom),
  };
}

function isLauncherViewportNearBottom(scrollViewport: HTMLElement) {
  const distanceFromBottom =
    scrollViewport.scrollHeight -
    scrollViewport.scrollTop -
    scrollViewport.clientHeight;
  return distanceFromBottom <= LAUNCHER_WIDGET_PARK_BOTTOM_THRESHOLD_PX;
}

function startsWithResourcePrefix(
  resourceUri: string | null | undefined,
  candidate: string,
) {
  if (!resourceUri) return false;
  return resourceUri.toLowerCase().startsWith(candidate.toLowerCase());
}

function isWhoamiToolName(toolName: string | null | undefined) {
  const normalized = (toolName || "").trim().toLowerCase();
  return (
    normalized === "whoami" ||
    normalized.startsWith("whoami.") ||
    normalized === "auth.whoami" ||
    normalized.startsWith("auth.whoami.")
  );
}

function whoamiToolActionFromName(toolName: string | null | undefined) {
  const normalized = (toolName || "").trim().toLowerCase();
  for (const prefix of ["auth.whoami.", "whoami."]) {
    if (normalized.startsWith(prefix)) {
      return normalized.slice(prefix.length);
    }
  }
  return "";
}

function canReplayCachedWhoamiResult(
  toolName: string | null | undefined,
  action: string | null | undefined,
) {
  const normalizedAction = (action || "").trim().toLowerCase();
  if (normalizedAction) {
    return WHOAMI_CACHED_REPLAY_ACTIONS.has(normalizedAction);
  }
  return WHOAMI_CACHED_REPLAY_ACTIONS.has(whoamiToolActionFromName(toolName));
}

function isWhoamiWidgetDescriptor(widget: SpaceAgentWidgetDescriptor) {
  const toolName = (widget.tool_name || "").trim().toLowerCase();
  const resourceUri = (widget.resource_uri || "").trim().toLowerCase();
  return (
    isWhoamiToolName(toolName) ||
    resourceUri.startsWith("ui://whoami") ||
    resourceUri.startsWith("ui://identity-card")
  );
}

function canRunViewerLocalTranscriptCall(
  toolName: string,
  input: Record<string, unknown>,
) {
  const normalizedToolName = toolName.trim().toLowerCase();
  let baseToolName = normalizedToolName;
  let action = (asString(input?.action) || "").trim().toLowerCase();
  const draftId = asString(input?.draft_id)?.trim();

  if (normalizedToolName.includes(".")) {
    const [base, ...rest] = normalizedToolName.split(".");
    baseToolName = base;
    if (!action) action = rest.join(".");
  }

  const canUseHitlToken = Boolean(
    TRANSCRIPT_HITL_TOOLS.has(baseToolName) &&
    TRANSCRIPT_HITL_DRAFT_ACTIONS.has(action) &&
    draftId,
  );
  if (canUseHitlToken) return true;

  // Shared transcript widgets are globally visible, but follow-up reads are
  // still viewer-local iframe interactions. Allow read/navigation calls so
  // users can browse a rendered widget without reopening it from the app panel.
  return Boolean(
    TRANSCRIPT_VIEWER_READ_ACTIONS_BY_TOOL.get(baseToolName)?.has(action),
  );
}

function getWorkflowTitle(
  widget: SpaceAgentWidgetDescriptor,
  resolvedResource: SpaceAgentResolvedWidgetResource | null,
) {
  const resourceUri = widget.resource_uri;
  const toolName = (widget.tool_name || "").trim().toLowerCase();
  const match = SPACE_AGENT_WORKFLOW_REGISTRY.find((workflow) => {
    const toolMatch =
      workflow.toolNames?.some(
        (candidate) => candidate.toLowerCase() === toolName,
      ) || false;
    const resourceMatch =
      workflow.resourceUriPrefixes?.some((candidate) =>
        startsWithResourcePrefix(resourceUri, candidate),
      ) || false;
    return toolMatch || resourceMatch;
  });

  const baseTitle =
    match?.title || resolvedResource?.title || widget.tool_name || "Panel";

  // For detail/create actions, try to extract a specific label from the
  // tool input or result so the widget title reads "Task Detail — Fix login"
  // instead of just "Task Detail".
  const detailLabel = extractWidgetDetailLabel(widget);
  return detailLabel ? `${baseTitle} — ${detailLabel}` : baseTitle;
}

/**
 * Extract a human-readable label from tool_input or tool_result.
 * Looks for common fields like title, name, key, summary.
 * Prioritizes tool_input (what was requested) over tool_result.
 */
const ACTION_LABELS: Record<string, string> = {
  remember: "Saved",
  create: "Created",
  update: "Updated",
  delete: "Removed",
  save: "Saved",
  add: "Added",
  remove: "Removed",
};

function ConfirmationCard({
  action,
  label,
  content,
}: {
  action?: string | null;
  label?: string | null;
  content?: string | null;
}) {
  const actionLabel =
    ACTION_LABELS[(action || "").toLowerCase()] || "Completed";
  return (
    <div className="py-1">
      <div className="flex items-center gap-2">
        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-400/10 text-emerald-300">
          <Check className="h-3 w-3" />
        </div>
        {label ? (
          <span className="text-sm font-medium text-white">{label}</span>
        ) : null}
        <span className="text-xs text-white/30">{actionLabel}</span>
      </div>
      {content ? (
        <div className="mt-2 rounded-xl bg-white/[0.03] px-3 py-2.5 text-[13px] leading-relaxed text-white/60">
          {content}
        </div>
      ) : null}
    </div>
  );
}

export function AxMcpAppWidget({
  messageId,
  spaceId,
  widget,
  timestamp,
  forceMount,
  panelMode = false,
  deferFrameUntilToolResult = false,
  hostCommand = null,
  onClose,
  onOpenDirectHtmlPanel,
}: AxMcpAppWidgetProps) {
  const widgetRootRef = useRef<HTMLElement | null>(null);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const initialPayloadSentRef = useRef(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [resolvedResource, setResolvedResource] =
    useState<SpaceAgentResolvedWidgetResource | null>(null);
  const [resolveError, setResolveError] = useState<string | null>(null);
  const [isResolving, setIsResolving] = useState(false);
  const [iframeReady, setIframeReady] = useState(false);
  const [appReady, setAppReady] = useState(false);
  const [frameHeight, setFrameHeight] = useState(120);
  const [panelFrameHeight, setPanelFrameHeight] = useState(720);
  const frameHeightRef = useRef(120);
  const pendingHeightRef = useRef<number | null>(null);
  const heightRafRef = useRef<number | null>(null);
  const [frameHeightTransition, setFrameHeightTransition] = useState(
    WIDGET_HEIGHT_TRANSITION_GROW,
  );
  // Throttle height updates to one per animation frame (~16ms) so rapid
  // notifyIntrinsicHeight calls from the MCP app don't cause jank.
  const updateFrameHeight = useCallback((next: number) => {
    pendingHeightRef.current = next;
    if (heightRafRef.current != null) return;
    heightRafRef.current = requestAnimationFrame(() => {
      heightRafRef.current = null;
      if (pendingHeightRef.current != null) {
        const nextHeight = pendingHeightRef.current;
        const previousHeight = frameHeightRef.current;
        setFrameHeightTransition(
          getWidgetHeightTransition(previousHeight, nextHeight),
        );
        frameHeightRef.current = nextHeight;
        setFrameHeight(nextHeight);
        pendingHeightRef.current = null;
      }
    });
  }, []);
  const [hostNotice, setHostNotice] = useState<WidgetHostNotice>(null);
  const [modelContext, setModelContext] = useState<unknown>(null);
  const [isNearViewport, setIsNearViewport] = useState(!!forceMount);
  const [isPageVisible, setIsPageVisible] = useState(() => {
    if (typeof document === "undefined") return true;
    return document.visibilityState !== "hidden";
  });
  const [resolveAttempt, setResolveAttempt] = useState(0);
  const [resolveTimedOut, setResolveTimedOut] = useState(false);
  const [widgetTheme, setWidgetTheme] = useState<"light" | "dark">(() =>
    getStoredThemeState().isDarkMode ? "dark" : "light",
  );
  const [iframeMountNonce, setIframeMountNonce] = useState(0);
  const [widgetBridgeTimedOut, setWidgetBridgeTimedOut] = useState(false);
  const [isLauncherParked, setIsLauncherParked] = useState(false);
  const [hydratedToolCallResult, setHydratedToolCallResult] = useState<Record<
    string,
    unknown
  > | null>(null);
  const launcherParkRafRef = useRef<number | null>(null);

  const isLauncherPanel = Boolean(forceMount);
  useEffect(() => {
    if (
      !forceMount ||
      panelMode ||
      isCollapsed ||
      isLauncherParked ||
      !isPageVisible ||
      typeof window === "undefined"
    ) {
      return undefined;
    }

    const root = widgetRootRef.current;
    const scrollViewport = getTranscriptScrollViewport(root);
    if (!root || !scrollViewport) return undefined;

    const maybeParkLauncherWidget = () => {
      if (launcherParkRafRef.current != null) return;
      launcherParkRafRef.current = window.requestAnimationFrame(() => {
        launcherParkRafRef.current = null;
        if (document.activeElement === iframeRef.current) return;
        if (!isLauncherViewportNearBottom(scrollViewport)) return;

        const rootRect = root.getBoundingClientRect();
        const { safeTop } = getLauncherWidgetVisibleBounds(scrollViewport);
        if (rootRect.bottom < safeTop - LAUNCHER_WIDGET_PARK_DISTANCE_PX) {
          setIsLauncherParked(true);
        }
      });
    };

    scrollViewport.addEventListener("scroll", maybeParkLauncherWidget, {
      passive: true,
    });
    window.addEventListener("resize", maybeParkLauncherWidget, {
      passive: true,
    });

    return () => {
      scrollViewport.removeEventListener("scroll", maybeParkLauncherWidget);
      window.removeEventListener("resize", maybeParkLauncherWidget);
      if (launcherParkRafRef.current != null) {
        window.cancelAnimationFrame(launcherParkRafRef.current);
        launcherParkRafRef.current = null;
      }
    };
  }, [forceMount, isCollapsed, isLauncherParked, isPageVisible, panelMode]);

  useEffect(() => {
    if (typeof document === "undefined") return undefined;

    const handleVisibilityChange = () => {
      setIsPageVisible(document.visibilityState !== "hidden");
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const syncTheme = () => {
      setWidgetTheme(getStoredThemeState().isDarkMode ? "dark" : "light");
    };

    const handleThemeChange = () => syncTheme();
    syncTheme();
    window.addEventListener(THEME_CHANGE_EVENT, handleThemeChange);

    return () =>
      window.removeEventListener(THEME_CHANGE_EVENT, handleThemeChange);
  }, []);

  // Lazy-mount transcript widgets near the viewport.
  // Regular transcript widgets intentionally go dormant again when they are
  // offscreen or minimized so background iframes do not keep burning CPU. Do
  // not tear down mounted frames solely because the browser tab is hidden —
  // users expect open MCP apps to remain open after switching tabs.
  useEffect(() => {
    // Launcher-opened widgets bypass lazy-mount gating — they're user-initiated
    // and may be above the current scroll position.
    if (forceMount) {
      setIsNearViewport(true);
      return;
    }

    const root = widgetRootRef.current;
    if (!root || typeof IntersectionObserver === "undefined") {
      setIsNearViewport(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsNearViewport(entry.isIntersecting);
      },
      { rootMargin: "300px 0px", threshold: 0.01 },
    );
    observer.observe(root);
    return () => {
      observer.disconnect();
    };
  }, []);

  const title =
    widget.title &&
    !["request processed", "mcp app"].includes(widget.title.toLowerCase())
      ? widget.title
      : getWorkflowTitle(widget, resolvedResource);
  const lifecycleLabel = buildLifecycleLabel(widget.lifecycle);
  const sourceUrl = isBrowserRenderableUrl(widget.resource_url)
    ? widget.resource_url
    : isBrowserRenderableUrl(resolvedResource?.resource_url)
      ? resolvedResource?.resource_url
      : null;
  const sourceHtml = widget.html || resolvedResource?.html || null;
  const isDirectHtmlArtifact = Boolean(
    sourceHtml &&
    widget.tool_action === "render_html" &&
    !sourceUrl &&
    !widget.resource_uri,
  );
  const sourceKey =
    sourceUrl ||
    sourceHtml ||
    widget.resource_uri ||
    `${widget.tool_name || "widget"}-${messageId}`;
  const shouldActivateWidgetFrame = Boolean(
    !isCollapsed && !isLauncherParked && (forceMount || isNearViewport),
  );
  const shouldShowDormantWidgetPlaceholder = Boolean(
    !isCollapsed &&
    !isLauncherParked &&
    !shouldActivateWidgetFrame &&
    (sourceUrl || sourceHtml || widget.resource_uri),
  );
  const collapsedPreview = useMemo(
    () => getCollapsedWidgetPreview(widget, title),
    [title, widget],
  );
  const widgetToolCallId =
    asString(widget.tool_call_id) || asString(resolvedResource?.tool_call_id);
  const durableWidgetToolCallId = isDurableToolCallId(widgetToolCallId)
    ? widgetToolCallId
    : null;
  const inlineToolResult = useMemo(() => {
    // Backend stores widget data as initial_data; some paths use tool_result
    if (widget.tool_result) return normalizeFrameToolResult(widget.tool_result);
    if (widget.initial_data) {
      return normalizeFrameToolResult(widget.initial_data);
    }
    if (widget.structured_content) {
      return normalizeFrameToolResult({
        structuredContent: widget.structured_content,
      });
    }
    return null;
  }, [widget.structured_content, widget.tool_result, widget.initial_data]);
  const resolvedToolResult = useMemo(() => {
    // Resolve returns the persisted message widget metadata. This covers
    // transcript surfaces that were opened from a thin/stale SSE pointer before
    // the transcript refetch reconciled first-paint initial_data.
    if (resolvedResource?.tool_result) {
      return normalizeFrameToolResult(resolvedResource.tool_result);
    }
    if (resolvedResource?.initial_data) {
      return normalizeFrameToolResult(resolvedResource.initial_data);
    }
    if (resolvedResource?.structured_content) {
      return normalizeFrameToolResult({
        structuredContent: resolvedResource.structured_content,
      });
    }
    return null;
  }, [
    resolvedResource?.initial_data,
    resolvedResource?.structured_content,
    resolvedResource?.tool_result,
  ]);
  const effectiveToolResult =
    inlineToolResult || resolvedToolResult || hydratedToolCallResult;
  const shouldDeferWidgetFrameUntilToolResult = Boolean(
    deferFrameUntilToolResult &&
    shouldActivateWidgetFrame &&
    !effectiveToolResult &&
    (widget.lifecycle === "working" || widget.lifecycle === "loading"),
  );
  const shouldRenderWidgetFrame =
    shouldActivateWidgetFrame && !shouldDeferWidgetFrameUntilToolResult;
  const contextUploadSummaries = useMemo(
    () =>
      isContextWidget(widget)
        ? extractContextUploadSummaries(effectiveToolResult)
        : [],
    [effectiveToolResult, widget],
  );
  const effectiveDisplayMode = panelMode ? "fullscreen" : "inline";
  const effectiveFrameHeight = panelMode ? panelFrameHeight : frameHeight;
  const themedFrameSurfaceClassName =
    widgetTheme === "dark" ? "bg-slate-950" : "bg-white";

  useLayoutEffect(() => {
    if (!panelMode || typeof window === "undefined") return undefined;

    const root = widgetRootRef.current;
    if (!root) return undefined;

    const syncPanelHeight = (height: number) => {
      if (height > 0) {
        setPanelFrameHeight(Math.round(height));
      }
    };
    syncPanelHeight(root.getBoundingClientRect().height);

    if (typeof ResizeObserver !== "function") return undefined;

    const resizeObserver = new ResizeObserver((entries) => {
      const blockSize = entries[0]?.borderBoxSize?.[0]?.blockSize;
      syncPanelHeight(blockSize || entries[0]?.contentRect.height || 0);
    });
    if (
      typeof resizeObserver.observe !== "function" ||
      typeof resizeObserver.disconnect !== "function"
    ) {
      return undefined;
    }
    resizeObserver.observe(root);

    return () => resizeObserver.disconnect();
  }, [panelMode]);

  useEffect(() => {
    setIsCollapsed(false);
  }, [sourceKey]);

  // Stable identity for this widget instance. We only want to re-run the
  // mount-time resets when this actually changes — not every time a derived
  // field (title, sourceKey) shifts after the resource finishes resolving.
  // Previously this effect was wired to title/sourceKey/display_mode/tool_name,
  // which meant the post-resolution state flip would wipe iframeReady and
  // frameHeight at exactly the moment the iframe was finishing its first
  // handshake, stranding launcher widgets in a permanent Loading state
  // (Agents, Identity, etc). See MCP-WIDGETS-UAT-2026-04-09.md section 0.
  const widgetInstanceKey = useMemo(
    () =>
      `${messageId}::${widget.tool_call_id || widget.resource_uri || widget.tool_name || "widget"}`,
    [messageId, widget.tool_call_id, widget.resource_uri, widget.tool_name],
  );
  const iframeRenderKey = `${widgetInstanceKey}:${iframeMountNonce}`;

  useEffect(() => {
    let cancelled = false;
    setHydratedToolCallResult(null);

    if (inlineToolResult || !durableWidgetToolCallId) {
      return () => {
        cancelled = true;
      };
    }

    void (async () => {
      for (
        let attempt = 0;
        attempt <= TOOL_CALL_HYDRATION_RETRY_DELAYS_MS.length;
        attempt += 1
      ) {
        if (attempt > 0) {
          await waitForToolCallHydrationRetry(
            TOOL_CALL_HYDRATION_RETRY_DELAYS_MS[attempt - 1],
          );
        }
        if (cancelled) return;

        try {
          const record = await getSpaceAgentToolCall(durableWidgetToolCallId);
          if (cancelled) return;

          const toolResult = extractToolCallInitialData(record);
          if (toolResult) {
            setHydratedToolCallResult(toolResult);
            return;
          }

          debugWidgetHost("tool-call initial_data not ready", {
            toolCallId: widgetToolCallId,
            attempt,
          });
        } catch (error: unknown) {
          if (cancelled) return;
          debugWidgetHost("tool-call initial_data hydration miss", {
            toolCallId: widgetToolCallId,
            attempt,
            error,
          });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [durableWidgetToolCallId, inlineToolResult, widgetToolCallId]);

  // Going-dormant reset: only clears runtime refs so the next activation
  // can re-run the handshake cleanly. Does NOT touch frameHeight, so a
  // brief visibility blip doesn't cram the widget back to 120px.
  useEffect(() => {
    if (shouldRenderWidgetFrame) {
      return;
    }

    setIframeReady(false);
    setAppReady(false);
    initialPayloadSentRef.current = false;
  }, [shouldRenderWidgetFrame, widgetInstanceKey]);

  useEffect(() => {
    let isCancelled = false;

    if (!shouldActivateWidgetFrame) {
      debugWidgetHost("widget dormant", messageId, {
        forceMount,
        isNearViewport,
        isPageVisible,
        isCollapsed,
      });
      return;
    }

    if (widget.html || widget.resource_url || !widget.resource_uri) {
      setResolvedResource(null);
      setResolveError(null);
      setIsResolving(false);
      return;
    }

    debugWidgetHost(
      "resolving widget resource",
      messageId,
      widget.resource_uri,
    );
    setIsResolving(true);
    setResolveError(null);
    setResolveTimedOut(false);

    const timeoutId = globalThis.setTimeout(() => {
      if (!isCancelled) setResolveTimedOut(true);
    }, 15000);

    void resolveWidgetResource({
      space_id: spaceId,
      message_id: messageId,
      resource_uri: widget.resource_uri!,
      tool_name: widget.tool_name,
      tool_call_id: widget.tool_call_id,
    })
      .then((resource) => {
        if (isCancelled) return;
        debugWidgetHost("widget resource resolved", messageId, {
          hasHtml: !!resource?.html,
          htmlLen: resource?.html?.length,
        });
        setResolvedResource(resource);
        // Clear any stale timeout flag from a slow-but-successful resolve.
        // Without this, resolveTimedOut stays true after the 15s timer fires
        // even though the resource arrived. That has two consequences:
        //   1) the bridge watchdog effect (above) gates on
        //      `if (isResolving || resolveError || resolveTimedOut) return;`
        //      so it never arms — the "stopped responding" retry path can
        //      never trigger if appReady never arrives later.
        //   2) the UI shows the resolve-timeout error state even though the
        //      widget actually loaded.
        setResolveTimedOut(false);
      })
      .catch((error: unknown) => {
        if (isCancelled) return;
        console.error("[AxWidget] resolve failed:", messageId, error);
        setResolveError(
          error instanceof Error
            ? error.message
            : "Widget resource could not be resolved.",
        );
      })
      .finally(() => {
        if (isCancelled) return;
        globalThis.clearTimeout(timeoutId);
        setIsResolving(false);
      });

    return () => {
      isCancelled = true;
      globalThis.clearTimeout(timeoutId);
    };
  }, [
    shouldActivateWidgetFrame,
    messageId,
    resolveAttempt,
    spaceId,
    isCollapsed,
    isNearViewport,
    isPageVisible,
    forceMount,
    widget.html,
    widget.resource_uri,
    widget.resource_url,
    widget.tool_call_id,
    widget.tool_name,
  ]);

  const iframeSource = useMemo(() => {
    if (sourceUrl) {
      return {
        src: sourceUrl,
        srcDoc: undefined,
        name: undefined,
      };
    }

    if (!sourceHtml) {
      return {
        src: undefined,
        srcDoc: undefined,
        name: undefined,
      };
    }

    const globals = {
      ...(effectiveToolResult ? { toolOutput: effectiveToolResult } : {}),
      ...(onOpenDirectHtmlPanel
        ? { axHostCapabilities: { directHtmlFullscreen: true } }
        : {}),
    };
    let encodedPayload = encodeFrameHostPayload({
      html: sourceHtml,
      globals,
    });

    if (
      effectiveToolResult &&
      encodedPayload.length > MAX_INLINE_FRAME_TOOL_RESULT_CHARS
    ) {
      debugWidgetHost("frame payload too large; deferring tool output", {
        payloadLength: encodedPayload.length,
        resourceUri: widget.resource_uri || null,
        toolName: widget.tool_name || null,
      });
      encodedPayload = encodeFrameHostPayload({
        html: sourceHtml,
        globals: {
          ...(onOpenDirectHtmlPanel
            ? { axHostCapabilities: { directHtmlFullscreen: true } }
            : {}),
        },
      });
    }

    const encodedHtml = encodedPayload ? "" : encodeFrameHostHtml(sourceHtml);
    const payloadName = encodedPayload
      ? `ax-mcp-html-v2:${encodedPayload}`
      : `ax-mcp-html:${encodedHtml}`;
    const payloadKey = buildFrameHostPayloadStorageKey(
      widgetInstanceKey,
      payloadName.length,
    );
    const storedPayload = storeFrameHostPayload(payloadKey, payloadName);

    return {
      src: buildFrameHostSrc(storedPayload ? payloadKey : null),
      srcDoc: undefined,
      name: payloadName,
    };
  }, [
    effectiveToolResult,
    onOpenDirectHtmlPanel,
    sourceHtml,
    sourceUrl,
    widgetInstanceKey,
  ]);

  // Instance-change reset: only fires when this is genuinely a different
  // widget (new message or new tool_call_id). Does NOT fire when derived
  // values like title/sourceKey flip during initial resource resolution,
  // which used to race the iframe's first handshake and wipe iframeReady
  // / frameHeight mid-mount. Does NOT touch frameHeight — the iframe's
  // notifyIntrinsicHeight call owns that, not the reset path.
  useEffect(() => {
    setIframeReady(false);
    setAppReady(false);
    initialPayloadSentRef.current = false;
    setHostNotice(null);
    setModelContext(null);
    setWidgetBridgeTimedOut(false);
    setIsLauncherParked(false);
    setIframeMountNonce(0);
  }, [widgetInstanceKey]);

  useLayoutEffect(() => {
    if (!shouldRenderWidgetFrame || (!sourceHtml && !sourceUrl)) {
      return;
    }

    // Read frameWindow dynamically from the ref so messages that arrive
    // between the iframe onLoad and this effect re-run are not dropped.
    // The MCP Apps SDK sends ui/initialize immediately on load — if we
    // capture frameWindow in a closure it may be null and we'd skip the
    // entire bridge setup, causing callServerTool to hang forever.
    const getFrameWindow = () => iframeRef.current?.contentWindow ?? null;

    const sendBridgeMessage = (payload: WidgetBridgeMessage) => {
      const fw = getFrameWindow();
      if (!fw) return;
      debugWidgetHost(
        "postMessage -> frame",
        payload.method || payload.id,
        payload,
      );
      postWidgetFrameMessage(fw as WidgetFrameWindow, payload);
    };

    const sendOpenAiGlobals = (globals: Record<string, unknown>) => {
      const fw = getFrameWindow();
      if (!fw) return;
      const delivery = setWidgetOpenAiGlobals(fw as WidgetFrameWindow, globals);
      debugWidgetHost("openai globals delivery", delivery, globals);
    };

    const sendInitialToolPayloads = () => {
      if (initialPayloadSentRef.current) return;

      if (widget.tool_input) {
        sendBridgeMessage({
          jsonrpc: "2.0",
          method: "ui/notifications/tool-input",
          params: {
            arguments: widget.tool_input,
          },
        });
      }

      if (effectiveToolResult) {
        sendOpenAiGlobals({
          toolOutput: effectiveToolResult,
        });
        sendBridgeMessage({
          jsonrpc: "2.0",
          method: "ui/notifications/tool-result",
          params: effectiveToolResult,
        });
        initialPayloadSentRef.current = true;
      } else if (widget.tool_name) {
        // No server-authored tool_result available. Widget renders its
        // HTML template but won't have data until the backend provides
        // initial_data in the widget metadata. We never replay the
        // originating tool call from the browser — that would execute
        // under the viewer's token instead of the agent's identity.
        debugWidgetHost("no tool_result, awaiting server-authored data", {
          toolName: widget.tool_name,
          resourceUri: widget.resource_uri,
        });
      }
    };

    const postResponse = (
      request: WidgetBridgeMessage,
      response: Pick<WidgetBridgeMessage, "result" | "error">,
    ) => {
      if (request.id == null) return;
      sendBridgeMessage({
        jsonrpc: "2.0",
        id: request.id,
        ...response,
      });
    };

    const handleMessage = (event: MessageEvent) => {
      // Use the ref dynamically — the iframe may not exist when the handler
      // is first registered, but will exist by the time messages arrive.
      const fw = getFrameWindow();
      // Only accept messages from this widget's own iframe. Allowing widgets
      // without a bound frame window to process any jsonrpc-looking message
      // causes duplicate host actions when multiple MCP widgets are mounted.
      if (!fw || event.source !== fw) return;
      const message = asRecord(event.data);
      if (!message) return;

      if (asString(message.type) === "ax/mcp-frame:request-payload") {
        const params = asRecord(message.params);
        const requestedKey = asString(params?.payloadKey);
        const payloadKey = getFrameHostPayloadKey(iframeSource.src);
        const requestedCurrentPayload =
          !requestedKey || requestedKey === payloadKey;
        const currentPayloadName =
          requestedCurrentPayload && isFrameHostPayloadName(iframeSource.name)
            ? iframeSource.name
            : null;
        const payloadName =
          currentPayloadName ||
          (requestedCurrentPayload ? readFrameHostPayload(payloadKey) : null);

        if (payloadName) {
          const responsePayloadKey = payloadKey;
          console.warn("[AxMcpAppWidget] replaying frame payload to host", {
            payloadKey: responsePayloadKey,
            requestedKey,
            payloadLength: payloadName.length,
            resourceUri: widget.resource_uri || null,
            toolName: widget.tool_name || null,
          });
          fw.postMessage(
            {
              type: "ax/mcp-frame:payload",
              payloadKey: responsePayloadKey,
              payloadName,
            },
            "*",
          );
        }
        return;
      }

      const request: WidgetBridgeMessage = {
        id:
          typeof message.id === "string" || typeof message.id === "number"
            ? message.id
            : null,
        jsonrpc: asString(message.jsonrpc) || undefined,
        method: asString(message.method) || undefined,
        params: message.params,
      };

      switch (request.method) {
        case "ui/initialize": {
          debugWidgetHost("frame -> host initialize", {
            toolName: widget.tool_name,
            resourceUri: widget.resource_uri,
          });
          postResponse(request, {
            result: {
              protocolVersion: "2026-01-26",
              hostInfo: {
                name: "Waystation",
                version: "1.0.0",
              },
              hostCapabilities: {
                openLinks: {},
                message: {
                  text: {},
                },
                updateModelContext: {
                  structuredContent: {},
                  text: {},
                },
                serverTools: {},
                serverResources: {},
                sandbox: {
                  csp: {
                    resourceDomains: [window.location.origin],
                  },
                },
              },
              hostContext: {
                theme: widgetTheme,
                locale:
                  typeof navigator !== "undefined"
                    ? navigator.language || "en-US"
                    : "en-US",
                platform: "web",
                displayMode: effectiveDisplayMode,
                availableDisplayModes: ["inline", "fullscreen"],
                containerDimensions: {
                  maxHeight: effectiveFrameHeight,
                  maxWidth: 1280,
                },
                toolInfo: {
                  id: widget.tool_call_id || messageId,
                  tool: {
                    name: widget.tool_name || "widget",
                    title,
                    description: widget.fallback_text || `${title} widget`,
                    inputSchema: {
                      type: "object",
                    },
                  },
                },
                userAgent: "Waystation widget host",
              },
            },
          });
          window.setTimeout(() => {
            if (!initialPayloadSentRef.current) {
              sendInitialToolPayloads();
            }
          }, 750);
          return;
        }
        case "ui/notifications/initialized": {
          debugWidgetHost("frame -> host initialized", {
            toolName: widget.tool_name,
            resourceUri: widget.resource_uri,
          });
          setAppReady(true);
          return;
        }
        case "ui/update-model-context": {
          const params = asRecord(request.params);
          const nextContext =
            params?.context ?? params?.modelContext ?? params?.value ?? null;
          setModelContext(nextContext);
          postResponse(request, { result: { ok: true } });
          return;
        }
        case "ui/open-link": {
          const params = asRecord(request.params);
          const url =
            asString(params?.url) ||
            asString(params?.href) ||
            asString(params?.uri);
          if (url) {
            window.open(url, "_blank", "noopener,noreferrer");
            postResponse(request, { result: { ok: true } });
          } else {
            postResponse(request, {
              error: {
                code: -32602,
                message: "ui/open-link requires a URL.",
              },
            });
          }
          return;
        }
        case "ui/size-change":
        case "ui/notifications/size-changed": {
          const params = asRecord(request.params);
          const nextHeight = Number(params?.height);
          if (Number.isFinite(nextHeight)) {
            updateFrameHeight(Math.max(60, Math.min(960, nextHeight)));
          }
          postResponse(request, { result: { ok: true } });
          return;
        }
        case "ui/open-html-fullscreen": {
          const params = asRecord(request.params);
          const html = asString(params?.html);
          if (!html) {
            postResponse(request, {
              error: {
                code: -32602,
                message: "ui/open-html-fullscreen requires HTML.",
              },
            });
            return;
          }
          if (!onOpenDirectHtmlPanel) {
            postResponse(request, {
              error: {
                code: -32601,
                message: "Host does not support direct HTML fullscreen.",
              },
            });
            return;
          }
          onOpenDirectHtmlPanel?.({
            title: asString(params?.title) || title,
            html,
            key: asString(params?.key),
          });
          postResponse(request, { result: { ok: true } });
          return;
        }
        case "ui/request-display-mode": {
          // Fullscreen mode disabled for now — always stay inline
          postResponse(request, {
            result: { mode: "inline" },
          });
          return;
        }
        case "ui/message": {
          const params = asRecord(request.params);
          const nextText =
            asString(params?.text) ||
            asString(params?.message) ||
            asString(params?.body) ||
            "Widget sent a host message.";
          setHostNotice({ tone: "info", text: nextText });
          postResponse(request, { result: { ok: true } });
          return;
        }
        case "ui/open-compose": {
          const params = asRecord(request.params);
          const handle =
            asString(params?.handle) ||
            asString(params?.mention) ||
            asString(params?.target) ||
            asString(params?.agentHandle) ||
            asString(params?.name);

          if (!appendAgentMentionToCompose(handle)) {
            postResponse(request, {
              error: {
                code: -32602,
                message: "ui/open-compose requires an agent handle.",
              },
            });
            return;
          }

          setHostNotice({
            tone: "info",
            text: `Compose ready for @${handle?.replace(/^@/, "")}`,
          });
          postResponse(request, { result: { ok: true } });
          return;
        }
        case "tools/call": {
          const params = asRecord(request.params);
          const toolName =
            asString(params?.name) || asString(params?.toolName) || null;
          const input =
            asRecord(params?.arguments) ||
            asRecord(params?.input) ||
            asRecord(params?.params) ||
            {};

          if (!toolName) {
            postResponse(request, {
              error: {
                code: -32602,
                message: "tools/call requires a tool name.",
              },
            });
            return;
          }

          if (!isLauncherPanel) {
            // Transcript widgets are agent-authored surfaces. They may render
            // the agent-authored initial_data, but they must not refresh
            // generic reads through the viewer's browser token. The only
            // browser-token exception here is explicit HITL draft work: the
            // user is acting as themselves to approve/edit/reject a specific
            // draft surfaced by the agent.
            const action = asString(input?.action) || null;
            if (
              effectiveToolResult &&
              isWhoamiWidgetDescriptor(widget) &&
              isWhoamiToolName(toolName) &&
              canReplayCachedWhoamiResult(toolName, action)
            ) {
              // Transcript identity widgets must never replay with the
              // viewer token. Read/bootstrap calls can reuse the
              // server-authored result the agent originally produced; writes
              // must continue to the transcript block instead of faking
              // success with stale data.
              postResponse(request, { result: effectiveToolResult });
              return;
            }

            const allowedInTranscript = canRunViewerLocalTranscriptCall(
              toolName,
              input,
            );

            if (!allowedInTranscript) {
              const message =
                "This widget action needs a user-approved write flow. Open the app panel or use an approval card to make changes.";
              postResponse(request, {
                error: {
                  code: -32001,
                  message,
                },
              });
              return;
            }
          }

          void proxyMcpToolCall(toolName, input, { spaceId })
            .then((result) => {
              postResponse(request, { result });
              setHostNotice({
                tone: "info",
                text: `Tool call completed: ${toolName}`,
              });
            })
            .catch((error: unknown) => {
              const message = getFriendlyErrorMessage(
                error,
                `tools/call failed for ${toolName}.`,
              );
              postResponse(request, {
                error: {
                  code: -32000,
                  message,
                },
              });
              setHostNotice({
                tone: "error",
                text: message,
              });
            });
          return;
        }
        case "resources/read": {
          const params = asRecord(request.params);
          const resourceUri =
            asString(params?.uri) || asString(params?.resourceUri) || null;

          if (!resourceUri) {
            postResponse(request, {
              error: {
                code: -32602,
                message: "resources/read requires a resource URI.",
              },
            });
            return;
          }

          void proxyMcpResourceRead(resourceUri)
            .then((result) => {
              postResponse(request, { result });
            })
            .catch((error: unknown) => {
              const message = getFriendlyErrorMessage(
                error,
                `resources/read failed for ${resourceUri}.`,
              );
              postResponse(request, {
                error: {
                  code: -32000,
                  message,
                },
              });
              setHostNotice({
                tone: "error",
                text: message,
              });
            });
          return;
        }
        default:
          return;
      }
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [
    shouldRenderWidgetFrame,
    effectiveToolResult,
    effectiveDisplayMode,
    effectiveFrameHeight,
    iframeReady,
    isLauncherPanel,
    messageId,
    sourceKey,
    sourceHtml,
    sourceUrl,
    iframeSource.name,
    iframeSource.src,
    onOpenDirectHtmlPanel,
    title,
    widget.fallback_text,
    widget.resource_uri,
    spaceId,
    widget.tool_call_id,
    widget.tool_input,
    widget.tool_name,
  ]);

  useEffect(() => {
    if (!iframeReady) return;
    if (!isFrameHostSource(iframeSource.src)) return;

    const frameWindow = iframeRef.current?.contentWindow;
    if (!frameWindow) return;

    setWidgetFrameTheme(frameWindow as WidgetFrameWindow, widgetTheme);
  }, [iframeReady, iframeSource.src, widgetTheme]);

  useEffect(() => {
    // Host chrome commands (for example the Tasks panel Home/All tasks button)
    // are consumed by widget code that registers its listener during app boot.
    // Wait for appReady so an early click does not race iframe load and get lost.
    if (!iframeReady || !appReady || !hostCommand) return;
    const frameWindow = iframeRef.current?.contentWindow;
    if (!frameWindow) return;
    postWidgetFrameMessage(frameWindow, {
      type: "ax/host-command",
      command: hostCommand.type,
      nonce: hostCommand.nonce,
    });
  }, [appReady, hostCommand, iframeReady]);

  useEffect(() => {
    if (!iframeReady || !appReady) return;
    const frameWindow = iframeRef.current?.contentWindow;
    if (!frameWindow) return;

    const send = (payload: WidgetBridgeMessage) => {
      postWidgetFrameMessage(frameWindow as WidgetFrameWindow, payload);
    };

    const sendOpenAiGlobals = (globals: Record<string, unknown>) => {
      const delivery = setWidgetOpenAiGlobals(
        frameWindow as WidgetFrameWindow,
        globals,
      );
      debugWidgetHost("openai globals delivery", delivery, globals);
    };

    if (initialPayloadSentRef.current) return;

    if (widget.tool_input) {
      debugWidgetHost("sending initial tool input", widget.tool_input);
      send({
        jsonrpc: "2.0",
        method: "ui/notifications/tool-input",
        params: {
          arguments: widget.tool_input,
        },
      });
    }

    if (effectiveToolResult) {
      sendOpenAiGlobals({
        toolOutput: effectiveToolResult,
      });
      debugWidgetHost("sending initial tool result", effectiveToolResult);
      send({
        jsonrpc: "2.0",
        method: "ui/notifications/tool-result",
        params: effectiveToolResult,
      });
    }

    if (effectiveToolResult) {
      initialPayloadSentRef.current = true;
    }
  }, [
    appReady,
    effectiveToolResult,
    effectiveDisplayMode,
    effectiveFrameHeight,
    iframeReady,
    messageId,
    title,
    widget.kind,
    widget.lifecycle,
    widget.resource_uri,
    widget.tool_call_id,
    widget.tool_input,
    widget.tool_name,
  ]);

  useEffect(() => {
    if (appReady || widgetBridgeTimedOut) return;
    if (!isPageVisible) return;
    if (isDirectHtmlArtifact) return;
    if (!shouldRenderWidgetFrame || (!sourceUrl && !sourceHtml)) return;
    if (isResolving || resolveError || resolveTimedOut) return;

    const timeoutId = window.setTimeout(() => {
      setWidgetBridgeTimedOut(true);
    }, 15000);

    return () => window.clearTimeout(timeoutId);
  }, [
    appReady,
    iframeRenderKey,
    isDirectHtmlArtifact,
    isPageVisible,
    isResolving,
    resolveError,
    resolveTimedOut,
    shouldRenderWidgetFrame,
    sourceHtml,
    sourceUrl,
    widgetBridgeTimedOut,
  ]);

  useEffect(() => {
    if (!iframeReady || !effectiveToolResult || initialPayloadSentRef.current) {
      return;
    }

    const frameWindow = iframeRef.current?.contentWindow;
    if (!frameWindow) return;

    const delivery = setWidgetOpenAiGlobals(frameWindow as WidgetFrameWindow, {
      toolOutput: effectiveToolResult,
    });
    debugWidgetHost(
      "late openai globals delivery before app ready",
      delivery,
      effectiveToolResult,
    );
    if (widget.tool_input) {
      debugWidgetHost(
        "sending late tool input before app ready",
        widget.tool_input,
      );
      postWidgetFrameMessage(frameWindow as WidgetFrameWindow, {
        jsonrpc: "2.0",
        method: "ui/notifications/tool-input",
        params: {
          arguments: widget.tool_input,
        },
      });
    }
    postWidgetFrameMessage(frameWindow as WidgetFrameWindow, {
      jsonrpc: "2.0",
      method: "ui/notifications/tool-result",
      params: effectiveToolResult,
    });
    initialPayloadSentRef.current = true;
  }, [effectiveToolResult, iframeReady, widget.tool_input]);

  useEffect(() => {
    if (!iframeReady || !appReady) return;
    const frameWindow = iframeRef.current?.contentWindow;
    if (!frameWindow) return;

    postWidgetFrameMessage(frameWindow as WidgetFrameWindow, {
      jsonrpc: "2.0",
      method: "ui/notifications/host-context-changed",
      params: {
        theme: widgetTheme,
        displayMode: effectiveDisplayMode,
        availableDisplayModes: ["inline", "fullscreen"],
        containerDimensions: {
          maxHeight: effectiveFrameHeight,
          maxWidth: 1280,
        },
      },
    });
  }, [
    appReady,
    effectiveDisplayMode,
    effectiveFrameHeight,
    iframeReady,
    widgetTheme,
  ]);

  const modelContextPreview = useMemo(
    () => getModelContextPreview(modelContext),
    [modelContext],
  );

  const retryResolve = () => {
    if (widget.resource_uri) {
      clearWidgetCacheEntry({
        space_id: spaceId,
        message_id: messageId,
        resource_uri: widget.resource_uri,
        tool_name: widget.tool_name,
        tool_call_id: widget.tool_call_id,
      });
    }
    setResolvedResource(null);
    setResolveError(null);
    setIsNearViewport(true);
    setResolveAttempt((current) => current + 1);
  };

  const retryWidgetFrame = () => {
    setWidgetBridgeTimedOut(false);
    setIframeReady(false);
    setAppReady(false);
    initialPayloadSentRef.current = false;
    setHostNotice(null);
    setModelContext(null);
    frameHeightRef.current = 120;
    setFrameHeight(120);
    setFrameHeightTransition(WIDGET_HEIGHT_TRANSITION_GROW);
    setIframeMountNonce((current) => current + 1);
  };

  const contextCatalogEntry = useMemo(
    () =>
      extractContextCatalogEntryFromWidget({
        initial_data: widget.initial_data,
        structured_content: widget.structured_content,
        tool_result: effectiveToolResult ?? widget.tool_result,
      }),
    [
      effectiveToolResult,
      widget.initial_data,
      widget.structured_content,
      widget.tool_result,
    ],
  );
  const handleContextCatalogAction = useCallback(
    (input: Parameters<typeof invokeContextCatalogAction>[2]) => {
      if (!contextCatalogEntry) {
        throw new Error("Context Catalog action lane is unavailable.");
      }
      return invokeContextCatalogAction(spaceId, contextCatalogEntry.id, input);
    },
    [contextCatalogEntry, spaceId],
  );

  const showFallback =
    !sourceUrl &&
    !sourceHtml &&
    !isResolving &&
    Boolean(resolveError || widget.fallback_text || widget.resource_uri);
  const fallbackNotice =
    normalizeWidgetNoticeText(widget.fallback_text) ||
    normalizeWidgetNoticeText(resolveError) ||
    "Widget resource could not be resolved.";

  // Fallback confirmation card: only when the MCP app genuinely has nothing
  // to render (no resource_uri, no url, no html, no payload). When a resource
  // IS available, always let the MCP app iframe render — the MCP app owns
  // the content, the frontend only owns the chrome.
  const isConfirmation =
    !sourceUrl &&
    !sourceHtml &&
    !isResolving &&
    !effectiveToolResult &&
    !widget.resource_uri &&
    !widget.fallback_text &&
    !resolveError;
  const detailLabel = extractWidgetDetailLabel(widget);
  const widgetShell = (
    <section
      ref={widgetRootRef}
      data-testid="ax-mcp-widget"
      data-quick-launch={isLauncherPanel ? "true" : undefined}
      className={cn(
        "min-w-0 max-w-full overflow-x-hidden text-white",
        panelMode
          ? "flex h-full min-h-0 flex-col"
          : cn(
              "mt-3",
              isLauncherPanel
                ? "relative overflow-hidden rounded-[34px] border border-cyan-200/15 bg-[radial-gradient(circle_at_12%_0%,rgba(34,211,238,0.18),transparent_34%),radial-gradient(circle_at_86%_10%,rgba(251,191,36,0.12),transparent_28%),linear-gradient(135deg,rgba(15,23,42,0.74),rgba(2,6,23,0.94))] p-2 shadow-[0_30px_90px_-58px_rgba(34,211,238,0.95)] backdrop-blur-2xl"
                : "rounded-2xl border border-white/[0.08] bg-white/[0.02] p-3",
            ),
      )}
    >
      {!panelMode ? (
        <div
          className={cn(
            "mb-2 flex flex-wrap items-start justify-between gap-2",
            isLauncherPanel && "px-2 pt-1",
          )}
        >
          <div
            className={cn(
              "flex min-w-0 items-baseline gap-2 text-sm text-slate-400",
              isLauncherPanel && "items-center",
            )}
          >
            {isLauncherPanel ? (
              <>
                <span className="min-w-0 truncate text-[15px] font-medium text-slate-100">
                  {title}
                </span>
                <span className="rounded-full border border-cyan-200/15 bg-cyan-300/[0.07] px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-cyan-100/80">
                  App panel
                </span>
                {formatTimestamp(timestamp) && (
                  <span
                    className="text-[11px] uppercase tracking-[0.16em] text-white/30"
                    title={formatAbsoluteTimestamp(timestamp) || undefined}
                  >
                    {formatTimestamp(timestamp)}
                  </span>
                )}
              </>
            ) : (
              <>
                <span>{title}</span>
                <span className="text-[11px] text-white/25">
                  Waystation tool call
                  {widget.tool_name && (
                    <>
                      <span className="text-white/15"> / </span>
                      <span className="font-mono">{widget.tool_name}</span>
                    </>
                  )}
                  {formatTimestamp(timestamp) && (
                    <>
                      <span className="text-white/15"> &middot; </span>
                      <span
                        title={formatAbsoluteTimestamp(timestamp) || undefined}
                      >
                        {formatTimestamp(timestamp)}
                      </span>
                    </>
                  )}
                </span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            {lifecycleLabel !== "Ready" && (
              <span
                data-testid="ax-mcp-widget-status"
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-[0.14em]",
                  getLifecycleTone(widget.lifecycle),
                )}
              >
                {lifecycleLabel}
              </span>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => {
                if (isLauncherParked) {
                  setIsLauncherParked(false);
                  return;
                }
                setIsCollapsed((current) => !current);
              }}
              className="h-8 w-8 rounded-full border border-white/10 bg-white/[0.04] text-slate-300 hover:border-cyan-300/35 hover:bg-white/[0.08] hover:text-white"
              aria-label={
                isCollapsed || isLauncherParked
                  ? "Expand widget"
                  : "Minimize widget"
              }
              title={isCollapsed || isLauncherParked ? "Expand" : "Minimize"}
            >
              {isCollapsed ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : isLauncherParked ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <Minimize2 className="h-3.5 w-3.5" />
              )}
            </Button>
            {onClose ? (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={onClose}
                className="h-8 w-8 rounded-full border border-white/10 bg-white/[0.04] text-slate-300 hover:border-rose-300/35 hover:bg-rose-400/[0.08] hover:text-white"
                aria-label="Close widget"
                title="Close"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {panelMode && contextCatalogEntry && !isCollapsed && !isLauncherParked ? (
        <ContextCatalogTrustedActionLane
          entry={contextCatalogEntry}
          onInvokeAction={handleContextCatalogAction}
        />
      ) : null}

      <div className={cn(panelMode && "min-h-0 flex-1")}>
        {isCollapsed ? (
          <button
            type="button"
            data-testid="ax-mcp-widget-collapsed"
            onClick={() => setIsCollapsed(false)}
            className="w-full rounded-xl border border-white/10 bg-slate-950/45 px-4 py-3 text-left transition hover:border-cyan-300/35 hover:bg-slate-950/65"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-[11px] uppercase tracking-[0.18em] text-slate-400">
                  App folded
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-200">
                  {collapsedPreview}
                </p>
              </div>
              <span className="shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] uppercase tracking-[0.14em] text-slate-300">
                Open
              </span>
            </div>
          </button>
        ) : isLauncherParked ? (
          <button
            type="button"
            data-testid="ax-mcp-widget-parked"
            onClick={() => setIsLauncherParked(false)}
            className="w-full rounded-[24px] border border-cyan-300/15 bg-slate-950/35 px-4 py-3 text-left transition hover:border-cyan-300/35 hover:bg-slate-950/55"
          >
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-[11px] uppercase tracking-[0.18em] text-cyan-100/75">
                  App parked
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-200">
                  {title} was folded after it moved out of the active
                  conversation area. Restore it when you want to keep working.
                </p>
              </div>
              <span className="shrink-0 rounded-full border border-cyan-300/20 bg-cyan-400/[0.08] px-2.5 py-1 text-[11px] uppercase tracking-[0.14em] text-cyan-100">
                Restore
              </span>
            </div>
          </button>
        ) : null}

        <div
          className={cn(
            panelMode && "h-full min-h-0",
            (isCollapsed || isLauncherParked) && "hidden",
          )}
          aria-hidden={isCollapsed || isLauncherParked}
        >
          {shouldShowDormantWidgetPlaceholder ? (
            <div className="flex min-h-[120px] flex-col items-center justify-center gap-1 text-center text-sm text-slate-500">
              <div>Scroll this app into view to load it.</div>
              <div className="text-xs text-slate-600">
                Offscreen apps pause to keep the workspace light.
              </div>
            </div>
          ) : isResolving ? (
            <div className="flex min-h-[120px] flex-col items-center justify-center gap-3 text-sm text-slate-400">
              <div className="flex items-center gap-3">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                {widget.loading_label || "Resolving widget resource..."}
              </div>
              {resolveTimedOut && (
                <button
                  type="button"
                  onClick={retryResolve}
                  className="mt-2 rounded-full border border-amber-300/20 bg-amber-400/[0.08] px-3 py-1.5 text-xs text-amber-50 transition hover:bg-amber-400/[0.16]"
                >
                  Taking too long — retry
                </button>
              )}
            </div>
          ) : shouldDeferWidgetFrameUntilToolResult ? (
            <div
              data-testid="ax-mcp-widget-deferred"
              className="flex min-h-[180px] flex-col items-center justify-center gap-3 rounded-[28px] border border-white/[0.07] bg-slate-950/55 px-5 py-8 text-center text-sm text-slate-300 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
            >
              <LoaderCircle className="h-5 w-5 animate-spin text-cyan-200" />
              <div>
                <div className="font-medium text-slate-100">
                  {widget.loading_label || `Preparing ${title}...`}
                </div>
                <p className="mt-1 text-xs text-slate-400">
                  Loading the latest app data before opening the panel.
                </p>
              </div>
            </div>
          ) : widgetBridgeTimedOut ? (
            <div className="flex min-h-[120px] flex-col items-center justify-center gap-3 rounded-xl border border-rose-300/20 bg-rose-400/[0.06] px-4 py-5 text-center">
              <AlertTriangle className="h-5 w-5 text-rose-200/80" />
              <div>
                <div className="text-sm font-medium text-rose-50">
                  Widget stopped responding
                </div>
                <p className="mt-1 text-xs text-rose-100/70">
                  The widget did not finish loading within 15 seconds.
                </p>
              </div>
              <button
                type="button"
                onClick={retryWidgetFrame}
                className="rounded-full border border-rose-200/25 bg-rose-300/10 px-3 py-1.5 text-xs font-medium text-rose-50 transition hover:bg-rose-300/20"
              >
                Retry
              </button>
            </div>
          ) : shouldRenderWidgetFrame && (sourceHtml || sourceUrl) ? (
            <div
              data-testid="ax-mcp-widget-frame-wrap"
              className={cn(
                "w-full overflow-hidden",
                panelMode
                  ? cn("h-full", themedFrameSurfaceClassName)
                  : isLauncherPanel
                    ? "rounded-[28px] border border-white/[0.07] bg-slate-950/55 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
                    : "rounded-xl bg-slate-950",
              )}
              style={{
                height: panelMode ? "100%" : `${effectiveFrameHeight}px`,
                transition: panelMode ? undefined : frameHeightTransition,
              }}
            >
              <iframe
                key={iframeRenderKey}
                ref={iframeRef}
                data-testid="ax-mcp-widget-frame"
                title={title}
                src={iframeSource.src}
                srcDoc={iframeSource.srcDoc}
                name={iframeSource.name}
                loading={isLauncherPanel ? "eager" : "lazy"}
                sandbox={
                  isFrameHostSource(iframeSource.src)
                    ? "allow-scripts allow-forms allow-modals allow-popups allow-same-origin allow-downloads allow-popups-to-escape-sandbox"
                    : "allow-scripts allow-forms allow-modals allow-popups allow-downloads"
                }
                className={cn(
                  "block h-full w-full",
                  panelMode
                    ? themedFrameSurfaceClassName
                    : isLauncherPanel
                      ? "bg-transparent"
                      : themedFrameSurfaceClassName,
                )}
                style={{ height: "100%" }}
                onLoad={() => setIframeReady(true)}
              />
            </div>
          ) : isConfirmation ? (
            <ConfirmationCard
              action={
                (widget.tool_action ||
                  (widget.tool_input as Record<string, unknown> | null)
                    ?.action) as string | null
              }
              label={detailLabel}
              content={extractWidgetDetailContent(widget)}
            />
          ) : showFallback ? (
            <div className="flex items-start gap-3 rounded-xl bg-amber-400/[0.06] px-3 py-2.5 text-sm text-amber-50/80">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300/60" />
              <div>{fallbackNotice}</div>
            </div>
          ) : null}
        </div>
      </div>

      {!panelMode && !isCollapsed && (showFallback || sourceUrl) && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {showFallback ? (
            <button
              type="button"
              onClick={retryResolve}
              className="text-xs text-amber-200/60 underline decoration-amber-200/20 hover:text-amber-100"
            >
              Retry
            </button>
          ) : null}
          {sourceUrl ? (
            <button
              type="button"
              onClick={() =>
                window.open(sourceUrl, "_blank", "noopener,noreferrer")
              }
              className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
            >
              <ExternalLink className="h-3 w-3" />
              Open
            </button>
          ) : null}
        </div>
      )}

      {!panelMode && !isCollapsed && contextUploadSummaries.length ? (
        <div className="mt-3 rounded-2xl border border-cyan-300/15 bg-cyan-400/[0.05] p-3">
          <div className="text-[11px] uppercase tracking-[0.18em] text-cyan-100/75">
            Shared context metadata
          </div>
          <div className="mt-3 space-y-2">
            {contextUploadSummaries.map((entry) => (
              <div
                key={`${entry.filename}-${entry.uploadedAt || "unknown"}`}
                className="rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2.5"
              >
                <div className="flex items-center gap-2 text-sm text-white">
                  {entry.fileType.toLowerCase().startsWith("image") ? (
                    <FileImage className="h-4 w-4 text-cyan-200" />
                  ) : (
                    <FileText className="h-4 w-4 text-cyan-200" />
                  )}
                  <span className="font-medium">{entry.filename}</span>
                  <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase tracking-[0.16em] text-slate-300">
                    {entry.fileType}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-300/80">
                  <span className="inline-flex items-center gap-1">
                    <UserRound className="h-3.5 w-3.5 text-slate-400" />
                    {entry.uploader}
                  </span>
                  <span
                    title={
                      entry.uploadedAt
                        ? formatAbsoluteTimestamp(entry.uploadedAt) || undefined
                        : undefined
                    }
                  >
                    {entry.uploadedAt
                      ? formatTimestamp(entry.uploadedAt) || entry.uploadedAt
                      : "Timestamp unavailable"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {!panelMode && !isCollapsed && shouldShowHostNotice(hostNotice) ? (
        <div
          data-testid="ax-mcp-widget-notice"
          className={cn(
            "mt-2 rounded-lg px-3 py-1.5 text-xs",
            hostNotice!.tone === "error"
              ? "bg-rose-400/[0.06] text-rose-200/60"
              : "bg-cyan-400/[0.06] text-cyan-200/50",
          )}
        >
          {hostNotice!.text}
        </div>
      ) : null}

      {!panelMode && !isCollapsed && modelContextPreview ? (
        <div
          data-testid="ax-mcp-widget-model-context"
          className="mt-3 rounded-2xl border border-cyan-300/15 bg-cyan-400/[0.05] p-3"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[11px] uppercase tracking-[0.18em] text-cyan-100/75">
              MCP context
            </div>
            {modelContextPreview.chips.length ? (
              <span className="rounded-full border border-cyan-200/15 bg-cyan-300/[0.07] px-2 py-0.5 text-[10px] uppercase tracking-[0.16em] text-cyan-100/70">
                {modelContextPreview.chips.length} selected
              </span>
            ) : null}
          </div>
          {modelContextPreview.chips.length ? (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {modelContextPreview.chips.map((chip) => (
                <div
                  key={`${chip.title}-${chip.type || "context"}-${chip.source || "mcp"}-${chip.preview || ""}`}
                  data-testid="ax-mcp-widget-model-context-chip"
                  className="rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2.5"
                >
                  <div className="flex min-w-0 items-center gap-2 text-sm text-white">
                    <FileText className="h-4 w-4 shrink-0 text-cyan-200" />
                    <span className="truncate font-medium">{chip.title}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-slate-300/85">
                    {chip.type ? (
                      <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5">
                        {chip.type}
                      </span>
                    ) : null}
                    {chip.source ? (
                      <span className="rounded-full border border-cyan-200/10 bg-cyan-300/[0.06] px-2 py-0.5 text-cyan-100/80">
                        {chip.source}
                      </span>
                    ) : null}
                  </div>
                  {chip.preview ? (
                    <p className="mt-2 text-xs leading-5 text-slate-300/85">
                      {chip.preview}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          ) : modelContextPreview.text ? (
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words text-sm leading-6 text-slate-200">
              {modelContextPreview.text}
            </pre>
          ) : null}
        </div>
      ) : null}
    </section>
  );

  return widgetShell;
}
