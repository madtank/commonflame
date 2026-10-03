import { describe, expect, it } from "vitest";
import {
  computeAgentPhaseTitle,
  getPendingResponseDisplay,
  type PendingResponseState,
} from "./pending-response";

function baseState(
  overrides: Partial<PendingResponseState> = {},
): PendingResponseState {
  return {
    sourceEntryId: "msg-1",
    targetHandle: "dev_sentinel",
    targetLabel: "@dev_sentinel",
    activeAgentLabel: null,
    statusLabel: "waiting",
    toolName: null,
    activity: null,
    toolCount: null,
    stepLabel: null,
    commandLabels: [],
    progress: null,
    reason: null,
    errorMessage: null,
    retryAfterSeconds: null,
    isKnownTarget: true,
    hasPresenceSignal: false,
    presenceStatus: null,
    ...overrides,
  };
}

describe("getPendingResponseDisplay", () => {
  it("shows the routed target while waiting for pickup", () => {
    expect(getPendingResponseDisplay(baseState())).toEqual({
      title: "Waiting for @dev_sentinel",
      detail: null,
      signals: [],
    });
  });

  it("does not surface transient offline availability detail", () => {
    expect(
      getPendingResponseDisplay(
        baseState({ hasPresenceSignal: true, presenceStatus: "offline" }),
      ),
    ).toEqual({
      title: "Waiting for @dev_sentinel",
      detail: null,
      signals: [],
    });
  });

  it("does not surface reroute detail as a layout-changing label", () => {
    expect(
      getPendingResponseDisplay(
        baseState({ activeAgentLabel: "aX", statusLabel: "processing" }),
      ),
    ).toEqual({
      title: "aX is working",
      detail: null,
      signals: [],
    });
  });

  it("keeps unknown-target pending display free of availability detail", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          sourceEntryId: "msg-4",
          targetHandle: "missing_agent",
          targetLabel: "@missing_agent",
          isKnownTarget: false,
        }),
      ),
    ).toEqual({
      title: "Waiting for @missing_agent",
      detail: null,
      signals: [],
    });
  });

  it("returns null when neither target nor active agent is known", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          sourceEntryId: null,
          targetHandle: null,
          targetLabel: "",
          statusLabel: "processing",
          isKnownTarget: false,
        }),
      ),
    ).toBeNull();
  });

  it("formats tool use with the acting agent label", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          activeAgentLabel: "@dev_sentinel",
          statusLabel: "tool_use",
          toolName: "tasks.list",
        }),
      ),
    ).toEqual({
      title: "@dev_sentinel is using tasks list...",
      detail: null,
      signals: [],
    });
  });

  it("prefers activity text when tool_call carries both tool and activity", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          activeAgentLabel: "@dev_sentinel",
          statusLabel: "tool_call",
          toolName: "shell",
          activity: "Running git status",
        }),
      ),
    ).toEqual({
      title: "@dev_sentinel: Running git status",
      detail: null,
      signals: [],
    });
  });

  it("includes tool counts, progress, and commands as inline signals", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          sourceEntryId: "msg-6",
          activeAgentLabel: "@dev_sentinel",
          statusLabel: "tool_use",
          toolName: "tasks.list",
          toolCount: 2,
          stepLabel: "Inspecting open tasks",
          commandLabels: ["git status", "npm run build"],
          progress: { current: 3, total: 7, unit: "files" },
        }),
      ),
    ).toEqual({
      title: "@dev_sentinel is using tasks list...",
      detail: null,
      signals: [
        "2 tools active",
        "3/7 files",
        "Inspecting open tasks",
        "git status",
        "npm run build",
      ],
    });
  });

  it("renders accepted as 'picked it up'", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          activeAgentLabel: "@dev_sentinel",
          statusLabel: "accepted",
        }),
      ),
    ).toEqual({
      title: "@dev_sentinel picked it up",
      detail: null,
      signals: [],
    });
  });

  it("renders queued with Gateway attribution when an active agent is known", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          activeAgentLabel: "@dev_sentinel",
          statusLabel: "queued",
        }),
      ),
    ).toEqual({
      title: "@dev_sentinel queued by Gateway",
      detail: null,
      signals: [],
    });
  });

  it("falls back to 'Queued for X' when the routed target hasn't picked up", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          statusLabel: "queued",
        }),
      ),
    ).toEqual({
      title: "Queued for @dev_sentinel",
      detail: null,
      signals: [],
    });
  });

  it("renders started with activity when provided", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          activeAgentLabel: "@dev_sentinel",
          statusLabel: "started",
          activity: "Warming up",
        }),
      ),
    ).toEqual({
      title: "@dev_sentinel: Warming up",
      detail: null,
      signals: [],
    });
  });

  it("renders rate_limited with retry-after hint when present", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          activeAgentLabel: "@dev_sentinel",
          statusLabel: "rate_limited",
          retryAfterSeconds: 45,
        }),
      ),
    ).toEqual({
      title: "@dev_sentinel rate limited, retrying in 45s",
      detail: null,
      signals: [],
    });
  });

  it("renders error with the message when provided", () => {
    expect(
      getPendingResponseDisplay(
        baseState({
          activeAgentLabel: "@dev_sentinel",
          statusLabel: "error",
          errorMessage: "Model call failed",
        }),
      ),
    ).toEqual({
      title: "@dev_sentinel hit an error: Model call failed",
      detail: null,
      signals: [],
    });
  });
});

describe("computeAgentPhaseTitle", () => {
  it("prefers activity text over generic tool label for tool_call", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "tool_call",
        toolName: "shell",
        activity: "Running git status",
      }),
    ).toBe("@cli-managed-bot: Running git status");
  });

  it("falls back to 'using <tool>' when no activity is provided", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "tool_call",
        toolName: "shell",
      }),
    ).toBe("@cli-managed-bot is using shell...");
  });

  it("uses 'picked it up' for accepted", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "accepted",
      }),
    ).toBe("@cli-managed-bot picked it up");
  });

  it("renders queued with gateway attribution when an active actor is present", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "queued",
        hasActiveActor: true,
      }),
    ).toBe("@cli-managed-bot queued by Gateway");
  });

  it("falls back to 'Queued for <routed>' when no active actor has claimed work", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "queued",
        routedLabel: "@cli-managed-bot",
        hasActiveActor: false,
      }),
    ).toBe("Queued for @cli-managed-bot");
  });

  it("includes retry-after for rate_limited", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "rate_limited",
        retryAfterSeconds: 45,
      }),
    ).toBe("@cli-managed-bot rate limited, retrying in 45s");
  });

  it("uses errorMessage ahead of reason for error phase", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "error",
        errorMessage: "Model call failed",
        reason: "upstream timeout",
      }),
    ).toBe("@cli-managed-bot hit an error: Model call failed");
  });

  it("renders generic 'is working' fallback for unknown phases", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "mystery-phase",
      }),
    ).toBe("@cli-managed-bot is mystery phase");
  });

  it("treats waiting as the pre-pickup fallback", () => {
    expect(
      computeAgentPhaseTitle({
        actorLabel: "@cli-managed-bot",
        status: "waiting",
        routedLabel: "@cli-managed-bot",
      }),
    ).toBe("Waiting for @cli-managed-bot");
  });
});
