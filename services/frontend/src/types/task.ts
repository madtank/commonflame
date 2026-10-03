/**
 * Centralized Task type definitions
 * Import from '@/types' instead of defining locally
 */

/** Task priority levels */
export type TaskPriority = "low" | "medium" | "high" | "urgent";

/** Task status states */
export type TaskStatus =
  | "not_started"
  | "in_progress"
  | "blocked"
  | "completed"
  | "cancelled";

/** Main task interface */
export interface Task {
  id: string;
  title: string;
  description?: string;
  status: TaskStatus;
  priority: TaskPriority;
  created_at: string;
  updated_at?: string;
  due_date?: string | null;

  // Assignment
  assigned_to?: string | null;
  assigned_at?: string | null;
  claimed_by?: string | null;
  claimed_at?: string | null;

  // Organization
  space_id?: string;
  org_id?: string;
  channel?: string;
  tags?: string[];
  links?: string[];

  // Progress
  progress?: number;
  closing_note?: string | null;
  completed_at?: string | null;

  // Creator
  created_by?: string;
  creator_username?: string;
}

/** Task summary for lists */
export interface TaskSummary {
  id: string;
  title: string;
  status: TaskStatus;
  priority: TaskPriority;
  assigned_to?: string | null;
  due_date?: string | null;
}

/** Task note/update */
export interface TaskNote {
  id?: string;
  note: string;
  note_type?: "general" | "progress" | "issue" | "solution";
  visibility?: "public" | "private" | "team";
  author_id?: string;
  author_name?: string;
  author_type?: "user" | "agent";
  created_at: string;
  updated_at?: string;
}
