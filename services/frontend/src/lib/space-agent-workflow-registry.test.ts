import { describe, expect, it } from "vitest";
import {
  resolveSpaceAgentWorkflowIds,
  resolveSpaceAgentWorkflowTitle,
} from "./space-agent-workflow-registry";

describe("space agent workflow registry", () => {
  it("maps task board widgets to the task list workflow", () => {
    expect(
      resolveSpaceAgentWorkflowIds({
        tool_name: "tasks.list",
        resource_uri: "ui://task-board@2",
      }),
    ).toEqual(["tasks.list"]);
  });

  it("maps agent create widgets to a create workflow", () => {
    expect(
      resolveSpaceAgentWorkflowIds({
        tool_name: "agents.create",
        resource_uri: "ui://agent-editor@1",
      }),
    ).toEqual(["agents.create"]);
  });

  it("uses tool_action to classify agent draft widgets before generic resource matches", () => {
    expect(
      resolveSpaceAgentWorkflowIds({
        tool_name: "agents",
        tool_action: "create_draft",
        resource_uri: "ui://agents/dashboard",
      }),
    ).toEqual(["agents.create"]);
  });

  it("uses tool_action to distinguish task detail from task list widgets", () => {
    expect(
      resolveSpaceAgentWorkflowIds({
        tool_name: "tasks",
        tool_action: "get",
        resource_uri: "ui://task-detail@2",
      }),
    ).toEqual(["tasks.get"]);
  });

  it("uses tool_action to classify space draft widgets", () => {
    expect(
      resolveSpaceAgentWorkflowIds({
        tool_name: "spaces",
        tool_action: "create_draft",
        resource_uri: "ui://space-navigator@2",
      }),
    ).toEqual(["spaces.create"]);
  });

  it("resolves a human workflow title for transcript panel headers", () => {
    expect(
      resolveSpaceAgentWorkflowTitle({
        title: "Request processed",
        tool_name: "agents",
        tool_action: "create_draft",
        resource_uri: "ui://agents/dashboard",
      }),
    ).toBe("Create Agent");
  });

  it("maps message timeline widgets to the messages workflow", () => {
    expect(
      resolveSpaceAgentWorkflowIds({
        tool_name: "messages.check",
        resource_uri: "ui://message-timeline@1",
      }),
    ).toEqual(["messages.check"]);
  });
});
