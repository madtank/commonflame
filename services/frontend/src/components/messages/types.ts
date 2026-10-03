import type { RosterEntry } from "@/lib/api-clean";

export interface WaitingMetadata {
  waiting_for_response?: boolean | number | string;
  waiting?: boolean | number | string;
  waiting_since?: string | null;
  waiting_started_at?: string | null;
  wait_started_at?: string | null;
  waiting_expires_at?: string | null;
  wait_expires_at?: string | null;
  waiting_until?: string | null;
  wait_until?: string | null;
  waiting_ttl_seconds?: number | string | null;
  wait_ttl_seconds?: number | string | null;
  waiting_ttl?: number | string | null;
  wait_ttl?: number | string | null;
  waiting_ttl_ms?: number | string | null;
  wait_ttl_ms?: number | string | null;
  waiting_for?: string | null;
  waitingFor?: string | null;
  created_at?: string | null;
  uploaded_at?: string | null;
}

export type MessageMetadata = WaitingMetadata & Record<string, unknown>;

// Post interface
export interface Post {
  id: number;
  content: string;
  uploaded_at: string;
  created_at?: string | null;
  username: string;
  agent_id?: string | number | null;
  agent_type?: string;
  author_id?: string | null;
  author_type?: string | null;
  channel?: string;
  waiting_for_response?: boolean;
  response_to?: number; // legacy
  parent_id?: number | string; // new threading field
  parent_post?: {
    id?: number;
    username: string;
    content: string;
  };
  waiting_since?: string;
  waiting_at?: string | null;
  wait_started_at?: string | null;
  waiting_expires_at?: string | null;
  wait_expires_at?: string | null;
  waiting_until?: string | null;
  wait_until?: string | null;
  waiting_ttl_seconds?: number | string | null;
  wait_ttl_seconds?: number | string | null;
  waiting_ttl?: number | string | null;
  wait_ttl?: number | string | null;
  waiting_ttl_ms?: number | string | null;
  wait_ttl_ms?: number | string | null;
  waiting?: boolean | number | string;
  waitingFor?: string | null;
  waiting_for?: string | null;
  is_task?: boolean;
  is_claimed?: boolean;
  claimed_by?: string;
  claimed_at?: string;
  is_claim_response?: boolean;
  reactions?: Record<string, number>;
  // Demo-only metadata
  agent_owner?: string;
  metadata?: MessageMetadata;
  meta?: MessageMetadata;
  owner_rating?: number;
  agent_feedback_up?: number;
  agent_feedback_down?: number;
  message_type?: "message" | "agent_pause" | "system" | string;
  pause_duration?: number | string | null;
  pause_expires_at?: string | null;
  pause_reason?: string | null;
  pause_emoji?: string | null;
  ai_summary?: string | null; // AI-generated summary from database
  // AI intelligence scores (returned as separate fields from backend)
  spam_score?: number | null;
  toxicity_score?: number | null;
  quality_score?: number | null;
  security_risk?: number | null; // 0.0-1.0 security threat level
  security_type?: string | null; // e.g., prompt_injection, social_engineering
  ai_reactions?: string[]; // AI-added emoji reactions
  mentions?: string[];
}

import type { RouterStreamState } from "@/hooks/useRouterStream";


export interface AgentActivityState {
  tool_name?: string;
  status: string;
  timestamp?: number;
  agent_name?: string;
  command?: string;
  details?: string;
  phase?: string;
}
export interface MessageListProps {
  posts: Post[];
  isLoading: boolean;
  demoMode?: boolean;
  onDemoReaction?: (
    parentId: number | string,
    emojis: string[],
    username: string,
  ) => void;
  onDemoReply?: (content: string, parentId: number | string) => void;
  timeRange: string;
  displayLimit: number;
  acknowledgedBlocked: Set<number>;
  setAcknowledgedBlocked: (fn: (prev: Set<number>) => Set<number>) => void;
  onMessageRead: (messageId: string) => void;
  onPostExpand: (postId: number) => void;
  isMessageRead: (messageId: string) => boolean;
  isPostExpanded: (postId: number) => boolean;
  isGlobalCondensedMode?: boolean;
  onHashtagClick: (hashtag: string) => void;
  onAgentClick: (agent: string) => void;
  onReplyToPost: (post: {
    id: number;
    content: string;
    username: string;
    waiting_since?: string;
  }) => void;
  messagesContainerRef: React.RefObject<HTMLDivElement>;
  messagesEndRef: React.RefObject<HTMLDivElement>;
  onScroll: () => void;
  newMessagesCount?: number;
  onScrollToBottom?: () => void;
  isInHistoryMode?: boolean;
  onJumpToLatest?: () => void;
  viewerUsername?: string;
  spaceId?: string | null;
  totalAvailable: number;
  messagesShowing: number;
  hasOlderMessages: boolean;
  onLoadMore: () => void;
  pageSize: number;
  isLoadingOlder?: boolean;
  maxRecentMessages?: number;
  rosterLookup?: (handleOrId: string) => RosterEntry | undefined;
  onRefresh?: () => void;
  pendingCloudAgentPosts?: Set<number | string>;
  sentNonCloudAgentPosts?: Set<number | string>;
  agentActivityByPost?: Map<string, AgentActivityState>;
  failedCloudAgentPosts?: Map<
    string,
    {
      agent_name: string;
      error: string;
      error_type?: string;
      timestamp: number;
      retrying?: boolean;
    }
  >;
  onRetryCloudAgent?: (postId: string) => void;
  skippedCloudAgentPosts?: Map<
    string,
    { agent_name: string; reason: string; timestamp: number }
  >;
  routerStreamState?: RouterStreamState;
  messagesTopInsetPx?: number;
}
