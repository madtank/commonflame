/**
 * Agent-related helper utilities
 */

/**
 * Normalize a handle/username for comparison.
 * Removes @ prefix, trims whitespace, lowercases, and strips zero-width chars.
 */
export const normalizeHandle = (value: unknown): string =>
  String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/^@/, "")
    .replace(/[\u200B-\u200D\uFEFF]/g, "");

/**
 * Check if a handle belongs to an internal system agent.
 * Internal agents follow the pattern: __name__ (double underscores on both sides)
 * Examples: __ai_validator__, __system__
 */
export const isInternalHandle = (value: unknown): boolean => {
  const normalized = normalizeHandle(value);
  return normalized.startsWith("__") && normalized.indexOf("__", 2) !== -1;
};

/**
 * Check if a post/message is from an internal agent by checking multiple
 * possible name fields.
 */
export const isFromInternalAgent = (post: {
  username?: string;
  handle?: string;
  author?: { username?: string; name?: string; handle?: string };
  agent_name?: string;
  metadata?: { agent_name?: string; username?: string };
}): boolean => {
  const candidateNames = [
    post.username,
    post.handle,
    post.author?.username,
    post.author?.name,
    post.author?.handle,
    post.agent_name,
    post.metadata?.agent_name,
    post.metadata?.username,
  ];
  return candidateNames.some(isInternalHandle);
};
