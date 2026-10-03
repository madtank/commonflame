import type React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FOLLOW_UUID } from "@/lib/agent-mobility";

import {
  AgentPanel,
  isPanelAgentVisibleInSpace,
  type PanelAgent,
} from "./AgentPanel";

const FAVORITES_KEY = "ax:agent-panel-favorites";
const ORDER_KEY = "ax:agent-panel-order";

function renderPanel(
  props: Partial<React.ComponentProps<typeof AgentPanel>> = {},
) {
  return render(
    <AgentPanel
      cloudAgents={[]}
      allAgents={[]}
      currentOrgId="space-a"
      onMentionAgent={vi.fn()}
      onNavigateToRegister={vi.fn()}
      {...props}
    />,
  );
}

afterEach(() => {
  localStorage.removeItem(FAVORITES_KEY);
  localStorage.removeItem(ORDER_KEY);
  vi.restoreAllMocks();
});

describe("AgentPanel space scoping", () => {
  it("treats mismatched pinned/org/space ids as not visible in the current space", () => {
    expect(
      isPanelAgentVisibleInSpace(
        { username: "local", org_id: "space-a" },
        "space-a",
      ),
    ).toBe(true);
    expect(
      isPanelAgentVisibleInSpace(
        { username: "moved", org_id: "space-b" },
        "space-a",
      ),
    ).toBe(false);
    expect(
      isPanelAgentVisibleInSpace(
        { username: "moved", pinned_org_id: "space-b", org_id: "space-a" },
        "space-a",
      ),
    ).toBe(false);
    expect(
      isPanelAgentVisibleInSpace(
        { username: "global", org_id: "space-b", is_global: true },
        "space-a",
      ),
    ).toBe(true);
    expect(
      isPanelAgentVisibleInSpace(
        { username: "metadata-global", metadata: { is_global: true } },
        "space-a",
      ),
    ).toBe(true);
  });

  it("keeps follow-mode agents visible in the current space", () => {
    expect(
      isPanelAgentVisibleInSpace(
        { username: "follow-pinned", pinned_org_id: FOLLOW_UUID },
        "space-a",
      ),
    ).toBe(true);
    expect(
      isPanelAgentVisibleInSpace(
        { username: "follow-org", org_id: FOLLOW_UUID },
        "space-a",
      ),
    ).toBe(true);
    expect(
      isPanelAgentVisibleInSpace(
        { username: "follow-flag", follow_user: true },
        "space-a",
      ),
    ).toBe(true);
    expect(
      isPanelAgentVisibleInSpace(
        { username: "follow-settings", settings: { follow_user: true } },
        "space-a",
      ),
    ).toBe(true);
    expect(
      isPanelAgentVisibleInSpace(
        { username: "follow-metadata", metadata: { pinned_org_id: FOLLOW_UUID } },
        "space-a",
      ),
    ).toBe(true);
  });

  it("does not show agents moved to another space in Add Agent search", async () => {
    const user = userEvent.setup();
    const allAgents: PanelAgent[] = [
      { username: "local_agent", org_id: "space-a", bio: "same space" },
      { username: "moved_agent", org_id: "space-b", bio: "other space" },
    ];

    renderPanel({ allAgents });

    await user.click(screen.getByRole("button", { name: /agents/i }));
    await user.click(screen.getByRole("button", { name: /add agent/i }));
    await user.type(
      screen.getByPlaceholderText(/search agents to add/i),
      "agent",
    );

    expect(screen.getByText("@local_agent")).toBeInTheDocument();
    expect(screen.queryByText("@moved_agent")).not.toBeInTheDocument();
  });

  it("does not let stale favorites make moved agents visible across spaces", async () => {
    localStorage.setItem(
      FAVORITES_KEY,
      JSON.stringify(["local_agent", "moved_agent"]),
    );
    const user = userEvent.setup();

    renderPanel({
      allAgents: [
        { username: "local_agent", org_id: "space-a" },
        { username: "moved_agent", org_id: "space-b" },
      ],
    });

    await user.click(screen.getByRole("button", { name: /agents/i }));

    const dialog = screen.getByText("My Agents").closest("div")?.parentElement
      ?.parentElement as HTMLElement;
    expect(within(dialog).getByText("@local_agent")).toBeInTheDocument();
    expect(within(dialog).queryByText("@moved_agent")).not.toBeInTheDocument();
  });
});
