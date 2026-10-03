/**
 * Task domain logic utilities.
 */

import {
  Task,
  TaskAttentionItem,
  TaskFilters,
  TaskStatistics,
} from "./task-types";

// ============================================================================
// Constants
// ============================================================================

/** SLA thresholds in hours by priority */
export const SLA_HOURS: Record<string, number> = {
  p0: 2,
  critical: 2,
  P0: 2,
  p1: 24,
  high: 24,
  P1: 24,
  p2: 168, // 7 days
  medium: 168,
  P2: 168,
  p3: 720, // 30 days
  low: 720,
  P3: 720,
};

/** Default SLA hours when priority is unknown */
export const DEFAULT_SLA_HOURS = 168; // 7 days

/** Stale task threshold in days */
export const STALE_THRESHOLD_DAYS = 3;

// ============================================================================
// Date & Time Utilities
// ============================================================================

/**
 * Parse UTC timestamps correctly - API returns without Z suffix
 * Returns null for invalid dates to prevent NaN propagation
 */
export function parseUTCDate(
  timestamp: string | null | undefined,
): Date | null {
  if (!timestamp) return null;
  // Append 'Z' to force UTC interpretation if not already present
  const utcTimestamp = timestamp.endsWith("Z") ? timestamp : timestamp + "Z";
  const date = new Date(utcTimestamp);
  // Return null for Invalid Date to prevent NaN in calculations
  return isNaN(date.getTime()) ? null : date;
}

/**
 * Calculate hours since a given timestamp
 */
export function hoursSince(timestamp: string | null | undefined): number {
  const date = parseUTCDate(timestamp);
  if (!date) return 0;
  return (Date.now() - date.getTime()) / (1000 * 60 * 60);
}

/**
 * Check if a timestamp is from today
 */
export function isToday(timestamp: string | null | undefined): boolean {
  if (!timestamp) return false;
  const date = parseUTCDate(timestamp);
  if (!date) return false;
  const today = new Date();
  return date.toDateString() === today.toDateString();
}

// ============================================================================
// Priority Utilities
// ============================================================================

/**
 * Normalize priority labels (P0-P3 ↔ critical/high/medium/low)
 */
export function normalizePriority(priority?: string | null): string {
  if (!priority) return "";
  switch (priority.toUpperCase()) {
    case "P0":
    case "CRITICAL":
      return "critical";
    case "P1":
    case "HIGH":
      return "high";
    case "P2":
    case "MEDIUM":
      return "medium";
    case "P3":
    case "LOW":
      return "low";
    default:
      return priority.toLowerCase();
  }
}

/**
 * Check if priority is high (P0/P1 or critical/high)
 */
export function isHighPriority(priority: string): boolean {
  const normalized = normalizePriority(priority);
  return normalized === "critical" || normalized === "high";
}

// ============================================================================
// Status Utilities
// ============================================================================

/**
 * Check if task is in an active state
 */
export function isActiveStatus(status: string): boolean {
  return ["claimed", "in_progress", "processing", "assigned"].includes(status);
}

/**
 * Check if task is in a terminal state
 */
export function isTerminalStatus(status: string): boolean {
  return ["completed", "failed", "expired", "cancelled"].includes(status);
}

// ============================================================================
// SLA & Attention Utilities
// ============================================================================

/**
 * Get SLA hours for a given priority
 */
export function getSLAHours(priority: string): number {
  return (
    SLA_HOURS[priority] ??
    SLA_HOURS[priority.toLowerCase()] ??
    DEFAULT_SLA_HOURS
  );
}

/**
 * Check if task is overdue based on SLA
 */
export function isOverdue(task: Task): boolean {
  if (task.status === "completed" || task.status === "cancelled") return false;
  const slaHours = getSLAHours(task.priority);
  return task.hours_since_creation > slaHours;
}

/**
 * Check if task is stale (no activity for threshold days)
 */
export function isStale(task: Task): boolean {
  if (task.status === "completed" || task.status === "cancelled") return false;
  const createdAt = parseUTCDate(task.created_at);
  if (!createdAt) return false;
  const thresholdDate = new Date(
    Date.now() - STALE_THRESHOLD_DAYS * 24 * 60 * 60 * 1000,
  );
  return createdAt < thresholdDate;
}

/**
 * Check if task needs attention
 */
export function needsAttention(task: Task): boolean {
  if (task.status === "completed" || task.status === "cancelled") return false;
  return (
    task.status === "blocked" ||
    isOverdue(task) ||
    (isHighPriority(task.priority) && !task.assigned_to && !task.claimed_by) ||
    isStale(task)
  );
}

/**
 * Get attention reason for a task
 */
export function getAttentionReason(task: Task): TaskAttentionItem | null {
  if (task.status === "completed" || task.status === "cancelled") return null;

  if (task.status === "blocked") {
    return {
      ...task,
      reason: "Blocked",
      detail: "Cannot progress - needs unblocking",
      urgency: 4,
      color: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200",
    };
  }

  if (isOverdue(task)) {
    return {
      ...task,
      reason: "Overdue",
      detail: `Past SLA deadline (${task.priority || "medium"} priority)`,
      urgency: 3,
      color: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200",
    };
  }

  if (isHighPriority(task.priority) && !task.assigned_to && !task.claimed_by) {
    return {
      ...task,
      reason: "Urgent & Unassigned",
      detail: "High priority task needs owner",
      urgency: 2,
      color:
        "bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-200",
    };
  }

  if (isStale(task)) {
    return {
      ...task,
      reason: "Stale",
      detail: "No activity for 3+ days",
      urgency: 1,
      color:
        "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-200",
    };
  }

  return null;
}

// ============================================================================
// Statistics Utilities
// ============================================================================

/**
 * Calculate task statistics from task array
 */
export function calculateTaskStatistics(tasks: Task[]): TaskStatistics {
  const now = new Date();
  const threeDaysAgo = new Date(
    now.getTime() - STALE_THRESHOLD_DAYS * 24 * 60 * 60 * 1000,
  );

  // Filter out cancelled tasks from statistics
  const activeTasks = tasks.filter((task) => task.status !== "cancelled");

  const totalTasks = activeTasks.length;
  const openTasks = activeTasks.filter(
    (task) => task.status !== "completed",
  ).length;
  const completedTasks = activeTasks.filter(
    (task) => task.status === "completed",
  ).length;

  const highPriorityTasks = activeTasks.filter((task) => {
    if (task.status === "completed") return false;
    return isHighPriority(task.priority);
  }).length;

  const staleTasks = activeTasks.filter((task) => {
    if (task.status === "completed") return false;
    const createdAt = parseUTCDate(task.created_at);
    if (!createdAt) return false;
    return createdAt < threeDaysAgo;
  }).length;

  const blockedTasks = activeTasks.filter(
    (task) => task.status === "blocked",
  ).length;

  const overdueTasks = activeTasks.filter((task) => isOverdue(task)).length;

  const unassignedTasks = activeTasks.filter(
    (task) =>
      task.status !== "completed" && !task.assigned_to && !task.claimed_by,
  ).length;

  const needAttentionTasks = blockedTasks + overdueTasks + unassignedTasks;

  return {
    totalTasks,
    openTasks,
    completedTasks,
    highPriorityTasks,
    staleTasks,
    blockedTasks,
    needAttentionTasks,
  };
}

/**
 * Get tasks needing attention with reasons
 */
export function getTasksNeedingAttention(tasks: Task[]): {
  needingAttention: TaskAttentionItem[];
  totalIssues: number;
} {
  const activeTasksForAttention = tasks.filter((t) => t.status !== "cancelled");

  const blockedTasks = activeTasksForAttention.filter(
    (t) => t.status === "blocked",
  );
  const overdueTasks = activeTasksForAttention.filter((t) => isOverdue(t));
  const unassignedUrgent = activeTasksForAttention.filter(
    (t) =>
      t.status !== "completed" &&
      !t.assigned_to &&
      !t.claimed_by &&
      isHighPriority(t.priority),
  );
  const staleTasks = activeTasksForAttention.filter((t) => isStale(t));

  const needingAttention = [
    ...blockedTasks.map((t) => ({
      ...t,
      reason: "Blocked",
      detail: "Cannot progress - needs unblocking",
      urgency: 4,
      color: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200",
    })),
    ...overdueTasks.map((t) => ({
      ...t,
      reason: "Overdue",
      detail: `Past SLA deadline (${t.priority || "medium"} priority)`,
      urgency: 3,
      color: "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200",
    })),
    ...unassignedUrgent.map((t) => ({
      ...t,
      reason: "Urgent & Unassigned",
      detail: "High priority task needs owner",
      urgency: 2,
      color:
        "bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-200",
    })),
    ...staleTasks.slice(0, 2).map((t) => ({
      ...t,
      reason: "Stale",
      detail: "No activity for 3+ days",
      urgency: 1,
      color:
        "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-200",
    })),
  ]
    .filter(
      (task, index, self) =>
        self.findIndex((t) => t.task_id === task.task_id) === index,
    )
    .sort((a, b) => b.urgency - a.urgency);

  const totalIssues =
    blockedTasks.length +
    overdueTasks.length +
    unassignedUrgent.length +
    staleTasks.length;

  return { needingAttention, totalIssues };
}

// ============================================================================
// Filter Utilities
// ============================================================================

/**
 * Filter tasks based on filter criteria
 */
export function filterTasks(tasks: Task[], filters: TaskFilters): Task[] {
  return tasks.filter((task) => {
    // Task ID filter (highest priority)
    if (filters.taskIdFilter && task.task_id !== filters.taskIdFilter) {
      return false;
    }

    // Agent filter
    if (filters.agentFilter) {
      if (filters.agentFilter === "unassigned") {
        if (task.assigned_to || task.claimed_by) {
          return false;
        }
      } else {
        if (
          task.assigned_to !== filters.agentFilter &&
          task.claimed_by !== filters.agentFilter
        ) {
          return false;
        }
      }
    }

    // Status filter
    if (filters.statusSelect === "all") {
      // "All Statuses" excludes completed and cancelled by default
      if (task.status === "completed" || task.status === "cancelled") {
        return false;
      }
    } else if (filters.statusSelect && filters.statusSelect !== "all") {
      if (task.status !== filters.statusSelect) {
        return false;
      }
    }

    // Priority filter
    if (filters.prioritySelect && filters.prioritySelect !== "all") {
      const taskPriority = normalizePriority(task.priority);
      if (taskPriority !== filters.prioritySelect) {
        return false;
      }
    }

    // Search filter
    if (filters.taskSearch) {
      const search = filters.taskSearch.toLowerCase();
      const matchesSearch =
        task.title?.toLowerCase().includes(search) ||
        task.description?.toLowerCase().includes(search) ||
        task.task_id?.toLowerCase().includes(search) ||
        task.task_display_id?.toLowerCase().includes(search);
      if (!matchesSearch) {
        return false;
      }
    }

    return true;
  });
}
