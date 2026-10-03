import { cn } from "@/lib/utils";

export type RoutedAgentPlaceholderProfile = {
  handle: string;
  emoji?: string | null;
};

const MAX_LISTED_AGENTS = 3;

export function ComposerRoutingPlaceholder({
  routedAgents,
  conciergeName,
  isDarkMode,
}: {
  routedAgents: RoutedAgentPlaceholderProfile[];
  conciergeName: string;
  isDarkMode: boolean;
}) {
  const hasRoute = routedAgents.length > 0;

  return (
    <div
      data-testid="ax-composer-placeholder"
      aria-hidden="true"
      className={cn(
        "pointer-events-none absolute left-4 top-3 flex max-w-[calc(100%-100px)] items-baseline gap-1.5 truncate text-[15px]",
        isDarkMode ? "text-slate-500" : "text-slate-400",
      )}
    >
      <span>{hasRoute ? "Message" : "Write to"}</span>
      <span
        data-testid="ax-composer-placeholder-agent"
        className={cn(
          "inline-flex items-baseline gap-1 font-medium",
          isDarkMode ? "text-cyan-200/80" : "text-cyan-700/90",
        )}
      >
        {!hasRoute ? (
          <>
            <span>this space</span>
          </>
        ) : routedAgents.length > MAX_LISTED_AGENTS ? (
          <span>{routedAgents.length} agents</span>
        ) : (
          routedAgents.map((agent) => (
            <span
              key={agent.handle}
              className="inline-flex items-baseline gap-1"
            >
              {agent.emoji ? <span>{agent.emoji}</span> : null}
              <span>@{agent.handle}</span>
            </span>
          ))
        )}
      </span>
      <span className="truncate">
        {hasRoute ? "— @ to override" : "— @ to message an agent"}
      </span>
    </div>
  );
}
