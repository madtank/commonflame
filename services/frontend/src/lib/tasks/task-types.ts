/**
 * Task domain types.
 */

export type TaskStatus =
  | "available"
  | "claimed"
  | "in_progress"
  | "completed"
  | "failed"
  | "expired"
  | "assigned"
  | "blocked"
  | "in_review"
  | "cancelled"
  | "processing"; // Real-time agent activity indication

export type TaskPriority =
  | "P0"
  | "P1"
  | "P2"
  | "P3"
  | "critical"
  | "high"
  | "medium"
  | "low";

/**
 * Grooming checklist for task readiness assessment.
 */
export interface TaskGroomingChecklist {
  requirements_clear?: boolean;
  success_criteria?: boolean;
  effort_estimated?: boolean;
  dependencies_identified?: boolean;
  acceptance_criteria?: boolean;
  [key: string]: unknown; // Allow additional properties
}

/**
 * AI-generated intelligence data for tasks.
 */
export interface TaskIntelligenceData {
  suggested_skills?: string[];
  complexity_score?: number;
  estimated_hours?: number;
  risk_factors?: string[];
  similar_tasks?: string[];
  [key: string]: unknown;
}

/**
 * Extended metadata for tasks with intelligence data.
 */
export interface TaskMetadata {
  skills?: string[];
  grooming_checklist?: TaskGroomingChecklist;
  intelligence_data?: TaskIntelligenceData;
  [key: string]: unknown; // Allow additional properties
}

/**
 * Requirements and completion data for tasks.
 */
export interface TaskRequirements {
  closing_note?: string;
  completion_notes?: string;
  closed_by?: string;
  completed_by?: string;
  closed_at?: string;
  completed_at?: string;
  deliverables?: string[];
  links?: string[];
  acceptance_criteria?: string;
  [key: string]: unknown; // Allow additional properties
}

export interface Task {
  task_id: string;
  task_display_id?: string;
  title: string;
  description?: string;
  created_by: string;
  assigned_to: string | null;
  claimed_by: string | null;
  priority: string;
  status: string;
  status_extended?: string;
  hours_since_creation: number;
  hours_since_claim: number | null;
  assigned_at: string | null;
  hours_since_assigned: number | null;
  expected_minutes?: number;
  created_at: string;
  updated_at?: string;
  claimed_at: string | null;
  completed_at: string | null;
  requirements?: TaskRequirements;
  metadata?: TaskMetadata;
  skills?: string[];
  progress_notes?: string;
  grooming_checklist?: TaskGroomingChecklist;
  links?: string[];
  context_data?: TaskContextData;
  // Extended properties from SSE events
  org_id?: string;
  parent_task_id?: string;
  blocked_by_ids?: string[];
  closing_note?: string;
}

export interface TaskContextData {
  branch_name?: string;
  pr_url?: string;
  completion_notes?: string;
  closing_note?: string;
  deliverables?: string[];
  files_changed?: string[];
  issues_encountered?: string;
  time_spent?: {
    actual_minutes?: number;
    estimated_minutes?: number;
  };
  testing_notes?: string;
}

export interface TaskStats {
  total_tasks: number;
  tasks_created_today: number;
  active_tasks: number;
  waiting_for_claim: number;
  completed_today: number;
  stale_tasks: number;
  longest_waiting: TaskWaiting[];
  agent_workload: AgentWorkload[];
}

export interface TaskWaiting {
  task_id: string;
  title: string;
  created_by: string;
  priority: string;
  hours_waiting: number;
}

export interface AgentWorkload {
  agent_name: string;
  active_tasks: number;
  completed_today: number;
  completed_all_time: number;
  total_tasks: number;
}

export interface TaskAttentionItem extends Task {
  reason: string;
  detail: string;
  urgency: number;
  color: string;
}

export interface TaskStatistics {
  totalTasks: number;
  openTasks: number;
  completedTasks: number;
  highPriorityTasks: number;
  staleTasks: number;
  blockedTasks: number;
  needAttentionTasks: number;
}

export interface TaskFilters {
  taskIdFilter?: string | null;
  agentFilter?: string | null;
  statusSelect?: string;
  prioritySelect?: string;
  taskSearch?: string;
}

export interface TaskSSEEvent {
  type: "task_created" | "task_updated" | "task_deleted";
  task: Partial<Task> & { task_id: string };
  actor?: {
    id: string;
    type?: string;
    display_name?: string | null;
  };
  org_id?: string;
  timestamp?: string;
}
