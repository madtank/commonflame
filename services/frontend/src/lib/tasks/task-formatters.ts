/**
 * Task formatting utilities.
 */

import { normalizePriority, parseUTCDate } from "./task-logic";

// ============================================================================
// Date & Time Formatting
// ============================================================================

/**
 * Format time elapsed in human-readable format
 */
export function formatTimeAgo(timestamp: string | null | undefined): string {
  if (!timestamp) return "Never";

  const date = parseUTCDate(timestamp);
  if (!date) return "Never";

  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);

  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return `${Math.floor(seconds / 604800)}w ago`;
}

/**
 * Format time remaining in hours and minutes
 */
export function formatTimeRemaining(milliseconds: number): string {
  const totalMinutes = Math.floor(milliseconds / (1000 * 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

/**
 * Format a date for display
 */
export function formatDate(timestamp: string | null | undefined): string {
  const date = parseUTCDate(timestamp);
  if (!date) return "N/A";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year:
      date.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined,
  });
}

/**
 * Format a date with time
 */
export function formatDateTime(timestamp: string | null | undefined): string {
  const date = parseUTCDate(timestamp);
  if (!date) return "N/A";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// ============================================================================
// Priority Formatting
// ============================================================================

/**
 * Get human-readable priority label
 */
export function getPriorityLabel(priority: string): string {
  switch (priority.toLowerCase()) {
    case "p0":
    case "critical":
      return "Critical";
    case "p1":
    case "high":
      return "High";
    case "p2":
    case "medium":
      return "Medium";
    case "p3":
    case "low":
      return "Low";
    default:
      return priority
        ? priority.charAt(0).toUpperCase() + priority.slice(1)
        : "Unknown";
  }
}

/**
 * Get priority color classes
 */
export function getPriorityColor(priority: string): string {
  const normalized = normalizePriority(priority);
  switch (normalized) {
    case "critical":
      return "text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-900/30";
    case "high":
      return "text-orange-700 bg-orange-100 dark:text-orange-300 dark:bg-orange-900/30";
    case "medium":
      return "text-blue-700 bg-blue-100 dark:text-blue-300 dark:bg-blue-900/30";
    case "low":
      return "text-gray-700 bg-gray-100 dark:text-gray-300 dark:bg-gray-900/30";
    default:
      return "text-gray-700 bg-gray-100 dark:text-gray-300 dark:bg-gray-900/30";
  }
}

/**
 * Get priority icon type for React component rendering
 * Returns: 'alert' | 'zap' | 'clock' | 'calendar' | 'info'
 */
export function getPriorityIconType(priority: string): string {
  switch (priority.toLowerCase()) {
    case "p0":
    case "critical":
      return "alert";
    case "p1":
    case "high":
      return "zap";
    case "p2":
    case "medium":
      return "clock";
    case "p3":
    case "low":
      return "calendar";
    default:
      return "info";
  }
}

// ============================================================================
// Status Formatting
// ============================================================================

/**
 * Get status color classes
 */
export function getStatusColor(status: string): string {
  switch (status) {
    case "available":
      return "text-yellow-700 bg-yellow-100 dark:text-yellow-300 dark:bg-yellow-900/20";
    case "claimed":
      return "text-blue-700 bg-blue-100 dark:text-blue-300 dark:bg-blue-900/20";
    case "in_progress":
      return "text-indigo-700 bg-indigo-100 dark:text-indigo-300 dark:bg-indigo-900/20";
    case "processing":
      return "text-purple-700 bg-purple-100 dark:text-purple-300 dark:bg-purple-900/20 animate-pulse";
    case "completed":
      return "text-green-700 bg-green-100 dark:text-green-300 dark:bg-green-900/20";
    case "failed":
      return "text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-900/20";
    case "expired":
      return "text-gray-700 bg-gray-100 dark:text-gray-300 dark:bg-gray-900/20";
    case "assigned":
      return "text-purple-700 bg-purple-100 dark:text-purple-300 dark:bg-purple-900/20";
    case "blocked":
      return "text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-900/20 border border-red-300 dark:border-red-700";
    case "in_review":
      return "text-orange-700 bg-orange-100 dark:text-orange-300 dark:bg-orange-900/20";
    case "cancelled":
      return "text-gray-700 bg-gray-100 dark:text-gray-300 dark:bg-gray-900/20";
    default:
      return "text-gray-700 bg-gray-100 dark:text-gray-300 dark:bg-gray-900/20";
  }
}

/**
 * Get status icon type for React component rendering
 * Returns: 'clock' | 'play' | 'zap' | 'loader' | 'check' | 'x' | 'calendar' | 'ban' | 'eye'
 */
export function getStatusIconType(status: string): string {
  switch (status) {
    case "available":
      return "clock";
    case "claimed":
      return "play";
    case "in_progress":
      return "zap";
    case "processing":
      return "loader";
    case "completed":
      return "check";
    case "failed":
    case "expired":
    case "cancelled":
      return "x";
    case "assigned":
      return "calendar";
    case "blocked":
      return "ban";
    case "in_review":
      return "eye";
    default:
      return "clock";
  }
}

/**
 * Get human-readable status label
 */
export function getStatusLabel(status: string): string {
  switch (status) {
    case "in_progress":
      return "In Progress";
    case "in_review":
      return "In Review";
    default:
      return status.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase());
  }
}
