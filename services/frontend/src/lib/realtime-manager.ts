/**
 * Realtime Connection Manager
 * Handles SSE and WebSocket connections with automatic reconnection
 * and React Query cache integration
 */

import { QueryClient } from "@tanstack/react-query";
import config from "@/config/environment";
import { getSseLatencyMs, isRemoteLoopbackApiTarget } from "./runtime-origin";
import { storage } from "./storage";

type ConnectionState = "disconnected" | "connecting" | "connected" | "error";

interface RealtimeConfig {
  queryClient: QueryClient;
  maxReconnectAttempts?: number;
  reconnectDelay?: number;
  heartbeatInterval?: number;
}

interface MessagePayload {
  id: string;
  content: string;
  author: string;
  org_id: string;
  timestamp: string;
  mentions?: string[];
  parent_id?: string | null;
  parent_message_id?: string | null;
  response_to?: string | null;
  parentId?: string | null;
}

export interface TaskActorPayload {
  id: string;
  type?: string;
  display_name?: string | null;
  owner_user_id?: string | null;
}

export interface TaskUpdatePayload {
  id: string;
  title?: string;
  status?: string;
  priority?: string;
  work_status?: string | null;
  due_date?: string | null;
  assigned_agent_id?: string | null;
  assigned_agent?: Record<string, unknown> | null;
  assigned_to?: string | null;
  claimed_by?: string | null;
  updated_at?: string;
  [key: string]: any;
}

export interface NormalizedTaskEvent {
  type: string;
  task: TaskUpdatePayload;
  actor?: TaskActorPayload;
  adapter?: string;
  org_id?: string;
  raw?: Record<string, unknown>;
}

interface IdentityViewerPayload {
  id: string;
  display_name?: string | null;
  handle?: string | null;
}

interface IdentityAgentPreview {
  id: string;
  name?: string;
  owner_user_id?: string | null;
  visibility?: string | null;
  status?: string | null;
  mode?: string | null;
}

interface IdentityContext {
  viewer: IdentityViewerPayload;
  space_id?: string | null;
  owned_agents: IdentityAgentPreview[];
  team_agents_preview: IdentityAgentPreview[];
  team_agents_total: number;
  server_time?: string;
}

interface AgentPresenceEvent {
  event: string;
  org_id?: string | null;
  space_id?: string | null;
  last_seen?: string | null;
  status?: string | null;
  mode?: string | null;
  agent: {
    id: string;
    name?: string;
    owner_user_id?: string | null;
    visibility?: string | null;
    agent_status?: string | null;
  };
}

export interface ReactionEvent {
  agent_id: string;
  emoji: string;
  parent_message_id: string;
  new_count: number;
  reactor_id: string;
  reactor_name: string;
}

export interface AgentActivityEvent {
  agent_id?: string;
  agent_name?: string;
  tool_name?: string;
  status?:
    | "queued"
    | "dispatched"
    | "started"
    | "processing"
    | "completed"
    | "error"
    | string;
  parent_message_id?: string;
  target_task_id?: string;
  task_id?: string;
  task?: Record<string, unknown> & { id?: string; task_id?: string };
  timestamp?: string;
  details?: string;
  command?: unknown;
  command_line?: unknown;
  input?: unknown;
  args?: unknown;
  parameters?: unknown;
  phase?: unknown;
}

export interface AgentProcessingEvent {
  agent_id: string;
  agent_name: string;
  message_id: string;
  status:
    | "queued"
    | "dispatched"
    | "started"
    | "processing"
    | "thinking"
    | "working";
}

export interface AgentErrorEvent {
  agent_id: string;
  agent_name: string;
  message_id: string;
  error: string;
  error_type?: string;
}

export interface AgentSkippedEvent {
  agent_id: string;
  agent_name: string;
  message_id: string;
  reason: string;
}

// --- Tool call context store ---
// Stores enriched tool call data from SSE `tool_call_completed` events so the
// widget enrichment hook can populate `tool_input` for context-specific rendering
// (e.g. showing a single task detail instead of the full task list).
export interface ToolCallContext {
  tool_name: string;
  tool_action: string | null;
  kind: string | null;
  arguments: Record<string, unknown> | null;
  resource_uri: string | null;
}

const toolCallContextMap = new Map<string, ToolCallContext>();
const MAX_TOOL_CALL_CONTEXT = 100;

/** Store tool call context from an SSE event, keyed by message ID. */
export function storeToolCallContext(
  messageId: string | undefined | null,
  ctx: ToolCallContext,
): void {
  if (!messageId) return;
  toolCallContextMap.set(messageId, ctx);
  // Also store by tool_name as a fallback for content-enriched widgets
  // that don't have a message-level match
  toolCallContextMap.set(`tool:${ctx.tool_name}:latest`, ctx);
  // Prune oldest entries
  if (toolCallContextMap.size > MAX_TOOL_CALL_CONTEXT) {
    const iter = toolCallContextMap.keys();
    const oldest = iter.next().value;
    if (oldest) toolCallContextMap.delete(oldest);
  }
}

/** Look up stored tool call context by message ID or tool name. */
export function getToolCallContext(
  messageId?: string | null,
  toolName?: string | null,
): ToolCallContext | null {
  if (messageId) {
    const byMsg = toolCallContextMap.get(messageId);
    if (byMsg) return byMsg;
  }
  if (toolName) {
    const byTool = toolCallContextMap.get(`tool:${toolName}:latest`);
    if (byTool) return byTool;
  }
  return null;
}

/** Clear stored tool call contexts (e.g. on space switch). */
export function clearToolCallContexts(): void {
  toolCallContextMap.clear();
}

export interface MessageUpdatedEvent {
  id: string;
  ai_summary?: string;
  summarized_at?: string;
  [key: string]: unknown;
}

export interface MessageStreamChunk {
  message_id: string;
  conversation_id: string;
  agent_id: string;
  type: "chunk" | "done" | "error";
  delta: string;
  sequence: number;
}

interface Task extends TaskUpdatePayload {
  title: string;
  status: string;
}

interface TasksQueryData {
  tasks: Task[];
  total?: number;
  [key: string]: any;
}

type TaskEventCallback = (event: NormalizedTaskEvent) => void;
type IdentityCallback = (identity: IdentityContext) => void;
type AgentPresenceCallback = (event: AgentPresenceEvent) => void;

function isHostedPaxaiHostname(hostname: string): boolean {
  return !["localhost", "127.0.0.1", "::1"].includes(hostname);
}

export class RealtimeManager {
  private sseConnection: EventSource | null = null;
  private wsConnection: WebSocket | null = null;
  private state: ConnectionState = "disconnected";
  private reconnectAttempts = 0;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private silentRefetchTimer: NodeJS.Timeout | null = null;
  private heartbeatTimer: NodeJS.Timeout | null = null;
  private refetchDebounceTimer: NodeJS.Timeout | null = null;
  private agentRefetchDebounceTimer: NodeJS.Timeout | null = null;
  private config: Required<RealtimeConfig>;
  private lastEventTime = Date.now();
  private token: string | null = null;
  private tokenRefreshHandler: ((e: CustomEvent) => void) | null = null;
  private tokenRefreshTimer: NodeJS.Timeout | null = null;
  private tokenIssuedAt = Date.now();
  private tokenRefreshFailures = 0;
  private readonly maxTokenRefreshFailures = 3;
  private visibilityChangeHandler: (() => void) | null = null;
  private lastVisibleTime = Date.now();
  private connectionStartTime = 0;

  // Connection lifecycle constants
  private readonly maxConnectionAge = 30 * 60 * 1000; // 30 min
  private readonly backgroundReconnectThreshold = 5 * 60 * 1000; // 5 min
  private readonly staleConnectionThreshold = 60 * 1000; // 1 min

  // SSE event listener references for cleanup
  private sseMessageListener: ((event: MessageEvent) => void) | null = null;
  private sseMentionListener: ((event: MessageEvent) => void) | null = null;
  private sseTaskUpdateListener: ((event: MessageEvent) => void) | null = null;
  private sseIdentityBootstrapListener: ((event: MessageEvent) => void) | null =
    null;
  private sseAgentPresenceListener: ((event: MessageEvent) => void) | null =
    null;
  private sseBootstrapListener: ((event: MessageEvent) => void) | null = null;
  private sseReactionListener: ((event: MessageEvent) => void) | null = null;
  private sseMessageDeletedListener: ((event: MessageEvent) => void) | null =
    null;
  private sseMessageUpdatedListener: ((event: MessageEvent) => void) | null =
    null;
  private sseMessageEditedListener: ((event: MessageEvent) => void) | null =
    null;
  private ssePingListener: (() => void) | null = null;

  // Single-flight guard to prevent duplicate connections
  private isConnecting = false;
  // Connection attempt stamp to invalidate stale in-flight connections
  private connectionAttemptId = 0;
  private reconnectSuspended = false;
  private reconnectSuspendedReason: string | null = null;

  // Task event callbacks for components not using React Query
  private taskEventCallbacks: Set<TaskEventCallback> = new Set();
  private identityCallbacks: Set<IdentityCallback> = new Set();
  private agentPresenceCallbacks: Set<AgentPresenceCallback> = new Set();
  private latestIdentityContext: IdentityContext | null = null;

  // Reaction event callbacks
  private reactionCallbacks: Set<(event: ReactionEvent) => void> = new Set();

  // Agent activity callbacks (tool calls, processing status)
  private agentActivityCallbacks: Set<(event: AgentActivityEvent) => void> =
    new Set();
  private sseAgentActivityListener: ((event: MessageEvent) => void) | null =
    null;

  // Cloud agent processing callbacks (started processing)
  private agentProcessingCallbacks: Set<(event: AgentProcessingEvent) => void> =
    new Set();
  private sseAgentProcessingListener: ((event: MessageEvent) => void) | null =
    null;

  // Cloud agent error callbacks (failed to process)
  private agentErrorCallbacks: Set<(event: AgentErrorEvent) => void> =
    new Set();
  private sseAgentErrorListener: ((event: MessageEvent) => void) | null = null;

  // Cloud agent skipped callbacks (paused, rate limited, inactive)
  private agentSkippedCallbacks: Set<(event: AgentSkippedEvent) => void> =
    new Set();
  private sseAgentSkippedListener: ((event: MessageEvent) => void) | null =
    null;

  // Message updated callbacks (summary arrival)
  private messageUpdatedCallbacks: Set<(event: MessageUpdatedEvent) => void> =
    new Set();

  // Message stream callbacks (SSE streaming contract v1)
  private messageStreamCallbacks: Set<(event: MessageStreamChunk) => void> =
    new Set();
  private sseMessageStreamListener: ((event: MessageEvent) => void) | null =
    null;

  // Exponential backoff configuration
  private readonly backoffBase = 250; // Start at 250ms
  private readonly backoffMax = 30000; // Cap at 30 seconds
  private readonly backoffJitter = 0.25; // ±25% jitter

  constructor(config: RealtimeConfig) {
    this.config = {
      queryClient: config.queryClient,
      maxReconnectAttempts: config.maxReconnectAttempts ?? 10,
      reconnectDelay: config.reconnectDelay ?? 1000,
      heartbeatInterval: config.heartbeatInterval ?? 30000,
    };

    // Listen for token refreshes to reconnect with new token
    // SSE needs the new token since it authenticates on each connection
    this.tokenRefreshHandler = (e: CustomEvent) => {
      console.log("🔄 Token refreshed, reconnecting SSE with new token");
      this.handleTokenRefresh();
    };

    window.addEventListener(
      "auth:token-refreshed",
      this.tokenRefreshHandler as EventListener,
    );

    // Listen for visibility changes to detect stale connections after idle
    this.visibilityChangeHandler = () => {
      if (document.visibilityState !== "visible") {
        this.lastVisibleTime = Date.now();
        return;
      }

      const backgroundDuration = Date.now() - this.lastVisibleTime;
      console.debug(
        `👁️ Page visible after ${Math.round(backgroundDuration / 1000)}s in background`,
      );

      // Always refetch posts to catch missed events during backgrounding
      this.config.queryClient.invalidateQueries({ queryKey: ["posts"] });

      // Force fresh connection after extended background time
      if (backgroundDuration > this.backgroundReconnectThreshold) {
        console.log(
          `🔄 Tab backgrounded for ${Math.round(backgroundDuration / 1000)}s, forcing fresh SSE`,
        );
        this.forceReconnect();
        return;
      }

      // Handle based on current connection state
      if (this.state === "connected") {
        const timeSinceLastEvent = Date.now() - this.lastEventTime;
        if (timeSinceLastEvent > this.staleConnectionThreshold) {
          console.log("🔄 SSE connection stale after idle, reconnecting...");
          this.handleConnectionError();
        }
      } else if (this.state === "error" || this.state === "disconnected") {
        if (this.reconnectSuspended) {
          console.debug(
            `SSE reconnect suspended${this.reconnectSuspendedReason ? `: ${this.reconnectSuspendedReason}` : ""}`,
          );
          return;
        }
        if (this.reconnectTimer || this.isConnecting) {
          console.debug("SSE reconnect already pending after page resume");
          return;
        }
        console.log("🔄 Page resumed, SSE not connected, reconnecting...");
        this.connectSSE();
      }
    };

    window.addEventListener("visibilitychange", this.visibilityChangeHandler);
  }

  /**
   * Force a fresh SSE connection, bypassing normal staleness checks.
   * Used after extended background periods or when connection age exceeds max.
   */
  private forceReconnect(): void {
    if (this.reconnectSuspended) {
      console.debug("SSE reconnect suspended, skipping forced reconnect");
      return;
    }
    console.log("🔄 Forcing fresh SSE connection...");
    this.cleanup();
    this.resetConnectionState();
    this.scheduleReconnectNow();
  }

  /**
   * Reset connection state for a fresh start.
   * Increments connectionAttemptId to invalidate any in-flight connect attempts.
   */
  private resetConnectionState(): void {
    this.state = "disconnected";
    this.reconnectAttempts = 0;
    this.isConnecting = false;
    this.connectionAttemptId++;
  }

  private scheduleReconnectNow(delay = 100): void {
    if (this.reconnectSuspended) {
      return;
    }
    if (this.reconnectTimer) {
      console.debug("SSE reconnect already scheduled");
      return;
    }

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connectSSE();
    }, delay);
  }

  private suspendReconnect(reason: string): void {
    this.reconnectSuspended = true;
    this.reconnectSuspendedReason = reason;
    this.state = "error";
    this.notifyState();
    this.isConnecting = false;
    this.cleanup();
    console.error(reason);
  }

  /**
   * Handle token refresh by gracefully reconnecting SSE
   */
  private async handleTokenRefresh(): Promise<void> {
    if (this.state !== "connected" || !this.sseConnection) {
      return;
    }

    console.log("🔄 Reconnecting SSE with refreshed token");
    this.sseConnection.close();
    this.sseConnection = null;
    this.resetConnectionState();
    this.scheduleReconnectNow();
  }

  /**
   * Connect to SSE endpoint for real-time updates
   */
  async connectSSE(): Promise<void> {
    if (this.reconnectSuspended) {
      console.debug(
        `SSE reconnect suspended${this.reconnectSuspendedReason ? `: ${this.reconnectSuspendedReason}` : ""}`,
      );
      return;
    }

    if (this.reconnectTimer) {
      console.debug("SSE reconnect already scheduled");
      return;
    }

    // Single-flight guard: prevent duplicate connection attempts
    if (this.isConnecting) {
      console.debug("SSE connection already in progress (single-flight)");
      return;
    }

    if (this.state === "connected") {
      console.debug("SSE already connected");
      return;
    }

    this.isConnecting = true;
    this.state = "connecting";
    this.notifyState();

    // Capture attempt ID to detect if this attempt becomes stale during async ops
    const attemptId = this.connectionAttemptId;

    this.token = await storage.getUserTokenAsync();

    // Check if this attempt was invalidated during token fetch
    if (attemptId !== this.connectionAttemptId) {
      console.debug("SSE connection attempt invalidated during token fetch");
      this.isConnecting = false;
      return;
    }

    // If no token, wait briefly and retry - token might be refreshing
    if (!this.token) {
      console.log("⏳ No auth token for SSE, waiting for authentication...");

      // Listen for token refresh event
      const waitForToken = new Promise<string | null>((resolve) => {
        const timeout = setTimeout(() => {
          resolve(null);
        }, 5000); // Wait up to 5 seconds

        const handler = () => {
          clearTimeout(timeout);
          window.removeEventListener("auth:token-refreshed", handler);
          resolve(storage.getUserToken());
        };
        window.addEventListener("auth:token-refreshed", handler);
      });

      this.token = await waitForToken;

      // Check if this attempt was invalidated during token wait
      if (attemptId !== this.connectionAttemptId) {
        console.debug("SSE connection attempt invalidated during token wait");
        this.isConnecting = false;
        return;
      }

      if (!this.token) {
        console.debug("❌ No auth token available for SSE after waiting");
        this.state = "error";
        this.notifyState();
        this.isConnecting = false;
        // Schedule a retry with backoff
        this.scheduleReconnect();
        return;
      }
      console.log("✅ Token received, proceeding with SSE connection");
    }

    const apiUrl = config.directApiUrl || window.location.origin;
    if (isRemoteLoopbackApiTarget(apiUrl, window.location.origin)) {
      this.suspendReconnect(
        `SSE reconnect disabled: remote page ${window.location.origin} cannot use loopback backend ${apiUrl}`,
      );
      return;
    }

    const spaceId = storage.getCurrentOrgId() ?? "";
    const encodedSpaceId = encodeURIComponent(spaceId);
    const sseUrl = `${apiUrl}/api/v1/sse/messages?space_id=${encodedSpaceId}`;

    try {
      console.debug("Connecting to SSE:", sseUrl);

      // Create SSE connection
      // Try with token in URL first, but also send cookies as fallback
      const tokenParam = encodeURIComponent(this.token);
      const separator = sseUrl.includes("?") ? "&" : "?";
      const authenticatedUrl = `${sseUrl}${separator}token=${tokenParam}`;

      // In production, we'll use withCredentials to send cookies as backup auth
      const isHostedPaxai = isHostedPaxaiHostname(window.location.hostname);

      // Final check before creating EventSource - prevent stale attempt from completing
      if (attemptId !== this.connectionAttemptId) {
        console.debug(
          "SSE connection attempt invalidated before EventSource creation",
        );
        this.isConnecting = false;
        return;
      }

      this.sseConnection = new EventSource(authenticatedUrl, {
        withCredentials: isHostedPaxai, // Send cookies on hosted deployments for fallback auth
      });

      // Connection opened
      this.sseConnection.onopen = () => {
        console.debug("SSE connection established");
        this.state = "connected";
        this.notifyState();
        this.reconnectAttempts = 0;
        this.reconnectSuspended = false;
        this.reconnectSuspendedReason = null;
        this.isConnecting = false; // Clear single-flight guard
        this.tokenIssuedAt = Date.now(); // Track when token was used
        this.connectionStartTime = Date.now(); // Track connection age
        this.lastEventTime = Date.now();
        this.tokenRefreshFailures = 0;
        this.stopSilentRefetch();
        this.startHeartbeat();
        this.startPreemptiveTokenRefresh(); // Start monitoring token age
        // Ensure freshest data on connect
        this.config.queryClient.refetchQueries({
          queryKey: ["posts"],
          type: "active",
          exact: false,
        });
      };

      // Store listener references for proper cleanup
      this.sseMessageListener = (event) => this.handleMessageEvent(event);
      this.sseMentionListener = (event) => this.handleMessageEvent(event);
      this.sseTaskUpdateListener = (event) => this.handleTaskEvent(event);
      this.sseIdentityBootstrapListener = (event) =>
        this.handleIdentityBootstrap(event);
      this.sseAgentPresenceListener = (event) =>
        this.handleAgentPresence(event);
      this.sseBootstrapListener = (event) => this.handleBootstrapEvent(event);
      this.sseReactionListener = (event) => this.handleReactionEvent(event);
      this.sseMessageDeletedListener = (event) =>
        this.handleMessageDeletedEvent(event);
      this.sseMessageUpdatedListener = (event) =>
        this.handleMessageUpdatedEvent(event);
      this.sseMessageEditedListener = (event) =>
        this.handleMessageEditedEvent(event);
      this.sseAgentActivityListener = (event) =>
        this.handleAgentActivityEvent(event);
      this.sseAgentProcessingListener = (event) =>
        this.handleAgentProcessingEvent(event);
      this.sseAgentErrorListener = (event) => this.handleAgentErrorEvent(event);
      this.sseAgentSkippedListener = (event) =>
        this.handleAgentSkippedEvent(event);
      this.sseMessageStreamListener = (event) =>
        this.handleMessageStreamEvent(event);
      this.ssePingListener = () => {
        this.lastEventTime = Date.now();
        // Silent heartbeat - no logging needed
      };

      // Attach event listeners
      this.sseConnection.addEventListener("message", this.sseMessageListener);
      this.sseConnection.addEventListener("mention", this.sseMentionListener);
      this.sseConnection.addEventListener(
        "task_update",
        this.sseTaskUpdateListener,
      );
      this.sseConnection.addEventListener(
        "identity_bootstrap",
        this.sseIdentityBootstrapListener,
      );
      this.sseConnection.addEventListener(
        "agent_presence",
        this.sseAgentPresenceListener,
      );
      this.sseConnection.addEventListener(
        "bootstrap",
        this.sseBootstrapListener,
      );
      this.sseConnection.addEventListener(
        "reaction_added",
        this.sseReactionListener,
      );
      this.sseConnection.addEventListener(
        "message_deleted",
        this.sseMessageDeletedListener,
      );
      this.sseConnection.addEventListener(
        "message_edited",
        this.sseMessageEditedListener,
      );
      this.sseConnection.addEventListener(
        "message_updated",
        this.sseMessageUpdatedListener,
      );
      this.sseConnection.addEventListener(
        "tool_call_completed",
        (event: MessageEvent) => {
          this.lastEventTime = Date.now();
          try {
            const data = JSON.parse(event.data);
            if (import.meta.env.DEV) {
              console.debug(
                "🧩 tool_call_completed SSE:",
                data.tool_name,
                data.resource_uri,
                data.kind,
                data.arguments,
              );
            }
            // Store tool call context so the enrichment hook can populate
            // tool_input on the widget descriptor for context-specific rendering.
            if (data.tool_name && (data.arguments || data.kind)) {
              storeToolCallContext(data.parent_message_id || data.message_id, {
                tool_name: data.tool_name,
                tool_action: data.tool_action || data.action || null,
                kind: data.kind || null,
                arguments: data.arguments || null,
                resource_uri: data.resource_uri || null,
              });
            }
          } catch {
            /* ignore parse errors */
          }
          this.config.queryClient.invalidateQueries({
            queryKey: ["space-agent-transcript"],
          });
        },
      );
      this.sseConnection.addEventListener(
        "agent_activity",
        this.sseAgentActivityListener,
      );
      this.sseConnection.addEventListener(
        "agent_processing",
        this.sseAgentProcessingListener,
      );
      this.sseConnection.addEventListener(
        "agent_error",
        this.sseAgentErrorListener,
      );
      this.sseConnection.addEventListener(
        "agent_skipped",
        this.sseAgentSkippedListener,
      );
      this.sseConnection.addEventListener(
        "message_stream",
        this.sseMessageStreamListener,
      );
      this.sseConnection.addEventListener("ping", this.ssePingListener);

      // Handle errors silently with smart recovery
      this.sseConnection.onerror = async (error) => {
        // Log at debug level to reduce console noise
        console.debug("SSE connection interrupted, handling silently...");

        // Check if we're offline first
        if (!navigator.onLine) {
          console.debug("Offline detected, will reconnect when online");
          this.state = "error";
          // Don't notify state to avoid UI flicker

          // Wait for online event
          const onlineHandler = () => {
            console.debug("Back online, reconnecting SSE...");
            window.removeEventListener("online", onlineHandler);
            this.reconnectAttempts = 0; // Reset for fresh start
            this.connectSSE();
          };
          window.addEventListener("online", onlineHandler);

          // Run silent refetch loop while offline
          this.startSilentRefetch();
          return;
        }

        // Check if this might be a token expiry
        const timeSinceLastEvent = Date.now() - this.lastEventTime;
        const isHostedPaxai = isHostedPaxaiHostname(window.location.hostname);
        const tokenLifetime = isHostedPaxai ? 60 * 60 * 1000 : 15 * 60 * 1000; // 60min hosted, 15min dev
        const isLikelyTokenExpired = timeSinceLastEvent > tokenLifetime * 0.8;

        if (isLikelyTokenExpired) {
          console.debug("Token likely expired, refreshing...");

          if (this.tokenRefreshFailures >= this.maxTokenRefreshFailures) {
            console.debug(
              "Token refresh failed repeatedly, backing off token refresh attempts.",
            );
            this.handleConnectionError();
            return;
          }

          // Try to refresh token immediately
          const refreshed = await storage.refreshTokens();

          if (refreshed) {
            console.debug("Token refreshed, reconnecting SSE...");
            // Immediate reconnect with fresh token
            this.reconnectAttempts = 0;
            this.tokenRefreshFailures = 0;
            this.connectSSE();
          } else {
            console.debug("Token refresh failed, retrying connection...");
            this.tokenRefreshFailures += 1;
            this.handleConnectionError();
          }
        } else {
          // Network blip or other transient error - use backoff
          console.debug("Network interruption, reconnecting with backoff...");
          this.handleConnectionError();
        }
      };
    } catch (error) {
      console.error("❌ Failed to create SSE connection:", error);
      this.isConnecting = false; // Clear single-flight guard on error
      this.handleConnectionError();
    }
  }

  /**
   * Handle incoming message events
   */
  private handleMessageEvent(event: MessageEvent): void {
    try {
      const data = JSON.parse(
        (event as MessageEvent).data as any,
      ) as MessagePayload & { server_time?: string };
      console.log("📨 New message event:", data);

      this.lastEventTime = Date.now();

      if (typeof window !== "undefined" && data.author) {
        const eventTimestamp = data.timestamp || data.server_time || null;
        const parentId =
          data.parent_id ??
          data.parent_message_id ??
          data.response_to ??
          data.parentId ??
          null;
        window.dispatchEvent(
          new CustomEvent("ax:message-received", {
            detail: {
              author: data.author,
              timestamp: eventTimestamp,
              org_id: data.org_id ?? null,
              id: data.id ?? null,
              parent_id: parentId,
            },
          }),
        );
      }

      // Measure server→client latency when available
      if (data.server_time) {
        const latencyMs = getSseLatencyMs(data.server_time);
        if (latencyMs != null) {
          if (latencyMs > 1500) {
            console.warn(`🐢 SSE latency ${latencyMs}ms`);
          } else {
            console.debug(`⚡ SSE latency ${latencyMs}ms`);
          }
        }
      }

      // Debounce immediate refetch so bursts of events coalesce
      // 800ms window to batch rapid agent messages and prevent scroll jank on mobile
      if (this.refetchDebounceTimer) clearTimeout(this.refetchDebounceTimer);
      this.refetchDebounceTimer = setTimeout(() => {
        // Invalidate transcript queries to trigger refetch of messages (with widget metadata)
        this.config.queryClient.invalidateQueries({
          queryKey: ["space-agent-transcript"],
        });
        // Keep legacy posts invalidation for backward compatibility
        this.config.queryClient.invalidateQueries({
          queryKey: ["posts"],
        });
      }, 800);

      // Debounced agent refetch so quick mentions stay reactive
      // Uses longer debounce (3s) to avoid hammering API during rapid message exchanges
      if (this.agentRefetchDebounceTimer)
        clearTimeout(this.agentRefetchDebounceTimer);
      this.agentRefetchDebounceTimer = setTimeout(() => {
        this.config.queryClient.invalidateQueries({
          queryKey: ["agents"],
        });
      }, 3000);

      // If message mentions current user, show notification and trigger notification refresh
      if (data.mentions?.includes(storage.getUsername() || "")) {
        this.showNotification("New mention", `${data.author}: ${data.content}`);
        // Trigger notification bell to refresh
        window.dispatchEvent(new CustomEvent("ax:notification-update"));
      }
    } catch (error) {
      console.error("Failed to parse message event:", error);
    }
  }

  /**
   * Notify listeners of SSE state changes
   */
  private notifyState(): void {
    window.dispatchEvent(
      new CustomEvent("sse:state", {
        detail: { state: this.state },
      }),
    );
  }

  /**
   * Handle bootstrap event with initial messages
   */
  private handleBootstrapEvent(event: MessageEvent): void {
    try {
      const payload = JSON.parse((event as MessageEvent).data as any);
      const posts = Array.isArray(payload?.posts) ? payload.posts : [];

      console.log(
        `🚀 SSE bootstrap received: ${posts.length} messages for org ${payload.org_id}`,
      );

      if (!posts.length) return;

      // Merge into ALL active ['posts'] caches (dedupe by id)
      const activeQueries = this.config.queryClient
        .getQueryCache()
        .findAll({ queryKey: ["posts"] });

      for (const query of activeQueries) {
        this.config.queryClient.setQueryData(
          query.queryKey,
          (old: any[] | undefined) => {
            const existing = new Map((old || []).map((p: any) => [p.id, p]));

            // Add bootstrap posts (will overwrite if duplicate ID)
            for (const p of posts) {
              existing.set(p.id, p);
            }

            // Maintain oldest→newest order
            return Array.from(existing.values()).sort(
              (a: any, b: any) =>
                new Date(a.uploaded_at || a.created_at).getTime() -
                new Date(b.uploaded_at || b.created_at).getTime(),
            );
          },
        );
      }

      // If no active queries, still set the data for the base ['posts'] key
      if (activeQueries.length === 0) {
        this.config.queryClient.setQueryData(["posts"], posts);
      }

      // Light refetch for freshness
      setTimeout(() => {
        this.config.queryClient.invalidateQueries({
          queryKey: ["space-agent-transcript"],
        });
        this.config.queryClient.refetchQueries({
          queryKey: ["posts"],
          type: "active",
          exact: false,
        });
      }, 500);

      console.log(
        `📦 Bootstrap applied: ${posts.length} posts to ${activeQueries.length || 1} cache(s)`,
      );
    } catch (e) {
      console.error("Bootstrap parse/apply error", e);
    }
  }

  /**
   * Handle incoming task events
   */
  private handleTaskEvent(event: MessageEvent): void {
    try {
      const raw = JSON.parse((event as MessageEvent).data as any) as Record<
        string,
        unknown
      >;

      const taskPayload: TaskUpdatePayload =
        raw && typeof raw === "object" && "task" in raw
          ? ({
              id: String((raw.task as Record<string, unknown>)?.id ?? ""),
              ...(raw.task as Record<string, unknown>),
            } as TaskUpdatePayload)
          : {
              id: String(raw?.id ?? ""),
              title: (raw?.title as string) ?? undefined,
              status: (raw?.status as string) ?? undefined,
              priority: (raw?.priority as string) ?? undefined,
              work_status: (raw?.work_status as string) ?? undefined,
              due_date: (raw?.due_date as string) ?? undefined,
              assigned_agent_id:
                (raw?.assigned_agent_id as string | null | undefined) ??
                (raw?.assigned_to as string | null | undefined) ??
                null,
              assigned_agent:
                (raw?.assigned_agent as Record<string, unknown>) ?? null,
              updated_at: (raw?.updated_at as string | undefined) ?? undefined,
            };

      // Ensure id is present
      if (!taskPayload.id && typeof raw?.id === "string") {
        taskPayload.id = raw.id;
      }

      const actorPayload: TaskActorPayload | undefined =
        raw && typeof raw === "object" && "actor" in raw
          ? (raw.actor as TaskActorPayload)
          : undefined;

      const normalized: NormalizedTaskEvent = {
        type: (raw?.type as string) ?? "updated",
        task: taskPayload,
        actor: actorPayload,
        adapter: raw?.adapter as string | undefined,
        org_id: raw?.org_id as string | undefined,
        raw: raw,
      };

      console.log("📋 Task event:", normalized.type, normalized);

      this.lastEventTime = Date.now();

      // Update React Query cache for components using it
      // Handle different event types: created, updated, deleted
      const eventType = normalized.type;

      if (eventType === "deleted") {
        // Remove task from cache
        this.config.queryClient.setQueryData(
          ["tasks"],
          (oldData: TasksQueryData | undefined) => {
            if (!oldData?.tasks) return oldData;
            return {
              ...oldData,
              tasks: oldData.tasks.filter(
                (taskItem: Task) => taskItem.id !== taskPayload.id,
              ),
              total: oldData.total ? oldData.total - 1 : undefined,
            };
          },
        );

        // Also update any list queries
        this.config.queryClient.setQueriesData(
          { queryKey: ["tasks", "list"] },
          (oldData: Task[] | undefined) => {
            if (!oldData) return oldData;
            return oldData.filter((task) => task.id !== taskPayload.id);
          },
        );
      } else if (eventType === "created") {
        // Add new task to cache
        this.config.queryClient.setQueryData(
          ["tasks"],
          (oldData: TasksQueryData | undefined) => {
            if (!oldData?.tasks) {
              return { tasks: [taskPayload as Task], total: 1 };
            }
            // Check if task already exists (avoid duplicates)
            const exists = oldData.tasks.some(
              (t: Task) => t.id === taskPayload.id,
            );
            if (exists) {
              return oldData;
            }
            return {
              ...oldData,
              tasks: [taskPayload as Task, ...oldData.tasks],
              total: oldData.total
                ? oldData.total + 1
                : oldData.tasks.length + 1,
            };
          },
        );

        // Also update list queries
        this.config.queryClient.setQueriesData(
          { queryKey: ["tasks", "list"] },
          (oldData: Task[] | undefined) => {
            if (!oldData) return [taskPayload as Task];
            const exists = oldData.some((t) => t.id === taskPayload.id);
            if (exists) return oldData;
            return [taskPayload as Task, ...oldData];
          },
        );
      } else {
        // Default: update existing task
        this.config.queryClient.setQueryData(
          ["tasks"],
          (oldData: TasksQueryData | undefined) => {
            if (!oldData?.tasks) return oldData;

            return {
              ...oldData,
              tasks: oldData.tasks.map((taskItem: Task) => {
                if (taskItem.id !== taskPayload.id) return taskItem;

                return {
                  ...taskItem,
                  ...taskPayload,
                  assigned_agent_id:
                    taskPayload.assigned_agent_id ?? taskItem.assigned_agent_id,
                  assigned_agent:
                    taskPayload.assigned_agent ?? taskItem.assigned_agent,
                  assigned_to: taskPayload.assigned_to ?? taskItem.assigned_to,
                  claimed_by: taskPayload.claimed_by ?? taskItem.claimed_by,
                  updated_at: taskPayload.updated_at ?? taskItem.updated_at,
                  work_status: taskPayload.work_status ?? taskItem.work_status,
                  due_date: taskPayload.due_date ?? taskItem.due_date,
                } as Task;
              }),
            };
          },
        );

        // Also update list queries
        this.config.queryClient.setQueriesData(
          { queryKey: ["tasks", "list"] },
          (oldData: Task[] | undefined) => {
            if (!oldData) return oldData;
            return oldData.map((task) => {
              if (task.id !== taskPayload.id) return task;
              return {
                ...task,
                ...taskPayload,
                assigned_agent_id:
                  taskPayload.assigned_agent_id ?? task.assigned_agent_id,
                assigned_to: taskPayload.assigned_to ?? task.assigned_to,
                claimed_by: taskPayload.claimed_by ?? task.claimed_by,
              } as Task;
            });
          },
        );
      }

      // Also invalidate the specific task query if it exists
      this.config.queryClient.invalidateQueries({
        queryKey: ["task", taskPayload.id],
      });

      // Invalidate tasks stats queries
      this.config.queryClient.invalidateQueries({
        queryKey: ["tasks", "stats"],
        refetchType: "none",
      });

      // Call registered callbacks for components using local state
      this.taskEventCallbacks.forEach((callback) => {
        try {
          callback(normalized);
        } catch (error) {
          console.error("Error in task event callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse task event:", error);
    }
  }

  /**
   * Handle identity bootstrap payload
   */
  private handleIdentityBootstrap(event: MessageEvent): void {
    try {
      const payload = JSON.parse(
        (event as MessageEvent).data as any,
      ) as IdentityContext;
      console.log("🪪 Identity bootstrap:", payload);
      this.lastEventTime = Date.now();
      this.latestIdentityContext = payload;
      this.identityCallbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error("Error in identity callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse identity bootstrap event:", error);
    }
  }

  /**
   * Handle agent presence updates
   */
  private handleAgentPresence(event: MessageEvent): void {
    try {
      const payload = JSON.parse(
        (event as MessageEvent).data as any,
      ) as AgentPresenceEvent;
      console.log("👥 Agent presence event:", payload);
      this.lastEventTime = Date.now();

      if (this.latestIdentityContext && payload.agent?.id) {
        const updatedContext: IdentityContext = {
          ...this.latestIdentityContext,
          owned_agents: [...this.latestIdentityContext.owned_agents],
          team_agents_preview: [
            ...this.latestIdentityContext.team_agents_preview,
          ],
        };

        const viewerId = this.latestIdentityContext.viewer.id;
        const agentEntry: IdentityAgentPreview = {
          id: payload.agent.id,
          name: payload.agent.name,
          owner_user_id: payload.agent.owner_user_id,
          visibility: payload.agent.visibility,
          status: payload.agent.agent_status ?? payload.status ?? null,
          mode: payload.mode ?? null,
        };

        const isOwned = payload.agent.owner_user_id === viewerId;

        if (isOwned) {
          updatedContext.owned_agents = updatedContext.owned_agents.filter(
            (a) => a.id !== agentEntry.id,
          );
          if (payload.event !== "offline") {
            updatedContext.owned_agents.unshift(agentEntry);
          }
        } else {
          updatedContext.team_agents_preview =
            updatedContext.team_agents_preview.filter(
              (a) => a.id !== agentEntry.id,
            );
          if (payload.event !== "offline") {
            updatedContext.team_agents_preview.unshift(agentEntry);
            updatedContext.team_agents_preview =
              updatedContext.team_agents_preview.slice(0, 10);
            if (updatedContext.team_agents_total < 10) {
              updatedContext.team_agents_total = Math.max(
                updatedContext.team_agents_total,
                updatedContext.team_agents_preview.length,
              );
            }
          } else {
            if (updatedContext.team_agents_total > 0) {
              updatedContext.team_agents_total = Math.max(
                0,
                updatedContext.team_agents_total - 1,
              );
            }
          }
        }

        this.latestIdentityContext = updatedContext;
        this.identityCallbacks.forEach((callback) => {
          try {
            callback(updatedContext);
          } catch (error) {
            console.error("Error in identity callback:", error);
          }
        });
      }

      this.agentPresenceCallbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error("Error in agent presence callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse agent presence event:", error);
    }
  }

  /**
   * Handle reaction added events
   */
  private handleReactionEvent(event: MessageEvent): void {
    try {
      const payload = JSON.parse(
        (event as MessageEvent).data as any,
      ) as ReactionEvent;
      console.log("👍 Reaction event:", payload);
      this.lastEventTime = Date.now();

      this.reactionCallbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error("Error in reaction callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse reaction event:", error);
    }
  }

  /**
   * Subscribe to reaction events
   */
  subscribeToReactions(callback: (event: ReactionEvent) => void): () => void {
    this.reactionCallbacks.add(callback);
    return () => {
      this.reactionCallbacks.delete(callback);
    };
  }

  /**
   * Handle message_deleted SSE event — remove or mark message in cache
   */
  private handleMessageDeletedEvent(event: MessageEvent): void {
    try {
      const data = JSON.parse((event as MessageEvent).data as any) as {
        id: string;
        deleted_by: string;
        permanent: boolean;
      };
      console.log("🗑️ Message deleted event:", data);
      this.lastEventTime = Date.now();

      if (data.permanent) {
        // Permanently remove from all posts caches
        const activeQueries = this.config.queryClient
          .getQueryCache()
          .findAll({ queryKey: ["posts"] });
        for (const query of activeQueries) {
          this.config.queryClient.setQueryData(
            query.queryKey,
            (old: any[] | undefined) => {
              if (!old) return old;
              return old.filter((p: any) => p.id !== data.id);
            },
          );
        }
      } else {
        // Soft delete — invalidate to refetch (server returns updated record)
        this.config.queryClient.invalidateQueries({ queryKey: ["posts"] });
      }
    } catch (error) {
      console.error("Failed to parse message_deleted event:", error);
    }
  }

  /**
   * Handle message_edited SSE event — update message content in cache
   */
  /**
   * Handle message_updated events (e.g. ai_summary populated after async pipeline)
   */
  private handleMessageUpdatedEvent(event: MessageEvent): void {
    try {
      const data = JSON.parse((event as MessageEvent).data as any) as {
        id: string;
        ai_summary?: string;
        [key: string]: any;
      };
      console.log("📝 Message updated event:", data);
      this.lastEventTime = Date.now();

      // Update the message in all posts caches
      const activeQueries = this.config.queryClient
        .getQueryCache()
        .findAll({ queryKey: ["posts"] });
      for (const query of activeQueries) {
        this.config.queryClient.setQueryData(
          query.queryKey,
          (old: any[] | undefined) => {
            if (!old) return old;
            return old.map((p: any) => {
              if (String(p.id) !== String(data.id)) return p;
              return {
                ...p,
                ...(data.ai_summary !== undefined && {
                  ai_summary: data.ai_summary,
                }),
                ...(data.content !== undefined && { content: data.content }),
                ...(data.updated_at !== undefined && {
                  updated_at: data.updated_at,
                }),
              };
            });
          },
        );
      }

      // If the update includes widget data, store tool call context and invalidate transcript
      const widgetData = data.widget || data.metadata?.ui?.widget;
      if (data.field === "ui.widget" || widgetData) {
        if (import.meta.env.DEV) {
          console.debug(
            "🧩 Widget metadata received via message_updated:",
            data.id,
            widgetData?.tool_name,
            widgetData?.tool_input,
          );
        }
        // Store tool call context from the widget metadata so the enrichment
        // hook can populate tool_input for context-specific rendering.
        if (widgetData?.tool_name && widgetData?.tool_input) {
          storeToolCallContext(data.id, {
            tool_name: widgetData.tool_name,
            tool_action: widgetData.tool_action || widgetData.action || null,
            kind: widgetData.kind || null,
            arguments: widgetData.tool_input,
            resource_uri: widgetData.resource_uri || null,
          });
        }
        this.config.queryClient.invalidateQueries({
          queryKey: ["space-agent-transcript"],
        });
      }

      // Fire message updated callbacks
      const updatedEvent: MessageUpdatedEvent = {
        id: data.id,
        ...(data.ai_summary !== undefined && { ai_summary: data.ai_summary }),
        ...(data.summarized_at !== undefined && {
          summarized_at: data.summarized_at,
        }),
      };
      this.messageUpdatedCallbacks.forEach((callback) => {
        try {
          callback(updatedEvent);
        } catch (err) {
          console.error("Error in message updated callback:", err);
        }
      });
    } catch (error) {
      console.error("Failed to parse message_updated event:", error);
    }
  }

  private handleMessageEditedEvent(event: MessageEvent): void {
    try {
      const data = JSON.parse((event as MessageEvent).data as any) as {
        id: string;
        content: string;
        edited_by: string;
        edited_at: string;
      };
      console.log("✏️ Message edited event:", data);
      this.lastEventTime = Date.now();

      // Update the message in all posts caches
      const activeQueries = this.config.queryClient
        .getQueryCache()
        .findAll({ queryKey: ["posts"] });
      for (const query of activeQueries) {
        this.config.queryClient.setQueryData(
          query.queryKey,
          (old: any[] | undefined) => {
            if (!old) return old;
            return old.map((p: any) => {
              if (p.id !== data.id) return p;
              return {
                ...p,
                content: data.content,
                edited_at: data.edited_at,
                is_edited: true,
              };
            });
          },
        );
      }
    } catch (error) {
      console.error("Failed to parse message_edited event:", error);
    }
  }

  /**
   * Handle agent activity events (tool calls, processing status)
   */
  private handleAgentActivityEvent(event: MessageEvent): void {
    try {
      const payload = JSON.parse(
        (event as MessageEvent).data as any,
      ) as AgentActivityEvent;
      console.log("🔧 Agent activity event:", payload);
      this.lastEventTime = Date.now();

      // Dispatch custom event for SSE debug panel
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent("ax:agent-activity", { detail: payload }),
        );
      }

      this.agentActivityCallbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error("Error in agent activity callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse agent activity event:", error);
    }
  }

  /**
   * Subscribe to agent activity events (tool calls)
   */
  subscribeToAgentActivity(
    callback: (event: AgentActivityEvent) => void,
  ): () => void {
    this.agentActivityCallbacks.add(callback);
    return () => {
      this.agentActivityCallbacks.delete(callback);
    };
  }

  /**
   * Handle cloud agent processing events (started processing a message)
   */
  private handleAgentProcessingEvent(event: MessageEvent): void {
    try {
      const payload = JSON.parse(
        (event as MessageEvent).data as any,
      ) as AgentProcessingEvent;
      console.log("⚙️ Agent processing event:", payload);
      this.lastEventTime = Date.now();

      // Dispatch custom event for UI components
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent("ax:agent-processing", { detail: payload }),
        );
      }

      this.agentProcessingCallbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error("Error in agent processing callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse agent processing event:", error);
    }
  }

  /**
   * Subscribe to agent processing events
   */
  subscribeToAgentProcessing(
    callback: (event: AgentProcessingEvent) => void,
  ): () => void {
    this.agentProcessingCallbacks.add(callback);
    return () => {
      this.agentProcessingCallbacks.delete(callback);
    };
  }

  /**
   * Handle cloud agent error events (failed to process a message)
   */
  private handleAgentErrorEvent(event: MessageEvent): void {
    try {
      const payload = JSON.parse(
        (event as MessageEvent).data as any,
      ) as AgentErrorEvent;
      console.log("❌ Agent error event:", payload);
      this.lastEventTime = Date.now();

      // Dispatch custom event for UI components
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent("ax:agent-error", { detail: payload }),
        );
      }

      this.agentErrorCallbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error("Error in agent error callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse agent error event:", error);
    }
  }

  /**
   * Subscribe to agent error events
   */
  subscribeToAgentError(
    callback: (event: AgentErrorEvent) => void,
  ): () => void {
    this.agentErrorCallbacks.add(callback);
    return () => {
      this.agentErrorCallbacks.delete(callback);
    };
  }

  /**
   * Handle cloud agent skipped events (paused, rate limited, inactive)
   */
  private handleAgentSkippedEvent(event: MessageEvent): void {
    try {
      const payload = JSON.parse(
        (event as MessageEvent).data as any,
      ) as AgentSkippedEvent;
      console.log("⏭️ Agent skipped event:", payload);
      this.lastEventTime = Date.now();

      // Dispatch custom event for UI components
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent("ax:agent-skipped", { detail: payload }),
        );
      }

      this.agentSkippedCallbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error("Error in agent skipped callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse agent skipped event:", error);
    }
  }

  /**
   * Subscribe to agent skipped events
   */
  subscribeToAgentSkipped(
    callback: (event: AgentSkippedEvent) => void,
  ): () => void {
    this.agentSkippedCallbacks.add(callback);
    return () => {
      this.agentSkippedCallbacks.delete(callback);
    };
  }

  /**
   * Handle message_stream SSE events (streaming contract v1)
   */
  private handleMessageStreamEvent(event: MessageEvent): void {
    try {
      const payload = JSON.parse(
        (event as MessageEvent).data as any,
      ) as MessageStreamChunk;
      this.lastEventTime = Date.now();

      this.messageStreamCallbacks.forEach((callback) => {
        try {
          callback(payload);
        } catch (error) {
          console.error("Error in message stream callback:", error);
        }
      });
    } catch (error) {
      console.error("Failed to parse message_stream event:", error);
    }
  }

  /**
   * Subscribe to message updated events (summary arrival)
   */
  subscribeToMessageUpdated(
    callback: (event: MessageUpdatedEvent) => void,
  ): () => void {
    this.messageUpdatedCallbacks.add(callback);
    return () => {
      this.messageUpdatedCallbacks.delete(callback);
    };
  }

  /**
   * Subscribe to message stream events (SSE streaming contract v1)
   */
  subscribeToMessageStream(
    callback: (event: MessageStreamChunk) => void,
  ): () => void {
    this.messageStreamCallbacks.add(callback);
    return () => {
      this.messageStreamCallbacks.delete(callback);
    };
  }

  /**
   * Start silent refetch loop while SSE is down
   */
  private startSilentRefetch(): void {
    if (this.silentRefetchTimer) return;

    // Run a 60s refetch loop while SSE is reconnecting
    const silentRefetch = () => {
      if (this.state === "connected" || this.reconnectSuspended) {
        this.stopSilentRefetch();
        return;
      }

      console.log("🔇 Silent refetch while SSE reconnecting...");
      this.config.queryClient.refetchQueries({
        queryKey: ["posts"],
        type: "active",
        exact: false,
      });
      this.silentRefetchTimer = setTimeout(silentRefetch, 60000);
    };
    // Start after 5 seconds
    this.silentRefetchTimer = setTimeout(silentRefetch, 5000);
  }

  private stopSilentRefetch(): void {
    if (this.silentRefetchTimer) {
      clearTimeout(this.silentRefetchTimer);
      this.silentRefetchTimer = null;
    }
  }

  /**
   * Schedule a reconnection attempt with backoff
   */
  private scheduleReconnect(): void {
    if (this.reconnectSuspended) return;
    if (this.reconnectTimer) {
      console.debug("SSE reconnect already scheduled");
      return;
    }

    const delay = this.calculateBackoffDelay();
    console.debug(
      `Scheduling SSE reconnect in ${delay}ms (attempt ${this.reconnectAttempts + 1})`,
    );

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectAttempts++;
      void this.connectSSE();
    }, delay);
  }

  /**
   * Calculate exponential backoff with jitter
   */
  private calculateBackoffDelay(): number {
    // Base delay: 250ms * 2^attempts, capped at 30s
    const baseDelay = Math.min(
      this.backoffBase * Math.pow(2, this.reconnectAttempts),
      this.backoffMax,
    );

    // Add jitter: ±25% randomization
    const jitter = baseDelay * this.backoffJitter * (Math.random() * 2 - 1);
    const delayWithJitter = Math.max(this.backoffBase, baseDelay + jitter);

    return Math.round(delayWithJitter);
  }

  /**
   * Handle connection errors with exponential backoff and jitter
   */
  private handleConnectionError(): void {
    this.state = "error";
    this.notifyState();
    this.isConnecting = false; // Clear single-flight guard
    this.cleanup();

    // Calculate delay with exponential backoff and jitter
    const delay = this.calculateBackoffDelay();

    // Log reconnection attempt
    if (this.reconnectAttempts >= this.config.maxReconnectAttempts) {
      console.debug(
        `SSE reconnecting (attempt ${this.reconnectAttempts + 1}, delay: ${delay}ms, periodic mode)`,
      );
      // Start silent refetch while we keep trying
      this.startSilentRefetch();
    } else {
      console.debug(
        `SSE reconnecting (attempt ${this.reconnectAttempts + 1}, delay: ${delay}ms)`,
      );
    }

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectAttempts++;
      void this.connectSSE();
    }, delay);
  }

  /**
   * Start preemptive token refresh monitoring
   */
  private startPreemptiveTokenRefresh(): void {
    this.stopPreemptiveTokenRefresh();

    // Check token age every minute
    this.tokenRefreshTimer = setInterval(() => {
      const tokenAge = Date.now() - this.tokenIssuedAt;
      const isHostedPaxai = isHostedPaxaiHostname(window.location.hostname);
      const tokenLifetime = isHostedPaxai ? 60 * 60 * 1000 : 15 * 60 * 1000; // 60min hosted, 15min dev
      const refreshThreshold = tokenLifetime * 0.8; // Refresh at 80% of lifetime

      if (tokenAge > refreshThreshold) {
        console.debug(
          "Token approaching expiry (80% of TTL), refreshing preemptively...",
        );
        this.handlePreemptiveTokenRefresh();
      }
    }, 60000); // Check every minute
  }

  /**
   * Stop preemptive token refresh monitoring
   */
  private stopPreemptiveTokenRefresh(): void {
    if (this.tokenRefreshTimer) {
      clearInterval(this.tokenRefreshTimer);
      this.tokenRefreshTimer = null;
    }
  }

  /**
   * Handle preemptive token refresh
   */
  private async handlePreemptiveTokenRefresh(): Promise<void> {
    const refreshed = await storage.refreshTokens();

    if (refreshed) {
      console.debug(
        "Token refreshed preemptively, reconnecting SSE with new token...",
      );
      this.tokenIssuedAt = Date.now();

      // Gracefully reconnect with new token
      if (this.sseConnection) {
        this.sseConnection.close();
        this.sseConnection = null;
      }

      // Quick reconnect with fresh token
      this.reconnectAttempts = 0;
      this.scheduleReconnectNow();
    } else {
      console.debug(
        "Preemptive token refresh failed, will retry on next check",
      );
    }
  }

  /**
   * Start heartbeat to detect stale connections and enforce max connection age
   */
  private startHeartbeat(): void {
    this.stopHeartbeat();

    this.heartbeatTimer = setInterval(() => {
      const now = Date.now();
      const timeSinceLastEvent = now - this.lastEventTime;
      const connectionAge = now - this.connectionStartTime;

      // Stale connection - no events received recently
      if (timeSinceLastEvent > this.config.heartbeatInterval * 2) {
        console.warn("⚠️ SSE connection appears stale, reconnecting...");
        this.handleConnectionError();
        return;
      }

      // Proactively refresh old connections to prevent degradation
      if (connectionAge > this.maxConnectionAge) {
        console.log(
          `🔄 SSE connection age (${Math.round(connectionAge / 60000)}min) exceeds max, refreshing...`,
        );
        this.forceReconnect();
      }
    }, this.config.heartbeatInterval);
  }

  /**
   * Stop heartbeat timer
   */
  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  /**
   * Enable fallback polling when SSE fails
   */
  private enableFallbackPolling(): void {
    console.log("📊 Enabling fallback polling mode");

    // Set longer polling intervals as fallback
    this.config.queryClient.setDefaultOptions({
      queries: {
        refetchInterval: 60000, // 60 seconds
        refetchIntervalInBackground: false,
      },
    });
  }

  /**
   * Show browser notification
   */
  private showNotification(title: string, body: string): void {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(title, { body, icon: "/favicon.ico" });
    }
  }

  /**
   * Clean up connections and timers
   */
  private cleanup(): void {
    if (this.sseConnection) {
      // Explicitly remove all event listeners before closing
      if (this.sseMessageListener) {
        this.sseConnection.removeEventListener(
          "message",
          this.sseMessageListener,
        );
      }
      if (this.sseMentionListener) {
        this.sseConnection.removeEventListener(
          "mention",
          this.sseMentionListener,
        );
      }
      if (this.sseTaskUpdateListener) {
        this.sseConnection.removeEventListener(
          "task_update",
          this.sseTaskUpdateListener,
        );
      }
      if (this.sseIdentityBootstrapListener) {
        this.sseConnection.removeEventListener(
          "identity_bootstrap",
          this.sseIdentityBootstrapListener,
        );
      }
      if (this.sseAgentPresenceListener) {
        this.sseConnection.removeEventListener(
          "agent_presence",
          this.sseAgentPresenceListener,
        );
      }
      if (this.sseBootstrapListener) {
        this.sseConnection.removeEventListener(
          "bootstrap",
          this.sseBootstrapListener,
        );
      }
      if (this.ssePingListener) {
        this.sseConnection.removeEventListener("ping", this.ssePingListener);
      }
      if (this.sseMessageDeletedListener) {
        this.sseConnection.removeEventListener(
          "message_deleted",
          this.sseMessageDeletedListener,
        );
      }
      if (this.sseMessageEditedListener) {
        this.sseConnection.removeEventListener(
          "message_edited",
          this.sseMessageEditedListener,
        );
      }
      if (this.sseMessageUpdatedListener) {
        this.sseConnection.removeEventListener(
          "message_updated",
          this.sseMessageUpdatedListener,
        );
      }
      if (this.sseAgentActivityListener) {
        this.sseConnection.removeEventListener(
          "agent_activity",
          this.sseAgentActivityListener,
        );
      }
      if (this.sseAgentProcessingListener) {
        this.sseConnection.removeEventListener(
          "agent_processing",
          this.sseAgentProcessingListener,
        );
      }
      if (this.sseAgentErrorListener) {
        this.sseConnection.removeEventListener(
          "agent_error",
          this.sseAgentErrorListener,
        );
      }
      if (this.sseAgentSkippedListener) {
        this.sseConnection.removeEventListener(
          "agent_skipped",
          this.sseAgentSkippedListener,
        );
      }
      if (this.sseMessageStreamListener) {
        this.sseConnection.removeEventListener(
          "message_stream",
          this.sseMessageStreamListener,
        );
      }

      // Clear listener references
      this.sseMessageListener = null;
      this.sseMentionListener = null;
      this.sseTaskUpdateListener = null;
      this.sseIdentityBootstrapListener = null;
      this.sseAgentPresenceListener = null;
      this.sseBootstrapListener = null;
      this.ssePingListener = null;
      this.sseMessageDeletedListener = null;
      this.sseMessageEditedListener = null;
      this.sseMessageUpdatedListener = null;
      this.sseAgentActivityListener = null;
      this.sseAgentProcessingListener = null;
      this.sseAgentErrorListener = null;
      this.sseAgentSkippedListener = null;
      this.sseMessageStreamListener = null;

      this.sseConnection.close();
      this.sseConnection = null;
    }

    if (this.wsConnection) {
      this.wsConnection.close();
      this.wsConnection = null;
    }

    this.stopHeartbeat();
    this.stopPreemptiveTokenRefresh();
    this.stopSilentRefetch();

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.agentRefetchDebounceTimer) {
      clearTimeout(this.agentRefetchDebounceTimer);
      this.agentRefetchDebounceTimer = null;
    }

    // NOTE: Do not remove the global token refresh listener here.
    // It must persist across transient errors so that future refreshes
    // can trigger a graceful SSE reconnect. We remove it only on
    // explicit disconnect() to avoid leaks.
  }

  /**
   * Disconnect all connections
   */
  disconnect(): void {
    console.log("🔌 Disconnecting realtime connections");
    this.state = "disconnected";
    this.notifyState();
    this.reconnectAttempts = 0;

    // On full disconnect, remove event listeners to avoid leaks
    if (this.tokenRefreshHandler) {
      window.removeEventListener(
        "auth:token-refreshed",
        this.tokenRefreshHandler as EventListener,
      );
    }
    if (this.visibilityChangeHandler) {
      window.removeEventListener(
        "visibilitychange",
        this.visibilityChangeHandler,
      );
    }

    this.cleanup();
  }

  /**
   * Get current connection state
   */
  getState(): ConnectionState {
    return this.state;
  }

  /**
   * Connect WebSocket for bidirectional communication (Phase 2)
   */
  async connectWebSocket(): Promise<void> {
    // TODO: Implement WebSocket connection
    console.log("WebSocket support coming in Phase 2");
  }

  /**
   * Register a callback for task events (for components not using React Query)
   */
  onTaskEvent(callback: TaskEventCallback): () => void {
    this.taskEventCallbacks.add(callback);

    // Return unsubscribe function
    return () => {
      this.taskEventCallbacks.delete(callback);
    };
  }

  /**
   * Unregister a task event callback
   */
  offTaskEvent(callback: TaskEventCallback): void {
    this.taskEventCallbacks.delete(callback);
  }

  /** Return the latest identity context if available */
  getIdentityContext(): IdentityContext | null {
    return this.latestIdentityContext;
  }

  /** Register a callback for identity updates */
  onIdentityUpdate(callback: IdentityCallback): () => void {
    this.identityCallbacks.add(callback);
    if (this.latestIdentityContext) {
      try {
        callback(this.latestIdentityContext);
      } catch (error) {
        console.error("Error in identity callback:", error);
      }
    }
    return () => {
      this.identityCallbacks.delete(callback);
    };
  }

  offIdentityUpdate(callback: IdentityCallback): void {
    this.identityCallbacks.delete(callback);
  }

  /** Register for agent presence events */
  onAgentPresence(callback: AgentPresenceCallback): () => void {
    this.agentPresenceCallbacks.add(callback);
    return () => {
      this.agentPresenceCallbacks.delete(callback);
    };
  }

  offAgentPresence(callback: AgentPresenceCallback): void {
    this.agentPresenceCallbacks.delete(callback);
  }
}

// Singleton instance
let realtimeManager: RealtimeManager | null = null;

export function getRealtimeManager(queryClient: QueryClient): RealtimeManager {
  if (!realtimeManager) {
    realtimeManager = new RealtimeManager({ queryClient });
  }
  return realtimeManager;
}
