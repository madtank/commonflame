import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-clean";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface AgentHeartbeatPayload {
  agent: string;
  model: string;
  status: string;
  ts: string; // ISO-8601 UTC
  last_task: string;
  capabilities: string[];
}

export interface PresenceEntry {
  agentId: string;
  agentName: string;
  presence: "online" | "offline";
  responsive: boolean;
  lastActive: string | null;
  agentType?: string;
  heartbeat?: AgentHeartbeatPayload;
  receivedAt: number; // Date.now() ms
}

export type PresenceStatus = "active" | "idle" | "offline";

type BulkPresenceResponse = {
  agents?: Array<{
    agent_id: string;
    name: string;
    presence: "online" | "offline";
    responsive: boolean;
    last_active: string | null;
    agent_type?: string;
  }>;
};

type LegacyPresenceContextResponse = Record<
  string,
  {
    value?: unknown;
    expires_at?: number;
  }
>;

// ─── Constants ────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 30_000;
const ACTIVE_THRESHOLD_MS = 60_000;
const IDLE_THRESHOLD_MS = 120_000;
const TTL_MS = 120_000;

let bulkPresenceEndpointAvailable: boolean | null = null;

// ─── Helpers ─────────────────────────────────────────────────────────────────

function normalizePresenceKey(value: string | null | undefined): string {
  return (value || "").replace(/^@/, "").trim();
}

function getPresenceTimestamp(entry: PresenceEntry | undefined): number | null {
  const ts = entry?.lastActive || entry?.heartbeat?.ts;
  if (!ts) return null;
  const parsed = new Date(ts).getTime();
  return Number.isFinite(parsed) ? parsed : null;
}

export function getPresenceStatus(
  entry: PresenceEntry | undefined,
): PresenceStatus {
  if (!entry || entry.responsive === false || entry.presence === "offline") {
    return "offline";
  }

  const timestamp = getPresenceTimestamp(entry);
  if (timestamp === null) {
    return "active";
  }

  const ageMs = Date.now() - timestamp;
  if (ageMs < ACTIVE_THRESHOLD_MS) return "active";
  if (ageMs < IDLE_THRESHOLD_MS) return "idle";
  return "offline";
}

/**
 * Returns 0.0 (fully stale) → 1.0 (just written).
 * Used to dim the PresenceDot as TTL drains.
 */
export function getPresenceFreshness(entry: PresenceEntry | undefined): number {
  const timestamp = getPresenceTimestamp(entry);
  if (timestamp === null) return 0;
  const ageMs = Date.now() - timestamp;
  return Math.max(0, 1 - ageMs / TTL_MS);
}

// ─── API fetch ────────────────────────────────────────────────────────────────

async function fetchPresence(
  spaceId?: string | null,
): Promise<Record<string, PresenceEntry>> {
  if (bulkPresenceEndpointAvailable === false) {
    return fetchLegacyPresence();
  }

  try {
    const response = await apiClient.get<BulkPresenceResponse>(
      "/api/v1/agents/presence",
      {
        params: spaceId ? { space_id: spaceId } : undefined,
      },
    );

    bulkPresenceEndpointAvailable = true;

    const result: Record<string, PresenceEntry> = {};
    const now = Date.now();

    for (const item of response.data.agents || []) {
      const agentName = normalizePresenceKey(item.name);
      if (!agentName) continue;

      result[agentName] = {
        agentId: item.agent_id,
        agentName,
        presence: item.presence,
        responsive: item.responsive,
        lastActive: item.last_active,
        agentType: item.agent_type,
        heartbeat: item.last_active
          ? {
              agent: agentName,
              model: "",
              status: item.presence,
              ts: item.last_active,
              last_task: "",
              capabilities: [],
            }
          : undefined,
        receivedAt: now,
      };
    }

    return result;
  } catch (error) {
    const status =
      typeof error === "object" &&
      error &&
      "response" in error &&
      typeof (error as { response?: { status?: unknown } }).response?.status ===
        "number"
        ? (error as { response?: { status?: number } }).response?.status
        : null;

    if (status === 404) {
      bulkPresenceEndpointAvailable = false;
      return fetchLegacyPresence();
    }

    throw error;
  }
}

async function fetchLegacyPresence(): Promise<Record<string, PresenceEntry>> {
  const response = await apiClient.get<LegacyPresenceContextResponse>(
    "/api/v1/context",
    { params: { topic: "presence", prefix: "heartbeat" } },
  );

  const result: Record<string, PresenceEntry> = {};
  const now = Date.now();

  for (const [contextKey, raw] of Object.entries(response.data || {})) {
    const agentName = normalizePresenceKey(
      contextKey.replace(/^heartbeat:/, ""),
    );
    if (!agentName) continue;

    let heartbeat: AgentHeartbeatPayload | null = null;
    try {
      if (typeof raw?.value === "string") {
        heartbeat = JSON.parse(raw.value) as AgentHeartbeatPayload;
      } else if (raw?.value && typeof raw.value === "object") {
        heartbeat = raw.value as AgentHeartbeatPayload;
      }
    } catch {
      continue;
    }

    if (!heartbeat?.agent) continue;

    result[agentName] = {
      agentId: heartbeat.agent,
      agentName,
      presence: "online",
      responsive: true,
      lastActive: heartbeat.ts || null,
      heartbeat,
      receivedAt: now,
    };
  }

  return result;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

interface UsePresenceResult {
  presence: Record<string, PresenceEntry>;
  getStatus: (agentName: string) => PresenceStatus;
  getFreshness: (agentName: string) => number;
  isLoading: boolean;
  refresh: () => void;
}

export function usePresence(spaceId?: string | null): UsePresenceResult {
  const normalizedSpaceId = spaceId?.trim() || null;
  const presenceQuery = useQuery({
    queryKey: ["agent-presence", normalizedSpaceId || "default"],
    queryFn: () => fetchPresence(normalizedSpaceId),
    enabled: Boolean(normalizedSpaceId),
    staleTime: 10_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval:
      typeof document === "undefined" || document.visibilityState === "visible"
        ? POLL_INTERVAL_MS
        : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });

  const getStatus = useCallback(
    (agentName: string): PresenceStatus =>
      getPresenceStatus(
        presenceQuery.data?.[normalizePresenceKey(agentName)] || undefined,
      ),
    [presenceQuery.data],
  );

  const getFreshness = useCallback(
    (agentName: string): number =>
      getPresenceFreshness(
        presenceQuery.data?.[normalizePresenceKey(agentName)] || undefined,
      ),
    [presenceQuery.data],
  );

  return {
    presence: presenceQuery.data || {},
    getStatus,
    getFreshness,
    isLoading: presenceQuery.isPending,
    refresh: () => {
      void presenceQuery.refetch();
    },
  };
}
