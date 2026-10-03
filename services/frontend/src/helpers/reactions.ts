// Reactions-as-Replies Contract Implementation
// A "reaction" = message with parent_id + (single emoji OR !command)

export type ReactionAction = 'kudos' | 'abuse' | 'flag' | 'helpful' | 'excellent' | 'emoji';

// Map legacy commands to reaction types
const COMMAND_MAP: Record<string, ReactionAction> = {
  '!kudos': 'kudos',
  '!like': 'kudos',
  '!thumbsup': 'kudos',
  '!abuse': 'abuse',
  '!flag': 'flag',
  '!helpful': 'helpful',
  '!excellent': 'excellent'
};

// Map common emoji commands to actual emojis
const EMOJI_COMMAND_MAP: Record<string, string> = {
  '!lol': '😂',
  '!laugh': '🤣',
  '!rofl': '🤣',
  '!love': '❤️',
  '!heart': '❤️',
  '!fire': '🔥',
  '!rocket': '🚀',
  '!eyes': '👀',
  '!thinking': '🤔',
  '!think': '🤔',
  '!clap': '👏',
  '!party': '🎉',
  '!celebrate': '🎉',
  '!sad': '😢',
  '!cry': '😭',
  '!angry': '😠',
  '!mad': '😡',
  '!wow': '😮',
  '!shocked': '😱',
  '!mindblown': '🤯',
  '!cool': '😎',
  '!thumbsdown': '👎',
  '!down': '👎',
  '!up': '👍',
  '!wave': '👋',
  '!bye': '👋',
  '!pray': '🙏',
  '!thanks': '🙏',
  '!100': '💯',
  '!perfect': '💯',
  '!check': '✅',
  '!done': '✅',
  '!x': '❌',
  '!no': '❌',
  '!warning': '⚠️',
  '!warn': '⚠️',
  '!question': '❓',
  '!idea': '💡',
  '!bulb': '💡',
  '!star': '⭐',
  '!trophy': '🏆',
  '!win': '🏆',
  '!muscle': '💪',
  '!strong': '💪',
  '!facepalm': '🤦',
  '!shrug': '🤷',
};

// Accepts VS16 + ZWJ sequences + skin tones - single emoji cluster
const EMOJI_CLUSTER = /^(?:\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?(?:\u200D\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?)?)$/u;

// Accepts multiple emojis (with optional spaces between them)
const MULTIPLE_EMOJIS = /^(?:\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?(?:\u200D\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?)?[\s]*)+$/u;

/**
 * Normalize parent_id - now handles UUIDs or numbers
 * Returns the ID as-is if valid (string UUID or number)
 */
export function normalizeParentId(p: any): string | number | undefined {
  if (p == null || p === '') return undefined;
  // If it's a valid UUID string, keep it as string
  if (typeof p === 'string' && p.length > 0) return p;
  // If it's a number, keep it as number
  if (typeof p === 'number') return p;
  // Try to parse as number for backward compat
  const n = Number(p);
  return Number.isNaN(n) ? undefined : n;
}

/**
 * Classify text as a reaction type or null
 * Also handles emoji commands like !lol, !laugh, etc.
 * IMPORTANT: Only returns a reaction if the ENTIRE message is just the command/emoji(s)
 */
export function classifyReactionText(text: string): ReactionAction | null {
  const t = text.trim();

  // Check if it's emoji(s) - single or multiple
  if (EMOJI_CLUSTER.test(t) || MULTIPLE_EMOJIS.test(t)) return 'emoji';

  // Fallback: strip all emoji clusters and common ignorable chars; if nothing remains, it's emoji reaction(s)
  try {
    const stripped = t
      .replace(EMOJI_CLUSTER, '')
      .replace(/[\s\uFFFD'’"“”.,:;!\-_/\\]+/g, '')
      .trim();
    if (stripped.length === 0 && t !== '') {
      return 'emoji';
    }
  } catch {} // eslint-disable-line no-empty

  // Split to check if there's more than just the command
  const parts = t.split(/\s+/);

  // If there's more than one word, it's not a pure reaction
  if (parts.length > 1) return null;

  const first = parts[0].toLowerCase();

  // Check known reaction commands
  if (COMMAND_MAP[first]) return COMMAND_MAP[first];

  // Check emoji commands
  if (EMOJI_COMMAND_MAP[first]) return 'emoji';

  // Check if it's !emoji format (e.g., !😂)
  if (first.startsWith('!') && first.length > 1) {
    const emojiPart = first.substring(1);
    if (EMOJI_CLUSTER.test(emojiPart)) return 'emoji';
  }

  return null;
}

/**
 * Determine if a message is a reaction reply (should be hidden)
 */
export function isReactionReply(content: string, parentId: any): boolean {
  const pid = normalizeParentId(parentId);
  return !!pid && !!classifyReactionText(content);
}

/**
 * Get a short display ID from a full UUID or ID
 * Takes first 8 chars of UUID or full ID if numeric
 */
export function getShortId(id: string | number | undefined): string {
  if (!id) return '';
  const idStr = String(id);
  // If it's a UUID (has dashes), take first segment
  if (idStr.includes('-')) {
    return idStr.split('-')[0];
  }
  // If it's short already (numeric ID), return as-is
  if (idStr.length <= 8) {
    return idStr;
  }
  // Otherwise take first 8 chars
  return idStr.substring(0, 8);
}

/**
 * Normalize reaction content to display emoji
 * Converts commands like !lol to 😂
 * For multiple emojis, returns just the first one (UI will handle splitting)
 */
export function normalizeReactionContent(content: string): string {
  const t = content.trim();

  // If it's already emoji(s), return it
  if (EMOJI_CLUSTER.test(t) || MULTIPLE_EMOJIS.test(t)) return t;

  const lower = t.toLowerCase();

  // Check emoji command map
  if (EMOJI_COMMAND_MAP[lower]) return EMOJI_COMMAND_MAP[lower];

  // Check if it's !emoji format
  if (lower.startsWith('!') && lower.length > 1) {
    const emojiPart = lower.substring(1);
    // If the part after ! is an emoji, return it
    if (EMOJI_CLUSTER.test(emojiPart)) return emojiPart;
  }

  // Default: return original content
  return t;
}

/**
 * Extract the actual emoji from text (for display)
 */
export function extractEmoji(text: string): string | null {
  const t = text.trim();
  if (EMOJI_CLUSTER.test(t)) {
    // Extract the emoji cluster
    const match = t.match(/\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?(?:\u200D\p{Extended_Pictographic}(?:\uFE0F|\uFE0E)?)*?/u);
    return match ? match[0] : null;
  }
  return null;
}

/**
 * Map reaction actions to display emojis
 */
export const REACTION_EMOJI_MAP: Record<string, string> = {
  'kudos': '👍',
  'abuse': '👎',
  'flag': '🚩',
  'helpful': '💡',
  'excellent': '⭐'
};

/**
 * Get emoji for a reaction action
 */
export function getReactionEmoji(action: ReactionAction, originalText?: string): string {
  if (action === 'emoji' && originalText) {
    // For generic emoji reactions, use the original emoji
    return extractEmoji(originalText) || '👍';
  }
  return REACTION_EMOJI_MAP[action] || '👍';
}
