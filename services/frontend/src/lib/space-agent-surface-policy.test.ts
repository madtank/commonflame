import { describe, expect, it } from "vitest";
import {
  getSpaceAgentSurfacePolicyId,
  resolveSpaceAgentSurfacePolicy,
} from "./space-agent-surface-policy";

describe("space agent surface policy", () => {
  it("routes known MCP task widgets to the app panel", () => {
    const policy = resolveSpaceAgentSurfacePolicy({
      tool_name: "tasks.list",
      resource_uri: "ui://task-board@2",
      display_mode: "inline",
    });

    expect(policy).toMatchObject({
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
    });
    expect(
      getSpaceAgentSurfacePolicyId({
        tool_name: "tasks.list",
        resource_uri: "ui://task-board@2",
      }),
    ).toBe("tasks");
  });

  it("routes message timeline widgets to the app panel", () => {
    const policy = resolveSpaceAgentSurfacePolicy({
      tool_name: "messages.check",
      resource_uri: "ui://message-timeline@1",
      display_mode: "inline",
    });

    expect(policy).toMatchObject({
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
    });
  });

  it("preserves panel placement when a surfaced widget requests fullscreen", () => {
    const policy = resolveSpaceAgentSurfacePolicy({
      tool_name: "agents.list",
      resource_uri: "ui://agent-dashboard@1",
      display_mode: "fullscreen",
    });

    expect(policy).toMatchObject({
      enabled: true,
      placement: "panel",
    });
  });

  it("routes unknown MCP widgets to the app panel by default", () => {
    const policy = resolveSpaceAgentSurfacePolicy({
      tool_name: "notifications.list",
      resource_uri: "ui://notification-center@1",
      display_mode: "inline",
    });

    expect(policy).toMatchObject({
      enabled: true,
      placement: "panel",
      allowFullscreen: true,
    });
  });

  it("hides a widget when its policy id is in the hidden set", () => {
    const hidden = new Set(["tasks"]);
    const policy = resolveSpaceAgentSurfacePolicy(
      { tool_name: "tasks.list", resource_uri: "ui://task-board@2" },
      hidden,
    );

    expect(policy).toMatchObject({
      enabled: false,
      placement: "none",
    });
  });

  it("shows a widget when hidden set does not include its policy id", () => {
    const hidden = new Set(["agents"]);
    const policy = resolveSpaceAgentSurfacePolicy(
      { tool_name: "tasks.list", resource_uri: "ui://task-board@2" },
      hidden,
    );

    expect(policy.enabled).toBe(true);
  });

  it("hides unknown widgets by tool_name when in hidden set", () => {
    const hidden = new Set(["notifications.list"]);
    const policy = resolveSpaceAgentSurfacePolicy(
      { tool_name: "notifications.list", resource_uri: "ui://notifications" },
      hidden,
    );

    expect(policy).toMatchObject({
      enabled: false,
      placement: "none",
    });
  });
});
