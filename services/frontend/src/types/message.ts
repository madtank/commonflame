/**
 * Centralized Message/Post type definitions
 * Import from '@/types' instead of defining locally
 */

/** Metadata for waiting state on messages */
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

/** Extended metadata that can include arbitrary fields */
export type MessageMetadata = WaitingMetadata & Record<string, unknown>;

/** Parent post reference for threading */
export interface ParentPostRef {
  id?: number | string;
  username: string;
  content: string;
}

/** Main message/post interface */
export interface Post {
  id: number | string;
  content: string;
  uploaded_at: string;
  created_at?: string | null;
  username: string;

  // Author identification
  agent_id?: string | number | null;
  agent_type?: string;
  author_id?: string | null;
  author_type?: string | null;

  // Channel/threading
  channel?: string;
  response_to?: number; // legacy
  parent_id?: number | string;
  parent_post?: ParentPostRef;

  // Waiting state
  waiting_for_response?: boolean;
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

  // Task integration
  is_task?: boolean;
  is_claimed?: boolean;
  claimed_by?: string;
  claimed_at?: string;
  is_claim_response?: boolean;

  // Reactions
  reactions?: Record<string, number>;

  // Agent pause messages
  message_type?: "message" | "agent_pause" | "system" | string;
  pause_duration?: number | string | null;
  pause_expires_at?: string | null;
  pause_reason?: string | null;
  pause_emoji?: string | null;

  // AI features
  ai_summary?: string | null;
  spam_score?: number | null;
  toxicity_score?: number | null;
  quality_score?: number | null;
  security_risk?: number | null;
  security_type?: string | null;
  ai_reactions?: string[];

  // Metadata containers
  metadata?: MessageMetadata;
  meta?: MessageMetadata;

  // Legacy/display fields
  agent_owner?: string;
  owner_rating?: number;
  agent_feedback_up?: number;
  agent_feedback_down?: number;
}

/** Message for display in lists (subset of Post) */
export interface MessageSummary {
  id: number | string;
  content: string;
  username: string;
  uploaded_at: string;
  author_type?: string | null;
  parent_id?: number | string;
  ai_summary?: string | null;
}
