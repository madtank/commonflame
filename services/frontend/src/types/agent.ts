/**
 * Centralized Agent type definitions
 * Import from '@/types' instead of defining locally
 */

// RosterEntry is defined in api-clean.ts, use loose typing here to avoid circular deps
// Components that need full RosterEntry should import from api-clean directly
export interface RosterEntryRef {
  id?: string;
  handle?: string;
  type?: "agent" | "human" | string;
  presence?: string;
  bio?: string;
}

/** Agent control state for real-time status */
export interface AgentControlState {
  isRunning?: boolean;
  isPaused?: boolean;
  lastActivity?: string;
  currentAction?: string;
  is_disabled?: boolean;
  disabled_reason?: string | null;
  disabled_by?: string[];
  disabled_until?: string | null;
  no_reply?: boolean;
  no_reply_reason?: string | null;
  no_reply_by?: string[];
  no_reply_until?: string | null;
  routing_only?: boolean;
  routing_only_reason?: string | null;
  routing_only_by?: string[];
  routing_only_until?: string | null;
}

/** Base agent interface - use for most agent displays */
export interface Agent {
  id: string;
  username: string;
  agent_type: "user" | "general" | string;
  bio?: string;
  status?: "active" | "inactive" | "busy" | "error" | string;
  capabilities?: Record<string, unknown>;
  permissions?: Record<string, unknown>;
  last_seen?: string;
  last_activity?: string;
  avatar_url?: string | null;
  posts_count?: number;
  post_count?: number;

  // Team membership
  team?: string;
  team_id?: string;
  team_name?: string;
  team_color?: string;
  team_role?: string;

  // Organization/Space & mobility
  // space_id is the canonical field (org→space rename); org_id kept for backward compat
  space_id?: string;
  org_id?: string;
  pinned_to_org?: string | null;
  pinned_to_space?: string | null;
  pinned_space_id?: string | null;
  pinned_org_id?: string | null;
  follow_user?: boolean;
  settings?: { follow_user?: boolean };

  // Task tracking
  current_task?: string;
  task_claimed_at?: string;
  task_progress?: number;
  tasks_completed?: number;
  tasks_assigned?: number;
  completion_rate?: number;

  // Ownership
  is_own_agent?: boolean;
  owner_username?: string;
  roster_entry?: RosterEntryRef;

  // Cloud agent capabilities
  control?: AgentControlState;
  cloud_function_url?: string | null;
  enable_cloud_agent?: boolean;
  is_automated?: boolean;
  web_browsing_enabled?: boolean;
  ax_mcp_enabled?: boolean;

  // Agent v2 engine version (for gradual rollout)
  engine_version?: "v1" | "v2";
  tools_enabled?: string[]; // e.g., ['image_gen', 'web_research', 'code_exec']

  // LLM model selection for cloud agents
  model?: string; // e.g., 'gemini-2.5-flash', 'gemini-2.5-pro'
  model_tier?: "standard" | "lite" | "premium"; // Quality/cost tier selection

  // Trust/Intelligence scores
  trust_score?: number | null;
  avg_quality_score?: number | null;
  avg_spam_score?: number | null;
  avg_toxicity_score?: number | null;
  messages_analyzed?: number | null;

  // Reactions
  reactions?: Record<string, number>;
}

/** Cloud agent with required cloud fields */
export interface CloudAgent extends Agent {
  enable_cloud_agent: true;
  cloud_function_url?: string | null;
  is_global?: boolean;
}

/** Simplified agent for lists/dropdowns */
export interface AgentSummary {
  id?: string;
  username: string;
  agent_type?: string;
  bio?: string;
  status?: string;
  avatar_url?: string | null;
  space_id?: string;
  org_id?: string;
  team_id?: string;
  team_name?: string;
  team_color?: string;
  last_activity?: string;
  post_count?: number;
}

/** Agent activity tracking */
export interface RecentAgentActivity {
  username: string;
  lastMessageAt: string;
  messageCount: number;
}

/** Team definition */
export interface Team {
  id: string;
  name: string;
  description?: string;
  color?: string;
  agents?: string[];
  agent_count?: number;
}
