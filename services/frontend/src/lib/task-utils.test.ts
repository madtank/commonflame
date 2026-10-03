/**
 * Tests for task-utils.ts
 */

import { describe, it, expect } from "vitest";
import { Task, TaskSSEEvent } from "./tasks/task-types";
import {
  calculateTaskStatistics,
  filterTasks,
  getSLAHours,
  hoursSince,
  isHighPriority,
  isOverdue,
  isStale,
  normalizePriority,
  parseUTCDate,
} from "./tasks/task-logic";
import { getPriorityLabel, getStatusColor } from "./tasks/task-formatters";
import {
  applyTaskSSEEvent,
  deduplicateTasks,
  hasTaskPatchChanges,
  mergeTaskUpdate,
  shouldUpdateTask,
  transformApiTask,
} from "./tasks/task-store-utils";

describe("task-utils", () => {
  describe("parseUTCDate", () => {
    it("should parse timestamp without Z suffix", () => {
      const result = parseUTCDate("2024-01-15T10:30:00");
      expect(result).toBeInstanceOf(Date);
      expect(result?.toISOString()).toBe("2024-01-15T10:30:00.000Z");
    });

    it("should parse timestamp with Z suffix", () => {
      const result = parseUTCDate("2024-01-15T10:30:00Z");
      expect(result).toBeInstanceOf(Date);
      expect(result?.toISOString()).toBe("2024-01-15T10:30:00.000Z");
    });

    it("should return null for null input", () => {
      expect(parseUTCDate(null)).toBeNull();
    });

    it("should return null for undefined input", () => {
      expect(parseUTCDate(undefined)).toBeNull();
    });
  });

  describe("hoursSince", () => {
    it("should calculate hours since timestamp", () => {
      const twoHoursAgo = new Date(
        Date.now() - 2 * 60 * 60 * 1000,
      ).toISOString();
      const hours = hoursSince(twoHoursAgo);
      expect(hours).toBeCloseTo(2, 0);
    });

    it("should return 0 for null input", () => {
      expect(hoursSince(null)).toBe(0);
    });
  });

  describe("normalizePriority", () => {
    it("should normalize P0 to critical", () => {
      expect(normalizePriority("P0")).toBe("critical");
      expect(normalizePriority("p0")).toBe("critical");
    });

    it("should normalize P1 to high", () => {
      expect(normalizePriority("P1")).toBe("high");
      expect(normalizePriority("HIGH")).toBe("high");
    });

    it("should normalize P2 to medium", () => {
      expect(normalizePriority("P2")).toBe("medium");
      expect(normalizePriority("MEDIUM")).toBe("medium");
    });

    it("should normalize P3 to low", () => {
      expect(normalizePriority("P3")).toBe("low");
      expect(normalizePriority("LOW")).toBe("low");
    });

    it("should return empty string for null", () => {
      expect(normalizePriority(null)).toBe("");
    });
  });

  describe("getPriorityLabel", () => {
    it("should return Critical for p0/critical", () => {
      expect(getPriorityLabel("p0")).toBe("Critical");
      expect(getPriorityLabel("critical")).toBe("Critical");
    });

    it("should return High for p1/high", () => {
      expect(getPriorityLabel("p1")).toBe("High");
      expect(getPriorityLabel("high")).toBe("High");
    });

    it("should return Medium for p2/medium", () => {
      expect(getPriorityLabel("p2")).toBe("Medium");
      expect(getPriorityLabel("medium")).toBe("Medium");
    });

    it("should return Low for p3/low", () => {
      expect(getPriorityLabel("p3")).toBe("Low");
      expect(getPriorityLabel("low")).toBe("Low");
    });

    it("should capitalize unknown priorities", () => {
      expect(getPriorityLabel("custom")).toBe("Custom");
    });
  });

  describe("getStatusColor", () => {
    it("should return correct color for available", () => {
      expect(getStatusColor("available")).toContain("yellow");
    });

    it("should return correct color for completed", () => {
      expect(getStatusColor("completed")).toContain("green");
    });

    it("should return correct color for blocked", () => {
      expect(getStatusColor("blocked")).toContain("red");
    });

    it("should return correct color for processing", () => {
      expect(getStatusColor("processing")).toContain("purple");
      expect(getStatusColor("processing")).toContain("animate-pulse");
    });
  });

  describe("getSLAHours", () => {
    it("should return 2 hours for critical/P0", () => {
      expect(getSLAHours("critical")).toBe(2);
      expect(getSLAHours("P0")).toBe(2);
    });

    it("should return 24 hours for high/P1", () => {
      expect(getSLAHours("high")).toBe(24);
      expect(getSLAHours("P1")).toBe(24);
    });

    it("should return 168 hours (7 days) for medium/P2", () => {
      expect(getSLAHours("medium")).toBe(168);
      expect(getSLAHours("P2")).toBe(168);
    });

    it("should return 720 hours (30 days) for low/P3", () => {
      expect(getSLAHours("low")).toBe(720);
      expect(getSLAHours("P3")).toBe(720);
    });

    it("should return default for unknown priority", () => {
      expect(getSLAHours("unknown")).toBe(168);
    });
  });

  describe("isHighPriority", () => {
    it("should return true for critical/high", () => {
      expect(isHighPriority("critical")).toBe(true);
      expect(isHighPriority("high")).toBe(true);
      expect(isHighPriority("P0")).toBe(true);
      expect(isHighPriority("P1")).toBe(true);
    });

    it("should return false for medium/low", () => {
      expect(isHighPriority("medium")).toBe(false);
      expect(isHighPriority("low")).toBe(false);
    });
  });

  describe("isOverdue", () => {
    it("should return true when task exceeds SLA", () => {
      const task: Task = {
        task_id: "1",
        title: "Test",
        created_by: "user",
        assigned_to: null,
        claimed_by: null,
        priority: "critical",
        status: "in_progress",
        hours_since_creation: 5, // Critical SLA is 2 hours
        hours_since_claim: null,
        assigned_at: null,
        hours_since_assigned: null,
        created_at: new Date().toISOString(),
        claimed_at: null,
        completed_at: null,
      };
      expect(isOverdue(task)).toBe(true);
    });

    it("should return false when task is within SLA", () => {
      const task: Task = {
        task_id: "1",
        title: "Test",
        created_by: "user",
        assigned_to: null,
        claimed_by: null,
        priority: "critical",
        status: "in_progress",
        hours_since_creation: 1, // Within 2 hour SLA
        hours_since_claim: null,
        assigned_at: null,
        hours_since_assigned: null,
        created_at: new Date().toISOString(),
        claimed_at: null,
        completed_at: null,
      };
      expect(isOverdue(task)).toBe(false);
    });

    it("should return false for completed tasks", () => {
      const task: Task = {
        task_id: "1",
        title: "Test",
        created_by: "user",
        assigned_to: null,
        claimed_by: null,
        priority: "critical",
        status: "completed",
        hours_since_creation: 100,
        hours_since_claim: null,
        assigned_at: null,
        hours_since_assigned: null,
        created_at: new Date().toISOString(),
        claimed_at: null,
        completed_at: new Date().toISOString(),
      };
      expect(isOverdue(task)).toBe(false);
    });
  });

  describe("calculateTaskStatistics", () => {
    const createTask = (overrides: Partial<Task> = {}): Task => ({
      task_id: "1",
      title: "Test",
      created_by: "user",
      assigned_to: null,
      claimed_by: null,
      priority: "medium",
      status: "available",
      hours_since_creation: 1,
      hours_since_claim: null,
      assigned_at: null,
      hours_since_assigned: null,
      created_at: new Date().toISOString(),
      claimed_at: null,
      completed_at: null,
      ...overrides,
    });

    it("should calculate total tasks excluding cancelled", () => {
      const tasks = [
        createTask({ task_id: "1", status: "available" }),
        createTask({ task_id: "2", status: "completed" }),
        createTask({ task_id: "3", status: "cancelled" }),
      ];
      const stats = calculateTaskStatistics(tasks);
      expect(stats.totalTasks).toBe(2);
    });

    it("should calculate open tasks", () => {
      const tasks = [
        createTask({ task_id: "1", status: "available" }),
        createTask({ task_id: "2", status: "in_progress" }),
        createTask({ task_id: "3", status: "completed" }),
      ];
      const stats = calculateTaskStatistics(tasks);
      expect(stats.openTasks).toBe(2);
    });

    it("should calculate high priority tasks", () => {
      const tasks = [
        createTask({ task_id: "1", priority: "critical" }),
        createTask({ task_id: "2", priority: "high" }),
        createTask({ task_id: "3", priority: "medium" }),
      ];
      const stats = calculateTaskStatistics(tasks);
      expect(stats.highPriorityTasks).toBe(2);
    });

    it("should count blocked tasks", () => {
      const tasks = [
        createTask({ task_id: "1", status: "blocked" }),
        createTask({ task_id: "2", status: "blocked" }),
        createTask({ task_id: "3", status: "available" }),
      ];
      const stats = calculateTaskStatistics(tasks);
      expect(stats.blockedTasks).toBe(2);
    });
  });

  describe("filterTasks", () => {
    const createTask = (overrides: Partial<Task> = {}): Task => ({
      task_id: "1",
      title: "Test Task",
      created_by: "user",
      assigned_to: null,
      claimed_by: null,
      priority: "medium",
      status: "available",
      hours_since_creation: 1,
      hours_since_claim: null,
      assigned_at: null,
      hours_since_assigned: null,
      created_at: new Date().toISOString(),
      claimed_at: null,
      completed_at: null,
      ...overrides,
    });

    it("should filter by task ID", () => {
      const tasks = [
        createTask({ task_id: "1" }),
        createTask({ task_id: "2" }),
      ];
      const filtered = filterTasks(tasks, { taskIdFilter: "1" });
      expect(filtered).toHaveLength(1);
      expect(filtered[0].task_id).toBe("1");
    });

    it("should filter by agent", () => {
      const tasks = [
        createTask({ task_id: "1", assigned_to: "agent1" }),
        createTask({ task_id: "2", assigned_to: "agent2" }),
      ];
      const filtered = filterTasks(tasks, { agentFilter: "agent1" });
      expect(filtered).toHaveLength(1);
      expect(filtered[0].assigned_to).toBe("agent1");
    });

    it("should filter unassigned tasks", () => {
      const tasks = [
        createTask({ task_id: "1", assigned_to: "agent1" }),
        createTask({ task_id: "2", assigned_to: null, claimed_by: null }),
      ];
      const filtered = filterTasks(tasks, { agentFilter: "unassigned" });
      expect(filtered).toHaveLength(1);
      expect(filtered[0].task_id).toBe("2");
    });

    it("should filter by status", () => {
      const tasks = [
        createTask({ task_id: "1", status: "available" }),
        createTask({ task_id: "2", status: "completed" }),
      ];
      const filtered = filterTasks(tasks, { statusSelect: "available" });
      expect(filtered).toHaveLength(1);
      expect(filtered[0].status).toBe("available");
    });

    it("should filter by search term", () => {
      const tasks = [
        createTask({ task_id: "1", title: "Fix the bug" }),
        createTask({ task_id: "2", title: "Add new feature" }),
      ];
      const filtered = filterTasks(tasks, { taskSearch: "bug" });
      expect(filtered).toHaveLength(1);
      expect(filtered[0].title).toContain("bug");
    });
  });

  describe("deduplicateTasks", () => {
    it("should remove duplicate tasks by task_id", () => {
      const tasks: Task[] = [
        {
          task_id: "1",
          title: "Task 1",
          created_by: "user",
          assigned_to: null,
          claimed_by: null,
          priority: "medium",
          status: "available",
          hours_since_creation: 1,
          hours_since_claim: null,
          assigned_at: null,
          hours_since_assigned: null,
          created_at: new Date().toISOString(),
          claimed_at: null,
          completed_at: null,
        },
        {
          task_id: "1", // Duplicate
          title: "Task 1 Updated",
          created_by: "user",
          assigned_to: null,
          claimed_by: null,
          priority: "high",
          status: "available",
          hours_since_creation: 1,
          hours_since_claim: null,
          assigned_at: null,
          hours_since_assigned: null,
          created_at: new Date().toISOString(),
          claimed_at: null,
          completed_at: null,
        },
        {
          task_id: "2",
          title: "Task 2",
          created_by: "user",
          assigned_to: null,
          claimed_by: null,
          priority: "medium",
          status: "available",
          hours_since_creation: 1,
          hours_since_claim: null,
          assigned_at: null,
          hours_since_assigned: null,
          created_at: new Date().toISOString(),
          claimed_at: null,
          completed_at: null,
        },
      ];

      const deduplicated = deduplicateTasks(tasks);
      expect(deduplicated).toHaveLength(2);
      expect(deduplicated[0].task_id).toBe("1");
      expect(deduplicated[1].task_id).toBe("2");
    });
  });

  describe("mergeTaskUpdate", () => {
    it("should merge update into existing task", () => {
      const existing: Task = {
        task_id: "1",
        title: "Original",
        created_by: "user",
        assigned_to: "agent1",
        claimed_by: null,
        priority: "medium",
        status: "available",
        hours_since_creation: 1,
        hours_since_claim: null,
        assigned_at: null,
        hours_since_assigned: null,
        created_at: new Date().toISOString(),
        claimed_at: null,
        completed_at: null,
      };

      const update = { status: "completed", title: "Updated" };
      const merged = mergeTaskUpdate(existing, update);

      expect(merged.title).toBe("Updated");
      expect(merged.status).toBe("completed");
      expect(merged.assigned_to).toBe("agent1"); // Preserved
      expect(merged.task_id).toBe("1"); // Preserved
    });
  });

  describe("shouldUpdateTask", () => {
    const baseTask: Task = {
      task_id: "1",
      title: "Task",
      created_by: "user",
      assigned_to: null,
      claimed_by: null,
      priority: "medium",
      status: "available",
      hours_since_creation: 1,
      hours_since_claim: null,
      assigned_at: null,
      hours_since_assigned: null,
      created_at: new Date().toISOString(),
      updated_at: "2024-01-02T10:00:00Z",
      claimed_at: null,
      completed_at: null,
    };

    it("should accept newer updates", () => {
      expect(
        shouldUpdateTask(baseTask, { updated_at: "2024-01-02T11:00:00Z" }),
      ).toBe(true);
    });

    it("should reject older updates", () => {
      expect(
        shouldUpdateTask(baseTask, { updated_at: "2024-01-02T09:00:00Z" }),
      ).toBe(false);
    });

    it("should accept updates when timestamps are missing", () => {
      const localWithoutTimestamp = { ...baseTask, updated_at: undefined };
      expect(
        shouldUpdateTask(localWithoutTimestamp, { status: "completed" }),
      ).toBe(true);
    });
  });

  describe("hasTaskPatchChanges", () => {
    const baseTask: Task = {
      task_id: "1",
      title: "Task",
      created_by: "user",
      assigned_to: null,
      claimed_by: null,
      priority: "medium",
      status: "available",
      hours_since_creation: 1,
      hours_since_claim: null,
      assigned_at: null,
      hours_since_assigned: null,
      created_at: new Date().toISOString(),
      updated_at: "2024-01-02T10:00:00Z",
      claimed_at: null,
      completed_at: null,
    };

    it("should return false when patch does not change values", () => {
      expect(hasTaskPatchChanges(baseTask, { status: "available" })).toBe(
        false,
      );
    });

    it("should return true when patch changes values", () => {
      expect(hasTaskPatchChanges(baseTask, { status: "completed" })).toBe(true);
    });

    it("should ignore undefined values", () => {
      expect(hasTaskPatchChanges(baseTask, { title: undefined })).toBe(false);
    });
  });

  describe("applyTaskSSEEvent", () => {
    const createTask = (id: string): Task => ({
      task_id: id,
      title: `Task ${id}`,
      created_by: "user",
      assigned_to: null,
      claimed_by: null,
      priority: "medium",
      status: "available",
      hours_since_creation: 1,
      hours_since_claim: null,
      assigned_at: null,
      hours_since_assigned: null,
      created_at: new Date().toISOString(),
      claimed_at: null,
      completed_at: null,
    });

    it("should add new task on task_created event", () => {
      const tasks = [createTask("1")];
      const event: TaskSSEEvent = {
        type: "task_created",
        task: { task_id: "2", title: "New Task", status: "available" },
      };
      const result = applyTaskSSEEvent(tasks, event);
      expect(result).toHaveLength(2);
      expect(result[0].task_id).toBe("2"); // New task at front
    });

    it("should update existing task on task_updated event", () => {
      const tasks = [createTask("1")];
      const event: TaskSSEEvent = {
        type: "task_updated",
        task: { task_id: "1", status: "completed" },
      };
      const result = applyTaskSSEEvent(tasks, event);
      expect(result).toHaveLength(1);
      expect(result[0].status).toBe("completed");
    });

    it("should remove task on task_deleted event", () => {
      const tasks = [createTask("1"), createTask("2")];
      const event: TaskSSEEvent = {
        type: "task_deleted",
        task: { task_id: "1" },
      };
      const result = applyTaskSSEEvent(tasks, event);
      expect(result).toHaveLength(1);
      expect(result[0].task_id).toBe("2");
    });

    it("should not duplicate on task_created if task exists", () => {
      const tasks = [createTask("1")];
      const event: TaskSSEEvent = {
        type: "task_created",
        task: { task_id: "1", title: "Duplicate" },
      };
      const result = applyTaskSSEEvent(tasks, event);
      expect(result).toHaveLength(1);
    });
  });

  describe("transformApiTask", () => {
    it("should transform API response to Task format", () => {
      const apiTask = {
        id: "task-123",
        title: "Test Task",
        description: "Description",
        posted_by: { username: "testuser" },
        assigned_agent: { name: "agent1" },
        priority: "P1",
        status: "in_progress",
        created_at: "2024-01-15T10:00:00Z",
        claimed_at: "2024-01-15T11:00:00Z",
        completed_at: null,
        requirements: { skills: ["typescript"] },
        links: ["https://example.com"],
      };

      const task = transformApiTask(apiTask);

      expect(task.task_id).toBe("task-123");
      expect(task.title).toBe("Test Task");
      expect(task.created_by).toBe("testuser");
      expect(task.assigned_to).toBe("agent1");
      expect(task.priority).toBe("P1");
      expect(task.status).toBe("in_progress");
      expect(task.skills).toEqual(["typescript"]);
      expect(task.links).toEqual(["https://example.com"]);
    });

    it("should handle missing fields gracefully", () => {
      const apiTask = {
        task_id: "task-456",
        title: "Minimal Task",
      };

      const task = transformApiTask(apiTask);

      expect(task.task_id).toBe("task-456");
      expect(task.title).toBe("Minimal Task");
      expect(task.description).toBe("No description provided");
      expect(task.created_by).toBe("Unknown");
      expect(task.priority).toBe("medium");
    });
  });
});
