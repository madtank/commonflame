type HarnessEventLike = Record<string, unknown>;

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function getStringField(
  payload: HarnessEventLike | null,
  field: string,
): string | null {
  const direct = payload?.[field];
  if (typeof direct === "string" && direct) return direct;
  const nested = asRecord(payload?.message)?.[field];
  if (typeof nested === "string" && nested) return nested;
  return null;
}

export function matchesHarnessRunEvent(
  payload: HarnessEventLike | null,
  currentRunMessageId: string | null,
  currentConversationId: string | null,
) {
  if (!payload) return false;
  if (!currentRunMessageId && !currentConversationId) return true;

  const eventMessageId =
    getStringField(payload, "message_id") || getStringField(payload, "id");
  const parentId = getStringField(payload, "parent_id");
  const conversationId = getStringField(payload, "conversation_id");

  const ids = [
    eventMessageId,
    parentId,
    conversationId,
    currentRunMessageId,
    currentConversationId,
  ].filter((value): value is string => Boolean(value));

  if (!ids.length) return false;

  return (
    (currentRunMessageId != null &&
      (eventMessageId === currentRunMessageId ||
        parentId === currentRunMessageId ||
        conversationId === currentRunMessageId)) ||
    (currentConversationId != null &&
      (eventMessageId === currentConversationId ||
        parentId === currentConversationId ||
        conversationId === currentConversationId))
  );
}
