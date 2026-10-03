import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@/test/utils";
import userEvent from "@testing-library/user-event";
import { AxMessageWidgets } from "./AxMessageWidgets";
import type { SpaceAgentCardEnvelope } from "@/lib/space-agent-api";

describe("AxMessageWidgets", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  const linkedWidgetSurface = {
    id: "widget:identity",
    kind: "widget" as const,
    placement: "panel" as const,
    status: "ready" as const,
    widget: {
      tool_name: "whoami",
      resource_uri: "ui://whoami/identity",
    },
  };

  it("keeps long assignee labels inside task metadata cards", () => {
    const longAssignee =
      "frontend_sentinel_super_long_agent_handle_that_should_wrap_inside_the_task_card";

    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "task-card-1",
        type: "task",
        version: 1,
        payload: {
          title: "Fix MCP task overflow",
          priority: "high",
          task_id: "task-123",
          assignee: {
            name: longAssignee,
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
      />,
    );

    const assigneeValue = screen.getByText(longAssignee);
    expect(assigneeValue.className).toContain("break-words");
    expect(assigneeValue.className).toContain("overflow-wrap-anywhere");

    const assigneeCard = assigneeValue.parentElement;
    expect(assigneeCard?.className).toContain("min-w-0");
    expect(assigneeCard?.className).toContain("max-w-full");
    expect(assigneeCard?.className).toContain("flex-1");
    expect(assigneeCard?.className).toContain("basis-[180px]");
  });

  it("hides stale task cards by default and can include them", async () => {
    const user = userEvent.setup();
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "task-active",
        type: "task",
        version: 1,
        payload: {
          title: "Active queue task",
          status: "in_progress",
          queue_position: 2,
          queue_total: 5,
          work_status: "wip",
          next_reminder_at: "2026-04-17T00:00:00Z",
        },
      },
      {
        card_id: "task-stale",
        type: "task",
        version: 1,
        payload: {
          title: "Old stale task",
          is_stale: true,
          status: "completed",
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-task-lifecycle"
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("Active queue task")).toBeInTheDocument();
    expect(screen.getByText("Queue")).toBeInTheDocument();
    expect(screen.getByText("2/5")).toBeInTheDocument();
    expect(screen.getByText("Work")).toBeInTheDocument();
    expect(screen.getByText("wip")).toBeInTheDocument();
    expect(screen.getByText("Reminder")).toBeInTheDocument();
    expect(screen.queryByText("Old stale task")).not.toBeInTheDocument();
    expect(screen.getByText("1 stale task hidden")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /include stale/i }));
    expect(screen.getByText("Old stale task")).toBeInTheDocument();
  });

  it("surfaces queue and work lifecycle on task reminder alerts", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-task-queue",
        type: "alert",
        version: 1,
        payload: {
          title: "Task reminder",
          summary: "Queue reminder fired.",
          alert: {
            kind: "task_reminder",
            severity: "info",
            task: {
              title: "Keep queue moving",
              deadline: "2026-04-17T00:00:00Z",
              active_queue_position: 1,
              active_queue_total: 3,
              lifecycle_status: "waiting_for_agent",
            },
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-reminder-lifecycle"
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("Keep queue moving")).toBeInTheDocument();
    expect(screen.getByText("Queue:")).toBeInTheDocument();
    expect(screen.getByText("1/3")).toBeInTheDocument();
    expect(screen.getByText("Work:")).toBeInTheDocument();
    expect(screen.getByText("waiting for agent")).toBeInTheDocument();
  });

  it("binds live activity stream state to the originating reminder alert card", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert:reminder-live",
        type: "alert",
        version: 1,
        payload: {
          title: "Task reminder",
          summary: "Reminder fired and dispatched to the agent.",
          alert: {
            id: "alert-live-1",
            kind: "task_reminder",
            severity: "info",
            source: "dogfood_reminder_alert",
            target_agent: "canary",
            task: {
              title: "Render live reminder activity",
              active_queue_position: 2,
              active_queue_total: 4,
            },
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="alert-message-1"
        onAction={vi.fn()}
        pendingResponsesBySourceId={{
          "alert-message-1": {
            sourceEntryId: "alert-message-1",
            targetHandle: "canary",
            targetLabel: "canary",
            activeAgentLabel: "canary",
            statusLabel: "tool_call",
            toolName: "terminal",
            activity: "Running targeted widget tests",
            toolCount: 2,
            stepLabel: "RED check",
            commandLabels: ["npm test AxMessageWidgets"],
            progress: { current: 1, total: 3, unit: "steps" },
            reason: null,
            errorMessage: null,
            retryAfterSeconds: null,
            isKnownTarget: true,
            hasPresenceSignal: false,
            presenceStatus: null,
          },
        }}
      />,
    );

    expect(screen.getByText("Work:")).toBeInTheDocument();
    expect(
      screen.getAllByText("canary: Running targeted widget tests"),
    ).toHaveLength(2);

    const monitor = screen.getByTestId("ax-card-activity-monitor");
    expect(monitor).toHaveTextContent("To canary");
    expect(monitor).toHaveTextContent("canary: Running targeted widget tests");
    expect(monitor).toHaveTextContent("2 tools active");
    expect(monitor).toHaveTextContent("1/3 steps");
    expect(monitor).toHaveTextContent("RED check");
    expect(monitor).toHaveTextContent("npm test AxMessageWidgets");
    expect(
      screen.queryByText("No recent agent activity"),
    ).not.toBeInTheDocument();
  });

  it("shows activity-region mock states for reminder-capable cards", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "reminder-no-activity",
        type: "task",
        version: 1,
        payload: {
          title: "Unclaimed reminder",
          next_reminder_at: "2026-04-17T00:00:00Z",
        },
      },
      {
        card_id: "reminder-processing",
        type: "alert",
        version: 1,
        payload: {
          alert: {
            kind: "task_reminder",
            task: { title: "Frontend follow-up" },
            agent_activity: {
              agent_name: "canary",
              status: "processing",
              summary: "Reviewing the reminder surface now.",
            },
          },
        },
      },
      {
        card_id: "reminder-no-reply",
        type: "task",
        version: 1,
        payload: {
          title: "Quiet closeout",
          can_notify_agent: true,
          agent_activity: {
            agent_name: "atlas",
            status: "no_reply",
            summary: "Safe-word closeout; no chat body was sent.",
          },
        },
      },
      {
        card_id: "reminder-blocked",
        type: "task",
        version: 1,
        payload: {
          title: "Blocked handoff",
          notify_action_id: "notify-blocked-owner",
          activity: {
            agent_name: "daimon",
            status: "blocked",
            summary: "BLOCKED: need backend status before retrying.",
          },
        },
      },
      {
        card_id: "reminder-documented-target",
        type: "task",
        version: 1,
        payload: {
          event: "reminder_due",
          title: "Documented scheduler shape",
          reminder_policy: { cadence_minutes: 30 },
          notification_target: {
            type: "agent",
            name: "frontend_sentinel",
            status: "processing",
            delivery_state: "delivered",
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-reminder-activity"
        onAction={vi.fn()}
      />,
    );

    const activityRegions = screen.getAllByTestId("ax-card-agent-activity");
    expect(activityRegions).toHaveLength(5);
    expect(screen.getByText("Agent activity")).toBeInTheDocument();
    expect(screen.getByText("No recent agent activity"));
    expect(screen.getByText("canary is processing"));
    expect(screen.getByText("Reviewing the reminder surface now."));
    expect(screen.getByText("atlas no reply"));
    expect(screen.getByText("Safe-word closeout; no chat body was sent."));
    expect(screen.getByText("daimon blocked"));
    expect(screen.getByText("BLOCKED: need backend status before retrying."));
    expect(screen.getByText("frontend_sentinel is processing"));
    expect(screen.getByText("Delivery: delivered"));
  });

  it("labels app-backed result cards by their app surface", () => {
    vi.spyOn(Date, "now").mockReturnValue(
      new Date("2026-04-14T17:27:00Z").getTime(),
    );

    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "task-card-2",
        type: "result",
        version: 1,
        payload: {
          title: "Run smoke tests",
          summary: "Open the task card for details.",
          tool_name: "tasks",
          resource_uri: "ui://tasks/board",
          source: "axctl_tasks_create",
          delivery: "task_notification",
          sender_label: "ChatGPT",
          priority: "medium",
          created_at: "2026-04-14T17:18:56.914866+00:00",
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
        linkedWidgetSurface={linkedWidgetSurface}
        onOpenWidgetPanel={vi.fn()}
      />,
    );

    // v2: singular resource-type labels — "Tasks" → "Task".
    const taskLabel = screen.getByText(
      (_, element) =>
        element?.textContent === "Task" &&
        element.className.includes("rounded-full"),
    );
    expect(taskLabel).toBeInTheDocument();
    expect(taskLabel.className).toContain("bg-slate-950/70");
    expect(taskLabel.className).not.toContain("bg-gray-100");
    expect(taskLabel.className).not.toContain("bg-cyan");
    expect(screen.queryByText("MCP App")).not.toBeInTheDocument();
    expect(screen.getByText("From:")).toBeInTheDocument();
    expect(screen.getByText("ChatGPT")).toBeInTheDocument();
    expect(screen.getByText("Source:")).toBeInTheDocument();
    expect(screen.getByText("axctl_tasks_create")).toBeInTheDocument();
    expect(screen.getByText("Delivery:")).toBeInTheDocument();
    expect(screen.getByText("task_notification")).toBeInTheDocument();
    expect(screen.queryByText("Sent:")).not.toBeInTheDocument();
    expect(screen.getByText("8 minutes ago")).toBeInTheDocument();
    expect(screen.queryByText(/2026-04-14T17/)).not.toBeInTheDocument();
  });

  it("renders alert cards as slim wake-up notifications", () => {
    vi.spyOn(Date, "now").mockReturnValue(
      new Date("2026-04-14T05:28:00Z").getTime(),
    );

    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-card-1",
        type: "alert",
        version: 1,
        payload: {
          title: "Supervisor nudge",
          summary: "Review queue triage",
          sender_label: "orion",
          sender_type: "agent",
          agent_id: "agent-orion-1234567890",
          alert: {
            kind: "supervisor_nudge",
            severity: "warn",
            source: "ax-nudge-review-triage",
            origin: "systemd-timer",
            target_agent: "frontend_sentinel",
            response_required: true,
            expected_response: "Acknowledge and clear the review queue.",
            attachments: [{ filename: "qa-smoke.png" }],
            target_group: "sentinels",
            cycle: { current: 1, max: 5 },
            cadence: "15m",
            completion_promise: "REVIEW QUEUE CLEARED",
            fired_at: "2026-04-14T05:20:00Z",
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("Alert")).toBeInTheDocument();
    expect(screen.getByText("Warn")).toBeInTheDocument();
    expect(screen.getByText("Target:")).toBeInTheDocument();
    expect(screen.getByText("frontend_sentinel")).toBeInTheDocument();
    expect(screen.getByText("Response:")).toBeInTheDocument();
    expect(screen.getByText("Required")).toBeInTheDocument();
    expect(screen.getByText("Evidence:")).toBeInTheDocument();
    expect(screen.getByText("1 attachment")).toBeInTheDocument();
    expect(screen.queryByText("Age:")).not.toBeInTheDocument();
    expect(screen.getByText("8 minutes ago")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /details/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("From:")).not.toBeInTheDocument();
    // v2 polish — Source chip is now rendered as its own fact chip when
    // alert.source is present (was previously bundled into Evidence).
    expect(screen.getByText("Source:")).toBeInTheDocument();
    expect(screen.getByText("ax-nudge-review-triage")).toBeInTheDocument();
    expect(screen.queryByText("Via:")).not.toBeInTheDocument();
    expect(screen.queryByText("Expected response")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Acknowledge and clear the review queue."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("2026-04-14T05:20:00Z")).not.toBeInTheDocument();
  });

  it("shows context evidence on alert cards without requiring attachment metadata", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-context-card",
        type: "alert",
        version: 1,
        payload: {
          title: "Alert evidence QA",
          summary: "Screenshot uploaded for review.",
          sender_label: "chatgpt_dev",
          context_key: "upload:qa-smoke.png",
          alert: {
            kind: "qa_widget_frame",
            severity: "warn",
            source: "axctl_apps_signal",
            target_agent: "cipher_shepherd",
            response_required: true,
            context_key: "upload:qa-smoke.png",
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("Evidence:")).toBeInTheDocument();
    expect(screen.getByText("Context attached")).toBeInTheDocument();
  });

  it("uses task subject on reminder alerts without rendering task context chrome", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-task-context-card",
        type: "alert",
        version: 1,
        payload: {
          title: "Task reminder",
          summary: "Please pick this back up.",
          task_title: "Reminder alert should open task detail widget",
          task_summary: "User-facing reminder copied from the task card.",
          task_id: "task-dogfood-1",
          task_status: "in_progress",
          task_priority: "high",
          assignee: {
            name: "frontend_sentinel",
          },
          alert: {
            kind: "task_reminder",
            severity: "warn",
            source: "dogfood_reminder_alert",
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Reminder alert should open task detail widget"),
    ).toBeInTheDocument();
    expect(screen.getByText("Please pick this back up.")).toBeInTheDocument();
    expect(screen.queryByText("Task context")).not.toBeInTheDocument();
    expect(screen.queryByText("Task ID:")).not.toBeInTheDocument();
    expect(screen.queryByText("Assignee:")).not.toBeInTheDocument();
    expect(screen.queryByText("frontend_sentinel")).not.toBeInTheDocument();
  });

  it("uses nested alert.task title as the visible reminder subject", () => {
    // axctl emits metadata.alert.task = {id, title, priority, status,
    // assignee_name, deadline}. The stream card stays slim and uses the task
    // title as the subject; the full task snapshot is for the widget/agent.
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-nested-task-snapshot",
        type: "alert",
        version: 1,
        payload: {
          title: "Task reminder",
          summary: "Pick this up.",
          alert: {
            kind: "task_reminder",
            severity: "info",
            source: "axctl_reminders",
            task: {
              id: "task-nested-1",
              title: "Ship Activity Stream polish",
              priority: "urgent",
              status: "in_progress",
              assignee_name: "orion",
              deadline: "2026-04-17T00:00:00Z",
            },
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("Reminder")).toBeInTheDocument();
    expect(screen.getByText("Ship Activity Stream polish")).toBeInTheDocument();
    expect(screen.getByText("Pick this up.")).toBeInTheDocument();
    expect(screen.getByText("Due:")).toBeInTheDocument();
    expect(screen.queryByText("Task context")).not.toBeInTheDocument();
    expect(screen.queryByText("Status:")).not.toBeInTheDocument();
    expect(screen.queryByText("Priority:")).not.toBeInTheDocument();
    expect(screen.queryByText("Assignee:")).not.toBeInTheDocument();
    expect(screen.queryByText("Task ID:")).not.toBeInTheDocument();
  });

  it("does not show task context for non-task alerts that only have a title", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-non-task-card",
        type: "alert",
        version: 1,
        payload: {
          title: "Supervisor nudge",
          summary: "Review queue triage",
          alert: {
            kind: "supervisor_nudge",
            severity: "warn",
            source: "ax-nudge-review-triage",
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
      />,
    );

    expect(screen.queryByText("Task context")).not.toBeInTheDocument();
    expect(screen.queryByText("Supervisor nudge")).toBeInTheDocument();
    expect(screen.queryByText("Review queue triage")).toBeInTheDocument();
  });

  it("opens result-card markdown links in a new tab", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "result-link-card",
        type: "result",
        version: 1,
        payload: {
          title: "Link smoke",
          body_markdown: "[Open report](https://example.com/report)",
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
      />,
    );

    const link = screen.getByRole("link", { name: "Open report" });
    expect(link).toHaveAttribute("href", "https://example.com/report");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("opens linked widget detail from the card surface without a visible open icon", async () => {
    const user = userEvent.setup();
    const onOpenWidgetPanel = vi.fn();
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-card-open",
        type: "alert",
        version: 1,
        payload: {
          title: "Identity smoke",
          alert: {
            kind: "identity_smoke",
            severity: "info",
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-1"
        onAction={vi.fn()}
        linkedWidgetSurface={linkedWidgetSurface}
        onOpenWidgetPanel={onOpenWidgetPanel}
        timestamp="2026-04-14T05:20:00Z"
      />,
    );

    await user.click(screen.getByTestId("ax-widget-card"));
    expect(onOpenWidgetPanel).toHaveBeenCalledWith({
      messageId: "message-1",
      surface: linkedWidgetSurface,
      timestamp: "2026-04-14T05:20:00Z",
    });
    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Kind")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /details/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("ax-card-open-widget")).not.toBeInTheDocument();
    expect(screen.queryByText("Kind")).not.toBeInTheDocument();
  });

  it("makes launchable task result cards whole-card clickable without double-triggering nested controls", async () => {
    const user = userEvent.setup();
    const onOpenWidgetPanel = vi.fn();
    const onForwardInit = vi.fn();
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "task-result-launchable",
        type: "result",
        version: 1,
        payload: {
          title: "Follow up with customer",
          summary: "Reminder copied from the task result card.",
          tool_name: "tasks",
          resource_uri: "ui://tasks/detail/task-1",
          delivery: "task_notification",
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-task-result-launch"
        onAction={vi.fn()}
        linkedWidgetSurface={linkedWidgetSurface}
        onOpenWidgetPanel={onOpenWidgetPanel}
        onForwardInit={onForwardInit}
      />,
    );

    const card = screen.getByTestId("ax-widget-card");
    expect(screen.getByTestId("ax-card-open-surface")).toHaveAccessibleName(
      "Open Task Detail",
    );
    expect(screen.getByText("Task")).toBeInTheDocument();
    expect(screen.queryByTestId("ax-card-open-widget")).not.toBeInTheDocument();

    await user.click(card);
    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(1);

    await user.tab();
    expect(screen.getByTestId("ax-card-open-surface")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(2);

    await user.click(screen.getByTestId("ax-card-share"));
    expect(onForwardInit).toHaveBeenCalledTimes(1);
    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn(() => true),
    });
    await user.click(screen.getByTestId("ax-card-copy"));
    expect(onOpenWidgetPanel).toHaveBeenCalledTimes(2);
  });

  it("preserves text selection on launchable cards instead of opening the widget", async () => {
    const user = userEvent.setup();
    const onOpenWidgetPanel = vi.fn();
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "task-result-selectable",
        type: "result",
        version: 1,
        payload: {
          title: "Copy this reminder text",
          summary: "Users may select this text without opening task detail.",
          tool_name: "tasks",
          resource_uri: "ui://tasks/detail/task-2",
        },
      },
    ];

    const getSelection = vi.spyOn(window, "getSelection").mockReturnValue({
      isCollapsed: false,
      toString: () => "Copy this reminder text",
    } as Selection);

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-task-result-select"
        onAction={vi.fn()}
        linkedWidgetSurface={linkedWidgetSurface}
        onOpenWidgetPanel={onOpenWidgetPanel}
      />,
    );

    await user.click(screen.getByTestId("ax-widget-card"));
    expect(onOpenWidgetPanel).not.toHaveBeenCalled();
    getSelection.mockRestore();
  });

  it("fires onForwardInit with the full card (task 48ae545f — Reply-pattern share)", async () => {
    const user = userEvent.setup();
    const onForwardInit = vi.fn();
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-forward-1",
        type: "alert",
        version: 1,
        payload: {
          title: "Task reminder",
          summary: "Review this.",
          alert: {
            kind: "task_reminder",
            severity: "info",
            task: {
              id: "task-fwd-1",
              title: "Review the thing",
              status: "in_progress",
            },
          },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-fwd"
        onAction={vi.fn()}
        onForwardInit={onForwardInit}
      />,
    );

    await user.click(screen.getByTestId("ax-card-share"));

    expect(onForwardInit).toHaveBeenCalledTimes(1);
    const callArg = onForwardInit.mock.calls[0][0];
    expect(callArg.cardId).toBe("alert-forward-1");
    expect(callArg.messageId).toBe("message-fwd");
    expect(callArg.card.card_id).toBe("alert-forward-1");
    // Per madtank msg 520a2269 — no inline strip; the composer bar drives
    // target selection via the existing @mention autocomplete.
    expect(screen.queryByTestId("ax-card-share-strip")).not.toBeInTheDocument();
  });

  it("keeps mobile card actions in the header with accessible touch targets", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "mobile-layout-card",
        type: "alert",
        version: 1,
        payload: {
          title: "Mobile Activity Stream card cleanup",
          summary: "Forward/share controls should not create a dead-space row.",
          alert: { kind: "task_reminder", severity: "info" },
        },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="mobile-message"
        onAction={vi.fn()}
        onForwardInit={vi.fn()}
      />,
    );

    const card = screen.getByTestId("ax-widget-card");
    const actions = screen.getByTestId("ax-card-actions");
    const share = screen.getByTestId("ax-card-share");

    expect(card.className).toContain("px-3");
    expect(card.className).toContain("py-3");
    expect(actions.className).toContain("items-center");
    expect(actions.className).not.toContain("flex-wrap");
    expect(share.className).toContain("h-10");
    expect(share.className).toContain("w-10");
    expect(share.className).toContain("sm:h-7");
    expect(share).toHaveAccessibleName("Share");
  });

  it("hides the Share button when no onForwardInit handler is provided", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-no-handler",
        type: "alert",
        version: 1,
        payload: {
          title: "T",
          summary: "s",
          alert: { kind: "task_reminder", severity: "info" },
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-nh"
        onAction={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("ax-card-share")).not.toBeInTheDocument();
  });

  it("does not show a Share button on agent cards", () => {
    // v2 contract — every card type supports Share EXCEPT agent cards.
    // Sharing an agent reads as "introduce them"; use @mention in the
    // composer instead. Open still navigates to the agent profile.
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "agent-card-1",
        type: "agent",
        version: 1,
        payload: { title: "@nova", agent_id: "a1", handle: "nova" },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-agent"
        onAction={vi.fn()}
        onForwardInit={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("ax-card-share")).not.toBeInTheDocument();
  });

  it("renders Notice badge + label for alert.kind=notice", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-notice",
        type: "alert",
        version: 1,
        payload: {
          title: "Task closed",
          summary: "Something completed.",
          alert: { kind: "notice", severity: "info" },
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-notice"
        onAction={vi.fn()}
      />,
    );
    // v2: alert.kind=notice flips the badge label from "Alert" → "Notice".
    expect(screen.getByText("Notice")).toBeInTheDocument();
    expect(screen.queryByText("Alert")).not.toBeInTheDocument();
  });

  it("renders Alert badge with security-coded tint when alert.security_sensitive=true", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "alert-security",
        type: "alert",
        version: 1,
        payload: {
          title: "Token misuse detected",
          summary: "Rejected.",
          alert: {
            kind: "token_misuse",
            severity: "critical",
            security_sensitive: true,
          },
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-security"
        onAction={vi.fn()}
      />,
    );
    // Label is still "Alert" for security_sensitive; icon and tint change
    // (ShieldAlert + rose) — this smoke check asserts the label stays Alert,
    // which distinguishes it from a Notice.
    expect(screen.getByText("Alert")).toBeInTheDocument();
    expect(screen.queryByText("Notice")).not.toBeInTheDocument();
  });

  it("renders fallback actions for card-only confirmations", async () => {
    const onAction = vi.fn();
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "confirm-1",
        type: "confirmation",
        version: 1,
        payload: {
          title: "Approve?",
          summary: "Choose.",
          action_id: "a1",
          confirm_label: "Approve",
          cancel_label: "Deny",
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-confirm"
        onAction={onAction}
      />,
    );
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("Review").closest("div")?.className).not.toContain(
      "bg-orange-100",
    );
    expect(screen.getByText("Review").closest("div")?.className).toContain(
      "bg-orange-400/12",
    );
    expect(screen.queryByText("Confirm")).not.toBeInTheDocument();
    expect(screen.queryByText("Confirmation")).not.toBeInTheDocument();
    expect(
      screen.getByTestId("ax-card-confirm-approve").className,
    ).not.toContain("bg-white");
    expect(screen.getByTestId("ax-card-confirm-approve").className).toContain(
      "bg-cyan-400/12",
    );
    await userEvent.click(screen.getByTestId("ax-card-confirm-approve"));
    expect(onAction).toHaveBeenCalledWith({
      actionId: "a1",
      cardId: "confirm-1",
      choiceId: "confirm",
    });
  });

  it("does not render action buttons for preview-only review cards", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "confirm-preview",
        type: "confirmation",
        version: 1,
        payload: {
          title: "Review release candidate",
          summary: "This is signal only until a real HITL action is wired.",
          action_id: "fake-approval",
          action_enabled: false,
          status: "needs_review",
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-confirm-preview"
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("needs_review")).toBeInTheDocument();
    expect(screen.queryByTestId("ax-card-confirm-approve")).toBeNull();
    expect(screen.queryByTestId("ax-card-confirm-deny")).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("shows a Share button on result and task cards (v2 expansion)", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "result-card-v2",
        type: "result",
        version: 1,
        payload: { title: "Some result" },
      },
      {
        card_id: "task-card-v2",
        type: "task",
        version: 1,
        payload: {
          title: "A task",
          task_id: "t1",
          status: "open",
          priority: "high",
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="message-v2-share"
        onAction={vi.fn()}
        onForwardInit={vi.fn()}
      />,
    );
    const shareButtons = screen.getAllByTestId("ax-card-share");
    expect(shareButtons).toHaveLength(2);
  });

  it("keeps confirmation actions inside the linked widget", async () => {
    const onAction = vi.fn();
    const onOpenWidgetPanel = vi.fn();
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "confirm-approve",
        type: "confirmation",
        version: 1,
        payload: {
          title: "Approve agent creation",
          summary: "HITL request.",
          action_id: "approval-1",
          confirm_label: "Approve",
          cancel_label: "Deny",
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-confirm-buttons"
        onAction={onAction}
        linkedWidgetSurface={linkedWidgetSurface}
        onOpenWidgetPanel={onOpenWidgetPanel}
      />,
    );

    expect(screen.queryByTestId("ax-card-confirm-approve")).toBeNull();
    expect(screen.queryByTestId("ax-card-confirm-deny")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Deny" }),
    ).not.toBeInTheDocument();

    expect(screen.queryByTestId("ax-card-open-widget")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("ax-widget-card"));

    expect(onOpenWidgetPanel).toHaveBeenCalledWith({
      messageId: "m-confirm-buttons",
      surface: linkedWidgetSurface,
      timestamp: undefined,
    });
    expect(onAction).not.toHaveBeenCalled();
  });

  it("renders Type/Size/Storage chips on context cards", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "ctx-chips",
        type: "context",
        version: 1,
        payload: {
          title: "spec.md",
          content_type: "text/markdown",
          size_bytes: 8600,
          storage: "vault",
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-ctx-chips"
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Type:")).toBeInTheDocument();
    expect(screen.getByText("Markdown")).toBeInTheDocument();
    expect(screen.getByText("Size:")).toBeInTheDocument();
    expect(screen.getByText("8.4 KB")).toBeInTheDocument();
    expect(screen.getByText("Storage:")).toBeInTheDocument();
    expect(screen.getByText("Vault")).toBeInTheDocument();
  });

  it("renders Status + Lane chips on agent cards", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "agent-chips",
        type: "agent",
        version: 1,
        payload: {
          title: "@frontend_sentinel",
          summary: "Owns ax-frontend.",
          status: "active",
          lane: "frontend",
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-agent-chips"
        onAction={vi.fn()}
      />,
    );
    // Status also appears in SignalContextStrip; accept multiple.
    expect(screen.getAllByText("Status:").length).toBeGreaterThan(0);
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Lane:")).toBeInTheDocument();
    expect(screen.getByText("Frontend")).toBeInTheDocument();
  });

  it("renders Agents + Tasks open chips on space result cards", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "space-chips",
        type: "result",
        version: 1,
        payload: {
          title: "Team Hub",
          summary: "Primary space.",
          tool_name: "spaces",
          agents_count: 8,
          tasks_open: 12,
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-space-chips"
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Agents:")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("Tasks open:")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("renders Status chip alongside Priority/Assignee on task cards", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "task-status",
        type: "task",
        version: 1,
        payload: {
          title: "Do the thing",
          summary: "Short.",
          task_id: "t-abc",
          priority: "high",
          status: "in_progress",
          assignee: { name: "orion" },
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-task-status"
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Priority")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("Assignee")).toBeInTheDocument();
    expect(screen.getByText("orion")).toBeInTheDocument();
    const taskBadge = screen.getByText("Task").closest("div");
    expect(taskBadge?.className).toContain("bg-slate-950/70");
    expect(taskBadge?.className).toContain("text-slate-300");
    expect(taskBadge?.className).not.toContain("bg-cyan");
    // Status appears in both SignalContextStrip (chip strip above) and the
    // new TaskCardBody chip row — both are valid; lock in "at least one".
    expect(screen.getAllByText("Status").length).toBeGreaterThan(0);
    expect(screen.getAllByText("in progress").length).toBeGreaterThan(0);
  });

  it("keeps seeded activity badge chrome dark-safe without light-fill fallbacks", () => {
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "seed-task",
        type: "task",
        version: 1,
        payload: { title: "Task", status: "open" },
      },
      {
        card_id: "seed-agent",
        type: "agent",
        version: 1,
        payload: { title: "Agent", status: "working" },
      },
      {
        card_id: "seed-reminder",
        type: "alert",
        version: 1,
        payload: {
          title: "Reminder",
          alert: { kind: "task_reminder", severity: "warn" },
        },
      },
      {
        card_id: "seed-review",
        type: "confirmation",
        version: 1,
        payload: {
          title: "Review",
          status: "needs_review",
          action_enabled: false,
        },
      },
      {
        card_id: "seed-result",
        type: "result",
        version: 1,
        payload: { title: "Result" },
      },
    ];

    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-seeded-badges"
        onAction={vi.fn()}
      />,
    );

    for (const label of ["Task", "Agent", "Reminder", "Review", "Result"]) {
      const badge =
        screen
          .getAllByText(label)
          .map((node) => node.closest("div"))
          .find((node) => node?.className.includes("rounded-full")) || null;
      // Dark-mode variants must stay subdued: no bright fills or dark text.
      // Light-mode classes (no `dark:` prefix) are allowed and required.
      expect(badge?.className).not.toMatch(
        /\bdark:bg-(?:white|gray-100|slate-100|cyan-100|sky-100|amber-100|orange-100|lime-100|emerald-100|violet-100|rose-100)\b/,
      );
      expect(badge?.className).not.toMatch(
        /\bdark:text-(?:gray|slate|cyan|sky|amber|orange|lime|emerald|violet|rose)-[78]00\b/,
      );
    }
  });

  it.each([
    {
      label: "Context",
      card: {
        card_id: "context-theme-badge",
        type: "context",
        version: 1,
        payload: {
          title: "Context Explorer - recent upload",
          summary: "Zip file pinned 2026-05-18",
        },
      } satisfies SpaceAgentCardEnvelope,
      includes: [
        "bg-violet-500/10",
        "text-violet-800",
        "dark:bg-violet-400/12",
        "dark:text-violet-100",
      ],
    },
    {
      label: "Agent",
      card: {
        card_id: "agent-theme-badge",
        type: "agent",
        version: 1,
        payload: {
          title: "Code Weaver",
          summary: "Ready for UI polish.",
        },
      } satisfies SpaceAgentCardEnvelope,
      includes: [
        "bg-amber-500/10",
        "text-amber-800",
        "dark:bg-amber-400/12",
        "dark:text-amber-100",
      ],
    },
    {
      label: "Alert",
      card: {
        card_id: "alert-theme-badge",
        type: "alert",
        version: 1,
        payload: {
          title: "Supervisor nudge",
          summary: "Review queue triage.",
          alert: { kind: "health", severity: "info" },
        },
      } satisfies SpaceAgentCardEnvelope,
      includes: [
        "bg-sky-500/10",
        "text-sky-800",
        "dark:bg-sky-400/12",
        "dark:text-sky-100",
      ],
    },
    {
      label: "Result",
      card: {
        card_id: "result-theme-badge",
        type: "result",
        version: 1,
        payload: {
          title: "Task reminder",
          summary: "Reminder signal emitted.",
        },
      } satisfies SpaceAgentCardEnvelope,
      includes: [
        "bg-emerald-100",
        "text-emerald-700",
        "dark:bg-emerald-950/55",
        "dark:text-emerald-100/85",
      ],
    },
  ])(
    "keeps the $label badge readable in light mode and subdued in dark mode",
    ({ card, label, includes }) => {
      render(
        <AxMessageWidgets
          cards={[card]}
          messageId={`m-${card.card_id}`}
          onAction={vi.fn()}
        />,
      );

      const badge = screen.getByText(
        (_, element) =>
          element?.textContent === label &&
          element.className.includes("rounded-full"),
      );

      for (const className of includes) {
        expect(badge.className).toContain(className);
      }
    },
  );

  it("uses task snapshot title on notice alerts with source_task_id (v2)", () => {
    // Extended from task_reminder — a notice alert that carries
    // source_task_id should also render with the task title/summary so
    // task_completed style notices read correctly.
    const cards: SpaceAgentCardEnvelope[] = [
      {
        card_id: "notice-task-completed",
        type: "alert",
        version: 1,
        payload: {
          title: "ignored fallback title",
          summary: "ignored fallback summary",
          alert: {
            kind: "notice",
            severity: "info",
            source_task_id: "task-closed-1",
            task: {
              title: "Close out the mobile card fix",
              summary: "Task body copy used as the summary.",
            },
          },
        },
      },
    ];
    render(
      <AxMessageWidgets
        cards={cards}
        messageId="m-notice-task"
        onAction={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Close out the mobile card fix"),
    ).toBeInTheDocument();
    expect(screen.getByText("Notice")).toBeInTheDocument();
  });
  it("shows Reply for agent cards instead of Share", async () => {
    const user = userEvent.setup();
    const onReplyInit = vi.fn();
    render(
      <AxMessageWidgets
        cards={[
          {
            card_id: "agent-card-1",
            type: "agent",
            version: 1,
            payload: { title: "Code Weaver", summary: "Best for UI polish." },
          },
        ]}
        messageId="message-agent-card"
        onAction={vi.fn()}
        onForwardInit={vi.fn()}
        onReplyInit={onReplyInit}
      />,
    );

    expect(screen.queryByTestId("ax-card-share")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("ax-card-reply"));
    expect(onReplyInit).toHaveBeenCalledWith({
      cardId: "agent-card-1",
      card: expect.objectContaining({ card_id: "agent-card-1", type: "agent" }),
      messageId: "message-agent-card",
    });
  });
});
