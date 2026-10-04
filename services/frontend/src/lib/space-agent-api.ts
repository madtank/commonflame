import { apiClient } from "@/lib/api-clean";
import { storage } from "@/lib/storage";
import config from "@/config/environment";

export type SpaceAgentCardType =
  | "receipt"
  | "task"
  | "agent"
  | "context"
  | "alert"
  | "confirmation"
  | "result"
  | "handoff_select";

export interface SpaceAgentCardEnvelope {
  card_id: string;
  type: SpaceAgentCardType;
  version: number;
  replace_in_place?: boolean;
  replace_target?: string | null;
  handoff_id?: string | null;
  payload: Record<string, unknown>;
}

export interface SpaceAgentWidgetCsp {
  connect_domains?: string[] | null;
  resource_domains?: string[] | null;
  frame_domains?: string[] | null;
}

export interface SpaceAgentWidgetDescriptor {
  kind?: "mcp_app" | string | null;
  tool_name?: string | null;
  tool_action?: string | null;
  tool_call_id?: string | null;
  resource_uri?: string | null;
  resource_mime_type?: string | null;
  resource_url?: string | null;
  html?: string | null;
  display_mode?: "inline" | "pip" | "fullscreen" | string | null;
  title?: string | null;
  loading_label?: string | null;
  fallback_text?: string | null;
  lifecycle?:
    | "ack"
    | "progress"
    | "working"
    | "complete"
    | "error"
    | "approval_required"
    | string
    | null;
  csp?: SpaceAgentWidgetCsp | null;
  structured_content?: Record<string, unknown> | null;
  arguments?: Record<string, unknown> | null;
  initial_data?: Record<string, unknown> | null;
  tool_input?: Record<string, unknown> | null;
  tool_result?: Record<string, unknown> | null;
  /** When the backend attaches multiple tool calls per turn, each entry lives here. */
  widgets?: SpaceAgentWidgetDescriptor[] | null;
}

export interface SpaceAgentSkippedSignal {
  id?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  message_id?: string | null;
  reason?: string | null;
  label?: string | null;
  reason_code?: string | null;
  signal_kind?: string | null;
  detail_reason_text?: string | null;
  emoji?: string | null;
  created_at?: string | null;
  signal_only?: boolean | null;
}

export interface SpaceAgentRouting {
  mode?: string | null;
  target_id?: string | null;
  target_name?: string | null;
  target_type?: string | null;
}

export interface SpaceAgentMessage {
  id: string;
  conversation_id?: string | null;
  parent_id?: string | null;
  space_id?: string | null;
  sender_type?: "user" | "agent" | "space_agent" | "system" | string | null;
  user_id?: string | null;
  agent_id?: string | null;
  display_name?: string | null;
  content?: string | null;
  ai_summary?: string | null;
  summarized_at?: string | null;
  message_type?: string | null;
  pause_duration?: number | string | null;
  pause_expires_at?: string | null;
  pause_reason?: string | null;
  pause_reason_text?: string | null;
  pause_emoji?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  routing?: SpaceAgentRouting | null;
  attachments?: Array<Record<string, unknown>> | null;
  accepted_attachments?: Array<Record<string, unknown>> | null;
  ui?: {
    cards?: SpaceAgentCardEnvelope[] | null;
    widget?: SpaceAgentWidgetDescriptor | null;
    signals?: {
      agent_skipped?: SpaceAgentSkippedSignal[] | null;
    } | null;
    replace_target?: string | null;
  } | null;
  metadata?:
    | (Record<string, unknown> & {
        alert?: Record<string, unknown> | null;
        ui?: {
          cards?: SpaceAgentCardEnvelope[] | null;
          widget?: SpaceAgentWidgetDescriptor | null;
          signals?: {
            agent_skipped?: SpaceAgentSkippedSignal[] | null;
          } | null;
          replace_target?: string | null;
        } | null;
      })
    | null;
  message_metadata?: {
    alert?: Record<string, unknown> | null;
    ui?: {
      cards?: SpaceAgentCardEnvelope[] | null;
      widget?: SpaceAgentWidgetDescriptor | null;
      signals?: {
        agent_skipped?: SpaceAgentSkippedSignal[] | null;
      } | null;
      replace_target?: string | null;
    } | null;
  } | null;
}

export interface SpaceAgentTranscriptResponse {
  messages?: SpaceAgentMessage[];
  posts?: SpaceAgentMessage[];
  items?: SpaceAgentMessage[];
  count?: number;
  total?: number;
  has_more?: boolean;
  has_older?: boolean;
  next_before?: string | null;
  oldest_cursor?: string | null;
}

export interface SpaceAgentConversationCardParticipant {
  id?: string | null;
  handle?: string | null;
  display_name?: string | null;
  name?: string | null;
  type?: "human" | "agent" | string | null;
  emoji?: string | null;
}

export interface SpaceAgentConversationCard {
  id: string;
  conversation_id?: string | null;
  root_message_id?: string | null;
  channel?: string | null;
  summary?: string | null;
  message_count?: number | null;
  participants?: Array<string | SpaceAgentConversationCardParticipant> | null;
  status?: string | null;
  metadata?: Record<string, unknown> | null;
  last_activity_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface SpaceAgentConversationCardsResponse {
  cards?: SpaceAgentConversationCard[];
  conversation_cards?: SpaceAgentConversationCard[];
  items?: SpaceAgentConversationCard[];
  count?: number;
}

export interface SpaceAgentSendReceipt {
  id?: string;
  message_id: string;
  /**
   * False when the server response carried no message id and message_id was
   * synthesized client-side — i.e. the send was NOT actually acknowledged.
   */
  server_confirmed?: boolean;
  conversation_id?: string | null;
  parent_id?: string | null;
  space_id?: string | null;
  sender_type?: string | null;
  display_name?: string | null;
  content?: string | null;
  ai_summary?: string | null;
  summarized_at?: string | null;
  message_type?: string | null;
  pause_duration?: number | string | null;
  pause_expires_at?: string | null;
  pause_reason?: string | null;
  pause_reason_text?: string | null;
  pause_emoji?: string | null;
  created_at?: string | null;
  received_by?: string[] | null;
  routing?: SpaceAgentRouting | null;
  ui?: {
    cards?: SpaceAgentCardEnvelope[] | null;
    widget?: SpaceAgentWidgetDescriptor | null;
    signals?: {
      agent_skipped?: SpaceAgentSkippedSignal[] | null;
    } | null;
    replace_target?: string | null;
  } | null;
  metadata?:
    | (Record<string, unknown> & {
        alert?: Record<string, unknown> | null;
        ui?: {
          cards?: SpaceAgentCardEnvelope[] | null;
          widget?: SpaceAgentWidgetDescriptor | null;
          signals?: {
            agent_skipped?: SpaceAgentSkippedSignal[] | null;
          } | null;
          replace_target?: string | null;
        } | null;
      })
    | null;
  message_metadata?: {
    alert?: Record<string, unknown> | null;
    ui?: {
      cards?: SpaceAgentCardEnvelope[] | null;
      widget?: SpaceAgentWidgetDescriptor | null;
      signals?: {
        agent_skipped?: SpaceAgentSkippedSignal[] | null;
      } | null;
      replace_target?: string | null;
    } | null;
  } | null;
}

export interface SpaceAgentSendOptions {
  parent_id?: string | null;
  attachments?: SpaceAgentAttachment[] | null;
  metadata?: Record<string, unknown> | null;
}

export interface SpaceAgentAttachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  url?: string;
  context_key?: string;
}

export interface SpaceAgentUploadResult {
  id: string;
  attachment_id?: string;
  url: string;
  content_type: string;
  size_bytes: number;
  filename: string;
}

export async function uploadSpaceAgentFile(
  spaceId: string,
  file: File,
): Promise<SpaceAgentUploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("space_id", spaceId);

  const response = await apiClient.post<SpaceAgentUploadResult>(
    "/api/v1/uploads/",
    formData,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return response.data;
}

export interface SpaceAgentActionPayload {
  space_id: string;
  message_id: string;
  action_id: string;
  card_id?: string;
  choice_id?: string;
  free_text?: string | null;
}

export interface ResolveSpaceAgentWidgetPayload {
  space_id: string;
  message_id: string;
  resource_uri: string;
  tool_name?: string | null;
  tool_call_id?: string | null;
}

export interface SpaceAgentResolvedWidgetResource {
  html?: string | null;
  resource_url?: string | null;
  resource_mime_type?: string | null;
  title?: string | null;
  csp?: SpaceAgentWidgetCsp | null;
  tool_call_id?: string | null;
  tool_name?: string | null;
  tool_action?: string | null;
  arguments?: Record<string, unknown> | null;
  initial_data?: Record<string, unknown> | null;
  tool_result?: Record<string, unknown> | null;
  structured_content?: Record<string, unknown> | null;
  result_kind?: string | null;
}

type McpJsonRpcEnvelope<T> = {
  jsonrpc?: string;
  id?: string | number | null;
  result?: T;
  error?: {
    code?: number;
    message?: string;
    data?: unknown;
  } | null;
};

type McpResourceContent = {
  uri?: string | null;
  mimeType?: string | null;
  mime_type?: string | null;
  text?: string | null;
  blob?: string | null;
};

type McpReadResourceResult = {
  contents?: McpResourceContent[] | null;
};

const MCP_PROTOCOL_VERSION = "2025-03-26";
const MCP_DIRECT_RESOURCE_READ_TIMEOUT_MS = 30000;
const MCP_DIRECT_TOOL_CALL_TIMEOUT_MS = 10000;
const MCP_DIRECT_SESSION_TIMEOUT_MS = 10000;

let mcpDirectSessionId: string | null = null;
let mcpDirectSessionSpaceId: string | null = null;
const mcpDirectSessionPromises = new Map<string, Promise<string | null>>();

export function __resetMcpDirectSessionForTests() {
  if (import.meta.env.MODE !== "test") return;
  mcpDirectSessionId = null;
  mcpDirectSessionSpaceId = null;
  mcpDirectSessionPromises.clear();
}

export function resolveMcpRequestUrl(mcpUrl: string) {
  if (typeof window !== "undefined") {
    return "/mcp";
  }

  return `${mcpUrl}/mcp`;
}

/**
 * URL for direct MCP server access (bypasses backend API) — used for widget resource reads.
 * In dev: returns /mcp-direct (Vite proxy rewrites to MCP server).
 * In browser production/staging: use same-origin /mcp so widgets follow the
 * public reverse-proxy path instead of trying to reach a private MCP port.
 * In non-browser contexts: fall back to the configured MCP origin.
 */
export function resolveMcpDirectUrl(mcpUrl: string) {
  if (typeof window !== "undefined") {
    return import.meta.env.DEV ? "/mcp-direct" : "/mcp";
  }

  return `${mcpUrl}/mcp`;
}

export function resolveMcpAppsUrl(mcpUrl: string, appPath = "") {
  const normalizedPath = appPath ? `/${appPath.replace(/^\/+/, "")}` : "";

  if (typeof window !== "undefined") {
    return `/mcp-apps${normalizedPath}`;
  }

  return `${mcpUrl}/apps${normalizedPath}`;
}

export interface SpaceAgentSummaryResponse {
  summary: string;
  cached?: boolean;
  message_id: string;
}

export interface SpaceAgentMember {
  id: string;
  handle?: string | null;
  display_name?: string | null;
  active?: boolean | null;
  // Real availability signal from the canonical roster (GET /api/v1/agents).
  // Threaded through so mention surfaces show live status instead of a static
  // "ACTIVE". See src/lib/agent-availability.ts.
  status?: string | null;
  lifecycle_state?: string | null;
  last_heartbeat?: string | null;
  presence_fresh?: boolean | null;
  presence_age_seconds?: number | null;
  is_online?: boolean | null;
  last_seen?: string | null;
  last_heartbeat_at?: string | null;
  last_active_at?: string | null;
  // How presence was established (e.g. the ax-presence monitor listener).
  // Surfaced as a more accurate connection label than the generic origin.
  // Pending backend support on the roster serializer (stack's lane).
  presence_source?: string | null;
  runtime_location?: {
    kind?: string | null;
    label?: string | null;
  } | null;
  capabilities?: string[] | null;
  capabilities_list?: string[] | null;
  capability_summary?: string | null;
}

export interface SpaceAgentSpace {
  id: string;
  name: string;
  slug?: string | null;
  visibility?: string | null;
  description?: string | null;
  is_personal?: boolean | null;
  member_count?: number | null;
  is_member?: boolean | null;
  is_current?: boolean | null;
}

type SpaceAgentSpacesResponse =
  | {
      spaces?: SpaceAgentSpace[];
      organizations?: SpaceAgentSpace[];
      items?: SpaceAgentSpace[];
      data?: SpaceAgentSpacesResponse | null;
      result?: SpaceAgentSpacesResponse | null;
      current_space?: SpaceAgentSpace | null;
      current_space_id?: string | null;
      active_space_id?: string | null;
    }
  | SpaceAgentSpace[];

type SpaceAgentTranscriptEnvelope =
  | SpaceAgentTranscriptResponse
  | SpaceAgentMessage[];

type SpaceAgentConversationCardsEnvelope =
  | SpaceAgentConversationCardsResponse
  | SpaceAgentConversationCard[];

type SpaceAgentSendReceiptEnvelope =
  | SpaceAgentSendReceipt
  | { message?: SpaceAgentMessage | null; received_by?: string[] | null };

function normalizeTranscriptResponse(
  data: SpaceAgentTranscriptEnvelope,
): SpaceAgentTranscriptResponse {
  if (Array.isArray(data)) {
    return { messages: data };
  }

  return {
    messages: data.messages || data.posts || data.items || [],
    count: typeof data.count === "number" ? data.count : undefined,
    total: typeof data.total === "number" ? data.total : undefined,
    has_more: typeof data.has_more === "boolean" ? data.has_more : undefined,
    has_older: typeof data.has_older === "boolean" ? data.has_older : undefined,
    next_before: typeof data.next_before === "string" ? data.next_before : null,
    oldest_cursor:
      typeof data.oldest_cursor === "string" ? data.oldest_cursor : null,
  };
}

function normalizeConversationCardsResponse(
  data: SpaceAgentConversationCardsEnvelope,
): SpaceAgentConversationCardsResponse {
  if (Array.isArray(data)) {
    return { cards: data, count: data.length };
  }

  const cards = data.cards || data.conversation_cards || data.items || [];
  return {
    cards,
    count: typeof data.count === "number" ? data.count : cards.length,
  };
}

function normalizeSendReceipt(
  data: SpaceAgentSendReceiptEnvelope,
): SpaceAgentSendReceipt {
  if ("message_id" in data) {
    return { ...data, server_confirmed: Boolean(data.message_id) };
  }

  const message = data.message;

  return {
    id: message?.id,
    message_id: message?.id || `message-${Date.now()}`,
    server_confirmed: Boolean(message?.id),
    conversation_id: message?.conversation_id,
    space_id: message?.space_id,
    sender_type: message?.sender_type,
    display_name: message?.display_name,
    content: message?.content,
    ai_summary: message?.ai_summary,
    summarized_at: message?.summarized_at,
    created_at: message?.created_at,
    received_by: data.received_by || null,
    routing: message?.routing || null,
    ui: message?.ui || null,
  };
}

export function normalizeSpacesResponse(
  data: SpaceAgentSpacesResponse | null | undefined,
  inheritedCurrentId?: string | null,
): SpaceAgentSpace[] {
  if (!data) return [];
  if (Array.isArray(data)) {
    return inheritedCurrentId
      ? data.map((space) => ({
          ...space,
          is_current: space.id === inheritedCurrentId,
        }))
      : data;
  }

  const currentId =
    data.current_space_id ||
    data.active_space_id ||
    data.current_space?.id ||
    inheritedCurrentId;
  const nested = data.data || data.result;
  if (nested) {
    const nestedSpaces = normalizeSpacesResponse(nested, currentId);
    if (nestedSpaces.length > 0) return nestedSpaces;
  }

  const spaces = data.spaces || data.organizations || data.items || [];
  const normalized = spaces.map((space) =>
    currentId ? { ...space, is_current: space.id === currentId } : space,
  );

  if (normalized.length === 0 && data.current_space?.id) {
    return [{ ...data.current_space, is_current: true }];
  }

  return normalized;
}

export async function getSpaceAgentTranscript(
  spaceId: string,
  options?: { limit?: number; before?: string | null },
) {
  const response = await apiClient.get<SpaceAgentTranscriptEnvelope>(
    "/api/v1/messages",
    {
      params: {
        space_id: spaceId,
        limit: Math.max(1, Math.min(options?.limit || 50, 100)),
        ...(options?.before ? { before: options.before } : {}),
      },
    },
  );
  return normalizeTranscriptResponse(response.data);
}

export async function getSpaceAgentConversationCards(spaceId: string) {
  const response = await apiClient.get<SpaceAgentConversationCardsEnvelope>(
    "/api/v1/conversations",
    {
      params: {
        space_id: spaceId,
        channel: "main",
        limit: 50,
        status: "active",
      },
    },
  );

  return normalizeConversationCardsResponse(response.data);
}

export async function sendSpaceAgentMessage(
  spaceId: string,
  content: string,
  options?: SpaceAgentSendOptions,
) {
  const acceptedAttachments = options?.attachments?.length
    ? options.attachments
    : undefined;
  const contextUploads = acceptedAttachments
    ?.filter((attachment) => attachment.context_key)
    .map((attachment) => ({
      key: attachment.context_key,
      attachment_id: attachment.id,
      filename: attachment.filename,
      content_type: attachment.content_type,
      url: attachment.url,
    }));

  const metadata = {
    ...(options?.metadata || {}),
    ...(acceptedAttachments
      ? {
          attachments: acceptedAttachments,
          accepted_attachments: acceptedAttachments,
        }
      : {}),
    ...(contextUploads?.length ? { context_uploads: contextUploads } : {}),
  };

  const response = await apiClient.post<SpaceAgentSendReceiptEnvelope>(
    "/api/v1/messages",
    {
      space_id: spaceId,
      content,
      ...(options?.parent_id
        ? {
            parent_id: options.parent_id,
            reply_to_message_id: options.parent_id,
          }
        : {}),
      ...(acceptedAttachments ? { attachments: acceptedAttachments } : {}),
      ...(Object.keys(metadata).length > 0 ? { metadata } : {}),
    },
  );
  return normalizeSendReceipt(response.data);
}

/**
 * Store an uploaded file reference in context so agents can discover it.
 * This is step 2 of the upload pipeline (UPLOADS-CONTEXT-001):
 *   1. Upload file → /api/v1/uploads/
 *   2. Store in context → /api/v1/context  (this function)
 *   3. Send message → /api/v1/messages
 *
 * Best-effort: failures are logged but don't block the message send.
 */
export async function storeUploadInContext(
  spaceId: string,
  attachment: {
    id: string;
    filename: string;
    content_type: string;
    size_bytes: number;
    url: string;
    uploaded_at?: string;
    uploaded_by?: string;
    upload_origin?: "paste" | "picker" | string;
  },
): Promise<string | null> {
  try {
    const uploadedAt = attachment.uploaded_at || new Date().toISOString();
    const contextKey = buildUploadContextKey(
      attachment,
      resolveUploadContextTimestampMs(uploadedAt),
    );
    await apiClient.post("/api/v1/context", {
      key: contextKey,
      value: JSON.stringify({
        type: "file_upload",
        attachment_id: attachment.id,
        filename: attachment.filename,
        content_type: attachment.content_type,
        file_type: attachment.content_type,
        size: attachment.size_bytes,
        url: attachment.url,
        uploaded_at: uploadedAt,
        uploaded_by: attachment.uploaded_by || null,
        upload_origin: attachment.upload_origin || null,
      }),
      space_id: spaceId,
    });
    return contextKey;
  } catch (err) {
    console.warn("storeUploadInContext failed (best-effort):", err);
    return null;
  }
}

function safeContextKeyPart(value: string): string {
  const normalized = value
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80);
  return normalized || "upload";
}

export function buildUploadContextKey(
  attachment: { id: string; filename: string },
  timestampMs = Date.now(),
): string {
  const safeName = safeContextKeyPart(attachment.filename || "upload");
  const safeId = safeContextKeyPart(attachment.id || "pending").slice(0, 32);
  return `upload:${timestampMs}:${safeName}:${safeId}`;
}

function resolveUploadContextTimestampMs(uploadedAt?: string | null) {
  if (!uploadedAt) return Date.now();
  const parsed = Date.parse(uploadedAt);
  return Number.isFinite(parsed) ? parsed : Date.now();
}

export async function postSpaceAgentAction(payload: SpaceAgentActionPayload) {
  const response = await apiClient.post("/api/v1/messages/actions", payload);
  return response.data;
}

export async function resolveSpaceAgentWidget(
  payload: ResolveSpaceAgentWidgetPayload,
) {
  const response = await apiClient.post<SpaceAgentResolvedWidgetResource>(
    "/api/v1/messages/widgets/resolve",
    payload,
    {
      timeout: 10000,
    },
  );
  return response.data;
}

export type SpaceAgentToolCallRecord = {
  tool_call_id: string;
  tool_name?: string | null;
  tool_action?: string | null;
  resource_uri?: string | null;
  kind?: string | null;
  arguments?: Record<string, unknown> | null;
  initial_data?: Record<string, unknown> | null;
  status?: string | null;
};

export async function getSpaceAgentToolCall(toolCallId: string) {
  const response = await apiClient.get<SpaceAgentToolCallRecord>(
    `/api/v1/tool-calls/${encodeURIComponent(toolCallId)}`,
  );
  return response.data;
}

function decodeBase64Utf8(value: string) {
  if (typeof window === "undefined") return value;
  try {
    const binary = window.atob(value);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  } catch {
    return value;
  }
}

function normalizeMcpReadResource(
  result: McpReadResourceResult | null | undefined,
  resourceUri: string,
): SpaceAgentResolvedWidgetResource {
  const contents = Array.isArray(result?.contents) ? result.contents : [];
  const primary = contents.find(
    (item) =>
      typeof item?.text === "string" ||
      typeof item?.blob === "string" ||
      typeof item?.uri === "string",
  );

  const mimeType =
    primary?.mimeType || primary?.mime_type || "text/html;profile=mcp-app";
  const html =
    typeof primary?.text === "string"
      ? primary.text
      : typeof primary?.blob === "string"
        ? decodeBase64Utf8(primary.blob)
        : null;

  return {
    html,
    resource_url: null,
    resource_mime_type: mimeType,
    title: resourceUri.replace(/^ui:\/\//, ""),
    csp: null,
  };
}

export async function postMcpJsonRpc<T = unknown>(
  method: string,
  params?: Record<string, unknown>,
) {
  const response = await apiClient.post<string | McpJsonRpcEnvelope<T>>(
    resolveMcpRequestUrl(config.mcpUrl),
    {
      jsonrpc: "2.0",
      id: `ax-${method}-${Date.now()}`,
      method,
      params: params || {},
    },
    {
      responseType: "text",
      headers: {
        Accept: "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
      },
      transformResponse: [(value) => value],
    },
  );

  const envelope = normalizeMcpJsonRpcEnvelope<T>(response.data);

  if (envelope?.error) {
    throw new Error(
      envelope.error.message || `${method} failed on the MCP server.`,
    );
  }

  return envelope.result as T;
}

/** Post JSON-RPC directly to MCP server (bypasses backend API) */
export async function postMcpJsonRpcDirect<T = unknown>(
  method: string,
  params?: Record<string, unknown>,
  options?: { authenticated?: boolean; spaceId?: string | null },
) {
  const url = resolveMcpDirectUrl(config.mcpUrl);
  const requestTimeoutMs =
    method === "resources/read"
      ? MCP_DIRECT_RESOURCE_READ_TIMEOUT_MS
      : MCP_DIRECT_TOOL_CALL_TIMEOUT_MS;

  const extractResultErrorText = (result: unknown): string | null => {
    if (!result || typeof result !== "object") return null;
    const content = Array.isArray((result as { content?: unknown }).content)
      ? ((result as { content?: unknown[] }).content ?? [])
      : [];
    for (const item of content) {
      if (!item || typeof item !== "object") continue;
      if (
        (item as { type?: unknown }).type === "text" &&
        typeof (item as { text?: unknown }).text === "string"
      ) {
        return (item as { text: string }).text;
      }
    }
    return null;
  };

  const isAuthLikeToolError = (result: unknown) => {
    if (!result || typeof result !== "object") return false;
    if ((result as { isError?: unknown }).isError !== true) return false;
    const text = extractResultErrorText(result);
    return /authentication required|presented bearer token invalid|unauthorized|access denied|forbidden/i.test(
      text || "",
    );
  };

  // Proactive refresh: if the token is expired, refresh before the first attempt
  if (options?.authenticated && storage.isTokenExpired(null, 30)) {
    await storage.refreshTokens();
  }

  let sessionIdForRequest: string | null = null;
  if (options?.authenticated && method !== "initialize") {
    sessionIdForRequest = await ensureMcpDirectSession(options);
  }

  let response = await sendMcpDirectRequest(
    url,
    method,
    params || {},
    options,
    requestTimeoutMs,
    sessionIdForRequest,
  );

  // 401 retry: refresh the browser session and retry once
  if (response.status === 401 && options?.authenticated) {
    clearMcpDirectSession();
    const refreshed = await storage.refreshTokens();
    if (refreshed) {
      sessionIdForRequest = await ensureMcpDirectSession(options);
      response = await sendMcpDirectRequest(
        url,
        method,
        params || {},
        options,
        requestTimeoutMs,
        sessionIdForRequest,
      );
    }
  }

  if (response.status === 400 && options?.authenticated) {
    const errorText = await response.text();
    if (/missing session|session not found|invalid session/i.test(errorText)) {
      clearMcpDirectSession();
      sessionIdForRequest = await ensureMcpDirectSession(options);
      response = await sendMcpDirectRequest(
        url,
        method,
        params || {},
        options,
        requestTimeoutMs,
        sessionIdForRequest,
      );
    } else {
      throw new Error(`MCP server returned 400 for ${method}`);
    }
  }

  if (!response.ok) {
    throw new Error(`MCP server returned ${response.status} for ${method}`);
  }

  let envelope = normalizeMcpJsonRpcEnvelope<T>(await response.text());

  if (options?.authenticated && isAuthLikeToolError(envelope.result)) {
    const refreshed = await storage.refreshTokens();
    if (refreshed) {
      clearMcpDirectSession();
      sessionIdForRequest = await ensureMcpDirectSession(options);
      response = await sendMcpDirectRequest(
        url,
        method,
        params || {},
        options,
        requestTimeoutMs,
        sessionIdForRequest,
      );
      if (!response.ok) {
        throw new Error(`MCP server returned ${response.status} for ${method}`);
      }
      envelope = normalizeMcpJsonRpcEnvelope<T>(await response.text());
    }
  }

  if (envelope?.error) {
    throw new Error(
      envelope.error.message || `${method} failed on the MCP server.`,
    );
  }

  return envelope.result as T;
}

export async function readMcpAppResource(resourceUri: string) {
  const readUri = canonicalizeMcpResourceUriForRead(resourceUri);
  const appPath = mcpResourceUriToAppPath(readUri);
  const response = await fetch(resolveMcpAppsUrl(config.mcpUrl, appPath), {
    method: "GET",
    headers: {
      Accept: "text/html, text/html;profile=mcp-app",
    },
  });

  if (!response.ok) {
    throw new Error(
      `MCP app server returned ${response.status} for ${readUri}`,
    );
  }

  return {
    html: await response.text(),
    resource_url: null,
    resource_mime_type:
      response.headers.get("content-type") || "text/html;profile=mcp-app",
    title: resourceUri.replace(/^ui:\/\//, ""),
    csp: null,
  };
}

export async function resolveSpaceAgentWidgetFromMcp(
  payload: ResolveSpaceAgentWidgetPayload,
) {
  return readMcpAppResource(payload.resource_uri);
}

export async function proxyMcpToolCall(
  toolName: string,
  args?: Record<string, unknown>,
  options?: { spaceId?: string | null },
) {
  return postMcpJsonRpcDirect(
    "tools/call",
    { name: toolName, arguments: args || {} },
    { authenticated: true, spaceId: options?.spaceId },
  );
}

export async function proxyMcpResourceRead(resourceUri: string) {
  const resource = await readMcpAppResource(resourceUri);
  return {
    contents: [
      {
        uri: canonicalizeMcpResourceUriForRead(resourceUri),
        mimeType: resource.resource_mime_type || "text/html;profile=mcp-app",
        text: resource.html || "",
      },
    ],
  };
}

function buildMcpDirectHeaders(
  options?: { authenticated?: boolean; spaceId?: string | null },
  sessionId = mcpDirectSessionId,
): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
  };
  if (options?.authenticated) {
    const token = storage.getUserToken();
    const currentSpaceId = options.spaceId || storage.getCurrentSpaceId?.();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    if (currentSpaceId) {
      headers["X-Space-Id"] = currentSpaceId;
    }
    if (sessionId) {
      headers["Mcp-Session-Id"] = sessionId;
    }
  }
  return headers;
}

function resolveMcpDirectSpaceId(options?: {
  authenticated?: boolean;
  spaceId?: string | null;
}) {
  return options?.spaceId || storage.getCurrentSpaceId?.() || null;
}

function mcpDirectSessionKey(spaceId: string | null) {
  return spaceId || "__default__";
}

function clearMcpDirectSession() {
  mcpDirectSessionId = null;
  mcpDirectSessionSpaceId = null;
  mcpDirectSessionPromises.clear();
}

function buildMcpDirectBody(method: string, params?: Record<string, unknown>) {
  if (method.startsWith("notifications/")) {
    return JSON.stringify({
      jsonrpc: "2.0",
      method,
      params: params || {},
    });
  }

  return JSON.stringify({
    jsonrpc: "2.0",
    id: `ax-direct-${method}-${Date.now()}`,
    method,
    params: params || {},
  });
}

async function sendMcpDirectRequest(
  url: string,
  method: string,
  params: Record<string, unknown>,
  options: { authenticated?: boolean; spaceId?: string | null } | undefined,
  timeoutMs: number,
  sessionId = mcpDirectSessionId,
) {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await fetch(url, {
      method: "POST",
      headers: buildMcpDirectHeaders(options, sessionId),
      body: buildMcpDirectBody(method, params),
      signal: controller.signal,
    });
  } catch (error) {
    if (
      (error instanceof DOMException && error.name === "AbortError") ||
      (error instanceof Error &&
        /signal is aborted without reason/i.test(error.message))
    ) {
      throw new Error(`MCP ${method} request timed out after ${timeoutMs}ms.`);
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

async function ensureMcpDirectSession(options?: {
  authenticated?: boolean;
  spaceId?: string | null;
}) {
  const spaceId = resolveMcpDirectSpaceId(options);
  if (mcpDirectSessionId && mcpDirectSessionSpaceId === spaceId) {
    return mcpDirectSessionId;
  }
  if (mcpDirectSessionId && mcpDirectSessionSpaceId !== spaceId) {
    mcpDirectSessionId = null;
    mcpDirectSessionSpaceId = null;
  }

  const sessionKey = mcpDirectSessionKey(spaceId);
  const pending = mcpDirectSessionPromises.get(sessionKey);
  if (pending) return pending;

  const nextPromise = initializeMcpDirectSessionWithAuthRetry(options, spaceId)
    .then((sessionId) => {
      if (sessionId) {
        mcpDirectSessionId = sessionId;
        mcpDirectSessionSpaceId = spaceId;
      }
      return sessionId;
    })
    .finally(() => {
      mcpDirectSessionPromises.delete(sessionKey);
    });
  mcpDirectSessionPromises.set(sessionKey, nextPromise);
  return nextPromise;
}

class McpDirectHttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "McpDirectHttpError";
  }
}

async function initializeMcpDirectSessionWithAuthRetry(
  options: { authenticated?: boolean; spaceId?: string | null } | undefined,
  spaceId: string | null,
) {
  try {
    return await initializeMcpDirectSession(options, spaceId);
  } catch (error) {
    if (
      error instanceof McpDirectHttpError &&
      error.status === 401 &&
      options?.authenticated !== false
    ) {
      const refreshed = await storage.refreshTokens();
      if (refreshed) {
        return initializeMcpDirectSession(options, spaceId);
      }
    }
    throw error;
  }
}

async function initializeMcpDirectSession(
  options?: {
    authenticated?: boolean;
    spaceId?: string | null;
  },
  spaceId = resolveMcpDirectSpaceId(options),
): Promise<string | null> {
  const url = resolveMcpDirectUrl(config.mcpUrl);
  const response = await sendMcpDirectRequest(
    url,
    "initialize",
    {
      protocolVersion: MCP_PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: "Commonflame Portal MCP Apps", version: "1.0.0" },
    },
    { authenticated: true, spaceId: options?.spaceId },
    MCP_DIRECT_SESSION_TIMEOUT_MS,
    null,
  );

  if (!response.ok) {
    throw new McpDirectHttpError(
      `MCP server returned ${response.status} for initialize`,
      response.status,
    );
  }

  const sessionId = response.headers?.get?.("mcp-session-id");
  await response.text().catch(() => null);
  if (!sessionId) return null;

  const initialized = await sendMcpDirectRequest(
    url,
    "notifications/initialized",
    {},
    { authenticated: true, spaceId: options?.spaceId },
    MCP_DIRECT_SESSION_TIMEOUT_MS,
    sessionId,
  );
  await initialized.text().catch(() => null);
  return sessionId;
}

function canonicalizeMcpResourceUriForRead(resourceUri: string) {
  // Task detail card payloads can carry a task-specific URI for host routing,
  // but the MCP server registers one static HTML app resource for the surface.
  if (/^ui:\/\/tasks\/detail\/[^?#]+/i.test(resourceUri)) {
    return "ui://tasks/detail";
  }
  return resourceUri;
}

function mcpResourceUriToAppPath(resourceUri: string) {
  const normalized = resourceUri.replace(/^ui:\/\//i, "").replace(/^\/+/, "");
  const [primitive, surface] = normalized.split("/");

  if (primitive === "tasks" && surface === "detail") return "tasks/detail";
  if (primitive === "context" && surface === "graph") return "context/graph";
  if (primitive) return primitive;

  return normalized;
}

function isMcpJsonRpcEnvelope<T>(
  value: unknown,
): value is McpJsonRpcEnvelope<T> {
  return Boolean(value && typeof value === "object");
}

function extractSseDataLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trim())
    .filter(Boolean);
}

export function normalizeMcpJsonRpcEnvelope<T = unknown>(
  payload: string | McpJsonRpcEnvelope<T>,
) {
  if (isMcpJsonRpcEnvelope<T>(payload)) {
    return payload;
  }

  const raw = String(payload || "").trim();
  if (!raw) {
    throw new Error("Empty response from the MCP server.");
  }

  const dataLines = extractSseDataLines(raw);
  const candidate =
    dataLines.length > 0 ? dataLines[dataLines.length - 1] : raw;

  try {
    return JSON.parse(candidate) as McpJsonRpcEnvelope<T>;
  } catch (error) {
    throw new Error(
      error instanceof Error
        ? `Invalid MCP response payload: ${error.message}`
        : "Invalid MCP response payload.",
    );
  }
}

export async function summarizeSpaceAgentMessage(messageId: string) {
  const response = await apiClient.post<SpaceAgentSummaryResponse>(
    `/api/v1/messages/${messageId}/summarize`,
  );
  return response.data;
}

export async function getSpaceAgentDirectory(spaceId: string) {
  // Fetch actual agents in this space (via agent_space_access), not user members.
  // The agents endpoint returns profiles with name/status that map to the
  // mention-resolution directory the frontend uses for isKnownTarget checks.
  const params = new URLSearchParams({ space_id: spaceId, limit: "200" });
  const response = await apiClient.get<{
    agents?: Array<Record<string, unknown>>;
    items?: Array<Record<string, unknown>>;
    total?: number;
  }>(`/api/v1/agents?${params.toString()}`);

  const raw = response.data;
  const agents: Array<Record<string, unknown>> = raw.agents || raw.items || [];

  const members: SpaceAgentMember[] = agents.map((a) => {
    const capabilitySummary = a.specialization ?? a.bio ?? a.description ?? "";

    return {
      id: String(a.id ?? a.agent_id ?? ""),
      handle: String(a.handle ?? a.name ?? a.agent_name ?? ""),
      display_name: String(a.display_name ?? a.name ?? a.agent_name ?? ""),
      active: a.status === "active",
      // Raw availability fields from serialize_agent — consumed by
      // deriveAvailabilityKey() so the @mention surfaces reflect real liveness.
      status: a.status != null ? String(a.status) : null,
      lifecycle_state:
        a.lifecycle_state != null ? String(a.lifecycle_state) : null,
      last_heartbeat:
        a.last_heartbeat != null
          ? String(a.last_heartbeat)
          : a.last_heartbeat_at != null
            ? String(a.last_heartbeat_at)
            : a.last_seen != null
              ? String(a.last_seen)
              : null,
      presence_fresh:
        typeof a.presence_fresh === "boolean"
          ? a.presence_fresh
          : typeof a.is_online === "boolean"
            ? a.is_online
            : null,
      presence_age_seconds:
        typeof a.presence_age_seconds === "number"
          ? a.presence_age_seconds
          : null,
      is_online: typeof a.is_online === "boolean" ? a.is_online : null,
      last_seen: a.last_seen != null ? String(a.last_seen) : null,
      last_heartbeat_at:
        a.last_heartbeat_at != null ? String(a.last_heartbeat_at) : null,
      last_active_at:
        a.last_active_at != null ? String(a.last_active_at) : null,
      presence_source:
        a.presence_source != null ? String(a.presence_source) : null,
      runtime_location: a.origin
        ? { kind: String(a.origin), label: String(a.origin) }
        : null,
      capabilities: Array.isArray(a.capabilities)
        ? a.capabilities.map((item) => String(item)).filter(Boolean)
        : null,
      capability_summary: String(capabilitySummary),
    };
  });

  return { members };
}

export async function getSpaceAgentSpaces() {
  const response =
    await apiClient.get<SpaceAgentSpacesResponse>("/api/v1/spaces");
  return normalizeSpacesResponse(response.data);
}

export async function switchSpaceAgentSpace(spaceId: string) {
  const response = await apiClient.post("/api/spaces/switch", {
    space_id: spaceId,
  });
  if (response.data?.new_token) {
    storage.setUserToken(response.data.new_token);
    window.dispatchEvent(new CustomEvent("auth:token-refreshed"));
  }
  return response.data;
}
