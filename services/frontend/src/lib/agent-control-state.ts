import type { AgentControlState } from "@/services/agentControlService";

export type AgentControlMode =
  | "active"
  | "disable_temporary"
  | "disable_indefinite"
  | "no_reply"
  | "routing_only";

export type AgentControlPresentation = {
  mode: AgentControlMode;
  label: string;
  detail: string | null;
  until: string | null;
  canReenable: boolean;
};

function parseActiveUntil(until: string | null | undefined, nowMs: number) {
  if (!until) return null;
  const timestamp = Date.parse(until);
  if (!Number.isFinite(timestamp)) return null;
  return timestamp > nowMs ? new Date(timestamp).toISOString() : null;
}

export function formatAgentControlUntil(until: string | null | undefined) {
  if (!until) return null;
  const timestamp = Date.parse(until);
  if (!Number.isFinite(timestamp)) return null;
  return new Date(timestamp).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

export function getAgentControlPresentation(
  state: AgentControlState | null | undefined,
  nowMs: number = Date.now(),
): AgentControlPresentation {
  if (!state) {
    return {
      mode: "active",
      label: "Active",
      detail: null,
      until: null,
      canReenable: false,
    };
  }

  const activeDisabledUntil = parseActiveUntil(state.disabled_until, nowMs);
  if (state.is_disabled) {
    if (activeDisabledUntil) {
      return {
        mode: "disable_temporary",
        label: "Taking a break",
        detail: state.disabled_reason || "Temporarily disabled in this space.",
        until: activeDisabledUntil,
        canReenable: true,
      };
    }
    return {
      mode: "disable_indefinite",
      label: "Disabled",
      detail: state.disabled_reason || "Disabled until re-enabled.",
      until: null,
      canReenable: true,
    };
  }

  const activeNoReplyUntil = parseActiveUntil(state.no_reply_until, nowMs);
  if (state.no_reply) {
    return {
      mode: "no_reply",
      label: activeNoReplyUntil ? "Taking a break" : "Chose not to reply",
      detail:
        state.no_reply_reason ||
        (activeNoReplyUntil
          ? "Temporarily not replying."
          : "Skipped this turn."),
      until: activeNoReplyUntil,
      canReenable: true,
    };
  }

  const activeRoutingOnlyUntil = parseActiveUntil(
    state.routing_only_until,
    nowMs,
  );
  if (state.routing_only) {
    return {
      mode: "routing_only",
      label: "Routing only",
      detail:
        state.routing_only_reason ||
        (activeRoutingOnlyUntil
          ? "Temporarily delegating without direct replies."
          : "Delegating without direct replies."),
      until: activeRoutingOnlyUntil,
      canReenable: true,
    };
  }

  return {
    mode: "active",
    label: "Active",
    detail: null,
    until: null,
    canReenable: false,
  };
}
