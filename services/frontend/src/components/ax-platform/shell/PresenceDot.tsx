import { cn } from "@/lib/utils";
import type { PresenceStatus } from "@/hooks/usePresence";

interface PresenceDotProps {
  status: PresenceStatus;
  /**
   * 0.0 (fully stale) → 1.0 (just written).
   * Pass getFreshness(agentName) to dim the dot as the heartbeat TTL drains.
   * Without this prop, dot renders at full opacity (binary on/off).
   */
  freshness?: number;
  className?: string;
  showTooltip?: boolean;
}

/**
 * PresenceDot — small colored liveness indicator for agents.
 *
 * active  → cyan + animate-ping pulse ring; dims as TTL drains if freshness provided
 * idle    → amber, no pulse; dims as TTL drains if freshness provided
 * offline → slate/gray, no pulse, full opacity
 *
 * Usage:
 *   const { getStatus, getFreshness } = usePresence();
 *   <PresenceDot
 *     status={getStatus("frontend_sentinel")}
 *     freshness={getFreshness("frontend_sentinel")}
 *   />
 */
export function PresenceDot({
  status,
  freshness,
  className,
  showTooltip = true,
}: PresenceDotProps) {
  const label =
    status === "active" ? "Active" : status === "idle" ? "Idle" : "Offline";

  // Interpolate opacity 0.35–1.0 based on heartbeat freshness.
  // Gives users a real-time confidence signal without waiting for hard expiry.
  const opacity =
    freshness !== undefined && status !== "offline"
      ? 0.35 + freshness * 0.65
      : 1;

  return (
    <span
      role="status"
      aria-label={label}
      title={showTooltip ? label : undefined}
      className={cn("relative inline-flex h-2 w-2 shrink-0", className)}
      style={freshness !== undefined ? { opacity } : undefined}
    >
      {/* Pulse ring — active only */}
      {status === "active" && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cyan-400 opacity-75" />
      )}
      {/* Solid dot */}
      <span
        className={cn(
          "relative inline-flex h-2 w-2 rounded-full",
          status === "active" && "bg-cyan-400",
          status === "idle" && "bg-amber-400",
          status === "offline" && "bg-slate-500",
        )}
      />
    </span>
  );
}
