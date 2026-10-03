import type { SpaceAgentWidgetDescriptor } from "@/lib/space-agent-api";
import { getSpaceAgentSurfacePolicyId } from "@/lib/space-agent-surface-policy";

const COUNT_LABELS: Record<string, string> = {
  agents: "agent",
  context: "entry",
  messages: "message",
  search: "result",
  spaces: "space",
  tasks: "task",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown) {
  return typeof value === "string" ? value : null;
}

function truncate(text: string, max: number) {
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

function parseJsonish(value: string) {
  const trimmed = value.trim();
  if (!trimmed || !(trimmed.startsWith("{") || trimmed.startsWith("["))) {
    return null;
  }

  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function findReadableMessage(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (!value || typeof value !== "object") return null;

  const record = value as Record<string, unknown>;
  for (const field of ["message", "error", "reason", "detail"]) {
    const candidate = record[field];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
    const nested = findReadableMessage(candidate);
    if (nested) return nested;
  }

  return null;
}

export function normalizeWidgetNoticeText(value?: string | null) {
  if (!value?.trim()) return null;
  const trimmed = value.trim();
  const parsed = parseJsonish(trimmed);
  return truncate(findReadableMessage(parsed) || trimmed, 220);
}

function normalizeAction(widget: SpaceAgentWidgetDescriptor) {
  const toolAction =
    typeof widget.tool_action === "string" ? widget.tool_action : null;
  const toolInputAction =
    typeof widget.tool_input === "object" &&
    widget.tool_input &&
    typeof (widget.tool_input as Record<string, unknown>).action === "string"
      ? ((widget.tool_input as Record<string, unknown>).action as string)
      : null;
  return (toolAction || toolInputAction || "").trim().toLowerCase();
}

function getStructuredPayload(widget: SpaceAgentWidgetDescriptor) {
  const toolResult = asRecord(widget.tool_result);
  const structuredContent =
    asRecord(toolResult?.structuredContent) ||
    asRecord(widget.structured_content);
  return structuredContent || toolResult;
}

function getNumericField(
  payload: Record<string, unknown> | null,
  field: string,
) {
  const value = payload?.[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function getArrayLength(
  payload: Record<string, unknown> | null,
  field: string,
) {
  const value = payload?.[field];
  return Array.isArray(value) ? value.length : null;
}

function getWidgetCount(widget: SpaceAgentWidgetDescriptor) {
  const payload = getStructuredPayload(widget);
  const directCount =
    getNumericField(payload, "count") ??
    getNumericField(payload, "total") ??
    getNumericField(asRecord(payload?.data), "count") ??
    getNumericField(asRecord(payload?.data), "total");
  if (directCount != null) return directCount;

  const data = asRecord(payload?.data);
  return (
    getArrayLength(payload, "items") ??
    getArrayLength(payload, "results") ??
    getArrayLength(payload, "spaces") ??
    getArrayLength(payload, "members") ??
    getArrayLength(data, "items") ??
    getArrayLength(data, "results")
  );
}

export function extractWidgetDetailLabel(widget: SpaceAgentWidgetDescriptor) {
  const sources = [widget.tool_input, widget.tool_result];
  for (const data of sources) {
    if (!data || typeof data !== "object") continue;
    const record = data as Record<string, unknown>;
    for (const field of ["title", "name", "key", "summary", "label"]) {
      const value = record[field];
      if (typeof value === "string" && value.trim()) {
        const trimmed = value.trim();
        return trimmed.length > 60 ? `${trimmed.slice(0, 57)}...` : trimmed;
      }
    }
  }
  return null;
}

function findMemoryEntryValue(
  obj: Record<string, unknown>,
  targetKey: string,
): string | null {
  const candidates = [
    asRecord(asRecord(obj.data)?.memory),
    asRecord(obj.memory),
    obj,
  ];

  for (const container of candidates) {
    if (!container) continue;
    const items = container.items;
    if (!Array.isArray(items)) continue;

    for (const entry of items) {
      if (
        entry &&
        typeof entry === "object" &&
        (entry as Record<string, unknown>).key === targetKey
      ) {
        const value = (entry as Record<string, unknown>).value;
        if (typeof value === "string" && value.trim()) {
          return truncate(value.trim(), 220);
        }
      }
    }
  }

  return null;
}

export function extractWidgetDetailContent(widget: SpaceAgentWidgetDescriptor) {
  const input = asRecord(widget.tool_input);
  const result = asRecord(widget.tool_result);
  const targetKey = input?.key;

  if (typeof targetKey === "string" && result) {
    const memoryValue = findMemoryEntryValue(result, targetKey);
    if (memoryValue) return memoryValue;
  }

  for (const data of [input, result, getStructuredPayload(widget)]) {
    if (!data) continue;

    for (const field of [
      "content",
      "value",
      "description",
      "body",
      "text",
      "note",
      "summary",
      "message",
    ]) {
      const value = data[field];
      if (typeof value === "string" && value.trim()) {
        return truncate(value.trim(), 220);
      }
    }
  }

  return null;
}

export function shouldDefaultCollapseWidget(
  _widget: SpaceAgentWidgetDescriptor,
) {
  // Fold is user-controlled for now. New widgets should open expanded.
  return false;
}

export function getCollapsedWidgetPreview(
  widget: SpaceAgentWidgetDescriptor,
  title?: string | null,
) {
  const policyId = getSpaceAgentSurfacePolicyId(widget);
  const action = normalizeAction(widget);
  const count = getWidgetCount(widget);

  if (count != null) {
    const noun =
      action === "members"
        ? "member"
        : COUNT_LABELS[policyId] || COUNT_LABELS[action] || "item";
    return `${count} ${noun}${count === 1 ? "" : "s"}`;
  }

  const detailContent = extractWidgetDetailContent(widget);
  if (detailContent) return detailContent;

  const detailLabel = extractWidgetDetailLabel(widget);
  if (detailLabel && detailLabel !== title) return detailLabel;

  const fallback =
    asString(asRecord(widget.tool_result)?.summary) ||
    asString(asRecord(widget.structured_content)?.summary) ||
    normalizeWidgetNoticeText(widget.fallback_text);

  return fallback ? truncate(fallback, 220) : "Open for details.";
}
