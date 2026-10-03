export function normalizeAgentHandle(value: string | null | undefined) {
  if (!value) return null;
  const normalized = value.trim().replace(/^@+/, "");
  return normalized || null;
}

export function appendAgentMentionToCompose(value: string | null | undefined) {
  const handle = normalizeAgentHandle(value);
  if (!handle || typeof window === "undefined") return false;

  window.dispatchEvent(
    new CustomEvent("ax:agent-mention-append", {
      detail: { handle },
    }),
  );
  return true;
}

export function extractExplicitMentionHandles(content: string) {
  const explicitMentionPattern = /(?:^|\s)@([\w-]+)/g;
  return Array.from(
    content.matchAll(explicitMentionPattern),
    (match) => normalizeAgentHandle(match[1]) || "",
  ).filter(Boolean);
}

export function prependAgentMentionIfNeeded(
  content: string,
  value: string | null | undefined,
) {
  const handle = normalizeAgentHandle(value);
  if (!handle) return content;

  const explicitMentions = extractExplicitMentionHandles(content);

  if (explicitMentions.includes(handle)) {
    return content;
  }

  if (explicitMentions.length > 0) {
    return content;
  }

  if (!content.trim()) {
    return `@${handle} `;
  }

  return `@${handle} ${content}`;
}
