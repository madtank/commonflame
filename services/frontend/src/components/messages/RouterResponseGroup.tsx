/**
 * RouterResponseGroup — renders the full router response:
 * router message, per-agent activity cards, and final summary.
 *
 * Consumes RouterStreamState from useRouterStream.
 */

import { memo } from "react";
import type { RouterStreamState } from "@/hooks/useRouterStream";
import { AgentActivityCard } from "./AgentActivityCard";

interface RouterResponseGroupProps {
  state: RouterStreamState;
}

export const RouterResponseGroup = memo(function RouterResponseGroup({
  state,
}: RouterResponseGroupProps) {
  if (state.phase === "idle") return null;

  const agentCards = Array.from(state.agents.values());
  const showWaitingState =
    (state.phase === "routing" || state.phase === "streaming") &&
    !state.routerMessage &&
    agentCards.length === 0;

  return (
    <div className="flex w-full min-w-0 max-w-full flex-col gap-3 px-2 py-2.5 sm:px-4">
      {showWaitingState && (
        <div className="ml-1 text-sm text-cyan-100/80">
          Waiting for first stream event…
        </div>
      )}

      {/* Router's initial message */}
      {state.routerMessage && (
        <div className="flex min-w-0 items-start gap-3 rounded-2xl border border-cyan-500/15 bg-slate-950 px-3.5 py-3 shadow-sm">
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-cyan-500 flex items-center justify-center text-white text-xs font-bold">
            R
          </div>
          <div className="flex-1 text-sm text-foreground/80">
            {state.routerMessage}
          </div>
        </div>
      )}

      {/* Agent activity cards */}
      {agentCards.length > 0 && (
        <div className="ml-0 flex min-w-0 flex-col gap-2 sm:ml-11">
          {agentCards.map((card) => (
            <AgentActivityCard key={card.agent_id} card={card} />
          ))}
        </div>
      )}

      {/* Router summary (phase: done) */}
      {state.phase === "done" && state.routerSummary && (
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-cyan-500 flex items-center justify-center text-white text-xs font-bold">
            R
          </div>
          <div className="flex-1 text-sm text-foreground/70 italic">
            {state.routerSummary}
          </div>
        </div>
      )}

      {/* Error state */}
      {state.phase === "error" && (
        <div className="ml-0 rounded-xl border border-rose-400/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200 sm:ml-11">
          <div>Router encountered an error.</div>
          {state.errorText ? (
            <div className="mt-2 whitespace-pre-wrap break-words text-xs text-rose-100/85">
              {state.errorText}
            </div>
          ) : (
            <div className="mt-2 text-xs text-rose-100/85">
              Please try again.
            </div>
          )}
        </div>
      )}
    </div>
  );
});
