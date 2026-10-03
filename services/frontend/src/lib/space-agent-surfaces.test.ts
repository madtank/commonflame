import { describe, expect, it } from "vitest";
import {
  getSurfaceCount,
  normalizeSpaceAgentSurfaces,
} from "./space-agent-surfaces";

describe("space agent surface normalization", () => {
  it("normalizes cards and widget from mixed metadata locations into one surface list", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        metadata: {
          ui: {
            cards: [
              {
                card_id: "card-1",
                type: "task",
                version: 1,
                payload: { title: "Task card" },
              },
            ],
          },
        },
        message_metadata: {
          ui: {
            widget: {
              tool_name: "agents",
              resource_uri: "ui://agent-dashboard@1",
              lifecycle: "complete",
            },
          },
        },
      },
      "message-1",
    );

    expect(surfaces).toHaveLength(2);
    expect(surfaces[0]).toMatchObject({
      sourceMessageId: "message-1",
      kind: "cards",
      placement: "card",
      status: "ready",
    });
    expect(surfaces[1]).toMatchObject({
      sourceMessageId: "message-1",
      policyId: "agents",
      workflowIds: ["agents.list"],
      kind: "widget",
      placement: "panel",
      status: "ready",
    });
    expect(getSurfaceCount(surfaces)).toBe(2);
  });

  it("maps fullscreen widgets to panel placement", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        ui: {
          widget: {
            tool_name: "search",
            resource_uri: "ui://search-results@2",
            display_mode: "fullscreen",
            lifecycle: "working",
          },
        },
      },
      "message-2",
    );

    expect(surfaces).toEqual([
      expect.objectContaining({
        sourceMessageId: "message-2",
        policyId: "search",
        workflowIds: [],
        kind: "widget",
        placement: "panel",
        status: "pending",
      }),
    ]);
  });

  it("expands widgets[] array into multiple surfaces", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        metadata: {
          ui: {
            widget: {
              kind: "mcp_app",
              tool_name: "tasks",
              tool_call_id: "call-1",
              resource_uri: "ui://tasks/board",
              lifecycle: "complete",
              title: "Request processed",
              widgets: [
                {
                  kind: "mcp_app",
                  tool_name: "tasks",
                  tool_call_id: "call-1",
                  resource_uri: "ui://tasks/board",
                  display_mode: "inline",
                },
                {
                  kind: "mcp_app",
                  tool_name: "context",
                  tool_call_id: "call-2",
                  resource_uri: "ui://context/explorer",
                  display_mode: "inline",
                },
              ],
            },
          },
        },
      },
      "msg-multi",
    );

    expect(surfaces).toHaveLength(2);
    expect(surfaces[0]).toMatchObject({
      kind: "widget",
      policyId: "tasks",
      status: "ready",
    });
    expect(
      surfaces[0].kind === "widget" && surfaces[0].widget.tool_call_id,
    ).toBe("call-1");
    expect(surfaces[1]).toMatchObject({
      kind: "widget",
      policyId: "context",
      status: "ready",
    });
    expect(
      surfaces[1].kind === "widget" && surfaces[1].widget.tool_call_id,
    ).toBe("call-2");
    expect(getSurfaceCount(surfaces)).toBe(2);
  });

  it("inherits lifecycle from top-level widget when individual entry lacks it", () => {
    const surfaces = normalizeSpaceAgentSurfaces({
      metadata: {
        ui: {
          widget: {
            tool_name: "tasks",
            lifecycle: "complete",
            widgets: [
              {
                tool_name: "search",
                tool_call_id: "s-1",
                resource_uri: "ui://search-results",
              },
            ],
          },
        },
      },
    });

    expect(surfaces).toHaveLength(1);
    expect(surfaces[0]).toMatchObject({
      kind: "widget",
      policyId: "search",
      status: "ready",
    });
  });

  it("normalizes arguments to tool_input in widgets[] entries", () => {
    const surfaces = normalizeSpaceAgentSurfaces({
      metadata: {
        ui: {
          widget: {
            tool_name: "context",
            lifecycle: "complete",
            widgets: [
              {
                tool_name: "context",
                tool_call_id: "c-1",
                resource_uri: "ui://context/explorer",
                arguments: { key: "test_key", action: "set" },
              },
            ],
          },
        },
      },
    });

    expect(surfaces).toHaveLength(1);
    const w = surfaces[0];
    expect(w.kind === "widget" && w.widget.tool_input).toEqual({
      key: "test_key",
      action: "set",
    });
  });

  it("inherits top-level initial_data onto the primary widgets[] entry", () => {
    const surfaces = normalizeSpaceAgentSurfaces({
      metadata: {
        ui: {
          widget: {
            tool_name: "agents",
            tool_call_id: "agent-create-1",
            lifecycle: "complete",
            initial_data: {
              kind: "agent_collection",
              data: {
                scope: "create",
                draft: { draft_id: "draft-1", name: "review_bot" },
              },
            },
            widgets: [
              {
                tool_name: "agents",
                tool_call_id: "agent-create-1",
                resource_uri: "ui://agent-dashboard/create",
              },
            ],
          },
        },
      },
    });

    expect(surfaces).toHaveLength(1);
    const surface = surfaces[0];
    expect(surface.kind === "widget" && surface.widget.tool_result).toEqual({
      kind: "agent_collection",
      data: {
        scope: "create",
        draft: { draft_id: "draft-1", name: "review_bot" },
      },
    });
  });

  it("normalizes legacy create_draft initial_data into the HITL draft shape", () => {
    const surfaces = normalizeSpaceAgentSurfaces({
      metadata: {
        ui: {
          widget: {
            tool_name: "agents",
            tool_action: "create_draft",
            resource_uri: "ui://agents/dashboard",
            lifecycle: "approval_required",
            initial_data: {
              kind: "agents",
              version: 1,
              action: "create_draft",
              state: "approval_required",
              draft: {
                draft_id: "draft-legacy",
                name: "review_bot",
                description: "Review me before creating the agent.",
              },
            },
          },
        },
      },
    });

    expect(surfaces).toHaveLength(1);
    const surface = surfaces[0];
    expect(surface.kind === "widget" && surface.widget.tool_result).toEqual({
      kind: "agent_collection",
      version: 1,
      state: "approval_required",
      data: {
        scope: "create",
        draft: {
          draft_id: "draft-legacy",
          name: "review_bot",
          description: "Review me before creating the agent.",
        },
        required_fields: ["name", "description", "agent_mode"],
        hint: "Review this draft before creating the agent.",
      },
    });
  });

  it("inherits top-level tool_call_id onto sparse primary widgets[] entries", () => {
    const toolCallId = "9f4798b4-4bc2-4ff0-8f18-9c8b1a8c8a65";
    const surfaces = normalizeSpaceAgentSurfaces({
      metadata: {
        ui: {
          widget: {
            tool_name: "agents",
            tool_call_id: toolCallId,
            resource_uri: "ui://agents/dashboard",
            lifecycle: "complete",
            initial_data: {
              kind: "agent_collection",
              data: {
                scope: "create",
                draft: { draft_id: "draft-2", name: "orbit_builder" },
              },
            },
            widgets: [
              {
                tool_name: "agents",
              },
            ],
          },
        },
      },
    });

    expect(surfaces).toHaveLength(1);
    const surface = surfaces[0];
    expect(surface.kind === "widget" && surface.id).toBe(toolCallId);
    expect(surface.kind === "widget" && surface.widget.tool_call_id).toBe(
      toolCallId,
    );
    expect(surface.kind === "widget" && surface.widget.resource_uri).toBe(
      "ui://agents/dashboard",
    );
    expect(surface.kind === "widget" && surface.widget.tool_result).toEqual({
      kind: "agent_collection",
      data: {
        scope: "create",
        draft: { draft_id: "draft-2", name: "orbit_builder" },
      },
    });
  });

  it("skips disabled widgets in the array but keeps enabled ones", () => {
    const surfaces = normalizeSpaceAgentSurfaces({
      metadata: {
        ui: {
          widget: {
            tool_name: "tasks",
            lifecycle: "complete",
            widgets: [
              {
                tool_name: "tasks",
                tool_call_id: "t-1",
                resource_uri: "ui://tasks/board",
                display_mode: "none",
              },
              {
                tool_name: "context",
                tool_call_id: "c-1",
                resource_uri: "ui://context/explorer",
              },
            ],
          },
        },
      },
    });

    expect(surfaces).toHaveLength(1);
    expect(surfaces[0]).toMatchObject({
      kind: "widget",
      policyId: "context",
    });
  });

  it("normalizes message timeline widgets when policy allows them", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        ui: {
          widget: {
            tool_name: "messages.check",
            resource_uri: "ui://message-timeline@1",
            display_mode: "inline",
            lifecycle: "complete",
          },
        },
      },
      "message-3",
    );

    expect(surfaces).toEqual([
      expect.objectContaining({
        sourceMessageId: "message-3",
        policyId: "messages",
        workflowIds: ["messages.check"],
        kind: "widget",
        placement: "panel",
        status: "ready",
      }),
    ]);
  });

  it("synthesizes alert cards from message alert metadata with sender context", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        id: "msg-alert-1",
        sender_type: "agent",
        agent_id: "agent-orion",
        display_name: "orion",
        created_at: "2026-04-14T05:20:00Z",
        metadata: {
          alert: {
            kind: "supervisor_nudge",
            source: "ax-nudge-review-triage",
            origin: "systemd-timer",
            cycle: { current: 1, max: 5 },
            cadence: "15m",
            completion_promise: "REVIEW QUEUE CLEARED",
          },
        },
      },
      "msg-alert-1",
    );

    expect(surfaces).toHaveLength(1);
    expect(surfaces[0]).toMatchObject({
      kind: "cards",
      placement: "card",
      status: "ready",
      cards: [
        {
          type: "alert",
          payload: {
            sender_label: "orion",
            sender_type: "agent",
            agent_id: "agent-orion",
            intent: "alert",
            evidence_mode: "none",
            alert: {
              kind: "supervisor_nudge",
              source: "ax-nudge-review-triage",
            },
          },
        },
      ],
    });
  });

  it("collapses alert-owned context widget evidence into one alert card", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        id: "msg-alert-card-1",
        display_name: "ChatGPT",
        metadata: {
          alert: {
            kind: "context_artifact",
            source: "axctl_apps_signal",
            context_key: "upload:qa-smoke.png",
            tool_call_id: "tool-call-1",
          },
          ui: {
            cards: [
              {
                card_id: "context-card",
                type: "context",
                version: 1,
                payload: {
                  title: "Context diagram",
                  summary: "QA smoke screenshot",
                  source: "axctl_apps_signal",
                  context_key: "upload:qa-smoke.png",
                  tool_call_id: "tool-call-1",
                  resource_uri: "ui://context/explorer",
                  tool_name: "context",
                },
              },
            ],
          },
        },
      },
      "msg-alert-card-1",
    );

    expect(surfaces[0]).toMatchObject({
      kind: "cards",
      cards: [
        {
          type: "alert",
          payload: {
            title: "Context Artifact",
            summary: "QA smoke screenshot",
            sender_label: "ChatGPT",
            context_key: "upload:qa-smoke.png",
            resource_uri: "ui://context/explorer",
            tool_name: "context",
            intent: "alert",
            evidence_mode: "preview_widget",
            alert: {
              kind: "context_artifact",
              context_key: "upload:qa-smoke.png",
            },
          },
        },
      ],
    });
    expect(surfaces[0].kind === "cards" ? surfaces[0].cards : []).toHaveLength(
      1,
    );
  });

  it("does not copy task context from duplicated context evidence cards", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        id: "msg-alert-context-duplicate-1",
        display_name: "ChatGPT",
        metadata: {
          alert: {
            kind: "context_artifact",
            title: "Context artifact",
            summary: "Opened the uploaded context in the widget.",
            source: "axctl_apps_signal",
            context_key: "upload:spec-notes.md",
            tool_call_id: "tool-call-context-1",
          },
          ui: {
            cards: [
              {
                card_id: "context-card-duplicate",
                type: "context",
                version: 1,
                payload: {
                  title: "Spec notes",
                  summary: "Design notes for the widget preview.",
                  source: "axctl_apps_signal",
                  context_key: "upload:spec-notes.md",
                  tool_call_id: "tool-call-context-1",
                  resource_uri: "ui://context/explorer/upload:spec-notes.md",
                  tool_name: "context",
                },
              },
            ],
          },
        },
      },
      "msg-alert-context-duplicate-1",
    );

    expect(surfaces[0]).toMatchObject({
      kind: "cards",
      cards: [
        {
          type: "alert",
          payload: {
            title: "Context artifact",
            summary: "Opened the uploaded context in the widget.",
            context_key: "upload:spec-notes.md",
            tool_call_id: "tool-call-context-1",
            resource_uri: "ui://context/explorer/upload:spec-notes.md",
            tool_name: "context",
          },
        },
      ],
    });
    const alertCard =
      surfaces[0].kind === "cards" ? surfaces[0].cards[0] : null;
    expect(alertCard?.type).toBe("alert");
    expect(alertCard?.payload).not.toHaveProperty("task_title");
    expect(alertCard?.payload).not.toHaveProperty("task_summary");
    expect(alertCard?.payload).not.toHaveProperty("task_status");
    expect(alertCard?.payload).not.toHaveProperty("task_priority");
  });

  it("preserves task-detail evidence on reminder alerts after deduping the task card", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        id: "msg-task-reminder-1",
        display_name: "dogfood_reminder",
        created_at: "2026-04-16T18:30:00Z",
        metadata: {
          alert: {
            kind: "task_reminder",
            title: "Task reminder",
            summary: "Please review the dogfood task.",
            source: "dogfood_reminder_alert",
            task_id: "task-dogfood-1",
          },
          ui: {
            cards: [
              {
                card_id: "task-card-reminder",
                type: "task",
                version: 1,
                payload: {
                  title: "Reminder alert should open task detail widget",
                  summary: "Dogfood task reminder context.",
                  task_id: "task-dogfood-1",
                  status: "in_progress",
                  priority: "high",
                  assignee: {
                    name: "frontend_sentinel",
                  },
                  tool_call_id: "tool-call-task-1",
                  resource_uri: "ui://task-detail/task-dogfood-1",
                  tool_name: "tasks.get",
                },
              },
            ],
          },
        },
      },
      "msg-task-reminder-1",
    );

    expect(surfaces[0]).toMatchObject({
      kind: "cards",
      cards: [
        {
          type: "alert",
          payload: {
            title: "Task reminder",
            task_title: "Reminder alert should open task detail widget",
            task_id: "task-dogfood-1",
            task_status: "in_progress",
            task_priority: "high",
            assignee: {
              name: "frontend_sentinel",
            },
            tool_call_id: "tool-call-task-1",
            resource_uri: "ui://task-detail/task-dogfood-1",
            tool_name: "tasks.get",
          },
        },
      ],
    });
    expect(surfaces[0].kind === "cards" ? surfaces[0].cards : []).toHaveLength(
      1,
    );
  });

  it("dedupes generic result cards that duplicate alert signal metadata", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        id: "msg-alert-result-1",
        display_name: "ChatGPT",
        created_at: "2026-04-14T05:20:00Z",
        metadata: {
          alert: {
            kind: "qa_smoke",
            severity: "warn",
            source: "axctl_apps_signal",
            target_agent: "frontend_sentinel",
            response_required: true,
            attachments: [{ filename: "qa-smoke.png" }],
          },
          ui: {
            cards: [
              {
                card_id: "app-signal:tool-call-1",
                type: "result",
                version: 1,
                payload: {
                  title: "QA alert signal card",
                  summary: "Smoke test",
                  severity: "warn",
                  source: "axctl_apps_signal",
                },
              },
            ],
          },
        },
      },
      "msg-alert-result-1",
    );

    expect(surfaces[0]).toMatchObject({
      kind: "cards",
      cards: [
        {
          type: "alert",
          payload: {
            title: "Qa Smoke",
            sender_label: "ChatGPT",
            created_at: "2026-04-14T05:20:00Z",
            intent: "alert",
            evidence_mode: "preview",
            alert: {
              target_agent: "frontend_sentinel",
              response_required: true,
            },
          },
        },
      ],
    });
    expect(surfaces[0].kind === "cards" ? surfaces[0].cards : []).toHaveLength(
      1,
    );
  });

  it("adds sender context to ordinary cards without requiring alert metadata", () => {
    const surfaces = normalizeSpaceAgentSurfaces(
      {
        id: "msg-task-card-1",
        sender_type: "agent",
        agent_id: "agent-chatgpt",
        display_name: "ChatGPT",
        metadata: {
          ui: {
            cards: [
              {
                card_id: "task-card",
                type: "task",
                version: 1,
                payload: {
                  title: "Run smoke tests",
                  source: "axctl_tasks_create",
                },
              },
            ],
          },
        },
      },
      "msg-task-card-1",
    );

    expect(surfaces[0]).toMatchObject({
      kind: "cards",
      cards: [
        {
          card_id: "task-card",
          type: "task",
          payload: {
            title: "Run smoke tests",
            source: "axctl_tasks_create",
            intent: "signal",
            evidence_mode: "none",
            sender_label: "ChatGPT",
            sender_type: "agent",
            agent_id: "agent-chatgpt",
          },
        },
      ],
    });
  });

  it("keeps context receipts compact when no renderable evidence exists", () => {
    const surfaces = normalizeSpaceAgentSurfaces({
      id: "msg-context-receipt-1",
      metadata: {
        ui: {
          cards: [
            {
              card_id: "context-receipt",
              type: "context",
              version: 1,
              payload: {
                title: "Context uploaded",
                summary: "Stored in context.",
              },
            },
          ],
        },
      },
    });

    expect(surfaces[0]).toMatchObject({
      kind: "cards",
      cards: [
        {
          type: "context",
          payload: {
            intent: "context",
            evidence_mode: "none",
          },
        },
      ],
    });
  });

  it("marks renderable app-backed evidence as preview_widget", () => {
    const surfaces = normalizeSpaceAgentSurfaces({
      id: "msg-preview-widget-1",
      metadata: {
        ui: {
          cards: [
            {
              card_id: "qa-smoke-card",
              type: "result",
              version: 1,
              payload: {
                title: "QA smoke screenshot",
                resource_uri: "ui://context/explorer",
                body_markdown: "![QA smoke](attachment://qa-smoke.png)",
              },
            },
          ],
        },
      },
    });

    expect(surfaces[0]).toMatchObject({
      kind: "cards",
      cards: [
        {
          type: "result",
          payload: {
            intent: "signal",
            evidence_mode: "preview_widget",
          },
        },
      ],
    });
  });
});
