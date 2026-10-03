/**
 * useTaskMonitor - Real-time Task State Mirror Hook
 *
 * This hook implements the State Mirror Architecture for tasks,
 * providing real-time synchronization between backend and UI state.
 *
 * Features:
 * - SSE event subscription for task updates
 * - React Query cache integration
 * - Agent activity tracking (processing status)
 * - Callback system for non-Query components
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { useQueryClient, useQuery } from "@tanstack/react-query";
import { getRealtimeManager, AgentActivityEvent } from "@/lib/realtime-manager";
import { api } from "@/lib/api-clean";
import { Task, TaskSSEEvent, TaskStatistics } from "@/lib/tasks/task-types";
import { calculateTaskStatistics } from "@/lib/tasks/task-logic";
import {
  applyTaskSSEEvent,
  deduplicateTasks,
  hasTaskPatchChanges,
  shouldUpdateTask,
  transformApiTask,
} from "@/lib/tasks/task-store-utils";

// ============================================================================
// Types
// ============================================================================

export interface TaskProcessingState {
  taskId: string;
  agentId: string;
  agentName: string;
  toolName: string;
  status: "started" | "processing" | "completed" | "error";
  startedAt: number;
  details?: string;
}

export interface UseTaskMonitorOptions {
  /** Enable real-time updates via SSE */
  enableRealtime?: boolean;
  /** Initial task limit for fetching */
  initialLimit?: number;
  /** Optional space/org identifier for cache isolation */
  spaceId?: string | null;
  /** Callback when a task is updated */
  onTaskUpdate?: (event: TaskSSEEvent) => void;
  /** Callback when agent starts processing a task */
  onProcessingStart?: (state: TaskProcessingState) => void;
  /** Callback when agent stops processing a task */
  onProcessingEnd?: (taskId: string) => void;
}

export interface UseTaskMonitorResult {
  /** Current list of tasks */
  tasks: Task[];
  /** Loading state */
  isLoading: boolean;
  /** Error state */
  error: Error | null;
  /** Currently processing tasks (agent activity) */
  processingTasks: Map<string, TaskProcessingState>;
  /** Task statistics */
  statistics: TaskStatistics;
  /** Refetch tasks manually */
  refetch: () => void;
  /** Check if a specific task is being processed */
  isTaskProcessing: (taskId: string) => boolean;
  /** Get processing state for a task */
  getProcessingState: (taskId: string) => TaskProcessingState | undefined;
  /** SSE connection state */
  connectionState: "connected" | "connecting" | "disconnected" | "error";
}

// ============================================================================
// Query Keys
// ============================================================================

export const taskQueryKeys = {
  all: ["tasks"] as const,
  list: (limit?: number, spaceId?: string | null) =>
    ["tasks", "list", limit ?? null, spaceId ?? null] as const,
  detail: (taskId: string) => ["tasks", "detail", taskId] as const,
  stats: () => ["tasks", "stats"] as const,
};

// ============================================================================
// Hook Implementation
// ============================================================================

export function useTaskMonitor(
  options: UseTaskMonitorOptions = {},
): UseTaskMonitorResult {
  const {
    enableRealtime = true,
    initialLimit = 500,
    spaceId = null,
    onTaskUpdate,
    onProcessingStart,
    onProcessingEnd,
  } = options;

  const queryClient = useQueryClient();
  const [processingTasks, setProcessingTasks] = useState<
    Map<string, TaskProcessingState>
  >(new Map());
  const [connectionState, setConnectionState] = useState<
    "connected" | "connecting" | "disconnected" | "error"
  >("disconnected");

  // Refs to hold latest callbacks without triggering re-subscriptions
  const onTaskUpdateRef = useRef(onTaskUpdate);
  const onProcessingStartRef = useRef(onProcessingStart);
  const onProcessingEndRef = useRef(onProcessingEnd);

  useEffect(() => {
    onTaskUpdateRef.current = onTaskUpdate;
    onProcessingStartRef.current = onProcessingStart;
    onProcessingEndRef.current = onProcessingEnd;
  }, [onTaskUpdate, onProcessingStart, onProcessingEnd]);

  // -------------------------------------------------------------------------
  // React Query for task fetching
  // -------------------------------------------------------------------------
  const listKey = taskQueryKeys.list(initialLimit, spaceId);
  const {
    data: rawTasksData,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: listKey,
    queryFn: async () => {
      const response = await api.getTasks(initialLimit);
      // Handle various response formats
      let tasks: Task[] = [];
      if (Array.isArray(response)) {
        tasks = response.map(transformApiTask);
      } else if (response?.tasks && Array.isArray(response.tasks)) {
        tasks = response.tasks.map(transformApiTask);
      } else if (response?.data && Array.isArray(response.data)) {
        tasks = response.data.map(transformApiTask);
      }
      return deduplicateTasks(tasks);
    },
    staleTime: 30000, // 30 seconds
    retry: 3,
  });

  const tasks = rawTasksData ?? [];

  // Calculate statistics from tasks
  const statistics = calculateTaskStatistics(tasks);

  // -------------------------------------------------------------------------
  // SSE Event Handling
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!enableRealtime) return;

    const realtimeManager = getRealtimeManager(queryClient);

    // Handle SSE state changes
    const handleSseState = (e: CustomEvent) => {
      setConnectionState(e.detail.state);
    };
    window.addEventListener("sse:state", handleSseState as EventListener);

    // Connect SSE
    realtimeManager.connectSSE().catch((err) => {
      console.warn("[TaskMonitor] Failed to connect SSE:", err);
      setConnectionState("error");
    });

    // Subscribe to task events
    const unsubscribeTask = realtimeManager.onTaskEvent((event) => {
      // Normalize the event to our TaskSSEEvent format
      const normalizedEvent: TaskSSEEvent = {
        type:
          event.type === "created"
            ? "task_created"
            : event.type === "deleted"
              ? "task_deleted"
              : "task_updated",
        task: {
          task_id: event.task.id,
          title: event.task.title,
          status: event.task.status,
          priority: event.task.priority,
          assigned_to: event.task.assigned_agent_id || event.task.assigned_to,
          ...event.task,
        },
        actor: event.actor,
        org_id: event.org_id,
      };

      // Update React Query cache directly for immediate UI update
      queryClient.setQueryData(listKey, (oldTasks: Task[] | undefined) => {
        if (!oldTasks) return oldTasks;

        if (
          normalizedEvent.type === "task_updated" ||
          normalizedEvent.type === "task_created"
        ) {
          const existingTask = oldTasks.find(
            (task) => task.task_id === normalizedEvent.task.task_id,
          );

          if (
            existingTask &&
            !shouldUpdateTask(existingTask, normalizedEvent.task)
          ) {
            return oldTasks;
          }
        }

        return applyTaskSSEEvent(oldTasks, normalizedEvent);
      });

      // Also invalidate to ensure freshness
      queryClient.invalidateQueries({
        queryKey: taskQueryKeys.all,
        refetchType: "none", // Don't refetch immediately, just mark stale
      });

      // Call user callback
      onTaskUpdateRef.current?.(normalizedEvent);
    });

    // Subscribe to agent activity events for processing state
    const unsubscribeActivity = realtimeManager.subscribeToAgentActivity(
      (event: AgentActivityEvent) => {
        const taskId =
          event.target_task_id ??
          event.task_id ??
          event.task?.task_id ??
          event.task?.id ??
          event.parent_message_id;

        // Only track activity that's related to tasks
        if (!taskId) return;

        if (event.status === "started" || event.status === "processing") {
          const processingState: TaskProcessingState = {
            taskId,
            agentId: event.agent_id,
            agentName: event.agent_name,
            toolName: event.tool_name,
            status: event.status === "started" ? "started" : "processing",
            startedAt: Date.now(),
            details: event.details,
          };

          setProcessingTasks((prev) => {
            const next = new Map(prev);
            next.set(taskId, processingState);
            return next;
          });

          onProcessingStartRef.current?.(processingState);
        } else if (event.status === "completed" || event.status === "error") {
          setProcessingTasks((prev) => {
            const next = new Map(prev);
            next.delete(taskId);
            return next;
          });

          onProcessingEndRef.current?.(taskId);
        }
      },
    );

    return () => {
      window.removeEventListener("sse:state", handleSseState as EventListener);
      unsubscribeTask();
      unsubscribeActivity();
    };
  }, [enableRealtime, queryClient, initialLimit, listKey, spaceId]);

  // -------------------------------------------------------------------------
  // Helper Functions
  // -------------------------------------------------------------------------
  const isTaskProcessing = useCallback(
    (taskId: string): boolean => {
      return processingTasks.has(taskId);
    },
    [processingTasks],
  );

  const getProcessingState = useCallback(
    (taskId: string): TaskProcessingState | undefined => {
      return processingTasks.get(taskId);
    },
    [processingTasks],
  );

  return {
    tasks,
    isLoading,
    error: error as Error | null,
    processingTasks,
    statistics,
    refetch,
    isTaskProcessing,
    getProcessingState,
    connectionState,
  };
}

// ============================================================================
// Additional Hooks
// ============================================================================

/**
 * Hook to monitor a single task's real-time state
 */
export function useTaskDetail(taskId: string | null) {
  const queryClient = useQueryClient();

  const {
    data: task,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: taskQueryKeys.detail(taskId ?? ""),
    queryFn: async () => {
      if (!taskId) return null;
      // Fetch single task from API
      const response = await api.getTask(taskId);
      return response ? transformApiTask(response) : null;
    },
    enabled: !!taskId,
    staleTime: 10000, // 10 seconds
  });

  // Subscribe to real-time updates for this specific task
  useEffect(() => {
    if (!taskId) return;

    const realtimeManager = getRealtimeManager(queryClient);

    const unsubscribe = realtimeManager.onTaskEvent((event) => {
      const incomingTaskId =
        (event.task as { task_id?: string; id?: string }).task_id ??
        event.task.id;

      if (!incomingTaskId || incomingTaskId !== taskId) return;

      const incomingTask: Partial<Task> = {
        ...(event.task as Partial<Task>),
        task_id: incomingTaskId,
      };

      // Update the cache for this specific task
      queryClient.setQueryData(
        taskQueryKeys.detail(taskId),
        (old: Task | null | undefined) => {
          if (!old) return old;
          if (!shouldUpdateTask(old, incomingTask)) return old;
          if (!hasTaskPatchChanges(old, incomingTask)) return old;
          return {
            ...old,
            ...incomingTask,
            task_id: old.task_id,
          };
        },
      );
    });

    return () => {
      unsubscribe();
    };
  }, [taskId, queryClient]);

  return {
    task,
    isLoading,
    error: error as Error | null,
    refetch,
  };
}

/**
 * Hook to get real-time task processing indicator
 * Use this to show "Agent is working..." indicators
 */
export function useTaskProcessingIndicator(taskId: string) {
  const [processingState, setProcessingState] =
    useState<TaskProcessingState | null>(null);

  useEffect(() => {
    if (!taskId) return;

    const handleActivity = (e: CustomEvent<AgentActivityEvent>) => {
      const event = e.detail;
      const eventTaskId =
        event.target_task_id ??
        event.task_id ??
        event.task?.task_id ??
        event.task?.id ??
        event.parent_message_id;

      if (!eventTaskId || eventTaskId !== taskId) return;

      if (event.status === "started" || event.status === "processing") {
        setProcessingState({
          taskId,
          agentId: event.agent_id,
          agentName: event.agent_name,
          toolName: event.tool_name,
          status: event.status === "started" ? "started" : "processing",
          startedAt: Date.now(),
          details: event.details,
        });
      } else {
        setProcessingState(null);
      }
    };

    window.addEventListener(
      "ax:agent-activity",
      handleActivity as EventListener,
    );

    return () => {
      window.removeEventListener(
        "ax:agent-activity",
        handleActivity as EventListener,
      );
    };
  }, [taskId]);

  return {
    isProcessing: processingState !== null,
    processingState,
    agentName: processingState?.agentName,
    toolName: processingState?.toolName,
  };
}

// ============================================================================
// Task Cache Utilities
// ============================================================================

/**
 * Optimistically update a task in the cache
 * Useful for immediate UI feedback before server confirmation
 */
export function useOptimisticTaskUpdate(
  listLimit?: number,
  spaceId?: string | null,
) {
  const queryClient = useQueryClient();
  const listKey = taskQueryKeys.list(listLimit, spaceId);

  const updateTask = useCallback(
    (taskId: string, updates: Partial<Task>) => {
      // Update in list cache
      queryClient.setQueryData(listKey, (old: Task[] | undefined) => {
        if (!old) return old;
        return old.map((task) =>
          task.task_id === taskId ? { ...task, ...updates } : task,
        );
      });

      // Update in detail cache if exists
      queryClient.setQueryData(
        taskQueryKeys.detail(taskId),
        (old: Task | null | undefined) => {
          if (!old) return old;
          return { ...old, ...updates };
        },
      );
    },
    [queryClient, listKey],
  );

  const invalidateTasks = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: taskQueryKeys.all });
  }, [queryClient]);

  return { updateTask, invalidateTasks };
}
