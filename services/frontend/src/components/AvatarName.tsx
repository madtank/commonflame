import { useState, useMemo, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  fetchAgentSummary,
  fetchUserSummary,
  type RosterEntry,
  type RosterTrust,
} from "@/lib/api-clean";
import { EmblemAvatar } from "@/components/AgentCard";
import {
  MessageSquare,
  Shield,
  CheckCircle2,
  Clock,
  User,
  PowerOff,
} from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { AgentKillSwitch } from "@/components/AgentKillSwitch";
import {
  agentControlService,
  type AgentControlState,
} from "@/services/agentControlService";
import { deriveAuthorRole } from "@/helpers/author-type";
import {
  formatAgentControlUntil,
  getAgentControlPresentation,
} from "@/lib/agent-control-state";
import { appendAgentMentionToCompose } from "@/lib/agent-compose";

const STATUS_COLOR: Record<string, string> = {
  active: "bg-emerald-500",
  recent: "bg-emerald-400",
  quiet: "bg-amber-500",
  online: "bg-emerald-500",
  offline: "bg-gray-400",
  new: "bg-gray-300",
  idle: "bg-amber-500",
  busy: "bg-red-500",
  inactive: "bg-gray-400",
};

// Trust tier configuration matching AgentCard styling
const TRUST_TIERS: Record<
  string,
  { label: string; color: string; bgColor: string; icon: typeof Shield }
> = {
  legendary: {
    label: "Legendary",
    color: "text-violet-600 dark:text-violet-400",
    bgColor: "bg-violet-100 dark:bg-violet-900/40",
    icon: Shield,
  },
  trusted: {
    label: "Trusted",
    color: "text-blue-600 dark:text-blue-400",
    bgColor: "bg-blue-100 dark:bg-blue-900/40",
    icon: Shield,
  },
  reliable: {
    label: "Reliable",
    color: "text-emerald-600 dark:text-emerald-400",
    bgColor: "bg-emerald-100 dark:bg-emerald-900/40",
    icon: CheckCircle2,
  },
  developing: {
    label: "Developing",
    color: "text-amber-600 dark:text-amber-400",
    bgColor: "bg-amber-100 dark:bg-amber-900/40",
    icon: Clock,
  },
  new: {
    label: "New",
    color: "text-gray-600 dark:text-gray-400",
    bgColor: "bg-gray-100 dark:bg-gray-800",
    icon: User,
  },
  basic: {
    label: "Basic",
    color: "text-gray-600 dark:text-gray-400",
    bgColor: "bg-gray-100 dark:bg-gray-800",
    icon: User,
  },
  verified: {
    label: "Verified",
    color: "text-purple-600 dark:text-purple-400",
    bgColor: "bg-purple-100 dark:bg-purple-900/40",
    icon: Shield,
  },
  moderator: {
    label: "Moderator",
    color: "text-yellow-600 dark:text-yellow-400",
    bgColor: "bg-yellow-100 dark:bg-yellow-900/40",
    icon: Shield,
  },
};

// Trust score thresholds for color coding (scores are 0-1 scale)
const TRUST_SCORE_THRESHOLDS = {
  EXCELLENT: 0.8,
  GOOD: 0.6,
  FAIR: 0.4,
} as const;

const getTrustScoreColor = (score: number, type: "text" | "bg") => {
  if (score >= TRUST_SCORE_THRESHOLDS.EXCELLENT) {
    return type === "text"
      ? "text-emerald-600 dark:text-emerald-400"
      : "bg-emerald-500";
  }
  if (score >= TRUST_SCORE_THRESHOLDS.GOOD) {
    return type === "text" ? "text-blue-600 dark:text-blue-400" : "bg-blue-500";
  }
  if (score >= TRUST_SCORE_THRESHOLDS.FAIR) {
    return type === "text"
      ? "text-amber-600 dark:text-amber-400"
      : "bg-amber-500";
  }
  return type === "text" ? "text-gray-500" : "bg-gray-400";
};

interface AvatarNameProps {
  type: "agent" | "user";
  id?: string | null;
  displayName: string;
  avatarUrl?: string | null;
  ownerHandle?: string;
  status?: string | null;
  spaceId?: string | null;
  disabled?: boolean;
  rosterEntry?: RosterEntry;
}

export function AvatarName({
  type,
  id,
  displayName,
  avatarUrl,
  ownerHandle,
  status,
  spaceId,
  disabled,
  rosterEntry,
}: AvatarNameProps) {
  const [open, setOpen] = useState(false);
  const { user } = useAuth();
  const [controlState, setControlState] = useState<AgentControlState | null>(
    null,
  );
  const [controlLoading, setControlLoading] = useState(false);

  // Check if current user owns this agent
  const currentUserId = user?.attributes?.id;
  const ownerUserId = rosterEntry?.owner_user?.id;
  const isOwnAgent = Boolean(
    type === "agent" &&
    currentUserId &&
    ownerUserId &&
    currentUserId === ownerUserId,
  );

  // Reset control state when space changes to prevent stale data
  useEffect(() => {
    setControlState(null);
  }, [spaceId]);

  // Fetch control state when popup opens for owned agents
  useEffect(() => {
    if (!open || !isOwnAgent || !id) {
      return;
    }

    const fetchControlState = async () => {
      setControlLoading(true);
      try {
        const state = await agentControlService.getControlState(id);
        setControlState(state);
      } catch (error) {
        console.error("Failed to fetch agent control state:", error);
        // Default to not disabled if we can't fetch
        setControlState({ is_disabled: false, disabled_by: [] });
      } finally {
        setControlLoading(false);
      }
    };

    fetchControlState();
  }, [open, isOwnAgent, id, spaceId]);

  const rosterOwner = useMemo(() => {
    if (!rosterEntry?.owner_user) return undefined;
    return {
      id: rosterEntry.owner_user.id,
      name: rosterEntry.owner_user.display_name,
      handle: rosterEntry.owner_user.handle,
      avatar: null,
    };
  }, [rosterEntry]);

  const rosterStatus = rosterEntry?.presence?.status ?? null;
  const rosterLastActive = rosterEntry?.presence?.last_active ?? null;

  const normalizedOwner = (
    ownerHandle ?? rosterEntry?.owner_user?.handle
  )?.replace(/^@/, "");

  const statusClass = (() => {
    if (type === "agent") return undefined;
    const value = status || rosterStatus;
    if (!value) return undefined;
    const normalized = value.toLowerCase();
    return STATUS_COLOR[normalized] || STATUS_COLOR.offline;
  })();

  const hasAgentSummary = Boolean(id && spaceId);
  const canOpen = !disabled && Boolean(rosterEntry || id);
  const headerControlPresentation = getAgentControlPresentation(controlState);

  const queryKey = useMemo(() => {
    if (!id || !spaceId) return [];
    return type === "agent"
      ? ["agentSummary", id, spaceId]
      : ["userSummary", id, spaceId];
  }, [id, spaceId, type]);

  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: () => {
      if (!spaceId || !id) throw new Error("space and id required");
      return type === "agent"
        ? fetchAgentSummary(id, spaceId)
        : fetchUserSummary(id, spaceId);
    },
    enabled: open && hasAgentSummary,
    retry: false,
  });

  const axiosError = axios.isAxiosError(error) ? error : null;
  // Only consider "private" if we have NO roster data (can't see agent in space)
  // If we have rosterEntry, the agent is visible to us and we should show their info
  const apiIndicatesPrivate =
    axiosError?.response?.status === 403 ||
    (type === "agent" && (data as any)?.visibility === "private");
  const privacyRestricted = apiIndicatesPrivate && !rosterEntry;
  const notFound = axiosError?.response?.status === 404;

  const panelData = (privacyRestricted || notFound ? undefined : data) as any;

  const authorRole = deriveAuthorRole(
    {
      author_type: type === "agent" ? "agent" : "user",
      metadata: rosterEntry?.metadata || panelData?.metadata,
    },
    rosterEntry,
  );
  const isAdminRole = authorRole === "ADMIN";

  // Phase 1: Always show agent name, never "Private agent"
  // If displayName is "Private agent", try to get the real name from data or fallback to a generic name
  let effectiveTitle = panelData?.name || rosterEntry?.display_name;
  if (!effectiveTitle) {
    effectiveTitle = displayName === "Private agent" ? "Agent" : displayName;
  }
  const panelTitle = effectiveTitle;
  const resolveAvatar = (...candidates: Array<string | null | undefined>) => {
    for (const candidate of candidates) {
      if (candidate) return candidate;
    }
    const knownName = (panelTitle || displayName || rosterEntry?.handle || "")
      .toLowerCase()
      .replace(/^@/, "");
    return null;
  };

  const panelAvatar = resolveAvatar(
    panelData?.avatar,
    rosterEntry?.avatar_url,
    avatarUrl,
  );
  const panelOwner =
    type === "agent"
      ? (panelData?.owner ??
        rosterOwner ??
        (normalizedOwner
          ? { handle: normalizedOwner, name: normalizedOwner.replace(/^@/, "") }
          : undefined))
      : undefined;
  const panelStatus =
    type === "agent"
      ? undefined
      : (panelData?.status ?? rosterStatus ?? status);
  const panelLastActive = panelData?.last_active_at ?? rosterLastActive ?? null;
  const panelVisibility =
    (panelData?.visibility as string | undefined) ||
    (privacyRestricted ? "private" : undefined);

  const mapStatusForEmblem = (
    value?: string | null,
  ): "active" | "recent" | "idle" | "offline" | "busy" => {
    if (!value) return "offline";
    const normalized = value.toLowerCase();
    if (["active", "online"].includes(normalized)) return "active";
    if (["recent"].includes(normalized)) return "recent";
    if (["busy"].includes(normalized)) return "busy";
    if (["quiet", "idle"].includes(normalized)) return "idle";
    if (normalized === "inactive") return "offline";
    return "offline";
  };

  const renderAvatar = () => {
    const fallbackAvatar = resolveAvatar(rosterEntry?.avatar_url, avatarUrl);

    if (fallbackAvatar) {
      return (
        <img
          src={fallbackAvatar}
          alt=""
          className="h-6 w-6 rounded-full object-cover"
        />
      );
    }

    if (type === "agent") {
      return (
        <EmblemAvatar
          idSeed={id || rosterEntry?.id || displayName}
          size={28}
          status={mapStatusForEmblem(rosterStatus ?? status)}
        />
      );
    }

    const initials = displayName.replace(/^@/, "").slice(0, 2).toUpperCase();
    return (
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-200 text-[11px] font-semibold text-gray-700 dark:bg-gray-700 dark:text-gray-200">
        {initials || "U"}
      </span>
    );
  };

  const handleOpen = () => {
    if (!canOpen) return;
    setOpen(true);
  };

  return (
    <>
      <button
        type="button"
        className={`inline-flex min-w-0 max-w-full items-center gap-2 rounded-full px-2 py-1 text-left transition ${canOpen ? "hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer" : "cursor-default"}`}
        onClick={handleOpen}
        title={
          type === "agent" && normalizedOwner
            ? `Owner: @${normalizedOwner}`
            : undefined
        }
        aria-disabled={!canOpen}
      >
        <span className="relative inline-flex items-center">
          {renderAvatar()}
          {statusClass && (
            <span
              className={`absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border border-white dark:border-gray-900 ${statusClass}`}
            />
          )}
        </span>
        <span className="min-w-0 max-w-[min(56vw,14rem)] truncate font-medium sm:max-w-none">
          {displayName}
        </span>
        {isAdminRole && (
          <span className="ml-0.5 shrink-0 text-[8px] text-red-700 dark:text-red-300 bg-red-100 dark:bg-red-900 px-1 py-0 rounded font-bold border border-red-200 dark:border-red-800 uppercase leading-none">
            Admin
          </span>
        )}
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3">
              {panelAvatar ? (
                <img
                  src={panelAvatar}
                  alt=""
                  className="h-10 w-10 rounded-full object-cover"
                />
              ) : type === "agent" ? (
                <EmblemAvatar
                  idSeed={id || rosterEntry?.id || displayName}
                  size={40}
                  status={mapStatusForEmblem(rosterStatus ?? status)}
                />
              ) : (
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-gray-200 text-sm font-semibold text-gray-700 dark:bg-gray-700 dark:text-gray-200">
                  {displayName.replace(/^@/, "").slice(0, 2).toUpperCase() ||
                    "U"}
                </span>
              )}
              <span>{panelTitle}</span>
              {isAdminRole && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 text-xs font-bold border border-red-200 dark:border-red-800">
                  <Shield className="w-3 h-3" />
                  ADMIN
                </span>
              )}
              {isOwnAgent && headerControlPresentation.mode !== "active" && (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-gray-200 px-2 py-0.5 text-xs text-gray-600 dark:bg-gray-700 dark:text-gray-400"
                  title={
                    headerControlPresentation.detail ||
                    headerControlPresentation.label
                  }
                >
                  <PowerOff className="w-3 h-3" />
                  {headerControlPresentation.label}
                </span>
              )}
            </DialogTitle>
          </DialogHeader>
          {isLoading ? (
            <div className="space-y-2 text-sm text-muted-foreground">
              <div className="h-4 w-32 animate-pulse rounded bg-muted" />
              <div className="h-4 w-40 animate-pulse rounded bg-muted" />
            </div>
          ) : type === "agent" ? (
            <AgentSummaryBody
              summary={(panelData as any) ?? undefined}
              status={panelStatus}
              owner={panelOwner}
              fallbackOwnerHandle={ownerHandle ?? normalizedOwner ?? undefined}
              lastActive={panelLastActive}
              showPrivacyNotice={privacyRestricted}
              trust={rosterEntry?.trust}
              agentHandle={rosterEntry?.handle || displayName}
              onClose={() => setOpen(false)}
              agentId={id ?? undefined}
              agentName={displayName}
              isOwnAgent={isOwnAgent}
              controlState={controlState}
              controlLoading={controlLoading}
              onControlToggle={setControlState}
            />
          ) : (
            <UserSummaryBody
              summary={data as any}
              status={panelStatus}
              lastActive={panelLastActive}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

export interface AgentSummaryBodyProps {
  summary?: {
    owner?: {
      id?: string;
      name?: string;
      handle?: string;
      avatar?: string | null;
    };
    last_active_at?: string | null;
    visibility?: string | null;
    agent_type?: string | null;
    origin?: string | null;
    runtime_kind?: string | null;
  };
  status?: string | null;
  owner?: {
    id?: string;
    name?: string;
    handle?: string;
    avatar?: string | null;
  } | null;
  fallbackOwnerHandle?: string;
  lastActive?: string | null;
  showPrivacyNotice?: boolean;
  // Trust data from roster entry (uses RosterTrust type from api-clean.ts)
  trust?: RosterTrust;
  // Handle for messaging
  agentHandle?: string;
  onMessageAgent?: (handle: string) => void;
  onClose?: () => void;
  // Control state for kill switch (only shown for own agents)
  agentId?: string;
  agentName?: string;
  isOwnAgent?: boolean;
  controlState?: AgentControlState | null;
  controlLoading?: boolean;
  onControlToggle?: (state: AgentControlState) => void;
}

export function AgentSummaryBody({
  summary,
  status,
  owner,
  fallbackOwnerHandle,
  lastActive,
  showPrivacyNotice,
  trust,
  agentHandle,
  onMessageAgent,
  onClose,
  agentId,
  agentName,
  isOwnAgent,
  controlState,
  controlLoading,
  onControlToggle,
}: AgentSummaryBodyProps) {
  const fallbackOwner = fallbackOwnerHandle
    ? {
        handle: fallbackOwnerHandle,
        name: fallbackOwnerHandle.replace(/^@/, ""),
      }
    : undefined;
  const effectiveOwner = owner ?? summary?.owner ?? fallbackOwner;
  const ownerHandle = effectiveOwner?.handle?.replace(/^@/, "") ?? "";
  const normalizedAgentName = (agentName || agentHandle || "")
    .replace(/^@/, "")
    .toLowerCase();
  const isSystemOwner = ownerHandle === "__system__";
  const isSpaceOwnedAgent =
    summary?.origin === "space_agent" ||
    summary?.agent_type === "space_agent" ||
    summary?.runtime_kind === "space_agent" ||
    (normalizedAgentName === "ax" && isSystemOwner);
  const ownerInitials = isSpaceOwnedAgent
    ? "SP"
    : ownerHandle.slice(0, 2).toUpperCase() || "U";
  const ownerLabel = isSpaceOwnedAgent ? "Space-owned agent" : "Owner";
  const ownerDisplay = isSpaceOwnedAgent
    ? "Owned by this space"
    : `@${ownerHandle}`;
  const lastSeen = lastActive ?? summary?.last_active_at;

  // Get trust tier config
  const tierKey = (trust?.tier ?? "new").toLowerCase();
  const trustConfig = TRUST_TIERS[tierKey] ?? TRUST_TIERS.new;
  const TrustIcon = trustConfig.icon;

  // Use trust_score (0-1 scale from AI intelligence analysis)
  // This is the computed score from quality/spam/toxicity analysis
  const trustScore = trust?.trust_score ?? null;

  // Handle message button click - appends to existing message instead of replacing
  const handleMessageClick = () => {
    if (!agentHandle) return;
    const handle = agentHandle.replace(/^@/, "");
    if (onMessageAgent) {
      onMessageAgent(handle);
    } else {
      appendAgentMentionToCompose(agentHandle);
    }
    onClose?.();
  };

  const isAgentDisabled = controlState?.is_disabled ?? false;
  const controlPresentation = getAgentControlPresentation(controlState);
  const controlUntilText = formatAgentControlUntil(controlPresentation.until);

  return (
    <div className="space-y-4 text-sm">
      {showPrivacyNotice && (
        <div className="rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200">
          This agent is marked private. Showing limited information.
        </div>
      )}

      {/* Agent Control Section - Only shown for own agents */}
      {isOwnAgent && agentId && (
        <div className="space-y-2">
          {/* Current control indicator */}
          {controlPresentation.mode !== "active" && (
            <div className="flex items-center gap-2 rounded border border-gray-300 bg-gray-100 p-2 text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300">
              <PowerOff className="h-4 w-4 text-gray-500" />
              <span>
                <strong>{controlPresentation.label}.</strong>{" "}
                {controlPresentation.detail}
                {controlUntilText ? ` Until ${controlUntilText}.` : ""}
              </span>
            </div>
          )}

          {/* Kill switch */}
          <div className="flex items-center justify-between pt-1">
            <span className="text-xs text-muted-foreground">
              State: {controlPresentation.label}
            </span>
            {controlLoading ? (
              <div className="h-8 w-20 animate-pulse rounded bg-muted" />
            ) : (
              <AgentKillSwitch
                agentId={agentId}
                agentName={agentName || "Agent"}
                isDisabled={isAgentDisabled}
                disabledReason={controlState?.disabled_reason}
                controlState={controlState}
                onToggle={onControlToggle}
                size="sm"
                showLabel={true}
              />
            )}
          </div>
        </div>
      )}

      {/* Trust Section */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div
            className={`flex items-center gap-2 px-2.5 py-1 rounded-full ${trustConfig.bgColor}`}
          >
            <TrustIcon className={`w-3.5 h-3.5 ${trustConfig.color}`} />
            <span className={`text-xs font-medium ${trustConfig.color}`}>
              {trustConfig.label}
            </span>
          </div>
          {trustScore != null && trustScore > 0 && (
            <span
              className={`text-sm font-bold ${getTrustScoreColor(trustScore, "text")}`}
            >
              {Math.round(trustScore * 100)}%
            </span>
          )}
        </div>

        {/* Trust score bar */}
        {trustScore != null && trustScore > 0 && (
          <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${getTrustScoreColor(trustScore, "bg")}`}
              style={{ width: `${Math.round(trustScore * 100)}%` }}
            />
          </div>
        )}

        {/* Quality metrics (if available) */}
        {trust?.messages_analyzed != null && trust.messages_analyzed > 0 && (
          <div className="flex items-center gap-4 text-[10px] text-muted-foreground">
            <span>{trust.messages_analyzed} messages analyzed</span>
            {trust.avg_quality_score != null && (
              <span className="text-emerald-600 dark:text-emerald-400">
                Quality: {Math.round(trust.avg_quality_score * 100)}%
              </span>
            )}
          </div>
        )}
      </div>

      {/* Owner info */}
      {effectiveOwner?.handle && (
        <div className="flex items-center gap-3 pt-2 border-t border-gray-100 dark:border-gray-800">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gray-200 text-xs font-semibold text-gray-700 dark:bg-gray-700 dark:text-gray-200">
            {ownerInitials}
          </span>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {ownerLabel}
            </div>
            <div className="font-medium text-sm">{ownerDisplay}</div>
          </div>
        </div>
      )}

      {/* Last active */}
      {lastSeen && (
        <div className="text-xs text-muted-foreground">
          Last active: {new Date(lastSeen).toLocaleString()}
        </div>
      )}

      {/* Message button */}
      {agentHandle && (
        <Button
          onClick={handleMessageClick}
          className="w-full mt-2"
          size="sm"
          variant="default"
          aria-label={`Send message to ${agentHandle.replace(/^@/, "")}`}
        >
          <MessageSquare className="w-4 h-4 mr-2" />
          Send Message
        </Button>
      )}
    </div>
  );
}

function UserSummaryBody({
  summary,
  lastActive,
}: {
  summary?: { last_active_at?: string | null };
  status?: string | null;
  lastActive?: string | null;
}) {
  const lastSeen = lastActive ?? summary?.last_active_at;
  return (
    <div className="space-y-3 text-sm">
      {lastSeen ? (
        <div className="text-xs text-muted-foreground">
          Last active: {new Date(lastSeen).toLocaleString()}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground">
          No recent activity recorded.
        </div>
      )}
    </div>
  );
}
