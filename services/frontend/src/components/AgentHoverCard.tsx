import React, { useState, useRef, useEffect } from "react";
import type { Agent } from "@/types/agent";
import { humanizeAgentType } from "@/lib/display-utils";

interface AgentHoverCardProps {
  agent: Partial<Agent> & { username: string; color?: string };
  children: React.ReactNode;
}

/**
 * Lightweight hover card for agent pills.
 * Shows agent details (bio, status, model, capabilities) on hover.
 * No external deps — pure CSS positioning + React state.
 */
export function AgentHoverCard({ agent, children }: AgentHoverCardProps) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<"above" | "below">("above");
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const show = () => {
    if (timeout.current) clearTimeout(timeout.current);
    timeout.current = setTimeout(() => {
      // Determine if card should render above or below
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        setPosition(rect.top > 300 ? "above" : "below");
      }
      setOpen(true);
    }, 300);
  };

  const hide = () => {
    if (timeout.current) clearTimeout(timeout.current);
    timeout.current = setTimeout(() => setOpen(false), 150);
  };

  useEffect(
    () => () => {
      if (timeout.current) clearTimeout(timeout.current);
    },
    [],
  );

  const statusColor =
    agent.status === "active"
      ? "bg-green-500"
      : agent.status === "busy"
        ? "bg-yellow-500"
        : agent.status === "idle"
          ? "bg-yellow-400"
          : "bg-gray-400";

  const statusLabel = agent.status
    ? agent.status.charAt(0).toUpperCase() + agent.status.slice(1)
    : "Unknown";

  // Derive last seen
  const lastSeen = (() => {
    const ts = agent.last_seen || agent.last_activity;
    if (!ts) return null;
    const diff = Date.now() - new Date(ts).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "Just now";
    if (mins < 60) return `${mins}m ago`;
    if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
    return `${Math.floor(mins / 1440)}d ago`;
  })();

  // Derive capability list from tools_enabled or capabilities
  const tools = agent.tools_enabled?.length
    ? agent.tools_enabled
    : agent.capabilities
      ? Object.entries(agent.capabilities)
          .filter(([, v]) => v === true)
          .map(([k]) => k.replace(/_/g, " "))
      : [];

  return (
    <div
      ref={containerRef}
      className="relative inline-flex"
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {open && (
        <div
          className={`absolute z-[100] w-64 p-3 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-left animate-in fade-in-0 zoom-in-95 duration-150
            ${position === "above" ? "bottom-full mb-2" : "top-full mt-2"} left-0`}
          onMouseEnter={show}
          onMouseLeave={hide}
        >
          {/* Header */}
          <div className="flex items-center gap-2 mb-2">
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold flex-shrink-0"
              style={{
                backgroundColor:
                  agent.color || (agent.avatar_url ? undefined : "#6366f1"),
              }}
            >
              {agent.avatar_url ? (
                <img
                  src={agent.avatar_url}
                  alt=""
                  className="w-8 h-8 rounded-full object-cover"
                />
              ) : (
                agent.username.charAt(0).toUpperCase()
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="font-semibold text-sm text-gray-900 dark:text-gray-100 truncate">
                  @{agent.username}
                </span>
                <span
                  className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`}
                  title={statusLabel}
                />
              </div>
              {agent.agent_type && (
                <span className="text-[10px] text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                  {humanizeAgentType(agent.agent_type)}
                </span>
              )}
            </div>
          </div>

          {/* Bio */}
          {agent.bio && (
            <p className="text-xs text-gray-600 dark:text-gray-300 mb-2 line-clamp-2">
              {agent.bio}
            </p>
          )}

          {/* Details grid */}
          <div className="space-y-1.5 text-[11px]">
            {lastSeen && (
              <div className="flex items-center justify-between">
                <span className="text-gray-500 dark:text-gray-400">
                  Last seen
                </span>
                <span className="text-gray-700 dark:text-gray-300 font-medium">
                  {lastSeen}
                </span>
              </div>
            )}
            {agent.model && (
              <div className="flex items-center justify-between">
                <span className="text-gray-500 dark:text-gray-400">Model</span>
                <span className="text-gray-700 dark:text-gray-300 font-medium truncate ml-2 max-w-[140px]">
                  {agent.model}
                </span>
              </div>
            )}
            {(agent.posts_count ?? agent.post_count) != null && (
              <div className="flex items-center justify-between">
                <span className="text-gray-500 dark:text-gray-400">
                  Messages
                </span>
                <span className="text-gray-700 dark:text-gray-300 font-medium">
                  {agent.posts_count ?? agent.post_count}
                </span>
              </div>
            )}
            {agent.team_name && (
              <div className="flex items-center justify-between">
                <span className="text-gray-500 dark:text-gray-400">Team</span>
                <span
                  className="px-1.5 py-0.5 rounded text-[10px] font-medium text-white"
                  style={{ backgroundColor: agent.team_color || "#6b7280" }}
                >
                  {agent.team_name}
                </span>
              </div>
            )}
          </div>

          {/* Tools/Capabilities */}
          {tools.length > 0 && (
            <div className="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700">
              <span className="text-[10px] text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                Capabilities
              </span>
              <div className="flex flex-wrap gap-1 mt-1">
                {tools.slice(0, 6).map((tool) => (
                  <span
                    key={tool}
                    className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-[10px] text-gray-600 dark:text-gray-300"
                  >
                    {tool}
                  </span>
                ))}
                {tools.length > 6 && (
                  <span className="px-1.5 py-0.5 text-[10px] text-gray-500">
                    +{tools.length - 6} more
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
