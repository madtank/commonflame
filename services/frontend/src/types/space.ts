/**
 * Centralized Space type definitions
 * Import from '@/types' instead of defining locally
 */

/** Space subscription tier */
export type SpaceTier = "regular" | "plus" | "admin";

/** Space definition */
export interface Space {
  id: string;
  name: string;
  slug?: string;
  description?: string;
  created_at?: string;
  updated_at?: string;
  owner_id?: string;
  owner_username?: string;
  settings?: SpaceSettings;
  member_count?: number;
  agent_count?: number;
  is_personal?: boolean;
  tier?: SpaceTier; // Subscription tier for feature access
  // Guest access fields
  /** Caller's role in this space — present on all responses */
  viewer_role?: "owner" | "admin" | "member" | "viewer" | "guest";
  /** Active guest members — omitted/null for member/guest callers (owners/admins only) */
  guest_count?: number;
  show_member_list_to_guests?: boolean;
}

/** Space settings */
export interface SpaceSettings {
  allow_public_agents?: boolean;
  require_approval?: boolean;
  default_agent_visibility?: "public" | "private" | "space";
  features?: string[];
}

/** Space membership */
export interface SpaceMember {
  user_id: string;
  username: string;
  role: "owner" | "admin" | "member" | "viewer" | "guest";
  joined_at?: string;
  avatar_url?: string | null;
}

/**
 * Channel with guest access flag.
 * Backend key: channel_name (string) + space_id — no UUID per channel.
 * Endpoint: GET/PATCH /api/v1/spaces/{space_id}/channels/{channel_name}
 */
export interface Channel {
  channel_name: string;
  space_id: string;
  guest_accessible: boolean;
  updated_at: string | null;
}

/** Space invite link */
export interface SpaceInvite {
  id: string;
  space_id: string;
  created_by: string;
  expires_at: string | null; // null = never
  max_uses: number | null; // null = unlimited
  use_count: number;
  revoked_at: string | null;
  created_at: string;
  // Token URL returned once on creation, never stored or returned again
  invite_url?: string;
}

/** Stored space state (from localStorage) */
export interface StoredSpace {
  id: string;
  name: string;
  slug?: string;
  is_personal?: boolean;
}

// Legacy aliases for backward compatibility during migration
/** @deprecated Use Space instead */
export type Organization = Space;
/** @deprecated Use SpaceTier instead */
export type OrganizationTier = SpaceTier;
/** @deprecated Use SpaceSettings instead */
export type OrganizationSettings = SpaceSettings;
/** @deprecated Use SpaceMember instead */
export type OrganizationMember = SpaceMember;
/** @deprecated Use StoredSpace instead */
export type StoredOrganization = StoredSpace;
