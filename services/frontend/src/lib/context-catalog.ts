import { apiClient } from "@/lib/api-clean";

export type ContextCatalogSourceLane =
  | "trusted_named_action"
  | "in_world_proposal"
  | string;

export type ContextCatalogConfirmation =
  | "none"
  | "optional"
  | "required"
  | string;

export interface ContextCatalogActionDefinition {
  id: string;
  canonical_label: string;
  source_lane: ContextCatalogSourceLane;
  confirmation?: ContextCatalogConfirmation | null;
  trusted_lane_only?: boolean | null;
  description?: string | null;
  payload_schema?: Record<string, unknown> | null;
  /** Artifact-provided labels are intentionally ignored by host UI. */
  label?: string | null;
}

export interface ContextCatalogConflict {
  error: string;
  catalog_entry_id?: string | null;
  base_artifact_version_id?: string | null;
  current_artifact_version_id?: string | null;
  base_state_version_id?: string | null;
  current_state_version_id?: string | null;
  suggested_resolution?: string | null;
}

export interface ContextCatalogEntry {
  id: string;
  title?: string | null;
  description?: string | null;
  artifact_type?: string | null;
  status?: string | null;
  shelf?: string | null;
  pinned?: boolean | null;
  current_context_object_id?: string | null;
  current_artifact_version_id?: string | null;
  current_state_version_id?: string | null;
  current_artifact_sha256?: string | null;
  available_actions?: ContextCatalogActionDefinition[] | null;
  conflict?: ContextCatalogConflict | null;
}

export interface ListContextCatalogEntriesOptions {
  includeContent?: boolean;
  shelf?: string;
  artifactType?: string;
  pinned?: boolean;
  status?: string;
  ownerId?: string;
  updatedAfter?: string;
}

export interface ContextCatalogActionInvocationInput {
  context_object_id: string;
  action_id: string;
  source_lane: "trusted_named_action";
  base_artifact_version_id: string;
  base_state_version_id: string;
  idempotency_key: string;
  payload?: Record<string, unknown>;
}

export interface ContextCatalogActionResult {
  ok?: boolean;
  audit_event_id?: string | null;
  new_state_version_id?: string | null;
  new_artifact_version_id?: string | null;
  status?: string | null;
  error?: string | null;
  conflict?: ContextCatalogConflict | null;
}

type WidgetLike = {
  initial_data?: unknown;
  structured_content?: unknown;
  tool_result?: unknown;
};

function compactParams(
  values: Record<string, string | boolean | undefined>,
): Record<string, string | boolean> {
  return Object.fromEntries(
    Object.entries(values).filter(([, value]) => value !== undefined),
  ) as Record<string, string | boolean>;
}

function catalogPath(spaceId: string, catalogEntryId?: string) {
  const base = `/api/v1/spaces/${encodeURIComponent(spaceId)}/context-catalog`;
  return catalogEntryId
    ? `${base}/${encodeURIComponent(catalogEntryId)}`
    : base;
}

export async function listContextCatalogEntries(
  spaceId: string,
  options: ListContextCatalogEntriesOptions = {},
) {
  const response = await apiClient.get(catalogPath(spaceId), {
    params: compactParams({
      include_content: options.includeContent ?? false,
      shelf: options.shelf,
      artifact_type: options.artifactType,
      pinned: options.pinned,
      status: options.status,
      owner_id: options.ownerId,
      updated_after: options.updatedAfter,
    }),
  });
  return response.data;
}

export async function getContextCatalogEntry(
  spaceId: string,
  catalogEntryId: string,
  options: { includeContent?: boolean } = {},
) {
  const response = await apiClient.get(catalogPath(spaceId, catalogEntryId), {
    params: { include_content: options.includeContent ?? false },
  });
  return response.data;
}

export async function invokeContextCatalogAction(
  spaceId: string,
  catalogEntryId: string,
  input: ContextCatalogActionInvocationInput,
): Promise<ContextCatalogActionResult> {
  const response = await apiClient.post(
    `${catalogPath(spaceId, catalogEntryId)}/actions`,
    {
      ...input,
      catalog_entry_id: catalogEntryId,
      payload: input.payload ?? {},
    },
  );
  return response.data;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function parseMaybeJsonObject(value: unknown): Record<string, unknown> | null {
  if (typeof value === "string") {
    try {
      return recordValue(JSON.parse(value));
    } catch {
      return null;
    }
  }
  return recordValue(value);
}

function isContextCatalogEntry(value: unknown): value is ContextCatalogEntry {
  const record = recordValue(value);
  return typeof record?.id === "string" && record.id.trim().length > 0;
}

function getNestedRecord(
  source: Record<string, unknown>,
  path: string[],
): Record<string, unknown> | null {
  let current: unknown = source;
  for (const key of path) {
    const record = recordValue(current);
    if (!record) return null;
    current = record[key];
  }
  return recordValue(current);
}

function getWidgetDataCandidates(widget: WidgetLike) {
  const roots = [
    widget.initial_data,
    widget.structured_content,
    widget.tool_result,
  ];
  const candidates: Record<string, unknown>[] = [];

  for (const root of roots) {
    const record = parseMaybeJsonObject(root);
    if (!record) continue;
    candidates.push(record);

    const structured =
      parseMaybeJsonObject(record.structuredContent) ||
      parseMaybeJsonObject(record.structured_content);
    if (structured) candidates.push(structured);

    const data = parseMaybeJsonObject(record.data);
    if (data) candidates.push(data);
  }

  return candidates;
}

export function extractContextCatalogEntryFromWidget(
  widget: WidgetLike,
): ContextCatalogEntry | null {
  for (const data of getWidgetDataCandidates(widget)) {
    const direct =
      data.context_catalog_entry ||
      data.catalog_entry ||
      getNestedRecord(data, ["context_catalog", "entry"]);
    if (isContextCatalogEntry(direct)) return direct;

    const items = Array.isArray(data.items) ? data.items : [];
    const selectedKey =
      typeof data.selected_key === "string" ? data.selected_key : null;
    const selectedItem =
      (selectedKey
        ? items.find((item) => recordValue(item)?.key === selectedKey)
        : items[0]) || null;
    const selectedRecord = recordValue(selectedItem);
    if (isContextCatalogEntry(selectedRecord?.context_catalog_entry)) {
      return selectedRecord.context_catalog_entry;
    }
  }

  return null;
}
