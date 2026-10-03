/**
 * Pure logic for the enhanced status indicator in message headers.
 * Maps statusLabel + toolName → display config (icon, color, animation, text).
 */

import {
  ACTIVITY_STREAM_STATUS_ATTENTION_CLASSNAME,
  ACTIVITY_STREAM_STATUS_DONE_CLASSNAME,
  ACTIVITY_STREAM_STATUS_ERROR_CLASSNAME,
  ACTIVITY_STREAM_STATUS_MUTED_CLASSNAME,
  ACTIVITY_STREAM_STATUS_PENDING_CLASSNAME,
  ACTIVITY_STREAM_STATUS_WORKING_CLASSNAME,
} from "@/components/ax-platform/activity-stream-tokens";

export type StatusDisplayConfig = {
  text: string;
  icon:
    | "sparkles"
    | "wrench"
    | "radio"
    | "clock"
    | "send"
    | "check"
    | "activity";
  colorClass: string;
  animation: "pulse" | "spin" | null;
};

const COMPLETED_STATUSES = new Set([
  "completed",
  "complete",
  "done",
  "finished",
  "idle",
  "delivered",
  "stopped",
  "inactive",
]);

function formatToolLabel(toolName: string): string {
  return toolName
    .trim()
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ");
}

export function getStatusDisplay(
  statusLabel: string | null | undefined,
  toolName: string | null | undefined,
): StatusDisplayConfig | null {
  if (!statusLabel) return null;

  const status = statusLabel.toLowerCase();

  // Completed / done
  if (COMPLETED_STATUSES.has(status)) {
    return {
      text: "Done",
      icon: "check",
      colorClass: ACTIVITY_STREAM_STATUS_DONE_CLASSNAME,
      animation: null,
    };
  }

  // Thinking
  if (status === "thinking") {
    return {
      text: "Thinking...",
      icon: "sparkles",
      colorClass: ACTIVITY_STREAM_STATUS_ATTENTION_CLASSNAME,
      animation: "pulse",
    };
  }

  // Tool use
  if (status === "tool_use" || status === "tool_call") {
    const formattedToolName = toolName ? formatToolLabel(toolName) : null;
    return {
      text: formattedToolName
        ? `Calling ${formattedToolName}...`
        : "Using tool...",
      icon: "wrench",
      colorClass: ACTIVITY_STREAM_STATUS_WORKING_CLASSNAME,
      animation: "spin",
    };
  }

  // Streaming
  if (status === "streaming") {
    return {
      text: "Streaming...",
      icon: "radio",
      colorClass: ACTIVITY_STREAM_STATUS_DONE_CLASSNAME,
      animation: "pulse",
    };
  }

  // Queued
  if (status === "queued" || status === "queued locally") {
    return {
      text: "Queued",
      icon: "clock",
      colorClass: ACTIVITY_STREAM_STATUS_PENDING_CLASSNAME,
      animation: null,
    };
  }

  // Sending
  if (status === "sending") {
    return {
      text: "Sending",
      icon: "send",
      colorClass: ACTIVITY_STREAM_STATUS_MUTED_CLASSNAME,
      animation: null,
    };
  }

  // Started
  if (status === "started") {
    return {
      text: "Starting...",
      icon: "sparkles",
      colorClass: ACTIVITY_STREAM_STATUS_ATTENTION_CLASSNAME,
      animation: "pulse",
    };
  }

  // Composer delivery feedback (see composer-recipients.describeDeliveryOutcome)
  if (status.startsWith("delivered")) {
    return {
      text: statusLabel,
      icon: "check",
      colorClass: ACTIVITY_STREAM_STATUS_DONE_CLASSNAME,
      animation: null,
    };
  }
  if (
    status.startsWith("send failed") ||
    status.startsWith("send not confirmed")
  ) {
    return {
      text: statusLabel,
      icon: "activity",
      colorClass: ACTIVITY_STREAM_STATUS_ERROR_CLASSNAME,
      animation: null,
    };
  }

  // Unknown / fallback — show raw status
  return {
    text: statusLabel,
    icon: "activity",
    colorClass: ACTIVITY_STREAM_STATUS_MUTED_CLASSNAME,
    animation: null,
  };
}
