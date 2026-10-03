/**
 * Agent Kill Switch Component
 *
 * Allows users to instantly disable/enable their agents to stop mention responses
 * and prevent runaway conversation loops
 */

import React, { useState } from "react";
import { Clock3, PauseCircle, Play, Power, PowerOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  formatAgentControlUntil,
  getAgentControlPresentation,
} from "@/lib/agent-control-state";
import {
  agentControlService,
  type AgentControlState,
  type AgentControlUpdate,
} from "@/services/agentControlService";

interface AgentKillSwitchProps {
  agentId: string;
  agentName: string;
  isDisabled: boolean;
  disabledReason?: string;
  controlState?: AgentControlState | null;
  onToggle?: (state: AgentControlState) => void;
  size?: "sm" | "md" | "lg";
  showLabel?: boolean;
  scope?: "agent" | "workspace" | "global" | "managed";
  disableReasonOverride?: string;
  targetSlug?: string;
}

export function AgentKillSwitch({
  agentId,
  agentName,
  isDisabled,
  disabledReason,
  controlState,
  onToggle,
  size = "md",
  showLabel = true,
  scope = "agent",
  disableReasonOverride,
  targetSlug,
}: AgentKillSwitchProps) {
  const [loading, setLoading] = useState(false);

  const effectiveControlState = controlState || {
    is_disabled: isDisabled,
    disabled_reason: disabledReason,
    disabled_by: [],
  };
  const controlPresentation = getAgentControlPresentation(
    effectiveControlState,
  );
  const untilText = formatAgentControlUntil(controlPresentation.until);
  const statusHint = untilText
    ? `${controlPresentation.label} until ${untilText}`
    : controlPresentation.label;

  const runUpdate = async (updates: Omit<AgentControlUpdate, "scope">) => {
    setLoading(true);
    const actionLabel =
      updates.disabled === false
        ? "re-enable"
        : updates.disabled
          ? "update"
          : "change";

    try {
      const payload: AgentControlUpdate = {
        scope,
        ...updates,
      };

      if (targetSlug) {
        payload.target_slug = targetSlug;
      }

      const result = await agentControlService.updateControl(agentId, payload);

      onToggle?.(result);
    } catch (error: any) {
      console.error("Failed to toggle agent:", error);
      let rawMessage =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message ||
        "Please try again.";
      if (typeof rawMessage === "object") {
        try {
          rawMessage = JSON.stringify(rawMessage);
        } catch {
          rawMessage = "[object Object]";
        }
      }
      alert(`Failed to ${actionLabel} agent. ${rawMessage}`);
    } finally {
      setLoading(false);
    }
  };

  const handleBreak = async (seconds: number) => {
    const labels: Record<number, string> = {
      30: "Taking a short break.",
      300: "Taking a 5 minute break.",
      900: "Taking a 15 minute break.",
    };
    await runUpdate({
      disabled: true,
      disabled_until: new Date(Date.now() + seconds * 1000).toISOString(),
      reason: labels[seconds] || "Temporarily disabled by owner.",
    });
  };

  const handleDisable = async () => {
    await runUpdate({
      disabled: true,
      disabled_until: null,
      reason:
        disableReasonOverride ||
        (scope === "global"
          ? "Disabled globally by administrator."
          : "Disabled until re-enabled."),
    });
  };

  const handleEnable = async () => {
    await runUpdate({
      disabled: false,
      disabled_until: null,
      reason: null,
    });
  };

  const getSizeClasses = () => {
    switch (size) {
      case "sm":
        return "h-8 px-2 text-xs";
      case "lg":
        return "h-12 px-6 text-base";
      default:
        return "h-10 px-4 text-sm";
    }
  };

  const toneClasses =
    controlPresentation.mode === "disable_indefinite"
      ? "bg-gray-600 hover:bg-gray-700 text-white"
      : controlPresentation.mode === "disable_temporary"
        ? "bg-amber-500 hover:bg-amber-600 text-white"
        : controlPresentation.mode === "no_reply"
          ? "bg-cyan-600 hover:bg-cyan-700 text-white"
          : "bg-slate-700 hover:bg-slate-800 text-white";

  const TriggerIcon =
    controlPresentation.mode === "disable_temporary"
      ? PauseCircle
      : controlPresentation.mode === "no_reply"
        ? Clock3
        : controlPresentation.mode === "disable_indefinite"
          ? Power
          : PowerOff;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          disabled={loading}
          className={`${getSizeClasses()} ${toneClasses} transition-all duration-200`}
          title={`${agentName}: ${statusHint}`}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
        >
          {loading ? (
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
          ) : (
            <>
              <TriggerIcon className="h-4 w-4" />
              {showLabel && (
                <span className="ml-2">{controlPresentation.label}</span>
              )}
            </>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>Agent Control</DropdownMenuLabel>
        <DropdownMenuItem disabled className="opacity-70">
          {statusHint}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={loading}
          onSelect={() => void handleBreak(30)}
        >
          Break 30s
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={loading}
          onSelect={() => void handleBreak(300)}
        >
          Break 5m
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={loading}
          onSelect={() => void handleBreak(900)}
        >
          Break 15m
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={loading}
          onSelect={() => void handleDisable()}
        >
          Disable Until Re-enabled
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={loading || !controlPresentation.canReenable}
          onSelect={() => void handleEnable()}
        >
          <Play className="mr-2 h-4 w-4" />
          Re-enable
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
