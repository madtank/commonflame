import {
  extractExplicitMentionHandles,
  normalizeAgentHandle,
} from "@/lib/agent-compose";

/**
 * Sticky multi-recipient selection for the composer's pinned agent rail.
 * The selected set is the visible routing state: unaddressed messages fan
 * out to every selected agent; an empty set routes to the concierge.
 */

export function toggleRecipientHandle(
  selected: string[],
  handle: string,
  conciergeHandle: string,
): string[] {
  const normalized = normalizeAgentHandle(handle);
  if (!normalized) return selected;
  if (normalized.toLowerCase() === conciergeHandle.toLowerCase()) {
    return [];
  }
  const key = normalized.toLowerCase();
  const without = selected.filter((entry) => entry.toLowerCase() !== key);
  return without.length === selected.length
    ? [...selected, normalized]
    : without;
}

export function prependAgentMentionsIfNeeded(
  content: string,
  handles: string[],
): string {
  if (handles.length === 0) return content;
  if (extractExplicitMentionHandles(content).length > 0) return content;
  const mentions = handles.map((handle) => `@${handle}`).join(" ");
  if (!content.trim()) return `${mentions} `;
  return `${mentions} ${content}`;
}

export function buildRailSuggestionHandles({
  selected,
  ranked,
  limit,
}: {
  selected: string[];
  ranked: string[];
  limit: number;
}): string[] {
  const seen = new Set(selected.map((handle) => handle.toLowerCase()));
  const suggestions = [...selected];
  for (const handle of ranked) {
    if (suggestions.length >= Math.max(limit, selected.length)) break;
    const key = handle.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    suggestions.push(handle);
  }
  return suggestions;
}

export function parseStoredRecipientHandles(
  raw: string | null,
  legacySingle: string | null,
): string[] {
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      if (
        Array.isArray(parsed) &&
        parsed.every((entry) => typeof entry === "string")
      ) {
        return parsed
          .map((entry) => normalizeAgentHandle(entry))
          .filter((entry): entry is string => Boolean(entry));
      }
    } catch {
      // fall through to legacy/default
    }
    return [];
  }
  const legacy = normalizeAgentHandle(legacySingle);
  return legacy ? [legacy] : [];
}
