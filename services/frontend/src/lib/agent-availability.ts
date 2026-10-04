/**
 * Agent availability — shared derivation for every surface that shows
 * "is this agent around right now": the Agents tab (MCP widget), the @mention
 * autocomplete dropdown, and the quick-action route grid.
 *
 * This mirrors the MCP agent-dashboard widget's availability logic
 * (`_derive_availability_label` / `AVAILABILITY_LABEL_META` / `_LABEL_PRIORITY`)
 * so the labels, dot colors, and live-first ordering stay identical across the
 * platform. The canonical roster (`GET /api/v1/agents` → `serialize_agent`)
 * already emits the raw fields below; before this helper the mention surfaces
 * threw them away and rendered a blanket static "ACTIVE", sorted alphabetically.
 *
 * Heartbeat freshness (`presence_fresh` / `presence_age_seconds` / the SSE
 * `last_heartbeat`) is the live signal. Because heartbeat coverage is still
 * rolling out (most agents don't POST presence yet), we fall back to the
 * message-derived `lifecycle_state` so the signal stays meaningful instead of
 * collapsing everyone to "dormant".
 */

export type AvailabilityKey =
  | "online"
  | "idle"
  | "needs_setup"
  | "dormant"
  | "disabled";

export interface AvailabilityMeta {
  key: AvailabilityKey;
  /** Single-word label shown to users. */
  text: string;
  /** Status-dot hex color. */
  dot: string;
  /** Sort weight — lower sorts first (live agents on top). */
  priority: number;
}

// Order/colors match the MCP widget's AVAILABILITY_LABEL_META + _LABEL_PRIORITY.
export const AVAILABILITY_META: Record<AvailabilityKey, AvailabilityMeta> = {
  online: { key: "online", text: "Online", dot: "#22c55e", priority: 0 },
  idle: { key: "idle", text: "Idle", dot: "#eab308", priority: 1 },
  needs_setup: {
    key: "needs_setup",
    text: "Needs setup",
    dot: "#f97316",
    priority: 2,
  },
  dormant: { key: "dormant", text: "Dormant", dot: "#9ca3af", priority: 3 },
  disabled: { key: "disabled", text: "Disabled", dot: "#ef4444", priority: 4 },
};

export interface AgentAvailabilityInput {
  status?: string | null;
  lifecycle_state?: string | null;
  is_online?: boolean | null;
  presence_fresh?: boolean | null;
  presence_age_seconds?: number | null;
  last_heartbeat?: string | null;
  last_heartbeat_at?: string | null;
  last_seen?: string | null;
  /** Agent origin (e.g. "space_agent"). Space agents like Commonflame are built into
   *  the platform and are always-on unless explicitly disabled. */
  origin?: string | null;
}

// Thresholds match the backend presence-join contract: fresh < 60s = online,
// < 5m = idle. (derive_presence_fields in app/schemas/agent.py.)
const ONLINE_MAX_AGE_SECONDS = 60;
const IDLE_MAX_AGE_SECONDS = 300;

function heartbeatAgeSeconds(input: AgentAvailabilityInput): number | null {
  if (
    typeof input.presence_age_seconds === "number" &&
    Number.isFinite(input.presence_age_seconds)
  ) {
    return input.presence_age_seconds;
  }
  const heartbeatTimestamp =
    input.last_heartbeat || input.last_heartbeat_at || input.last_seen;
  if (heartbeatTimestamp) {
    const parsed = Date.parse(heartbeatTimestamp);
    if (!Number.isNaN(parsed)) {
      return (Date.now() - parsed) / 1000;
    }
  }
  return null;
}

export function deriveAvailabilityKey(
  input: AgentAvailabilityInput,
): AvailabilityKey {
  const status = String(input.status || "").toLowerCase();
  if (
    status === "disabled" ||
    status === "suspended" ||
    status === "quarantined"
  ) {
    return "disabled";
  }

  // Space agents (e.g. Commonflame) are built into the platform and always available
  // unless disabled — they don't rely on a heartbeat to be "on".
  if (String(input.origin || "").toLowerCase() === "space_agent") {
    return "online";
  }

  const age = heartbeatAgeSeconds(input);
  const fresh =
    input.is_online === true ||
    input.presence_fresh === true ||
    (age !== null && age < ONLINE_MAX_AGE_SECONDS);
  if (fresh) return "online";
  if (age !== null && age < IDLE_MAX_AGE_SECONDS) return "idle";

  // No fresh heartbeat — fall back to the message-derived lifecycle so the
  // signal stays useful while heartbeat coverage rolls out. "active"/"idle"
  // lifecycles mean recently seen (but not live) → Idle; long-quiet → Dormant.
  const lifecycle = String(input.lifecycle_state || "").toLowerCase();
  if (lifecycle === "active" || lifecycle === "idle") return "idle";
  if (lifecycle === "dormant") return "dormant";

  return "dormant";
}

export function availabilityMetaFor(
  input: AgentAvailabilityInput,
): AvailabilityMeta {
  return AVAILABILITY_META[deriveAvailabilityKey(input)];
}

export function availabilityPriority(key: AvailabilityKey): number {
  return AVAILABILITY_META[key]?.priority ?? AVAILABILITY_META.dormant.priority;
}
