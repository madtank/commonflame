/**
 * AgentActivityCard — displays a single agent's streamed response
 * within a router-multiplexed conversation.
 *
 * Shows: role label, streaming content with cursor, completion state,
 * routing hint, and optional token usage.
 */

import { memo } from "react";
import type { AgentCardState } from "@/hooks/useRouterStream";

// Role → emoji mapping for visual distinction
const ROLE_EMOJI: Record<string, string> = {
  Frontend: "🎨",
  Backend: "⚙️",
  Research: "📚",
  Design: "🖌️",
  Data: "📊",
  Security: "🔒",
  DevOps: "🚀",
};

function getRoleEmoji(role: string): string {
  return ROLE_EMOJI[role] ?? "🤖";
}

interface AgentActivityCardProps {
  card: AgentCardState;
}

export const AgentActivityCard = memo(function AgentActivityCard({
  card,
}: AgentActivityCardProps) {
  const emoji = getRoleEmoji(card.role);
  const isPending = card.status === "pending";
  const isStreaming = card.status === "streaming";
  const isDone = card.status === "done";
  const isError = card.status === "error";

  return (
    <div
      className={`min-w-0 rounded-xl border px-3.5 py-3 transition-colors sm:px-4 ${
        isError
          ? "border-destructive/30 bg-destructive/5"
          : isDone
            ? "border-border/50 bg-muted/30"
            : "border-border bg-card"
      }`}
      data-agent-card={card.agent_id}
    >
      {/* Header: role + status */}
      <div className="mb-2 flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-base">{emoji}</span>
          <span className="min-w-0 break-words text-sm font-semibold text-foreground">
            {card.role}
          </span>
          {card.reason && (
            <span className="min-w-0 break-words text-xs text-muted-foreground">
              — {card.reason}
            </span>
          )}
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          {isPending && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-500 animate-pulse" />
              waiting
            </span>
          )}
          {isStreaming && (
            <span className="flex items-center gap-1 text-xs text-blue-500">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
              streaming
            </span>
          )}
          {isDone && (
            <span className="text-xs text-green-600 dark:text-green-400 font-medium">
              ✓ done
            </span>
          )}
          {isError && (
            <span className="text-xs text-destructive font-medium">
              ✗ error
            </span>
          )}
        </div>
      </div>

      {/* Content area */}
      {(card.content || isPending) && (
        <div className="min-w-0 text-sm text-foreground whitespace-pre-wrap break-words">
          {isPending && !card.content && (
            <span className="text-muted-foreground italic">
              Waiting for response...
            </span>
          )}
          {card.content}
          {isStreaming && (
            <span className="inline-block w-[2px] h-[1em] bg-foreground/60 ml-[1px] animate-blink align-text-bottom" />
          )}
        </div>
      )}

      {/* Summary (shown on completion) */}
      {isDone && card.summary && (
        <div className="mt-2 pt-2 border-t border-border/50 text-xs text-muted-foreground">
          {card.summary}
        </div>
      )}

      {/* Error text */}
      {isError && card.errorText && (
        <div className="mt-2 text-xs text-destructive/80 italic">
          {card.errorText}
        </div>
      )}

      {/* Token usage (shown on completion) */}
      {isDone && card.usage && (
        <div className="mt-1 text-[11px] text-muted-foreground/60">
          {card.usage.prompt_tokens + card.usage.completion_tokens} tokens
        </div>
      )}
    </div>
  );
});
