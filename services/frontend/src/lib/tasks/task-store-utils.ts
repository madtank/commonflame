/**
 * Task store utilities for data transformation and SSE sync.
 */

import { Task, TaskContextData, TaskSSEEvent } from "./task-types";
import { hoursSince, parseUTCDate } from "./task-logic";

// ============================================================================
// SSE Guard Utilities
// ============================================================================

const resolveUpdatedAt = (task: Partial<Task>): string | null => {
  if (!task) return null;
  const withAlt = task as { updated_at?: string; updatedAt?: string };
  return withAlt.updated_at ?? withAlt.updatedAt ?? null;
};

/**
 * Determines if an incoming SSE update should overwrite local state.
 * Prevents stale fetches from overwriting newer SSE data.
 */
export function shouldUpdateTask(
  localTask: Task,
  incomingTask: Partial<Task>,
): boolean {
  const localUpdatedAt = parseUTCDate(resolveUpdatedAt(localTask));
  const incomingUpdatedAt = parseUTCDate(resolveUpdatedAt(incomingTask));

  if (!localUpdatedAt || !incomingUpdatedAt) {
    return true;
  }

  return incomingUpdatedAt.getTime() >= localUpdatedAt.getTime();
}

/**
 * Checks whether a partial update would change any existing task fields.
 * Undefined values are ignored to avoid no-op updates.
 */
export function hasTaskPatchChanges(
  existing: Task,
  patch: Partial<Task>,
): boolean {
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    if (
      !Object.is(
        (existing as unknown as Record<string, unknown>)[key],
        value as unknown,
      )
    ) {
      return true;
    }
  }
  return false;
}

// ============================================================================
// Task Transformation Utilities
// ============================================================================

/**
 * Transform API task response to frontend Task format
 */
export function transformApiTask(apiTask: Record<string, unknown>): Task {
  const createdAt = apiTask.created_at as string | null;
  const claimedAt = apiTask.claimed_at as string | null;
  const assignedAt = apiTask.assigned_at as string | null;

  // Parse links - can be JSON string or array
  let links: string[] = [];
  if (apiTask.links) {
    if (Array.isArray(apiTask.links)) {
      links = apiTask.links as string[];
    } else if (typeof apiTask.links === "string") {
      try {
        links = JSON.parse(apiTask.links);
      } catch {
        links = [];
      }
    }
  }

  // Parse metadata/requirements
  let metadata = (apiTask.requirements || apiTask.metadata || {}) as Record<
    string,
    unknown
  >;
  const description = apiTask.description as string | undefined;

  // Fallback: if description contains JSON-like data, try to parse it
  if (
    (!metadata.grooming_checklist || Object.keys(metadata).length <= 1) &&
    description
  ) {
    try {
      const jsonMatch = description.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        metadata = { ...metadata, ...parsed };
      }
    } catch {
      // Ignore parsing errors
    }
  }

  return {
    task_id: (apiTask.id || apiTask.task_id) as string,
    task_display_id: apiTask.task_display_id as string | undefined,
    title: apiTask.title as string,
    description: description || "No description provided",
    created_by:
      ((apiTask.posted_by as Record<string, unknown>)?.username as string) ||
      (apiTask.created_by as string) ||
      "Unknown",
    assigned_to:
      ((apiTask.assigned_agent as Record<string, unknown>)?.name as string) ||
      (apiTask.assigned_to as string | null),
    claimed_by: apiTask.claimed_by as string | null,
    priority: (apiTask.priority as string) || "medium",
    status: apiTask.status as string,
    status_extended: apiTask.status_extended as string | undefined,
    hours_since_creation: hoursSince(createdAt),
    hours_since_claim: claimedAt ? hoursSince(claimedAt) : null,
    assigned_at: assignedAt,
    hours_since_assigned: assignedAt ? hoursSince(assignedAt) : null,
    expected_minutes: (apiTask.expected_minutes as number) ?? 30,
    created_at: createdAt || new Date().toISOString(),
    updated_at: apiTask.updated_at as string | undefined,
    claimed_at: claimedAt,
    completed_at: apiTask.completed_at as string | null,
    requirements: (apiTask.requirements || {}) as Record<string, unknown>,
    metadata,
    skills:
      ((apiTask.requirements as Record<string, unknown>)?.skills as string[]) ||
      (apiTask.skills as string[]) ||
      [],
    progress_notes: (apiTask.progress_notes as string) || "",
    links,
    context_data: (apiTask.context_data || {}) as TaskContextData,
  };
}

/**
 * Deduplicate tasks by task_id
 */
export function deduplicateTasks(tasks: Task[]): Task[] {
  return tasks.filter(
    (task, index, self) =>
      index === self.findIndex((t) => t.task_id === task.task_id),
  );
}

// ============================================================================
// SSE Merge Utilities
// ============================================================================

/**
 * Merge SSE task update with existing task
 */
export function mergeTaskUpdate(existing: Task, update: Partial<Task>): Task {
  return {
    ...existing,
    ...update,
    // Preserve fields that may not be in the update
    task_id: existing.task_id,
    assigned_to: update.assigned_to ?? existing.assigned_to,
    claimed_by: update.claimed_by ?? existing.claimed_by,
    // Recalculate time-based fields if timestamps changed
    hours_since_creation:
      update.created_at !== undefined
        ? hoursSince(update.created_at)
        : existing.hours_since_creation,
    hours_since_claim:
      update.claimed_at !== undefined
        ? hoursSince(update.claimed_at)
        : existing.hours_since_claim,
    hours_since_assigned:
      update.assigned_at !== undefined
        ? hoursSince(update.assigned_at)
        : existing.hours_since_assigned,
  };
}

/**
 * Apply SSE event to task list - returns new array
 */
export function applyTaskSSEEvent(tasks: Task[], event: TaskSSEEvent): Task[] {
  switch (event.type) {
    case "task_created": {
      // Add new task if it doesn't exist
      const existingTask = tasks.find((t) => t.task_id === event.task.task_id);
      if (existingTask) {
        if (!shouldUpdateTask(existingTask, event.task)) {
          return tasks;
        }
        if (!hasTaskPatchChanges(existingTask, event.task)) {
          return tasks;
        }
        return tasks.map((t) =>
          t.task_id === event.task.task_id ? mergeTaskUpdate(t, event.task) : t,
        );
      }
      // Transform partial task to full task with defaults
      // Note: We explicitly set each field to avoid undefined values from spread overwriting defaults
      const newTask: Task = {
        task_id: event.task.task_id,
        title: event.task.title || "Untitled Task",
        description: event.task.description || "",
        created_by: event.task.created_by || "Unknown",
        assigned_to: event.task.assigned_to ?? null,
        claimed_by: event.task.claimed_by ?? null,
        priority: event.task.priority || "medium",
        status: event.task.status || "available",
        hours_since_creation: event.task.hours_since_creation ?? 0,
        hours_since_claim: event.task.hours_since_claim ?? null,
        assigned_at: event.task.assigned_at ?? null,
        hours_since_assigned: event.task.hours_since_assigned ?? null,
        created_at: event.task.created_at || new Date().toISOString(),
        updated_at: event.task.updated_at,
        claimed_at: event.task.claimed_at ?? null,
        completed_at: event.task.completed_at ?? null,
        // Include optional fields that may come from the event
        org_id: event.task.org_id,
        parent_task_id: event.task.parent_task_id,
        blocked_by_ids: event.task.blocked_by_ids,
        links: event.task.links,
        closing_note: event.task.closing_note,
      };
      return [newTask, ...tasks];
    }

    case "task_updated": {
      let changed = false;
      const nextTasks = tasks.map((t) => {
        if (t.task_id !== event.task.task_id) return t;
        if (!shouldUpdateTask(t, event.task)) return t;
        if (!hasTaskPatchChanges(t, event.task)) return t;
        changed = true;
        return mergeTaskUpdate(t, event.task);
      });
      return changed ? nextTasks : tasks;
    }

    case "task_deleted": {
      return tasks.filter((t) => t.task_id !== event.task.task_id);
    }

    default:
      return tasks;
  }
}
