import React, { useState } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { AgentKillSwitch } from "@/components/AgentKillSwitch";
import type { AgentControlState } from "@/services/agentControlService";
import { useToast } from "@/components/ui/use-toast";
import {
  formatAgentControlUntil,
  getAgentControlPresentation,
} from "@/lib/agent-control-state";
import {
  Bot,
  User,
  Trophy,
  Zap,
  MessageSquare,
  Calendar,
  Shield,
  Star,
  Pin,
  TrendingUp,
  Award,
  Crown,
  Sparkles,
  Code,
  Palette,
  Search,
  Brain,
  Lock,
  Unlock,
  Compass,
  Rocket,
  Cloud,
  Server,
  Check,
  Settings,
  MoreHorizontal,
  Activity,
  Clock,
  Eye,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Globe,
  Boxes,
  Image as ImageIcon,
  Flame,
} from "lucide-react";
import {
  FOLLOW_UUID,
  getMobilityMode,
  buildWorkspaceDisplay,
} from "@/lib/agent-mobility";
import {
  isCloudLikeAgent,
  isExternalAgent,
  isNativeCloudAgent,
  isV2Engine,
  resolveExternalSubType,
  getAgentRuntimeDisplay,
} from "@/lib/agent-utils";

interface AgentStats {
  messages: number;
  responses: number;
  accuracy: number;
  streak: number;
  helpfulVotes: number;
  reportedCount: number;
  moderationScore: number;
  reactions?: Record<string, number>; // Emoji reactions: { "👍": count, "👎": count, ... }
}

interface Achievement {
  id: string;
  name: string;
  icon: React.ReactNode;
  unlocked: boolean;
  description: string;
}

interface SafetyFlags {
  isSpammer: boolean;
  isBully: boolean;
  isThreat: boolean;
  isTroll: boolean;
  trustLevel: "new" | "basic" | "trusted" | "verified" | "moderator";
  moderationAlerts: number;
  reportsAgainst: number;
}

interface GamifiedAgentCardProps {
  agent: {
    id: string;
    username: string;
    agent_type: "user" | "general"; // Align with database schema
    avatar_url?: string | null;
    bio?: string;
    email?: string;
    last_active?: string;
    user_id?: string; // Link to user_profiles for humans
    team_id?: string;
    team_name?: string;
    team_color?: string;
    status?: "active" | "idle" | "offline" | "busy" | "inactive" | "error";
    posts_count?: number;
    daily_posts?: number;
    verified?: boolean;
    // Gamification data
    level?: number;
    experience?: number;
    rank?: string;
    achievements?: Achievement[];
    stats?: AgentStats;
    // Safety & Moderation
    safety?: SafetyFlags;
    // Ownership info
    is_own_agent?: boolean;
    owner_username?: string;
    origin?: "cloud" | "mcp" | "external_gateway" | string;
    // Space awareness (optional)
    current_org_id?: string;
    current_space_name?: string;
    current_space_slug?: string;
    pinned?: boolean;
    // Space/mobility typing helpers
    tasks_completed?: number;
    follow_user?: boolean;
    pinned_to_org?: string;
    pinned_org_id?: string;
    settings?: {
      follow_user?: boolean;
      sub_type?: string;
      subtype?: string;
      external_sub_type?: string;
      webhook_url?: string;
      webhookUrl?: string;
    };
    sub_type?: string;
    external_sub_type?: string;
    webhook_url?: string;
    webhookUrl?: string;
    control?: AgentControlState;
    // Cloud agent fields
    cloud_function_url?: string;
    enable_cloud_agent?: boolean;
    webhook_verified?: boolean | null;
    system_prompt?: string;
    is_automated?: boolean;
    capabilities?: {
      auto_respond?: boolean;
      monitor_all?: boolean;
      supports_wait?: boolean;
      can_initiate?: boolean;
      sub_type?: string;
    };
    // Tool capabilities (JSONB - preferred)
    enabled_tools?: Record<string, boolean>;
    // Legacy individual fields (backwards compat)
    web_browsing_enabled?: boolean;
    ax_mcp_enabled?: boolean;
    image_gen_enabled?: boolean;
    web_fetch_enabled?: boolean;
    // Engine version for v1/v2 routing
    engine_version?: "v1" | "v2";
    // Trust/Intelligence scores (aggregated from message analysis)
    trust_score?: number | null; // 0-1, computed from quality/spam/toxicity
    trust_tier?: string | null; // e.g., "trusted", "new", "verified"
    avg_quality_score?: number | null;
    avg_spam_score?: number | null;
    avg_toxicity_score?: number | null;
    messages_analyzed?: number; // how many messages contributed to scores
    // LLM Model Info
    model?: string;
    model_tier?: "standard" | "lite" | "premium";
  };
  onClick?: () => void;
  isSelected?: boolean;
  onControlUpdate?: (agentId: string, next: AgentControlState) => void;
  onMessage?: (agentUsername: string) => void;
  onMcpConfig?: (agentId: string) => void;
}

// --- Inline, composable emblem avatar (no extra deps) ------------------------
function seededInt(seed: string, mod: number) {
  let h = 2166136261 >>> 0; // FNV-1a
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h) % mod;
}

type EmblemVariant = "robot" | "chess" | "shield" | "glyph";

type EmblemProps = {
  idSeed: string;
  size?: number;
  variant?: EmblemVariant;
  subtype?: string;
  hue?: "indigo" | "violet" | "cyan" | "emerald" | "amber" | "rose" | "slate";
  level?: number;
  progress?: number; // 0..1
  trust?: boolean;
  freeRoam?: boolean;
  status?: "active" | "recent" | "idle" | "offline" | "busy";
};

const HUES = {
  indigo: { a: "#6366F1", b: "#22D3EE", glow: "#A5B4FC" },
  violet: { a: "#8B5CF6", b: "#06B6D4", glow: "#C4B5FD" },
  cyan: { a: "#22D3EE", b: "#14B8A6", glow: "#99F6E4" },
  emerald: { a: "#34D399", b: "#06B6D4", glow: "#A7F3D0" },
  amber: { a: "#F59E0B", b: "#F43F5E", glow: "#FDE68A" },
  rose: { a: "#FB7185", b: "#6366F1", glow: "#FDA4AF" },
  slate: { a: "#334155", b: "#06B6D4", glow: "#94A3B8" },
};

const CLAWDBOT_LOGO = "/brands/clawdbot-crab.svg";

function ChessGlyph({ sub }: { sub: string }) {
  const fill = "#0B1020";
  switch (sub) {
    case "king":
      return (
        <g>
          <path d="M32 14h2v4h4v2h-4v4h-2v-4h-4v-2h4z" fill={fill} />
          <path
            d="M20 34c0-6 5-10 12-10s12 4 12 10v2H20z"
            fill={fill}
            opacity={0.9}
          />
          <path d="M24 38h16l2 6H22z" fill={fill} />
        </g>
      );
    case "queen":
      return (
        <g>
          <circle cx={22} cy={20} r={3} fill={fill} />
          <circle cx={32} cy={18} r={3} fill={fill} />
          <circle cx={42} cy={20} r={3} fill={fill} />
          <path
            d="M20 34c0-6 5-10 12-10s12 4 12 10v2H20z"
            fill={fill}
            opacity={0.9}
          />
          <path d="M24 38h16l2 6H22z" fill={fill} />
        </g>
      );
    case "rook":
      return (
        <g>
          <path d="M24 18h16v6H24z" fill={fill} />
          <path d="M24 24h16v12H24z" fill={fill} opacity={0.9} />
          <path d="M22 38h20l2 6H20z" fill={fill} />
        </g>
      );
    case "bishop":
      return (
        <g>
          <path d="M32 16c6 3 8 9 6 14H26c-2-5 0-11 6-14z" fill={fill} />
          <circle cx={32} cy={16} r={2} fill={fill} />
          <path d="M24 38h16l2 6H22z" fill={fill} />
        </g>
      );
    case "knight":
      return (
        <g>
          <path
            d="M26 18c6 0 10 3 12 8l-6 2 2 4H24c-3-8 2-14 2-14z"
            fill={fill}
          />
          <circle cx={32} cy={22} r={1.3} fill="#111" />
          <path d="M24 38h16l2 6H22z" fill={fill} />
        </g>
      );
    default:
      return (
        <g>
          <circle cx={32} cy={20} r={5} fill={fill} />
          <path d="M24 32h16v4H24z" fill={fill} opacity={0.9} />
          <path d="M24 38h16l2 6H22z" fill={fill} />
        </g>
      );
  }
}

function ShieldGlyph({ sub }: { sub: string }) {
  const fill = "#0B1020";
  switch (sub) {
    case "kite":
      return (
        <path
          d="M32 14c8 4 12 6 12 12 0 10-8 16-12 20-4-4-12-10-12-20 0-6 4-8 12-12z"
          fill={fill}
        />
      );
    case "round":
      return <circle cx={32} cy={28} r={14} fill={fill} />;
    case "heater":
      return (
        <path d="M20 16h24v10c0 8-6 14-12 18-6-4-12-10-12-18z" fill={fill} />
      );
    default:
      return <path d="M24 16h16v8c0 7-4 12-8 16-4-4-8-9-8-16z" fill={fill} />;
  }
}

function CircuitGlyph() {
  return (
    <g stroke="#0B1020" strokeWidth={2.4} strokeLinecap="round" fill="none">
      <path d="M18 28h28M32 16v24M24 20l16 16" />
    </g>
  );
}

export function EmblemAvatar({
  idSeed,
  size = 48,
  variant = "chess",
  subtype,
  hue = "indigo",
  level = 4,
  progress = 0.6,
  trust = false,
  freeRoam = false,
  status = "active",
}: EmblemProps) {
  const id = `g-${idSeed.replace(/[^a-zA-Z0-9]/g, "")}`;
  const colors = HUES[hue];
  const r = 30,
    C = 2 * Math.PI * r,
    p = Math.max(0, Math.min(1, progress));
  const subAuto =
    subtype ||
    (variant === "chess"
      ? ["pawn", "knight", "bishop", "rook", "queen", "king"][
          seededInt(idSeed, 6)
        ]
      : variant === "shield"
        ? ["heater", "kite", "round", "tab"][seededInt(idSeed, 4)]
        : "0");

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg
        viewBox="0 0 64 64"
        width={size}
        height={size}
        role="img"
        aria-label="Agent emblem"
      >
        <defs>
          <linearGradient id={id} x1="0" x2="1" y1="1" y2="0">
            <stop stopColor={colors.a} />
            <stop offset="1" stopColor={colors.b} />
          </linearGradient>
        </defs>
        {/* Plate */}
        <circle cx={32} cy={32} r={28} fill={`url(#${id})`} />
        <circle
          cx={32}
          cy={32}
          r={26}
          fill="none"
          stroke="rgba(255,255,255,0.22)"
          strokeDasharray="2 4"
        />
        {/* Emblem */}
        {variant === "robot" && (
          <g>
            <rect x={20} y={18} width={24} height={20} rx={6} fill="#0B1020" />
            <circle cx={28} cy={26} r={2.8} fill="#0B1020" />
            <circle cx={36} cy={26} r={2.8} fill="#0B1020" />
            <rect
              x={28}
              y={32}
              width={8}
              height={2.6}
              rx={1.3}
              fill="#0B1020"
            />
          </g>
        )}
        {variant === "chess" && <ChessGlyph sub={subAuto} />}
        {variant === "shield" && <ShieldGlyph sub={subAuto} />}
        {variant === "glyph" && <CircuitGlyph />}
        {/* Progress ring */}
        <g transform="rotate(-90 32 32)">
          <circle
            cx={32}
            cy={32}
            r={r}
            fill="none"
            stroke="rgba(255,255,255,0.18)"
            strokeWidth={4}
          />
          <circle
            cx={32}
            cy={32}
            r={r}
            fill="none"
            stroke="#fff"
            strokeOpacity={0.9}
            strokeWidth={4}
            strokeLinecap="round"
            strokeDasharray={`${C * p} ${C}`}
          />
        </g>
        {/* Level pips */}
        {Array.from({ length: Math.min(10, Math.max(1, level)) }).map(
          (_, i) => {
            const angle = (i / 10) * 2 * Math.PI - Math.PI / 2;
            const R = 22;
            const x = 32 + R * Math.cos(angle);
            const y = 32 + R * Math.sin(angle);
            return (
              <circle
                key={i}
                cx={x}
                cy={y}
                r={1.6}
                fill="rgba(255,255,255,0.75)"
              />
            );
          },
        )}
      </svg>
      {/* Status dot (now supports derived 'recent') */}
      <div
        className={`absolute bottom-0 right-0 w-3 h-3 rounded-full border-2 border-white dark:border-gray-800
          ${status === "active" || status === "busy" ? "bg-green-500" : status === "recent" ? "bg-emerald-400" : status === "idle" ? "bg-yellow-500" : "bg-gray-400"}
          ${status === "active" || status === "busy" ? "animate-pulse" : ""}`}
        title={
          status === "recent"
            ? "Recently active"
            : status.charAt(0).toUpperCase() + status.slice(1)
        }
      />
      {/* Trust / roam pips */}
      {trust && (
        <div className="absolute -top-1 -left-1 w-4 h-4 rounded-full bg-emerald-500 border-2 border-white text-[10px] flex items-center justify-center">
          ✓
        </div>
      )}
      {freeRoam && (
        <div className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-sky-500 border-2 border-white text-[10px] flex items-center justify-center">
          ⚡
        </div>
      )}
    </div>
  );
}

/** Threshold for showing truncation UI (character count) */
const BIO_TRUNCATE_THRESHOLD = 150;

/**
 * AgentBio - Truncated bio with click-to-expand
 * Shows ~3 lines by default, full text on click
 * Exported for use in PublicAgentCard
 */
export function AgentBio({ bio }: { bio: string }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const isLong = bio.length > BIO_TRUNCATE_THRESHOLD;

  if (!isLong) {
    return <p className="text-xs text-muted-foreground mt-1">{bio}</p>;
  }

  const handleToggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsExpanded(!isExpanded);
  };

  return (
    <div className="mt-1">
      <p
        className={`text-xs text-muted-foreground cursor-pointer transition-all ${
          isExpanded ? "" : "line-clamp-3"
        }`}
        onClick={handleToggle}
        title={isExpanded ? "Click to collapse" : "Click to expand"}
      >
        {bio}
      </p>
      <button
        type="button"
        onClick={handleToggle}
        aria-expanded={isExpanded}
        aria-label={isExpanded ? "Collapse agent bio" : "Expand agent bio"}
        className="text-[10px] text-blue-500 hover:text-blue-600 dark:text-blue-400 dark:hover:text-blue-300 mt-0.5"
      >
        {isExpanded ? "Show less" : "Show more"}
      </button>
    </div>
  );
}

export function GamifiedAgentCard({
  agent,
  onClick,
  isSelected = false,
  onControlUpdate,
  onMessage,
  onMcpConfig,
}: GamifiedAgentCardProps) {
  const { toast } = useToast();
  const [showBurst, setShowBurst] = React.useState(false);
  const [showAllReactions, setShowAllReactions] = React.useState(false);
  const [controlState, setControlState] = React.useState<AgentControlState>({
    is_disabled: agent.control?.is_disabled ?? false,
    disabled_reason: agent.control?.disabled_reason,
    disabled_by: agent.control?.disabled_by ?? [],
    disabled_until: agent.control?.disabled_until ?? null,
    no_reply: agent.control?.no_reply ?? false,
    no_reply_reason: agent.control?.no_reply_reason,
    no_reply_by: agent.control?.no_reply_by ?? [],
    no_reply_until: agent.control?.no_reply_until ?? null,
    routing_only: agent.control?.routing_only ?? false,
    routing_only_reason: agent.control?.routing_only_reason,
    routing_only_by: agent.control?.routing_only_by ?? [],
    routing_only_until: agent.control?.routing_only_until ?? null,
    user_hourly_limit: agent.control?.user_hourly_limit,
    user_daily_limit: agent.control?.user_daily_limit,
    agent_hourly_limit: agent.control?.agent_hourly_limit,
    agent_daily_limit: agent.control?.agent_daily_limit,
  });

  React.useEffect(() => {
    setControlState({
      is_disabled: agent.control?.is_disabled ?? false,
      disabled_reason: agent.control?.disabled_reason,
      disabled_by: agent.control?.disabled_by ?? [],
      disabled_until: agent.control?.disabled_until ?? null,
      no_reply: agent.control?.no_reply ?? false,
      no_reply_reason: agent.control?.no_reply_reason,
      no_reply_by: agent.control?.no_reply_by ?? [],
      no_reply_until: agent.control?.no_reply_until ?? null,
      routing_only: agent.control?.routing_only ?? false,
      routing_only_reason: agent.control?.routing_only_reason,
      routing_only_by: agent.control?.routing_only_by ?? [],
      routing_only_until: agent.control?.routing_only_until ?? null,
      user_hourly_limit: agent.control?.user_hourly_limit,
      user_daily_limit: agent.control?.user_daily_limit,
      agent_hourly_limit: agent.control?.agent_hourly_limit,
      agent_daily_limit: agent.control?.agent_daily_limit,
    });
  }, [agent.control, agent.id]);

  React.useEffect(() => {
    setShowAllReactions(false);
  }, [agent.id]);

  const isDisabled = controlState.is_disabled;
  const disabledReason = controlState.disabled_reason;
  const controlPresentation = getAgentControlPresentation(controlState);
  const controlUntilText = formatAgentControlUntil(controlPresentation.until);
  // Calculate level based on activity
  const calculateLevel = () => {
    const posts = agent.posts_count || 0;
    if (posts < 10) return 1;
    if (posts < 50) return 2;
    if (posts < 100) return 3;
    if (posts < 500) return 4;
    if (posts < 1000) return 5;
    return Math.floor(Math.log10(posts) * 2);
  };

  const level = agent.level || calculateLevel();
  const prevLevelRef = React.useRef(level);
  React.useEffect(() => {
    if (level > prevLevelRef.current) {
      setShowBurst(true);
      const t = setTimeout(() => setShowBurst(false), 1300);
      prevLevelRef.current = level;
      return () => clearTimeout(t);
    }
    prevLevelRef.current = level;
  }, [level]);

  // Get rank based on level
  const getRank = (level: number) => {
    if (level < 2)
      return { name: "Rookie", color: "text-gray-500", icon: "🌱" };
    if (level < 4)
      return { name: "Explorer", color: "text-blue-500", icon: "🚀" };
    if (level < 6)
      return { name: "Expert", color: "text-purple-500", icon: "⚡" };
    if (level < 8)
      return { name: "Master", color: "text-orange-500", icon: "🔥" };
    if (level < 10)
      return { name: "Legend", color: "text-red-500", icon: "👑" };
    return { name: "Mythic", color: "text-yellow-500", icon: "✨" };
  };

  const rank = getRank(level);

  // Get agent type icon and color
  const getAgentTypeStyle = (type: string) => {
    switch (type?.toLowerCase()) {
      case "research":
        return {
          icon: <Search className="w-4 h-4" />,
          color: "bg-blue-500",
          label: "Research",
        };
      case "creative":
        return {
          icon: <Palette className="w-4 h-4" />,
          color: "bg-purple-500",
          label: "Creative",
        };
      case "analytical":
        return {
          icon: <Brain className="w-4 h-4" />,
          color: "bg-green-500",
          label: "Analytical",
        };
      case "development":
        return {
          icon: <Code className="w-4 h-4" />,
          color: "bg-orange-500",
          label: "Development",
        };
      default:
        return {
          icon: <Zap className="w-4 h-4" />,
          color: "bg-gray-500",
          label: "General",
        };
    }
  };

  const agentType = getAgentTypeStyle(agent.agent_type);

  const trustScoreRaw = (() => {
    const raw = agent.trust_score;
    const value =
      typeof raw === "number"
        ? raw
        : typeof raw === "string"
          ? Number(raw)
          : NaN;
    return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
  })();
  const hasTrustSignals = (agent.messages_analyzed ?? 0) > 0;
  const trustScore =
    trustScoreRaw != null &&
    (hasTrustSignals || agent.messages_analyzed == null)
      ? trustScoreRaw
      : null;

  // Activity / status derivation
  // We previously trusted agent.status (always 'active' in many cases). Now derive from last_active for truthful display.
  // Thresholds (could be lifted into config):
  //  - <2m  => active
  //  - <60m => recent
  //  - <6h  => idle
  //  - else offline
  const deriveActivityStatus = () => {
    const explicit =
      agent.status && !["", undefined, null].includes(agent.status as any)
        ? agent.status
        : undefined;
    const last = agent.last_active ? new Date(agent.last_active) : null;
    if (explicit && explicit !== "active") return explicit; // honor non-active explicit statuses
    if (!last) return explicit === "active" ? "offline" : "offline";
    const diffMs = Date.now() - last.getTime();
    if (diffMs < 2 * 60 * 1000) return "active";
    if (diffMs < 60 * 60 * 1000) return "recent";
    if (diffMs < 6 * 60 * 60 * 1000) return "idle";
    return "offline";
  };

  const activityStatus = deriveActivityStatus();
  // Prefer branded artwork; default to our SVG emblem when none exists.
  const avatarUrl =
    (agent as any)?.avatar_url || (agent as any)?.config?.avatar_url || null;

  // Status indicator styling (now includes 'recent')
  const getStatusStyle = (status: string) => {
    switch (status) {
      case "active":
        return { color: "bg-green-500", pulse: true };
      case "recent":
        return { color: "bg-emerald-400", pulse: false };
      case "idle":
        return { color: "bg-yellow-500", pulse: false };
      case "offline":
        return { color: "bg-gray-400", pulse: false };
      case "error":
        return { color: "bg-red-500", pulse: false };
      case "inactive":
        return { color: "bg-gray-400", pulse: false };
      default:
        return { color: "bg-gray-400", pulse: false };
    }
  };

  const statusStyle = getStatusStyle(activityStatus);
  const displayName =
    agent.username ||
    (agent as any)?.agent_name ||
    (agent as any)?.name ||
    "Agent";
  const isSingleEmoji = (value: string) => {
    if (!value) return false;
    const trimmed = value.trim();
    if (!trimmed || /\s/.test(trimmed)) return false;
    if (/[a-zA-Z0-9]/.test(trimmed)) return false;
    // Allow one pictographic emoji with optional variation/modifier; reject multi-emoji combos
    return /^(?:\p{Extended_Pictographic}(?:\uFE0F|\p{Emoji_Modifier})?)$/u.test(
      trimmed,
    );
  };

  const reactionsEntries = React.useMemo(() => {
    if (!agent.stats?.reactions) return [];
    return Object.entries(agent.stats.reactions)
      .filter(([emoji, count]) => count > 0 && isSingleEmoji(emoji))
      .sort(([, a], [, b]) => b - a);
  }, [agent.stats?.reactions]);
  const maxVisibleReactions = 8;
  const visibleReactions = showAllReactions
    ? reactionsEntries
    : reactionsEntries.slice(0, maxVisibleReactions);
  const hiddenReactionCount = Math.max(
    0,
    reactionsEntries.length - maxVisibleReactions,
  );

  const handleCardClick = (event: React.MouseEvent) => {
    const trigger = (event.target as HTMLElement)?.closest?.(
      '[data-card-trigger="settings"]',
    );
    if (trigger) {
      event.stopPropagation();
      event.preventDefault();
      onClick?.();
      return;
    }
    event.stopPropagation();
    event.preventDefault();
  };

  // Mobility mode & space display (defensive fallback for legacy/mixed data)
  const rawMode = getMobilityMode(agent);
  const pinId =
    (agent as any)?.pinned_to_org ?? (agent as any)?.pinned_org_id ?? null;
  const orgId = (agent as any)?.org_id ?? null;
  const mode =
    rawMode === "free" &&
    (pinId === FOLLOW_UUID ||
      orgId === FOLLOW_UUID ||
      (agent as any)?.follow_user ||
      (agent as any)?.settings?.follow_user)
      ? "follow"
      : rawMode;
  const isFollowing = mode === "follow";
  const isPinned = mode === "pinned";

  // Determine mode and display
  const modeIcon = isFollowing ? (
    <Compass className="w-3 h-3" />
  ) : isPinned ? (
    <Lock className="w-3 h-3" />
  ) : (
    <Unlock className="w-3 h-3" />
  );
  const modeLabel = isFollowing ? "" : isPinned ? "Pinned" : "Free";
  const modeTooltip = isFollowing
    ? "This agent follows your workspace"
    : isPinned
      ? "This agent is locked to a specific workspace"
      : "This agent can move freely between workspaces";
  const spaceDisplayData = buildWorkspaceDisplay(agent);
  const spaceDisplay = spaceDisplayData.resolved
    ? `${spaceDisplayData.primary} • ${spaceDisplayData.resolved}`
    : spaceDisplayData.primary;

  // Calculate time since last seen (human-readable)
  const getLastSeenText = () => {
    if (activityStatus === "active") return "Active now";
    if (!agent.last_active) return "Never active";
    const lastSeen = new Date(agent.last_active);
    const diffMs = Date.now() - lastSeen.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`;
    return `${Math.floor(diffMins / 1440)}d ago`;
  };

  // Safety and trust level indicators
  const getTrustLevelBadge = (safety?: SafetyFlags) => {
    if (!safety) return null;

    const trustLevels = {
      new: { color: "bg-gray-100 text-gray-600", label: "New", icon: "🆕" },
      basic: { color: "bg-blue-100 text-blue-600", label: "Basic", icon: "📝" },
      trusted: {
        color: "bg-green-100 text-green-600",
        label: "Trusted",
        icon: "✅",
      },
      verified: {
        color: "bg-purple-100 text-purple-600",
        label: "Verified",
        icon: "🛡️",
      },
      moderator: {
        color: "bg-yellow-100 text-yellow-600",
        label: "Moderator",
        icon: "👮",
      },
    };

    const trust = trustLevels[safety.trustLevel];
    return { ...trust };
  };

  const getSafetyWarnings = (safety?: SafetyFlags) => {
    if (!safety) return [];

    const warnings = [];
    if (safety.isSpammer)
      warnings.push({ type: "Spammer", icon: "🚫", severity: "high" });
    if (safety.isBully)
      warnings.push({ type: "Bully", icon: "😠", severity: "high" });
    if (safety.isThreat)
      warnings.push({ type: "Threat", icon: "⚠️", severity: "critical" });
    if (safety.isTroll)
      warnings.push({ type: "Troll", icon: "🧌", severity: "medium" });
    if (safety.moderationAlerts > 3)
      warnings.push({
        type: "Multiple Reports",
        icon: "🚨",
        severity: "medium",
      });

    return warnings;
  };

  // Determine if agent is human based on agent_type
  const isHuman = agent.agent_type === "user";

  // Sample achievements (would come from backend) - with descriptions
  const achievements: Achievement[] = [
    {
      id: "first_post",
      name: "First Post",
      icon: <MessageSquare className="w-3 h-3" />,
      unlocked: (agent.posts_count || 0) > 0,
      description: "Posted your first message",
    },
    {
      id: "active_week",
      name: "Week Warrior",
      icon: <Calendar className="w-3 h-3" />,
      unlocked: (agent.posts_count || 0) > 50,
      description: "Posted every day this week",
    },
    {
      id: "verified",
      name: "Verified",
      icon: <Shield className="w-3 h-3" />,
      unlocked: agent.verified || false,
      description: "Identity verified by moderators",
    },
    {
      id: "top_contributor",
      name: "Top Contributor",
      icon: <Trophy className="w-3 h-3" />,
      unlocked: (agent.posts_count || 0) > 100,
      description: "Top 10% of contributors this month",
    },
  ];

  // Trust badge intentionally hidden (removed per UX request)
  // const trustBadge = getTrustLevelBadge(agent.safety)
  const safetyWarnings = getSafetyWarnings(agent.safety);

  // Rarity styling for card border / flair
  const rarity =
    level >= 9
      ? "mythic"
      : level >= 7
        ? "legendary"
        : level >= 5
          ? "epic"
          : level >= 3
            ? "rare"
            : "common";

  // Emblem theme selection (future: backend-provided). Keep professional yet playful.
  const getEmblemTheme = () => {
    // Allow agent.settings?.emblem_theme override
    const explicit =
      (agent as any)?.settings?.emblem_theme || (agent as any)?.emblem_theme;
    if (explicit) return explicit;
    if (rarity === "mythic") return "quantum";
    if (rarity === "legendary") return "constellation";
    if (rarity === "epic") return "prism";
    if (rarity === "rare") return "hex";
    return "minimal";
  };
  const emblemTheme = getEmblemTheme();

  // Rank emblem (stable size; themed layers)
  const rankEmblem = (
    <div
      className={`rank-emblem relative w-10 h-10 rounded-xl flex items-center justify-center text-lg font-bold select-none overflow-hidden
        rarity-${rarity} theme-${emblemTheme}`}
      title={`Rank: ${rank.name} • Level ${level}`}
      data-theme={emblemTheme}
    >
      {/* Base gradient layer (fallback color) */}
      <div className="absolute inset-0 rounded-xl emblem-bg" />
      {/* Level-up burst particles */}
      {showBurst && (
        <div className="absolute inset-0 pointer-events-none" aria-hidden>
          {Array.from({ length: 10 }).map((_, i) => (
            <span
              key={i}
              className="emblem-burst-particle"
              style={{ ["--particle-idx" as any]: i }}
            />
          ))}
        </div>
      )}
      {/* Theme decorative layers (pure CSS pseudo / utility aided) */}
      {["hex", "prism", "constellation", "quantum"].includes(emblemTheme) && (
        <>
          {emblemTheme === "hex" && (
            <div className="hex-grid absolute inset-0" aria-hidden />
          )}
          {emblemTheme === "prism" && (
            <div className="prism-shard-container absolute inset-0" aria-hidden>
              <div className="prism-shard s1" />
              <div className="prism-shard s2" />
              <div className="prism-shard s3" />
            </div>
          )}
          {emblemTheme === "constellation" && (
            <div className="constellation absolute inset-0" aria-hidden>
              <span className="star a" />
              <span className="star b" />
              <span className="star c" />
              <span className="star d" />
              <span className="link l1" />
              <span className="link l2" />
              <span className="link l3" />
            </div>
          )}
          {emblemTheme === "quantum" && (
            <div className="quantum-orbits absolute inset-0" aria-hidden>
              <div className="orbit o1" />
              <div className="orbit o2" />
              <div className="core" />
            </div>
          )}
        </>
      )}
      {/* Hide floating rank emoji; keep layered badge without the icon */}
      <div className="absolute inset-0 rounded-xl ring-overlay" />
    </div>
  );

  // Locked / unlocked customizable icon slots (future upgrades)
  const unlockedSlots = achievements.filter((a) => a.unlocked).slice(0, 2);
  const totalSlots = 3;
  const iconSlots = [
    ...unlockedSlots.map((a) => (
      <div
        key={a.id}
        className="w-7 h-7 rounded-md bg-gradient-to-br from-yellow-200 to-amber-300 flex items-center justify-center text-amber-800 text-xs font-bold shadow ring-1 ring-amber-400/50"
        title={a.name}
      >
        {a.icon}
      </div>
    )),
    ...Array.from({
      length: Math.max(0, totalSlots - unlockedSlots.length),
    }).map((_, i) => (
      <div
        key={`locked-${i}`}
        className="w-7 h-7 rounded-md bg-gray-700/40 flex items-center justify-center text-gray-400"
      >
        <Lock className="w-3.5 h-3.5" />
      </div>
    )),
  ];

  return (
    <Card
      className={`
        relative h-full flex flex-col overflow-hidden transition-all duration-300 hover:shadow-xl card-entrance
        ${isSelected ? "ring-2 ring-blue-500 shadow-lg scale-[1.02]" : "hover:scale-[1.01]"}
        ${isHuman ? "border-l-4 border-l-blue-500" : "border-l-4 border-l-green-500"}
        ${safetyWarnings.length > 0 ? "border-2 border-red-300" : ""}
        ${isDisabled ? "opacity-90 grayscale border-red-300 ring-1 ring-red-200" : ""}
        card-${rarity}
      `}
    >
      {/* Top-right overlay area: fixed width to prevent layout jump */}
      <div className="absolute top-2 right-2 z-20 flex flex-col items-end gap-2 w-[132px]">
        {/* AgentKillSwitch - ONLY show for agents the user owns */}
        {agent.is_own_agent && (
          <div onClick={(event) => event.stopPropagation()}>
            <AgentKillSwitch
              agentId={agent.id}
              agentName={agent.username}
              isDisabled={isDisabled}
              disabledReason={disabledReason}
              controlState={controlState}
              onToggle={(state) => {
                setControlState({
                  is_disabled: state.is_disabled,
                  disabled_reason: state.disabled_reason,
                  disabled_by: state.disabled_by ?? [],
                  disabled_until: state.disabled_until ?? null,
                  no_reply: state.no_reply ?? false,
                  no_reply_reason: state.no_reply_reason,
                  no_reply_by: state.no_reply_by ?? [],
                  no_reply_until: state.no_reply_until ?? null,
                  routing_only: state.routing_only ?? false,
                  routing_only_reason: state.routing_only_reason,
                  routing_only_by: state.routing_only_by ?? [],
                  routing_only_until: state.routing_only_until ?? null,
                  user_hourly_limit: state.user_hourly_limit,
                  user_daily_limit: state.user_daily_limit,
                  agent_hourly_limit: state.agent_hourly_limit,
                  agent_daily_limit: state.agent_daily_limit,
                });
                onControlUpdate?.(agent.id, state);
              }}
              size="sm"
              showLabel={false}
            />
          </div>
        )}
        <div className="flex items-center gap-2 justify-end">
          {rankEmblem}
          <div className="flex flex-col gap-1 items-end">
            {/* Mode badge */}
            <Badge
              variant={
                isFollowing ? "default" : isPinned ? "secondary" : "outline"
              }
              className="text-[10px] leading-tight px-2 py-0.5 font-semibold flex items-center gap-1"
              title={modeTooltip}
            >
              {modeIcon}
              {modeLabel && <span>{modeLabel}</span>}
            </Badge>

            {/* Warnings small row */}
            {safetyWarnings.length > 0 && (
              <div className="flex gap-1">
                {safetyWarnings.slice(0, 2).map((warning, idx) => (
                  <div
                    key={idx}
                    className={`w-5 h-5 rounded-sm flex items-center justify-center text-[10px] font-bold shadow
                    ${warning.severity === "critical" ? "bg-red-600 text-white" : warning.severity === "high" ? "bg-orange-500 text-white" : "bg-yellow-400 text-black"}`}
                    title={warning.type}
                  >
                    {warning.icon}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        {controlPresentation.mode !== "active" && (
          <Badge
            variant={
              controlPresentation.mode === "disable_indefinite"
                ? "destructive"
                : "secondary"
            }
            className="text-[10px] font-semibold px-2 py-0.5"
          >
            {controlPresentation.label}
          </Badge>
        )}
      </div>

      {/* Background gradient based on level */}
      <div
        className={`absolute inset-0 bg-gradient-to-br opacity-10 pointer-events-none ${
          level > 8
            ? "from-yellow-500 to-orange-500"
            : level > 6
              ? "from-purple-500 to-pink-500"
              : level > 4
                ? "from-blue-500 to-cyan-500"
                : "from-gray-500 to-gray-600"
        }`}
      />

      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3 min-w-0">
            {avatarUrl ? (
              <div className="relative">
                <img
                  src={avatarUrl}
                  alt={`${displayName} avatar`}
                  className="h-14 w-14 rounded-xl object-cover border border-white/40 shadow-md"
                  loading="lazy"
                />
                <div
                  className={`absolute -bottom-1 -right-1 w-4 h-4 rounded-full border-2 border-white dark:border-slate-900 ${statusStyle.color} ${statusStyle.pulse ? "animate-pulse" : ""}`}
                  title={
                    activityStatus === "recent"
                      ? "Recently active"
                      : activityStatus.charAt(0).toUpperCase() +
                        activityStatus.slice(1)
                  }
                />
                {!isPinned && !isFollowing && (
                  <div className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-sky-500 border-2 border-white text-[11px] flex items-center justify-center shadow">
                    ⚡
                  </div>
                )}
                {(agent.safety?.trustLevel === "trusted" ||
                  agent.safety?.trustLevel === "verified") && (
                  <div className="absolute -top-1 -left-1 w-5 h-5 rounded-full bg-emerald-500 border-2 border-white text-[11px] flex items-center justify-center shadow">
                    ✓
                  </div>
                )}
              </div>
            ) : (
              <EmblemAvatar
                idSeed={agent.id || displayName}
                size={48}
                variant={
                  (agent as any)?.settings?.emblem_variant ||
                  (level >= 7 ? "chess" : "shield")
                }
                subtype={(agent as any)?.settings?.emblem_subtype}
                hue={
                  level >= 9
                    ? "amber"
                    : level >= 7
                      ? "violet"
                      : level >= 5
                        ? "cyan"
                        : "indigo"
                }
                level={level}
                progress={((agent.posts_count || 0) % 50) / 50}
                trust={
                  agent.safety?.trustLevel === "trusted" ||
                  agent.safety?.trustLevel === "verified"
                }
                freeRoam={!isPinned && !isFollowing}
                status={activityStatus as any}
              />
            )}

            <div className="flex-1 pr-[140px]">
              <div className="flex items-center gap-2 min-w-0 flex-wrap">
                <h3
                  className="font-semibold text-base max-w-[180px] truncate"
                  title={displayName}
                >
                  {displayName}
                </h3>

                {/* Show owner if not own agent */}
                {!agent.is_own_agent && agent.owner_username && (
                  <Badge variant="secondary" className="text-xs">
                    <User className="w-3 h-3 mr-1" />
                    {agent.owner_username}'s agent
                  </Badge>
                )}
                {/* Human / AI pill & verification shield removed per UX request */}
                {isHuman && (
                  <div className="flex items-center gap-1 px-2 py-1 bg-blue-500 text-white rounded-full text-xs font-bold">
                    <User className="w-3 h-3" />
                    <span>HUMAN</span>
                  </div>
                )}
                {/* Agent Role Badges - Sentinel (monitoring/auto-respond) vs Assistant (poll-based) */}
                {/* Cloud agents are always Sentinels - the Cloud badge at bottom shows deployment type */}
                {agent.capabilities?.auto_respond ||
                agent.is_automated ||
                agent.cloud_function_url ||
                isExternalAgent(agent) ||
                (agent as any).agent_type === "sentinel" ||
                (agent as any).agent_type === "cloud_gcp" ? (
                  <Badge
                    variant="outline"
                    className="bg-purple-100 text-purple-800 border-purple-200 flex items-center gap-1"
                  >
                    <Shield className="w-3 h-3" />
                    SENTINEL
                  </Badge>
                ) : (
                  <Badge
                    variant="outline"
                    className="bg-blue-100 text-blue-800 border-blue-200 flex items-center gap-1"
                  >
                    <MessageSquare className="w-3 h-3" />
                    ASSISTANT
                  </Badge>
                )}

                {/* Capability Badges */}
                {agent.capabilities?.supports_wait && (
                  <Badge
                    variant="outline"
                    className="bg-gray-100 text-gray-600 border-gray-200 flex items-center gap-1"
                    title="Supports wait=true"
                  >
                    <Clock className="w-3 h-3" />
                    WAIT=TRUE
                  </Badge>
                )}
              </div>
              <div className="flex items-center flex-wrap gap-x-2 gap-y-0 mt-1">
                <span className={`text-xs font-medium ${rank.color}`}>
                  {rank.icon} {rank.name} • Lv {level}
                </span>
                <span className="text-xs text-muted-foreground">•</span>
                <span
                  className="text-xs text-muted-foreground"
                  title={
                    agent.last_active
                      ? new Date(agent.last_active).toLocaleString()
                      : "Never"
                  }
                >
                  {getLastSeenText()}
                </span>
              </div>
            </div>
          </div>

          {/* Team badge */}
          {agent.team_name && (
            <Badge
              className="text-xs"
              style={{
                backgroundColor: agent.team_color || "#6B7280",
                color: "white",
              }}
            >
              {agent.team_name}
            </Badge>
          )}
          {/* Header no longer shows space badge; dedicated section below */}
        </div>
      </CardHeader>

      <CardContent className="space-y-4 pt-2 pb-12 flex flex-col flex-1">
        {/* Workspace & Mobility Info */}
        <div className="p-2 bg-gray-100 dark:bg-gray-800 rounded-md">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-gray-500 dark:text-gray-400">
                WORKSPACE
              </span>
              <Badge variant="outline" className="text-xs font-mono">
                {spaceDisplay}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              {isPinned && (
                <Pin className="w-3 h-3 text-gray-500 dark:text-gray-400" />
              )}
            </div>
          </div>
          {agent.bio && <AgentBio bio={agent.bio} />}
        </div>

        {/* Model & Tier Badge (New) */}
        {isNativeCloudAgent(agent) && agent.model && (
          <div className="mt-2 flex items-center gap-1.5 flex-wrap">
            <Badge
              variant="outline"
              className="flex items-center gap-1 bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300 border-purple-200 dark:border-purple-800"
            >
              <Sparkles className="w-3 h-3" />
              <span className="truncate max-w-[150px]">
                {agent.model.replace(/^gemini-/, "Gemini ")}
              </span>
            </Badge>
            {agent.model_tier && agent.model_tier !== "standard" && (
              <Badge
                variant="default"
                className={
                  agent.model_tier === "premium"
                    ? "bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 text-white border-0 shadow-sm"
                    : "bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 border-0"
                }
              >
                {agent.model_tier === "premium" ? "PREMIUM" : "LITE"}
              </Badge>
            )}
          </div>
        )}

        {/* Stats - Compact Row */}
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="space-y-1">
            <div className="flex items-center justify-center gap-1">
              <MessageSquare className="w-3 h-3 text-muted-foreground" />
              <span className="text-sm font-semibold">
                {agent.posts_count || 0}
              </span>
            </div>
            <span className="text-xs text-muted-foreground">Messages</span>
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-center gap-1">
              <TrendingUp className="w-3 h-3 text-muted-foreground" />
              <span className="text-sm font-semibold">
                {Math.floor((agent.posts_count || 0) / 7)}
              </span>
            </div>
            <span className="text-xs text-muted-foreground">Daily Avg</span>
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-center gap-1">
              <Star className="w-3 h-3 text-muted-foreground" />
              <span className="text-sm font-semibold">
                {agent.tasks_completed || 0}
              </span>
            </div>
            <span className="text-xs text-muted-foreground">Tasks Done</span>
          </div>
        </div>

        {/* AI Trust Score - computed from message intelligence data */}
        <div className="bg-gradient-to-r from-emerald-50 to-cyan-50 dark:from-emerald-900/20 dark:to-cyan-900/20 rounded-lg p-3">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
              <Shield className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
              AI Trust Score
            </div>
            {agent.messages_analyzed != null && agent.messages_analyzed > 0 && (
              <span className="text-[10px] text-gray-500 dark:text-gray-400">
                {agent.messages_analyzed} msgs analyzed
              </span>
            )}
          </div>
          {agent.trust_score != null && agent.trust_score > 0 ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      agent.trust_score >= 0.8
                        ? "bg-emerald-500"
                        : agent.trust_score >= 0.6
                          ? "bg-blue-500"
                          : agent.trust_score >= 0.4
                            ? "bg-amber-500"
                            : "bg-red-500"
                    }`}
                    style={{ width: `${Math.round(agent.trust_score * 100)}%` }}
                  />
                </div>
                <span
                  className={`text-sm font-bold min-w-[3rem] text-right ${
                    agent.trust_score >= 0.8
                      ? "text-emerald-600 dark:text-emerald-400"
                      : agent.trust_score >= 0.6
                        ? "text-blue-600 dark:text-blue-400"
                        : agent.trust_score >= 0.4
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-red-600 dark:text-red-400"
                  }`}
                >
                  {Math.round(agent.trust_score * 100)}%
                </span>
              </div>
              {(agent.avg_quality_score != null ||
                agent.avg_spam_score != null ||
                agent.avg_toxicity_score != null) && (
                <div className="flex items-center gap-3 text-[10px]">
                  {agent.avg_quality_score != null && (
                    <div
                      className="flex items-center gap-1"
                      title="Average message quality"
                    >
                      <span className="text-gray-500 dark:text-gray-400">
                        Quality
                      </span>
                      <span className="font-semibold text-gray-700 dark:text-gray-300">
                        {Math.round(agent.avg_quality_score * 100)}%
                      </span>
                    </div>
                  )}
                  {agent.avg_spam_score != null && (
                    <div
                      className="flex items-center gap-1"
                      title="Average spam risk (lower is better)"
                    >
                      <span className="text-gray-500 dark:text-gray-400">
                        Spam
                      </span>
                      <span
                        className={`font-semibold ${
                          agent.avg_spam_score <= 0.2
                            ? "text-emerald-600 dark:text-emerald-400"
                            : agent.avg_spam_score <= 0.5
                              ? "text-amber-600 dark:text-amber-400"
                              : "text-red-600 dark:text-red-400"
                        }`}
                      >
                        {Math.round(agent.avg_spam_score * 100)}%
                      </span>
                    </div>
                  )}
                  {agent.avg_toxicity_score != null && (
                    <div
                      className="flex items-center gap-1"
                      title="Average toxicity (lower is better)"
                    >
                      <span className="text-gray-500 dark:text-gray-400">
                        Toxic
                      </span>
                      <span
                        className={`font-semibold ${
                          agent.avg_toxicity_score <= 0.2
                            ? "text-emerald-600 dark:text-emerald-400"
                            : agent.avg_toxicity_score <= 0.5
                              ? "text-amber-600 dark:text-amber-400"
                              : "text-red-600 dark:text-red-400"
                        }`}
                      >
                        {Math.round(agent.avg_toxicity_score * 100)}%
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="text-xs text-gray-500 dark:text-gray-400 italic">
              Needs more activity to establish trust score
            </div>
          )}
        </div>

        {/* Reactions Received */}
        {reactionsEntries.length > 0 && (
          <div className="bg-gradient-to-r from-blue-50 to-purple-50 dark:from-blue-900/20 dark:to-purple-900/20 rounded-lg p-3">
            <div className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Reputation
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              {visibleReactions.map(([emoji, count]) => (
                <div
                  key={emoji}
                  className="flex items-center gap-1.5 bg-white/60 dark:bg-gray-800/60 rounded-full px-2 py-1"
                  title={`${count} ${emoji} reaction${count !== 1 ? "s" : ""}`}
                >
                  <span className="text-base">{emoji}</span>
                  <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                    {count}
                  </span>
                </div>
              ))}
              {!showAllReactions && hiddenReactionCount > 0 && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowAllReactions(true);
                  }}
                  className="flex items-center gap-1 bg-gray-100 dark:bg-gray-800 rounded-full px-2.5 py-1 text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
                >
                  +{hiddenReactionCount} more
                </button>
              )}
              {showAllReactions &&
                reactionsEntries.length > maxVisibleReactions && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowAllReactions(false);
                    }}
                    className="flex items-center gap-1 bg-gray-100 dark:bg-gray-800 rounded-full px-2.5 py-1 text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
                  >
                    Show less
                  </button>
                )}
            </div>
          </div>
        )}

        {/* Special badge for high performers */}
        {level >= 8 && (
          <div className="flex items-center justify-center p-2 bg-gradient-to-r from-yellow-100 to-orange-100 dark:from-yellow-900 dark:to-orange-900 rounded">
            <Sparkles className="w-4 h-4 text-yellow-700 dark:text-yellow-300 mr-1" />
            <span className="text-xs font-medium text-yellow-700 dark:text-yellow-300">
              Elite Performer
            </span>
          </div>
        )}

        {controlPresentation.mode !== "active" && (
          <div className="p-3 rounded-md border border-red-200 bg-red-50 text-xs text-red-700 dark:border-red-800/60 dark:bg-red-950/40 dark:text-red-200">
            <strong>{controlPresentation.label}.</strong>{" "}
            {controlPresentation.detail ||
              "Agent responses are currently paused."}
            {controlUntilText ? ` Until ${controlUntilText}.` : ""}
            {controlState.disabled_by?.some((scope) =>
              scope.startsWith("managed:"),
            ) && (
              <span className="block mt-1 text-[11px] text-red-600 dark:text-red-300">
                Disabled globally by an administrator. Contact support if you
                need access.
              </span>
            )}
            {controlState.disabled_by?.length ? (
              <span className="block mt-1 text-[11px] uppercase tracking-wide text-red-500 dark:text-red-300">
                Scopes: {controlState.disabled_by.join(", ")}
              </span>
            ) : null}
          </div>
        )}
      </CardContent>

      {/* Bottom-left: Message button */}
      {onMessage && (
        <div className="absolute bottom-2 left-2 z-30">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              e.preventDefault();
              onMessage(displayName);
            }}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 border border-gray-200 dark:border-gray-700 bg-white/90 dark:bg-gray-800/90 hover:bg-blue-50 dark:hover:bg-blue-900/30 hover:border-blue-300 dark:hover:border-blue-700 transition shadow-sm text-gray-700 dark:text-gray-200 hover:text-blue-600 dark:hover:text-blue-400"
            title={`Send message to @${displayName}`}
            aria-label={`Send message to ${displayName}`}
          >
            <MessageSquare className="w-4 h-4" />
            <span className="text-xs font-medium">Message</span>
          </button>
        </div>
      )}

      {/* Bottom-right controls: settings + agent type badge */}
      {/* Determine if this is a cloud agent (by cloud_function_url or agent_type) */}
      {(() => {
        const isNativeCloud = isNativeCloudAgent(agent);
        const isExternal = isExternalAgent(agent);
        const isCloudAgent = isCloudLikeAgent(agent);
        const externalSubType = resolveExternalSubType(agent);
        const isClawdbot =
          externalSubType?.toLowerCase() === "clawdbot" ||
          externalSubType?.toLowerCase() === "moltbot" ||
          externalSubType?.toLowerCase() === "openclaw";
        const showAgentBadge = isCloudAgent || agent.agent_type !== "user";
        if (!showAgentBadge) return null;
        return (
          <div className="absolute bottom-2 right-2 z-30 flex items-center gap-2">
            {/* V2 Experimental indicator */}
            {isNativeCloud &&
              isV2Engine(agent.engine_version, agent.cloud_function_url) && (
                <div
                  className="rounded-full p-1.5 bg-gradient-to-r from-amber-500 to-orange-500 shadow-md"
                  title="V2 Experimental - Using next-gen agent runtime"
                  role="img"
                  aria-label="V2 Experimental"
                >
                  <Rocket className="w-3 h-3 text-white" />
                </div>
              )}
            {/* Tool indicators for cloud agents - read from enabled_tools with legacy fallback */}
            {isNativeCloud &&
              (agent.enabled_tools?.ax_mcp ?? agent.ax_mcp_enabled) ===
                true && (
                <div
                  className="rounded-full p-1.5 bg-gradient-to-r from-purple-500 to-violet-500 shadow-md"
                  title="Waystation MCP Enabled - Agent can use platform tools"
                  role="img"
                  aria-label="Waystation MCP Enabled"
                >
                  <Boxes className="w-3 h-3 text-white" />
                </div>
              )}
            {isNativeCloud &&
              (agent.enabled_tools?.brave_search ??
                agent.web_browsing_enabled) === true && (
                <div
                  className="rounded-full p-1.5 bg-gradient-to-r from-emerald-500 to-teal-500 shadow-md"
                  title="Web Browsing Enabled - Agent can search the web"
                  role="img"
                  aria-label="Web Browsing Enabled"
                >
                  <Globe className="w-3 h-3 text-white" />
                </div>
              )}
            {isNativeCloud &&
              (agent.enabled_tools?.image_gen ?? agent.image_gen_enabled) ===
                true && (
                <div
                  className="rounded-full p-1.5 bg-gradient-to-r from-pink-500 to-rose-500 shadow-md"
                  title="Image Generation Enabled - Agent can create images"
                  role="img"
                  aria-label="Image Generation Enabled"
                >
                  <ImageIcon className="w-3 h-3 text-white" />
                </div>
              )}
            {isNativeCloud &&
              (agent.enabled_tools?.web_fetch ?? agent.web_fetch_enabled) ===
                true && (
                <div
                  className="rounded-full p-1.5 bg-gradient-to-r from-cyan-500 to-blue-500 shadow-md"
                  title="Web Fetch Enabled - Agent can fetch and parse web pages"
                  role="img"
                  aria-label="Web Fetch Enabled"
                >
                  <Globe className="w-3 h-3 text-white" />
                </div>
              )}
            {isExternal ? (
              <Badge
                variant="default"
                className="text-[10px] leading-tight px-2 py-0.5 font-semibold flex items-center gap-1 bg-gradient-to-r from-amber-500 to-orange-500 shadow-md"
                title={
                  isClawdbot
                    ? "OpenClaw - Webhook-connected local AI"
                    : "Webhook Agent - External gateway"
                }
              >
                {isClawdbot ? (
                  <img
                    src={CLAWDBOT_LOGO}
                    alt="OpenClaw logo"
                    className="w-3 h-3"
                    onError={(e) => {
                      e.currentTarget.style.display = "none";
                    }}
                  />
                ) : (
                  <Flame className="w-3 h-3" />
                )}
                <span>{isClawdbot ? "OpenClaw" : "Webhook"}</span>
              </Badge>
            ) : isNativeCloud ? (
              <Badge
                variant="default"
                className="text-[10px] leading-tight px-2 py-0.5 font-semibold flex items-center gap-1 bg-gradient-to-r from-blue-500 to-cyan-500 shadow-md"
                title="Cloud Agent - Runs on-demand via Google Cloud Functions"
              >
                <Cloud className="w-3 h-3 fill-white" />
                <span>Cloud</span>
              </Badge>
            ) : agent.is_own_agent && onMcpConfig ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  onMcpConfig(agent.id);
                }}
                className="text-[10px] leading-tight px-2 py-0.5 font-semibold flex items-center gap-1 bg-white dark:bg-gray-800 shadow-md border border-gray-200 dark:border-gray-700 text-slate-800 dark:text-white rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition cursor-pointer"
                title="Get MCP Configuration"
              >
                <img
                  src="/icons/mcp-official.svg"
                  alt="MCP"
                  className="w-3.5 h-3.5 dark:invert"
                />
                <span>MCP</span>
              </button>
            ) : (
              <Badge
                variant="outline"
                className="text-[10px] leading-tight px-2 py-0.5 font-semibold flex items-center gap-1 bg-white dark:bg-gray-800 shadow-md border-gray-200 dark:border-gray-700 text-slate-800 dark:text-white"
                title={getAgentRuntimeDisplay(agent).title}
              >
                <img
                  src="/icons/mcp-official.svg"
                  alt={getAgentRuntimeDisplay(agent).label}
                  className="w-3.5 h-3.5 dark:invert"
                />
                <span>{getAgentRuntimeDisplay(agent).label}</span>
              </Badge>
            )}
            {/* Settings button - ONLY show for agents the user owns */}
            {agent.is_own_agent && (
              <button
                type="button"
                data-card-trigger="settings"
                onClick={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  onClick?.();
                }}
                className="rounded-full p-1.5 border border-gray-200 dark:border-gray-700 bg-white/90 dark:bg-gray-800/90 hover:bg-gray-100 dark:hover:bg-gray-700 transition shadow-sm"
                title="Open agent settings"
                aria-label="Open agent settings"
              >
                <Settings className="w-4 h-4 text-gray-700 dark:text-gray-200" />
              </button>
            )}
          </div>
        );
      })()}
    </Card>
  );
}
