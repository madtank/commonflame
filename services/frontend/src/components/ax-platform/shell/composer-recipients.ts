import { extractExplicitMentionHandles } from "@/lib/agent-compose";

export type DraftRecipientSource = "explicit" | "default";

export type DraftRecipient = {
  handle: string;
  source: DraftRecipientSource;
};

export function deriveDraftRecipients({
  draft,
  knownHandles,
  defaultAgentHandle,
}: {
  draft: string;
  knownHandles: Iterable<string>;
  defaultAgentHandle: string | null;
}): DraftRecipient[] {
  const directory = new Map<string, string>();
  for (const handle of knownHandles) {
    directory.set(handle.toLowerCase(), handle);
  }

  const seen = new Set<string>();
  const explicit: DraftRecipient[] = [];
  for (const mention of extractExplicitMentionHandles(draft)) {
    const key = mention.toLowerCase();
    const known = directory.get(key);
    if (!known || seen.has(key)) continue;
    seen.add(key);
    explicit.push({ handle: known, source: "explicit" });
  }

  if (explicit.length > 0) return explicit;
  if (defaultAgentHandle) {
    return [{ handle: defaultAgentHandle, source: "default" }];
  }
  return [];
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function removeMentionFromDraft(draft: string, handle: string) {
  const pattern = new RegExp(`(^|\\s)@${escapeRegExp(handle)}(?![\\w-])`, "gi");
  return draft
    .replace(pattern, "$1")
    .replace(/[^\S\n]{2,}/g, " ")
    .trim();
}

export function toggleHandleSelection(selected: string[], handle: string) {
  const key = handle.toLowerCase();
  const without = selected.filter((entry) => entry.toLowerCase() !== key);
  return without.length === selected.length ? [...selected, handle] : without;
}

export function applyMentionSelections(draft: string, handles: string[]) {
  const alreadyMentioned = new Set(
    extractExplicitMentionHandles(draft).map((entry) => entry.toLowerCase()),
  );
  const additions = handles.filter(
    (handle) => !alreadyMentioned.has(handle.toLowerCase()),
  );

  const inProgressToken = /(^|\s)@[\w-]*$/;
  const base = inProgressToken.test(draft)
    ? draft.replace(inProgressToken, "$1")
    : draft;

  if (additions.length === 0) {
    return base === draft ? draft : `${base.trimEnd()} `.replace(/^ $/, "");
  }

  const mentionText = additions.map((handle) => `@${handle}`).join(" ");
  if (!base.trim()) return `${mentionText} `;
  return `${base.trimEnd()} ${mentionText} `;
}

export type DeliveryOutcomeState = "delivered" | "sent" | "failed";

export type DeliveryOutcome = {
  state: DeliveryOutcomeState;
  label: string;
  wokeCount: number;
};

const UNCONFIRMED_SEND_LABEL = "Send not confirmed — check before retrying";
const MAX_LISTED_RECIPIENTS = 3;

export function describeDeliveryOutcome({
  receipt,
  error,
}: {
  receipt?: {
    message_id?: string | null;
    received_by?: string[] | null;
    server_confirmed?: boolean;
  } | null;
  error?: unknown;
}): DeliveryOutcome {
  if (error !== undefined) {
    return {
      state: "failed",
      label: "Send failed — message kept in queue",
      wokeCount: 0,
    };
  }

  // server_confirmed === false means the API layer synthesized message_id
  // because the server never returned one — that is not a real ack.
  if (!receipt?.message_id || receipt.server_confirmed === false) {
    return { state: "failed", label: UNCONFIRMED_SEND_LABEL, wokeCount: 0 };
  }

  const receivedBy = receipt.received_by?.filter(Boolean) ?? [];
  if (receivedBy.length === 0) {
    return { state: "sent", label: "Sent", wokeCount: 0 };
  }

  if (receivedBy.length > MAX_LISTED_RECIPIENTS) {
    return {
      state: "delivered",
      label: `Delivered · woke ${receivedBy.length} agents`,
      wokeCount: receivedBy.length,
    };
  }

  return {
    state: "delivered",
    label: `Delivered to ${receivedBy.map((handle) => `@${handle}`).join(" · ")}`,
    wokeCount: receivedBy.length,
  };
}
