export const CONCIERGE_HANDLE = "Commonflame";

export function normalizeHandle(value: string | null | undefined) {
  return String(value || "")
    .trim()
    .replace(/^@/, "");
}

export function escapeMentionHandle(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function prependMentionToMessage(text: string, username: string) {
  const handle = normalizeHandle(username);
  const trimmedText = text.trim();
  if (!handle) return trimmedText;
  if (!trimmedText) return `@${handle}`;
  const mentionPattern = new RegExp(
    String.raw`(^|\s)@${escapeMentionHandle(handle)}(?=\s|$|[^\w-])`,
    "i",
  );
  if (mentionPattern.test(trimmedText)) return trimmedText;
  return `@${handle} ${trimmedText}`;
}

export function extractMentionUsernames(text: string) {
  const matches: string[] = [];
  const mentionPattern = /(^|\s)@([\w-]+)(?=\s|$|[^\w-])/gi;
  let match: RegExpExecArray | null;
  while ((match = mentionPattern.exec(text)) !== null) {
    const username = normalizeHandle(match[2] || "").toLowerCase();
    if (username) {
      matches.push(username);
    }
  }
  return matches;
}

export function getKnownMentionedAgentUsernames(
  text: string,
  resolveAgentUsernameToId: Map<string, string>,
) {
  return Array.from(
    new Set(
      extractMentionUsernames(text).filter((username) =>
        resolveAgentUsernameToId.has(normalizeHandle(username).toLowerCase()),
      ),
    ),
  );
}

export function resolveMentionedAgentIds(
  usernames: string[],
  resolveAgentUsernameToId: Map<string, string>,
) {
  const ids = usernames
    .map((raw) => {
      const normalized = normalizeHandle(raw).toLowerCase();
      return resolveAgentUsernameToId.get(normalized);
    })
    .filter((id): id is string => Boolean(id));
  return Array.from(new Set(ids));
}

export function planStickyRouting(
  messageText: string,
  defaultAgentUsername: string | null,
  resolveAgentUsernameToId: Map<string, string>,
  conciergeHandle = CONCIERGE_HANDLE,
) {
  const allMentionUsernames = extractMentionUsernames(messageText);
  const firstMention = allMentionUsernames[0];
  const nextDefaultAgent =
    typeof firstMention === "string"
      ? firstMention === normalizeHandle(conciergeHandle).toLowerCase()
        ? null
        : resolveAgentUsernameToId.has(firstMention)
          ? normalizeHandle(firstMention)
          : undefined
      : undefined;

  const routedMessageBase =
    allMentionUsernames.length === 0 && defaultAgentUsername
      ? prependMentionToMessage(messageText, defaultAgentUsername)
      : messageText.trim();

  const knownMentionedAgentUsernames = getKnownMentionedAgentUsernames(
    routedMessageBase,
    resolveAgentUsernameToId,
  );
  const mentionedAgentIds = resolveMentionedAgentIds(
    knownMentionedAgentUsernames,
    resolveAgentUsernameToId,
  );

  return {
    allMentionUsernames,
    nextDefaultAgent,
    routedMessageBase,
    knownMentionedAgentUsernames,
    mentionedAgentIds,
  };
}
