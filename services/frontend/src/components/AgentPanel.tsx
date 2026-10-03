/**
 * AgentPanel.tsx
 *
 * Customizable "Favorite Agents" sidebar panel. Evolved from CloudAgentPanel.
 *
 * - Shows user's curated list of favorite agents (any type: cloud, webhook, MCP)
 * - Defaults to cloud agents when user has no favorites yet
 * - Search bar to find and add any agent from the workspace roster
 * - Drag-to-reorder, star to favorite, click to @mention
 */
import type React from "react";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";
import {
  Bot,
  X,
  Plus,
  Cloud,
  Webhook,
  Plug,
  MessageSquare,
  Loader2,
  GripVertical,
  Star,
  StarOff,
  Search,
  UserPlus,
} from "lucide-react";
import { FOLLOW_UUID } from "@/lib/agent-mobility";

/** Unified agent shape for the panel */
export interface PanelAgent {
  id?: string;
  username: string;
  bio?: string;
  agentType?: "cloud" | "webhook" | "mcp" | "general";
  enable_cloud_agent?: boolean;
  cloud_function_url?: string | null;
  org_id?: string | null;
  space_id?: string | null;
  workspace_id?: string | null;
  pinned_org_id?: string | null;
  pinned_to_org?: string | null;
  follow_user?: boolean;
  settings?: {
    follow_user?: boolean;
  } | null;
  is_global?: boolean;
  metadata?: {
    org_id?: string | null;
    pinned_org_id?: string | null;
    pinned_to_org?: string | null;
    space_id?: string | null;
    workspace_id?: string | null;
    follow_user?: boolean;
    is_global?: boolean;
  } | null;
}

export interface AgentPanelProps {
  /** Cloud agents (shown by default when no favorites) */
  cloudAgents: PanelAgent[];
  /** Full workspace roster for search/add */
  allAgents?: PanelAgent[];
  currentOrgId?: string | null;
  onMentionAgent: (username: string) => void;
  onNavigateToRegister: () => void;
  isCollapsed?: boolean;
  isLoading?: boolean;
}

function normalizeSpaceId(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const normalized = String(value).trim();
  return normalized.length > 0 ? normalized : null;
}

function isFollowModeAgent(agent: PanelAgent): boolean {
  const pinnedSpaceId =
    normalizeSpaceId(agent.pinned_org_id) ||
    normalizeSpaceId(agent.pinned_to_org) ||
    normalizeSpaceId(agent.metadata?.pinned_org_id) ||
    normalizeSpaceId(agent.metadata?.pinned_to_org);
  const orgSpaceId =
    normalizeSpaceId(agent.org_id) || normalizeSpaceId(agent.metadata?.org_id);

  return (
    pinnedSpaceId === FOLLOW_UUID ||
    orgSpaceId === FOLLOW_UUID ||
    agent.follow_user === true ||
    agent.settings?.follow_user === true ||
    agent.metadata?.follow_user === true
  );
}

export function getPanelAgentSpaceId(agent: PanelAgent): string | null {
  return (
    normalizeSpaceId(agent.pinned_org_id) ||
    normalizeSpaceId(agent.pinned_to_org) ||
    normalizeSpaceId(agent.metadata?.pinned_org_id) ||
    normalizeSpaceId(agent.metadata?.pinned_to_org) ||
    normalizeSpaceId(agent.org_id) ||
    normalizeSpaceId(agent.space_id) ||
    normalizeSpaceId(agent.workspace_id) ||
    normalizeSpaceId(agent.metadata?.org_id) ||
    normalizeSpaceId(agent.metadata?.space_id) ||
    normalizeSpaceId(agent.metadata?.workspace_id)
  );
}

export function isPanelAgentVisibleInSpace(
  agent: PanelAgent,
  currentOrgId?: string | null,
): boolean {
  const normalizedCurrentOrgId = normalizeSpaceId(currentOrgId);
  if (!normalizedCurrentOrgId) return true;
  if (agent.is_global || agent.metadata?.is_global) return true;
  if (isFollowModeAgent(agent)) return true;

  const agentSpaceId = getPanelAgentSpaceId(agent);
  return !agentSpaceId || agentSpaceId === normalizedCurrentOrgId;
}

// Storage keys (same as before for migration compat)
const AGENT_ORDER_KEY = "ax:agent-panel-order";
const AGENT_FAVORITES_KEY = "ax:agent-panel-favorites";

function loadOrder(): string[] {
  try {
    return JSON.parse(localStorage.getItem(AGENT_ORDER_KEY) || "[]");
  } catch {
    return [];
  }
}
function saveOrder(order: string[]) {
  localStorage.setItem(AGENT_ORDER_KEY, JSON.stringify(order));
}
function loadFavorites(): Set<string> {
  try {
    return new Set(
      JSON.parse(localStorage.getItem(AGENT_FAVORITES_KEY) || "[]"),
    );
  } catch {
    return new Set();
  }
}
function saveFavorites(favs: Set<string>) {
  localStorage.setItem(AGENT_FAVORITES_KEY, JSON.stringify([...favs]));
}

/** Resolve agent type from raw agent data */
function resolveAgentType(
  agent: PanelAgent,
): "cloud" | "webhook" | "mcp" | "general" {
  if (agent.agentType) return agent.agentType;
  if (agent.enable_cloud_agent || agent.cloud_function_url) return "cloud";
  return "general";
}

/** Badge component for agent type */
function AgentTypeBadge({
  type,
}: {
  type: ReturnType<typeof resolveAgentType>;
}) {
  switch (type) {
    case "cloud":
      return (
        <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30 px-1.5 py-0.5 rounded-full">
          <Cloud className="h-2.5 w-2.5" />
          Cloud
        </span>
      );
    case "webhook":
      return (
        <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/30 px-1.5 py-0.5 rounded-full">
          <Webhook className="h-2.5 w-2.5" />
          Webhook
        </span>
      );
    case "mcp":
      return (
        <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/30 px-1.5 py-0.5 rounded-full">
          <Plug className="h-2.5 w-2.5" />
          MCP
        </span>
      );
    default:
      return null;
  }
}

export function AgentPanel({
  cloudAgents,
  allAgents = [],
  currentOrgId,
  onMentionAgent,
  onNavigateToRegister,
  isCollapsed = false,
  isLoading = false,
}: AgentPanelProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [favorites, setFavorites] = useState<Set<string>>(loadFavorites);
  const [customOrder, setCustomOrder] = useState<string[]>(loadOrder);
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const dragItemRef = useRef<number | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Listen for open event from HelpDialog (backward compat)
  useEffect(() => {
    const handleOpen = () => setIsExpanded(true);
    window.addEventListener("ax:open-cloud-agents", handleOpen);
    return () => window.removeEventListener("ax:open-cloud-agents", handleOpen);
  }, []);

  // Focus search input when entering search mode
  useEffect(() => {
    if (isSearching && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [isSearching]);

  const scopedCloudAgents = useMemo(
    () =>
      cloudAgents.filter((agent) =>
        isPanelAgentVisibleInSpace(agent, currentOrgId),
      ),
    [cloudAgents, currentOrgId],
  );

  const scopedAllAgents = useMemo(
    () =>
      allAgents.filter((agent) =>
        isPanelAgentVisibleInSpace(agent, currentOrgId),
      ),
    [allAgents, currentOrgId],
  );

  // The "display" agents: user's favorites from the current-space roster, or cloud agents as default
  const hasFavorites = favorites.size > 0;

  const displayAgents = useMemo(() => {
    if (hasFavorites) {
      // Merge: show favorited agents from current-space allAgents + cloudAgents only.
      // Favorites are localStorage-backed and can outlive an agent being moved to
      // another space, so do not let stale favorites punch through scoping.
      const agentMap = new Map<string, PanelAgent>();
      for (const a of scopedCloudAgents) agentMap.set(a.username, a);
      for (const a of scopedAllAgents) agentMap.set(a.username, a);
      return [...favorites]
        .map((username) => agentMap.get(username))
        .filter((a): a is PanelAgent => !!a);
    }
    // Default: show cloud agents in this space
    return scopedCloudAgents;
  }, [hasFavorites, favorites, scopedCloudAgents, scopedAllAgents]);

  // Sort: favorites first, then custom order, then alphabetical
  const sortedAgents = useMemo(() => {
    return [...displayAgents].sort((a, b) => {
      const aFav = favorites.has(a.username);
      const bFav = favorites.has(b.username);
      if (aFav !== bFav) return aFav ? -1 : 1;
      const aIdx = customOrder.indexOf(a.username);
      const bIdx = customOrder.indexOf(b.username);
      if (aIdx !== -1 && bIdx !== -1) return aIdx - bIdx;
      if (aIdx !== -1) return -1;
      if (bIdx !== -1) return 1;
      return a.username.localeCompare(b.username);
    });
  }, [displayAgents, favorites, customOrder]);

  // Search results: filter all agents not already in display list
  const searchResults = useMemo(() => {
    if (!searchQuery.trim()) return [];
    const q = searchQuery.toLowerCase().replace(/^@/, "");
    const displaySet = new Set(displayAgents.map((a) => a.username));
    return scopedAllAgents
      .filter(
        (a) =>
          !displaySet.has(a.username) &&
          (a.username.toLowerCase().includes(q) ||
            a.bio?.toLowerCase().includes(q)),
      )
      .slice(0, 20);
  }, [searchQuery, scopedAllAgents, displayAgents]);

  const toggleFavorite = useCallback(
    (username: string, e?: React.MouseEvent) => {
      e?.stopPropagation();
      setFavorites((prev) => {
        const next = new Set(prev);
        if (next.has(username)) next.delete(username);
        else next.add(username);
        saveFavorites(next);
        return next;
      });
    },
    [],
  );

  const addToFavorites = useCallback((username: string) => {
    setFavorites((prev) => {
      const next = new Set(prev);
      next.add(username);
      saveFavorites(next);
      return next;
    });
    setSearchQuery("");
    setIsSearching(false);
  }, []);

  const handleDragStart = useCallback((idx: number) => {
    dragItemRef.current = idx;
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent, idx: number) => {
    e.preventDefault();
    setDragOverIdx(idx);
  }, []);

  const handleDrop = useCallback(
    (idx: number) => {
      const fromIdx = dragItemRef.current;
      if (fromIdx === null || fromIdx === idx) {
        setDragOverIdx(null);
        return;
      }
      const reordered = [...sortedAgents.map((a) => a.username)];
      const [moved] = reordered.splice(fromIdx, 1);
      reordered.splice(idx, 0, moved);
      setCustomOrder(reordered);
      saveOrder(reordered);
      setDragOverIdx(null);
      dragItemRef.current = null;
    },
    [sortedAgents],
  );

  const handleDragEnd = useCallback(() => {
    setDragOverIdx(null);
    dragItemRef.current = null;
  }, []);

  const handleMention = (username: string) => {
    onMentionAgent(username);
    setIsExpanded(false);
  };

  const agentCount = hasFavorites
    ? sortedAgents.length
    : scopedCloudAgents.length;

  return (
    <>
      {/* Trigger Button */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className={`group flex items-center px-2 py-2 text-sm font-medium rounded-md w-full transition-colors justify-center lg:justify-start ${
          isExpanded
            ? "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
            : "text-gray-600 hover:bg-gray-50 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white"
        } ${isCollapsed ? "lg:justify-center" : ""}`}
        title={`Agents${agentCount > 0 ? ` (${agentCount})` : ""}`}
      >
        <div
          className={`relative flex-shrink-0 ${!isCollapsed ? "lg:mr-3" : ""}`}
        >
          <Bot
            className={`h-5 w-5 ${
              isExpanded
                ? "text-blue-500 dark:text-blue-300"
                : "text-gray-400 group-hover:text-gray-500"
            }`}
          />
          {agentCount > 0 && (
            <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-green-500 ring-1.5 ring-white dark:ring-gray-800" />
          )}
        </div>
        <span className="hidden lg:inline">
          {!isCollapsed && (
            <>
              Agents
              {agentCount > 0 && (
                <span className="ml-1.5 text-xs text-gray-400 dark:text-gray-500 font-normal">
                  {agentCount}
                </span>
              )}
            </>
          )}
        </span>
      </button>

      {/* Expanded Panel via Portal */}
      {isExpanded &&
        createPortal(
          <div className="fixed inset-0 z-50 flex">
            {/* Backdrop */}
            <div
              className="absolute inset-0 bg-gray-900/50 backdrop-blur-sm"
              onClick={() => setIsExpanded(false)}
            />

            {/* Panel */}
            <div
              className="absolute left-16 lg:left-64 top-0 h-full w-80 max-w-[calc(100vw-4rem)] bg-white dark:bg-gray-800 shadow-2xl border-r border-gray-200 dark:border-gray-700 flex flex-col animate-slide-in-left"
              style={{ animationDuration: "150ms" }}
            >
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
                <div className="flex items-center gap-2">
                  <Bot className="h-5 w-5 text-purple-500" />
                  <h2 className="text-base font-semibold text-gray-900 dark:text-white">
                    {hasFavorites ? "My Agents" : "Agents"}
                  </h2>
                  {hasFavorites && (
                    <span className="text-[10px] text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded-full">
                      {favorites.size} saved
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setIsSearching(!isSearching);
                      if (isSearching) setSearchQuery("");
                    }}
                    className="p-2"
                    aria-label={isSearching ? "Close search" : "Add agent"}
                    title="Search & add agents"
                  >
                    {isSearching ? (
                      <X className="h-4 w-4" />
                    ) : (
                      <UserPlus className="h-4 w-4" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setIsExpanded(false)}
                    className="p-2"
                    aria-label="Close panel"
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {/* Search Bar (when active) */}
              {isSearching && (
                <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                    <input
                      ref={searchInputRef}
                      type="text"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      placeholder="Search agents to add..."
                      className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                    />
                  </div>
                  {/* Search Results */}
                  {searchQuery.trim() && (
                    <div className="mt-2 max-h-48 overflow-y-auto space-y-1">
                      {searchResults.length > 0 ? (
                        searchResults.map((agent) => {
                          const type = resolveAgentType(agent);
                          return (
                            <button
                              key={agent.username}
                              onClick={() => addToFavorites(agent.username)}
                              className="w-full flex items-center gap-2 p-2 rounded-lg text-left hover:bg-purple-50 dark:hover:bg-purple-900/20 transition-colors"
                            >
                              <div className="flex-shrink-0 w-7 h-7 rounded-full bg-gradient-to-br from-gray-300 to-gray-400 dark:from-gray-600 dark:to-gray-500 flex items-center justify-center text-white text-xs font-medium">
                                {agent.username.charAt(0).toUpperCase()}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-1.5">
                                  <span className="text-sm font-medium text-gray-900 dark:text-white truncate">
                                    @{agent.username}
                                  </span>
                                  <AgentTypeBadge type={type} />
                                </div>
                                {agent.bio && (
                                  <p className="text-[11px] text-gray-500 dark:text-gray-400 truncate">
                                    {agent.bio}
                                  </p>
                                )}
                              </div>
                              <Plus className="h-4 w-4 text-purple-500 flex-shrink-0" />
                            </button>
                          );
                        })
                      ) : (
                        <p className="text-xs text-gray-500 dark:text-gray-400 text-center py-2">
                          No matching agents found
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Agent List */}
              <div className="flex-1 overflow-y-auto p-4">
                {isLoading ? (
                  <div className="flex flex-col items-center justify-center h-full text-center px-4">
                    <div className="w-16 h-16 rounded-full bg-purple-100 dark:bg-purple-900/30 flex items-center justify-center mb-4">
                      <Loader2 className="h-8 w-8 text-purple-500 animate-spin" />
                    </div>
                    <h3 className="text-sm font-medium text-gray-900 dark:text-white mb-1">
                      Loading agents...
                    </h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      Finding available agents in this space
                    </p>
                    <div className="w-full mt-6 space-y-2">
                      {[1, 2, 3].map((i) => (
                        <div
                          key={i}
                          className="w-full p-3 rounded-lg border border-gray-200 dark:border-gray-700 animate-pulse"
                        >
                          <div className="flex items-start gap-3">
                            <div className="flex-shrink-0 w-10 h-10 rounded-full bg-gray-200 dark:bg-gray-700" />
                            <div className="flex-1 space-y-2">
                              <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-24" />
                              <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-full" />
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : sortedAgents.length > 0 ? (
                  <div className="space-y-2">
                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                      {hasFavorites
                        ? "Your saved agents · Drag to reorder · Click to mention"
                        : "Cloud agents · ★ to save · Click to mention"}
                    </p>
                    {sortedAgents.map((agent, idx) => {
                      const isFav = favorites.has(agent.username);
                      const isDragOver = dragOverIdx === idx;
                      const type = resolveAgentType(agent);
                      return (
                        <div
                          key={agent.username}
                          draggable
                          onDragStart={() => handleDragStart(idx)}
                          onDragOver={(e) => handleDragOver(e, idx)}
                          onDrop={() => handleDrop(idx)}
                          onDragEnd={handleDragEnd}
                          className={`w-full p-3 rounded-lg border transition-all text-left group cursor-grab active:cursor-grabbing ${
                            isDragOver
                              ? "border-purple-400 bg-purple-50 dark:bg-purple-900/30 scale-[1.02]"
                              : isFav
                                ? "border-yellow-200 dark:border-yellow-800 bg-yellow-50/30 dark:bg-yellow-900/10 hover:border-purple-300 dark:hover:border-purple-600"
                                : "border-gray-200 dark:border-gray-700 hover:border-purple-300 dark:hover:border-purple-600 hover:bg-purple-50 dark:hover:bg-purple-900/20"
                          }`}
                        >
                          <div className="flex items-start gap-2">
                            <div className="flex-shrink-0 pt-2.5 text-gray-300 dark:text-gray-600 opacity-60 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                              <GripVertical className="h-4 w-4" />
                            </div>
                            <button
                              onClick={() => handleMention(agent.username)}
                              className="flex items-start gap-3 flex-1 min-w-0 text-left"
                            >
                              <div
                                className={`flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white font-medium ${
                                  type === "cloud"
                                    ? "bg-gradient-to-br from-purple-400 to-blue-500"
                                    : type === "webhook"
                                      ? "bg-gradient-to-br from-green-400 to-emerald-500"
                                      : type === "mcp"
                                        ? "bg-gradient-to-br from-orange-400 to-amber-500"
                                        : "bg-gradient-to-br from-gray-400 to-gray-500"
                                }`}
                              >
                                {agent.username.charAt(0).toUpperCase()}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="font-medium text-gray-900 dark:text-white truncate">
                                    @{agent.username}
                                  </span>
                                  <AgentTypeBadge type={type} />
                                </div>
                                {agent.bio && (
                                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-2">
                                    {agent.bio}
                                  </p>
                                )}
                                <div className="flex items-center gap-1 mt-1.5 text-xs text-purple-600 dark:text-purple-400 opacity-60 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                                  <MessageSquare className="h-3 w-3" />
                                  Click to mention
                                </div>
                              </div>
                            </button>
                            <button
                              onClick={(e) => toggleFavorite(agent.username, e)}
                              className="flex-shrink-0 p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                              title={
                                isFav
                                  ? "Remove from favorites"
                                  : "Add to favorites"
                              }
                            >
                              {isFav ? (
                                <Star className="h-4 w-4 text-yellow-500 fill-yellow-500" />
                              ) : (
                                <StarOff className="h-4 w-4 text-gray-300 dark:text-gray-600 opacity-60 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity" />
                              )}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-center px-4">
                    <div className="w-16 h-16 rounded-full bg-gray-100 dark:bg-gray-700 flex items-center justify-center mb-4">
                      <Bot className="h-8 w-8 text-gray-400" />
                    </div>
                    <h3 className="text-sm font-medium text-gray-900 dark:text-white mb-1">
                      No agents here yet
                    </h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
                      Create an agent or use the search button above to add
                      existing agents to your favorites.
                    </p>
                    <Button
                      onClick={() => {
                        setIsExpanded(false);
                        onNavigateToRegister();
                      }}
                      className="gap-2"
                    >
                      <Plus className="h-4 w-4" />
                      Create Agent
                    </Button>
                  </div>
                )}
              </div>

              {/* Footer */}
              {sortedAgents.length > 0 && (
                <div className="p-4 border-t border-gray-200 dark:border-gray-700 flex gap-2">
                  <Button
                    variant="outline"
                    className="flex-1 gap-2"
                    onClick={() => {
                      setIsSearching(true);
                    }}
                  >
                    <UserPlus className="h-4 w-4" />
                    Add Agent
                  </Button>
                  <Button
                    variant="outline"
                    className="gap-2"
                    onClick={() => {
                      setIsExpanded(false);
                      onNavigateToRegister();
                    }}
                  >
                    <Plus className="h-4 w-4" />
                    Create
                  </Button>
                </div>
              )}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

/**
 * Backward-compatible re-export.
 * Existing consumers can keep using CloudAgentPanel without changes.
 */
export { AgentPanel as CloudAgentPanel };
