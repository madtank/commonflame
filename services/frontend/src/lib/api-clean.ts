import axios from "axios";
import { config } from "../config/environment";
import { storage } from "./storage";
import { FOLLOW_UUID } from "./agent-mobility";
import { FLEET_CONTROL_ROUTES, SPACE_ROUTES, TASK_ROUTES } from "./api-routes";

export type OwnerRef = {
  id?: string;
  name?: string;
  handle?: string;
  avatar?: string | null;
};

export async function fetchAgentSummary(
  agentId: string,
  spaceId: string,
): Promise<AgentSummary> {
  if (!agentId) throw new Error("agentId required");
  if (!spaceId) throw new Error("spaceId required");
  const response = await apiClient.get(`/api/agents/${agentId}/summary`, {
    params: { space_id: spaceId },
  });
  return response.data as AgentSummary;
}

export async function fetchUserSummary(
  userId: string,
  spaceId: string,
): Promise<UserSummary> {
  if (!userId) throw new Error("userId required");
  if (!spaceId) throw new Error("spaceId required");
  const response = await apiClient.get(`/api/users/${userId}/summary`, {
    params: { space_id: spaceId },
  });
  return response.data as UserSummary;
}

/**
 * Update user feature flags (e.g., v2_enabled for Agent v2 engine)
 */
export async function updateUserFeatureFlags(flags: {
  v2_enabled?: boolean;
  can_access_beta_features?: boolean;
}): Promise<void> {
  await apiClient.patch("/auth/me/feature-flags", { feature_flags: flags });
}

/**
 * Update agent engine version (v1 or v2)
 */
export async function updateAgentEngineVersion(
  agentId: string,
  engineVersion: "v1" | "v2",
): Promise<void> {
  await apiClient.put(`/auth/agents/${agentId}`, {
    engine_version: engineVersion,
  });
}

/**
 * Model tier selection - quality/cost tradeoff for cloud agents
 */
export type ModelTier = "standard" | "lite" | "premium";

export interface ModelTierInfo {
  id: ModelTier;
  name: string;
  description: string;
  warning?: string;
  badge?: string;
}

export const MODEL_TIERS: ModelTierInfo[] = [
  {
    id: "lite",
    name: "Lite",
    description: "Faster and cheaper responses",
  },
  {
    id: "standard",
    name: "Standard",
    description: "Reliable performance with good quotas",
    badge: "Default",
  },
  {
    id: "premium",
    name: "Premium",
    description: "Highest quality responses",
    badge: "Plus",
  },
];

/**
 * Get the model for a tier from available models
 * Uses pattern matching to find the right model from the API response
 */
export function getModelForTier(
  tier: ModelTier,
  availableModels: ModelOption[],
): ModelOption | null {
  if (availableModels.length === 0) return null;

  switch (tier) {
    case "premium":
      // Premium: Gemini 3.0 Flash Preview (contains "3.0" or "preview")
      return (
        availableModels.find(
          (m) => m.id.includes("3.0") || m.id.includes("preview"),
        ) || null
      );
    case "lite":
      // Lite: Flash Lite (contains "lite")
      return availableModels.find((m) => m.id.includes("lite")) || null;
    case "standard":
    default:
      // Standard: Regular Flash (contains "flash" but not "lite" or "3.0" or "preview")
      return (
        availableModels.find(
          (m) =>
            m.id.includes("flash") &&
            !m.id.includes("lite") &&
            !m.id.includes("3.0") &&
            !m.id.includes("preview"),
        ) || availableModels[0]
      );
  }
}

/**
 * Model option returned from backend
 */
export interface ModelOption {
  id: string;
  name: string;
  description: string;
  tier_required: "free" | "plus"; // Org subscription tier needed
  is_default: boolean;
  available: boolean;
}

/**
 * Response from GET /auth/agents/models
 */
export interface AvailableModelsResponse {
  models: ModelOption[];
  default_model: string;
  org_tier: string;
}

/**
 * Fetch available LLM models for cloud agents
 * Backend returns only models available for the current org's tier
 */
export async function getAvailableModels(): Promise<AvailableModelsResponse> {
  const response = await apiClient.get("/auth/agents/models");
  return response.data;
}

/**
 * Agent template from backend API
 */
export interface ApiAgentTemplate {
  key: string;
  name: string;
  parent_key: string | null;
  badge: string | null;
  icon_url: string | null;
  short_description: string | null;
  full_description: string | null;
  default_bio: string | null;
  available_models: string[] | null;
  default_tools: Record<string, boolean> | null;
  system_prompt_additions: string | null;
  is_top_level: boolean;
  display_order: number | null;
  is_active: boolean;
}

/**
 * Tool metadata from mcp_servers registry
 */
export interface AvailableTool {
  id: string;
  name: string;
  description: string | null;
  category: string | null;
  icon_url: string | null;
  min_user_tier: "free" | "plus";
  required_runner_tag: string | null;
}

export interface AgentKeyInfo {
  key_id: string;
  client_id: string;
  label?: string | null;
  scopes?: string | null;
  is_active: boolean;
  last_used_at?: string | null;
  created_at: string;
  expires_at?: string | null;
}

export interface AgentKeyCreateRequest {
  label?: string;
  scopes?: string;
}

export interface AgentKeyCreateResponse {
  key_id: string;
  client_id: string;
  client_secret: string;
  label?: string | null;
  scopes: string;
  created_at: string;
}

export type PersonalAccessKeyAgentScope = "all" | "user" | "agents" | "unbound";

export interface PersonalAccessKey {
  credential_id: string;
  name: string;
  scopes?: string[] | string | null;
  agent_scope?: PersonalAccessKeyAgentScope | null;
  allowed_agent_ids?: string[] | null;
  last_used_at?: string | null;
  created_at: string;
  revoked_at?: string | null;
}

export interface PersonalAccessKeyCreateRequest {
  name: string;
  agent_scope?: PersonalAccessKeyAgentScope;
  allowed_agent_ids?: string[];
  bound_agent_id?: string; // AUTH-SPEC-001: bind token to a single agent
  audience?: "cli" | "mcp" | "both"; // AUTH-SPEC-001 Phase 3: target audience
}

export interface PersonalAccessKeyCreateResponse extends PersonalAccessKey {
  token?: string | null;
  key?: string | null;
  raw_token?: string | null;
}

export interface PatScopeAgent {
  id: string;
  name?: string | null;
  agent_name?: string | null;
  status?: string | null;
  agent_type?: string | null;
}
/**
 * Response from GET /api/agent-templates
 */
export interface AgentTemplatesResponse {
  templates: ApiAgentTemplate[];
  available_tools: AvailableTool[];
  top_level_count: number;
  total_count: number;
}

/**
 * Fetch available agent templates
 * Returns all active, non-admin-only templates
 */
export async function getAgentTemplates(): Promise<AgentTemplatesResponse> {
  const response = await apiClient.get("/api/agent-templates");
  return response.data;
}

export async function listPersonalAccessKeys(): Promise<PersonalAccessKey[]> {
  const response = await apiClient.get("/api/v1/keys");
  const data = response.data;
  if (Array.isArray(data)) return data as PersonalAccessKey[];
  return (data?.keys || []) as PersonalAccessKey[];
}

export async function createPersonalAccessKey(
  payload: PersonalAccessKeyCreateRequest,
): Promise<PersonalAccessKeyCreateResponse> {
  const response = await apiClient.post("/api/v1/keys", payload);
  return response.data as PersonalAccessKeyCreateResponse;
}

export async function revokePersonalAccessKey(
  credentialId: string,
): Promise<void> {
  await apiClient.delete(`/api/v1/keys/${credentialId}`);
}

export async function rotatePersonalAccessKey(
  credentialId: string,
): Promise<PersonalAccessKeyCreateResponse> {
  const response = await apiClient.post(`/api/v1/keys/${credentialId}/rotate`);
  return response.data as PersonalAccessKeyCreateResponse;
}

export async function listPatScopeAgents(options?: {
  search?: string;
  spaceId?: string | null;
}): Promise<PatScopeAgent[]> {
  const params: Record<string, string | number> = { limit: 200 };
  const search = options?.search?.trim();
  if (search) params.search = search;
  if (options?.spaceId) params.space_id = options.spaceId;
  const response = await apiClient.get("/api/v1/agents", { params });
  const data = response.data;
  const rawAgents = (
    Array.isArray(data) ? data : data?.agents || []
  ) as PatScopeAgent[];
  return rawAgents.map((agent) => ({
    ...agent,
    name: agent.name || agent.agent_name || "Unnamed agent",
  }));
}
export type AgentSummary = {
  id: string;
  name: string;
  avatar?: string | null;
  owner?: OwnerRef | null;
  last_active_at?: string | null;
  status?: string | null;
  visibility?: string | null;
  agent_type?: string | null;
  origin?: string | null;
  runtime_kind?: string | null;
  runtime_label?: string | null;
  runtime?: string | null;
  runner_type?: string | null;
  execution_method?: string | null;
  control?: {
    is_disabled?: boolean;
    disabled_reason?: string | null;
    disabled_by?: string[];
    disabled_until?: string | null;
    no_reply?: boolean;
    no_reply_reason?: string | null;
    no_reply_by?: string[];
    no_reply_until?: string | null;
    routing_only?: boolean;
    routing_only_reason?: string | null;
    routing_only_by?: string[];
    routing_only_until?: string | null;
  } | null;
};

type RawAgentSummary = AgentSummary & {
  agent_name?: string | null;
  avatar_url?: string | null;
  last_seen?: string | null;
  visibility_level?: string | null;
  owner_id?: string | null;
  owner_username?: string | null;
  owner_full_name?: string | null;
};

export function normalizeAgentSummary(agent: RawAgentSummary): AgentSummary {
  const owner =
    agent.owner ||
    (agent.owner_id
      ? {
          id: agent.owner_id,
          name: agent.owner_full_name || agent.owner_username || undefined,
          handle: agent.owner_username || undefined,
          avatar: null,
        }
      : undefined);

  return {
    ...agent,
    name: agent.name || agent.agent_name || "Unnamed agent",
    avatar: agent.avatar || agent.avatar_url || null,
    owner,
    last_active_at: agent.last_active_at || agent.last_seen || null,
    visibility: agent.visibility || agent.visibility_level || null,
    status: agent.status || null,
  };
}

export type UserSummary = {
  id: string;
  name: string;
  handle?: string | null;
  avatar?: string | null;
  last_active_at?: string | null;
};

export interface RosterActivityStats {
  messages_24h: number;
  messages_7d: number;
  tasks_open: number;
  tasks_24h: number;
}

export interface RosterPresence {
  last_active: string | null;
  status: string;
  minutes_since?: number | null;
}

export interface RosterTrust {
  score: number;
  tier: string;
  // Extended trust fields from AI intelligence analysis (PR #12)
  trust_score?: number | null; // 0-1, computed from quality/spam/toxicity
  avg_quality_score?: number | null;
  avg_spam_score?: number | null;
  avg_toxicity_score?: number | null;
  messages_analyzed?: number;
}

export interface RosterOwner {
  id: string;
  display_name: string;
  handle: string;
}

export interface RosterEntry {
  id: string;
  type: "human" | "agent";
  display_name: string;
  handle: string;
  workspace_id: string;
  owner_user?: RosterOwner | null;
  presence: RosterPresence;
  trust: RosterTrust;
  activity_stats: RosterActivityStats;
  skills: string[];
  tags: string[];
  avatar_url?: string | null;
  cloud_function_url?: string | null;
  metadata?: Record<string, any> | null;
  // Tool capabilities (JSONB - preferred)
  enabled_tools?: Record<string, boolean>;
  // Legacy individual fields (backwards compat)
  web_browsing_enabled?: boolean;
  ax_mcp_enabled?: boolean;
  image_gen_enabled?: boolean;
  web_fetch_enabled?: boolean;
  // Engine version
  engine_version?: "v1" | "v2";
}

export interface RosterResponse {
  items: RosterEntry[];
  total: number;
  limit: number;
  offset: number;
}

// Admin activity report (GET /api/admin/activity-report). Field names are
// snake_case exactly as delivered by the backend. `last_login_at` is null for
// users that have never logged in; the user lists are capped at 200 rows
// server-side with `*_total` carrying the uncapped counts.
export interface ActivityReportSignup {
  username: string | null;
  email: string | null;
  auth_provider: string | null;
  created_at: string | null;
}

export interface ActivityReportUser {
  username: string | null;
  email: string | null;
  last_login_at: string | null;
}

export interface ActivityReport {
  period_days: number;
  new_signups: ActivityReportSignup[];
  login_events: number;
  active_users: ActivityReportUser[];
  active_users_total: number;
  dormant_users: ActivityReportUser[];
  dormant_users_total: number;
  // Fixed dormancy window (settings.dormant_alert_days) used for the cutoff;
  // independent of the requested `days`. May be absent from older backend
  // deployments, so consumers should guard before rendering it.
  dormant_days: number;
  pending_access_requests: number;
}

// Connect to FastAPI backend using environment configuration
const API_URL = config.apiUrl;

// In-memory coordination for 429 pauses (simple per-tab)
let global429Until = 0; // epoch ms to pause outbound requests

const normalizeWaitingFlag = (value: any): boolean =>
  value === true || value === 1 || value === "true";

// Create axios instance with default config
export const apiClient = axios.create({
  baseURL: API_URL,
  headers: {
    "Content-Type": "application/json",
  },
  transitional: {
    silentJSONParsing: false,
  },
  // Ensure auth cookies (ax_session, refresh_token) are included on API calls so
  // logout can actually clear them when running same-origin or compatibility-host flows
  withCredentials: true,
});

// Logout is shared across tabs. Access tokens remain tab scoped; each tab
// refreshes its own access token under the browser's refresh-cookie lock.
try {
  if (typeof window !== "undefined" && "BroadcastChannel" in window) {
    const channel = new BroadcastChannel(`waystation-auth-${config.environment}`);
    channel.onmessage = (event) => {
      if (event.data?.type === "logout") {
        storage.clearTokens();
        window.dispatchEvent(new CustomEvent("auth:logout", { detail: { reason: "broadcast_logout" } }));
      }
    };
  }
} catch { /* Browser session still works without cross-tab events. */ }

// Singleflight refresh gate (per-tab)
let refreshInFlight: Promise<boolean> | null = null;
let isRefreshing = false;
let failedQueue: any[] = [];

const processQueue = (error: any, token: string | null = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token);
    }
  });
  failedQueue = [];
};

const singleflightRefresh = async (): Promise<boolean> => {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    if (config.features?.verboseLogging)
      console.log("🔐 singleflight: starting token refresh");
    // Use storage.refreshTokens which now has its own singleflight coordination
    // This ensures we don't duplicate refresh attempts across components
    let ok = await storage.refreshTokens();
    if (!ok) {
      // Retry once with jitter to smooth races
      // Use a shorter jitter since storage now coordinates internally
      try {
        const jitter = Math.random() * 500 + 250;
        await new Promise((res) => setTimeout(res, jitter));
        ok = await storage.refreshTokens();
      } catch (error) {
        console.error("Retry refresh failed:", error);
        ok = false;
      }
    }
    return ok;
  })();
  try {
    // 30s guard timeout
    const result = await Promise.race([
      refreshInFlight,
      new Promise<boolean>((res) => setTimeout(() => res(false), 30000)),
    ]);
    return result as boolean;
  } finally {
    // Keep the guard for slightly longer to allow coordinated requests to join
    setTimeout(() => {
      refreshInFlight = null;
    }, 250);
  }
};

// Log API connection in dev/test
if (config.features?.verboseLogging) {
  console.log(
    `🔌 API Client connected to ${config.environment} backend at ${API_URL}`,
  );
}

// Add token to requests if available (temporary: bearer support for legacy paths)
// TODO(security): flip to cookies-only auth and remove bearer header once backend supports it
apiClient.interceptors.request.use(async (config) => {
  const token = await storage.getUserTokenAsync();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // Attach stable device id for per-device sessions
  try {
    const deviceId = storage.getDeviceId?.();
    if (deviceId) {
      config.headers["X-Device-Id"] = deviceId;
    }
  } catch {} // eslint-disable-line no-empty
  // Respect any global 429 pause
  const now = Date.now();
  if (global429Until > now) {
    const delay = Math.min(global429Until - now, 60000);
    await new Promise((res) => setTimeout(res, delay));
  }
  return config;
});

// Add response interceptor for automatic token refresh
apiClient.interceptors.response.use(
  (response) => {
    // If response is successful, just return it
    return response;
  },
  async (error) => {
    const originalRequest = error.config || {};
    const status = error.response?.status;
    const detail = error.response?.data?.detail;

    // Central 429 handling with Retry-After support
    if (status === 429) {
      const headers = error.response?.headers || ({} as Record<string, string>);
      const body = error.response?.data || ({} as any);
      const now = Date.now();
      // Standard headers first
      let retryAfterMs = 0;
      const hRA = headers["retry-after"] || headers["Retry-After"];
      const hReset =
        headers["x-ratelimit-reset"] || headers["X-Rate-Limit-Reset"];
      if (hRA && !isNaN(Number(hRA))) retryAfterMs = Number(hRA) * 1000;
      else if (hReset && !isNaN(Number(hReset)))
        retryAfterMs = Math.max(0, Number(hReset) * 1000 - now);
      else if (typeof body?.retry_after === "number")
        retryAfterMs = Math.max(0, Math.floor(body.retry_after * 1000));

      const attempts = (originalRequest._retry429 || 0) as number;
      if (!retryAfterMs) {
        // Fallback exponential backoff with jitter
        retryAfterMs = Math.min(
          60000,
          1000 * Math.pow(2, Math.min(attempts, 5)),
        );
        retryAfterMs += Math.floor(Math.random() * 250);
      }

      if (attempts >= 2) {
        // Short global dampener, then bubble the error
        global429Until = Math.max(
          global429Until,
          now + Math.min(retryAfterMs, 10000),
        );
        // Broadcast breaker info if present so UI can notify
        try {
          const type = body?.type || body?.error;
          if (type === "agent_circuit_breaker") {
            window.dispatchEvent(
              new CustomEvent("rate-limit:circuit-breaker", {
                detail: {
                  key: body?.key,
                  retryAfter:
                    body?.retry_after || Math.round(retryAfterMs / 1000),
                },
              }),
            );
          }
        } catch {} // eslint-disable-line no-empty
        return Promise.reject(error);
      }

      global429Until = Math.max(global429Until, now + retryAfterMs);
      originalRequest._retry429 = attempts + 1;
      await new Promise((res) => setTimeout(res, retryAfterMs));
      try {
        return await apiClient(originalRequest);
      } catch (e: any) {
        try {
          const type = e?.response?.data?.type || e?.response?.data?.error;
          if (type === "agent_circuit_breaker") {
            window.dispatchEvent(
              new CustomEvent("rate-limit:circuit-breaker", {
                detail: {
                  key: e?.response?.data?.key,
                  retryAfter: e?.response?.data?.retry_after,
                },
              }),
            );
          }
        } catch {} // eslint-disable-line no-empty
        throw e;
      }
    }

    // Handle organization context changes gracefully (prevents empty messages until manual refresh)
    const isOrgCtxChange =
      status === 409 &&
      (detail?.code === "ORG_CONTEXT_CHANGED" ||
        (typeof detail === "string" &&
          detail.includes("Organization context has changed")));

    if ((status === 401 || isOrgCtxChange) && !originalRequest._retry) {
      originalRequest._retry = true;

      console.log(
        `🔄 ${status} detected${isOrgCtxChange ? " (ORG_CONTEXT_CHANGED)" : ""}, attempting automatic token refresh...`,
      );

      // Try singleflight refresh to prevent concurrent storms in this tab
      isRefreshing = true;

      try {
        if (config.features?.verboseLogging)
          console.log(
            `🔐 Auto-refreshing token due to ${status} (CtxChange: ${isOrgCtxChange})`,
          );

        // Perform the refresh
        const success = await singleflightRefresh();

        if (success) {
          if (config.features?.verboseLogging)
            console.log("✅ Token refresh successful, retrying request");
          isRefreshing = false;
          processQueue(null, "refreshed");
          return apiClient(originalRequest);
        } else {
          throw new Error("Refresh failed");
        }
      } catch (refreshError) {
        processQueue(refreshError, null);
        isRefreshing = false;

        // Only logout if we're truly unauthorized (401)
        // For 409, if refresh fails, we might just want to redirect to home or reload
        // But usually if refresh fails, session is dead anyway.
        console.warn("❌ Token refresh failed:", refreshError);
        storage.clearTokens();

        const reason = isOrgCtxChange
          ? "org_context_refresh_failed"
          : "token_refresh_failed";
        window.dispatchEvent(
          new CustomEvent("auth:logout", { detail: { reason } }),
        );

        return Promise.reject(refreshError);
      }
    }

    if (status === 403) {
      console.warn(
        "🚫 Access forbidden. Possible missing membership for target org.",
      );
    }

    // If it's not a handled case or refresh failed, reject with the original error
    return Promise.reject(error);
  },
);

type SpacesEnvelope =
  | any[]
  | {
      spaces?: any[];
      organizations?: any[];
      items?: any[];
      data?: SpacesEnvelope | null;
      result?: SpacesEnvelope | null;
      current_space?: any | null;
      current_space_id?: string | null;
      active_space_id?: string | null;
    }
  | null
  | undefined;

export function normalizeApiSpacesResponse(
  data: SpacesEnvelope,
  inheritedCurrentId?: string | null,
): any[] {
  if (!data) return [];
  if (Array.isArray(data)) {
    return inheritedCurrentId
      ? data.map((space) => ({
          ...space,
          is_current: space?.id === inheritedCurrentId,
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
    const nestedSpaces = normalizeApiSpacesResponse(nested, currentId);
    if (nestedSpaces.length > 0) return nestedSpaces;
  }

  const spaces = data.spaces || data.organizations || data.items || [];
  const normalized = spaces.map((space) =>
    currentId ? { ...space, is_current: space?.id === currentId } : space,
  );

  if (normalized.length === 0 && data.current_space?.id) {
    return [{ ...data.current_space, is_current: true }];
  }

  return normalized;
}

function normalizeUserProfileResponse(data: any): any {
  if (!data) return null;
  return (
    data.user ||
    data.profile ||
    data.account ||
    data.data?.user ||
    data.data?.profile ||
    data.data ||
    data
  );
}
export interface FleetControlState {
  emergency_stop: boolean;
  reminder_silence: boolean;
  reason?: string | null;
  actor_id?: string | null;
  actor_type?: string | null;
  updated_at?: string | null;
  transition_id?: string | null;
  audit?: Array<Record<string, unknown>>;
}

export interface FleetControlUpdate {
  emergency_stop?: boolean;
  reminder_silence?: boolean;
  reason: string;
  transition_id?: string | null;
}

export interface FleetControlEnforcementReadback {
  blocked: boolean;
  surface: "agent_communication" | "reminders";
  reason?: string | null;
  emergency_stop: boolean;
  reminder_silence: boolean;
  actor_id?: string | null;
  actor_type?: string | null;
  updated_at?: string | null;
  transition_id?: string | null;
}

export const api = {
  // 🛠️ Utility Methods
  parsePostData(data: any[], source: string = "user") {
    return data.map((item: any) => ({
      id: item.id,
      content: item.content || item.text || "",
      author: item.username || item.agent_username || item.author || "Unknown",
      uploaded_at:
        item.uploaded_at ||
        item.created_at ||
        item.timestamp ||
        new Date().toISOString(),
      timestamp:
        item.uploaded_at ||
        item.created_at ||
        item.timestamp ||
        new Date().toISOString(),
      channel: item.channel || "main",
      agent_id: item.agent_id || item.id,
      agent_type: item.agent_type,
      waiting_for_response:
        item.waiting_for_response != null
          ? normalizeWaitingFlag(item.waiting_for_response)
          : undefined,
      waiting_since: item.waiting_since ?? item.waitingSince ?? null,
      waiting_expires_at:
        item.waiting_expires_at ?? item.waitingExpiresAt ?? null,
      waiting_ttl_seconds:
        item.waiting_ttl_seconds ?? item.waitingTtlSeconds ?? null,
      waiting_for: item.waiting_for ?? item.waitingFor ?? null,
      user_isolated: source === "user",
    }));
  },

  // 🔔 Notifications
  async getNotifications(params?: { space_id?: string }) {
    try {
      const response = await apiClient.get("/notifications", { params });
      return response.data;
    } catch (error: any) {
      console.error("Error fetching notifications:", error);
      throw error;
    }
  },

  // 🧪 Demo (ephemeral, Redis-backed)
  async demoStart(scenario?: string) {
    try {
      const response = await apiClient.post("/demo/start", { scenario });
      // Mark backend demo active for frontend components
      try {
        sessionStorage.setItem("ax_demo_backend", "true");
      } catch {} // eslint-disable-line no-empty
      return response.data;
    } catch (error: any) {
      console.warn(
        "Demo start failed (non-fatal):",
        error?.response?.data || error,
      );
      throw error;
    }
  },

  async markAsRead(notificationId: string) {
    try {
      const response = await apiClient.post(
        `/notifications/${notificationId}/read`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error marking notification as read:", error);
      // Non-blocking error
      return null;
    }
  },
  async markAllAsRead(notificationIds: string[]) {
    try {
      if (!notificationIds || notificationIds.length === 0) return true;

      await apiClient.post("/notifications/read-batch", {
        notification_ids: notificationIds,
      });
      return true;
    } catch (error) {
      console.error("Error marking all as read:", error);
      return false;
    }
  },
  async markAllNotificationsAsRead(allSpaces: boolean = true) {
    try {
      // Pass all_spaces=true to clear notifications across all spaces
      await apiClient.post(
        "/notifications/read-all",
        {},
        {
          params: allSpaces ? { all_spaces: true } : undefined,
        },
      );
      return true;
    } catch (error) {
      console.error("Error marking all notifications as read:", error);
      return false;
    }
  },
  async demoMessages() {
    try {
      const response = await apiClient.get("/demo/messages");
      return response.data?.messages || [];
    } catch (error: any) {
      console.warn(
        "Demo messages fetch failed:",
        error?.response?.data || error,
      );
      return [];
    }
  },
  async demoMessage(content: string, parentId?: string | number) {
    try {
      const body: any = { content };
      if (parentId !== undefined) body.parent_id = parentId;
      const response = await apiClient.post("/demo/message", body);
      return response.data;
    } catch (error: any) {
      console.warn("Demo message failed:", error?.response?.data || error);
      throw error;
    }
  },
  async demoReact(parentId: string | number, emoji: string) {
    try {
      const response = await apiClient.post("/demo/react", {
        parent_id: parentId,
        emoji,
      });
      return response.data;
    } catch (error: any) {
      console.warn("Demo react failed:", error?.response?.data || error);
      throw error;
    }
  },
  async demoClear() {
    try {
      const response = await apiClient.post("/demo/clear", {});
      try {
        sessionStorage.removeItem("ax_demo_backend");
      } catch {} // eslint-disable-line no-empty
      return response.data;
    } catch (error: any) {
      console.warn("Demo clear failed:", error?.response?.data || error);
      throw error;
    }
  },

  // 🔐 Authentication Methods
  async login(email: string, password: string) {
    try {
      const response = await apiClient.post("/auth/login", {
        email,
        password,
      });
      const data = response.data;

      // Store both access and refresh tokens
      if (data.access_token) {
        // Pass refresh_token if available (for Bearer auth), otherwise empty string (for cookie auth)
        storage.setTokens(data.access_token, data.refresh_token || "");
        console.log(
          "✅ Login successful - stored tokens for Bearer authentication",
        );
      } else {
        console.warn("⚠️ Login response missing access token");
      }

      return data;
    } catch (error: any) {
      console.error("Login error:", error);
      throw error;
    }
  },

  async register(userData: {
    email: string;
    password: string;
    username: string;
    full_name: string;
    organization_name?: string;
    invite_code?: string;
  }) {
    try {
      const response = await apiClient.post("/auth/register", userData);
      const data = response.data;

      // Store both access and refresh tokens after registration
      if (data.access_token) {
        // Pass refresh_token if available (for Bearer auth), otherwise empty string (for cookie auth)
        storage.setTokens(data.access_token, data.refresh_token || "");
        console.log(
          "✅ Registration successful - stored tokens for Bearer authentication",
        );
      } else {
        console.warn("⚠️ Registration response missing access token");
      }

      return data;
    } catch (error: any) {
      console.error("Registration error:", error);
      throw error;
    }
  },

  // 🔐 Device session management
  async listDevices() {
    const response = await apiClient.get("/auth/devices");
    return response.data;
  },

  async revokeDevice(deviceId: string) {
    const response = await apiClient.post("/auth/devices/revoke", {
      device_id: deviceId,
    });
    return response.data;
  },

  // Compatibility helper for callers clearing their client-side session.
  async logout() {
    storage.clearTokens();
    storage.clearAll();
    console.log("✅ Logout complete - all tokens cleared");
  },

  // 🔐 SECURE USER-SCOPED ENDPOINTS
  async getUserProfile() {
    try {
      const response = await apiClient.get("/auth/me");
      const profile = normalizeUserProfileResponse(response.data);
      console.log("User profile fetched:", profile);
      return profile;
    } catch (error: any) {
      console.error("Error fetching user profile:", error);
      return null;
    }
  },

  async getUserAgents(options?: {
    limit?: number;
    offset?: number;
    owner?: "me" | string;
    search?: string;
    sort?: string;
  }) {
    try {
      const params = new URLSearchParams();
      if (options?.limit) params.append("limit", String(options.limit));
      if (options?.offset) params.append("offset", String(options.offset));
      if (options?.owner) params.append("owner", options.owner);
      if (options?.search) params.append("search", options.search);
      // Request backend sorting: pinned agents first, then by activity (posts+tasks)
      if (options?.sort) params.append("sort", options.sort);

      const url = params.toString() ? `/auth/agents?${params}` : "/auth/agents";
      const response = await apiClient.get(url);

      if (response.data && Array.isArray(response.data.agents)) {
        const normalizedAgents = response.data.agents.map(
          (agent: RawAgentSummary) => normalizeAgentSummary(agent),
        );
        return {
          agents: normalizedAgents,
          total_count: response.data.total_count ?? normalizedAgents.length,
          limit: response.data.limit ?? options?.limit ?? 20,
          offset: response.data.offset ?? options?.offset ?? 0,
          has_more: response.data.has_more ?? false,
        };
      }
      console.error("Malformed response data:", response.data);
      return {
        agents: [],
        total_count: 0,
        limit: 20,
        offset: 0,
        has_more: false,
      };
    } catch (error: any) {
      console.error("Error fetching user agents:", error);
      throw error;
    }
  },

  // Alias for getUserAgents for compatibility
  async getAgents(options?: {
    limit?: number;
    offset?: number;
    owner?: "me" | string;
    search?: string;
    sort?: string;
  }) {
    return this.getUserAgents(options);
  },

  // Check if an agent name is available (case-insensitive)
  // Uses dedicated backend endpoint for efficient validation
  // Checks: pattern, length, reserved names, and duplicate names
  async checkAgentNameAvailable(
    name: string,
  ): Promise<{ available: boolean; message?: string }> {
    if (!name || name.length < 3) {
      return {
        available: false,
        message: "Name must be at least 3 characters",
      };
    }

    try {
      const response = await apiClient.get("/auth/agents/check-name", {
        params: { name },
      });
      return response.data;
    } catch (error: any) {
      console.error("Error checking agent name availability:", error);
      // On error, allow the submission (backend will catch duplicates)
      return { available: true };
    }
  },

  // Get cloud agents in the current space (includes global agents like ax_guide)
  async getCloudAgents(includeGlobal: boolean = true) {
    try {
      const response = await apiClient.get("/auth/agents/cloud", {
        params: { include_global: includeGlobal },
      });
      return response.data;
    } catch (error: any) {
      console.error("Error fetching cloud agents:", error);
      return { agents: [], count: 0, space_id: null };
    }
  },

  // Get recent agents for quick actions in the current space
  async getRecentAgents(spaceId: string) {
    if (!spaceId) {
      return { agents: [], cached: false };
    }
    try {
      const response = await apiClient.get(
        `/api/v1/spaces/${spaceId}/recent-agents`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error fetching recent agents:", {
        message: error?.message,
        status: error?.response?.status,
        data: error?.response?.data,
        url: error?.config?.url,
      });
      return { agents: [], cached: false };
    }
  },

  async getUserMessages(
    timeHours?: number | null,
    limit: number = 50,
    since?: string | null,
    before?: string | null,
    mediaType?: "all" | "image" | "video" | "audio" | "media" | null,
  ) {
    try {
      // Cap limit at 50 to match backend constraints for transcript fetches
      const cappedLimit = Math.min(limit, 50);
      // Request AI intelligence scores when available (backend may ignore this param if unsupported)
      const query = new URLSearchParams({
        limit: String(cappedLimit),
        include_intelligence: "true",
      });

      const spaceId = storage.getCurrentOrgId?.() ?? "";
      if (spaceId) {
        query.set("space_id", spaceId);
      }

      // Add 'since' parameter for incremental fetching (highest priority - for new messages)
      if (since && typeof since === "string") {
        query.set("since", since);
      }
      // Add 'before' parameter for fetching older messages (for pagination)
      else if (before && typeof before === "string") {
        query.set("before", before);
      }
      // Only add hours parameter if neither 'since' nor 'before' is provided
      else if (
        timeHours !== undefined &&
        timeHours !== null &&
        typeof timeHours === "number" &&
        timeHours > 0
      ) {
        query.set("hours", String(timeHours));
      }

      // Add media_type filter if specified (kept for future backend support)
      // Note: Backend media_type filtering is not yet implemented, so LiveMonitor
      // currently passes null and does client-side filtering instead
      if (mediaType && mediaType !== "all") {
        query.set("media_type", mediaType);
      }

      const url = `/api/v1/messages?${query.toString()}`;

      console.log("🔍 getUserMessages request:", {
        timeHours,
        limit,
        since,
        before,
        mediaType,
        spaceId,
        url,
      });
      let response;
      try {
        response = await apiClient.get(url);
      } catch (error: any) {
        if (error?.response?.status === 404 && spaceId) {
          const legacyQuery = new URLSearchParams(query);
          legacyQuery.delete("space_id");
          legacyQuery.set("org_id", spaceId);
          response = await apiClient.get(
            `/api/messages?${legacyQuery.toString()}`,
          );
        } else {
          throw error;
        }
      }
      console.log("📬 Messages fetched:", response.data);
      console.log("🔐 [Security] Using authenticated message stream");

      // Handle both old and new response formats
      if (response.data.data) {
        // Old format - parse the data
        const posts = this.parsePostData(response.data.data, "user");
        return { posts, user_isolated: true };
      }

      // New format: direct message array
      // Map 'messages' to 'posts' for consistency with the rest of the app
      const rawMessages = response.data.messages || response.data.posts || [];

      const normalizeIntelligenceScores = (value: any) => {
        if (!value || typeof value !== "object") return {};

        const coerceScore01 = (raw: any): number | undefined => {
          if (raw == null) return undefined;
          const n =
            typeof raw === "number"
              ? raw
              : typeof raw === "string"
                ? parseFloat(raw)
                : NaN;
          if (!Number.isFinite(n)) return undefined;
          // Some backends may send scores in 0-100; normalize to 0-1.
          if (n > 1 && n <= 100) return n / 100;
          return n;
        };

        const candidates = [
          value,
          (value as any).ai_analysis,
          (value as any).aiAnalysis,
          (value as any).intelligence,
          (value as any).ai_intelligence,
          (value as any).ai,
          (value as any).analysis,
          (value as any).ai_scores,
          (value as any).aiScores,
          (value as any).scores,
          (value as any).ai_analysis?.scores,
          (value as any).aiAnalysis?.scores,
          (value as any).intelligence?.scores,
          (value as any).analysis?.scores,
        ].filter(Boolean);

        const pickNumber = (getter: (obj: any) => any) => {
          for (const candidate of candidates) {
            const maybe = coerceScore01(getter(candidate));
            if (maybe !== undefined) return maybe;
          }
          return undefined;
        };

        const pickString = (getter: (obj: any) => any) => {
          for (const candidate of candidates) {
            const maybe = getter(candidate);
            if (typeof maybe === "string" && maybe.trim()) return maybe;
          }
          return undefined;
        };

        const pickStringArray = (getter: (obj: any) => any) => {
          for (const candidate of candidates) {
            const maybe = getter(candidate);
            if (Array.isArray(maybe))
              return maybe.filter((item) => typeof item === "string");
          }
          return undefined;
        };

        const out: Record<string, any> = {};

        const quality = pickNumber(
          (obj) => obj.quality_score ?? obj.qualityScore ?? obj.quality,
        );
        const spam = pickNumber(
          (obj) =>
            obj.spam_score ??
            obj.spamScore ??
            obj.spam ??
            obj.spam_risk ??
            obj.spamRisk,
        );
        const toxicity = pickNumber(
          (obj) =>
            obj.toxicity_score ??
            obj.toxicityScore ??
            obj.toxicity ??
            obj.toxic_score ??
            obj.toxicScore,
        );
        const securityRisk = pickNumber(
          (obj) =>
            obj.security_risk ??
            obj.securityRisk ??
            obj.security_score ??
            obj.securityScore ??
            obj.security,
        );
        const securityType = pickString(
          (obj) => obj.security_type ?? obj.securityType,
        );
        const aiReactions = pickStringArray(
          (obj) => obj.ai_reactions ?? obj.aiReactions,
        );

        if (quality !== undefined) out.quality_score = quality;
        if (spam !== undefined) out.spam_score = spam;
        if (toxicity !== undefined) out.toxicity_score = toxicity;
        if (securityRisk !== undefined) out.security_risk = securityRisk;
        if (securityType !== undefined) out.security_type = securityType;
        if (aiReactions !== undefined) out.ai_reactions = aiReactions;

        return out;
      };

      const mappedPosts = rawMessages.map((msg: any) => {
        const str = (v: any) =>
          String(v || "")
            .toLowerCase()
            .trim();
        const authorType = str(msg.author?.type);
        const agentType = str(msg.agent_type);
        const senderType = str(msg.sender_type);
        const msgAuthorType = str(msg.author_type);

        // check explicit agent flags
        const isExplicitAgent =
          authorType === "agent" ||
          agentType === "agent" ||
          senderType === "agent" ||
          msgAuthorType === "agent";

        // Check if agent_type is present and clearly NOT human
        // This catches 'general', 'specialist', 'coding', etc.
        const isLikelyAgentType =
          agentType &&
          agentType !== "human" &&
          agentType !== "user" &&
          agentType !== "unknown";

        const hasAgentIdMismatch =
          msg.agent_id != null &&
          msg.author_id != null &&
          msg.agent_id !== msg.author_id;

        const isAgent =
          isExplicitAgent || isLikelyAgentType || hasAgentIdMismatch;

        const normalizedScores = isAgent
          ? normalizeIntelligenceScores(msg)
          : {
              quality_score: undefined,
              spam_score: undefined,
              toxicity_score: undefined,
              security_risk: undefined,
              security_type: undefined,
              ai_reactions: undefined,
            };

        if (
          import.meta.env?.DEV &&
          url.includes("include_intelligence=true") &&
          rawMessages.indexOf(msg) === 0
        ) {
          const hasAnyScore =
            normalizedScores.quality_score !== undefined ||
            normalizedScores.spam_score !== undefined ||
            normalizedScores.toxicity_score !== undefined ||
            normalizedScores.security_risk !== undefined;
          if (!hasAnyScore && isAgent) {
            console.warn(
              "[AI_INTELLIGENCE] No score fields found on /api/messages response for AGENT (sample keys):",
              Object.keys(msg),
            );
          }
        }

        const resolvedAuthorType =
          msg.author?.type ?? msg.author_type ?? msg.sender_type;
        /**
         * Parse message timestamp with fallbacks during API migration.
         *
         * Priority order (descending):
         * 1) uploaded_at - canonical field from /api/messages
         * 2) uploadedAt - camelCase legacy variant
         * 3) created_at - legacy field (pre-2.0)
         * 4) timestamp/time - deprecated (remove after 2025-Q2)
         */
        const uploadedAt =
          msg.uploaded_at ??
          msg.uploadedAt ??
          msg.created_at ??
          msg.createdAt ??
          msg.timestamp ??
          msg.time ??
          null;
        const waitingForResponse =
          msg.waiting_for_response ??
          msg.waitingForResponse ??
          msg.waiting ??
          msg.waiting_for_reply ??
          undefined;
        const waitingSince =
          msg.waiting_since ??
          msg.waitingSince ??
          msg.waiting_at ??
          msg.waiting_started_at ??
          msg.wait_started_at ??
          null;
        const waitingExpiresAt =
          msg.waiting_expires_at ??
          msg.wait_expires_at ??
          msg.waiting_until ??
          msg.wait_until ??
          null;
        const waitingTtlSeconds =
          msg.waiting_ttl_seconds ??
          msg.wait_ttl_seconds ??
          msg.waiting_ttl ??
          msg.wait_ttl ??
          null;
        const waitingFor =
          msg.waiting_for ??
          msg.waitingFor ??
          msg.waiting_for_user ??
          msg.waitingForUser ??
          null;
        const metadata = msg.metadata ?? msg.meta ?? null;

        return {
          ...msg,
          ...normalizedScores,
          username:
            msg.username ||
            msg.display_name ||
            msg.sender_name ||
            msg.author?.name ||
            "Unknown",
          uploaded_at: uploadedAt || msg.uploaded_at || msg.created_at || null,
          agent_type: isAgent ? "agent" : "user",
          author_id: msg.author?.id || msg.author_id,
          author_type: resolvedAuthorType || (isAgent ? "agent" : "human"), // Default based on detection
          waiting_for_response: normalizeWaitingFlag(waitingForResponse),
          waiting_since: waitingSince,
          waiting_expires_at: waitingExpiresAt,
          waiting_ttl_seconds: waitingTtlSeconds,
          waiting_for: waitingFor,
          metadata,
        };
      });

      // NOTE: Do NOT filter internal agents (like __ai_validator__) here.
      // Their reactions (emoji replies) need to be counted in MessageList.tsx.
      // MessageList handles display filtering (hides internal agent rows) separately.
      const visiblePosts = mappedPosts;

      // Calculate oldest_timestamp from returned messages for pagination
      // Use next_cursor from backend if available, otherwise calculate oldest_timestamp
      const next_cursor = response.data.next_cursor;
      const oldest_timestamp =
        next_cursor ||
        (mappedPosts.length > 0
          ? mappedPosts.reduce(
              (oldest, msg) => {
                const msgTime =
                  msg.uploaded_at || msg.timestamp || msg.created_at;
                if (!msgTime) return oldest;
                return !oldest || new Date(msgTime) < new Date(oldest)
                  ? msgTime
                  : oldest;
              },
              null as string | null,
            )
          : null);

      // Use has_more from backend if available, otherwise estimate
      const has_more =
        response.data.has_more ?? mappedPosts.length >= cappedLimit;

      console.log("📜 API pagination debug:", {
        backend_next_cursor: response.data.next_cursor,
        backend_has_more: response.data.has_more,
        calculated_oldest_timestamp: oldest_timestamp,
        final_has_more: has_more,
        message_count: mappedPosts.length,
      });

      return {
        posts: visiblePosts,
        user_isolated: response.data.user_isolated || true,
        count: response.data.count || 0,
        server_time: response.data.server_time,
        latest_timestamp: response.data.latest_timestamp,
        oldest_timestamp,
        next_cursor,
        has_more,
      };
    } catch (error) {
      console.error("Error fetching user messages:", error);
      return {
        posts: [],
        user_isolated: true,
        count: 0,
        has_more: false,
        next_cursor: null,
      };
    }
  },

  // 🚀 Onboarding helpers
  async runOnboardingDemo() {
    try {
      const response = await apiClient.post("/onboarding/run_demo", {});
      return response.data;
    } catch (error) {
      console.error("Run demo failed:", error);
      throw error;
    }
  },

  async helpGuide(text: string) {
    try {
      const response = await apiClient.post("/onboarding/helpbot", { text });
      return response.data;
    } catch (error) {
      console.error("Help Guide failed:", error);
      throw error;
    }
  },

  async summarizeMessage(
    messageId: string,
  ): Promise<{ summary: string; cached: boolean; message_id: string }> {
    try {
      const response = await apiClient.post(
        `/api/v1/messages/${messageId}/summarize`,
      );
      const data = response.data;

      // Validate response structure
      if (!data || typeof data.summary !== "string") {
        throw new Error("Invalid API response: missing summary field");
      }

      return {
        summary: data.summary,
        cached: !!data.cached,
        message_id: String(data.message_id || messageId),
      };
    } catch (error) {
      console.error("Summarize message failed:", error);
      throw error;
    }
  },

  async registerAgent(agentData: {
    agent_name: string;
    agent_type?: string;
    description?: string;
    pinned_org_id?: string; // optional pin at registration
    follow_user?: boolean; // when true, map to FOLLOW_UUID on both fields
    cloud_function_url?: string; // For cloud agents (legacy)
    enable_cloud_agent?: boolean; // Enable cloud agent (backend auto-populates URL)
    system_prompt?: string; // Custom agent instructions
    capabilities?: any; // Agent capabilities (auto_respond, etc.)
  }) {
    try {
      // Transform frontend data format to backend expected format
      const backendData: any = {
        name: agentData.agent_name, // Backend expects 'name'
        // Also include agent_name for tests and forward-compat
        agent_name: agentData.agent_name,
        agent_type: agentData.agent_type || "general",
        description: agentData.description || "",
        capabilities: agentData.capabilities || {},
      };

      // Cloud agent fields
      if (agentData.enable_cloud_agent) {
        backendData.enable_cloud_agent = agentData.enable_cloud_agent;
      } else if (agentData.cloud_function_url) {
        // Legacy support: if cloud_function_url is provided, enable cloud agent
        backendData.enable_cloud_agent = true;
      }
      if (agentData.system_prompt) {
        backendData.system_prompt = agentData.system_prompt;
      }
      // Mobility semantics at registration
      if (agentData.follow_user) {
        // Follow mode: set BOTH fields to FOLLOW_UUID to satisfy backend preflight
        backendData.pinned_org_id = FOLLOW_UUID;
        backendData.org_id = FOLLOW_UUID;
      } else if (agentData.pinned_org_id) {
        // Pinned mode: set both so org_id === pinned_to_org
        backendData.pinned_org_id = agentData.pinned_org_id;
        backendData.org_id = agentData.pinned_org_id;
      }

      const response = await apiClient.post(
        "/auth/agents/register",
        backendData,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error registering agent:", error);
      throw error;
    }
  },

  async registerExternalAgent(payload: {
    name: string;
    webhook_url: string;
    sub_type?: string;
    /** Organization/space ID to pin the agent to. Defaults to user's current org. */
    org_id?: string;
  }): Promise<{
    agent_id: string;
    webhook_secret: string;
    webhook_verified: boolean;
  }> {
    try {
      const response = await apiClient.post(
        "/auth/agents/register-external",
        payload,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error registering external agent:", error);
      throw error;
    }
  },

  async updateAgent(
    agentId: string,
    updates: {
      name?: string;
      description?: string; // 500 chars max (short tagline)
      bio?: string; // 5000 chars max (detailed background)
      specialization?: string; // 1000 chars max
      agent_type?: string;
      capabilities?: Record<string, any>; // Must be an object, not array
      status?: string;
      pinned_org_id?: string | null; // null to unpin
      // If supported by backend, update current working org ("move now")
      org_id?: string;
      // Optional shorthand to indicate follow user; maps to FOLLOW_UUID fields
      follow_user?: boolean;
      avatar_url?: string | null;
      // Cloud agent toggle - backend auto-populates/clears cloud_function_url
      enable_cloud_agent?: boolean;
      system_prompt?: string;
      // Direct cloud_function_url update for v1/v2 version switching
      cloud_function_url?: string;
      // Web browsing capability for cloud agents (Brave search)
      webBrowsingEnabled?: boolean;
      // Commonflame MCP tools capability for cloud agents
      axMcpEnabled?: boolean;
      // Image generation capability for cloud agents
      imageGenEnabled?: boolean;
      // Web fetch capability for cloud agents
      webFetchEnabled?: boolean;
      // LLM model selection for cloud agents
      model?: string;
      // Model tier selection for cloud agents (quality/cost tradeoff)
      model_tier?: ModelTier;
      // Engine version for v1/v2 switching
      engine_version?: "v1" | "v2";
      // Tool capabilities JSONB - preferred over individual boolean fields
      enabled_tools?: Record<string, boolean>;
      // External webhook agents
      webhook_url?: string;
      settings?: Record<string, any>;
    },
  ) {
    try {
      // Normalize mobility semantics client-side for idempotency with backend
      const payload: any = { ...updates };

      // IMPORTANT: capabilities must be an object, not an array
      // Some legacy agents have capabilities as string[] - strip these out
      if (Array.isArray(payload.capabilities)) {
        console.warn("Stripping array-type capabilities from update payload");
        delete payload.capabilities;
      }

      // Convert camelCase frontend fields to snake_case backend fields
      if (Object.prototype.hasOwnProperty.call(payload, "webBrowsingEnabled")) {
        payload.web_browsing_enabled = payload.webBrowsingEnabled;
        delete payload.webBrowsingEnabled;
      }
      if (Object.prototype.hasOwnProperty.call(payload, "axMcpEnabled")) {
        payload.ax_mcp_enabled = payload.axMcpEnabled;
        delete payload.axMcpEnabled;
      }
      if (Object.prototype.hasOwnProperty.call(payload, "imageGenEnabled")) {
        payload.image_gen_enabled = payload.imageGenEnabled;
        delete payload.imageGenEnabled;
      }
      if (Object.prototype.hasOwnProperty.call(payload, "webFetchEnabled")) {
        payload.web_fetch_enabled = payload.webFetchEnabled;
        delete payload.webFetchEnabled;
      }

      if (payload.follow_user === true) {
        payload.pinned_org_id = FOLLOW_UUID;
        payload.org_id = FOLLOW_UUID;
        delete payload.follow_user;
      } else if (
        Object.prototype.hasOwnProperty.call(payload, "pinned_org_id")
      ) {
        // If setting pinned (non-null), also align org_id; if unpin (null), leave org_id unchanged
        if (
          payload.pinned_org_id &&
          typeof payload.pinned_org_id === "string"
        ) {
          payload.org_id = payload.pinned_org_id;
        }
      } else if (
        typeof payload.org_id === "string" &&
        payload.org_id === FOLLOW_UUID
      ) {
        // Moving directly to FOLLOW_UUID implies follow mode; align pinned as well
        payload.pinned_org_id = FOLLOW_UUID;
      }

      const response = await apiClient.put(`/auth/agents/${agentId}`, payload);
      return response.data;
    } catch (error: any) {
      console.error("Error updating agent:", error);
      throw error;
    }
  },

  async regenerateAgentToken(agentId: string) {
    try {
      const response = await apiClient.post(
        `/auth/agents/${agentId}/regenerate-token`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error regenerating agent token:", error);
      throw error;
    }
  },

  async regenerateExternalWebhookSecret(agentId: string): Promise<{
    webhook_secret?: string;
    webhookSecret?: string;
    secret?: string;
  }> {
    try {
      const response = await apiClient.post(
        `/auth/agents/${agentId}/regenerate-webhook-secret`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error regenerating webhook secret:", error);
      throw error;
    }
  },

  async listAgentKeys(agentId: string): Promise<AgentKeyInfo[]> {
    try {
      const response = await apiClient.get(`/api/v1/agents/${agentId}/keys`);
      return response.data;
    } catch (error: any) {
      console.error("Error listing agent keys:", error);
      throw error;
    }
  },

  async createAgentKey(
    agentId: string,
    payload: AgentKeyCreateRequest = {},
  ): Promise<AgentKeyCreateResponse> {
    try {
      const response = await apiClient.post(
        `/api/v1/agents/${agentId}/keys`,
        payload,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error creating agent key:", error);
      throw error;
    }
  },

  async rotateAgentKey(
    agentId: string,
    keyId: string,
  ): Promise<AgentKeyCreateResponse> {
    try {
      const response = await apiClient.post(
        `/api/v1/agents/${agentId}/keys/${keyId}/rotate`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error rotating agent key:", error);
      throw error;
    }
  },

  async revokeAgentKey(agentId: string, keyId: string): Promise<void> {
    try {
      await apiClient.delete(`/api/v1/agents/${agentId}/keys/${keyId}`);
    } catch (error: any) {
      console.error("Error revoking agent key:", error);
      throw error;
    }
  },

  /**
   * Verify external agent webhook and clear any failure counters.
   * This "unlocks" an agent that was quarantined due to failed dispatches.
   */
  async verifyExternalWebhook(
    agentId: string,
  ): Promise<{ success: boolean; message?: string }> {
    try {
      const response = await apiClient.post(
        `/auth/agents/${agentId}/verify-webhook`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error verifying webhook:", error);
      throw error;
    }
  },

  async getAgentConfig(agentId: string) {
    try {
      const response = await apiClient.get(`/auth/agents/${agentId}/config`);
      return response.data;
    } catch (error: any) {
      console.error("Error getting agent config:", error);
      throw error;
    }
  },

  async updateAgentTeam(agentId: string, teamId: string) {
    try {
      // For now, just log the change since team management might not be implemented in backend
      console.log(`Updating agent ${agentId} to team ${teamId}`);
      // You can implement the actual API call when backend supports it
      return { success: true, message: "Team updated locally" };
    } catch (error: any) {
      console.error("Error updating agent team:", error);
      throw error;
    }
  },

  async deleteAgent(agentId: string) {
    try {
      const response = await apiClient.delete(`/auth/agents/${agentId}`);
      return response.data;
    } catch (error: any) {
      console.error("Error deleting agent:", error);
      throw error;
    }
  },

  async getAgentStats(agentId: string) {
    try {
      const response = await apiClient.get(`/auth/agents/${agentId}/stats`);
      return response.data;
    } catch (error: any) {
      console.error("Error getting agent stats:", error);
      throw error;
    }
  },

  async getAvailableChannels() {
    try {
      const response = await apiClient.get("/api/channels");
      if (Array.isArray(response.data)) {
        return response.data;
      }
      return response.data.channels || ["main", "testing", "dev"];
    } catch (error) {
      console.warn("Channels endpoint not available, using fallback");
      return ["main", "testing", "dev", "status", "chaos"];
    }
  },

  // Post message functions with guardrail error handling
  // Backwards compatible signature; optionally pass channel & parentId
  async postMessage(
    content: string,
    options?: {
      channel?: string;
      parentId?: number | string;
      primaryAgentId?: string;
      mentionedAgentIds?: string[];
      metadata?: Record<string, unknown>;
    },
  ) {
    const channel = options?.channel || "main";
    const parentId = options?.parentId;
    try {
      const metadata: Record<string, unknown> = {
        ...(options?.metadata || {}),
      };
      if (options?.primaryAgentId) {
        metadata.primary_agent_id = options.primaryAgentId;
      }
      if (options?.mentionedAgentIds?.length) {
        metadata.mentioned_agent_ids = options.mentionedAgentIds;
      }

      const body: any = { content, channel };
      if (Object.keys(metadata).length > 0) {
        body.metadata = metadata;
      }
      if (parentId !== undefined) {
        // New threading field
        body.parent_id = String(parentId); // Ensure it's a string as backend expects
        // Transitional: also include legacy field in case backend still accepts it
        body.reply_to = String(parentId);
        body.message_type = "reply";

        // Log to confirm parent_id is being sent
        console.log(
          `🎯 Sending reaction/reply with parent_id: ${parentId}`,
          body,
        );
      }
      const response = await apiClient.post("/auth/messages", body);
      return response.data;
    } catch (error: any) {
      // Handle guardrail violations with user-friendly messages
      if (
        error.response?.status === 400 &&
        error.response?.data?.error === "guardrail_violation"
      ) {
        const violationData = error.response.data;

        // Create user-friendly error object
        const guardrailError = {
          isGuardrailViolation: true,
          title: violationData.title || "🔒 Content Blocked",
          message:
            violationData.message ||
            "Your message has been blocked by security filters.",
          suggestion:
            violationData.suggestion ||
            "Please review your message and try again.",
          violationType: violationData.violation_type,
          severity: violationData.severity || "medium",
          originalError: error.response.data,
        };

        console.warn("🛡️ Guardrail violation:", guardrailError);
        throw guardrailError;
      }

      // Handle other errors normally
      console.error("Error posting message:", error);
      throw error;
    }
  },

  async reactToPost(postId: number, emoji: string) {
    const response = await apiClient.post(
      `/api/v1/messages/${postId}/reactions`,
      {
        emoji,
      },
    );
    return response.data;
  },

  async getPostReactions(postId: number) {
    try {
      const response = await apiClient.get(`/api/messages/${postId}`);
      const message = response.data?.message ?? response.data;
      const reactions = message?.reactions ?? response.data?.reactions ?? {};
      return { reaction_counts: reactions };
    } catch (error) {
      console.warn(`Failed to fetch reactions for post ${postId}:`, error);
      return { reaction_counts: {} };
    }
  },

  async getBatchPostReactions(postIds: number[]) {
    // Message list responses already include reaction counts; there is no
    // canonical backend batch-read endpoint for arbitrary message IDs.
    const reactionCounts = Object.fromEntries(
      postIds.map((postId) => [String(postId), {}]),
    );
    return { reaction_counts: reactionCounts, reactions: reactionCounts };
  },

  async updateMessage(
    messageId: string | number,
    updates: { content?: string; read_state?: string; metadata?: any },
  ) {
    try {
      const response = await apiClient.put(
        `/api/messages/${messageId}`,
        updates,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error updating message:", error?.response?.data || error);
      throw error;
    }
  },

  async adjustMessageWait(messageId: string | number, deltaSeconds: number) {
    try {
      const response = await apiClient.post(`/api/messages/${messageId}/wait`, {
        delta_seconds: deltaSeconds,
      });
      return response.data;
    } catch (error: any) {
      console.error(
        "Error adjusting message wait:",
        error?.response?.data || error,
      );
      throw error;
    }
  },

  async deleteMessage(
    messageId: string | number,
    reason: string,
    permanent: boolean = false,
  ) {
    try {
      const response = await apiClient.delete(`/api/messages/${messageId}`, {
        params: { reason, permanent },
      });
      return response.data;
    } catch (error: any) {
      console.error("Error deleting message:", error?.response?.data || error);
      throw error;
    }
  },

  // Task management functions
  async createTask(taskData: {
    title: string;
    description: string;
    priority?: string;
    requirements?: any;
    agent_name?: string;
  }) {
    // Map frontend priority format to backend expected format
    const priorityMap: { [key: string]: string } = {
      P0: "critical",
      P1: "high",
      P2: "medium",
      P3: "low",
      critical: "critical",
      high: "high",
      medium: "medium",
      low: "low",
    };

    const backendData = {
      title: taskData.title,
      description: taskData.description,
      priority: priorityMap[taskData.priority || "medium"] || "medium",
      requirements: taskData.requirements || {},
    };

    console.log("🚀 [API] Creating task with data:", backendData);

    try {
      const response = await apiClient.post(
        TASK_ROUTES.writeCollection,
        backendData,
      );
      return response.data;
    } catch (error: any) {
      // Handle guardrail violations for task creation
      if (
        error.response?.status === 400 &&
        error.response?.data?.error === "guardrail_violation"
      ) {
        const violationData = error.response.data;

        // Create user-friendly error object for tasks
        const guardrailError = {
          isGuardrailViolation: true,
          title: violationData.title || "🔒 Task Content Blocked",
          message:
            violationData.message ||
            "Your task contains content that has been blocked by security filters.",
          suggestion:
            violationData.suggestion ||
            "Please review your task title and description, then try again.",
          violationType: violationData.violation_type,
          severity: violationData.severity || "medium",
          originalError: error.response.data,
        };

        console.warn("🛡️ Task guardrail violation:", guardrailError);
        throw guardrailError;
      }

      // Handle other errors normally
      console.error("Error creating task:", error);
      throw error;
    }
  },

  async getTasks(limit: number = 50) {
    try {
      const response = await apiClient.get(TASK_ROUTES.collection, {
        params: { limit },
      });

      // The backend returns { tasks: [...], total: number, limit: number, offset: number, filters: {...} }
      // But frontend expects either an array directly or { tasks: [...] }
      if (response.data && Array.isArray(response.data.tasks)) {
        return {
          tasks: response.data.tasks,
          total: response.data.total,
          limit: response.data.limit,
          offset: response.data.offset,
        };
      }

      return response.data;
    } catch (error: any) {
      if (error.response?.status === 404) {
        console.warn(
          "Tasks endpoint not found (404). This endpoint may not be implemented yet.",
        );
      } else {
        console.error("Error fetching tasks:", error);
      }
      return { tasks: [] };
    }
  },

  async getTask(taskId: string) {
    try {
      const response = await apiClient.get(TASK_ROUTES.item(taskId));
      return response.data;
    } catch (error: any) {
      if (error.response?.status === 404) {
        console.warn(`Task ${taskId} not found (404).`);
        return null;
      }
      console.error(`Error fetching task ${taskId}:`, error);
      throw error;
    }
  },

  async updateTaskStatus(taskId: number, status: string) {
    const response = await apiClient.patch(TASK_ROUTES.writeItem(taskId), {
      status,
    });
    return response.data;
  },

  async deleteTask(taskId: number) {
    const response = await apiClient.delete(TASK_ROUTES.writeItem(taskId));
    return response.data;
  },

  // 🔄 API Compatibility Methods for Copied Components
  // These methods provide compatibility with the copied LiveMonitor components

  async getRecentPosts(limit: number = 200, hours?: number) {
    // Alias for getUserMessages to maintain compatibility
    const result = await this.getUserMessages(hours, limit);

    // Ensure field mapping for LiveMonitor components
    if (result.posts) {
      // Debug: Check what timestamp fields are available
      if (result.posts.length > 0) {
        console.log(
          "🕐 Timestamp debug - First post fields:",
          Object.keys(result.posts[0]),
        );
        console.log("🕐 Timestamp debug - Sample timestamps:", {
          uploaded_at: result.posts[0].uploaded_at,
          timestamp: result.posts[0].timestamp,
          created_at: result.posts[0].created_at,
        });
      }

      result.posts = result.posts.map((post: any) => {
        const waitingForResponse =
          post.waiting_for_response ??
          post.waitingForResponse ??
          post.waiting ??
          post.waiting_for_reply ??
          false;

        return {
          ...post,
          author_id: post.author_id || post.author?.id || null,
          author_type: post.author_type || post.author?.type || null,
          agent_username:
            post.agent_username || post.username || post.author || "Unknown",
          uploaded_at: post.uploaded_at || post.timestamp || post.created_at,
          agent_type: post.agent_type || "Agent",
          waiting_for_response: normalizeWaitingFlag(waitingForResponse),
          waiting_since:
            post.waiting_since ??
            post.waitingSince ??
            post.waiting_at ??
            post.waiting_started_at ??
            post.wait_started_at ??
            null,
          waiting_expires_at:
            post.waiting_expires_at ??
            post.wait_expires_at ??
            post.waiting_until ??
            post.wait_until ??
            null,
          waiting_ttl_seconds:
            post.waiting_ttl_seconds ??
            post.wait_ttl_seconds ??
            post.waiting_ttl ??
            post.wait_ttl ??
            null,
          metadata: post.metadata ?? post.meta ?? null,
          is_task: post.is_task || false,
          is_claimed: post.is_claimed || false,
          channel: post.channel || "main",
        };
      });

      // Sort messages newest-first for proper chat flow (as recommended by test_sentinel)
      result.posts.sort((a: any, b: any) => {
        const timeA = new Date(
          a.uploaded_at || a.timestamp || a.created_at,
        ).getTime();
        const timeB = new Date(
          b.uploaded_at || b.timestamp || b.created_at,
        ).getTime();
        return timeB - timeA; // Descending order (newest first)
      });
    }

    return result;
  },

  async getTeams() {
    // Return default teams structure expected by components
    // TODO: Replace with actual teams endpoint when available
    return [
      {
        id: "core",
        name: "Core Team",
        description: "Core development team",
        color: "#3B82F6",
        agents: [],
      },
      {
        id: "research",
        name: "Research",
        description: "Research & development",
        color: "#8B5CF6",
        agents: [],
      },
      {
        id: "creative",
        name: "Creative",
        description: "Creative & design",
        color: "#10B981",
        agents: [],
      },
      {
        id: "analytical",
        name: "Analytical",
        description: "Data & analytics",
        color: "#F59E0B",
        agents: [],
      },
    ];
  },

  async respondToPost(
    postId: number | string,
    content: string,
    options?: {
      primaryAgentId?: string;
      mentionedAgentIds?: string[];
      metadata?: Record<string, unknown>;
    },
  ) {
    return this.postMessage(content, {
      channel: "main",
      parentId: postId,
      ...(options?.primaryAgentId
        ? { primaryAgentId: options.primaryAgentId }
        : {}),
      ...(options?.mentionedAgentIds?.length
        ? { mentionedAgentIds: options.mentionedAgentIds }
        : {}),
      metadata:
        options?.metadata ||
        options?.primaryAgentId ||
        options?.mentionedAgentIds?.length
          ? {
              ...(options?.metadata || {}),
              ...(options?.primaryAgentId
                ? { primary_agent_id: options.primaryAgentId }
                : {}),
              ...(options?.mentionedAgentIds?.length
                ? { mentioned_agent_ids: options.mentionedAgentIds }
                : {}),
            }
          : {},
    });
  },

  async replyToPost(
    postId: number | string,
    content: string,
    options?: {
      primaryAgentId?: string;
      mentionedAgentIds?: string[];
      metadata?: Record<string, unknown>;
    },
  ) {
    return this.postMessage(content, {
      channel: "main",
      parentId: postId,
      ...(options?.primaryAgentId
        ? { primaryAgentId: options.primaryAgentId }
        : {}),
      ...(options?.mentionedAgentIds?.length
        ? { mentionedAgentIds: options.mentionedAgentIds }
        : {}),
      metadata:
        options?.metadata ||
        options?.primaryAgentId ||
        options?.mentionedAgentIds?.length
          ? {
              ...(options?.metadata || {}),
              ...(options?.primaryAgentId
                ? { primary_agent_id: options.primaryAgentId }
                : {}),
              ...(options?.mentionedAgentIds?.length
                ? { mentioned_agent_ids: options.mentionedAgentIds }
                : {}),
            }
          : {},
    });
  },

  // Get Trending Topics - Extract topics from recent messages using same logic as LiveMonitor
  async getTrendingTopics(days_back?: number) {
    try {
      // If no days_back specified, get all data (no time limit)
      const timeLimit = days_back ? days_back * 24 : undefined;
      const dataSource = days_back ? `past ${days_back} days` : "all time";

      console.log(
        `📊 Analyzing messages from ${dataSource} for trending topics...`,
      );

      // Use getUserMessages - if timeLimit is undefined, it gets all messages
      const messages = await this.getUserMessages(timeLimit, 20000); // Increase limit for full analysis
      const hashtagCounts: Record<string, number> = {};

      if (messages.posts && messages.posts.length > 0) {
        console.log(
          `🔍 Analyzing ${messages.posts.length} messages for hashtags...`,
        );

        // Use the same hashtag extraction logic as LiveMonitor
        messages.posts.forEach((post: any) => {
          const content = post.content || "";
          // Extract hashtags using same regex as LiveMonitor: /#\w+/g
          const hashtags = content.match(/#\w+/g) || [];
          hashtags.forEach((hashtag: string) => {
            // Remove the # symbol and normalize to lowercase
            const cleanTag = hashtag.substring(1).toLowerCase();

            // Filter out hashtags that are:
            // - Just numbers (like #18, #123, etc.)
            // - Too short (less than 2 characters)
            // - Common noise words
            const isJustNumbers = /^\d+$/.test(cleanTag);
            const isTooShort = cleanTag.length < 2;
            const isNoise = [
              "a",
              "an",
              "the",
              "is",
              "at",
              "in",
              "on",
              "of",
              "to",
              "be",
            ].includes(cleanTag);

            if (!isJustNumbers && !isTooShort && !isNoise) {
              hashtagCounts[cleanTag] = (hashtagCounts[cleanTag] || 0) + 1;
            }
          });
        });
      }

      // Convert to array and sort by count (descending)
      const trendingTopics = Object.entries(hashtagCounts)
        .map(([topic, count]) => ({ topic, count }))
        .filter(({ count }) => count > 1) // Only include topics mentioned more than once
        .sort((a, b) => b.count - a.count);

      console.log(
        `✅ Found ${trendingTopics.length} trending topics from ${dataSource}`,
        trendingTopics.slice(0, 10), // Log top 10 for debugging
      );

      return {
        success: true,
        trending_topics: trendingTopics,
        total_topics: trendingTopics.length,
        days_analyzed: days_back || "all",
        source: "message_analysis",
      };
    } catch (error: any) {
      console.error("❌ Error analyzing trending topics:", error);
      return {
        success: false,
        error: `Failed to analyze trending topics: ${error.message}`,
        trending_topics: [],
        total_topics: 0,
        source: "error",
      };
    }
  },

  // 🔧 Admin API Methods
  async getFleetControlState(): Promise<FleetControlState> {
    const response = await apiClient.get(FLEET_CONTROL_ROUTES.state);
    return response.data;
  },

  async updateFleetControlState(
    update: FleetControlUpdate,
  ): Promise<FleetControlState> {
    const response = await apiClient.patch(FLEET_CONTROL_ROUTES.state, update);
    return response.data;
  },

  async getFleetControlEnforcement(
    surface: "agent_communication" | "reminders",
  ): Promise<FleetControlEnforcementReadback> {
    const response = await apiClient.get(
      FLEET_CONTROL_ROUTES.enforcement(surface),
    );
    return response.data;
  },

  async adminStats() {
    try {
      const response = await apiClient.get("/api/admin/stats");
      return response.data;
    } catch (error: any) {
      console.error("Error fetching admin stats:", error);
      return {
        total_users: 0,
        active_users: 0,
        total_agents: 0,
        active_agents: 0,
        guardrail_violations: 0,
        recent_signups: 0,
      };
    }
  },

  async adminUsers(
    limit: number = 200,
    offset: number = 0,
    search?: string,
    role_filter?: string,
    options?: {
      status?: "active" | "inactive";
      created_within_days?: number;
      sort_by?: "created" | "username" | "email" | "role" | "status";
      sort_dir?: "asc" | "desc";
    },
  ) {
    let url = `/api/admin/users?limit=${limit}&offset=${offset}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (role_filter) url += `&role_filter=${encodeURIComponent(role_filter)}`;
    if (options?.status) url += `&status=${encodeURIComponent(options.status)}`;
    if (options?.created_within_days)
      url += `&created_within_days=${options.created_within_days}`;
    // sort_by/sort_dir are always sent (backend defaults to created/desc).
    url += `&sort_by=${encodeURIComponent(options?.sort_by ?? "created")}`;
    url += `&sort_dir=${encodeURIComponent(options?.sort_dir ?? "desc")}`;

    const response = await apiClient.get(url);
    return response.data;
  },

  async adminActivity(limit: number = 20, hours: number = 24) {
    try {
      const response = await apiClient.get(
        `/api/admin/activity?limit=${limit}&hours=${hours}`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error fetching admin activity:", error);
      return { activities: [], total: 0, period_hours: hours };
    }
  },

  async adminViolations(limit: number = 20, resolved?: boolean) {
    try {
      let url = `/api/admin/violations?limit=${limit}`;
      if (resolved !== undefined) url += `&resolved=${resolved}`;

      const response = await apiClient.get(url);
      return response.data;
    } catch (error: any) {
      console.error("Error fetching admin violations:", error);
      return { violations: [], total: 0, unresolved: 0 };
    }
  },

  async resolveViolation(violationId: string) {
    try {
      const response = await apiClient.post(
        `/api/admin/violations/${violationId}/resolve`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error resolving violation:", error);
      throw error;
    }
  },

  async adminAIFlaggedIssues(limit: number = 50, minScore: number = 0.5) {
    try {
      const response = await apiClient.get(
        `/api/admin/ai-flagged-issues?limit=${limit}&min_score=${minScore}`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error fetching AI flagged issues:", error);
      // Return empty state - endpoint may not exist yet
      return {
        issues: [],
        total: 0,
        stats: { high_spam: 0, high_toxicity: 0, low_quality: 0 },
      };
    }
  },

  async dismissAIFlaggedIssue(messageId: string, reason: string) {
    try {
      const response = await apiClient.post(
        `/api/admin/ai-flagged-issues/${messageId}/dismiss`,
        { reason },
      );
      return response.data;
    } catch (error: any) {
      console.error("Error dismissing AI flagged issue:", error);
      throw error;
    }
  },

  async getPortkeyStatus() {
    try {
      const response = await apiClient.get("/api/admin/portkey/status");
      return response.data;
    } catch (error: any) {
      console.error("Error fetching Portkey status:", error);
      return {
        portkey_status: "unavailable",
        portkey_url: "unknown",
        portkey_response: { error: "Failed to fetch status" },
        guardrails_config: { input_guardrails: 0, output_guardrails: 0 },
      };
    }
  },

  async adminOrganizations() {
    try {
      const response = await apiClient.get("/api/admin/organizations");
      return response.data;
    } catch (error: any) {
      console.error("Error fetching admin organizations:", error);
      return {
        organizations: [],
        health: {
          total_organizations: 0,
          total_members: 0,
          users_without_orgs: 0,
          users_invalid_orgs: 0,
          health_status: "unknown",
        },
      };
    }
  },

  async adminUpdateUserRole(userId: string, roleData: { role: string }) {
    try {
      const response = await apiClient.patch(
        `/api/admin/users/${userId}/role`,
        roleData,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error updating user role:", error);
      throw error;
    }
  },

  async adminUpdateUserStatus(
    userId: string,
    statusData: { is_active: boolean },
  ) {
    try {
      const response = await apiClient.patch(
        `/api/admin/users/${userId}/status`,
        statusData,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error updating user status:", error);
      throw error;
    }
  },

  async adminAccessRequests(status?: string) {
    try {
      let url = "/api/admin/access-requests";
      if (status) url += `?status=${encodeURIComponent(status)}`;

      const response = await apiClient.get(url);
      return response.data;
    } catch (error: any) {
      // Re-throw so react-query enters isError and the page's error UI shows,
      // matching adminUsers. Swallowing here would make a fetch failure render
      // as an empty list (dead error branch).
      console.error("Error fetching access requests:", error);
      throw error;
    }
  },

  async adminApproveAccessRequest(id: string) {
    try {
      const response = await apiClient.post(
        `/api/admin/access-requests/${id}/approve`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error approving access request:", error);
      throw error;
    }
  },

  async adminDenyAccessRequest(id: string) {
    try {
      const response = await apiClient.post(
        `/api/admin/access-requests/${id}/deny`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error denying access request:", error);
      throw error;
    }
  },

  async adminResetAccessRequest(id: string) {
    try {
      const response = await apiClient.post(
        `/api/admin/access-requests/${id}/reset`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error resetting access request:", error);
      throw error;
    }
  },

  async getActivityReport(days: number): Promise<ActivityReport> {
    try {
      const response = await apiClient.get(
        `/api/admin/activity-report?days=${days}`,
      );
      return response.data;
    } catch (error: any) {
      // Re-throw so react-query enters isError and the section's error UI
      // shows, matching adminAccessRequests.
      console.error("Error fetching activity report:", error);
      throw error;
    }
  },

  async adminGetUserDetails(userId: string) {
    try {
      const response = await apiClient.get(`/api/admin/users/${userId}`);
      return response.data;
    } catch (error: any) {
      console.error("Error fetching user details:", error);
      throw error;
    }
  },

  async adminBulkCreatePersonalOrgs() {
    try {
      const response = await apiClient.post(
        "/api/admin/users/bulk-create-orgs",
      );
      return response.data;
    } catch (error: any) {
      console.error("Error creating bulk organizations:", error);
      throw error;
    }
  },

  async adminDatabaseHealth() {
    try {
      const response = await apiClient.get("/api/admin/database/health");
      return response.data;
    } catch (error: any) {
      console.error("Error fetching database health:", error);
      return {
        health_status: "unknown",
        summary: "Failed to check database health",
        issues_count: 0,
        issues: [],
        error: error.message,
      };
    }
  },

  async adminCloudUsage() {
    try {
      const response = await apiClient.get("/api/admin/cloud-usage");
      return response.data;
    } catch (error: any) {
      console.error("Error fetching cloud usage:", error);
      throw error;
    }
  },

  async adminSetCloudLimit(userId: string, dailyLimit: number | null) {
    try {
      const response = await apiClient.post("/api/admin/cloud-usage/limit", {
        user_id: userId,
        daily_limit: dailyLimit,
      });
      return response.data;
    } catch (error: any) {
      console.error("Error setting cloud usage limit:", error);
      throw error;
    }
  },

  getSpaceStats: async () => {
    try {
      const response = await apiClient.get("/api/organizations/stats/me");
      return response.data;
    } catch (error) {
      console.error("Error fetching space stats:", error);
      // Safe defaults so UI doesn't break
      return {
        is_admin: false,
        limits: { private: 10, invite_only: 5, public: 3 },
        used: { private: 0, invite_only: 0, public: 0 },
        remaining: { private: 10, invite_only: 5, public: 3 },
      };
    }
  },

  // Global Search - unified endpoint for tasks/messages/agents
  async searchGlobal(params: {
    query: string;
    scope?: "all" | "messages" | "tasks" | "agents";
    limit?: number;
    offset?: number;
    spaceId?: string | null;
  }) {
    const query = params.query?.trim() ? params.query.trim() : "*";
    const scope = params.scope ?? "all";
    const limit = params.limit ?? 20;
    const offset = params.offset ?? 0;
    const searchParams = new URLSearchParams();

    searchParams.set("q", query);
    searchParams.set("scope", scope);
    searchParams.set("limit", String(limit));
    searchParams.set("offset", String(offset));

    if (params.spaceId) {
      searchParams.set("space_id", params.spaceId);
    }

    const response = await apiClient.get(
      `/api/search?${searchParams.toString()}`,
    );
    return response.data;
  },

  // Search Messages - uses backend search endpoint with fallback to legacy scan
  async searchMessages(query: string, filters: any = {}) {
    const limit = filters.limit || 20;
    const offset = filters.offset || 0;
    const channelFilter = filters.channel_filter;
    const agentFilter = filters.agent_filter;
    const daysBack = filters.days_back || 0;

    console.log(`🔍 [Search] Query: "${query}", Filters:`, filters);

    try {
      const payload: any = {
        query: query && query.trim() ? query : "*",
        limit,
        offset,
      };

      if (channelFilter && channelFilter.trim()) {
        payload.channel = channelFilter.trim();
      }

      if (daysBack > 0) {
        const from = new Date();
        from.setHours(from.getHours() - daysBack * 24);
        payload.date_from = from.toISOString();
      }

      const response = await apiClient.post("/api/search/messages", payload);

      if (response.data?.success) {
        let messages = (response.data.messages || []).map((msg: any) => ({
          id: msg.id,
          content: msg.content,
          timestamp: msg.timestamp,
          channel: msg.channel || "main",
          author: msg.sender,
          response_to: msg.parent_id || msg.response_to || null,
          relevance_score: msg.relevance_score,
          context: msg.context,
          sender_type: msg.sender_type,
        }));

        if (agentFilter && agentFilter.trim()) {
          const needle = agentFilter.trim().toLowerCase();
          messages = messages.filter((msg: any) =>
            (msg.author || "").toLowerCase().includes(needle),
          );
        }

        return {
          success: true,
          messages,
          topics: response.data.topics || [],
          results_count: response.data.total ?? messages.length,
        };
      }

      console.warn(
        "🔍 [Search] Backend search returned unexpected payload",
        response.data,
      );
      throw new Error(response.data?.error || "Backend search failed");
    } catch (error: any) {
      console.error(
        "Error searching messages via backend:",
        error?.response?.data || error,
      );

      try {
        const timeLimit = daysBack > 0 ? daysBack * 24 : undefined;
        const messages = await this.getUserMessages(timeLimit, 20000);

        if (!messages.posts) {
          return {
            success: true,
            messages: [],
            topics: [],
            results_count: 0,
          };
        }

        const trimmedQuery = query.trim();
        const searchTerm = trimmedQuery.toLowerCase();
        const isIdQuery = /^[a-z0-9-]{6,}$/i.test(trimmedQuery);

        const filteredMessages = messages.posts.filter((post: any) => {
          if (channelFilter && channelFilter.trim()) {
            const channelValue = (post.channel || "main").toLowerCase();
            if (!channelValue.includes(channelFilter.toLowerCase())) {
              return false;
            }
          }

          if (agentFilter && agentFilter.trim()) {
            const authorValue = (
              post.username ||
              post.author ||
              ""
            ).toLowerCase();
            if (!authorValue.includes(agentFilter.toLowerCase())) {
              return false;
            }
          }

          if (!trimmedQuery || trimmedQuery === "*") {
            return true;
          }

          const content = (post.content || "").toLowerCase();
          const author = (post.username || post.author || "").toLowerCase();

          if (isIdQuery) {
            const idCandidates = [
              post.id,
              post.message_id,
              post.uuid,
              post.short_id,
              post.shortcode,
              post.response_to,
              post.parent_id,
            ];
            const matchesId = idCandidates.some((candidate) => {
              if (candidate === undefined || candidate === null) return false;
              return String(candidate).toLowerCase().includes(searchTerm);
            });
            if (matchesId) {
              return true;
            }
          }

          if (trimmedQuery.startsWith("#")) {
            const hashtagToFind = trimmedQuery.substring(1).toLowerCase();
            const contentHashtags = content.match(/#\w+/g) || [];
            return contentHashtags.some((tag: string) => {
              const cleanTag = tag.substring(1).toLowerCase();
              return (
                cleanTag === hashtagToFind ||
                cleanTag.includes(hashtagToFind) ||
                hashtagToFind.includes(cleanTag)
              );
            });
          }

          if (trimmedQuery.includes(".*")) {
            try {
              const regex = new RegExp(searchTerm, "i");
              return regex.test(content) || regex.test(author);
            } catch {
              return (
                content.includes(searchTerm) || author.includes(searchTerm)
              );
            }
          }

          return content.includes(searchTerm) || author.includes(searchTerm);
        });

        const totalResults = filteredMessages.length;
        const paginated = filteredMessages.slice(offset, offset + limit);

        const results = paginated.map((post: any) => ({
          id: post.id,
          content: post.content,
          timestamp: post.uploaded_at || post.timestamp,
          channel: post.channel || "main",
          author: post.username || post.author,
          response_to: post.response_to,
        }));

        return {
          success: true,
          messages: results,
          topics: [],
          results_count: totalResults,
        };
      } catch (fallbackError: any) {
        console.error("Error during fallback search:", fallbackError);
        return {
          success: false,
          error: fallbackError?.message || error?.message || "Search failed",
          messages: [],
          topics: [],
          results_count: 0,
        };
      }
    }
  },

  // 🏢 Organization Management API Methods
  async getOrganizations() {
    try {
      const response = await apiClient.get(SPACE_ROUTES.collection);
      return normalizeApiSpacesResponse(response.data) as any;
    } catch (error: any) {
      console.error("Error fetching organizations/spaces:", error);
      throw error;
    }
  },

  async getOrganizationBySlug(slug: string) {
    try {
      const response = await apiClient.get(
        `/api/organizations/slug/${encodeURIComponent(slug)}`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error fetching organization by slug:", error);
      throw error;
    }
  },

  async updateOrganization(
    orgId: string,
    data: { description?: string; is_archived?: boolean },
  ) {
    try {
      const response = await apiClient.put(`/api/organizations/${orgId}`, data);
      return response.data;
    } catch (error: any) {
      console.error("Error updating organization:", error);
      throw error;
    }
  },

  async getPublicOrganizations() {
    try {
      const response = await apiClient.get("/api/organizations/public");
      return response.data;
    } catch (error: any) {
      console.error("Error fetching public organizations:", error);
      return [];
    }
  },

  async createOrganization(orgData: {
    name: string;
    description?: string;
    visibility: string;
    /** §9.2 — opt-in guest access flag, passed through to backend at creation time */
    enable_guest_access?: boolean;
  }) {
    try {
      const response = await apiClient.post(
        "/api/organizations/create",
        orgData,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error creating organization:", error);
      throw error;
    }
  },

  async switchOrganization(orgId: string) {
    try {
      const response = await apiClient.post(SPACE_ROUTES.switch, {
        space_id: orgId,
      });
      return response.data;
    } catch (error: any) {
      console.error("Error switching organization:", error);
      throw error;
    }
  },

  // Space API methods (new)
  async getSpaces() {
    try {
      const response = await apiClient.get(SPACE_ROUTES.collection);
      const spaces = normalizeApiSpacesResponse(response.data);
      return {
        ...(response.data && !Array.isArray(response.data)
          ? response.data
          : {}),
        spaces,
      };
    } catch (error: any) {
      console.error("Error fetching spaces:", error);
      throw error;
    }
  },

  async switchSpace(spaceId: string) {
    try {
      const response = await apiClient.post(SPACE_ROUTES.switch, {
        space_id: spaceId,
      });
      return response.data;
    } catch (error: any) {
      console.error("Error switching space:", error);
      throw error;
    }
  },

  async leaveOrganization(orgId: string) {
    try {
      const response = await apiClient.delete(
        `/api/organizations/leave/${orgId}`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error leaving organization:", error);
      throw error;
    }
  },

  async updateMemberRole(
    orgId: string,
    userId: string,
    role: "admin" | "member",
  ) {
    try {
      const response = await apiClient.put(
        `/api/organizations/${orgId}/members/${userId}/role`,
        { role },
      );
      return response.data;
    } catch (error: any) {
      console.error("Error updating member role:", error);
      throw error;
    }
  },

  async getInviteDetails(code: string) {
    try {
      const response = await apiClient.get(
        `/api/organizations/invites/${code}`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error fetching invite details:", error);
      throw error;
    }
  },

  async joinOrganization(data: { invite_code: string }) {
    try {
      const response = await apiClient.post("/api/organizations/join", data);
      return response.data;
    } catch (error: any) {
      console.error("Error joining organization:", error);
      throw error;
    }
  },

  async joinPublicOrganization(orgId: string) {
    try {
      const response = await apiClient.post("/api/organizations/join-public", {
        org_id: orgId,
      });
      return response.data;
    } catch (error: any) {
      console.error("Error joining public organization:", error);
      throw error;
    }
  },

  async createOrganizationInvite(
    orgId: string,
    inviteData: {
      expires_hours?: number;
      max_uses?: number;
    },
  ) {
    try {
      const response = await apiClient.post(
        `/api/organizations/${orgId}/invites`,
        inviteData,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error creating organization invite:", error);
      throw error;
    }
  },

  async getOrganizationMembers(orgId: string) {
    try {
      const response = await apiClient.get(
        `/api/organizations/${orgId}/members`,
      );
      return response.data;
    } catch (error: any) {
      console.error("Error fetching organization members:", error);
      throw error;
    }
  },

  // Task Notes API Methods
  async getTaskNotes(taskId: string) {
    try {
      const response = await apiClient.get(TASK_ROUTES.notes(taskId));
      return response.data;
    } catch (error: any) {
      console.error("Error fetching task notes:", error);
      throw error;
    }
  },

  async createTaskNote(
    taskId: string,
    noteData: {
      content?: string;
      note?: string;
      note_type?: string;
      visibility?: string;
    },
  ) {
    try {
      const content = (noteData.content || noteData.note || "").trim();
      const visibility =
        noteData.visibility === "org" ? "team" : noteData.visibility;

      const payload = {
        content,
        note_type: noteData.note_type || "general",
        ...(visibility ? { visibility } : {}),
      };

      const response = await apiClient.post(TASK_ROUTES.notes(taskId), payload);
      return response.data;
    } catch (error: any) {
      // Handle guardrail violations with proper error structure
      if (
        error.response?.status === 400 &&
        error.response?.data?.error === "guardrail_violation"
      ) {
        const guardrailError = {
          ...error.response.data,
          isGuardrailViolation: true,
        };

        console.warn("🛡️ Task note guardrail violation:", guardrailError);
        throw guardrailError;
      }

      // Handle other errors normally
      console.error("Error creating task note:", error);
      throw error;
    }
  },

  async deleteTaskNote(taskId: string, noteId: string) {
    try {
      const response = await apiClient.delete(TASK_ROUTES.note(taskId, noteId));
      return response.data;
    } catch (error: any) {
      console.error("Error deleting task note:", error);
      throw error;
    }
  },

  async fetchRoster(
    orgId: string,
    params?: {
      type?: "human" | "agent";
      search?: string;
      limit?: number;
      offset?: number;
    },
  ): Promise<RosterResponse> {
    if (!orgId) {
      throw new Error("Organization ID is required to fetch roster");
    }

    const response = await apiClient.get(`/api/organizations/${orgId}/roster`, {
      params: {
        type: params?.type,
        search: params?.search,
        limit: params?.limit,
        offset: params?.offset,
      },
    });

    return response.data as RosterResponse;
  },

  // ─── Guest Space Access ──────────────────────────────────────────────────

  /** List channels for a space. Returns guest_accessible flag per channel.
   *  Backend returns { channel_name, guest_accessible, updated_at }[] —
   *  normalized here to match the Channel interface (channel_name → id + name).
   */
  async getSpaceChannels(spaceId: string) {
    try {
      // GET /api/v1/spaces/{space_id}/channels → ChannelSettingResponse[] (direct array)
      const response = await apiClient.get(
        `/api/v1/spaces/${spaceId}/channels`,
      );
      const raw: import("@/types/space").Channel[] = Array.isArray(
        response.data,
      )
        ? response.data
        : (response.data?.channels ?? []);
      // Inject space_id (not returned by backend) so callers can use it for PATCH
      return raw.map((c) => ({ ...c, space_id: spaceId }));
    } catch (error: any) {
      console.error("Error fetching space channels:", error);
      throw error;
    }
  },

  /** Toggle guest_accessible on a channel. Owner/admin only.
   *  Backend: PATCH /api/v1/spaces/{space_id}/channels/{channel_name}
   *  Uses channel name (not ID) — matches backend route param.
   */
  async setChannelGuestAccess(
    spaceId: string,
    channelName: string,
    guestAccessible: boolean,
  ) {
    try {
      const response = await apiClient.patch(
        `/api/v1/spaces/${spaceId}/channels/${encodeURIComponent(channelName)}`,
        { guest_accessible: guestAccessible },
      );
      return response.data as import("@/types/space").Channel;
    } catch (error: any) {
      console.error("Error updating channel guest access:", error);
      throw error;
    }
  },

  /** Create a guest invite link. Returns invite_url (shown once — never stored).
   *  Backend returns plaintext_token — we construct the shareable URL here
   *  so callers receive a ready-to-display invite_url on the SpaceInvite object.
   */
  async createSpaceInvite(
    spaceId: string,
    opts: { expires_at?: string | null; max_uses?: number | null } = {},
  ) {
    try {
      const response = await apiClient.post(
        `/api/v1/spaces/${spaceId}/invites`,
        {
          expires_at: opts.expires_at ?? null,
          // Use undefined-check (not nullish coalescing) so null passes through
          // as "unlimited" — null ?? 1 would silently cap unlimited invites at 1.
          max_uses: opts.max_uses === undefined ? 1 : opts.max_uses,
        },
      );
      // Backend returns plaintext_token (one-time) — map to invite_url for display.
      const raw = response.data as {
        plaintext_token?: string;
      } & import("@/types/space").SpaceInvite;
      return {
        ...raw,
        invite_url: raw.plaintext_token
          ? `${window.location.origin}/invite/${raw.plaintext_token}`
          : undefined,
      } as import("@/types/space").SpaceInvite;
    } catch (error: any) {
      console.error("Error creating space invite:", error);
      throw error;
    }
  },

  /** List active invites for a space.
   *  Backend returns a plain array (not { invites: [...] }).
   */
  async getSpaceInvites(spaceId: string) {
    try {
      const response = await apiClient.get(`/api/v1/spaces/${spaceId}/invites`);
      // Handle both plain array and wrapped object for forward-compat
      const data = Array.isArray(response.data)
        ? response.data
        : (response.data?.invites ?? []);
      return data as import("@/types/space").SpaceInvite[];
    } catch (error: any) {
      console.error("Error fetching space invites:", error);
      throw error;
    }
  },

  /** Revoke an invite link. */
  async revokeSpaceInvite(spaceId: string, inviteId: string) {
    try {
      await apiClient.delete(`/api/v1/spaces/${spaceId}/invites/${inviteId}`);
    } catch (error: any) {
      console.error("Error revoking space invite:", error);
      throw error;
    }
  },

  // Teams API (stubbed for now until backend endpoint exists)
  async createTeam(teamData: {
    name: string;
    description?: string;
    color?: string;
  }) {
    try {
      console.warn(
        "[API] createTeam is currently a stub. Returning local object.",
      );
      return {
        id: `${Date.now()}`,
        name: teamData.name,
        description: teamData.description || `${teamData.name} team`,
        color: teamData.color || "#3B82F6",
      };
    } catch (error: any) {
      console.error("Error creating team:", error);
      throw error;
    }
  },
};
