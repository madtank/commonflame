/**
 * StreamingBubble — placeholder message bubble for in-flight streaming messages.
 *
 * Shows the incrementally-arriving content with a typing cursor animation.
 * Swaps out once the persisted message arrives (handled by parent).
 */

import { memo } from "react";
import type { StreamingMessage } from "@/hooks/useMessageStream";
import { ReasoningContent } from "./MessageContent";

interface StreamingBubbleProps {
  stream: StreamingMessage;
  agentName?: string;
  onHashtagClick?: (hashtag: string) => void;
  onAgentClick?: (agent: string) => void;
}

export const StreamingBubble = memo(function StreamingBubble({
  stream,
  agentName,
  onHashtagClick,
  onAgentClick,
}: StreamingBubbleProps) {
  const isError = stream.status === "error";
  const isStreaming = stream.status === "streaming";

  return (
    <div
      className={`w-full min-w-0 max-w-full px-2 py-2.5 select-text sm:px-4 ${isError ? "opacity-70" : ""}`}
      data-streaming-message-id={stream.message_id}
    >
      <div className="flex min-w-0 max-w-full items-start gap-3 rounded-2xl border border-blue-200/70 bg-blue-50/70 px-3.5 py-3 shadow-sm ring-1 ring-blue-500/5 dark:border-blue-900/60 dark:bg-blue-950/20">
        {/* Avatar placeholder */}
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-purple-600 text-xs font-medium text-white select-none">
          {agentName?.[0]?.toUpperCase() ?? "A"}
        </div>

        <div className="flex-1 min-w-0">
          {/* Agent name */}
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-medium text-foreground/80">
              {agentName ?? "Agent"}
            </span>
            {isStreaming && (
              <span className="text-xs text-muted-foreground animate-pulse select-none">
                typing...
              </span>
            )}
            {isError && (
              <span className="text-xs text-destructive select-none">
                error
              </span>
            )}
          </div>

          {/* Content */}
          <div className="text-sm text-foreground break-words select-text">
            <ReasoningContent
              text={stream.content}
              onHashtagClick={onHashtagClick}
              onAgentClick={onAgentClick}
              collapseByDefault={false}
              autoCollapse={!isStreaming}
              sanitizeSegments={true}
              showReasoningPanel={false}
            />
            {isStreaming && (
              <span className="inline-block w-[2px] h-[1em] bg-foreground/60 ml-[1px] animate-blink align-text-bottom" />
            )}
          </div>

          {/* Error details */}
          {isError && stream.errorText && (
            <div className="mt-1 text-xs text-destructive/80 italic select-text">
              {stream.errorText}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
