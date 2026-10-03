/**
 * Author Type Detection Utility
 *
 * Determines whether a message author is a USER (human), AGENT, or ADMIN.
 * This is critical for:
 * - Displaying correct labels (USER vs AGENT vs ADMIN badge)
 * - Showing/hiding AI trust scores (only for agents)
 * - Showing/hiding owner information (only for agents)
 *
 * IMPORTANT: This logic has regressed multiple times. Any changes must be
 * accompanied by test updates to prevent future regressions.
 */

export type AuthorRole = "USER" | "AGENT" | "ADMIN";

export interface AuthorTypeInput {
  // Primary indicators (from backend)
  author_type?: string | null; // 'agent', 'human', 'user', 'admin'
  sender_type?: string | null; // 'agent', 'human', 'user', 'admin'

  // Secondary indicators
  agent_type?: string | null; // 'agent', 'human', 'user'

  // IDs for fallback logic
  agent_id?: string | number | null;
  author_id?: string | null;
  user_id?: string | null;

  // Nested author object (some API responses)
  author?: {
    type?: string | null;
    id?: string | null;
  } | null;

  // Metadata for role detection
  metadata?: Record<string, any> | null;
}

export interface RosterEntryInput {
  type?: "human" | "agent" | string | null;
  metadata?: Record<string, any> | null;
  tags?: string[];
}

/**
 * Normalizes a type string for comparison
 */
function normalizeType(value: unknown): string {
  if (value == null) return "";
  return String(value).toLowerCase().trim();
}

/**
 * Checks if a type value indicates a human user
 */
function isHumanType(type: string): boolean {
  const normalized = normalizeType(type);
  return normalized === "human" || normalized === "user";
}

/**
 * Checks if a type value indicates an admin
 */
function isAdminType(type: string): boolean {
  const normalized = normalizeType(type);
  return normalized === "admin";
}

/**
 * Checks if a type value indicates an agent
 */
function isAgentType(type: string): boolean {
  const normalized = normalizeType(type);
  return normalized === "agent";
}

/**
 * Determines if a message author is a USER, AGENT, or ADMIN.
 *
 * Priority order:
 * 1. author_type from backend (most explicit)
 * 2. Nested author.type object
 * 3. sender_type field
 * 4. Roster entry type and metadata
 * 5. agent_type field
 * 6. Default to USER
 *
 * @param post - The message/post object with author information
 * @param rosterEntry - Optional roster entry for the author
 * @returns 'USER', 'AGENT', or 'ADMIN'
 */
export function deriveAuthorRole(
  post: AuthorTypeInput,
  rosterEntry?: RosterEntryInput | null,
): AuthorRole {
  // Helper to check for admin status in metadata or tags
  const checkAdminMetadata = (
    meta?: Record<string, any> | null,
    tags?: string[],
  ) => {
    if (!meta && (!tags || tags.length === 0)) return false;

    // Check common admin flags in metadata
    if (meta) {
      if (
        meta.admin === true ||
        meta.is_admin === true ||
        meta.role === "admin" ||
        meta.role === "ADMIN"
      ) {
        return true;
      }
    }

    // Check tags
    if (tags && (tags.includes("admin") || tags.includes("ADMIN"))) {
      return true;
    }

    return false;
  };

  // Priority 1: Explicit author_type from backend
  if (post.author_type != null) {
    if (isAdminType(post.author_type)) return "ADMIN";
    if (isAgentType(post.author_type)) return "AGENT";
    if (isHumanType(post.author_type)) {
      if (checkAdminMetadata(post.metadata, [])) return "ADMIN";
      return "USER";
    }
  }

  // Priority 2: Nested author.type object
  if (post.author?.type != null) {
    if (isAdminType(post.author.type)) return "ADMIN";
    if (isAgentType(post.author.type)) return "AGENT";
    if (isHumanType(post.author.type)) return "USER";
  }

  // Priority 3: sender_type field
  if (post.sender_type != null) {
    if (isAdminType(post.sender_type)) return "ADMIN";
    if (isAgentType(post.sender_type)) return "AGENT";
    if (isHumanType(post.sender_type)) return "USER";
  }

  // Priority 4: Roster entry type and metadata
  if (rosterEntry) {
    if (rosterEntry.type != null) {
      if (isAgentType(rosterEntry.type)) return "AGENT";
      // If it's human/user or unknown, check metadata for admin
      if (checkAdminMetadata(rosterEntry.metadata, rosterEntry.tags || []))
        return "ADMIN";
      if (isHumanType(rosterEntry.type)) return "USER";
    } else {
      // No explicit type but check metadata anyway
      if (checkAdminMetadata(rosterEntry.metadata, rosterEntry.tags || []))
        return "ADMIN";
    }
  }

  // Check post metadata as fallback for admin status
  if (checkAdminMetadata(post.metadata, [])) return "ADMIN";

  // Priority 5: agent_type field (legacy)
  if (post.agent_type != null) {
    if (isAgentType(post.agent_type)) return "AGENT";
    if (isHumanType(post.agent_type)) return "USER";
  }

  // IMPORTANT: Default to USER
  return "USER";
}

/**
 * Determines if AI trust/quality scores should be displayed for a message.
 * Scores should ONLY be shown for agent messages, never for human users.
 *
 * @param role - The derived author role
 * @returns true if scores should be displayed
 */
export function shouldShowTrustScores(role: AuthorRole): boolean {
  return role === "AGENT";
}

/**
 * Determines if owner information should be displayed for a message.
 * Owner info should ONLY be shown for agent messages.
 *
 * @param role - The derived author role
 * @returns true if owner info should be displayed
 */
export function shouldShowOwnerInfo(role: AuthorRole): boolean {
  return role === "AGENT";
}
