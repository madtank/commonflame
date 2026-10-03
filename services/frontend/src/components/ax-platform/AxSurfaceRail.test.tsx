import { render, screen, userEvent } from "@/test/utils";
import { describe, expect, it, vi } from "vitest";
import { AxSurfaceRail } from "./AxSurfaceRail";
import type { SpaceAgentNormalizedSurface } from "@/lib/space-agent-surfaces";

const widgetSurface: SpaceAgentNormalizedSurface = {
  id: "tool-call-1",
  sourceMessageId: "message-1",
  policyId: "tasks",
  workflowIds: ["tasks.list"],
  kind: "widget",
  placement: "panel",
  status: "ready",
  widget: {
    kind: "mcp_app",
    title: "Task Board",
    tool_name: "tasks.list",
    resource_uri: "ui://task-board@2",
    lifecycle: "complete",
    tool_result: {
      structuredContent: {
        count: 3,
        status: "open",
        created_at: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
        assignee: null,
        creator: { name: "madtank" },
        reminder_policy: {
          cadence: "every 30m",
          next_fire_at: "2026-04-12T18:30:00Z",
        },
        wake_up: {
          status: "queued",
          target: "madtank",
        },
      },
    },
  },
};

describe("AxSurfaceRail", () => {
  it("combines cards and widget opener into one card opened by the card surface", async () => {
    const onOpenWidgetPanel = vi.fn();
    const cardsSurface: SpaceAgentNormalizedSurface = {
      id: "cards:task-card-1",
      sourceMessageId: "message-1",
      policyId: "cards",
      workflowIds: [],
      kind: "cards",
      placement: "card",
      status: "ready",
      cards: [
        {
          card_id: "task-card-1",
          type: "task",
          version: 1,
          payload: {
            title: "CLI signal smoke",
            summary: "Task created from axctl.",
            source: "axctl_tasks_create",
            delivery: "task_notification",
            status: "open",
          },
        },
      ],
    };

    render(
      <AxSurfaceRail
        messageId="message-1"
        spaceId="space-1"
        surfaces={[cardsSurface, widgetSurface]}
        onAction={vi.fn()}
        onOpenWidgetPanel={onOpenWidgetPanel}
        timestamp="2026-04-12T18:00:00Z"
      />,
    );

    expect(screen.getByTestId("ax-widget-card")).toHaveTextContent(
      "CLI signal smoke",
    );
    expect(screen.getByTestId("ax-widget-card")).toHaveTextContent(
      "axctl_tasks_create",
    );
    expect(screen.getByText("Status:")).toBeInTheDocument();
    expect(screen.getAllByText("open").length).toBeGreaterThan(0);
    expect(screen.getByTestId("ax-card-open-surface")).toHaveAccessibleName(
      "Open Task Detail",
    );
    expect(screen.queryByTestId("ax-card-open-widget")).not.toBeInTheDocument();
    expect(screen.queryByTestId("ax-mcp-app-signal")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("ax-widget-card"));

    expect(onOpenWidgetPanel).toHaveBeenCalledWith({
      messageId: "message-1",
      surface: widgetSurface,
      timestamp: "2026-04-12T18:00:00Z",
    });
    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByTestId("ax-card-open-surface"));
    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(2);
  });

  it("links each card open action to the matching widget surface", async () => {
    const onOpenWidgetPanel = vi.fn();
    const cardsSurface: SpaceAgentNormalizedSurface = {
      id: "cards:multi-tool",
      sourceMessageId: "message-1",
      policyId: "cards",
      workflowIds: [],
      kind: "cards",
      placement: "card",
      status: "ready",
      cards: [
        {
          card_id: "task-card-1",
          type: "result",
          version: 1,
          payload: {
            title: "Task list",
            summary: "Open the task board.",
            tool_name: "tasks",
            tool_call_id: "tool-call-1",
          },
        },
        {
          card_id: "context-card-1",
          type: "result",
          version: 1,
          payload: {
            title: "Context upload",
            summary: "Open the context explorer.",
            tool_name: "context",
            tool_call_id: "tool-call-2",
          },
        },
      ],
    };
    const contextWidgetSurface: SpaceAgentNormalizedSurface = {
      ...widgetSurface,
      id: "tool-call-2",
      policyId: "context",
      workflowIds: ["context.list"],
      widget: {
        ...widgetSurface.widget,
        title: "Context Explorer",
        tool_name: "context",
        tool_call_id: "tool-call-2",
        resource_uri: "ui://context/explorer",
      },
    };

    render(
      <AxSurfaceRail
        messageId="message-1"
        spaceId="space-1"
        surfaces={[cardsSurface, widgetSurface, contextWidgetSurface]}
        onAction={vi.fn()}
        onOpenWidgetPanel={onOpenWidgetPanel}
        timestamp="2026-04-12T18:00:00Z"
      />,
    );

    expect(screen.getAllByTestId("ax-widget-card")).toHaveLength(2);
    expect(screen.queryByTestId("ax-mcp-app-signal")).not.toBeInTheDocument();

    const cards = screen.getAllByTestId("ax-widget-card");
    expect(screen.queryByTestId("ax-card-open-widget")).not.toBeInTheDocument();
    await userEvent.click(cards[1]);

    expect(onOpenWidgetPanel).toHaveBeenCalledWith({
      messageId: "message-1",
      surface: contextWidgetSurface,
      timestamp: "2026-04-12T18:00:00Z",
    });
    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(1);
  });

  it("links reminder alerts to task-detail widgets by task id", async () => {
    const onOpenWidgetPanel = vi.fn();
    const cardsSurface: SpaceAgentNormalizedSurface = {
      id: "cards:task-reminder",
      sourceMessageId: "message-1",
      policyId: "cards",
      workflowIds: [],
      kind: "cards",
      placement: "card",
      status: "ready",
      cards: [
        {
          card_id: "alert-card-1",
          type: "alert",
          version: 1,
          payload: {
            title: "Task reminder",
            summary: "Please follow up on the assigned task.",
            task_title: "Reminder alert should open task detail widget",
            task_id: "task-dogfood-1",
            alert: {
              kind: "task_reminder",
              severity: "warn",
            },
          },
        },
      ],
    };
    const widgetSurface: SpaceAgentNormalizedSurface = {
      id: "tool-call-task-detail",
      sourceMessageId: "message-1",
      policyId: "tasks",
      workflowIds: ["tasks.get"],
      kind: "widget",
      placement: "panel",
      status: "ready",
      widget: {
        kind: "mcp_app",
        tool_name: "tasks",
        tool_action: "get",
        title: "Task Detail",
        tool_result: {
          kind: "task",
          action: "get",
          selected_task_id: "task-dogfood-1",
          items: [
            {
              id: "task-dogfood-1",
              title: "Reminder alert should open task detail widget",
            },
          ],
        },
      },
    };

    render(
      <AxSurfaceRail
        messageId="message-1"
        spaceId="space-1"
        surfaces={[cardsSurface, widgetSurface]}
        onAction={vi.fn()}
        onOpenWidgetPanel={onOpenWidgetPanel}
        timestamp="2026-04-16T18:30:00Z"
      />,
    );

    expect(screen.queryByTestId("ax-card-open-widget")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("ax-widget-card"));

    expect(onOpenWidgetPanel).toHaveBeenCalledWith({
      messageId: "message-1",
      surface: widgetSurface,
      timestamp: "2026-04-16T18:30:00Z",
    });
  });

  it("renders MCP widget surfaces as compact signals when an app panel opener is provided", async () => {
    const onOpenWidgetPanel = vi.fn();
    const onForwardInit = vi.fn();

    render(
      <AxSurfaceRail
        messageId="message-1"
        spaceId="space-1"
        surfaces={[widgetSurface]}
        onAction={vi.fn()}
        onOpenWidgetPanel={onOpenWidgetPanel}
        onForwardInit={onForwardInit}
        timestamp="2026-04-12T18:00:00Z"
      />,
    );

    const signal = screen.getByTestId("ax-mcp-app-signal");
    expect(screen.queryByTestId("ax-mcp-widget")).not.toBeInTheDocument();
    expect(signal).toHaveTextContent("Task Board");
    expect(signal).not.toHaveTextContent("tasks.list");
    expect(signal).toHaveTextContent("3 tasks");
    expect(signal).toHaveTextContent("Status: open");
    expect(signal).toHaveTextContent("Unassigned");
    expect(signal).toHaveTextContent("Reminder: every 30m");
    expect(signal).toHaveTextContent("Next: 2026-04-12T18:30:00Z");
    expect(signal).toHaveTextContent("Created: 10 minutes ago");
    expect(signal).toHaveTextContent("Creator: madtank");
    expect(signal).toHaveTextContent("Wake-up: queued");
    expect(signal).toHaveClass("shadow-[0_18px_42px_-30px_rgba(8,47,73,0.75)]");
    expect(screen.getByText("Status:").parentElement).toHaveClass(
      "bg-slate-100/80",
      "text-slate-700",
      "dark:bg-slate-950/55",
    );
    expect(screen.getByText("open")).toHaveClass(
      "text-slate-900",
      "dark:text-slate-100",
    );

    await userEvent.click(screen.getByTestId("ax-mcp-app-signal-share"));
    expect(onForwardInit).toHaveBeenCalledWith({
      messageId: "message-1",
      cardId: "widget:tool-call-1",
      card: expect.objectContaining({
        card_id: "widget:tool-call-1",
        type: "result",
        payload: expect.objectContaining({
          title: "Task Board",
          summary: "3 tasks",
          tool_name: "tasks.list",
          resource_uri: "ui://task-board@2",
        }),
      }),
    });
    expect(onOpenWidgetPanel).not.toHaveBeenCalled();

    await userEvent.click(signal);

    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(1);
    expect(onOpenWidgetPanel).toHaveBeenLastCalledWith({
      messageId: "message-1",
      surface: widgetSurface,
      timestamp: "2026-04-12T18:00:00Z",
    });

    expect(
      screen.queryByTestId("ax-mcp-app-signal-open"),
    ).not.toBeInTheDocument();
  });

  it("highlights approval-style widget signals as needing review", () => {
    const reviewSurface: SpaceAgentNormalizedSurface = {
      ...widgetSurface,
      id: "tool-call-review",
      policyId: "agents",
      workflowIds: ["agents.create"],
      widget: {
        ...widgetSurface.widget,
        title: "Request processed",
        tool_name: "agents.create",
        lifecycle: "approval_required",
        tool_result: {
          structuredContent: {
            draft_id: "draft-1",
          },
        },
      },
    };

    render(
      <AxSurfaceRail
        messageId="message-1"
        spaceId="space-1"
        surfaces={[reviewSurface]}
        onAction={vi.fn()}
        onOpenWidgetPanel={vi.fn()}
      />,
    );

    const signal = screen.getByTestId("ax-mcp-app-signal");
    expect(signal).toHaveTextContent("Needs review");
    expect(signal).toHaveTextContent("Human review required");
    expect(
      screen.queryByTestId("ax-mcp-app-signal-open"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Review")).not.toBeInTheDocument();
  });

  it("does not mark ordinary informational text as needing review", () => {
    const infoSurface: SpaceAgentNormalizedSurface = {
      ...widgetSurface,
      id: "tool-call-info",
      workflowIds: ["tasks.list"],
      widget: {
        ...widgetSurface.widget,
        title: "Task Board",
        lifecycle: "complete",
        tool_result: {
          summary: "A human-readable draft summary is available.",
        },
      },
    };

    render(
      <AxSurfaceRail
        messageId="message-1"
        spaceId="space-1"
        surfaces={[infoSurface]}
        onAction={vi.fn()}
        onOpenWidgetPanel={vi.fn()}
      />,
    );

    const signal = screen.getByTestId("ax-mcp-app-signal");
    expect(signal).toHaveTextContent("Ready");
    expect(
      screen.queryByTestId("ax-mcp-app-signal-open"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Open")).not.toBeInTheDocument();
    expect(signal).not.toHaveTextContent("Needs review");
  });

  it("does not mark identity permission payloads as needing review", () => {
    const infoSurface: SpaceAgentNormalizedSurface = {
      ...widgetSurface,
      id: "tool-call-whoami",
      policyId: "whoami",
      workflowIds: ["whoami.get"],
      widget: {
        ...widgetSurface.widget,
        title: "Request processed",
        tool_name: "whoami",
        lifecycle: "complete",
        tool_result: {
          structuredContent: {
            kind: "whoami_profile",
            data: {
              identity: { handle: "aX" },
              permissions: {
                can_use_hitl_approval: true,
                can_create_drafts: true,
              },
            },
          },
        },
      },
    };

    render(
      <AxSurfaceRail
        messageId="message-1"
        spaceId="space-1"
        surfaces={[infoSurface]}
        onAction={vi.fn()}
        onOpenWidgetPanel={vi.fn()}
      />,
    );

    const signal = screen.getByTestId("ax-mcp-app-signal");
    expect(signal).toHaveTextContent("Ready");
    expect(
      screen.queryByTestId("ax-mcp-app-signal-open"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Open")).not.toBeInTheDocument();
    expect(signal).not.toHaveTextContent("Needs review");
  });
});
