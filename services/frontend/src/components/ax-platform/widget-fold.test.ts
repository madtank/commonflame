import { describe, expect, it } from "vitest";

import type { SpaceAgentWidgetDescriptor } from "@/lib/space-agent-api";
import {
  getCollapsedWidgetPreview,
  shouldDefaultCollapseWidget,
} from "@/components/ax-platform/widget-fold";

function makeWidget(
  overrides: Partial<SpaceAgentWidgetDescriptor>,
): SpaceAgentWidgetDescriptor {
  return {
    tool_name: "agents.list",
    resource_uri: "ui://agent-dashboard@1",
    display_mode: "inline",
    ...overrides,
  };
}

describe("widget fold behavior", () => {
  it("starts widgets expanded by default", () => {
    expect(
      shouldDefaultCollapseWidget(
        makeWidget({
          tool_name: "agents.list",
          resource_uri: "ui://agent-dashboard@1",
        }),
      ),
    ).toBe(false);

    expect(
      shouldDefaultCollapseWidget(
        makeWidget({
          tool_name: "tasks.get",
          resource_uri: "ui://tasks/detail@1",
          tool_action: "get",
        }),
      ),
    ).toBe(false);
  });

  it("does not auto-collapse whoami widgets", () => {
    expect(
      shouldDefaultCollapseWidget(
        makeWidget({
          tool_name: "whoami.list",
          resource_uri: "ui://whoami/identity@1",
          tool_action: "list",
        }),
      ),
    ).toBe(false);
  });

  it("builds a count preview when the payload has one", () => {
    const preview = getCollapsedWidgetPreview(
      makeWidget({
        tool_name: "agents.list",
        resource_uri: "ui://agent-dashboard@1",
        tool_result: {
          structuredContent: {
            count: 37,
          },
        },
      }),
      "Agents",
    );

    expect(preview).toBe("37 agents");
  });

  it("uses saved memory content when there is no count", () => {
    const preview = getCollapsedWidgetPreview(
      makeWidget({
        tool_name: "whoami.remember",
        resource_uri: "ui://whoami/identity@1",
        tool_action: "remember",
        tool_input: {
          key: "owner",
        },
        tool_result: {
          data: {
            memory: {
              items: [
                {
                  key: "owner",
                  value: "Jacob owns the workspace and coordinates the team.",
                },
              ],
            },
          },
        },
      }),
      "Identity",
    );

    expect(preview).toContain("Jacob owns the workspace");
  });
});
