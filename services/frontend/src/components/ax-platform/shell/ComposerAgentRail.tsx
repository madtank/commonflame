import { Check, PinOff } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  AVAILABILITY_META,
  type AvailabilityKey,
} from "@/lib/agent-availability";

export type ComposerAgentRailChip = {
  handle: string;
  name?: string | null;
  emoji?: string | null;
  selected: boolean;
  availability?: AvailabilityKey;
};

/**
 * Pinned multi-select agent rail above the composer. Chips toggle agents
 * in/out of the sticky recipient set — checkbox semantics, not a switcher.
 */
export function ComposerAgentRail({
  agents,
  pinned,
  onToggleAgent,
  onTogglePinned,
  isDarkMode,
}: {
  agents: ComposerAgentRailChip[];
  pinned: boolean;
  onToggleAgent: (handle: string) => void;
  onTogglePinned: () => void;
  isDarkMode: boolean;
}) {
  if (!pinned) return null;

  return (
    <div
      data-testid="ax-agent-rail"
      className="mb-2 flex items-center gap-1.5 overflow-x-auto"
    >
      {agents.map((agent) => (
        <button
          key={agent.handle}
          type="button"
          aria-pressed={agent.selected}
          data-testid={`ax-rail-chip-${agent.handle}`}
          onClick={() => onToggleAgent(agent.handle)}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition",
            agent.selected
              ? isDarkMode
                ? "border-cyan-300/40 bg-cyan-400/10 text-cyan-50"
                : "border-cyan-300 bg-cyan-50 text-cyan-900"
              : isDarkMode
                ? "border-white/[0.08] bg-white/[0.02] text-slate-300 hover:border-white/20"
                : "border-slate-200 bg-white text-slate-600 hover:border-slate-300",
          )}
        >
          {agent.emoji ? <span>{agent.emoji}</span> : null}
          <span className="font-medium">@{agent.handle}</span>
          {agent.availability ? (
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full"
              style={{
                backgroundColor: AVAILABILITY_META[agent.availability].dot,
              }}
            />
          ) : null}
          {agent.selected ? <Check className="h-3 w-3" /> : null}
        </button>
      ))}
      <button
        type="button"
        aria-label="Unpin agent selector"
        title="Unpin agent selector"
        onClick={onTogglePinned}
        className={cn(
          "ml-auto shrink-0 rounded-full p-1.5 transition",
          isDarkMode
            ? "text-slate-500 hover:bg-white/[0.08] hover:text-slate-300"
            : "text-slate-400 hover:bg-slate-100 hover:text-slate-600",
        )}
      >
        <PinOff className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
