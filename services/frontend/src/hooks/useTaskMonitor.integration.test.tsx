import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@/test/utils";
import { useTaskMonitor } from "@/hooks/useTaskMonitor";
import { api } from "@/lib/api-clean";

let taskEventCallback: ((event: any) => void) | null = null;

vi.mock("@/lib/realtime-manager", () => {
  return {
    getRealtimeManager: vi.fn(() => ({
      connectSSE: vi.fn(() => Promise.resolve()),
      onTaskEvent: (cb: (event: any) => void) => {
        taskEventCallback = cb;
        return () => {};
      },
      subscribeToAgentActivity: vi.fn(() => () => {}),
    })),
  };
});

vi.mock("@/lib/api-clean", () => ({
  api: {
    getTasks: vi.fn(),
  },
}));

const TestTaskMonitor = () => {
  const { tasks, isLoading } = useTaskMonitor({ enableRealtime: true });

  if (isLoading) return <div>loading</div>;

  return <div data-testid="task-title">{tasks[0]?.title ?? "none"}</div>;
};

describe("useTaskMonitor integration", () => {
  beforeEach(() => {
    taskEventCallback = null;
    vi.clearAllMocks();
  });

  it("ignores stale SSE updates with older updated_at", async () => {
    vi.mocked(api.getTasks).mockResolvedValue([
      {
        id: "task-1",
        title: "Newer Title",
        status: "available",
        priority: "high",
        updated_at: "2025-01-02T10:00:00Z",
        created_at: "2025-01-02T09:00:00Z",
      },
    ]);

    render(<TestTaskMonitor />);

    await waitFor(() =>
      expect(screen.getByTestId("task-title").textContent).toBe("Newer Title"),
    );

    await waitFor(() => {
      expect(taskEventCallback).toBeTypeOf("function");
    });

    act(() => {
      taskEventCallback?.({
        type: "updated",
        task: {
          id: "task-1",
          title: "Stale Title",
          status: "available",
          priority: "high",
          updated_at: "2025-01-02T09:00:00Z",
        },
      });
    });

    await waitFor(() =>
      expect(screen.getByTestId("task-title").textContent).toBe("Newer Title"),
    );
  });
});
