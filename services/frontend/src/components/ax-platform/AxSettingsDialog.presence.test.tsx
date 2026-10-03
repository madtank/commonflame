import { beforeEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom";
import { render, screen, userEvent } from "@/test/utils";

import { AxSettingsDialog } from "./AxSettingsDialog";
import { createPersonalAccessKey, listPatScopeAgents } from "@/lib/api-clean";

vi.mock("@/lib/api-clean", () => ({
  api: {
    getUserProfile: vi.fn(async () => ({
      id: "user-1",
      username: "madtank",
      email: "madtank@example.com",
    })),
  },
  createPersonalAccessKey: vi.fn(),
  listPatScopeAgents: vi.fn(async () => [
    {
      id: "agent-1",
      name: "frontend_sentinel",
      agent_name: "frontend_sentinel",
      agent_type: "core",
    },
  ]),
  listPersonalAccessKeys: vi.fn(async () => []),
  revokePersonalAccessKey: vi.fn(),
  rotatePersonalAccessKey: vi.fn(),
}));

vi.mock("@/hooks/useUserSettings", () => ({
  useUserSettings: () => ({
    settings: {
      compact_mode: false,
      ai_auto_summarize: false,
      theme: "system",
    },
    saving: false,
    updateSettings: vi.fn(async () => undefined),
  }),
}));

vi.mock("@/hooks/usePresence", () => ({
  usePresence: () => ({
    presence: {
      frontend_sentinel: {
        agentName: "frontend_sentinel",
        heartbeat: {
          agent: "frontend_sentinel",
          model: "gpt-5.4",
          status: "active",
          ts: new Date().toISOString(),
          last_task: "test",
          capabilities: [],
        },
        expiresAt: 0,
        receivedAt: Date.now(),
      },
    },
    getStatus: () => "active",
    getFreshness: () => 1,
    isLoading: false,
    refresh: vi.fn(),
  }),
}));

describe("AxSettingsDialog presence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listPatScopeAgents).mockResolvedValue([
      {
        id: "agent-1",
        name: "frontend_sentinel",
        agent_name: "frontend_sentinel",
        agent_type: "core",
      },
    ]);
    vi.mocked(createPersonalAccessKey).mockResolvedValue({
      credential_id: "credential-1",
      key_id: "key-1",
      name: "Test token",
      scopes: [],
      token: "axp_u_test",
      created_at: new Date().toISOString(),
    });
  });

  it("defaults the credentials creator to agent tokens", async () => {
    const user = userEvent.setup();

    render(
      <AxSettingsDialog
        open
        onOpenChange={() => undefined}
        currentSpaceId="space-1"
        currentSpaceName="Test Space"
      />,
    );

    await user.click(screen.getByRole("tab", { name: /credentials/i }));

    expect(
      await screen.findByText(
        /Choose the agent this token will be permanently bound to/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /create agent token/i }),
    ).toBeInTheDocument();
  });

  it("shows presence dots in the PAT agent scope list", async () => {
    const user = userEvent.setup();

    render(
      <AxSettingsDialog
        open
        onOpenChange={() => undefined}
        currentSpaceId="space-1"
        currentSpaceName="Test Space"
      />,
    );

    await user.click(screen.getByRole("tab", { name: /credentials/i }));

    expect(await screen.findByText("frontend_sentinel")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Active" })).toBeInTheDocument();
  });

  it("allows enrollment PAT creation when only the agent picker endpoint is unavailable", async () => {
    const user = userEvent.setup();
    vi.mocked(listPatScopeAgents).mockRejectedValue(
      Object.assign(new Error("Not Found"), {
        response: { status: 404 },
      }),
    );

    render(
      <AxSettingsDialog
        open
        onOpenChange={() => undefined}
        currentSpaceId="space-1"
        currentSpaceName="Test Space"
      />,
    );

    await user.click(screen.getByRole("tab", { name: /credentials/i }));
    await user.click(
      screen.getByRole("checkbox", {
        name: /register agent and bind on first use/i,
      }),
    );
    await user.type(screen.getByLabelText(/token name/i), "Bootstrap token");

    expect(
      screen.queryByText(/PAT endpoints are not available/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("User Token")).not.toBeInTheDocument();

    const createButton = screen.getByRole("button", {
      name: /create agent token/i,
    });
    expect(createButton).toBeEnabled();

    await user.click(createButton);
    expect(createPersonalAccessKey).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Bootstrap token",
        agent_scope: "unbound",
      }),
    );
  }, 10_000);
});
