import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import config from "@/config/environment";
import { AgentHoverCard } from "@/components/AgentHoverCard";
import { humanizeAgentType } from "@/lib/display-utils";
import {
  PendingFileChip,
  type PendingFile,
  type UploadProgress,
} from "@/components/ChatInputPendingFileChip";
/**
 * MOBILE_SCROLL_PADDING_FIX / IPHONE_VIEWPORT_SAFE_AREA
 * ----------------------------------------------------
 * ChatInput exports its dynamic rendered height (including reply bars, quick mention rows, etc.)
 * via the CSS variable --chat-input-height (see ResizeObserver below). MessageList consumes this
 * to create a bottom spacer & conditional padding on iPhone so the final messages are never
 * obscured behind the input (address bar collapse, safe-area, keyboard transitions).
 * If you tweak layout here ALSO search for the same token in MessageList.tsx to keep logic aligned.
 * ----------------------------------------------------
 */
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";

// Dialog imports removed — validation dialog eliminated
import {
  Send,
  X,
  Clock,
  AlertTriangle,
  Hash,
  Plus,
  MoreHorizontal,
  Paperclip,
  ChevronUp,
  ChevronDown,
  MessageSquareShare,
} from "lucide-react";
import { api } from "@/lib/api-clean";
import { storage } from "@/lib/storage";
import { storeUploadInContext } from "@/lib/space-agent-api";
import {
  CONCIERGE_HANDLE,
  extractMentionUsernames as extractMentionUsernamesFromText,
  getKnownMentionedAgentUsernames as getKnownMentionedAgentUsernamesFromText,
  normalizeHandle,
  planStickyRouting,
  resolveMentionedAgentIds as resolveMentionedAgentIdsFromUsernames,
} from "./chat-input-routing";
// Emoji cluster pattern (VS16/ZWJ aware)
const EMOJI_CLUSTER =
  /(?:\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?(?:\u200D\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?)?)/gu;
const MAX_REACTION_EMOJI_TYPES = 10; // cap per single reaction message
const MAX_UPLOAD_FILE_SIZE_BYTES = 10 * 1024 * 1024;
const ACCEPTED_UPLOAD_ATTR =
  "image/png,image/jpeg,image/gif,image/webp,text/plain,text/markdown,text/csv,application/pdf,application/json,application/zip,.txt,.md,.markdown,.csv,.pdf,.json,.zip";
const ACCEPTED_UPLOAD_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".pdf",
  ".json",
  ".zip",
]);
const ACCEPTED_UPLOAD_MIME_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "text/plain",
  "text/markdown",
  "text/csv",
  "application/pdf",
  "application/json",
  "application/zip",
  "application/x-zip-compressed",
]);
import { HelpDialog } from "@/components/HelpDialog";

function getFileExtension(name: string) {
  const dotIndex = name.lastIndexOf(".");
  return dotIndex >= 0 ? name.slice(dotIndex).toLowerCase() : "";
}

function isImageUpload(file: File) {
  return (
    file.type.startsWith("image/") ||
    [".png", ".jpg", ".jpeg", ".gif", ".webp"].includes(
      getFileExtension(file.name),
    )
  );
}

function isSupportedUpload(file: File) {
  return (
    ACCEPTED_UPLOAD_MIME_TYPES.has(file.type) ||
    ACCEPTED_UPLOAD_EXTENSIONS.has(getFileExtension(file.name))
  );
}

function buildPendingFile(file: File): PendingFile {
  if (isImageUpload(file)) {
    return {
      kind: "image",
      file,
      preview: URL.createObjectURL(file),
    };
  }

  return {
    kind: "file",
    file,
    preview: null,
  };
}

function buildUploadedFileMarkdown(pendingFile: PendingFile, url: string) {
  const label = pendingFile.file.name
    .replace(/\[/g, "\\[")
    .replace(/\]/g, "\\]");
  return pendingFile.kind === "image"
    ? `![${label}](${url})`
    : `[${label}](${url})`;
}
type UploadedAttachmentReference = {
  id: string;
  attachment_id?: string;
  filename: string;
  name: string;
  content_type: string;
  mime_type: string;
  size_bytes: number;
  size: number;
  url: string;
  context_key?: string | null;
  uploaded_at: string;
  upload_origin: "chat_input";
};

function buildAttachmentMetadata(attachment: UploadedAttachmentReference) {
  const contextUpload = attachment.context_key
    ? [
        {
          key: attachment.context_key,
          attachment_id: attachment.attachment_id || attachment.id,
          filename: attachment.filename,
          content_type: attachment.content_type,
          url: attachment.url,
        },
      ]
    : undefined;

  return {
    attachments: [attachment],
    accepted_attachments: [attachment],
    ...(contextUpload ? { context_uploads: contextUpload } : {}),
  };
}

function rankTimestamp(value?: string) {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

interface Agent {
  id?: string;
  username: string;
  agent_type: string;
  last_activity?: string;
  post_count?: number;
  org_id?: string;
  team_id?: string;
  team_name?: string;
  team_color?: string;
}

interface RecentAgentActivity {
  username: string;
  lastMessageAt: string;
  messageCount: number;
}

export interface QuickActionAgentOption {
  username: string;
  agent_type: string;
  color: string;
  _agentRef?: Agent;
  activityScore: number;
  frequencyScore: number;
}

export interface QuickActionRowAgent extends QuickActionAgentOption {
  isConcierge?: boolean;
  isCurrentRoute?: boolean;
}

export interface QuickActionGroupOption {
  id: string;
  name: string;
  color: string;
  members: string[];
}

function compareQuickActionAgents(
  a: QuickActionAgentOption,
  b: QuickActionAgentOption,
) {
  if (b.activityScore !== a.activityScore) {
    return b.activityScore - a.activityScore;
  }
  if (b.frequencyScore !== a.frequencyScore) {
    return b.frequencyScore - a.frequencyScore;
  }
  return a.username.localeCompare(b.username);
}

export function buildQuickActionRowAgents(
  currentRouteQuickAction: QuickActionRowAgent,
  curatedQuickActionAgents: QuickActionAgentOption[],
) {
  const ordered: QuickActionRowAgent[] = [];
  const seen = new Set<string>();
  const currentRouteHandle = normalizeHandle(
    currentRouteQuickAction.username,
  ).toLowerCase();
  if (currentRouteHandle) seen.add(currentRouteHandle);

  for (const agent of curatedQuickActionAgents) {
    const normalized = normalizeHandle(agent.username).toLowerCase();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    ordered.push(agent);
  }

  return ordered.slice(0, 6);
}

interface Team {
  id: string;
  name: string;
  description: string;
  color: string;
  agents: string[];
}

export function buildQuickActionGroupOptions(
  teams: Team[],
  agents: Agent[],
  currentOrgId?: string,
): QuickActionGroupOption[] {
  return teams
    .map((team) => {
      const memberNames = agents
        .filter((agent) => {
          if (currentOrgId && agent.org_id !== currentOrgId) return false;
          return (
            agent.team_id === team.id ||
            team.agents.includes(normalizeHandle(agent.username)) ||
            team.agents.includes(agent.id || "")
          );
        })
        .map((agent) => normalizeHandle(agent.username))
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b));

      return {
        id: team.id,
        name: team.name,
        color: team.color,
        members: memberNames,
      };
    })
    .filter((group) => group.members.length > 0);
}

interface ChatInputProps {
  replyToPost?: {
    id: number;
    content: string;
    username: string;
    waiting_since?: string;
  } | null;
  onReplyPosted?: () => void;
  onClearReply?: () => void;
  // Task 48ae545f — Share mirrors the Reply pattern. When a card Share button
  // is clicked, AxPlatformShell sets this state and ChatInput renders a
  // "Sharing: …" context bar identical in behavior to the reply bar. On send,
  // the posted message carries the existing `metadata.forward` contract so
  // receivers can render or resolve the shared object natively.
  forwardingCard?: ChatInputForwardingCard | null;
  onClearForward?: () => void;
  agents?: Agent[];
  currentOrgId?: string;
  analytics?: {
    hashtags: Record<string, number>;
  };
  recentAgentActivity?: RecentAgentActivity[];
  isPersonalSpace?: boolean;
  /** Current user's username - excluded from quick mentions */
  viewerUsername?: string;
}

export type ChatInputForwardingCard = {
  cardId: string;
  cardType?: string | null;
  sourceMessageId?: string;
  title: string;
  summary?: string | null;
  resourceType?: string | null;
  resourceId?: string | null;
  resourceUri?: string | null;
  taskId?: string | null;
  contextKey?: string | null;
  attachments?: unknown[] | null;
  references?: unknown[] | null;
};

export function buildForwardMetadata(
  forwardingCard: ChatInputForwardingCard | null | undefined,
) {
  if (!forwardingCard) return {};
  const attachments =
    Array.isArray(forwardingCard.attachments) &&
    forwardingCard.attachments.length > 0
      ? forwardingCard.attachments
      : null;
  const references =
    Array.isArray(forwardingCard.references) &&
    forwardingCard.references.length > 0
      ? forwardingCard.references
      : null;
  return {
    forward: {
      intent: "share",
      source_card_id: forwardingCard.cardId,
      ...(forwardingCard.cardType
        ? { card_type: forwardingCard.cardType }
        : {}),
      ...(forwardingCard.sourceMessageId
        ? { source_message_id: forwardingCard.sourceMessageId }
        : {}),
      ...(forwardingCard.resourceType
        ? { resource_type: forwardingCard.resourceType }
        : {}),
      ...(forwardingCard.resourceId
        ? { resource_id: forwardingCard.resourceId }
        : {}),
      ...(forwardingCard.resourceUri
        ? { resource_uri: forwardingCard.resourceUri }
        : {}),
      ...(forwardingCard.taskId ? { task_id: forwardingCard.taskId } : {}),
      ...(forwardingCard.contextKey
        ? { context_key: forwardingCard.contextKey }
        : {}),
      title: forwardingCard.title,
      ...(forwardingCard.summary ? { summary: forwardingCard.summary } : {}),
      ...(attachments ? { attachments } : {}),
      ...(references ? { references } : {}),
    },
  };
}

export function buildChatInputSendOptions({
  mentionedAgentIds,
  uploadMetadata,
  forwardingCard,
}: {
  mentionedAgentIds: string[];
  uploadMetadata?: Record<string, unknown>;
  forwardingCard?: ChatInputForwardingCard | null;
}) {
  const metadata = {
    ...(uploadMetadata || {}),
    ...buildForwardMetadata(forwardingCard),
  };
  return {
    mentionedAgentIds,
    ...(Object.keys(metadata).length > 0 ? { metadata } : {}),
  };
}

export function ChatInput({
  replyToPost,
  onReplyPosted,
  onClearReply,
  forwardingCard,
  onClearForward,
  agents = [],
  currentOrgId,
  analytics = { hashtags: {} },
  recentAgentActivity = [],
  isPersonalSpace: _isPersonalSpace = false,
  viewerUsername,
}: ChatInputProps) {
  const queryClient = useQueryClient();
  // Restore draft from localStorage
  const draftKey = `ax_draft:${currentOrgId || "default"}`;
  const [message, setMessage] = useState(() => {
    try {
      return localStorage.getItem(draftKey) || "";
    } catch {
      return "";
    }
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { toast } = useToast();
  const [showMentions, setShowMentions] = useState(false);
  const [showHashtags, setShowHashtags] = useState(false);
  // Quick actions collapsed by default, persist collapse state
  const [quickActionsCollapsed, setQuickActionsCollapsed] = useState(() => {
    try {
      const saved = localStorage.getItem("ax_quick_collapsed");
      if (saved === null) return true;
      return saved === "true";
    } catch {
      return true;
    }
  });
  const [selectedTeam, setSelectedTeam] = useState<string>("all");
  const [selectedQuickActionGroupId, setSelectedQuickActionGroupId] = useState<
    string | null
  >(null);
  const [mentionQuery, setMentionQuery] = useState("");
  const [hashtagQuery, setHashtagQuery] = useState("");
  const [mentionPosition, setMentionPosition] = useState({ start: 0, end: 0 });
  const [hashtagPosition, setHashtagPosition] = useState({ start: 0, end: 0 });
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [filteredAgents, setFilteredAgents] = useState<Agent[]>([]);
  const [filteredTopics, setFilteredTopics] = useState<string[]>([]);
  const [selectedAgentIndex, setSelectedAgentIndex] = useState(0);
  const [selectedTopicIndex, setSelectedTopicIndex] = useState(0);

  const stripHandle = useCallback(
    (value: string) => normalizeHandle(value),
    [],
  );
  const extractMentionUsernames = useCallback(
    (text: string) => extractMentionUsernamesFromText(text),
    [],
  );

  const resolveAgentUsernameToId = useMemo(() => {
    const map = new Map<string, string>();
    for (const agent of agents) {
      if (agent?.username && agent?.id) {
        map.set(stripHandle(agent.username).toLowerCase(), agent.id);
      }
    }
    return map;
  }, [agents, stripHandle]);

  const resolveMentionedAgentIds = useCallback(
    (usernames: string[]) => {
      return resolveMentionedAgentIdsFromUsernames(
        usernames,
        resolveAgentUsernameToId,
      );
    },
    [resolveAgentUsernameToId],
  );

  const getKnownMentionedAgentUsernames = useCallback(
    (text: string) =>
      getKnownMentionedAgentUsernamesFromText(text, resolveAgentUsernameToId),
    [resolveAgentUsernameToId],
  );

  const defaultAgentStorageKey = `ax_default_agent:${currentOrgId || "default"}`;
  const [defaultAgentUsername, setDefaultAgentUsername] = useState<
    string | null
  >(() => {
    try {
      const saved = localStorage.getItem(defaultAgentStorageKey);
      return saved ? stripHandle(saved) : null;
    } catch {
      return null;
    }
  });

  const applyDefaultAgentSelection = useCallback(
    (username: string | null, options?: { closeMenus?: boolean }) => {
      const normalized = username ? stripHandle(username) : null;
      setDefaultAgentUsername(normalized || null);
      setTimeout(() => {
        textareaRef.current?.focus();
      }, 0);
      if (options?.closeMenus) {
        setShowMoreQuickMentions(false);
        setShowPlusDropdown(false);
      }
    },
    [stripHandle],
  );

  // Get real topics from message analytics (sorted by frequency)
  const availableTopics = useMemo(() => {
    return Object.entries(analytics.hashtags)
      .map(([hashtag, count]) => ({
        topic: hashtag.startsWith("#") ? hashtag.substring(1) : hashtag,
        count,
      }))
      .sort((a, b) => b.count - a.count) // Most used topics first
      .map((item) => item.topic);
  }, [analytics.hashtags]);

  const [showMoreQuickMentions, setShowMoreQuickMentions] = useState(false);
  const [showPlusDropdown, setShowPlusDropdown] = useState(false);
  const [showHelpDialog, setShowHelpDialog] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pendingFile, setPendingFile] = useState<PendingFile | null>(null);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress>({
    phase: "idle",
    percent: 0,
  });

  // File Upload Handling
  const stagePendingFile = useCallback(
    (file: File) => {
      if (!isSupportedUpload(file)) {
        toast({
          title: "Unsupported file type",
          description:
            "Attach PNG, JPG, GIF, WEBP, TXT, MD, CSV, PDF, or JSON files.",
          variant: "destructive",
        });
        return;
      }

      if (file.size > MAX_UPLOAD_FILE_SIZE_BYTES) {
        toast({
          title: "File too large",
          description: "Files must be under 10MB.",
          variant: "destructive",
        });
        return;
      }

      setUploadProgress({ phase: "idle", percent: 0 });
      setPendingFile(buildPendingFile(file));
    },
    [toast],
  );

  const clearPendingFile = useCallback(() => {
    setUploadProgress({ phase: "idle", percent: 0 });
    setPendingFile(null);
  }, []);

  useEffect(() => {
    if (pendingFile?.kind !== "image") return;

    return () => {
      URL.revokeObjectURL(pendingFile.preview);
    };
  }, [pendingFile]);

  const handlePaste = async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.indexOf("image") !== -1) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) {
          stagePendingFile(file);
        }
        return;
      }
    }
  };

  const uploadFile = async (
    file: File,
    onProgress?: (percent: number) => void,
  ): Promise<{ url: string; attachment_id?: string }> => {
    const token = storage.getUserToken();
    const endpoint = `${config.apiUrl}/api/v1/uploads/`;

    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", endpoint);
      if (token) {
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      }

      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        onProgress?.(
          Math.min(100, Math.round((event.loaded / event.total) * 100)),
        );
      });

      xhr.onerror = () => {
        reject(new Error("Upload failed"));
      };

      xhr.onload = () => {
        let payload: Record<string, string> = {};
        try {
          payload = xhr.responseText
            ? (JSON.parse(xhr.responseText) as Record<string, string>)
            : {};
        } catch {
          payload = {};
        }

        if (xhr.status >= 200 && xhr.status < 300 && payload.url) {
          onProgress?.(100);
          resolve({
            url: payload.url,
            attachment_id: payload.attachment_id || payload.id,
          });
          return;
        }

        reject(new Error(payload.detail || "Upload failed"));
      };

      const formData = new FormData();
      formData.append("file", file);
      xhr.send(formData);
    });
  };

  // Generate consistent color for an agent based on username hash
  const generateAgentColor = (username: string, index: number): string => {
    const colors = [
      "#8B5CF6", // Purple
      "#06B6D4", // Cyan
      "#10B981", // Green
      "#F59E0B", // Yellow
      "#EF4444", // Red
      "#7C3AED", // Violet
      "#3B82F6", // Blue
    ];
    return colors[index % colors.length];
  };

  // Quick mention agents - derived from LiveMonitor's recent message activity
  const quickMentionAgents = useMemo(() => {
    const viewerLower = viewerUsername?.toLowerCase();

    let result: QuickActionAgentOption[] = [];

    if (recentAgentActivity && recentAgentActivity.length > 0) {
      result = recentAgentActivity
        .filter((activity) => {
          const username = stripHandle(activity.username || "").toLowerCase();
          return username !== viewerLower;
        })
        .map((activity, index) => {
          const uname = stripHandle(activity.username || "");
          const fullAgent = agents?.find(
            (a) => a.username?.toLowerCase() === uname.toLowerCase(),
          );
          const activityScore = rankTimestamp(activity.lastMessageAt);
          const frequencyScore =
            activity.messageCount || fullAgent?.post_count || 0;
          return {
            username: uname,
            agent_type: fullAgent?.agent_type || "Agent",
            color: generateAgentColor(activity.username || "", index),
            _agentRef: fullAgent,
            activityScore,
            frequencyScore,
          };
        })
        .filter((agent) => agent.username)
        .sort(compareQuickActionAgents)
        .slice(0, 10);
    } else {
      if (!agents || agents.length === 0) return [];

      const agentsInSpace = currentOrgId
        ? agents.filter((agent) => agent.org_id === currentOrgId)
        : agents;

      result = agentsInSpace
        .filter((agent) => agent?.username)
        .map((agent, index) => {
          const activityScore = rankTimestamp(agent.last_activity);
          const frequencyScore = agent.post_count || 0;
          return {
            username: agent.username,
            agent_type: agent.agent_type || "Agent",
            color: generateAgentColor(agent.username, index),
            _agentRef: agent,
            activityScore,
            frequencyScore,
          };
        })
        .sort(compareQuickActionAgents)
        .slice(0, 10);
    }

    return result;
  }, [recentAgentActivity, agents, currentOrgId, viewerUsername, stripHandle]);

  const normalizedDefaultAgent = defaultAgentUsername
    ? stripHandle(defaultAgentUsername).toLowerCase()
    : "";

  const quickActionAgentOptions = useMemo(() => {
    const merged = new Map<string, QuickActionAgentOption>();

    for (const agent of quickMentionAgents) {
      const normalized = stripHandle(agent.username).toLowerCase();
      if (!normalized || merged.has(normalized)) continue;
      merged.set(normalized, agent);
    }

    for (const agent of availableAgents) {
      const normalized = stripHandle(agent.username).toLowerCase();
      if (!normalized || merged.has(normalized)) continue;
      const activityScore = rankTimestamp(agent.last_activity);
      const frequencyScore = agent.post_count || 0;
      merged.set(normalized, {
        username: stripHandle(agent.username),
        agent_type: agent.agent_type || "Agent",
        color:
          agent.team_color || generateAgentColor(agent.username, merged.size),
        _agentRef: agent,
        activityScore,
        frequencyScore,
      });
    }

    return Array.from(merged.values()).sort(compareQuickActionAgents);
  }, [availableAgents, quickMentionAgents, stripHandle]);

  const defaultAgentCard = useMemo(() => {
    if (!normalizedDefaultAgent) return null;
    return (
      quickActionAgentOptions.find(
        (agent) =>
          stripHandle(agent.username).toLowerCase() === normalizedDefaultAgent,
      ) || null
    );
  }, [normalizedDefaultAgent, quickActionAgentOptions, stripHandle]);
  const defaultAgentLabel = defaultAgentCard?.username || defaultAgentUsername;
  const isRoutingToConcierge = !defaultAgentLabel;
  const currentRouteQuickAction = useMemo<QuickActionRowAgent>(() => {
    if (isRoutingToConcierge) {
      return {
        username: CONCIERGE_HANDLE,
        agent_type: "Concierge",
        color: "#111827",
        activityScore: Number.MAX_SAFE_INTEGER,
        frequencyScore: Number.MAX_SAFE_INTEGER,
        isConcierge: true,
        isCurrentRoute: true,
      };
    }

    if (defaultAgentCard) {
      return {
        ...defaultAgentCard,
        isCurrentRoute: true,
      };
    }

    return {
      username: defaultAgentLabel || CONCIERGE_HANDLE,
      agent_type: "Agent",
      color: "#2563EB",
      activityScore: Number.MAX_SAFE_INTEGER - 1,
      frequencyScore: Number.MAX_SAFE_INTEGER - 1,
      isCurrentRoute: true,
    };
  }, [defaultAgentCard, defaultAgentLabel, isRoutingToConcierge]);

  const curatedQuickActionAgents = useMemo(
    () => quickMentionAgents.slice(0, 6),
    [quickMentionAgents],
  );

  const quickActionGroups = useMemo(
    () => buildQuickActionGroupOptions(teams, agents, currentOrgId).slice(0, 4),
    [agents, currentOrgId, teams],
  );

  const selectedQuickActionGroup = useMemo(
    () =>
      selectedQuickActionGroupId
        ? quickActionGroups.find(
            (group) => group.id === selectedQuickActionGroupId,
          ) || null
        : null,
    [quickActionGroups, selectedQuickActionGroupId],
  );

  const quickActionRowAgents = useMemo(
    () =>
      buildQuickActionRowAgents(
        currentRouteQuickAction,
        curatedQuickActionAgents,
      ),
    [currentRouteQuickAction, curatedQuickActionAgents],
  );

  // Persist quick actions collapse state
  useEffect(() => {
    try {
      localStorage.setItem(
        "ax_quick_collapsed",
        quickActionsCollapsed ? "true" : "false",
      );
    } catch {
      /* no-op */
    }
  }, [quickActionsCollapsed]);

  useEffect(() => {
    try {
      if (defaultAgentUsername) {
        localStorage.setItem(defaultAgentStorageKey, defaultAgentUsername);
      } else {
        localStorage.removeItem(defaultAgentStorageKey);
      }
    } catch {
      /* no-op */
    }
  }, [defaultAgentStorageKey, defaultAgentUsername]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(defaultAgentStorageKey);
      setDefaultAgentUsername(saved ? stripHandle(saved) : null);
    } catch {
      setDefaultAgentUsername(null);
    }
  }, [defaultAgentStorageKey, stripHandle]);

  // Persist draft text to localStorage (debounced 300ms)
  const draftTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (draftTimerRef.current) clearTimeout(draftTimerRef.current);
    draftTimerRef.current = setTimeout(() => {
      try {
        if (message.trim()) {
          localStorage.setItem(draftKey, message);
        } else {
          localStorage.removeItem(draftKey);
        }
      } catch {
        /* no-op */
      }
    }, 300);
    return () => {
      if (draftTimerRef.current) clearTimeout(draftTimerRef.current);
    };
  }, [message, draftKey]);

  // Use agents from props for @mention autocomplete
  useEffect(() => {
    setAvailableAgents(agents);

    // Load teams for filtering (still needed for team mentions)
    const loadTeams = async () => {
      try {
        const teamsData = await api.getTeams();
        setTeams(Array.isArray(teamsData) ? teamsData : []);
      } catch (error) {
        console.error("Failed to load teams:", error);
        setTeams([]);
      }
    };
    loadTeams();
  }, [agents]);

  // Auto-focus when replying to a post
  useEffect(() => {
    if (replyToPost && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [replyToPost]);

  // Auto-focus the composer when sharing. Do NOT seed "@" — auto-opening
  // the mention dropdown on Share click is jarring (madtank feedback
  // 2026-04-16). User types @ themselves when picking a target.
  useEffect(() => {
    if (forwardingCard && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [forwardingCard]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height =
        textareaRef.current.scrollHeight + "px";
    }
  }, [message]);

  // Filter agents based on mention query and selected team
  useEffect(() => {
    // Ensure availableAgents is an array before using array methods
    const fallbackAgents: Agent[] =
      !Array.isArray(availableAgents) || availableAgents.length === 0
        ? quickMentionAgents
            .filter((agent) => Boolean(stripHandle(agent.username)))
            .map((agent) => ({
              username: stripHandle(agent.username),
              agent_type: agent.agent_type || "Agent",
              org_id: currentOrgId,
              team_id: agent._agentRef?.team_id,
              team_name: agent._agentRef?.team_name,
              team_color: agent._agentRef?.team_color,
              id: agent._agentRef?.id,
              last_activity: agent._agentRef?.last_activity,
              post_count: agent._agentRef?.post_count || 0,
            }))
        : [...availableAgents];

    if (!Array.isArray(fallbackAgents) || fallbackAgents.length === 0) {
      setFilteredAgents([]);
      return;
    }

    let agentsToFilter = fallbackAgents;

    // Filter by team if a specific team is selected
    if (selectedTeam !== "all") {
      agentsToFilter = fallbackAgents.filter(
        (agent) =>
          agent.team_id === selectedTeam ||
          (selectedTeam === "core" &&
            agent.agent_type?.toLowerCase().includes("core")),
      );
    }

    const query = mentionQuery.toLowerCase();

    // Filter by mention query
    if (!query) {
      setFilteredAgents(agentsToFilter.slice(0, 8)); // Show top 8 agents
    } else {
      const filtered = agentsToFilter
        .filter(
          (agent) =>
            agent.username.toLowerCase().includes(query) ||
            (agent.agent_type &&
              agent.agent_type.toLowerCase().includes(query)) ||
            (agent.team_name && agent.team_name.toLowerCase().includes(query)),
        )
        .slice(0, 8);
      setFilteredAgents(filtered);
    }
    setSelectedAgentIndex(0);
  }, [
    mentionQuery,
    availableAgents,
    selectedTeam,
    quickMentionAgents,
    currentOrgId,
    stripHandle,
  ]);

  // Handle @ mention and @team detection
  const detectMention = (text: string, cursorPosition: number) => {
    const safeCursor = Math.max(0, Math.min(cursorPosition, text.length));
    const beforeCursor = text.substring(0, safeCursor);

    // Check for @team mentions (e.g., @core-team, @main-team)
    const teamMentionMatch = beforeCursor.match(
      /(?:^|\s)@(core|main|research|development|creative|analytical)[-_]?team$/i,
    );
    if (teamMentionMatch) {
      const teamName = teamMentionMatch[1].toLowerCase();
      const teamId = teamName === "main" ? "core" : teamName;
      setSelectedTeam(teamId);
      // Show team members for this mention
      setMentionQuery("");
      setMentionPosition({
        start: beforeCursor.lastIndexOf("@"),
        end: cursorPosition,
      });
      setShowMentions(true);
      return;
    }

    // Regular @mention detection
    const mentionMatch = beforeCursor.match(/(?:^|\s)@([\w-]*)$/);
    if (mentionMatch) {
      const start = beforeCursor.lastIndexOf("@");
      const query = mentionMatch[1];
      setMentionQuery(query);
      setMentionPosition({ start, end: cursorPosition });
      setShowMentions(true);
      setSelectedTeam("all");
    } else {
      setShowMentions(false);
      setMentionQuery("");
    }
  };

  // Handle hashtag detection for topics
  const detectHashtag = (text: string, cursorPosition: number) => {
    const beforeCursor = text.substring(0, cursorPosition);

    // Look for hashtag pattern: # followed by optional letters
    const hashtagMatch = beforeCursor.match(/#([a-zA-Z]*)$/);

    if (hashtagMatch) {
      const start = beforeCursor.lastIndexOf("#");
      const query = hashtagMatch[1];
      setHashtagQuery(query);
      setHashtagPosition({ start, end: cursorPosition });
      setShowHashtags(true);
    } else {
      setShowHashtags(false);
      setHashtagQuery("");
    }
  };

  // Filter topics based on hashtag query
  useEffect(() => {
    if (hashtagQuery !== "") {
      const filtered = availableTopics
        .filter((topic) =>
          topic.toLowerCase().includes(hashtagQuery.toLowerCase()),
        )
        .slice(0, 8);
      setFilteredTopics(filtered);
    } else {
      setFilteredTopics(availableTopics.slice(0, 8));
    }
    setSelectedTopicIndex(0);
  }, [hashtagQuery, availableTopics]);

  const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    setMessage(newValue);

    const cursorPosition = e.target.selectionStart ?? newValue.length;
    detectMention(newValue, cursorPosition);
    detectHashtag(newValue, cursorPosition);
  };

  const insertMention = (agent: Agent) => {
    const beforeMention = message.substring(0, mentionPosition.start);
    const afterMention = message.substring(mentionPosition.end);
    const newMessage = beforeMention + `@${agent.username} ` + afterMention;

    setMessage(newMessage);
    setShowMentions(false);
    setMentionQuery("");

    // Focus back to textarea
    setTimeout(() => {
      if (textareaRef.current) {
        const newCursorPosition =
          mentionPosition.start + agent.username.length + 2;
        textareaRef.current.setSelectionRange(
          newCursorPosition,
          newCursorPosition,
        );
        textareaRef.current.focus();
      }
    }, 0);
  };

  const appendMentionToDraft = useCallback(
    (username: string) => {
      const cleanUsername = stripHandle(username);
      if (!cleanUsername) return;

      setMessage((current) => {
        const mention = `@${cleanUsername}`;
        const hasMention = new RegExp(
          `(?:^|\\s)@${cleanUsername.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&")}(?=\\s|$)`,
          "i",
        ).test(current);
        if (hasMention) return current;
        return current.trim().length > 0
          ? `${current.trimEnd()} ${mention} `
          : `${mention} `;
      });

      setTimeout(() => textareaRef.current?.focus(), 0);
    },
    [stripHandle],
  );

  // Helper function to insert hashtag
  const insertHashtag = (topic: string) => {
    if (textareaRef.current) {
      const currentMessage = message;
      const beforeHashtag = currentMessage.substring(0, hashtagPosition.start);
      const afterHashtag = currentMessage.substring(hashtagPosition.end);

      const newMessage = beforeHashtag + `#${topic} ` + afterHashtag;
      setMessage(newMessage);
      setShowHashtags(false);

      // Focus back to textarea and position cursor after hashtag
      setTimeout(() => {
        if (textareaRef.current) {
          const newCursorPosition = hashtagPosition.start + topic.length + 2; // +2 for # and space
          textareaRef.current.setSelectionRange(
            newCursorPosition,
            newCursorPosition,
          );
          textareaRef.current.focus();
        }
      }, 0);
    }
  };

  const submitMessage = useCallback(
    async (messageText: string) => {
      setIsSubmitting(true);
      try {
        const trimmed = messageText.trim();
        const lower = trimmed.toLowerCase();
        const isDemoTrigger = !replyToPost && /^(\/demo|\?demo)\b/.test(lower);

        // Guard: prevent oversized reaction messages (emoji-only with too many types)
        try {
          const matches = trimmed.match(EMOJI_CLUSTER) || [];
          const nonEmoji = trimmed
            .replace(EMOJI_CLUSTER, "")
            .replace(/\s+/g, "");
          const isEmojiOnly = matches.length > 0 && nonEmoji.length === 0;
          if (isEmojiOnly) {
            const unique = Array.from(new Set(matches));
            if (unique.length > MAX_REACTION_EMOJI_TYPES) {
              toast({
                title: "Too many emojis",
                description: `Reactions are limited to ${MAX_REACTION_EMOJI_TYPES} unique emoji per message.`,
                variant: "destructive",
              });
              setIsSubmitting(false);
              return;
            }
          }
        } catch {
          /* no-op */
        }

        if (isDemoTrigger) {
          await api.runOnboardingDemo();
          toast({
            title: "Demo running…",
            description: "Seeding a short two‑agent exchange",
          });
          setMessage("");
          onReplyPosted?.();
          return;
        }
        // Upload staged file and append markdown to the outbound message.
        let uploadedFileMarkdown = "";
        let uploadedAttachment: UploadedAttachmentReference | null = null;
        if (pendingFile) {
          try {
            setUploadProgress({ phase: "uploading", percent: 0 });
            const { url, attachment_id: attId } = await uploadFile(
              pendingFile.file,
              (percent) => {
                setUploadProgress({
                  phase: "uploading",
                  percent,
                });
              },
            );
            if (url) {
              uploadedFileMarkdown = buildUploadedFileMarkdown(
                pendingFile,
                url,
              );
              const uploadedAt = new Date().toISOString();
              const attachmentId =
                attId ||
                url.split("/").filter(Boolean).pop() ||
                `upload-${Date.now()}`;
              const contentType =
                pendingFile.file.type ||
                (pendingFile.kind === "image"
                  ? "image/*"
                  : "application/octet-stream");
              const contextKey = currentOrgId
                ? await storeUploadInContext(currentOrgId, {
                    id: attachmentId,
                    filename: pendingFile.file.name,
                    content_type: contentType,
                    size_bytes: pendingFile.file.size,
                    url,
                    uploaded_at: uploadedAt,
                    upload_origin: "chat_input",
                  })
                : null;
              uploadedAttachment = {
                id: attachmentId,
                ...(attId ? { attachment_id: attId } : {}),
                filename: pendingFile.file.name,
                name: pendingFile.file.name,
                content_type: contentType,
                mime_type: contentType,
                size_bytes: pendingFile.file.size,
                size: pendingFile.file.size,
                url,
                context_key: contextKey,
                uploaded_at: uploadedAt,
                upload_origin: "chat_input",
              };
            }
          } catch (uploadErr: unknown) {
            const errMsg =
              uploadErr instanceof Error
                ? uploadErr.message
                : "Could not upload file.";
            setUploadProgress({
              phase: "error",
              percent: 0,
              errorMsg: errMsg,
            });
            toast({
              title: "Upload Failed",
              description: errMsg,
              variant: "destructive",
            });
            return;
          }
          clearPendingFile();
        }

        const { nextDefaultAgent, routedMessageBase } = planStickyRouting(
          messageText,
          defaultAgentUsername,
          resolveAgentUsernameToId,
          CONCIERGE_HANDLE,
        );
        const finalMessage = uploadedFileMarkdown
          ? routedMessageBase
            ? `${routedMessageBase}
${uploadedFileMarkdown}`
            : uploadedFileMarkdown
          : routedMessageBase;
        const mentionedAgentIds = resolveMentionedAgentIds(
          getKnownMentionedAgentUsernames(finalMessage),
        );
        const uploadMetadata = uploadedAttachment
          ? buildAttachmentMetadata(uploadedAttachment)
          : {};
        const sendOptions = buildChatInputSendOptions({
          mentionedAgentIds,
          uploadMetadata,
          forwardingCard,
        });

        if (replyToPost) {
          // Check if this is a waiting post or regular reply
          if (replyToPost.waiting_since) {
            // Use respond endpoint for waiting posts
            await api.respondToPost(replyToPost.id, finalMessage, sendOptions);
          } else {
            // Use regular reply for non-waiting posts
            await api.replyToPost(replyToPost.id, finalMessage, sendOptions);
          }
          onClearReply?.();
        } else {
          // Regular post as human user (optionally carries forward metadata)
          await api.postMessage(finalMessage, {
            channel: "main",
            ...(sendOptions.mentionedAgentIds.length > 0
              ? { mentionedAgentIds: sendOptions.mentionedAgentIds }
              : {}),
            ...(sendOptions.metadata ? { metadata: sendOptions.metadata } : {}),
          });
        }
        if (forwardingCard) {
          onClearForward?.();
        }

        try {
          storage.setHasSentMessage(true);
        } catch {
          /* no-op */
        }
        if (nextDefaultAgent !== undefined) {
          setDefaultAgentUsername(nextDefaultAgent);
        }
        setMessage("");
        try {
          localStorage.removeItem(draftKey);
        } catch {
          /* no-op */
        }

        // Immediately invalidate posts query to show the sent message
        // SSE should also trigger this, but this ensures immediate feedback
        queryClient.invalidateQueries({ queryKey: ["posts"] });

        onReplyPosted?.();
      } catch (error: unknown) {
        console.error("Failed to send message:", error);
        const guardrailError =
          typeof error === "object" && error !== null
            ? (error as {
                isGuardrailViolation?: boolean;
                title?: string;
                message?: string;
                suggestion?: string;
              })
            : null;

        // Handle guardrail violations with user-friendly messages
        if (guardrailError?.isGuardrailViolation) {
          toast({
            title: guardrailError.title || "🔒 Content Blocked",
            description:
              guardrailError.message ||
              "Your message has been blocked by security filters.",
            variant: "destructive",
          });

          // Optionally show the suggestion in a separate toast
          if (guardrailError.suggestion) {
            setTimeout(() => {
              toast({
                title: "💡 Suggestion",
                description: guardrailError.suggestion,
                variant: "default",
              });
            }, 1000);
          }
        } else {
          // Generic error handling for other types of errors
          toast({
            title: "Failed to send",
            description:
              "There was an error sending your message. Please try again.",
            variant: "destructive",
          });
        }
      } finally {
        setIsSubmitting(false);
      }
    },
    [
      replyToPost,
      forwardingCard,
      onClearForward,
      toast,
      onReplyPosted,
      onClearReply,
      queryClient,
      pendingFile,
      clearPendingFile,
      defaultAgentUsername,
      draftKey,
      extractMentionUsernames,
      getKnownMentionedAgentUsernames,
      resolveMentionedAgentIds,
      resolveAgentUsernameToId,
      stripHandle,
    ],
  );

  // Validation dialog removed — default routing handles un-mentioned messages

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ username?: string }>).detail;
      if (!detail?.username) return;
      applyDefaultAgentSelection(detail.username);
    };
    window.addEventListener("ax:insert-mention", handler as EventListener);
    return () => {
      window.removeEventListener("ax:insert-mention", handler as EventListener);
    };
  }, [applyDefaultAgentSelection]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (event: Event) => {
      const detail = (
        event as CustomEvent<{ text?: string; autoSend?: boolean }>
      ).detail;
      if (!detail?.text) return;
      const textValue = String(detail.text);
      setMessage(textValue);
      if (detail.autoSend) {
        setTimeout(() => {
          submitMessage(textValue);
        }, 0);
      } else {
        textareaRef.current?.focus();
      }
    };
    window.addEventListener("ax:help-quick-action", handler as EventListener);
    return () => {
      window.removeEventListener(
        "ax:help-quick-action",
        handler as EventListener,
      );
    };
  }, [submitMessage]);

  // Handle agent mention append from popover - appends @handle to existing message
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ handle?: string }>).detail;
      if (!detail?.handle) return;
      const handle = detail.handle.replace(/^@/, "");
      const normalizedHandle = stripHandle(handle).toLowerCase();
      if (normalizedHandle && normalizedHandle === normalizedDefaultAgent) {
        textareaRef.current?.focus();
        return;
      }
      // Use word boundary regex to avoid false positives (e.g., @user1 vs @user123)
      const mentionPattern = new RegExp(`(^|\\s)@${handle}\\b`, "i");
      setMessage((prev) => {
        if (mentionPattern.test(prev)) return prev; // Already mentioned
        return `${prev}@${handle} `;
      });
      textareaRef.current?.focus();
    };
    window.addEventListener(
      "ax:agent-mention-append",
      handler as EventListener,
    );
    return () => {
      window.removeEventListener(
        "ax:agent-mention-append",
        handler as EventListener,
      );
    };
  }, [normalizedDefaultAgent, stripHandle]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!message.trim() && !pendingFile) || isSubmitting) return;

    const trimmedMessage = message.trim();

    // Check if this is a help trigger
    if (trimmedMessage === "?" || trimmedMessage === "/?") {
      setShowHelpDialog(true);
      setMessage(""); // Clear the input
      return;
    }

    // Check message length (most systems have ~2000-4000 char limits)
    // Increased to 50,000 to support long code blocks and complex prompts
    if (trimmedMessage.length > 50000) {
      toast({
        title: "Message too long",
        description: `Message is ${trimmedMessage.length} characters. Please keep under 50,000 characters.`,
        variant: "destructive",
      });
      return;
    }

    await submitMessage(trimmedMessage);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Ensure mention dropdown opens on natural mention boundaries
    if (e.key === "@") {
      const cursorPosition =
        textareaRef.current?.selectionStart ?? message.length;
      const safeCursor = Math.max(0, Math.min(cursorPosition, message.length));
      const prevChar = safeCursor > 0 ? message.charAt(safeCursor - 1) : "";
      const isMentionsBoundary = safeCursor === 0 || /\s/.test(prevChar);

      if (isMentionsBoundary) {
        const nextCursor = safeCursor + 1;
        setMentionQuery("");
        setSelectedTeam("all");
        setSelectedAgentIndex(0);
        setShowHashtags(false);
        setMentionPosition({
          start: safeCursor,
          end: nextCursor,
        });
        setShowMentions(true);
      }
    }

    const canInsertMention =
      filteredAgents.length > 0 && mentionQuery.length > 0;
    if (showMentions && canInsertMention) {
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setSelectedAgentIndex((prev) =>
            prev < filteredAgents.length - 1 ? prev + 1 : 0,
          );
          break;
        case "ArrowUp":
          e.preventDefault();
          setSelectedAgentIndex((prev) =>
            prev > 0 ? prev - 1 : filteredAgents.length - 1,
          );
          break;
        case "Enter":
        case "Tab":
          e.preventDefault();
          insertMention(filteredAgents[selectedAgentIndex]);
          break;
        case "Escape":
          e.preventDefault();
          setShowMentions(false);
          break;
      }
      return;
    }

    if (showMentions && e.key === "Escape") {
      e.preventDefault();
      setShowMentions(false);
    }

    if (showHashtags && filteredTopics.length > 0) {
      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setSelectedTopicIndex((prev) =>
            prev < filteredTopics.length - 1 ? prev + 1 : 0,
          );
          break;
        case "ArrowUp":
          e.preventDefault();
          setSelectedTopicIndex((prev) =>
            prev > 0 ? prev - 1 : filteredTopics.length - 1,
          );
          break;
        case "Enter":
        case "Tab":
          e.preventDefault();
          insertHashtag(filteredTopics[selectedTopicIndex]);
          break;
        case "Escape":
          e.preventDefault();
          setShowHashtags(false);
          break;
      }
      return;
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const getWaitingDuration = () => {
    if (!replyToPost?.waiting_since) return "";
    const waitTime = Date.now() - new Date(replyToPost.waiting_since).getTime();
    const minutes = Math.floor(waitTime / 60000);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ${minutes % 60}m ago`;
    return `${Math.floor(hours / 24)}d ago`;
  };

  // Root ref for internal use (mention positioning, etc.)
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Publish actual ChatInput height as CSS variable so MessageList can size its bottom padding
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const h = Math.ceil(
        entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height,
      );
      document.documentElement.style.setProperty(
        "--chat-input-height",
        `${h}px`,
      );
      // Re-trigger scroll check so auto-scroll state stays accurate
      const container = document.querySelector("[data-messages-container]");
      if (container) container.dispatchEvent(new Event("scroll"));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div
      ref={rootRef}
      data-chat-input-root
      className="bg-white dark:bg-gray-800 relative"
    >
      {/* @ Mention Dropdown — positioned absolutely above the input so it doesn't
          change the fixed container's height and cause mobile keyboard/scroll issues */}
      {showMentions && (
        <div
          className="absolute bottom-full left-0 right-0 z-50 px-4 pb-1"
          onPointerDown={(e) => e.preventDefault()}
        >
          <div className="z-50 max-h-64 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-800">
            <div className="border-b border-gray-200 px-3 py-2 text-xs font-medium text-gray-500 dark:border-slate-700 dark:text-gray-400">
              <Hash className="w-3 h-3 inline mr-1" />@ Mention agents{" "}
              {mentionQuery && `• "${mentionQuery}"`}
              {selectedTeam !== "all" && (
                <span className="ml-2 px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded text-xs">
                  {teams.find((t) => t.id === selectedTeam)?.name ||
                    selectedTeam}{" "}
                  team
                </span>
              )}
            </div>

            {/* Team Quick Filters - Only show teams where user has agents */}
            {!mentionQuery && (
              <div className="border-b border-gray-200 px-3 py-2 dark:border-slate-700">
                <div className="flex flex-wrap gap-1">
                  {/* Always show All Teams */}
                  <button
                    onClick={() => setSelectedTeam("all")}
                    className={`px-2 py-1 text-xs rounded ${
                      selectedTeam === "all"
                        ? "bg-blue-500 text-white"
                        : "bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-slate-700 dark:text-gray-200 dark:hover:bg-slate-600"
                    }`}
                  >
                    All Teams
                  </button>

                  {/* Only show team filters for teams where user has agents */}
                  {(() => {
                    // Get unique team IDs from user's agents
                    const userTeamIds = new Set(
                      availableAgents
                        .filter((agent) => agent.team_id)
                        .map((agent) => agent.team_id),
                    );

                    // Filter teams to only those the user has agents in
                    const userTeams = teams.filter((team) =>
                      userTeamIds.has(team.id),
                    );

                    return userTeams.slice(0, 4).map((team) => (
                      <button
                        key={team.id}
                        onClick={() => setSelectedTeam(team.id)}
                        className={`px-2 py-1 text-xs rounded ${
                          selectedTeam === team.id
                            ? "bg-blue-500 text-white"
                            : "bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-slate-700 dark:text-gray-200 dark:hover:bg-slate-600"
                        }`}
                      >
                        {team.id === "core" ? "🎯 " : ""}
                        {team.name}
                      </button>
                    ));
                  })()}
                </div>
              </div>
            )}

            {filteredAgents.length === 0 ? (
              <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                No matching agents
              </div>
            ) : (
              filteredAgents.map((agent, index) => (
                <div
                  key={agent.username}
                  className={`cursor-pointer px-3 py-2 transition-colors hover:bg-gray-50 dark:hover:bg-slate-700/80 ${
                    index === selectedAgentIndex
                      ? "bg-blue-50 dark:bg-blue-900/30"
                      : ""
                  }`}
                  onPointerDown={(e) => {
                    e.preventDefault();
                    insertMention(agent);
                  }}
                >
                  <div className="flex items-center space-x-2">
                    <div
                      className="w-6 h-6 rounded-full flex items-center justify-center text-white text-xs font-bold"
                      style={{ backgroundColor: agent.team_color || "#6B7280" }}
                    >
                      {agent.username?.charAt(0).toUpperCase() || "?"}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center space-x-2">
                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                          @{agent.username}
                        </span>
                        <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500 dark:bg-slate-700 dark:text-gray-300">
                          {humanizeAgentType(agent.agent_type)}
                        </span>
                        {agent.team_name && (
                          <span
                            className="text-xs px-1.5 py-0.5 rounded text-white"
                            style={{
                              backgroundColor: agent.team_color || "#6B7280",
                            }}
                          >
                            {agent.team_name}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        {agent.post_count} posts •{" "}
                        {new Date(
                          agent.last_activity || Date.now(),
                        ).toLocaleDateString()}
                      </div>
                    </div>
                  </div>
                </div>
              ))
            )}
            <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-600">
              ↑↓ to navigate • Enter/Tab to select • Esc to cancel • Try
              @core-team for team mentions
            </div>
          </div>
        </div>
      )}

      {/* # Hashtag Dropdown — also absolutely positioned to avoid mobile layout issues */}
      {showHashtags && filteredTopics.length > 0 && (
        <div
          className="absolute bottom-full left-0 right-0 z-50 px-4 pb-1"
          onPointerDown={(e) => e.preventDefault()}
        >
          <div className="z-50 max-h-64 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-800">
            <div className="border-b border-gray-200 px-3 py-2 text-xs font-medium text-gray-500 dark:border-slate-700 dark:text-gray-400">
              <Hash className="w-3 h-3 inline mr-1" /># Add topic{" "}
              {hashtagQuery && `• "${hashtagQuery}"`}
            </div>

            {filteredTopics.map((topic, index) => (
              <div
                key={topic}
                onPointerDown={(e) => e.preventDefault()}
                onClick={() => insertHashtag(topic)}
                className={`px-3 py-2 cursor-pointer transition-colors border-b border-gray-100 dark:border-gray-600 last:border-b-0 ${
                  index === selectedTopicIndex
                    ? "bg-blue-50 dark:bg-blue-900/20"
                    : "hover:bg-gray-50 dark:hover:bg-gray-600"
                }`}
              >
                <div className="flex items-center space-x-2">
                  <Hash className="w-4 h-4 text-blue-500" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center space-x-2">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                        #{topic}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">
                      Add #{topic} topic to your message
                    </div>
                  </div>
                </div>
              </div>
            ))}

            <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400 border-t border-gray-200 dark:border-gray-600">
              ↑↓ to navigate • Enter/Tab to select • Esc to cancel
            </div>
          </div>
        </div>
      )}

      <div className="px-4">
        {/* Reply Context Bar */}
        {replyToPost && (
          <div className="py-2 border-b border-gray-200 dark:border-gray-700">
            <div className="flex items-start justify-between bg-orange-50 dark:bg-orange-900/20 rounded-lg p-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center space-x-2 mb-1">
                  <AlertTriangle className="w-4 h-4 text-orange-500" />
                  <span className="text-sm font-medium text-orange-800 dark:text-orange-200">
                    Replying to @{replyToPost.username}
                  </span>
                  {replyToPost.waiting_since && (
                    <div className="flex items-center space-x-1 text-xs text-orange-600 dark:text-orange-400">
                      <Clock className="w-3 h-3" />
                      <span>Waiting {getWaitingDuration()}</span>
                    </div>
                  )}
                </div>
                <p className="text-sm text-gray-700 dark:text-gray-300 truncate">
                  {replyToPost.content.length > 100
                    ? replyToPost.content.substring(0, 100) + "..."
                    : replyToPost.content}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={onClearReply}
                className="flex-shrink-0 ml-2"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
          </div>
        )}
        {/* Share Context Bar — mirrors Reply (task 48ae545f, msg 520a2269) */}
        {forwardingCard && (
          <div
            className="py-2 border-b border-gray-200 dark:border-gray-700"
            data-testid="chat-input-forward-bar"
          >
            <div className="flex items-start justify-between bg-amber-50 dark:bg-amber-900/20 rounded-lg p-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center space-x-2 mb-1">
                  <MessageSquareShare className="w-4 h-4 text-amber-600" />
                  <span className="text-sm font-medium text-amber-800 dark:text-amber-200">
                    Sharing {forwardingCard.title}
                  </span>
                </div>
                {forwardingCard.summary ? (
                  <p className="text-sm text-gray-700 dark:text-gray-300 truncate">
                    {forwardingCard.summary.length > 100
                      ? forwardingCard.summary.substring(0, 100) + "..."
                      : forwardingCard.summary}
                  </p>
                ) : null}
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={onClearForward}
                className="flex-shrink-0 ml-2"
                data-testid="chat-input-forward-cancel"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
          </div>
        )}

        {/* Quick Actions / Default Agent Selector */}
        {!quickActionsCollapsed && (
          <div className="py-2 border-b border-gray-100 dark:border-gray-700 space-y-2">
            <div className="flex items-center space-x-2 max-w-full">
              <div className="flex items-center space-x-1 flex-1 overflow-x-auto scrollbar-hide relative">
                <div className="flex items-center space-x-1 min-w-max">
                  <>
                    {quickActionGroups.map((group) => {
                      const isSelected =
                        selectedQuickActionGroup?.id === group.id;
                      return (
                        <button
                          key={`group:${group.id}`}
                          type="button"
                          onPointerDown={(e) => e.preventDefault()}
                          onClick={() => {
                            setSelectedQuickActionGroupId(
                              isSelected ? null : group.id,
                            );
                          }}
                          className={`group relative flex items-center space-x-1 px-2 py-1 rounded-md transition-all text-xs font-medium flex-shrink-0 ${
                            isSelected
                              ? "bg-purple-600 text-white shadow-sm"
                              : "bg-purple-50 text-purple-700 hover:bg-purple-100 dark:bg-purple-900/20 dark:text-purple-200 dark:hover:bg-purple-800/40"
                          }`}
                          title={`Show ${group.name} members before sending`}
                        >
                          <div
                            className="w-4 h-4 rounded-full flex items-center justify-center text-white text-[10px] font-bold flex-shrink-0"
                            style={{ backgroundColor: group.color }}
                          >
                            👥
                          </div>
                          <span>{group.name}</span>
                          <span className="rounded-full bg-white/70 px-1.5 text-[10px] text-purple-700 dark:bg-purple-950/60 dark:text-purple-100">
                            {group.members.length}
                          </span>
                        </button>
                      );
                    })}

                    {quickActionRowAgents.map((agent) => {
                      const isCurrentRoute = Boolean(agent.isCurrentRoute);
                      const buttonTitle = agent.isConcierge
                        ? "Messages route to @Commonflame concierge"
                        : isCurrentRoute
                          ? `Messages route to @${agent.username}`
                          : `Set @${agent.username} as default agent`;
                      return (
                        <AgentHoverCard
                          key={`${agent.isConcierge ? "concierge" : "agent"}:${agent.username}`}
                          agent={{
                            ...agent._agentRef,
                            username: agent.username,
                            color: agent.color,
                          }}
                        >
                          <button
                            onPointerDown={(e) => e.preventDefault()}
                            onClick={() => {
                              applyDefaultAgentSelection(
                                agent.isConcierge ? null : agent.username,
                              );
                            }}
                            className={`group relative flex items-center space-x-1 px-2 py-1 rounded-md transition-all text-xs font-medium flex-shrink-0 ${
                              isCurrentRoute
                                ? "bg-blue-600 text-white shadow-sm"
                                : "bg-gray-50 dark:bg-gray-700 hover:bg-gray-100 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white"
                            }`}
                            title={buttonTitle}
                          >
                            {agent.isConcierge ? (
                              <div className="w-4 h-4 rounded-full bg-gray-900 text-white text-[10px] font-bold flex items-center justify-center flex-shrink-0 dark:bg-gray-200 dark:text-gray-900">
                                Commonflame
                              </div>
                            ) : (
                              <div
                                className="w-4 h-4 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0"
                                style={{ backgroundColor: agent.color }}
                              >
                                {agent.username?.charAt(0).toUpperCase() || "?"}
                              </div>
                            )}
                            {isCurrentRoute ? (
                              <span className="ml-0.5 text-white/80">✓</span>
                            ) : null}
                            <span className="hidden sm:inline">
                              @{agent.username}
                            </span>
                            <span className="sm:hidden">
                              @{agent.username.slice(0, 6)}
                            </span>
                          </button>
                        </AgentHoverCard>
                      );
                    })}

                    <div className="relative">
                      <button
                        type="button"
                        onClick={() =>
                          setShowMoreQuickMentions(!showMoreQuickMentions)
                        }
                        className="flex items-center justify-center gap-1 rounded-md bg-gray-100 px-2 py-1 text-xs text-gray-600 transition-colors hover:bg-gray-200 dark:bg-gray-600 dark:text-gray-300 dark:hover:bg-gray-500"
                        title="Choose default agent"
                      >
                        <MoreHorizontal className="w-3 h-3" />
                        <span>All agents</span>
                      </button>

                      {showMoreQuickMentions && (
                        <div className="absolute top-8 left-0 z-50 min-w-56 rounded-md border border-gray-200 bg-white p-2 shadow-lg dark:border-gray-600 dark:bg-gray-800">
                          <div className="px-2 pb-2 text-xs font-medium text-gray-500 dark:text-gray-400">
                            Default agent routing
                          </div>
                          <div className="max-h-64 overflow-y-auto">
                            <button
                              type="button"
                              onPointerDown={(e) => e.preventDefault()}
                              onClick={() => {
                                applyDefaultAgentSelection(null, {
                                  closeMenus: true,
                                });
                              }}
                              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-gray-100 dark:hover:bg-gray-700"
                            >
                              <input
                                type="checkbox"
                                readOnly
                                checked={isRoutingToConcierge}
                                className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600"
                                aria-label="Route messages to Commonflame concierge"
                              />
                              <div className="w-4 h-4 rounded-full bg-gray-900 text-white text-[10px] font-bold flex items-center justify-center flex-shrink-0 dark:bg-gray-200 dark:text-gray-900">
                                Commonflame
                              </div>
                              <span className="flex-1 text-gray-700 dark:text-gray-300">
                                @{CONCIERGE_HANDLE}
                              </span>
                              <span className="text-[10px] text-gray-500 dark:text-gray-400">
                                Concierge
                              </span>
                            </button>
                            {quickActionAgentOptions.map((agent) => {
                              const normalizedAgent = stripHandle(
                                agent.username,
                              ).toLowerCase();
                              const isDefaultAgent =
                                normalizedAgent === normalizedDefaultAgent;
                              return (
                                <button
                                  key={agent.username}
                                  type="button"
                                  onPointerDown={(e) => e.preventDefault()}
                                  onClick={() => {
                                    applyDefaultAgentSelection(agent.username, {
                                      closeMenus: true,
                                    });
                                  }}
                                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-gray-100 dark:hover:bg-gray-700"
                                >
                                  <input
                                    type="checkbox"
                                    readOnly
                                    checked={isDefaultAgent}
                                    className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600"
                                    aria-label={`Default agent ${agent.username}`}
                                  />
                                  <div
                                    className="w-4 h-4 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0"
                                    style={{ backgroundColor: agent.color }}
                                  >
                                    {agent.username?.charAt(0).toUpperCase() ||
                                      "?"}
                                  </div>
                                  <span className="flex-1 text-gray-700 dark:text-gray-300">
                                    @{agent.username}
                                  </span>
                                  <span className="text-[10px] text-gray-500 dark:text-gray-400">
                                    {humanizeAgentType(agent.agent_type)}
                                  </span>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}
                    </div>
                  </>
                </div>
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setShowPlusDropdown(!showPlusDropdown)}
                    className="flex items-center justify-center w-6 h-6 rounded-md bg-gray-100 dark:bg-gray-600 hover:bg-gray-200 dark:hover:bg-gray-500 transition-colors text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
                    title="Add mention or topic"
                  >
                    <Plus className="w-3 h-3" />
                  </button>

                  {showPlusDropdown && (
                    <div className="absolute top-8 left-0 z-50 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-md shadow-lg p-2 min-w-64 max-h-80 overflow-y-auto">
                      <div className="mb-4">
                        <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2 px-2">
                          Topics
                        </div>
                        {[
                          "#Dev",
                          "#Design",
                          "#Testing",
                          "#Bug",
                          "#Feature",
                          "#Help",
                          "#Planning",
                        ].map((topic) => (
                          <button
                            key={topic}
                            type="button"
                            onPointerDown={(e) => e.preventDefault()}
                            onClick={() => {
                              if (textareaRef.current) {
                                const cursorPosition =
                                  textareaRef.current.selectionStart;
                                const beforeCursor = message.substring(
                                  0,
                                  cursorPosition,
                                );
                                const afterCursor =
                                  message.substring(cursorPosition);
                                const needsSpaceBefore =
                                  beforeCursor.length > 0 &&
                                  !beforeCursor.endsWith(" ");
                                const topicText = `${needsSpaceBefore ? " " : ""}${topic} `;
                                const newMessage =
                                  beforeCursor + topicText + afterCursor;
                                setMessage(newMessage);
                                setTimeout(() => {
                                  if (textareaRef.current) {
                                    const newPos =
                                      cursorPosition + topicText.length;
                                    textareaRef.current.setSelectionRange(
                                      newPos,
                                      newPos,
                                    );
                                    textareaRef.current.focus();
                                  }
                                }, 0);
                              }
                              setShowPlusDropdown(false);
                            }}
                            className="w-full flex items-center space-x-2 px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-xs text-left"
                          >
                            <Hash className="w-3 h-3 text-blue-500" />
                            <span className="text-gray-700 dark:text-gray-300">
                              {topic}
                            </span>
                          </button>
                        ))}
                      </div>

                      <div>
                        <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2 px-2">
                          Default Agent
                        </div>
                        <button
                          type="button"
                          onPointerDown={(e) => e.preventDefault()}
                          onClick={() => {
                            applyDefaultAgentSelection(null, {
                              closeMenus: true,
                            });
                          }}
                          className="w-full flex items-center space-x-2 px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-xs text-left"
                        >
                          <input
                            type="checkbox"
                            readOnly
                            checked={isRoutingToConcierge}
                            className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600"
                            aria-label="Select Commonflame concierge as default routing"
                          />
                          <div className="w-4 h-4 rounded-full bg-gray-900 text-white text-[10px] font-bold flex items-center justify-center flex-shrink-0 dark:bg-gray-200 dark:text-gray-900">
                            Commonflame
                          </div>
                          <span className="text-gray-700 dark:text-gray-300">
                            @{CONCIERGE_HANDLE}
                          </span>
                          <span className="text-gray-500 dark:text-gray-400 text-xs">
                            (Concierge)
                          </span>
                        </button>
                        {quickActionAgentOptions.map((agent) => {
                          const isDefaultAgent =
                            stripHandle(agent.username).toLowerCase() ===
                            normalizedDefaultAgent;
                          return (
                            <button
                              key={agent.username}
                              type="button"
                              onPointerDown={(e) => e.preventDefault()}
                              onClick={() => {
                                applyDefaultAgentSelection(agent.username, {
                                  closeMenus: true,
                                });
                              }}
                              className="w-full flex items-center space-x-2 px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-xs text-left"
                            >
                              <input
                                type="checkbox"
                                readOnly
                                checked={isDefaultAgent}
                                className="h-3.5 w-3.5 rounded border-gray-300 text-blue-600"
                                aria-label={`Select default agent ${agent.username}`}
                              />
                              <div
                                className="w-4 h-4 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0"
                                style={{ backgroundColor: agent.color }}
                              >
                                {agent.username?.charAt(0).toUpperCase() || "?"}
                              </div>
                              <span className="text-gray-700 dark:text-gray-300">
                                @{agent.username}
                              </span>
                              <span className="text-gray-500 dark:text-gray-400 text-xs">
                                ({humanizeAgentType(agent.agent_type)})
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
            {selectedQuickActionGroup && (
              <div className="flex flex-wrap items-center gap-1 rounded-lg bg-purple-50 px-3 py-2 text-xs text-purple-900 dark:bg-purple-900/20 dark:text-purple-100">
                <span className="font-semibold">
                  {selectedQuickActionGroup.name} members
                </span>
                {selectedQuickActionGroup.members.map((member) => (
                  <button
                    key={`${selectedQuickActionGroup.id}:${member}`}
                    type="button"
                    onPointerDown={(e) => e.preventDefault()}
                    onClick={() => appendMentionToDraft(member)}
                    className="rounded-full bg-white px-2 py-0.5 font-medium text-purple-700 shadow-sm transition-colors hover:bg-purple-100 dark:bg-purple-950/50 dark:text-purple-100 dark:hover:bg-purple-800/50"
                    title={`Add @${member} to this message`}
                  >
                    @{member}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Chat Input */}
        <form
          onSubmit={handleSubmit}
          className="py-3"
          style={{
            paddingBottom: /iPhone|iPod/.test(navigator.userAgent)
              ? "max(1rem, env(safe-area-inset-bottom, 0px))"
              : undefined,
          }}
        >
          {pendingFile && (
            <div className="mb-2">
              <PendingFileChip
                pendingFile={pendingFile}
                uploadProgress={uploadProgress}
                onClear={clearPendingFile}
              />
            </div>
          )}

          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_UPLOAD_ATTR}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) stagePendingFile(file);
              e.target.value = "";
            }}
          />

          <div className="flex items-end gap-1 sm:gap-2">
            {/* Left column: toggle chevron (always) + paperclip (always) stacked vertically */}
            <div className="flex flex-col items-center gap-0.5 flex-shrink-0">
              <button
                type="button"
                onClick={() => setQuickActionsCollapsed(!quickActionsCollapsed)}
                className="h-8 w-8 flex items-center justify-center rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                aria-label={
                  quickActionsCollapsed
                    ? "Show quick actions"
                    : "Hide quick actions"
                }
                title={
                  quickActionsCollapsed
                    ? "Show quick actions"
                    : "Hide quick actions"
                }
              >
                {quickActionsCollapsed ? (
                  <ChevronUp className="w-4 h-4" />
                ) : (
                  <ChevronDown className="w-3 h-3" />
                )}
              </button>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="h-8 w-8 flex items-center justify-center rounded-md text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                aria-label="Attach file"
                title="Attach file"
              >
                <Paperclip className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1">
              <textarea
                ref={textareaRef}
                value={message}
                onChange={handleTextChange}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                autoComplete="off"
                autoCorrect="off"
                data-form-type="other"
                data-lpignore="true"
                placeholder={
                  replyToPost
                    ? `Reply to @${replyToPost.username}...`
                    : defaultAgentUsername
                      ? `Message @${defaultAgentUsername} by default...`
                      : "Type a message... (@ to mention a specific agent, Enter to send)"
                }
                className="w-full resize-none border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-500 dark:placeholder-gray-400"
                rows={1}
                style={{ minHeight: "40px", maxHeight: "120px" }}
                disabled={isSubmitting}
              />
            </div>
            <Button
              type="submit"
              size="sm"
              disabled={(!message.trim() && !pendingFile) || isSubmitting}
              aria-label={replyToPost ? "Send reply" : "Send message"}
              className={`flex-shrink-0 h-10 w-10 p-0 ${replyToPost ? "bg-orange-600 hover:bg-orange-700" : ""}`}
            >
              {isSubmitting ? (
                <div className="w-4 h-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </Button>
          </div>
        </form>
      </div>

      {/* Help Dialog */}
      <HelpDialog
        open={showHelpDialog}
        onOpenChange={setShowHelpDialog}
        onNavigate={(path) => {
          try {
            window.history.pushState({}, "", path);
            window.dispatchEvent(new PopStateEvent("popstate"));
          } catch {
            window.location.assign(path);
          }
        }}
      />
    </div>
  );
}
